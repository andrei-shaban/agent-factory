# vm1/gateway/app/main.py
"""
AG-UI Gateway — FastAPI application.
Production Blueprint v5.2 — ВМ №1

Обязанности:
  - Приём RunAgentInput от AG-UI клиентов (браузеров)
  - Постановка задач в Redis фабрики (ВМ №2) через Taskiq broker
  - SSE-relay событий от воркеров к браузерам (XREAD BLOCK из Redis Streams)
  - Реконнект по thread_id с Last-Event-ID
  - Resume HITL-пауз с идемпотентностью
  - Отмена ранов через флаг в реестре
  - Реестр ранов в PostgreSQL (ВМ №2)
  - Трейсинг операций в Langfuse

ВАЖНО: Gateway НЕ исполняет граф — это задача Worker на ВМ №2.

Исправления (Blueprint v5.2):
  - Taskiq broker вместо прямого LPUSH (критическое №1)
  - PostgreSQL pool для реестра ранов (критическое №3)
  - Langfuse client для трейсинга (среднее №1)
  - Идемпотентность resume по interrupt_id (среднее №2)
  - Cancel run через флаг в БД (среднее №3)
  - Реестр ранов с полным жизненным циклом статусов
  - TLS для PostgreSQL runtime (Шаг 3): postgresql+ssl://...?sslmode=require
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
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from langfuse import Langfuse
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from taskiq import TaskiqScheduler
from taskiq_redis import ListQueueBroker

# ==============================================================================
# LOGGING
# ==============================================================================
# В продакшене используем JSON-рендерер для агрегации логов.
# В разработке можно переключить на Console-рендерер.

_settings_log_level = logging.INFO

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
        # JSON-рендерер для продакшена
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(_settings_log_level),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=False,
)

logger = structlog.get_logger()


# ==============================================================================
# SETTINGS
# ==============================================================================

class Settings(BaseSettings):
    """Конфигурация из переменных окружения (.env)."""

    environment: str = "production"
    log_level: str = "info"

    # Redis фабрики (ВМ №2)
    # Адрес ВМ №2 задаётся переменной VM2_HOST_IP в .env
    redis_factory_url: str = "rediss://agui_svc:password@10.0.0.2:6379"
    redis_factory_tls: bool = True

    # Имя очереди для постановки задач (согласовано с broker ВМ №2)
    task_queue_name: str = "factory:tasks"

    # PostgreSQL runtime (ВМ №2) — для реестра ранов
    # Схема postgresql+ssl:// с sslmode=require — для TLS-подключения (Blueprint §3.1)
    postgres_runtime_url: str = "postgresql+ssl://factory_gateway:password@10.0.0.2:5432/factory?sslmode=require"

    # Langfuse
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://langfuse-web:3000"

    # SSE
    sse_heartbeat_interval: int = 15  # секунды
    sse_reconnect_timeout: int = 300  # секунд

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()

# Имя очереди — единое для постановки задач и реконнекта
TASK_QUEUE_KEY = settings.task_queue_name

# Имена задач Taskiq (согласованы с Worker на ВМ №2)
TASK_RUN_AGENT = "factory.run_agent"
TASK_RESUME_RUN = "factory.resume_run"


# ==============================================================================
# MODELS
# ==============================================================================

class RunAgentInput(BaseModel):
    """Входной контракт AG-UI протокола."""
    thread_id: str = Field(..., description="Идентификатор треда (диалога)")
    run_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Идентификатор рана",
    )
    messages: list[dict] = Field(default_factory=list, description="История сообщений")
    state: dict = Field(default_factory=dict, description="Состояние графа")
    tools: list[dict] = Field(default_factory=list, description="Доступные инструменты")
    context: dict = Field(default_factory=dict, description="Контекст выполнения")
    forwarded_props: dict = Field(
        default_factory=dict, description="Проброшенные свойства"
    )


class RunStatus(BaseModel):
    """Статус рана из реестра."""
    run_id: str
    thread_id: str
    status: str  # QUEUED, RUNNING, WAITING_HUMAN, SUCCEEDED, FAILED, CANCELLED
    user: str
    created_at: str
    updated_at: str


class ResumeRequest(BaseModel):
    """Запрос на resume HITL-паузы."""
    interrupt_id: str
    value: dict


# ==============================================================================
# HELPERS
# ==============================================================================

def _serialize(obj) -> bytes:
    """
    Сериализация в JSON-байты через orjson (быстрее стандартного).
    Используется для постановки задач в очередь.
    """
    return orjson.dumps(obj)


def _stream_key(thread_id: str) -> str:
    """
    Ключ стрима для событий треда.
    Согласован с event_bus ВМ №2: `agui:stream:{thread_id}`.
    """
    return f"agui:stream:{thread_id}"


# ==============================================================================
# LIFESPAN
# ==============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup / shutdown."""
    logger.info("Starting AG-UI Gateway", environment=settings.environment)

    # Redis connection pool
    # redis:// or rediss:// selects plaintext/TLS. Do not pass redis-py ssl kwargs,
    # which are incompatible with the pinned Redis 8 client.
    app.state.redis = redis.from_url(
        settings.redis_factory_url,
        decode_responses=True,
    )

    # Проверка подключения к Redis
    try:
        await app.state.redis.ping()
        logger.info("Redis connection established")
    except Exception as e:
        logger.error("Failed to connect to Redis", error=str(e))
        raise

    # PostgreSQL connection pool (для реестра ранов)
    # Blueprint §3.1: autocommit=True, row_factory=dict_row, prepare_threshold=0
    # TLS включён через схему postgresql+ssl:// и sslmode=require в URL
    app.state.pg_pool = AsyncConnectionPool(
        conninfo=settings.postgres_runtime_url,
        min_size=2,
        max_size=10,
        kwargs={
            "autocommit": True,
            "row_factory": None,  # Будем использовать dict_row в запросах
        },
    )

    # Проверка подключения к PostgreSQL
    try:
        async with app.state.pg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
        logger.info("PostgreSQL connection established")
    except Exception as e:
        logger.error("Failed to connect to PostgreSQL", error=str(e))
        raise

    # Taskiq broker (для постановки задач в Worker)
    app.state.broker = ListQueueBroker(url=settings.redis_factory_url)
    logger.info("Taskiq broker initialized")

    # Langfuse client (для трейсинга операций Gateway)
    app.state.langfuse = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )
    logger.info("Langfuse client initialized")

    yield

    # Shutdown
    logger.info("Shutting down AG-UI Gateway")
    await app.state.redis.close()
    await app.state.pg_pool.close()
    app.state.langfuse.flush()


# ==============================================================================
# FASTAPI APP
# ==============================================================================

app = FastAPI(
    title="AG-UI Gateway",
    description="FastAPI gateway для протокола AG-UI",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS (если нужен cross-origin для SPA)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://factory.corp.example.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==============================================================================
# MIDDLEWARE: извлечение X-Forwarded-User/Groups от nginx
# ==============================================================================

@app.middleware("http")
async def extract_user_headers(request: Request, call_next):
    """Извлекает X-Forwarded-User/Groups из заголовков nginx."""
    user = request.headers.get("X-Forwarded-User", "anonymous")
    groups = request.headers.get("X-Forwarded-Groups", "")

    # Сохраняем в контекст для логирования
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(user=user, groups=groups)

    response = await call_next(request)
    return response


# ==============================================================================
# ENDPOINTS
# ==============================================================================

@app.get("/health")
async def health():
    """Healthcheck endpoint (без аутентификации)."""
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
    """
    Приём RunAgentInput, постановка задачи в Redis через Taskiq, SSE-relay событий.

    Это основной endpoint AG-UI протокола:
      1. Клиент отправляет POST с RunAgentInput
      2. Gateway создаёт запись в реестре ранов (PostgreSQL) со статусом QUEUED
      3. Gateway ставит задачу в Redis через Taskiq broker
      4. Gateway открывает SSE-соединение и читает события из Redis Streams
      5. События стримятся клиенту в формате AG-UI
    """
    user = request.headers.get("X-Forwarded-User", "anonymous")

    logger.info(
        "RunAgentInput received",
        thread_id=input.thread_id,
        run_id=input.run_id,
        user=user,
    )

    # Langfuse trace
    trace = app.state.langfuse.trace(
        name="gateway.run_agent",
        metadata={
            "thread_id": input.thread_id,
            "run_id": input.run_id,
            "user": user,
        },
    )

    # 1. Создание записи в реестре ранов (PostgreSQL)
    try:
        async with app.state.pg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO runs (run_id, thread_id, status, user, payload, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id) DO NOTHING
                    """,
                    (
                        input.run_id,
                        input.thread_id,
                        "QUEUED",
                        user,
                        orjson.dumps({
                            "messages": input.messages,
                            "state": input.state,
                            "tools": input.tools,
                            "context": input.context,
                            "forwarded_props": input.forwarded_props,
                        }).decode("utf-8"),
                        datetime.utcnow(),
                        datetime.utcnow(),
                    ),
                )
        logger.info("Run created in registry", run_id=input.run_id, status="QUEUED")
    except Exception as e:
        logger.error("Failed to create run in registry", run_id=input.run_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to create run")

    # 2. Постановка задачи в Redis через Taskiq broker
    task_payload = {
        "run_id": input.run_id,
        "thread_id": input.thread_id,
        "user": user,
        "messages": input.messages,
        "state": input.state,
        "tools": input.tools,
        "context": input.context,
        "forwarded_props": input.forwarded_props,
    }

    try:
        # Отправка задачи через Taskiq broker
        await app.state.broker.send_task(
            task_name=TASK_RUN_AGENT,
            args=[task_payload],
            queue=TASK_QUEUE_KEY,
        )
        logger.info("Task enqueued via Taskiq", run_id=input.run_id)
    except Exception as e:
        logger.error("Failed to enqueue task", run_id=input.run_id, error=str(e))
        # Обновляем статус рана на FAILED
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

    # 3. SSE-relay: читаем события из Redis Streams и стримим клиенту
    async def event_generator() -> AsyncGenerator[str, None]:
        stream_key = _stream_key(input.thread_id)
        # Начинаем с последнего сообщения стрима, чтобы не читать историю прошлых ранов
        last_id = "$"

        try:
            while True:
                # XREAD BLOCK: ждём новые события
                result = await app.state.redis.xread(
                    {stream_key: last_id},
                    count=10,
                    block=5000,  # 5 секунд
                )

                if result:
                    for _, messages in result:
                        for msg_id, msg_data in messages:
                            last_id = msg_id

                            # Формируем SSE-событие
                            event_type = msg_data.get("type", "custom")
                            event_data = msg_data.get("data", "{}")

                            yield (
                                f"id: {msg_id}\n"
                                f"event: {event_type}\n"
                                f"data: {event_data}\n\n"
                            )

                            # Если это терминальное событие — закрываем стрим
                            if event_type in ("RUN_FINISHED", "RUN_ERROR"):
                                logger.info(
                                    "Run finished",
                                    run_id=input.run_id,
                                    event_type=event_type,
                                )
                                return

                # Heartbeat (чтобы nginx не закрывал соединение)
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
    """
    Реконнект SSE-соединения по thread_id с Last-Event-ID.
    Используется, когда клиент потерял соединение и хочет продолжить чтение событий.
    Заголовок `Last-Event-ID` — стандартный SSE-механизм реконнекта.
    """
    user = request.headers.get("X-Forwarded-User", "anonymous")

    # Читаем заголовок `Last-Event-ID` (стандартный механизм)
    # Если заголовка нет — используем query-параметр (для обратной совместимости)
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
            # Читаем пропущенные события (с последнего известного)
            result = await app.state.redis.xread({stream_key: start_id}, count=100)

            if result:
                for _, messages in result:
                    for msg_id, msg_data in messages:
                        event_type = msg_data.get("type", "custom")
                        event_data = msg_data.get("data", "{}")

                        yield (
                            f"id: {msg_id}\n"
                            f"event: {event_type}\n"
                            f"data: {event_data}\n\n"
                        )

            # Продолжаем live-tail
            last_id = start_id
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
async def resume_run(
        thread_id: str, resume_req: ResumeRequest, request: Request
):
    """
    Resume HITL-паузы с идемпотентностью.

    Воркер остановился на interrupt(), ждёт решения человека.
    Gateway:
      1. Проверяет идемпотентность по interrupt_id (нет ли уже обработанного)
      2. Обновляет статус рана на WAITING_HUMAN
      3. Ставит задачу resume в очередь через Taskiq
      4. Воркер продолжает выполнение
    """
    user = request.headers.get("X-Forwarded-User", "anonymous")

    logger.info(
        "Resume requested",
        thread_id=thread_id,
        interrupt_id=resume_req.interrupt_id,
        user=user,
    )

    # Langfuse trace
    trace = app.state.langfuse.trace(
        name="gateway.resume_run",
        metadata={
            "thread_id": thread_id,
            "interrupt_id": resume_req.interrupt_id,
            "user": user,
        },
    )

    # 1. Идемпотентность: проверяем, нет ли уже обработанного interrupt_id
    try:
        async with app.state.pg_pool.connection() as conn:
            async with conn.cursor() as cur:
                # Проверяем, есть ли ран с таким interrupt_id и статусом WAITING_HUMAN
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

                    # Если уже обработан (SUCCEEDED/FAILED/CANCELLED) — возвращаем 409
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

                    # Если уже WAITING_HUMAN — идемпотентно возвращаем 200
                    if existing_status == "WAITING_HUMAN":
                        logger.info(
                            "Resume idempotent: already waiting",
                            thread_id=thread_id,
                            interrupt_id=resume_req.interrupt_id,
                        )
                        return JSONResponse(
                            {"status": "already_waiting", "thread_id": thread_id}
                        )

                # 2. Обновляем статус рана на WAITING_HUMAN
                await cur.execute(
                    """
                    UPDATE runs
                    SET status = %s, interrupt_id = %s, updated_at = %s
                    WHERE thread_id = %s AND status IN ('RUNNING', 'WAITING_HUMAN')
                    """,
                    ("WAITING_HUMAN", resume_req.interrupt_id, datetime.utcnow(), thread_id),
                )

                if cur.rowcount == 0:
                    logger.error(
                        "Resume failed: no running run found",
                        thread_id=thread_id,
                    )
                    raise HTTPException(
                        status_code=404,
                        detail="No running run found for this thread",
                    )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to update run status", thread_id=thread_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to update run status")

    # 3. Постановка задачи resume в очередь через Taskiq
    resume_payload = {
        "type": "resume",
        "thread_id": thread_id,
        "interrupt_id": resume_req.interrupt_id,
        "value": resume_req.value,
        "user": user,
    }

    try:
        await app.state.broker.send_task(
            task_name=TASK_RESUME_RUN,
            args=[resume_payload],
            queue=TASK_QUEUE_KEY,
        )
        logger.info(
            "Resume task enqueued via Taskiq",
            thread_id=thread_id,
            interrupt_id=resume_req.interrupt_id,
        )
    except Exception as e:
        logger.error("Failed to enqueue resume task", thread_id=thread_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to enqueue resume task")

    return JSONResponse({"status": "accepted", "thread_id": thread_id})


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request):
    """
    Отмена рана через флаг в реестре.

    Кооперативная отмена:
      1. Gateway устанавливает статус CANCELLED в реестре ранов
      2. Воркер проверяет этот флаг перед каждым шагом
      3. Если флаг установлен — воркер завершает выполнение
    """
    user = request.headers.get("X-Forwarded-User", "anonymous")

    logger.info("Cancel requested", run_id=run_id, user=user)

    # Langfuse trace
    trace = app.state.langfuse.trace(
        name="gateway.cancel_run",
        metadata={
            "run_id": run_id,
            "user": user,
        },
    )

    # Установка флага отмены в реестре ранов
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
    """
    Получение статуса рана из реестра.
    """
    user = request.headers.get("X-Forwarded-User", "anonymous")

    try:
        async with app.state.pg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT run_id, thread_id, status, user, created_at, updated_at
                    FROM runs
                    WHERE run_id = %s
                    """,
                    (run_id,),
                )
                row = await cur.fetchone()

                if not row:
                    raise HTTPException(status_code=404, detail="Run not found")

                return RunStatus(
                    run_id=row[0],
                    thread_id=row[1],
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


# ==============================================================================
# ERROR HANDLERS
# ==============================================================================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception", error=str(exc), exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# ==============================================================================
# ENTRYPOINT
# ==============================================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        workers=4,
        log_level=settings.log_level.lower(),
    )