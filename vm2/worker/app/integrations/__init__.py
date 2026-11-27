"""
Integrations package — клиенты внешних систем (ВМ №2).

Реэкспорт готовых экземпляров: `from worker.app.integrations import s3_client`
должен возвращать экземпляр клиента, а не модуль (D1).
Экземпляры создаются в соответствующих модулях (module = Class()).
"""

from worker.app.integrations.corp_pg_client import corp_pg_client
from worker.app.integrations.gpu_client import gpu_client
from worker.app.integrations.langfuse_client import langfuse_client
from worker.app.integrations.s3_client import s3_client

__all__ = [
    "corp_pg_client",
    "gpu_client",
    "langfuse_client",
    "s3_client",
]