# vm1/gateway/app/main.py
"""
AG-UI Gateway — FastAPI-приложение периметра ВМ №1.
Production Blueprint v5.2.1

- приём RunAgentInput от браузерных клиентов через nginx (auth_request);
- атомарный входной барьер: runs + thread_locks (дубликат/занятый тред → 409);
  stale-локи (heartbeat воркера не пришёл 5+ мин) освобождаются автоматически;
- постановка задач и resume в очередь Redis фабрики (TaskIQ ListQueueBroker);
- SSE-relay из per-run ленты agui:run:{run_id}:events (XREAD BLOCK);
- реконнект по Last-Event-ID; снапшот при пустой ленте запрашивается у воркера;
- resume HITL с идемпотентностью через interrupt_decisions;
- кооперативная отмена ранов.

Трейсинг Langfuse временно отключён (API SDK 5.2.0 требует уточнения сигнатур).
"""
import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

import orjson
import redis.asyncio as redis
import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from taskiq_redis import ListQueueBroker

_settings_log_level = logging.INFO

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(_settings_log_level),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=False,
)
logger = structlog.get_logger()


class Settings(BaseSettings):
    environment: str = "production"
    log_level: str = "info"
    redis_factory_url: str = "rediss://agui_svc:password@10.0.0.2:6379"
    task_queue_name: str = "agui:tasks"
    default_graph_name: str = "document_processing"
    postgres_runtime_url: str = (
        "postgresql://factory_gateway:password@10.0.0.2:5432/factory?sslmode=require"
    )
    stale_lock_ttl_minutes: int = 5

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()

TASK_QUEUE_KEY = settings.task_queue_name
TASK_RUN_AGENT = "factory.run_agent"
TASK_RESUME_RUN = "factory.resume_run"
TASK_PUBLISH_SNAPSHOT = "factory.publish_snapshot"

broker = ListQueueBroker(url=settings.redis_factory_url, queue_name=TASK_QUEUE_KEY)


@broker.task(task_name=TASK_RUN_AGENT)
async def _proxy_run_agent(*args, **kwargs):
    raise NotImplementedError("Proxy task — executed on Worker")


@broker.task(task_name=TASK_RESUME_RUN)
async def _proxy_resume_run(*args, **kwargs):
    raise NotImplementedError("Proxy task — executed on Worker")


@broker.task(task_name=TASK_PUBLISH_SNAPSHOT)
async def _proxy_publish_snapshot(*args, **kwargs):
    raise NotImplementedError("Proxy task — executed on Worker")


class RunAgentInput(BaseModel):
    thread_id: str = Field(..., description="Идентификатор треда")
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    messages: list[dict] = Field(default_factory=list)
    state: dict = Field(default_factory=dict)
    tools: list[dict] = Field(default_factory=list)
    context: dict = Field(default_factory=dict)
    forwarded_props: dict = Field(default_factory=dict)


class RunStatus(BaseModel):
    run_id: str
    thread_id: str
    status: str
    user: str
    created_at: str
    updated_at: str


class ResumeRequest(BaseModel):
    interrupt_id: str
    value: dict


def _stream_key(run_id: str) -> str:
    return f"agui:run:{run_id}:events"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Starting AG-UI Gateway", environment=settings.environment)

    app.state.redis = redis.from_url(settings.redis_factory_url, decode_responses=True)
    try:
        await app.state.redis.ping()
        logger.info("Redis connection established")
    except Exception as e:
        logger.error("Failed to connect to Redis", error=str(e))
        raise

    app.state.pg_pool = AsyncConnectionPool(
        conninfo=settings.postgres_runtime_url,
        min_size=2,
        max_size=10,
        open=False,
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0},
    )
    await app.state.pg_pool.open()
    try:
        async with app.state.pg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
        logger.info("PostgreSQL connection established")
    except Exception as e:
        logger.error("Failed to connect to PostgreSQL", error=str(e))
        raise

    await broker.startup()
    app.state.broker = broker
    logger.info("Taskiq broker initialized", queue=TASK_QUEUE_KEY)

    yield

    logger.info("Shutting down AG-UI Gateway")
    await app.state.redis.close()
    await app.state.pg_pool.close()
    await broker.shutdown()


app = FastAPI(
    title="AG-UI Gateway",
    description="FastAPI gateway для протокола AG-UI",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://factory.corp.example.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def extract_user_headers(request: Request, call_next):
    user = request.headers.get("X-Forwarded-User", "anonymous")
    groups = request.headers.get("X-Forwarded-Groups", "")
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(user=user, groups=groups)
    return await call_next(request)


@app.get("/health")
async def health():
    try:
        await app.state.redis.ping()
        async with app.state.pg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
        return {"status": "healthy", "redis": "ok", "postgres": "ok"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Unhealthy: {str(e)}")


async def _sse_loop(stream_key: str, last_id: str, run_id: str) -> AsyncGenerator[str, None]:
    try:
        while True:
            result = await app.state.redis.xread({stream_key: last_id}, count=100, block=10000)
            if not result:
                yield ": heartbeat\n\n"
                continue
            for _, messages in result:
                for msg_id, msg_data in messages:
                    last_id = msg_id
                    event_type = msg_data.get("type", "custom")
                    event_data = msg_data.get("data", "{}")
                    yield f"id: {msg_id}\nevent: {event_type}\ndata: {event_data}\n\n"
                    if event_type in ("RUN_FINISHED", "RUN_ERROR"):
                        return
    except asyncio.CancelledError:
        logger.info("SSE connection closed", run_id=run_id)
    except Exception as e:
        logger.error("SSE error", run_id=run_id, error=str(e))
        yield f"event: error\ndata: {str(e)}\n\n"


async def _resolve_run_for_thread(thread_id: str) -> Optional[str]:
    async with app.state.pg_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT run_id FROM runs
                WHERE thread_id = %s
                ORDER BY (status IN ('QUEUED','RUNNING','WAITING_HUMAN')) DESC, created_at DESC
                LIMIT 1
                """,
                (thread_id,),
            )
            row = await cur.fetchone()
            return str(row["run_id"]) if row else None


async def _free_stale_lock(conn, thread_id: str) -> None:
    """Освобождает thread_locks, у которых heartbeat не приходил > TTL."""
    async with conn.cursor() as cur:
        await cur.execute(
            "DELETE FROM thread_locks WHERE thread_id = %s AND locked_at < NOW() - make_interval(mins => %s)",
            (thread_id, settings.stale_lock_ttl_minutes),
        )


@app.post("/api/agent")
async def run_agent(input: RunAgentInput, request: Request):
    user = request.headers.get("X-Forwarded-User", "anonymous")
    graph_name = str(input.context.get("graph_name") or settings.default_graph_name)
    now = datetime.now(timezone.utc)
    logger.info("RunAgentInput received", thread_id=input.thread_id,
                run_id=input.run_id, user=user, graph_name=graph_name)

    run_payload = {
        "graph_name": graph_name,
        "messages": input.messages,
        "state": input.state,
        "tools": input.tools,
        "context": input.context,
        "forwarded_props": input.forwarded_props,
    }

    try:
        async with app.state.pg_pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    # Освобождение stale lock перед INSERT
                    await _free_stale_lock(conn, input.thread_id)

                    await cur.execute(
                        """
                        INSERT INTO runs (run_id, thread_id, status, "user", payload, created_at, updated_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (run_id) DO NOTHING
                        """,
                        (input.run_id, input.thread_id, "QUEUED", user,
                         orjson.dumps(run_payload).decode("utf-8"), now, now),
                    )
                    if cur.rowcount == 0:
                        raise HTTPException(status_code=409, detail="Duplicate run_id")
                    await cur.execute(
                        """
                        INSERT INTO thread_locks (thread_id, run_id, locked_by, locked_at)
                        VALUES (%s,%s,%s,%s)
                        ON CONFLICT (thread_id) DO NOTHING
                        """,
                        (input.thread_id, input.run_id, user, now),
                    )
                    if cur.rowcount == 0:
                        raise HTTPException(status_code=409, detail="Thread already has an active run")
        logger.info("Run created in registry", run_id=input.run_id, status="QUEUED")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to create run in registry", run_id=input.run_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to create run")

    initial_state = {**input.state, "run_id": input.run_id,
                     "thread_id": input.thread_id, "user_id": user}

    try:
        await _proxy_run_agent.kiq(input.run_id, input.thread_id, graph_name, initial_state)
        logger.info("Task enqueued via Taskiq", run_id=input.run_id, graph_name=graph_name)
    except Exception as e:
        logger.error("Failed to enqueue task", run_id=input.run_id, error=str(e))
        async with app.state.pg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE runs SET status=%s, updated_at=%s WHERE run_id=%s",
                    ("FAILED", datetime.now(timezone.utc), input.run_id),
                )
                await cur.execute("DELETE FROM thread_locks WHERE run_id=%s", (input.run_id,))
        raise HTTPException(status_code=500, detail="Failed to enqueue task")

    return StreamingResponse(
        _sse_loop(_stream_key(input.run_id), "0-0", input.run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.get("/api/threads/{thread_id}/events")
async def reconnect_events(thread_id: str, request: Request, last_event_id: Optional[str] = None):
    user = request.headers.get("X-Forwarded-User", "anonymous")
    run_id = await _resolve_run_for_thread(thread_id)
    if not run_id:
        raise HTTPException(status_code=404, detail="No run found for this thread")
    key = _stream_key(run_id)
    start_id = request.headers.get("Last-Event-ID") or last_event_id or "0-0"
    logger.info("Reconnect requested", thread_id=thread_id, run_id=run_id,
                last_event_id=start_id, user=user)

    async def gen():
        caught = await app.state.redis.xread({key: start_id}, count=100)
        if not caught:
            async with app.state.pg_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT status FROM runs WHERE run_id=%s", (run_id,))
                    row = await cur.fetchone()
            if row and row["status"] in ("QUEUED", "RUNNING", "WAITING_HUMAN"):
                await _proxy_publish_snapshot.kiq(run_id, thread_id, settings.default_graph_name)
        async for chunk in _sse_loop(key, start_id, run_id):
            yield chunk

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.post("/api/threads/{thread_id}/resume")
async def resume_run(thread_id: str, resume_req: ResumeRequest, request: Request):
    user = request.headers.get("X-Forwarded-User", "anonymous")
    logger.info("Resume requested", thread_id=thread_id,
                interrupt_id=resume_req.interrupt_id, user=user)

    active_run_id = None
    graph_name = settings.default_graph_name
    try:
        async with app.state.pg_pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT run_id, status, interrupt_id, payload FROM runs
                        WHERE thread_id = %s AND status IN ('RUNNING','WAITING_HUMAN')
                        ORDER BY created_at DESC LIMIT 1
                        """,
                        (thread_id,),
                    )
                    active = await cur.fetchone()
                    if not active:
                        await cur.execute(
                            "SELECT status FROM runs WHERE thread_id=%s AND interrupt_id=%s",
                            (thread_id, resume_req.interrupt_id),
                        )
                        prior = await cur.fetchone()
                        if prior and prior["status"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
                            raise HTTPException(
                                status_code=409,
                                detail=f"Interrupt already processed: {prior['status']}",
                            )
                        raise HTTPException(status_code=404, detail="No running run found for this thread")

                    active_run_id = active["run_id"]
                    current_interrupt_id = active["interrupt_id"]
                    if current_interrupt_id and str(current_interrupt_id) != resume_req.interrupt_id:
                        raise HTTPException(status_code=409, detail="Interrupt id mismatch")
                    payload = active["payload"] if isinstance(active["payload"], dict) else {}
                    graph_name = str(payload.get("graph_name") or settings.default_graph_name)

                    await cur.execute(
                        """
                        INSERT INTO interrupt_decisions (run_id, interrupt_id, "user", decision)
                        VALUES (%s,%s,%s,%s)
                        ON CONFLICT (run_id, interrupt_id) DO NOTHING
                        """,
                        (str(active_run_id), resume_req.interrupt_id, user,
                         orjson.dumps(resume_req.value).decode("utf-8")),
                    )
                    if cur.rowcount == 0:
                        raise HTTPException(status_code=409, detail="Interrupt decision already recorded")

                    await cur.execute(
                        "UPDATE runs SET interrupt_id=%s, updated_at=%s WHERE run_id=%s",
                        (resume_req.interrupt_id, datetime.now(timezone.utc), str(active_run_id)),
                    )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to update run status", thread_id=thread_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to update run status")

    try:
        await _proxy_resume_run.kiq(str(active_run_id), thread_id, graph_name,
                                    resume_req.interrupt_id, resume_req.value)
        logger.info("Resume task enqueued via Taskiq", thread_id=thread_id,
                    run_id=str(active_run_id), interrupt_id=resume_req.interrupt_id)
    except Exception as e:
        async with app.state.pg_pool.connection() as conn:
            await conn.execute(
                "DELETE FROM interrupt_decisions WHERE run_id=%s AND interrupt_id=%s",
                (str(active_run_id), resume_req.interrupt_id),
            )
        logger.error("Resume enqueue failed, decision rolled back",
                     thread_id=thread_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to enqueue resume task")

    return JSONResponse({"status": "accepted", "thread_id": thread_id})


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request):
    user = request.headers.get("X-Forwarded-User", "anonymous")
    logger.info("Cancel requested", run_id=run_id, user=user)
    try:
        async with app.state.pg_pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        UPDATE runs SET status=%s, updated_at=%s
                        WHERE run_id=%s AND status IN ('QUEUED','RUNNING','WAITING_HUMAN')
                        """,
                        ("CANCELLED", datetime.now(timezone.utc), run_id),
                    )
                    if cur.rowcount == 0:
                        raise HTTPException(status_code=404, detail="Run not found or already completed")
                    await cur.execute("DELETE FROM thread_locks WHERE run_id=%s", (run_id,))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to cancel run", run_id=run_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to cancel run")
    logger.info("Run cancelled", run_id=run_id)
    return JSONResponse({"status": "accepted", "run_id": run_id})


@app.get("/api/runs/{run_id}/status")
async def get_run_status(run_id: str, request: Request):
    try:
        async with app.state.pg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    'SELECT run_id, thread_id, status, "user", created_at, updated_at FROM runs WHERE run_id=%s',
                    (run_id,),
                )
                row = await cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail="Run not found")
                return RunStatus(
                    run_id=str(row["run_id"]),
                    thread_id=str(row["thread_id"]),
                    status=row["status"],
                    user=row["user"],
                    created_at=row["created_at"].isoformat() if row["created_at"] else "",
                    updated_at=row["updated_at"].isoformat() if row["updated_at"] else "",
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get run status", run_id=run_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to get run status")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception", error=str(exc), exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, workers=4,
                log_level=settings.log_level.lower())