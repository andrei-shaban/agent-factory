"""
Langfuse Client — Клиент для трейсинга через Langfuse.
Production Blueprint v5.1 — ВМ №2

Назначение:
  - Инициализация Langfuse SDK для трейсинга графов LangGraph.
  - Создание trace для каждого run_id.
  - Интеграция с LangGraph через callbacks (опционально).
  - Отправка трейсов в Langfuse web (ВМ №1).

Использует langfuse SDK (уже есть в зависимостях gateway/requirements.txt).
"""

import os
from typing import Any, Dict, Optional

import structlog
from langfuse import Langfuse

from worker.app.settings import Settings, get_settings

logger = structlog.get_logger()


class LangfuseClient:
    """
    Клиент для Langfuse observability.

    Создаёт traces, spans и события для каждого запуска графа.
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._client: Optional[Langfuse] = None

    def _get_client(self) -> Langfuse:
        """Ленивая инициализация Langfuse клиента."""
        if self._client is None:
            self._client = Langfuse(
                public_key=self.settings.langfuse_public_key,
                secret_key=self.settings.langfuse_secret_key,
                host=self.settings.langfuse_host,
                debug=False,
            )
            logger.info("Langfuse client initialized", host=self.settings.langfuse_host)
        return self._client

    def create_trace(
            self,
            run_id: str,
            thread_id: str,
            graph_name: str,
            user_id: str,
            metadata: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Создание trace для запуска графа.

        Args:
            run_id: Идентификатор запуска (trace_id в Langfuse).
            thread_id: Идентификатор треда (session_id в Langfuse).
            graph_name: Имя графа (name в Langfuse).
            user_id: Идентификатор пользователя.
            metadata: Дополнительные метаданные.

        Returns:
            Объект Langfuse trace.
        """
        client = self._get_client()

        trace = client.trace(
            id=run_id,
            name=graph_name,
            session_id=thread_id,
            user_id=user_id,
            metadata=metadata or {},
        )

        logger.debug("Trace created", run_id=run_id, graph_name=graph_name)
        return trace

    def create_span(
            self,
            trace: Any,
            name: str,
            input_data: Optional[Dict[str, Any]] = None,
            metadata: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Создание span внутри trace (для узла графа).

        Args:
            trace: Родительский trace объект.
            name: Имя span (например, имя узла графа).
            input_data: Входные данные узла.
            metadata: Дополнительные метаданные.

        Returns:
            Объект Langfuse span.
        """
        span = trace.span(
            name=name,
            input=input_data or {},
            metadata=metadata or {},
        )

        logger.debug("Span created", trace_id=trace.id, span_name=name)
        return span

    def end_span(
            self,
            span: Any,
            output_data: Optional[Dict[str, Any]] = None,
            status: str = "success",
    ) -> None:
        """
        Завершение span.

        Args:
            span: Объект span.
            output_data: Выходные данные узла.
            status: Статус выполнения ("success" или "error").
        """
        span.end(
            output=output_data or {},
            status=status,
        )

        logger.debug("Span ended", span_name=span.name, status=status)

    def create_event(
            self,
            trace: Any,
            name: str,
            input_data: Optional[Dict[str, Any]] = None,
            output_data: Optional[Dict[str, Any]] = None,
            metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Создание события в trace (для дискретных действий).

        Args:
            trace: Родительский trace объект.
            name: Имя события.
            input_data: Входные данные.
            output_data: Выходные данные.
            metadata: Дополнительные метаданные.
        """
        trace.event(
            name=name,
            input=input_data or {},
            output=output_data or {},
            metadata=metadata or {},
        )

        logger.debug("Event created", trace_id=trace.id, event_name=name)

    def flush(self) -> None:
        """Принудительная отправка всех трейсов в Langfuse."""
        if self._client:
            self._client.flush()
            logger.debug("Langfuse traces flushed")

    def shutdown(self) -> None:
        """Закрытие клиента и отправка оставшихся трейсов."""
        if self._client:
            self._client.flush()
            self._client = None
            logger.info("Langfuse client shut down")


# Глобальный экземпляр клиента
langfuse_client = LangfuseClient()