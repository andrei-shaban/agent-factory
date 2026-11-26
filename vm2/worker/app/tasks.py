# vm2/worker/app/tasks.py
"""
TaskIQ Tasks — точки входа Worker.
Production Blueprint v5.2.1 — ВМ №2

Пауза детектится по __interrupt__ в результате ainvoke.
Актуальный interrupt_id сразу пишется в runs. Терминальный статус
освобождает thread_locks. Снапшот для реконнекта — из чекпоинта.
Heartbeat держит thread_locks живым при длительных HITL-паузах.
"""
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

import psycopg
import structlog

from worker.app.broker import broker
from worker.app.cancellation import (
    RunCancelledError,
    _check_cancellation,
    _normalize_pg_url,
)
from worker.app.event_bus import event_bus
from worker.app.graphs.registry import graph_registry
from worker.app.sandbox import SandboxClient, SandboxResult
from worker.app.settings import get_settings

logger = structlog.get_logger()

TERMINAL_STATUSES = ("SUCCEEDED", "FAILED", "CANCELLED")
SUCCESS_STATUSES = ("approved", "auto_approved")

HEARTBEAT_INTERVAL_SECONDS = 60
STALE_LOCK_TTL = timedelta(minutes=5)


async def _heartbeat_loop(run_id: str) -> None:
    """Обновление locked_at в thread_locks каждые HEARTBEAT_INTERVAL_SECONDS."""
    settings = get_settings()
    url = _normalize_pg_url(settings.postgres_runtime_url)
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        try:
            async with await psycopg.AsyncConnection.connect(url) as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "UPDATE thread_locks SET locked_at = NOW() WHERE run_id = %s",
                        (run_id,),
                    )
        except Exception as e:
            logger.warning("Heartbeat failed", run_id=run_id, error=str(e))


async def _update_run_status(
    run_id: str,
    status: str,
    interrupt_id: Optional[str] = None,
    error: Optional[str] = None,
    clear_interrupt: bool = False,
) -> None:
    settings = get_settings()
    url = _normalize_pg_url(settings.postgres_runtime_url)
    now = datetime.now(timezone.utc)
    try:
        async with await psycopg.AsyncConnection.connect(url) as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT status FROM runs WHERE run_id = %s", (run_id,))
                row = await cur.fetchone()
                if not row:
                    logger.warning("Run not found in registry", run_id=run_id)
                    return
                if row[0] in TERMINAL_STATUSES:
                    return

                if status == "RUNNING":
                    await cur.execute(
                        "UPDATE runs SET status=%s, started_at=%s, updated_at=%s WHERE run_id=%s",
                        (status, now, now, run_id),
                    )
                elif status in ("SUCCEEDED", "FAILED"):
                    await cur.execute(
                        "UPDATE runs SET status=%s, error=%s, finished_at=%s, updated_at=%s WHERE run_id=%s",
                        (status, error, now, now, run_id),
                    )
                    await cur.execute("DELETE FROM thread_locks WHERE run_id=%s", (run_id,))
                elif status == "WAITING_HUMAN":
                    await cur.execute(
                        "UPDATE runs SET status=%s, interrupt_id=%s, updated_at=%s WHERE run_id=%s",
                        (status, interrupt_id, now, run_id),
                    )
                elif status == "CANCELLED":
                    await cur.execute(
                        "UPDATE runs SET status=%s, finished_at=%s, updated_at=%s WHERE run_id=%s",
                        (status, now, now, run_id),
                    )
                    await cur.execute("DELETE FROM thread_locks WHERE run_id=%s", (run_id,))
                elif clear_interrupt:
                    await cur.execute(
                        "UPDATE runs SET status=%s, interrupt_id=NULL, updated_at=%s WHERE run_id=%s",
                        (status, now, run_id),
                    )
                else:
                    await cur.execute(
                        "UPDATE runs SET status=%s, updated_at=%s WHERE run_id=%s",
                        (status, now, run_id),
                    )
    except Exception as e:
        logger.error("Failed to update run status", run_id=run_id, status=status, error=str(e))


async def _finalize_run(run_id: str, thread_id: str, final_state: Dict[str, Any]) -> Dict[str, Any]:
    if final_state.get("__interrupt__"):
        interrupt_id = (
            final_state.get("approver_interrupt_id")
            if final_state.get("specialist_decision")
            else final_state.get("specialist_interrupt_id")
        )
        await _update_run_status(run_id, "WAITING_HUMAN", interrupt_id=interrupt_id)
        return {"status": "waiting_human", "run_id": run_id, "thread_id": thread_id,
                "interrupt_id": interrupt_id}
    status = final_state.get("status", "unknown")
    final_status = "SUCCEEDED" if status in SUCCESS_STATUSES else "FAILED"
    await _update_run_status(run_id, final_status)
    return {"status": final_status.lower(), "run_id": run_id, "thread_id": thread_id,
            "final_state": final_state}


@broker.task(task_name="factory.run_agent")
async def run_agent_graph(
    run_id: str,
    thread_id: str,
    graph_name: str,
    initial_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    log = logger.bind(run_id=run_id, thread_id=thread_id, graph=graph_name)
    log.info("Starting LangGraph execution")

    try:
        await _check_cancellation(run_id)
    except RunCancelledError:
        return {"status": "cancelled", "run_id": run_id, "thread_id": thread_id}

    await _update_run_status(run_id, "RUNNING")

    heartbeat = asyncio.create_task(_heartbeat_loop(run_id))
    try:
        final_state = await graph_registry.run(
            graph_name=graph_name,
            initial_state=initial_state or {},
            thread_id=thread_id,
            run_id=run_id,
        )
        return await _finalize_run(run_id, thread_id, final_state)
    except RunCancelledError:
        await _update_run_status(run_id, "CANCELLED")
        return {"status": "cancelled", "run_id": run_id, "thread_id": thread_id}
    except Exception as e:
        log.error("LangGraph execution failed", error=str(e), exc_info=True)
        await _update_run_status(run_id, "FAILED", error=str(e))
        return {"status": "failed", "run_id": run_id, "thread_id": thread_id, "error": str(e)}
    finally:
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass


@broker.task(task_name="factory.resume_run")
async def resume_agent_graph(
    run_id: str,
    thread_id: str,
    graph_name: str,
    interrupt_id: str,
    human_response: Dict[str, Any],
) -> Dict[str, Any]:
    log = logger.bind(run_id=run_id, thread_id=thread_id, interrupt_id=interrupt_id)
    log.info("Resuming LangGraph after HITL")

    try:
        await _check_cancellation(run_id)
    except RunCancelledError:
        return {"status": "cancelled", "run_id": run_id, "thread_id": thread_id}

    await _update_run_status(run_id, "RUNNING", clear_interrupt=True)

    heartbeat = asyncio.create_task(_heartbeat_loop(run_id))
    try:
        final_state = await graph_registry.resume(
            graph_name=graph_name,
            thread_id=thread_id,
            human_response=human_response,
            run_id=run_id,
        )
        return await _finalize_run(run_id, thread_id, final_state)
    except RunCancelledError:
        await _update_run_status(run_id, "CANCELLED")
        return {"status": "cancelled", "run_id": run_id, "thread_id": thread_id}
    except Exception as e:
        log.error("LangGraph resume failed", error=str(e), exc_info=True)
        await _update_run_status(run_id, "FAILED", error=str(e))
        return {"status": "failed", "run_id": run_id, "thread_id": thread_id, "error": str(e)}
    finally:
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass


@broker.task(task_name="factory.publish_snapshot")
async def publish_snapshot(run_id: str, thread_id: str, graph_name: str) -> Dict[str, Any]:
    state = await graph_registry.get_state(graph_name, thread_id)
    await event_bus.publish_state_snapshot(thread_id, run_id, state)
    return {"status": "published", "run_id": run_id}


@broker.task(task_name="factory.execute_sandbox_tool")
async def execute_sandbox_tool(
    run_id: str,
    tool_name: str,
    toolset_image: str,
    payload: Dict[str, Any],
    timeout: int = 300,
) -> Dict[str, Any]:
    log = logger.bind(run_id=run_id, tool=tool_name, image=toolset_image)
    log.info("Starting sandbox execution")
    settings = get_settings()
    sandbox = SandboxClient(settings)
    try:
        import json
        input_files = {"payload.json": json.dumps(payload, ensure_ascii=False).encode("utf-8")}
        result: SandboxResult = await sandbox.execute_tool(
            image=toolset_image,
            cmd=["python", f"/app/tools/{tool_name}.py"],
            input_files=input_files,
            env={"RUN_ID": run_id, "TOOL_NAME": tool_name},
        )
        if result.success:
            from worker.app.integrations import s3_client
            artifact_urls = []
            for artifact_name, artifact_bytes in result.artifacts.items():
                key = f"runs/{run_id}/{tool_name}/{artifact_name}"
                url = await s3_client.upload_file(
                    bucket=settings.seaweed_s3_bucket_factory, key=key,
                    file_content=artifact_bytes,
                )
                artifact_urls.append(url)
            return {"status": "succeeded", "tool": tool_name,
                    "artifacts": artifact_urls, "stdout": result.stdout[:1000]}
        return {"status": "failed", "tool": tool_name,
                "error": result.error_message or result.stderr[:500],
                "exit_code": result.exit_code}
    except Exception as e:
        log.error("Sandbox execution error", error=str(e), exc_info=True)
        return {"status": "failed", "tool": tool_name, "error": str(e)}