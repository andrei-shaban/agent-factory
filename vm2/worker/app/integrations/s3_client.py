# vm2/worker/app/integrations/s3_client.py
"""
S3 Client — Клиент для SeaweedFS S3 (ВМ №2).
Production Blueprint v5.2

Назначение:
  - Загрузка/скачивание артефактов фабрики (drafts, final acts, contracts).
  - Хранение blob Langfuse (events, media).
  - Бэкапы PostgreSQL (WAL-архивация, pg_basebackup).
  - Path-style addressing (требование Blueprint для SeaweedFS).

Использует boto3 (стандарт де-факто для S3).
"""

import io
from typing import BinaryIO, Dict, List, Optional

import boto3
import structlog
from botocore.config import Config

from worker.app.settings import Settings, get_settings

logger = structlog.get_logger()


class S3Client:
    """
    Асинхронно-совместимый клиент для SeaweedFS S3.

    Boto3 не имеет нативной async-поддержки, поэтому используем sync-методы
    в async-контексте (для I/O-bound операций это приемлемо).
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._client: Optional[boto3.client] = None

    def _get_client(self) -> boto3.client:
        """Ленивая инициализация boto3 клиента."""
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=self.settings.seaweed_s3_endpoint,
                aws_access_key_id=self.settings.seaweed_s3_access_key,
                aws_secret_access_key=self.settings.seaweed_s3_secret_key,
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},  # Path-style для SeaweedFS
                    connect_timeout=5,
                    read_timeout=60,
                ),
            )
            logger.info("S3 client initialized", endpoint=self.settings.seaweed_s3_endpoint)
        return self._client

    # ==============================================================================
    # UPLOAD
    # ==============================================================================

    async def upload_file(
            self,
            bucket: str,
            key: str,
            file_content: bytes,
            content_type: Optional[str] = None,
            metadata: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Загрузка файла в S3.

        Args:
            bucket: Имя бакета (factory-artifacts, postgres-backups, langfuse-events).
            key: Путь к файлу в бакете (например, "runs/{run_id}/draft.pdf").
            file_content: Содержимое файла в байтах.
            content_type: MIME-тип (например, "application/pdf").
            metadata: Пользовательские метаданные (ключи должны быть строками).

        Returns:
            URL загруженного файла (для хранения в БД).
        """
        client = self._get_client()
        kwargs = {
            "Bucket": bucket,
            "Key": key,
            "Body": file_content,
        }
        if content_type:
            kwargs["ContentType"] = content_type
        if metadata:
            kwargs["Metadata"] = metadata

        logger.info("Uploading to S3", bucket=bucket, key=key, size=len(file_content))

        try:
            client.put_object(**kwargs)
            url = f"{self.settings.seaweed_s3_endpoint}/{bucket}/{key}"
            logger.info("Upload successful", url=url)
            return url
        except Exception as e:
            logger.error("Upload failed", bucket=bucket, key=key, error=str(e))
            raise

    async def upload_fileobj(
            self,
            bucket: str,
            key: str,
            fileobj: BinaryIO,
            content_type: Optional[str] = None,
    ) -> str:
        """
        Загрузка файла из file-like объекта (для больших файлов без загрузки в память).
        """
        client = self._get_client()
        kwargs = {
            "Bucket": bucket,
            "Key": key,
            "Body": fileobj,
        }
        if content_type:
            kwargs["ContentType"] = content_type

        logger.info("Uploading fileobj to S3", bucket=bucket, key=key)

        try:
            client.upload_fileobj(**kwargs)
            url = f"{self.settings.seaweed_s3_endpoint}/{bucket}/{key}"
            return url
        except Exception as e:
            logger.error("Upload fileobj failed", bucket=bucket, key=key, error=str(e))
            raise

    # ==============================================================================
    # DOWNLOAD
    # ==============================================================================

    async def download_file(self, bucket: str, key: str) -> bytes:
        """
        Скачивание файла из S3.

        Args:
            bucket: Имя бакета.
            key: Путь к файлу.

        Returns:
            Содержимое файла в байтах.
        """
        client = self._get_client()
        logger.info("Downloading from S3", bucket=bucket, key=key)

        try:
            resp = client.get_object(Bucket=bucket, Key=key)
            content = resp["Body"].read()
            logger.info("Download successful", bucket=bucket, key=key, size=len(content))
            return content
        except Exception as e:
            logger.error("Download failed", bucket=bucket, key=key, error=str(e))
            raise

    async def download_fileobj(self, bucket: str, key: str, fileobj: BinaryIO) -> None:
        """
        Скачивание файла в file-like объект (для больших файлов).
        """
        client = self._get_client()
        logger.info("Downloading fileobj from S3", bucket=bucket, key=key)

        try:
            client.download_fileobj(Bucket=bucket, Key=key, Fileobj=fileobj)
        except Exception as e:
            logger.error("Download fileobj failed", bucket=bucket, key=key, error=str(e))
            raise

    # ==============================================================================
    # LIST & DELETE
    # ==============================================================================

    async def list_objects(self, bucket: str, prefix: str = "") -> List[Dict[str, str]]:
        """
        Список объектов в бакете с префиксом.

        Returns:
            Список словарей {"Key": "...", "Size": 123, "LastModified": "..."}
        """
        client = self._get_client()
        logger.info("Listing S3 objects", bucket=bucket, prefix=prefix)

        try:
            resp = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
            objects = [
                {
                    "Key": obj["Key"],
                    "Size": obj["Size"],
                    "LastModified": obj["LastModified"].isoformat(),
                }
                for obj in resp.get("Contents", [])
            ]
            return objects
        except Exception as e:
            logger.error("List objects failed", bucket=bucket, prefix=prefix, error=str(e))
            raise

    async def delete_object(self, bucket: str, key: str) -> None:
        """Удаление объекта из S3."""
        client = self._get_client()
        logger.info("Deleting from S3", bucket=bucket, key=key)

        try:
            client.delete_object(Bucket=bucket, Key=key)
        except Exception as e:
            logger.error("Delete failed", bucket=bucket, key=key, error=str(e))
            raise

    # ==============================================================================
    # BUCKET OPERATIONS
    # ==============================================================================

    async def bucket_exists(self, bucket: str) -> bool:
        """Проверка существования бакета."""
        client = self._get_client()
        try:
            client.head_bucket(Bucket=bucket)
            return True
        except Exception:
            return False


# Глобальный экземпляр клиента
s3_client = S3Client()