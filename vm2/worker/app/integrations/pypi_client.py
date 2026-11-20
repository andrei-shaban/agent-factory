"""
PyPI Client — Клиент для управления wheel-cache через внутреннее зеркало (ВМ №1).
Production Blueprint v5.2 — ВМ №2

Назначение:
  - Скачивание Python-пакетов (.whl) из внутреннего PyPI-зеркала (air-gap).
  - Сохранение пакетов в локальный wheel-cache (/app/wheel-cache).
  - Проверка целостности файлов через SHA256.
  - Парсинг PEP 503 Simple Repository API для поиска нужной версии и платформы.

Используется Worker'ом при подготовке toolset-образов или динамическом
обновлении кэша для песочниц.
"""

import hashlib
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx
import structlog

from worker.app.settings import Settings, get_settings

logger = structlog.get_logger()

# Regex для парсинга PEP 503 Simple API (строго определённый формат, bs4 не нужен)
_SIMPLE_API_LINK_RE = re.compile(r'<a\s+[^>]*href="([^"]+)"[^>]*>([^<]+)</a>', re.IGNORECASE)


class PyPIError(Exception):
    """Исключение для ошибок взаимодействия с PyPI-зеркалом."""
    pass


class PyPIClient:
    """
    Асинхронный клиент для внутреннего PyPI-зеркала.
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        """Ленивая инициализация HTTP клиента."""
        if self._client is None:
            # Внутреннее зеркало обычно доступно по HTTP внутри Docker-сети
            self._client = httpx.AsyncClient(
                base_url=self.settings.pypi_mirror_url,
                timeout=httpx.Timeout(30.0, connect=5.0),
                follow_redirects=True,
            )
            logger.info("PyPI client initialized", mirror=self.settings.pypi_mirror_url)
        return self._client

    async def close(self) -> None:
        """Закрытие клиента."""
        if self._client:
            await self._client.aclose()
            self._client = None

    # ==============================================================================
    # ПОИСК И РАЗРЕШЕНИЕ ЗАВИСИМОСТЕЙ
    # ==============================================================================

    async def get_package_urls(self, package_name: str, version: Optional[str] = None) -> List[Dict[str, str]]:
        """
        Получение списка URL для скачивания пакета через PEP 503 Simple API.

        Args:
            package_name: Имя пакета (например, 'pandas').
            version: Требуемая версия (например, '2.1.4'). Если None, вернёт все.

        Returns:
            Список словарей {"url": "...", "filename": "...", "requires-python": "..."}
        """
        client = self._get_client()
        # PEP 503: имена пакетов нормализуются (lowercase, дефисы/точки/подчёркивания -> дефис)
        normalized_name = re.sub(r"[-_.]+", "-", package_name).lower()

        logger.debug("Querying Simple API", package=package_name, normalized=normalized_name)

        try:
            resp = await client.get(f"/simple/{normalized_name}/", headers={"Accept": "text/html"})
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise PyPIError(f"Package '{package_name}' not found in mirror")
            raise PyPIError(f"Simple API error for '{package_name}': {e}")
        except httpx.TransportError as e:
            raise PyPIError(f"Mirror unreachable: {e}")

        # Парсинг HTML (без внешних зависимостей)
        links = []
        for match in _SIMPLE_API_LINK_RE.finditer(resp.text):
            url, filename = match.groups()
            # Относительный URL нужно сделать абсолютным
            if url.startswith("/"):
                url = f"{self.settings.pypi_mirror_url}{url}"
            elif not url.startswith("http"):
                url = f"{self.settings.pypi_mirror_url}/simple/{normalized_name}/{url}"

            links.append({"url": url, "filename": filename})

        # Фильтрация по версии, если указана
        if version:
            # Имя файла wheel обычно имеет формат: {name}-{version}-{python}-{abi}-{platform}.whl
            version_tag = f"-{version}-"
            links = [link for link in links if version_tag in link["filename"]]

        logger.info("Found package versions", package=package_name, count=len(links))
        return links

    def select_best_wheel(self, urls: List[Dict[str, str]], python_version: str = "cp314") -> Optional[Dict[str, str]]:
        """
        Выбор наиболее подходящего wheel-файла из списка.
        Приоритет: специфичный для платформы/версии Python > универсальный (py3-none-any).
        """
        if not urls:
            return None

        # Фильтруем только .whl файлы
        wheels = [u for u in urls if u["filename"].endswith(".whl")]
        if not wheels:
            return None

        # Ищем сначала специфичный для нужной версии Python (например, cp314)
        for w in wheels:
            if python_version in w["filename"]:
                return w

        # Ищем универсальный (pure python)
        for w in wheels:
            if "py3-none-any" in w["filename"]:
                return w

        # Возвращаем первый попавшийся, если ничего не подошло
        return wheels[0]

    # ==============================================================================
    # СКАЧИВАНИЕ И КЭШИРОВАНИЕ
    # ==============================================================================

    async def download_and_cache(
            self,
            package_name: str,
            version: str,
            python_version: str = "cp314",
            cache_dir: Optional[Path] = None
    ) -> Tuple[Path, str]:
        """
        Скачивание пакета и сохранение в wheel-cache.

        Args:
            package_name: Имя пакета.
            version: Версия.
            python_version: Тэг версии Python (например, 'cp314', 'cp313').
            cache_dir: Путь к кэшу (по умолчанию settings.wheel_cache_path).

        Returns:
            Кортеж (путь_к_файлу, sha256_хэш).
        """
        cache_dir = cache_dir or Path(self.settings.wheel_cache_path)
        cache_dir.mkdir(parents=True, exist_ok=True)

        # 1. Проверяем, есть ли уже файл в кэше
        urls = await self.get_package_urls(package_name, version)
        best_match = self.select_best_wheel(urls, python_version)

        if not best_match:
            raise PyPIError(f"No suitable wheel found for {package_name}=={version} (python={python_version})")

        filename = best_match["filename"]
        dest_path = cache_dir / filename

        if dest_path.exists():
            logger.info("Wheel already cached", package=package_name, version=version, path=str(dest_path))
            sha256 = self._calculate_sha256(dest_path)
            return dest_path, sha256

        # 2. Скачиваем файл
        client = self._get_client()
        logger.info("Downloading wheel", package=package_name, version=version, url=best_match["url"])

        try:
            async with client.stream("GET", best_match["url"]) as resp:
                resp.raise_for_status()

                # Скачиваем во временный файл, чтобы не испортить кэш при обрыве
                tmp_path = dest_path.with_suffix(".tmp")
                sha256_hash = hashlib.sha256()

                with open(tmp_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=8192):
                        f.write(chunk)
                        sha256_hash.update(chunk)

                # Переименовываем в целевой файл (атомарно в пределах одной ФС)
                tmp_path.rename(dest_path)

        except httpx.HTTPError as e:
            if tmp_path.exists():
                tmp_path.unlink()
            raise PyPIError(f"Download failed for {package_name}: {e}")

        sha256 = sha256_hash.hexdigest()
        logger.info("Wheel cached successfully", path=str(dest_path), sha256=sha256)

        return dest_path, sha256

    @staticmethod
    def _calculate_sha256(file_path: Path) -> str:
        """Вычисление SHA256 хэша существующего файла."""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256_hash.update(chunk)
        return sha256_hash.hexdigest()


# Глобальный экземпляр клиента
pypi_client = PyPIClient()