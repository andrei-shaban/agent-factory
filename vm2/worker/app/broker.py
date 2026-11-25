"""
TaskIQ Broker initialization.
Production Blueprint v5.2 — ВМ №2

Инициализирует брокер сообщений для асинхронного выполнения задач.
Использует Redis (ВМ №2) в качестве бэкенда очереди.
Брокер импортируется CLI taskiq при запуске воркера.
"""

from taskiq_redis import ListQueueBroker

from worker.app.settings import get_settings

# Получаем кэшированный экземпляр настроек
settings = get_settings()

# Инициализация брокера
# ListQueueBroker использует Redis Lists (LPUSH/BRPOP) для надежной доставки задач.
broker = ListQueueBroker(
    url=settings.redis_factory_url,
    queue_name="agui:tasks",
    # serializer по умолчанию использует JSON (orjson, если установлен)
)

# Опционально: настройка middleware или зависимостей, если потребуется
# broker.add_middleware(...)