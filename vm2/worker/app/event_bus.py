# vm2/worker/app/event_bus.py
"""AG-UI события → Redis Streams. Лента на ран: agui:run:{run_id}:events."""
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

import redis.asyncio as redis
import structlog

from worker.app.settings import get_settings

logger = structlog.get_logger()

STREAM_TTL_SECONDS = 30 * 86400  # §3.1: горизонт HITL-паузы
STREAM_MAXLEN = 10000


class EventType(str, Enum):
    RUN_STARTED = "RUN_STARTED"
    RUN_FINISHED = "RUN_FINISHED"
    RUN_ERROR = "RUN_ERROR"          # было RUN_FAILED — канон AG-UI
    NODE_STARTED = "NODE_STARTED"
    NODE_FINISHED = "NODE_FINISHED"
    HITL_INTERRUPT = "HITL_INTERRUPT"
    HITL_RESUMED = "HITL_RESUMED"
    TOOL_EXECUTED = "TOOL_EXECUTED"
    SSE_MESSAGE = "SSE_MESSAGE"
    STATE_SNAPSHOT = "STATE_SNAPSHOT"
    ERROR = "ERROR"


def stream_key(run_id: str) -> str:
    return f"agui:run:{run_id}:events"


class EventBus:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self._redis: Optional[redis.Redis] = None

    async def _get_redis(self) -> redis.Redis:
        if self._redis is None:
            self._redis = redis.from_url(self.settings.redis_factory_url, decode_responses=True)
        return self._redis

    async def close(self) -> None:
        if self._redis:
            await self._redis.close()
            self._redis = None

    async def publish_event(self, event_type: EventType, thread_id: str, run_id: str,
                            data: Dict[str, Any], node_name: Optional[str] = None) -> str:
        client = await self._get_redis()
        key = stream_key(run_id)
        event = {
            "type": event_type.value,
            "run_id": run_id,
            "thread_id": thread_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": json.dumps(data, ensure_ascii=False, default=str),
        }
        if node_name:
            event["node"] = node_name
        pipe = client.pipeline(transaction=False)
        pipe.xadd(key, event, maxlen=STREAM_MAXLEN, approximate=True)
        pipe.expire(key, STREAM_TTL_SECONDS)
        results = await pipe.execute()
        return results[0]

    # --- обёртки (сигнатуры прежние, чтобы граф не менять) ---
    async def publish_run_started(self, thread_id, run_id, graph_name, user_id):
        return await self.publish_event(EventType.RUN_STARTED, thread_id, run_id,
                                        {"graph_name": graph_name, "user_id": user_id})

    async def publish_run_finished(self, thread_id, run_id, final_state):
        return await self.publish_event(EventType.RUN_FINISHED, thread_id, run_id,
                                        {"final_state": final_state})

    async def publish_run_failed(self, thread_id, run_id, error_message):
        return await self.publish_event(EventType.RUN_ERROR, thread_id, run_id,
                                        {"error": error_message})

    async def publish_node_started(self, thread_id, run_id, node_name):
        return await self.publish_event(EventType.NODE_STARTED, thread_id, run_id, {}, node_name)

    async def publish_node_finished(self, thread_id, run_id, node_name, output=None):
        return await self.publish_event(EventType.NODE_FINISHED, thread_id, run_id,
                                        {"output": output or {}}, node_name)

    async def publish_hitl_interrupt(self, thread_id, run_id, interrupt_id, form_data):
        return await self.publish_event(EventType.HITL_INTERRUPT, thread_id, run_id,
                                        {"interrupt_id": interrupt_id, "form": form_data})

    async def publish_hitl_resumed(self, thread_id, run_id, interrupt_id, decision):
        return await self.publish_event(EventType.HITL_RESUMED, thread_id, run_id,
                                        {"interrupt_id": interrupt_id, "decision": decision})

    async def publish_tool_executed(self, thread_id, run_id, tool_name, success, duration_ms=None):
        return await self.publish_event(EventType.TOOL_EXECUTED, thread_id, run_id,
                                        {"tool_name": tool_name, "success": success, "duration_ms": duration_ms})

    async def publish_sse_message(self, thread_id, run_id, message, role="assistant"):
        return await self.publish_event(EventType.SSE_MESSAGE, thread_id, run_id,
                                        {"message": message, "role": role})

    async def publish_error(self, thread_id, run_id, error, node_name=None):
        return await self.publish_event(EventType.ERROR, thread_id, run_id,
                                        {"error": str(error), "error_type": type(error).__name__}, node_name)

    async def publish_state_snapshot(self, thread_id, run_id, state_values):
        return await self.publish_event(EventType.STATE_SNAPSHOT, thread_id, run_id,
                                        {"state": state_values})


event_bus = EventBus()