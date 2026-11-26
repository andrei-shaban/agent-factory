# vm2/worker/app/tasks.py
"""
TaskIQ Tasks — Точки входа для Worker.
Production Blueprint v5.2 — ВМ №2

Регистрирует задачи, которые брокер (Redis) ставит в очередь.
Основные сценарии:
- Исполнение графа LangGraph (оркестрация Ф1–Ф5, HITL).
- Запуск изолированного инструмента в песочнице gVisor.
- Resume графа после паузы HITL.

Жизненный цикл статусов (Blueprint §3.1):
- QUEUED → RUNNING (при старте исполнения)
- RUNNING → WAITING_HUMAN (при interrupt)
- RUNNING/WAITING_HUMAN → SUCCEEDED (при успешном завершении)
- RUNNING/WAITING_HUMAN → FAILED (при ошибке)
- QUEUED/RUNNING/WAITING_HUMAN → CANCELLED (при отмене через Gateway)
"""
from typing import Any, Dict, Optional

import psycopg
import structlog
from psycopg_pool import AsyncConnectionPool

from worker.app.broker import broker
from worker.app.graphs.registry import graph_registry
from worker.app.sandbox import SandboxClient, SandboxResult
from worker.app.settings import get_settings

logger = structlog.get_logger()


# ==============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==============================================================================
async def _update_run_status(
        run_id: str,
        status: str,
        interrupt_id: Optional[str] = None,
        error: Optional[str] = None,
) -> None:
    """
    Обновляет статус рана в реестре PostgreSQL.
    Blueprint §3.1: воркер управляет жизненным циклом статусов.
    """
    settings = get_settings()
    url = settings.postgres_runtime_url.replace("postgresql+asyncpg://", "postgresql://")
    try:
        async with await psycopg.AsyncConnection.connect(url) as conn:
            async with conn.cursor() as cur:
                if interrupt_id:
                    await cur.execute(
                        """
                        UPDATE runs
                        SET status = %s, interrupt_id = %s, updated_at = NOW()
                        WHERE run_id = %s
                        """,
                        (status, interrupt_id, run_id),
                    )
                elif error:
                    await cur.execute(
                        """
                        UPDATE runs
                        SET status = %s, error = %s, finished_at = NOW(), updated_at = NOW()
                        WHERE run_id = %s
                        """,
                        (status, error, run_id),
                    )
                elif status in ("SUCCEEDED", "FAILED"):
                    await cur.execute(
                        """
                        UPDATE runs
                        SET status = %s, finished_at = NOW(), updated_at = NOW()
                        WHERE run_id = %s
                        """,
                        (status, run_id),
                    )
                else:
                    await cur.execute(
                        """
                        UPDATE runs
                        SET status = %s, started_at = NOW(), updated_at = NOW()
                        WHERE run_id = %s
                        """,
                        (status, run_id),
                    )
    except Exception as e:
        logger.error("Failed to update run status in DB", run_id=run_id, status=status, error=str(e))


# ==============================================================================
# 1. ИСПОЛНЕНИЕ ГРАФА LANGGRAPH
# ==============================================================================
@broker.task(task_name="factory.run_agent")
async def run_agent_graph(
        run_id: str,
        thread_id: str,
        graph_name: str,
        initial_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Основная задача: запуск графа LangGraph.

    Жизненный цикл статусов:
    1. QUEUED → RUNNING (при старте)
    2. RUNNING → WAITING_HUMAN (при interrupt, если граф остановился)
    3. RUNNING → SUCCEEDED (при успешном завершении)
    4. RUNNING → FAILED (при ошибке)
    """
    log = logger.bind(run_id=run_id, thread_id=thread_id, graph=graph_name)
    log.info("Starting LangGraph execution")

    # Обновляем статус на RUNNING
    await _update_run_status(run_id, "RUNNING")

    try:
        final_state = await graph_registry.run(
            graph_name=graph_name,
            initial_state=initial_state or {},
            thread_id=thread_id,
            run_id=run_id,
        )
        status = final_state.get("status", "unknown")
        log.info("LangGraph execution finished", status=status)

        # Определяем финальный статус
        if status in ("approved", "auto_approved", "output_generated"):
            final_status = "SUCCEEDED"
        elif status == "waiting_hitl":
            # Граф остановился на interrupt — статус WAITING_HUMAN
            # interrupt_id будет установлен через event_bus
            final_status = "WAITING_HUMAN"
        else:
            final_status = "FAILED"

        # Обновляем статус в БД
        await _update_run_status(run_id, final_status)

        return {
            "status": final_status.lower(),
            "run_id": run_id,
            "thread_id": thread_id,
            "final_state": final_state,
        }
    except Exception as e:
        log.error("LangGraph execution failed", error=str(e), exc_info=True)

        # Обновляем статус на FAILED
        await _update_run_status(run_id, "FAILED", error=str(e))

        return {
            "status": "failed",
            "run_id": run_id,
            "thread_id": thread_id,
            "error": str(e),
        }


# ==============================================================================
# 2. RESUME ПОСЛЕ HITL (ЧЕЛОВЕК В КОНТУРЕ)
# ==============================================================================
@broker.task(task_name="factory.resume_run")
async def resume_agent_graph(
        run_id: str,
        thread_id: str,
        graph_name: str,
        interrupt_id: str,
        human_response: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Возобновление исполнения графа после утверждения формы специалистом.

    Жизненный цикл статусов:
    1. WAITING_HUMAN → RUNNING (при резюме)
    2. RUNNING → SUCCEEDED (при успешном завершении)
    3. RUNNING → FAILED (при ошибке)
    """
    log = logger.bind(run_id=run_id, thread_id=thread_id, interrupt_id=interrupt_id)
    log.info("Resuming LangGraph after HITL", response_keys=list(human_response.keys()))

    # Обновляем статус на RUNNING (сбрасываем interrupt_id)
    await _update_run_status(run_id, "RUNNING", interrupt_id=None)

    try:
        final_state = await graph_registry.resume(
            graph_name=graph_name,
            thread_id=thread_id,
            human_response=human_response,
            run_id=run_id,
        )
        status = final_state.get("status", "unknown")
        log.info("LangGraph resumed successfully", status=status)

        # Определяем финальный статус
        if status in ("approved", "auto_approved", "output_generated"):
            final_status = "SUCCEEDED"
        else:
            final_status = "FAILED"

        # Обновляем статус в БД
        await _update_run_status(run_id, final_status)

        return {
            "status": final_status.lower(),
            "run_id": run_id,
            "thread_id": thread_id,
            "final_state": final_state,
        }
    except Exception as e:
        log.error("LangGraph resume failed", error=str(e), exc_info=True)

        # Обновляем статус на FAILED
        await _update_run_status(run_id, "FAILED", error=str(e))

        return {
            "status": "failed",
            "run_id": run_id,
            "thread_id": thread_id,
            "error": str(e),
        }


# ==============================================================================
# 3. ЗАПУСК ИНСТРУМЕНТА В ПЕСОЧНИЦЕ (gVisor)
# ==============================================================================
@broker.task(task_name="factory.execute_sandbox_tool")
async def execute_sandbox_tool(
        run_id: str,
        tool_name: str,
        toolset_image: str,
        payload: Dict[str, Any],
        timeout: int = 300,
) -> Dict[str, Any]:
    """
    Запуск недоверенного кода в изолированном контейнере gVisor.
    Blueprint §3.2:
    - runtime: runsc (gVisor)
    - network: none
    - read-only rootfs + tmpfs
    - cap-drop ALL, no-new-privileges
    - лимиты CPU/memory/pids
    """
    log = logger.bind(run_id=run_id, tool=tool_name, image=toolset_image)
    log.info("Starting sandbox execution")

    settings = get_settings()
    sandbox = SandboxClient(settings)

    try:
        # Подготовка входных файлов для песочницы
        import json
        input_files = {
            "payload.json": json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        }

        result: SandboxResult = await sandbox.execute_tool(
            image=toolset_image,
            cmd=["python", f"/app/tools/{tool_name}.py"],
            input_files=input_files,
            env={"RUN_ID": run_id, "TOOL_NAME": tool_name},
        )

        if result.success:
            log.info(
                "Sandbox execution succeeded",
                exit_code=result.exit_code,
                artifacts_count=len(result.artifacts),
            )

            # Загрузка артефактов в SeaweedFS
            from worker.app.integrations import s3_client
            artifact_urls = []
            for artifact_name, artifact_bytes in result.artifacts.items():
                key = f"runs/{run_id}/{tool_name}/{artifact_name}"
                url = await s3_client.upload_file(
                    bucket=settings.seaweed_s3_bucket_factory,
                    key=key,
                    file_content=artifact_bytes,
                )
                artifact_urls.append(url)

            return {
                "status": "succeeded",
                "tool": tool_name,
                "artifacts": artifact_urls,
                "stdout": result.stdout[:1000],
            }
        else:
            log.warning(
                "Sandbox execution failed",
                exit_code=result.exit_code,
                stderr=result.stderr[:500],
            )
            return {
                "status": "failed",
                "tool": tool_name,
                "error": result.error_message or result.stderr[:500],
                "exit_code": result.exit_code,
            }
    except Exception as e:
        log.error("Sandbox execution error", error=str(e), exc_info=True)
        return {
            "status": "failed",
            "tool": tool_name,
            "error": str(e),
        }