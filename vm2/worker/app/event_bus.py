# vm2/worker/app/event_bus.py
"""
Event Bus — Публикация событий AG-UI в Redis Streams.
Production Blueprint v5.2 — ВМ №2

Назначение:
  - Публикация событий исполнения графа в Redis Streams
  - Gateway (ВМ №1) читает эти события через XREAD и стримит клиенту по SSE
  - Ограничение размера стрима через MAXLEN для предотвращения разрастания
  - Интеграция с LangGraph через callbacks (опционально)

Формат событий AG-UI:
  - RUN_STARTED, RUN_FINISHED, RUN_FAILED
  - NODE_STARTED, NODE_FINISHED
  - HITL_INTERRUPT, HITL_RESUMED
  - TOOL_EXECUTED
  - SSE_MESSAGE (произвольные сообщения для фронтенда)
  - ERROR

Ключ Redis: agui:stream:{thread_id}
"""

import json
import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

import redis.asyncio as redis
import structlog

from worker.app.settings import Settings, get_settings

logger = structlog.get_logger()


class EventType(str, Enum):
    """Типы событий AG-UI протокола."""
    RUN_STARTED = "RUN_STARTED"
    RUN_FINISHED = "RUN_FINISHED"
    RUN_FAILED = "RUN_FAILED"
    NODE_STARTED = "NODE_STARTED"
    NODE_FINISHED = "NODE_FINISHED"
    HITL_INTERRUPT = "HITL_INTERRUPT"
    HITL_RESUMED = "HITL_RESUMED"
    TOOL_EXECUTED = "TOOL_EXECUTED"
    SSE_MESSAGE = "SSE_MESSAGE"
    ERROR = "ERROR"


class EventBus:
    """
    Шина событий AG-UI.
    Публикует события в Redis Streams для SSE-relay через Gateway.
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._redis: Optional[redis.Redis] = None

    async def _get_redis(self) -> redis.Redis:
        """Ленивая инициализация Redis клиента."""
        if self._redis is None:
            self._redis = redis.from_url(
                self.settings.redis_factory_url,
                decode_responses=True,
            )
            logger.info("EventBus Redis client initialized")
        return self._redis

    async def close(self) -> None:
        """Закрытие Redis соединения."""
        if self._redis:
            await self._redis.close()
            self._redis = None

    async def publish_event(
            self,
            event_type: EventType,
            thread_id: str,
            run_id: str,
            data: Dict[str, Any],
            node_name: Optional[str] = None,
            timestamp: Optional[str] = None,
    ) -> str:
        """
        Публикация события в Redis Streams.

        Args:
            event_type: Тип события (EventType).
            thread_id: Идентификатор треда (ключ стрима: agui:stream:{thread_id}).
            run_id: Идентификатор запуска.
            data: Полезная нагрузка события (словарь).
            node_name: Имя узла графа (для NODE_STARTED/NODE_FINISHED).
            timestamp: Временная метка (ISO 8601, по умолчанию — текущее время).

        Returns:
            ID добавленного сообщения в стрим (например, "1234567890123-0").
        """
        redis_client = await self._get_redis()
        stream_key = f"agui:stream:{thread_id}"

        timestamp = timestamp or datetime.now(timezone.utc).isoformat()

        event = {
            "type": event_type.value,
            "run_id": run_id,
            "thread_id": thread_id,
            "timestamp": timestamp,
            "data": json.dumps(data, ensure_ascii=False, default=str),
        }

        if node_name:
            event["node"] = node_name

        # Публикация в стрим с ограничением размера (MAXLEN ~ 10000)
        # ~ означает approximate trimming (быстрее, чем точное ограничение)
        message_id = await redis_client.xadd(
            name=stream_key,
            fields=event,
            maxlen=10000,
            approximate=True,
        )

        logger.debug(
            "Event published",
            event_type=event_type.value,
            thread_id=thread_id,
            run_id=run_id,
            message_id=message_id,
        )

        return message_id

    # ==============================================================================
    # УДОБНЫЕ МЕТОДЫ-ОБЁРТКИ
    # ==============================================================================

    async def publish_run_started(
            self, thread_id: str, run_id: str, graph_name: str, user_id: str
    ) -> str:
        """Публикация события начала запуска графа."""
        return await self.publish_event(
            event_type=EventType.RUN_STARTED,
            thread_id=thread_id,
            run_id=run_id,
            data={
                "graph_name": graph_name,
                "user_id": user_id,
            },
        )

    async def publish_run_finished(
            self, thread_id: str, run_id: str, final_state: Dict[str, Any]
    ) -> str:
        """Публикация события успешного завершения графа."""
        return await self.publish_event(
            event_type=EventType.RUN_FINISHED,
            thread_id=thread_id,
            run_id=run_id,
            data={"final_state": final_state},
        )

    async def publish_run_failed(
            self, thread_id: str, run_id: str, error_message: str
    ) -> str:
        """Публикация события ошибки графа."""
        return await self.publish_event(
            event_type=EventType.RUN_FAILED,
            thread_id=thread_id,
            run_id=run_id,
            data={"error": error_message},
        )

    async def publish_node_started(
            self, thread_id: str, run_id: str, node_name: str
    ) -> str:
        """Публикация события начала выполнения узла графа."""
        return await self.publish_event(
            event_type=EventType.NODE_STARTED,
            thread_id=thread_id,
            run_id=run_id,
            data={},
            node_name=node_name,
        )

    async def publish_node_finished(
            self, thread_id: str, run_id: str, node_name: str, output: Optional[Dict[str, Any]] = None
    ) -> str:
        """Публикация события завершения узла графа."""
        return await self.publish_event(
            event_type=EventType.NODE_FINISHED,
            thread_id=thread_id,
            run_id=run_id,
            data={"output": output or {}},
            node_name=node_name,
        )

    async def publish_hitl_interrupt(
            self,
            thread_id: str,
            run_id: str,
            interrupt_id: str,
            form_data: Dict[str, Any],
    ) -> str:
        """Публикация события паузы HITL (ожидание решения человека)."""
        return await self.publish_event(
            event_type=EventType.HITL_INTERRUPT,
            thread_id=thread_id,
            run_id=run_id,
            data={
                "interrupt_id": interrupt_id,
                "form": form_data,
            },
        )

    async def publish_hitl_resumed(
            self, thread_id: str, run_id: str, interrupt_id: str, decision: Dict[str, Any]
    ) -> str:
        """Публикация события возобновления после HITL."""
        return await self.publish_event(
            event_type=EventType.HITL_RESUMED,
            thread_id=thread_id,
            run_id=run_id,
            data={
                "interrupt_id": interrupt_id,
                "decision": decision,
            },
        )

    async def publish_tool_executed(
            self,
            thread_id: str,
            run_id: str,
            tool_name: str,
            success: bool,
            duration_ms: Optional[int] = None,
    ) -> str:
        """Публикация события выполнения инструмента (в песочнице или локально)."""
        return await self.publish_event(
            event_type=EventType.TOOL_EXECUTED,
            thread_id=thread_id,
            run_id=run_id,
            data={
                "tool_name": tool_name,
                "success": success,
                "duration_ms": duration_ms,
            },
        )

    async def publish_sse_message(
            self, thread_id: str, run_id: str, message: str, role: str = "assistant"
    ) -> str:
        """Публикация произвольного сообщения для фронтенда (SSE)."""
        return await self.publish_event(
            event_type=EventType.SSE_MESSAGE,
            thread_id=thread_id,
            run_id=run_id,
            data={"message": message, "role": role},
        )

    async def publish_error(
            self, thread_id: str, run_id: str, error: Exception, node_name: Optional[str] = None
    ) -> str:
        """Публикация события ошибки."""
        return await self.publish_event(
            event_type=EventType.ERROR,
            thread_id=thread_id,
            run_id=run_id,
            data={"error": str(error), "error_type": type(error).__name__},
            node_name=node_name,
        )


# Глобальный экземпляр шины событий
event_bus = EventBus()