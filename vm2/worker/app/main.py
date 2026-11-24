"""
TaskIQ Worker Main — Инициализация брокера, жизненный цикл и регистрация задач.
Production Blueprint v5.2 — ВМ №2

Назначение:
  - Настройка хуков жизненного цикла брокера (startup/shutdown).
  - Инициализация LangGraph Checkpoint Saver (PostgreSQL).
  - Инициализация пулов соединений и внешних клиентов.
  - Импорт модулей с задачами для их регистрации в брокере.

"""
import structlog
from taskiq import TaskiqState

from worker.app.broker import broker
from worker.app.integrations import corp_pg_client, gpu_client, langfuse_client, s3_client
from worker.app.settings import get_settings

# Импортируем задачи и реестр графов, чтобы они зарегистрировались в брокере.
# document_processing.py при импорте вызывает graph_registry.register(...).
from worker.app import tasks  # noqa: F401
from worker.app.graphs import document_processing  # noqa: F401  (side-effect: регистрация графа)
from worker.app.graphs.registry import graph_registry

logger = structlog.get_logger()


@broker.on_event("startup")
async def startup(state: TaskiqState) -> None:
    """
    Инициализация ресурсов при старте воркера.
    Выполняется один раз при запуске процесса worker.
    """
    settings = get_settings()
    log = logger.bind(environment=settings.environment, worker_id=getattr(state, "worker_id", "unknown"))
    log.info("Worker startup initiated")

    # 1. Инициализация LangGraph Checkpoint Saver (PostgreSQL)
    # Blueprint §3.1: чекпоинты живут в PostgreSQL ВМ №2.
    try:
        await graph_registry.init_checkpointer()
        log.info(
            "LangGraph checkpointer initialized",
            registered_graphs=graph_registry.list_graphs(),
        )
    except Exception as e:
        log.error("Failed to initialize checkpointer", error=str(e), exc_info=True)
        raise

    # 2. Ленивая инициализация внешних клиентов (первый вызов создаст подключения)
    # gpu_client, s3_client, corp_pg_client — создадут подключения при первом вызове.
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
        # 1. Закрытие LangGraph Checkpoint Saver (PostgreSQL pool)
        await graph_registry.close_checkpointer()

        # 2. Закрытие HTTP-клиентов и пулов соединений
        await gpu_client.close()
        await corp_pg_client.close()
        # Flush Langfuse telemetry and close its shared mTLS HTTP client.
        langfuse_client.flush()
        await langfuse_client.close()
        # s3_client — boto3, не требует явного закрытия

        log.info("Worker shutdown completed successfully")
    except Exception as e:
        log.error("Error during worker shutdown", error=str(e), exc_info=True)