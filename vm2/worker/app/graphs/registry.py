"""
Graph Registry — Реестр и оркестратор графов LangGraph.
Production Blueprint v5.2 — ВМ №2

Назначение:
  - Регистрация графов (StateGraph) по имени.
  - Компиляция графа с PostgresSaver (чекпоинты в PostgreSQL).
  - Запуск графа с поддержкой checkpoint'ов.
  - Обработка HITL-пауз (interrupt) и resume.
  - Интеграция с Langfuse для трейсинга (опционально, на уровне вызывающего кода).
"""
import uuid
from typing import Any, Dict, Optional

import structlog
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import StateGraph
from psycopg_pool import AsyncConnectionPool

from worker.app.settings import Settings, get_settings

logger = structlog.get_logger()


def _normalize_pg_url(url: str) -> str:
    """
    Нормализация URL для psycopg 3.
    psycopg 3 понимает схемы: postgresql://, postgres://
    Не понимает: postgresql+asyncpg://, postgresql+ssl://
    """
    if url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    elif url.startswith("postgresql+ssl://"):
        url = url.replace("postgresql+ssl://", "postgresql://", 1)
        if "sslmode=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}sslmode=require"
    return url


class GraphRegistry:
    """
    Реестр графов LangGraph.
    Хранит StateGraph'ы и управляет их выполнением
    с checkpoint'ами в PostgreSQL через AsyncPostgresSaver.
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._graphs: Dict[str, StateGraph] = {}
        self._pool: Optional[AsyncConnectionPool] = None
        self._checkpointer: Optional[AsyncPostgresSaver] = None

    # ==========================================================================
    # РЕГИСТРАЦИЯ ГРАФОВ
    # ==========================================================================

    def register(self, name: str, graph: StateGraph) -> None:
        """
        Регистрация StateGraph.
        Граф компилируется с checkpointer'ом только в момент запуска (run/resume).
        """
        if name in self._graphs:
            logger.warning("Graph already registered, overwriting", graph_name=name)
        self._graphs[name] = graph
        logger.info("Graph registered", graph_name=name)

    def get(self, name: str) -> Optional[StateGraph]:
        return self._graphs.get(name)

    def list_graphs(self) -> list[str]:
        return list(self._graphs.keys())

    # ==========================================================================
    # CHECKPOINTER (PostgreSQL)
    # ==========================================================================

    async def init_checkpointer(self) -> None:
        """
        Инициализация пула соединений и AsyncPostgresSaver.
        Blueprint §3.1: autocommit=True, prepare_threshold=0.
        Blueprint §3.6: psycopg_pool 3.3.9.
        """
        if self._checkpointer is not None:
            logger.debug("Checkpointer already initialized")
            return

        conninfo = _normalize_pg_url(self.settings.postgres_runtime_url)
        logger.info("Initializing PostgresSaver", url=conninfo.split("@")[-1] if "@" in conninfo else conninfo)

        self._pool = AsyncConnectionPool(
            conninfo=conninfo,
            min_size=2,
            max_size=10,
            timeout=30,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
            },
        )
        await self._pool.open()

        self._checkpointer = AsyncPostgresSaver(self._pool)
        await self._checkpointer.setup()
        logger.info("PostgresSaver initialized and schema ready")

    async def close_checkpointer(self) -> None:
        """Закрытие пула соединений при graceful shutdown."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            self._checkpointer = None
            logger.info("PostgresSaver closed")

    # ==========================================================================
    # ЗАПУСК ГРАФА
    # ==========================================================================

    async def run(
        self,
        graph_name: str,
        initial_state: Dict[str, Any],
        thread_id: str,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Запуск графа с checkpoint'ами в PostgreSQL.
        Если граф останавливается на interrupt() (HITL), выполнение приостанавливается,
        состояние сохраняется в PG, и метод возвращает текущее состояние.
        """
        graph = self.get(graph_name)
        if graph is None:
            raise ValueError(f"Graph '{graph_name}' not found in registry")

        if self._checkpointer is None:
            await self.init_checkpointer()

        run_id = run_id or str(uuid.uuid4())
        log = logger.bind(run_id=run_id, thread_id=thread_id, graph=graph_name)
        log.info("Starting graph execution")

        # Компиляция графа с checkpointer'ом
        compiled = graph.compile(checkpointer=self._checkpointer)
        config = {"configurable": {"thread_id": thread_id}}

        try:
            final_state = await compiled.ainvoke(initial_state, config)
            status = final_state.get("status", "unknown")
            log.info("Graph execution finished", status=status)
            return final_state
        except Exception as e:
            log.error("Graph execution failed", error=str(e), exc_info=True)
            raise

    # ==========================================================================
    # RESUME ПОСЛЕ HITL
    # ==========================================================================

    async def resume(
        self,
        graph_name: str,
        thread_id: str,
        human_response: Dict[str, Any],
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Возобновление графа после HITL-паузы.
        LangGraph восстанавливает состояние из checkpoint'а по thread_id
        и передаёт human_response в точку прерывания.
        """
        graph = self.get(graph_name)
        if graph is None:
            raise ValueError(f"Graph '{graph_name}' not found in registry")

        if self._checkpointer is None:
            await self.init_checkpointer()

        run_id = run_id or str(uuid.uuid4())
        log = logger.bind(run_id=run_id, thread_id=thread_id, graph=graph_name)
        log.info("Resuming graph after HITL")

        compiled = graph.compile(checkpointer=self._checkpointer)
        config = {"configurable": {"thread_id": thread_id}}

        try:
            # LangGraph resume: передаём human_response как новый input
            # граф восстановит состояние из checkpoint и продолжит выполнение
            final_state = await compiled.ainvoke(human_response, config)
            status = final_state.get("status", "unknown")
            log.info("Graph resumed successfully", status=status)
            return final_state
        except Exception as e:
            log.error("Graph resume failed", error=str(e), exc_info=True)
            raise


# Глобальный экземпляр реестра (singleton)
graph_registry = GraphRegistry()