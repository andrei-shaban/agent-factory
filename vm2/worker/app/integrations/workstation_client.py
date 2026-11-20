"""
Workstation Client — mTLS-клиент для корпоративной рабочей станции (Этап 2).
Production Blueprint v5.2 — ВМ №2

Назначение:
  - Выполнение бизнес-операций в корпоративных системах (регистрация актов,
    отправка данных в учётные системы, синхронизация статусов).
  - mTLS-аутентификация через клиентские сертификаты.
  - Устойчивость к сбоям: автоматические retry с экспоненциальной задержкой.

Использует httpx (async) и tenacity (retry logic).
"""

import json
from typing import Any, Dict, Optional

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from worker.app.settings import Settings, get_settings

logger = structlog.get_logger()


class WorkstationError(Exception):
    """Базовое исключение для ошибок рабочей станции."""

    def __init__(self, message: str, status_code: Optional[int] = None, response_body: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class WorkstationClient:
    """
    Асинхронный mTLS-клиент для корпоративной рабочей станции.
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        """Ленивая инициализация httpx клиента с mTLS."""
        if self._client is None:
            cert = (
                self.settings.mtls_client_cert_path,
                self.settings.mtls_client_key_path,
            )
            self._client = httpx.AsyncClient(
                base_url=self.settings.workstation_url,
                cert=cert,
                verify=self.settings.mtls_ca_cert_path,
                timeout=httpx.Timeout(self.settings.workstation_timeout),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "X-Factory-Agent": "true",
                },
            )
            logger.info("Workstation client initialized", url=self.settings.workstation_url)
        return self._client

    async def close(self) -> None:
        """Закрытие клиента."""
        if self._client:
            await self._client.aclose()
            self._client = None

    # ==============================================================================
    # CORE REQUEST WITH RETRY
    # ==============================================================================

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
        reraise=True,
    )
    async def _request(
            self,
            method: str,
            endpoint: str,
            payload: Optional[Dict[str, Any]] = None,
            params: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Базовый метод запроса с автоматическими retry.

        Args:
            method: HTTP метод (GET, POST, PUT).
            endpoint: Путь эндпоинта относительно base_url.
            payload: Тело запроса (для POST/PUT).
            params: Query-параметры.
        """
        client = self._get_client()
        log = logger.bind(endpoint=endpoint, method=method)

        try:
            log.info("Sending request to workstation")
            resp = await client.request(
                method=method,
                url=endpoint,
                json=payload,
                params=params,
            )
            resp.raise_for_status()

            # Пустой ответ (204 No Content)
            if resp.status_code == 204:
                return {}

            return resp.json()

        except httpx.HTTPStatusError as e:
            log.error(
                "Workstation returned error status",
                status=e.response.status_code,
                body=e.response.text[:500]
            )
            raise WorkstationError(
                message=f"Workstation error: {e.response.status_code}",
                status_code=e.response.status_code,
                response_body=e.response.text,
            )
        except httpx.TransportError as e:
            log.warning("Transport error, will retry", error=str(e))
            raise

    # ==============================================================================
    # DOMAIN-SPECIFIC METHODS (ЭТАП 2)
    # ==============================================================================

    async def register_document(self, document_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Регистрация документа в учётной системе.

        Args:
            document_data: Структура документа (номер, дата, суммы, контрагент).

        Returns:
            Ответ системы с присвоенным внутренним ID и статусом.
        """
        logger.info("Registering document in workstation", doc_type=document_data.get("type"))
        return await self._request("POST", "/api/v1/documents/register", payload=document_data)

    async def post_act(self, act_data: Dict[str, Any], artifact_url: str) -> Dict[str, Any]:
        """
        Отправка акта выполненных работ с ссылкой на артефакт в S3.

        Args:
            act_data: Метаданные акта.
            artifact_url: URL сгенерированного Excel-шаблона в SeaweedFS.
        """
        payload = {**act_data, "artifact_url": artifact_url}
        logger.info("Posting act to workstation", contract_id=act_data.get("contract_id"))
        return await self._request("POST", "/api/v1/acts/post", payload=payload)

    async def get_contract_status(self, contract_id: str) -> Dict[str, Any]:
        """Получение текущего статуса договора из учётной системы."""
        logger.info("Fetching contract status", contract_id=contract_id)
        return await self._request("GET", f"/api/v1/contracts/{contract_id}/status")


# Глобальный экземпляр клиента
workstation_client = WorkstationClient()