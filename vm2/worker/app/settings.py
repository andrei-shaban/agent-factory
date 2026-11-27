# vm2/worker/app/settings.py
"""
Settings module for the Worker application.
Production Blueprint v5.3.1 — ВМ №2
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
    environment: str = Field(default="production")
    log_level: str = Field(default="info")
    tz: str = Field(default="Asia/Yekaterinburg")

    # ==============================================================================
    # POSTGRESQL RUNTIME & RAG (ВМ №2)
    # ==============================================================================
    postgres_runtime_url: str = Field(
        default="postgresql+asyncpg://factory_worker:password@postgres-runtime:5432/factory"
    )
    postgres_runtime_user: str = Field(default="factory_worker")
    postgres_runtime_password: str = Field(default="")

    # ==============================================================================
    # REDIS — ФАБРИКА (ВМ №2)
    # ==============================================================================
    redis_factory_url: str = Field(default="redis://:password@redis-factory:6379")
    redis_factory_password: str = Field(default="")
    redis_factory_tls: bool = Field(default=False)

    # ==============================================================================
    # LANGFUSE (ВМ №1)
    # ==============================================================================
    langfuse_public_key: str = Field(default="")
    langfuse_secret_key: str = Field(default="")
    langfuse_host: str = Field(default="https://langfuse-ingest.corp.example.com")
    langfuse_client_cert_path: str = Field(default="/etc/certs/vm2-client.crt")
    langfuse_client_key_path: str = Field(default="/etc/certs/vm2-client.key")
    langfuse_ca_cert_path: str = Field(default="/etc/certs/ca.crt")

    # ==============================================================================
    # SEAWEEDFS / S3 (ВМ №2)
    # ==============================================================================
    seaweed_s3_endpoint: str = Field(default="http://seaweedfs-s3:8333")
    seaweed_s3_access_key: str = Field(default="")
    seaweed_s3_secret_key: str = Field(default="")
    seaweed_s3_bucket_factory: str = Field(default="factory-artifacts")
    seaweed_s3_bucket_backups: str = Field(default="postgres-backups")

    # ==============================================================================
    # mTLS — ВНЕШНИЕ ИНТЕГРАЦИИ
    # ==============================================================================
    mtls_ca_cert_path: str = Field(default="/etc/certs/corp-ca.crt")
    mtls_client_cert_path: str = Field(default="/etc/certs/vm2-client.crt")
    mtls_client_key_path: str = Field(default="/etc/certs/vm2-client.key")

    # ==============================================================================
    # GPU ЕНДПОИНТЫ
    # ==============================================================================
    gpu_endpoint_url: str = Field(default="https://gpu-node.corp.example.com:443")
    gpu_llm_timeout: int = Field(default=600)
    gpu_llm_stream_timeout: int = Field(default=3600)
    gpu_ocr_timeout: int = Field(default=120)
    gpu_llm_model_name: Optional[str] = Field(default=None)

    # ==============================================================================
    # КОРПОРАТИВНОЕ ХРАНИЛИЩЕ ДАННЫХ (ВМ №4)
    # ==============================================================================
    corp_pg_url: str = Field(
        default="postgresql+asyncpg://factory_reader:password@vm4-postgres:5432/corp_data"
    )

    # ==============================================================================
    # РАБОЧАЯ СТАНЦИЯ (ЭТАП 2)
    # ==============================================================================
    workstation_url: str = Field(default="https://workstation.corp.example.com:443")
    workstation_timeout: int = Field(default=30)

    # ==============================================================================
    # ПЕСОЧНИЦЫ (SANDBOX)
    # ==============================================================================
    sandbox_cpu_limit: float = Field(default=2.0)
    sandbox_memory_limit: str = Field(default="4g")
    sandbox_pids_limit: int = Field(default=256)
    sandbox_timeout: int = Field(default=300)
    docker_host: str = Field(default="tcp://docker-socket-proxy:2375")

    # E3: хостовой путь к wheel-cache (bind-mount, общий с sandbox-демоном)
    wheel_cache_host_path: str = Field(default="/srv/factory/wheel-cache")
    # E1/E2: корень per-run каталогов на хосте
    sandbox_runs_host_path: str = Field(default="/srv/factory/runs")

    # E7: toolset-образы по digest (фиксируются deploy-toolsets.sh)
    toolset_image_excel: str = Field(
        default="factory/toolset-excel:1.0.0"
    )

    # ==============================================================================
    # PYPI MIRROR (ВМ №1)
    # ==============================================================================
    pypi_mirror_url: str = Field(default="http://vm1-pypiserver:8080")

    # ==============================================================================
    # AIR-GAP / TELEMETRY
    # ==============================================================================
    langsmith_tracing: bool = Field(default=False)
    langchain_tracing_v2: bool = Field(default=False)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    """Возвращает кэшированный экземпляр настроек."""
    return Settings()