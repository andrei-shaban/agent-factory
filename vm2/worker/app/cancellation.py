# vm2/worker/app/cancellation.py
"""
Кооперативная отмена ранов. Вынесено из tasks.py, чтобы графы не
импортировали tasks (устранение циклической зависимости).
"""
from typing import Optional

import psycopg
import structlog

from worker.app.settings import get_settings

logger = structlog.get_logger()


class RunCancelledError(Exception):
    """Ран отменён оператором; бросается перед каждым шагом графа."""


def _normalize_pg_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql://", 1)
    if url.startswith("postgresql+ssl://"):
        return url.replace("postgresql+ssl://", "postgresql://", 1)
    return url


async def _check_cancellation(run_id: str) -> None:
    settings = get_settings()
    url = _normalize_pg_url(settings.postgres_runtime_url)
    try:
        async with await psycopg.AsyncConnection.connect(url) as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT status FROM runs WHERE run_id = %s", (run_id,))
                row = await cur.fetchone()
                if row and row[0] == "CANCELLED":
                    logger.info("Run cancelled by operator", run_id=run_id)
                    raise RunCancelledError(f"Run {run_id} was cancelled")
    except RunCancelledError:
        raise
    except Exception as e:
        logger.warning("Cancellation check failed, continuing", run_id=run_id, error=str(e))