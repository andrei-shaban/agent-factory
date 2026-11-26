# vm2/worker/app/tasks.py
"""
TaskIQ Tasks — Точки входа для Worker.
Production Blueprint v5.2 — ВМ №2

Регистрирует задачи, которые брокер (Redis) ставит в очередь.
Основные сценарии:
  1. Исполнение графа LangGraph (оркестрация Ф1–Ф5, HITL).
  2. Запуск изолированного инструмента в песочнице gVisor.
  3. Resume графа после паузы HITL.

"""
from typing import Any, Dict, Optional

import structlog

from worker.app.broker import broker
from worker.app.graphs.registry import graph_registry
from worker.app.sandbox import SandboxClient, SandboxResult
from worker.app.settings import get_settings

logger = structlog.get_logger()


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

    Args:
        run_id: Уникальный идентификатор запуска (для аудита и идемпотентности).
        thread_id: Идентификатор треда (диалога/документа).
        graph_name: Имя зарегистрированного графа (например, 'document_processing').
        initial_state: Начальное состояние графа.

    Returns:
        Словарь с финальным состоянием графа и статусом выполнения.
    """
    log = logger.bind(run_id=run_id, thread_id=thread_id, graph=graph_name)
    log.info("Starting LangGraph execution")

    try:
        final_state = await graph_registry.run(
            graph_name=graph_name,
            initial_state=initial_state or {},
            thread_id=thread_id,
            run_id=run_id,
        )

        status = final_state.get("status", "unknown")
        log.info("LangGraph execution finished", status=status)

        # Если граф остановился на interrupt() — статус будет содержать информацию о HITL-паузе.
        # Gateway получит событие HITL_INTERRUPT через event_bus (публикуется внутри графа).
        return {
            "status": "succeeded" if status in ("approved", "auto_approved", "output_generated") else status,
            "run_id": run_id,
            "thread_id": thread_id,
            "final_state": final_state,
        }

    except Exception as e:
        log.error("LangGraph execution failed", error=str(e), exc_info=True)
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

    Args:
        run_id: Идентификатор запуска.
        thread_id: Идентификатор треда.
        graph_name: Имя графа (для поиска в реестре).
        interrupt_id: Идентификатор точки остановки (из прерывания графа).
        human_response: Данные, введённые специалистом в HITL-форме.

    Returns:
        Словарь со статусом и финальным состоянием.
    """
    log = logger.bind(run_id=run_id, thread_id=thread_id, interrupt_id=interrupt_id)
    log.info("Resuming LangGraph after HITL", response_keys=list(human_response.keys()))

    try:
        final_state = await graph_registry.resume(
            graph_name=graph_name,
            thread_id=thread_id,
            human_response=human_response,
            run_id=run_id,
        )

        status = final_state.get("status", "unknown")
        log.info("LangGraph resumed successfully", status=status)

        return {
            "status": "resumed",
            "run_id": run_id,
            "thread_id": thread_id,
            "final_state": final_state,
        }

    except Exception as e:
        log.error("LangGraph resume failed", error=str(e), exc_info=True)
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

    Args:
        run_id: Идентификатор запуска (для аудита sandbox_executions).
        tool_name: Имя инструмента (например, 'generate_sap_excel').
        toolset_image: Docker-образ песочницы (например, 'factory/toolset-excel:1.0.0').
        payload: Входные данные для инструмента (монтируются в /app/input).
        timeout: Максимальное время выполнения в секундах.

    Returns:
        Словарь со статусом и списком артефактов.
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
                "stdout": result.stdout[:1000],  # Обрезаем для логов
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