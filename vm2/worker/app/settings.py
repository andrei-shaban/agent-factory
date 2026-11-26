# vm2/worker/app/settings.py
"""
Settings module for the Worker application.
Production Blueprint v5.2 — ВМ №2

Читает переменные окружения (инжектируются docker-compose из .env).
Используется для инициализации подключений к БД, Redis, S3, GPU и настройки песочниц.
"""

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Глобальные настройки приложения Worker."""

    # ==============================================================================
    # BASIC
    # ==============================================================================
    environment: str = Field(default="production", description="Окружение (production/staging)")
    log_level: str = Field(default="info", description="Уровень логирования")
    tz: str = Field(default="Asia/Yekaterinburg", description="Часовой пояс")

    # ==============================================================================
    # POSTGRESQL RUNTIME & RAG (ВМ №2)
    # ==============================================================================
    postgres_runtime_url: str = Field(
        default="postgresql+asyncpg://factory_worker:password@postgres-runtime:5432/factory",
        description="URL PostgreSQL Runtime (чекпоинты, реестр ранов, RAG)"
    )
    postgres_runtime_user: str = Field(default="factory_worker")
    postgres_runtime_password: str = Field(default="")
    postgres_shared_buffers: str = Field(default="2GB")
    postgres_work_mem: str = Field(default="256MB")

    # ==============================================================================
    # REDIS — ФАБРИКА (ВМ №2)
    # ==============================================================================
    redis_factory_url: str = Field(
        default="redis://:password@redis-factory:6379",
        description="URL Redis фабрики (очереди, Streams)"
    )
    redis_factory_password: str = Field(default="")
    redis_factory_tls: bool = Field(default=False, description="Использовать TLS для Redis")

    # ==============================================================================
    # LANGFUSE (ВМ №1)
    # ==============================================================================
    langfuse_public_key: str = Field(default="", description="Langfuse Public Key")
    langfuse_secret_key: str = Field(default="", description="Langfuse Secret Key")
    langfuse_host: str = Field(default="https://langfuse-ingest.corp.example.com", description="Langfuse mTLS ingest endpoint")
    langfuse_client_cert_path: str = Field(default="/etc/certs/vm2-client.crt")
    langfuse_client_key_path: str = Field(default="/etc/certs/vm2-client.key")
    langfuse_ca_cert_path: str = Field(default="/etc/certs/ca.crt")

    # ==============================================================================
    # SEAWEEDFS / S3 (ВМ №2)
    # ==============================================================================
    seaweed_s3_endpoint: str = Field(default="http://seaweedfs-s3:8333", description="S3 Endpoint")
    seaweed_s3_access_key: str = Field(default="", description="S3 Access Key")
    seaweed_s3_secret_key: str = Field(default="", description="S3 Secret Key")
    seaweed_s3_bucket_factory: str = Field(default="factory-artifacts", description="Бакет для артефактов")
    seaweed_s3_bucket_backups: str = Field(default="postgres-backups", description="Бакет для бэкапов")

    # ==============================================================================
    # mTLS — ВНЕШНИЕ ИНТЕГРАЦИИ
    # ==============================================================================
    mtls_ca_cert_path: str = Field(
        default="/etc/certs/corp-ca.crt",
        description="Путь к корпоративному Root CA"
    )
    mtls_client_cert_path: str = Field(
        default="/etc/certs/vm2-client.crt",
        description="Путь к клиентскому сертификату"
    )
    mtls_client_key_path: str = Field(
        default="/etc/certs/vm2-client.key",
        description="Путь к приватному ключу"
    )

    # ==============================================================================
    # GPU ЕНДПОИНТЫ (.228)
    # ==============================================================================
    gpu_endpoint_url: str = Field(default="https://gpu-node.corp.example.com:443", description="URL mTLS шлюза GPU")
    gpu_llm_timeout: int = Field(default=600, description="Таймаут LLM запросов (сек)")
    gpu_llm_stream_timeout: int = Field(default=3600, description="Таймаут стриминга LLM (сек)")
    gpu_ocr_timeout: int = Field(default=120, description="Таймаут OCR запросов (сек)")
    gpu_llm_model_name: Optional[str] = Field(default=None, description="Имя модели (автообнаружение, если пусто)")

    # ==============================================================================
    # КОРПОРАТИВНОЕ ХРАНИЛИЩЕ ДАННЫХ (ВМ №4)
    # ==============================================================================
    corp_pg_url: str = Field(
        default="postgresql+asyncpg://factory_reader:password@vm4-postgres:5432/corp_data",
        description="URL корпоративного PG (read-only, mTLS)"
    )

    # ==============================================================================
    # РАБОЧАЯ СТАНЦИЯ (ЭТАП 2)
    # ==============================================================================
    workstation_url: str = Field(default="https://workstation.corp.example.com:443", description="URL рабочей станции")
    workstation_timeout: int = Field(default=30, description="Таймаут запросов к рабочей станции (сек)")

    # ==============================================================================
    # ПЕСОЧНИЦЫ (SANDBOX)
    # ==============================================================================
    sandbox_cpu_limit: float = Field(default=2.0, description="Лимит CPU для песочницы")
    sandbox_memory_limit: str = Field(default="4g", description="Лимит памяти для песочницы")
    sandbox_pids_limit: int = Field(default=256, description="Лимит PID для песочницы")
    sandbox_timeout: int = Field(default=300, description="Таймаут выполнения инструмента в песочнице (сек)")
    wheel_cache_path: str = Field(default="/app/wheel-cache", description="Путь к wheel-cache")
    docker_host: str = Field(default="tcp://docker-socket-proxy:2375", description="Docker Socket Proxy URL")

    # ==============================================================================
    # PYPI MIRROR (ВМ №1)
    # ==============================================================================
    pypi_mirror_url: str = Field(default="http://vm1-pypiserver:8080", description="URL внутреннего зеркала PyPI")

    # ==============================================================================
    # AIR-GAP / TELEMETRY
    # ==============================================================================
    langsmith_tracing: bool = Field(default=False, description="Отключить LangSmith")
    langchain_tracing_v2: bool = Field(default=False, description="Отключить LangChain tracing")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )


@lru_cache()
def get_settings() -> Settings:
    """
    Возвращает кэшированный экземпляр настроек.
    Используется как dependency в FastAPI/TaskIQ.
    """
    return Settings()