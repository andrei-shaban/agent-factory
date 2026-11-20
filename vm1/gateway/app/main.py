"""
AG-UI Gateway — FastAPI application.

Production Blueprint v5.1 — ВМ №1

Обязанности:
  - Приём RunAgentInput от AG-UI клиентов (браузеров)
  - Постановка задач в Redis фабрики (ВМ №2)
  - SSE-relay событий от воркеров к браузерам (XREAD BLOCK из Redis Streams)
  - Реконнект по thread_id с Last-Event-ID
  - Resume HITL-пауз
  - Отмена ранов
  - Healthcheck

ВАЖНО: Gateway НЕ исполняет граф — это задача Worker на ВМ №2.
"""

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import redis.asyncio as redis
import structlog
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

# ==============================================================================
# LOGGING
# ==============================================================================
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
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
    redis_factory_url: str = "rediss://agui_svc:password@vm2-redis-factory:6379"
    redis_factory_tls: bool = True

    # PostgreSQL runtime (ВМ №2) — для реестра ранов
    postgres_runtime_url: str = "postgresql+asyncpg://user:pass@vm2-postgres-runtime:5432/factory"

    # Langfuse
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://langfuse-web:3000"

    # SSE
    sse_heartbeat_interval: int = 15  # секунды
    sse_reconnect_timeout: int = 300  # секунд

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()

# ==============================================================================
# MODELS
# ==============================================================================
class RunAgentInput(BaseModel):
    """Входной контракт AG-UI протокола."""

    thread_id: str = Field(..., description="Идентификатор треда (диалога)")
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Идентификатор рана")
    messages: list[dict] = Field(default_factory=list, description="История сообщений")
    state: dict = Field(default_factory=dict, description="Состояние графа")
    tools: list[dict] = Field(default_factory=list, description="Доступные инструменты")
    context: dict = Field(default_factory=dict, description="Контекст выполнения")
    forwarded_props: dict = Field(default_factory=dict, description="Проброшенные свойства")


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
# LIFESPAN
# ==============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup / shutdown."""
    logger.info("Starting AG-UI Gateway", environment=settings.environment)

    # Redis connection pool
    app.state.redis = redis.from_url(
        settings.redis_factory_url,
        decode_responses=True,
        ssl=settings.redis_factory_tls,
        ssl_cert_reqs="required",
    )

    # Проверка подключения к Redis
    try:
        await app.state.redis.ping()
        logger.info("Redis connection established")
    except Exception as e:
        logger.error("Failed to connect to Redis", error=str(e))
        raise

    # TODO: PostgreSQL connection pool (для реестра ранов)
    # app.state.pg_pool = await create_async_pool(settings.postgres_runtime_url)

    # TODO: Langfuse client
    # from langfuse import Langfuse
    # app.state.langfuse = Langfuse(
    #     public_key=settings.langfuse_public_key,
    #     secret_key=settings.langfuse_secret_key,
    #     host=settings.langfuse_host,
    # )

    yield

    # Shutdown
    logger.info("Shutting down AG-UI Gateway")
    await app.state.redis.close()
    # await app.state.pg_pool.close()
    # app.state.langfuse.flush()


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
        return {"status": "healthy", "redis": "ok"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Redis unhealthy: {str(e)}")


@app.post("/api/agent")
async def run_agent(input: RunAgentInput, request: Request):
    """
    Приём RunAgentInput, постановка задачи в Redis, SSE-relay событий.

    Это основной endpoint AG-UI протокола:
      1. Клиент отправляет POST с RunAgentInput
      2. Gateway ставит задачу в Redis (очередь Worker'ов)
      3. Gateway открывает SSE-соединение и читает события из Redis Streams
      4. События стримятся клиенту в формате AG-UI
    """
    user = request.headers.get("X-Forwarded-User", "anonymous")
    logger.info("RunAgentInput received", thread_id=input.thread_id, run_id=input.run_id, user=user)

    # 1. Постановка задачи в Redis (очередь Worker'ов)
    # TODO: использовать Taskiq broker
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

    # Временная реализация: прямой LPUSH в очередь
    # В проде — Taskiq broker.send_task(...)
    await app.state.redis.lpush("taskiq:factory:default", str(task_payload))
    logger.info("Task enqueued", run_id=input.run_id)

    # 2. SSE-relay: читаем события из Redis Streams и стримим клиенту
    async def event_generator() -> AsyncGenerator[str, None]:
        stream_key = f"stream:{input.thread_id}"
        last_id = "0-0"  # начинаем с начала

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
                            yield f"id: {msg_id}\nevent: {event_type}\ndata: {event_data}\n\n"

                            # Если это терминальное событие — закрываем стрим
                            if event_type in ("RUN_FINISHED", "RUN_ERROR"):
                                logger.info("Run finished", run_id=input.run_id, event_type=event_type)
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
async def reconnect_events(thread_id: str, request: Request, last_event_id: str = None):
    """
    Реконнект SSE-соединения по thread_id с Last-Event-ID.

    Используется, когда клиент потерял соединение и хочет продолжить чтение событий.
    """
    user = request.headers.get("X-Forwarded-User", "anonymous")
    logger.info("Reconnect requested", thread_id=thread_id, last_event_id=last_event_id, user=user)

    stream_key = f"stream:{thread_id}"
    start_id = last_event_id if last_event_id else "0-0"

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            # Читаем пропущенные события
            result = await app.state.redis.xread({stream_key: start_id}, count=100)

            if result:
                for _, messages in result:
                    for msg_id, msg_data in messages:
                        event_type = msg_data.get("type", "custom")
                        event_data = msg_data.get("data", "{}")
                        yield f"id: {msg_id}\nevent: {event_type}\ndata: {event_data}\n\n"

            # Продолжаем live-tail
            last_id = start_id
            while True:
                result = await app.state.redis.xread({stream_key: last_id}, count=10, block=5000)

                if result:
                    for _, messages in result:
                        for msg_id, msg_data in messages:
                            last_id = msg_id
                            event_type = msg_data.get("type", "custom")
                            event_data = msg_data.get("data", "{}")
                            yield f"id: {msg_id}\nevent: {event_type}\ndata: {event_data}\n\n"

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
    """
    Resume HITL-паузы.

    Воркер остановился на interrupt(), ждёт решения человека.
    Gateway ставит задачу resume в очередь, воркер продолжает выполнение.
    """
    user = request.headers.get("X-Forwarded-User", "anonymous")
    logger.info(
        "Resume requested",
        thread_id=thread_id,
        interrupt_id=resume_req.interrupt_id,
        user=user,
    )

    # TODO: идемпотентность по interrupt_id (проверить в реестре ранов)

    # Постановка задачи resume в очередь
    resume_payload = {
        "type": "resume",
        "thread_id": thread_id,
        "interrupt_id": resume_req.interrupt_id,
        "value": resume_req.value,
        "user": user,
    }

    await app.state.redis.lpush("taskiq:factory:default", str(resume_payload))
    logger.info("Resume task enqueued", thread_id=thread_id, interrupt_id=resume_req.interrupt_id)

    return JSONResponse({"status": "accepted", "thread_id": thread_id})


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request):
    """
    Отмена рана.

    Кооперативная отмена: воркер проверяет флаг отмены и завершает выполнение.
    """
    user = request.headers.get("X-Forwarded-User", "anonymous")
    logger.info("Cancel requested", run_id=run_id, user=user)

    # TODO: установить флаг отмены в реестре ранов (PostgreSQL)
    # TODO: воркер проверяет флаг и завершает выполнение

    return JSONResponse({"status": "accepted", "run_id": run_id})


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