# vm1/gateway/app/main.py
"""
AG-UI Gateway — FastAPI-приложение периметра ВМ №1.
Production Blueprint v5.2.1.

Модуль реализует шлюз протокола AG-UI между SPA и фабрикой агентов:

- приём RunAgentInput от браузерных клиентов через nginx (auth_request);
- регистрация рана в реестре ранов PostgreSQL ВМ №2 со статусом QUEUED;
- постановка задач исполнения и резюма в очередь Redis фабрики ВМ №2
  через TaskIQ ListQueueBroker (очередь agui:tasks);
- SSE-relay событий воркеров из Redis Streams (XREAD BLOCK) к клиентам;
- реконнект потока событий по thread_id с заголовком Last-Event-ID;
- resume HITL-пауз с идемпотентностью по interrupt_id;
- кооперативная отмена ранов через статус в реестре;
- чтение статуса рана из реестра;
- трейсинг операций шлюза в Langfuse.

Шлюз не исполняет графы — исполнение выполняет Worker на ВМ №2.

Контракт задач с Worker (позиционные аргументы):
- factory.run_agent(run_id, thread_id, graph_name, initial_state)
- factory.resume_run(run_id, thread_id, graph_name, interrupt_id, human_response)

Идентификаторы исполнения (run_id, thread_id, user_id) инжектируются
шлюзом в initial_state: они необходимы узлам графа и event_bus.

Постановка задач реализована через прокси-функции, зарегистрированные
на брокере с теми же task_name, что и на воркере. Вызов .kiq() отправляет
сообщение в очередь Redis, откуда его забирает воркер на ВМ №2.
"""

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncGenerator, Optional

import orjson
import redis.asyncio as redis
import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from langfuse import Langfuse
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
    redis_factory_tls: bool = True
    task_queue_name: str = "agui:tasks"
    default_graph_name: str = "document_processing"
    postgres_runtime_url: str = "postgresql+ssl://factory_gateway:password@10.0.0.2:5432/factory?sslmode=require"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://langfuse-web:3000"
    sse_heartbeat_interval: int = 15
    sse_reconnect_timeout: int = 300

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()

TASK_QUEUE_KEY = settings.task_queue_name
TASK_RUN_AGENT = "factory.run_agent"
TASK_RESUME_RUN = "factory.resume_run"

broker = ListQueueBroker(
    url=settings.redis_factory_url,
    queue_name=TASK_QUEUE_KEY,
)


@broker.task(task_name=TASK_RUN_AGENT)
async def _proxy_run_agent(*args, **kwargs):
    raise NotImplementedError("Proxy task — executed on Worker")


@broker.task(task_name=TASK_RESUME_RUN)
async def _proxy_resume_run(*args, **kwargs):
    raise NotImplementedError("Proxy task — executed on Worker")


class RunAgentInput(BaseModel):
    thread_id: str = Field(..., description="Идентификатор треда (диалога)")
    run_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Идентификатор рана",
    )
    messages: list[dict] = Field(default_factory=list, description="История сообщений")
    state: dict = Field(default_factory=dict, description="Состояние графа")
    tools: list[dict] = Field(default_factory=list, description="Доступные инструменты")
    context: dict = Field(default_factory=dict, description="Контекст выполнения")
    forwarded_props: dict = Field(default_factory=dict, description="Проброшенные свойства")


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


def _stream_key(thread_id: str) -> str:
    return f"agui:stream:{thread_id}"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Starting AG-UI Gateway", environment=settings.environment)

    app.state.redis = redis.from_url(
        settings.redis_factory_url,
        decode_responses=True,
    )
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
        kwargs={
            "autocommit": True,
            "row_factory": None,
        },
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

    app.state.langfuse = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )
    logger.info("Langfuse client initialized")

    yield

    logger.info("Shutting down AG-UI Gateway")
    await app.state.redis.close()
    await app.state.pg_pool.close()
    await broker.shutdown()
    app.state.langfuse.flush()


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
    response = await call_next(request)
    return response


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


@app.post("/api/agent")
async def run_agent(input: RunAgentInput, request: Request):
    user = request.headers.get("X-Forwarded-User", "anonymous")
    graph_name = str(input.context.get("graph_name") or settings.default_graph_name)
    logger.info(
        "RunAgentInput received",
        thread_id=input.thread_id,
        run_id=input.run_id,
        user=user,
        graph_name=graph_name,
    )

    app.state.langfuse.trace(
        name="gateway.run_agent",
        metadata={
            "thread_id": input.thread_id,
            "run_id": input.run_id,
            "user": user,
            "graph_name": graph_name,
        },
    )

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
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO runs (run_id, thread_id, status, "user", payload, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id) DO NOTHING
                    """,
                    (
                        input.run_id,
                        input.thread_id,
                        "QUEUED",
                        user,
                        orjson.dumps(run_payload).decode("utf-8"),
                        datetime.utcnow(),
                        datetime.utcnow(),
                    ),
                )
        logger.info("Run created in registry", run_id=input.run_id, status="QUEUED")
    except Exception as e:
        logger.error("Failed to create run in registry", run_id=input.run_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to create run")

    initial_state = {
        **input.state,
        "run_id": input.run_id,
        "thread_id": input.thread_id,
        "user_id": user,
    }

    try:
        await _proxy_run_agent.kiq(input.run_id, input.thread_id, graph_name, initial_state)
        logger.info("Task enqueued via Taskiq", run_id=input.run_id, graph_name=graph_name)
    except Exception as e:
        logger.error("Failed to enqueue task", run_id=input.run_id, error=str(e))
        async with app.state.pg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE runs
                    SET status = %s, updated_at = %s
                    WHERE run_id = %s
                    """,
                    ("FAILED", datetime.utcnow(), input.run_id),
                )
        raise HTTPException(status_code=500, detail="Failed to enqueue task")

    async def event_generator() -> AsyncGenerator[str, None]:
        stream_key = _stream_key(input.thread_id)
        last_id = "$"
        try:
            while True:
                result = await app.state.redis.xread(
                    {stream_key: last_id},
                    count=10,
                    block=5000,
                )
                if result:
                    for _, messages in result:
                        for msg_id, msg_data in messages:
                            last_id = msg_id
                            event_type = msg_data.get("type", "custom")
                            event_data = msg_data.get("data", "{}")
                            yield (
                                f"id: {msg_id}\n"
                                f"event: {event_type}\n"
                                f"data: {event_data}\n\n"
                            )
                            if event_type in ("RUN_FINISHED", "RUN_ERROR"):
                                logger.info(
                                    "Run finished",
                                    run_id=input.run_id,
                                    event_type=event_type,
                                )
                                return
                yield ": heartbeat\n\n"
                await asyncio.sleep(settings.sse_heartbeat_interval)
        except asyncio.CancelledError:
            logger.info("SSE connection closed by client", run_id=input.run_id)
        except Exception as e:
            logger.error("SSE error", run_id=input.run_id, error=str(e))
            yield f"event: error\ndata: {str(e)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/threads/{thread_id}/events")
async def reconnect_events(
    thread_id: str,
    request: Request,
    last_event_id: Optional[str] = None,
):
    user = request.headers.get("X-Forwarded-User", "anonymous")
    header_last_id = request.headers.get("Last-Event-ID")
    if header_last_id:
        start_id = header_last_id
    else:
        start_id = last_event_id if last_event_id else "0-0"
    logger.info(
        "Reconnect requested",
        thread_id=thread_id,
        last_event_id=start_id,
        user=user,
    )
    stream_key = _stream_key(thread_id)

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            result = await app.state.redis.xread({stream_key: start_id}, count=100)
            last_id = start_id
            if result:
                for _, messages in result:
                    for msg_id, msg_data in messages:
                        last_id = msg_id
                        event_type = msg_data.get("type", "custom")
                        event_data = msg_data.get("data", "{}")
                        yield (
                            f"id: {msg_id}\n"
                            f"event: {event_type}\n"
                            f"data: {event_data}\n\n"
                        )
            while True:
                result = await app.state.redis.xread(
                    {stream_key: last_id}, count=10, block=5000
                )
                if result:
                    for _, messages in result:
                        for msg_id, msg_data in messages:
                            last_id = msg_id
                            event_type = msg_data.get("type", "custom")
                            event_data = msg_data.get("data", "{}")
                            yield (
                                f"id: {msg_id}\n"
                                f"event: {event_type}\n"
                                f"data: {event_data}\n\n"
                            )
                            if event_type in ("RUN_FINISHED", "RUN_ERROR"):
                                return
                yield ": heartbeat\n\n"
                await asyncio.sleep(settings.sse_heartbeat_interval)
        except asyncio.CancelledError:
            logger.info("Reconnect SSE closed", thread_id=thread_id)
        except Exception as e:
            logger.error("Reconnect SSE error", thread_id=thread_id, error=str(e))
            yield f"event: error\ndata: {str(e)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/threads/{thread_id}/resume")
async def resume_run(thread_id: str, resume_req: ResumeRequest, request: Request):
    user = request.headers.get("X-Forwarded-User", "anonymous")
    logger.info(
        "Resume requested",
        thread_id=thread_id,
        interrupt_id=resume_req.interrupt_id,
        user=user,
    )

    app.state.langfuse.trace(
        name="gateway.resume_run",
        metadata={
            "thread_id": thread_id,
            "interrupt_id": resume_req.interrupt_id,
            "user": user,
        },
    )

    try:
        async with app.state.pg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT run_id, status FROM runs
                    WHERE thread_id = %s AND interrupt_id = %s
                    """,
                    (thread_id, resume_req.interrupt_id),
                )
                existing = await cur.fetchone()
                if existing:
                    existing_run_id, existing_status = existing
                    if existing_status in ("SUCCEEDED", "FAILED", "CANCELLED"):
                        logger.warning(
                            "Resume rejected: interrupt already processed",
                            thread_id=thread_id,
                            interrupt_id=resume_req.interrupt_id,
                            existing_status=existing_status,
                        )
                        raise HTTPException(
                            status_code=409,
                            detail=f"Interrupt already processed: {existing_status}",
                        )
                    if existing_status == "WAITING_HUMAN":
                        logger.info(
                            "Resume idempotent: already waiting",
                            thread_id=thread_id,
                            interrupt_id=resume_req.interrupt_id,
                        )
                        return JSONResponse(
                            {"status": "already_waiting", "thread_id": thread_id}
                        )

                await cur.execute(
                    """
                    SELECT run_id, payload FROM runs
                    WHERE thread_id = %s AND status IN ('RUNNING', 'WAITING_HUMAN')
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (thread_id,),
                )
                active = await cur.fetchone()
                if not active:
                    logger.error("Resume failed: no running run found", thread_id=thread_id)
                    raise HTTPException(
                        status_code=404,
                        detail="No running run found for this thread",
                    )
                active_run_id, active_payload = active
                if not isinstance(active_payload, dict):
                    active_payload = {}
                graph_name = str(active_payload.get("graph_name") or settings.default_graph_name)

                await cur.execute(
                    """
                    UPDATE runs
                    SET status = %s, interrupt_id = %s, updated_at = %s
                    WHERE thread_id = %s AND status IN ('RUNNING', 'WAITING_HUMAN')
                    """,
                    ("WAITING_HUMAN", resume_req.interrupt_id, datetime.utcnow(), thread_id),
                )
                if cur.rowcount == 0:
                    logger.error("Resume failed: no running run found", thread_id=thread_id)
                    raise HTTPException(
                        status_code=404,
                        detail="No running run found for this thread",
                    )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to update run status", thread_id=thread_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to update run status")

    try:
        await _proxy_resume_run.kiq(
            str(active_run_id),
            thread_id,
            graph_name,
            resume_req.interrupt_id,
            resume_req.value,
        )
        logger.info(
            "Resume task enqueued via Taskiq",
            thread_id=thread_id,
            run_id=str(active_run_id),
            interrupt_id=resume_req.interrupt_id,
        )
    except Exception as e:
        logger.error("Failed to enqueue resume task", thread_id=thread_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to enqueue resume task")

    return JSONResponse({"status": "accepted", "thread_id": thread_id})


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request):
    user = request.headers.get("X-Forwarded-User", "anonymous")
    logger.info("Cancel requested", run_id=run_id, user=user)

    app.state.langfuse.trace(
        name="gateway.cancel_run",
        metadata={
            "run_id": run_id,
            "user": user,
        },
    )

    try:
        async with app.state.pg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE runs
                    SET status = %s, updated_at = %s
                    WHERE run_id = %s AND status IN ('QUEUED', 'RUNNING', 'WAITING_HUMAN')
                    """,
                    ("CANCELLED", datetime.utcnow(), run_id),
                )
                if cur.rowcount == 0:
                    logger.warning(
                        "Cancel failed: run not found or already terminal",
                        run_id=run_id,
                    )
                    raise HTTPException(
                        status_code=404,
                        detail="Run not found or already completed",
                    )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to cancel run", run_id=run_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to cancel run")

    logger.info("Run cancelled", run_id=run_id)
    return JSONResponse({"status": "accepted", "run_id": run_id})


@app.get("/api/runs/{run_id}/status")
async def get_run_status(run_id: str, request: Request):
    request.headers.get("X-Forwarded-User", "anonymous")
    try:
        async with app.state.pg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT run_id, thread_id, status, "user", created_at, updated_at
                    FROM runs
                    WHERE run_id = %s
                    """,
                    (run_id,),
                )
                row = await cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail="Run not found")
                return RunStatus(
                    run_id=str(row[0]),
                    thread_id=str(row[1]),
                    status=row[2],
                    user=row[3],
                    created_at=row[4].isoformat() if row[4] else "",
                    updated_at=row[5].isoformat() if row[5] else "",
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get run status", run_id=run_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to get run status")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception", error=str(exc), exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        workers=4,
        log_level=settings.log_level.lower(),
    )