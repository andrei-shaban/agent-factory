"""
GPU Client — mTLS-клиент для запросов к GPU-узлу.
Production Blueprint v5.3.1 — ВМ №2

Назначение:
  LLM-запросы (/v1/chat/completions) с поддержкой стриминга.
  Embeddings (/v1/embeddings) для RAG.
  OCR (/ocr/) для распознавания документов.
  Автообнаружение моделей через /v1/models.
  Retry логика и обработка таймаутов.
"""

import base64
import json
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
import structlog

from worker.app.settings import Settings, get_settings

logger = structlog.get_logger()

# Blueprint §3.3: лимит тела запроса OCR — 20 МБ с учётом base64.
OCR_MAX_BASE64_BYTES = 20 * 1024 * 1024


class GPUClient:
    """
    Асинхронный mTLS-клиент для GPU-узла.
    Использует httpx с клиентскими сертификатами для mTLS-аутентификации.
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Ленивая инициализация httpx клиента с mTLS."""
        if self._client is None:
            cert = (
                self.settings.mtls_client_cert_path,
                self.settings.mtls_client_key_path,
            )
            self._client = httpx.AsyncClient(
                base_url=self.settings.gpu_endpoint_url,
                cert=cert,
                verify=self.settings.mtls_ca_cert_path,
                timeout=httpx.Timeout(
                    connect=10.0,
                    read=self.settings.gpu_llm_timeout,
                    write=30.0,
                    pool=10.0,
                ),
            )
            logger.info("GPU client initialized", endpoint=self.settings.gpu_endpoint_url)
        return self._client

    async def close(self) -> None:
        """Закрытие клиента."""
        if self._client:
            await self._client.aclose()
            self._client = None

    # ==========================================================================
    # LLM CHAT COMPLETIONS
    # ==========================================================================

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        stream: bool = False,
    ) -> Dict[str, Any]:
        """
        Запрос к LLM (без стриминга).

        Args:
            messages: Список сообщений [{"role": "user", "content": "..."}].
            model: Имя модели (если None, автообнаружение).
            max_tokens: Максимальное количество токенов в ответе.
            temperature: Температура генерации.
            stream: Если True — используйте chat_completion_stream().

        Returns:
            Ответ в формате OpenAI API.
        """
        if stream:
            raise ValueError("Use chat_completion_stream() for streaming")

        client = await self._get_client()
        model = model or await self._get_default_model()

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }

        logger.info("LLM request", model=model, messages_count=len(messages))
        try:
            resp = await client.post("/v1/chat/completions", json=payload)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            logger.error("LLM request failed", status=e.response.status_code, error=e.response.text)
            raise
        except Exception as e:
            logger.error("LLM request error", error=str(e))
            raise

    async def chat_completion_stream(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Стриминг LLM-ответа (SSE).

        Yields:
            Чанки ответа в формате OpenAI API.
        """
        client = await self._get_client()
        model = model or await self._get_default_model()

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }

        logger.info("LLM streaming request", model=model, messages_count=len(messages))
        async with client.stream("POST", "/v1/chat/completions", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        yield json.loads(data)
                    except json.JSONDecodeError as e:
                        logger.warning("Failed to parse SSE chunk", error=str(e))

    # ==========================================================================
    # EMBEDDINGS
    # ==========================================================================

    async def embeddings(
        self,
        texts: List[str],
        model: Optional[str] = None,
    ) -> List[List[float]]:
        """
        Получение эмбеддингов для текстов.

        Args:
            texts: Список текстов для эмбеддинга.
            model: Имя модели эмбеддингов (если None, автообнаружение).

        Returns:
            Список векторов эмбеддингов.
        """
        client = await self._get_client()
        model = model or "embedding-model"  # TODO: автообнаружение (этап 11, J2)

        payload = {"model": model, "input": texts}

        logger.info("Embeddings request", model=model, texts_count=len(texts))
        try:
            resp = await client.post("/v1/embeddings", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return [item["embedding"] for item in data["data"]]
        except Exception as e:
            logger.error("Embeddings request error", error=str(e))
            raise

    # ==========================================================================
    # OCR (PaddleOCR-VL)
    # ==========================================================================

    @staticmethod
    def _normalize_ocr(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        D4: приведение ответа PaddleOCR-VL к контракту, который потребляет граф.

        Blueprint §3.3: ответ содержит result.layoutParsingResults[] (по страницам).
        Граф работает с fullText / tables / pages, поэтому нормализуем здесь,
        сохраняя исходный массив для аудита и дальнейшей обработки.
        """
        result = data.get("result") or {}
        pages = result.get("layoutParsingResults") or []

        texts: List[str] = []
        tables: List[Dict[str, Any]] = []

        for page in pages:
            markdown = page.get("markdown") or {}
            text = markdown.get("text") or page.get("text") or ""
            if text:
                texts.append(text)

            page_tables = page.get("tables") or markdown.get("tables") or []
            for table in page_tables:
                if isinstance(table, dict):
                    tables.append(table)
                else:
                    tables.append({"content": table})

        return {
            "fullText": "\n\n".join(texts),
            "tables": tables,
            "pages": len(pages),
            "layoutParsingResults": pages,
        }

    async def ocr(
        self,
        file_content: bytes,
        file_type: str = "pdf",
        visualize: bool = False,
        return_markdown_images: bool = False,
    ) -> Dict[str, Any]:
        """
        Распознавание документа через PaddleOCR-VL.

        Args:
            file_content: Содержимое файла (PDF или изображение).
            file_type: Тип файла ("pdf" или "image").
            visualize: Если True, возвращает визуализацию.
            return_markdown_images: Если True, возвращает изображения из markdown.

        Returns:
            Нормализованный результат: fullText, tables, pages, layoutParsingResults.

        Raises:
            ValueError: если base64-тело превышает лимит 20 МБ (Blueprint §3.3).
        """
        client = await self._get_client()

        file_base64 = base64.b64encode(file_content).decode("ascii")
        if len(file_base64) > OCR_MAX_BASE64_BYTES:
            raise ValueError(
                f"OCR payload exceeds 20MB base64 limit "
                f"({len(file_base64)} bytes); document must be split into batches"
            )

        payload = {
            "file": file_base64,
            "fileType": 0 if file_type == "pdf" else 1,
            "visualize": visualize,
            "returnMarkdownImages": return_markdown_images,
        }

        logger.info("OCR request", file_type=file_type, file_size=len(file_content))
        try:
            resp = await client.post(
                "/ocr/",
                json=payload,
                timeout=httpx.Timeout(self.settings.gpu_ocr_timeout),
            )
            resp.raise_for_status()
            normalized = self._normalize_ocr(resp.json())
            logger.info("OCR completed", pages=normalized["pages"], tables=len(normalized["tables"]))
            return normalized
        except Exception as e:
            logger.error("OCR request error", error=str(e))
            raise

    # ==========================================================================
    # MODEL DISCOVERY
    # ==========================================================================

    async def _get_default_model(self) -> str:
        """
        Автообнаружение модели LLM через /v1/models.

        Returns:
            Имя первой доступной модели.
        """
        if self.settings.gpu_llm_model_name:
            return self.settings.gpu_llm_model_name

        client = await self._get_client()
        try:
            resp = await client.get("/v1/models")
            resp.raise_for_status()
            models = resp.json().get("data", [])
            if models:
                return models[0]["id"]
            raise ValueError("No models available")
        except Exception as e:
            logger.error("Model discovery failed", error=str(e))
            raise


# Глобальный экземпляр клиента
gpu_client = GPUClient()