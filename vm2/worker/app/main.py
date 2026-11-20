"""
TaskIQ Worker Main — Инициализация брокера, жизненный цикл и регистрация задач.
Production Blueprint v5.1 — ВМ №2

Назначение:
  - Настройка хуков жизненного цикла брокера (startup/shutdown).
  - Инициализация пулов соединений и внешних клиентов.
  - Импорт модулей с задачами для их регистрации в брокере.

Важно: В docker-compose.yml entrypoint должен указывать на этот модуль:
  taskiq worker app.main:broker --fs-discover --tasks-module app.tasks
(или просто app.main:broker, так как задачи импортируются здесь).
"""

import structlog
from taskiq import TaskiqState

from worker.app.broker import broker
from worker.app.integrations import corp_pg_client, gpu_client, s3_client
from worker.app.settings import get_settings

# Импортируем задачи и реестр графов, чтобы они зарегистрировались в брокере
from worker.app import tasks  # noqa: F401
from worker.app.graphs import registry  # noqa: F401

logger = structlog.get_logger()


@broker.on_event("startup")
async def startup(state: TaskiqState) -> None:
    """
    Инициализация ресурсов при старте воркера.
    Выполняется один раз при запуске процесса worker.
    """
    settings = get_settings()
    log = logger.bind(environment=settings.environment, worker_id=state.worker_id)
    log.info("Worker startup initiated")

    # 1. Инициализация внешних клиентов (ленивая, но можно форсировать)
    # gpu_client, s3_client, corp_pg_client создадут подключения при первом вызове.
    # Если нужна явная проверка доступности — раскомментируйте тестовые запросы.

    # 2. Инициализация LangGraph Checkpoint Saver (PostgreSQL)
    # TODO: await registry.init_checkpointer(settings.postgres_runtime_url)

    # 3. Инициализация Langfuse (опционально, если SDK требует явного старта)
    # from langfuse import Langfuse
    # state.langfuse = Langfuse(
    #     public_key=settings.langfuse_public_key,
    #     secret_key=settings.langfuse_secret_key,
    #     host=settings.langfuse_host,
    # )

    log.info("Worker startup completed successfully")


@broker.on_event("shutdown")
async def shutdown(state: TaskiqState) -> None:
    """
    Очистка ресурсов при graceful shutdown воркера.
    Выполняется при получении SIGTERM/SIGINT.
    """
    log = logger.bind(worker_id=getattr(state, "worker_id", "unknown"))
    log.info("Worker shutdown initiated")

    try:
        # Закрытие HTTP-клиентов и пулов соединений
        await gpu_client.close()
        await corp_pg_client.close()

        # TODO: Закрытие LangGraph Checkpoint Saver
        # await registry.close_checkpointer()

        # TODO: Flush Langfuse traces
        # if hasattr(state, "langfuse") and state.langfuse:
        #     state.langfuse.flush()

        log.info("Worker shutdown completed successfully")
    except Exception as e:
        log.error("Error during worker shutdown", error=str(e))