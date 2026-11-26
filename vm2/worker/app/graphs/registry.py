# vm2/worker/app/graphs/registry.py
"""
Graph Registry — реестр и оркестратор графов LangGraph.
Production Blueprint v5.2.1 — ВМ №2

Регистрация графов, компиляция с AsyncPostgresSaver (чекпоинты в PG),
запуск, resume после HITL и чтение состояния для снапшота.
"""
import uuid
from typing import Any, Dict, Optional

import structlog
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import StateGraph
from langgraph.types import Command
from psycopg_pool import AsyncConnectionPool

from worker.app.settings import Settings, get_settings

logger = structlog.get_logger()


def _normalize_pg_url(url: str) -> str:
    """psycopg 3 понимает только postgresql:// / postgres://."""
    if url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    elif url.startswith("postgresql+ssl://"):
        url = url.replace("postgresql+ssl://", "postgresql://", 1)
    if "sslmode=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"
    return url


class GraphRegistry:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._graphs: Dict[str, StateGraph] = {}
        self._pool: Optional[AsyncConnectionPool] = None
        self._checkpointer: Optional[AsyncPostgresSaver] = None

    def register(self, name: str, graph: StateGraph) -> None:
        if name in self._graphs:
            logger.warning("Graph already registered, overwriting", graph_name=name)
        self._graphs[name] = graph
        logger.info("Graph registered", graph_name=name)

    def get(self, name: str) -> Optional[StateGraph]:
        return self._graphs.get(name)

    def list_graphs(self) -> list[str]:
        return list(self._graphs.keys())

    async def init_checkpointer(self) -> None:
        if self._checkpointer is not None:
            return
        conninfo = _normalize_pg_url(self.settings.postgres_runtime_url)
        logger.info("Initializing PostgresSaver", url=conninfo.split("@")[-1])
        # Служебный пул чекпоинтера: row_factory НЕ dict_row —
        # AsyncPostgresSaver читает строки по индексам.
        self._pool = AsyncConnectionPool(
            conninfo=conninfo,
            min_size=2,
            max_size=10,
            timeout=30,
            kwargs={"autocommit": True, "prepare_threshold": 0},
        )
        await self._pool.open()
        self._checkpointer = AsyncPostgresSaver(self._pool)
        await self._checkpointer.setup()
        logger.info("PostgresSaver initialized and schema ready")

    async def close_checkpointer(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            self._checkpointer = None
            logger.info("PostgresSaver closed")

    async def run(
        self,
        graph_name: str,
        initial_state: Dict[str, Any],
        thread_id: str,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        graph = self.get(graph_name)
        if graph is None:
            raise ValueError(f"Graph '{graph_name}' not found in registry")
        if self._checkpointer is None:
            await self.init_checkpointer()

        run_id = run_id or str(uuid.uuid4())
        log = logger.bind(run_id=run_id, thread_id=thread_id, graph=graph_name)
        log.info("Starting graph execution")

        compiled = graph.compile(checkpointer=self._checkpointer)
        config = {"configurable": {"thread_id": thread_id}}
        try:
            final_state = await compiled.ainvoke(initial_state, config)
            log.info("Graph execution finished", status=final_state.get("status", "unknown"))
            return final_state
        except Exception as e:
            log.error("Graph execution failed", error=str(e), exc_info=True)
            raise

    async def resume(
        self,
        graph_name: str,
        thread_id: str,
        human_response: Dict[str, Any],
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
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
            final_state = await compiled.ainvoke(Command(resume=human_response), config)
            log.info("Graph resumed", status=final_state.get("status", "unknown"))
            return final_state
        except Exception as e:
            log.error("Graph resume failed", error=str(e), exc_info=True)
            raise

    async def get_state(self, graph_name: str, thread_id: str) -> Dict[str, Any]:
        """Актуальное состояние из чекпоинта PG — для снапшота при реконнекте (§3.1)."""
        graph = self.get(graph_name)
        if graph is None:
            raise ValueError(f"Graph '{graph_name}' not found in registry")
        if self._checkpointer is None:
            await self.init_checkpointer()
        compiled = graph.compile(checkpointer=self._checkpointer)
        snapshot = await compiled.aget_state({"configurable": {"thread_id": thread_id}})
        return dict(snapshot.values) if snapshot and snapshot.values else {}


graph_registry = GraphRegistry()