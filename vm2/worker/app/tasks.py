"""
TaskIQ Tasks — Точки входа для Worker.
Production Blueprint v5.1 — ВМ №2

Регистрирует задачи, которые брокер (Redis) ставит в очередь.
Основные сценарии:
  1. Исполнение графа LangGraph (оркестрация Ф1–Ф5, HITL).
  2. Запуск изолированного инструмента в песочнице gVisor.
  3. Resume графа после паузы HITL.
"""

import uuid
from typing import Any, Dict, Optional

import structlog
from taskiq import TaskiqMessage

from worker.app.broker import broker

logger = structlog.get_logger()


# ==============================================================================
# 1. ИСПОЛНЕНИЕ ГРАФА LANGGRAPH
# ==============================================================================
@broker.task
async def run_agent_graph(
        run_id: str,
        thread_id: str,
        graph_name: str,
        initial_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Основная задача: запуск или возобновление графа LangGraph.

    Args:
        run_id: Уникальный идентификатор запуска (для аудита и идемпотентности).
        thread_id: Идентификатор треда (диалога/документа).
        graph_name: Имя скомпилированного графа (например, 'document_processing').
        initial_state: Начальное состояние графа (сообщения, метаданные).

    Returns:
        Словарь с финальным состоянием графа и статусом выполнения.
    """
    log = logger.bind(run_id=run_id, thread_id=thread_id, graph=graph_name)
    log.info("Starting LangGraph execution")

    # TODO:
    # 1. Инициализировать Langfuse trace (trace_id=run_id).
    # 2. Загрузить скомпилированный граф из реестра (worker.app.graphs.registry).
    # 3. Вызвать graph.ainvoke(initial_state, config={"configurable": {"thread_id": thread_id}}).
    # 4. Обработать LangGraph interrupt() (HITL) — если граф остановился,
    #    сохранить состояние в PG и отправить событие в Redis Stream для Gateway.

    log.info("LangGraph execution finished successfully")
    return {
        "status": "succeeded",
        "run_id": run_id,
        "thread_id": thread_id,
        "final_state": initial_state or {}
    }


# ==============================================================================
# 2. RESUME ПОСЛЕ HITL (ЧЕЛОВЕК В КОНТУРЕ)
# ==============================================================================
@broker.task
async def resume_agent_graph(
        run_id: str,
        thread_id: str,
        interrupt_id: str,
        human_response: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Возобновление исполнения графа после утверждения формы специалистом.

    Args:
        run_id: Идентификатор запуска.
        thread_id: Идентификатор треда.
        interrupt_id: Идентификатор точки остановки (из прерывания графа).
        human_response: Данные, введенные специалистом в HITL-форме.
    """
    log = logger.bind(run_id=run_id, thread_id=thread_id, interrupt_id=interrupt_id)
    log.info("Resuming LangGraph after HITL", response_keys=list(human_response.keys()))

    # TODO:
    # 1. Найти граф по thread_id.
    # 2. Вызвать graph.ainvoke(human_response, config={...}, task_id=interrupt_id).
    # 3. Записать решение в таблицу interrupt_decisions (PostgreSQL).

    log.info("LangGraph resumed successfully")
    return {"status": "resumed", "run_id": run_id}


# ==============================================================================
# 3. ЗАПУСК ИНСТРУМЕНТА В ПЕСОЧНИЦЕ (gVisor)
# ==============================================================================
@broker.task
async def execute_sandbox_tool(
        run_id: str,
        tool_name: str,
        toolset_image: str,
        payload: Dict[str, Any],
        timeout: int = 300,
) -> Dict[str, Any]:
    """
    Запуск недоверенного кода в изолированном контейнере gVisor.

    Args:
        run_id: Идентификатор запуска (для аудита sandbox_executions).
        tool_name: Имя инструмента (например, 'generate_sap_excel').
        toolset_image: Docker-образ песочницы (например, 'factory/toolset-excel:1.0.0').
        payload: Входные данные для инструмента (монтируются в /app/input).
        timeout: Максимальное время выполнения в секундах.
    """
    log = logger.bind(run_id=run_id, tool=tool_name, image=toolset_image)
    log.info("Starting sandbox execution")

    # TODO:
    # 1. Через Docker Socket Proxy (DOCKER_HOST) создать контейнер:
    #    - image: toolset_image
    #    - network: none
    #    - read_only: true
    #    - tmpfs: {"/tmp": ""}
    #    - security_opt: ["no-new-privileges:true"]
    #    - host_config: runtime="runsc"
    # 2. Смонтировать payload в /app/input (read-only).
    # 3. Запустить контейнер и ждать завершения (с таймаутом).
    # 4. Считать stdout/stderr и забрать артефакты из /app/output.
    # 5. Удалить контейнер.
    # 6. Записать метрики (duration, memory_peak) в sandbox_executions (PostgreSQL).

    log.info("Sandbox execution finished")
    return {
        "status": "succeeded",
        "tool": tool_name,
        "artifacts": []  # Список путей к артефактам в SeaweedFS
    }