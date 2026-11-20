"""
Corporate PostgreSQL Client — mTLS-клиент для корпоративного PG (ВМ №4).
Production Blueprint v5.2

Назначение:
  - Read-only доступ к мастер-данным: реестры, справочники, договоры.
  - mTLS-аутентификация через клиентские сертификаты.
  - Connection pooling для производительности.

Использует psycopg 3.x (async режим) — уже есть в зависимостях.
"""

from typing import Any, Dict, List, Optional, Tuple

import psycopg
import psycopg.rows
import structlog
from psycopg_pool import AsyncConnectionPool

from worker.app.settings import Settings, get_settings

logger = structlog.get_logger()


class CorpPGClient:
    """
    Асинхронный клиент для корпоративного PostgreSQL с mTLS.

    Read-only доступ к мастер-данным на ВМ №4.
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._pool: Optional[AsyncConnectionPool] = None

    async def _get_pool(self) -> AsyncConnectionPool:
        """Ленивая инициализация connection pool."""
        if self._pool is None:
            # URL уже содержит параметры mTLS (sslmode, sslrootcert, sslcert, sslkey)
            # из settings.corp_pg_url
            self._pool = AsyncConnectionPool(
                conninfo=self.settings.corp_pg_url,
                min_size=2,
                max_size=10,
                timeout=30,
                # Read-only режим на уровне сессии
                kwargs={
                    "options": "-c default_transaction_read_only=on"
                },
            )
            await self._pool.open()
            logger.info("Corp PG pool initialized", url=self.settings.corp_pg_url)
        return self._pool

    async def close(self) -> None:
        """Закрытие pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None

    # ==============================================================================
    # QUERY EXECUTION
    # ==============================================================================

    async def fetch_all(
            self,
            query: str,
            params: Optional[Tuple[Any, ...]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Выполнение SELECT-запроса и возврат всех строк.

        Args:
            query: SQL-запрос (используйте %s для параметров).
            params: Кортеж параметров.

        Returns:
            Список словарей (имя_колонки: значение).
        """
        pool = await self._get_pool()
        logger.info("Executing query", query=query[:100], params_count=len(params or ()))

        async with pool.connection() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(query, params)
                return await cur.fetchall()

    async def fetch_one(
            self,
            query: str,
            params: Optional[Tuple[Any, ...]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Выполнение SELECT-запроса и возврат одной строки.

        Returns:
            Словарь или None, если ничего не найдено.
        """
        pool = await self._get_pool()
        logger.info("Executing query (fetch_one)", query=query[:100])

        async with pool.connection() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(query, params)
                return await cur.fetchone()

    async def fetch_value(
            self,
            query: str,
            params: Optional[Tuple[Any, ...]] = None,
    ) -> Optional[Any]:
        """
        Выполнение SELECT-запроса и возврат одного значения (первая колонка первой строки).
        """
        pool = await self._get_pool()
        logger.info("Executing query (fetch_value)", query=query[:100])

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                row = await cur.fetchone()
                return row[0] if row else None

    # ==============================================================================
    # CONVENIENCE METHODS
    # ==============================================================================

    async def get_contract(self, contract_id: str) -> Optional[Dict[str, Any]]:
        """Получение договора по ID."""
        query = """
            SELECT id, contract_number, contractor_name, signed_at, status
            FROM contracts
            WHERE id = %s
        """
        return await self.fetch_one(query, (contract_id,))

    async def get_rates_for_contract(
            self, contract_id: str, as_of_date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Получение актуальных ставок для договора на указанную дату.

        Args:
            contract_id: ID договора.
            as_of_date: Дата в формате YYYY-MM-DD (по умолчанию — сегодня).
        """
        query = """
            SELECT id, item_name, unit, price, valid_from, valid_to
            FROM rates
            WHERE contract_id = %s
              AND valid_from <= %s::date
              AND (valid_to IS NULL OR valid_to >= %s::date)
            ORDER BY item_name
        """
        from datetime import date
        as_of = as_of_date or date.today().isoformat()
        return await self.fetch_all(query, (contract_id, as_of, as_of))

    async def get_contractors(self, search: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Поиск подрядчиков по наименованию (нечёткий поиск через pg_trgm).
        """
        if search:
            query = """
                SELECT id, name, inn, kpp
                FROM contractors
                WHERE name ILIKE %s
                ORDER BY similarity(name, %s) DESC
                LIMIT 50
            """
            pattern = f"%{search}%"
            return await self.fetch_all(query, (pattern, search))
        else:
            query = """
                SELECT id, name, inn, kpp
                FROM contractors
                ORDER BY name
                LIMIT 100
            """
            return await self.fetch_all(query)


# Глобальный экземпляр клиента
corp_pg_client = CorpPGClient()