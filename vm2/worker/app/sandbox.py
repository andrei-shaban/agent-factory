"""
Sandbox Client — Управление изолированными песочницами gVisor.
Production Blueprint v5.1 — ВМ №2

Реализует создание, запуск и уничтожение контейнеров через Docker Socket Proxy.
Строго соблюдает требования безопасности Blueprint:
  - gVisor runtime (runsc)
  - Сеть отключена (network: none)
  - Read-only корневая ФС + tmpfs
  - Drop всех Linux capabilities
  - Непривилегированный пользователь (UID 10000)
  - Лимиты CPU, Memory, PIDs

Использует httpx для взаимодействия с Docker API (без добавления aiodocker).
"""

import base64
import io
import json
import tarfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx
import structlog

from worker.app.settings import Settings, get_settings

logger = structlog.get_logger()


@dataclass
class SandboxResult:
    """Результат выполнения кода в песочнице."""
    success: bool
    exit_code: int
    stdout: str
    stderr: str
    artifacts: Dict[str, bytes] = field(default_factory=dict)
    error_message: Optional[str] = None


class SandboxClient:
    """Клиент для управления песочницами через Docker Socket Proxy."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        # Docker Socket Proxy exposes TCP 2375 internally
        self.base_url = self.settings.docker_host.replace("tcp://", "http://")
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.settings.sandbox_timeout + 30,  # Запас на создание/удаление
        )
        self.volume_name = "factory-vm2_wheel-cache"  # Имя volume из docker-compose.yml

    # ==========================================================================
    # PUBLIC API
    # ==========================================================================

    async def execute_tool(
            self,
            image: str,
            cmd: List[str],
            input_files: Optional[Dict[str, bytes]] = None,
            env: Optional[Dict[str, str]] = None,
    ) -> SandboxResult:
        """
        Запуск инструмента в изолированной песочнице.

        Args:
            image: Docker-образ toolset (например, factory/toolset-excel:1.0.0).
            cmd: Команда для выполнения (например, ["python", "/app/tools/run.py"]).
            input_files: Словарь {путь_в_контейнере: содержимое_байты} для монтирования.
            env: Дополнительные переменные окружения для контейнера.
        """
        container_id = None
        try:
            # 1. Создание контейнера
            container_id = await self._create_container(image, cmd, env)
            logger.info("Sandbox container created", container_id=container_id[:12])

            # 2. Загрузка входных файлов (если есть)
            if input_files:
                await self._upload_archive(container_id, input_files, "/app/input")

            # 3. Запуск
            await self._start_container(container_id)

            # 4. Ожидание завершения
            exit_code = await self._wait_container(container_id)

            # 5. Получение логов
            stdout, stderr = await self._get_logs(container_id)

            # 6. Выгрузка артефактов из /app/output
            artifacts = await self._download_archive(container_id, "/app/output")

            success = (exit_code == 0)
            if not success:
                logger.warning("Sandbox tool failed", exit_code=exit_code, stderr=stderr[:500])

            return SandboxResult(
                success=success,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                artifacts=artifacts,
                error_message=stderr if not success else None,
            )

        except httpx.TimeoutException:
            logger.error("Sandbox execution timed out", timeout=self.settings.sandbox_timeout)
            return SandboxResult(
                success=False, exit_code=-1, stdout="", stderr="",
                error_message=f"Timeout after {self.settings.sandbox_timeout}s"
            )
        except Exception as e:
            logger.error("Sandbox execution failed", error=str(e))
            return SandboxResult(
                success=False, exit_code=-1, stdout="", stderr="",
                error_message=str(e)
            )
        finally:
            # 7. Гарантированное уничтожение контейнера
            if container_id:
                await self._remove_container(container_id)

    # ==========================================================================
    # DOCKER API WRAPPERS
    # ==========================================================================

    async def _create_container(self, image: str, cmd: List[str], env: Optional[Dict[str, str]]) -> str:
        """POST /containers/create"""
        # Базовые переменные окружения
        container_env = [
            "PYTHONDONTWRITEBYTECODE=1",
            "HF_HUB_OFFLINE=1",
            "TRANSFORMERS_OFFLINE=1",
        ]
        if env:
            container_env.extend([f"{k}={v}" for k, v in env.items()])

        payload = {
            "Image": image,
            "Cmd": cmd,
            "Env": container_env,
            "User": "10000:10000",  # sandbox:sandbox
            "WorkingDir": "/app",
            "Tty": False,
            "OpenStdin": False,
            "HostConfig": {
                "Runtime": "runsc",  # gVisor
                "NetworkMode": "none",
                "ReadonlyRootfs": True,
                "SecurityOpt": ["no-new-privileges:true"],
                "CapDrop": ["ALL"],
                "Tmpfs": {
                    "/tmp": f"size=512m,uid=10000,gid=10000",
                    "/app/input": f"size=100m,uid=10000,gid=10000",
                    "/app/output": f"size=512m,uid=10000,gid=10000",
                },
                "NanoCpus": int(self.settings.sandbox_cpu_limit * 1e9),
                "Memory": self._parse_memory(self.settings.sandbox_memory_limit),
                "PidsLimit": self.settings.sandbox_pids_limit,
                "Mounts": [
                    {
                        "Type": "volume",
                        "Source": self.volume_name,
                        "Target": "/app/wheel-cache",
                        "ReadOnly": True,
                    }
                ],
            },
        }

        resp = await self.client.post("/containers/create", json=payload)
        resp.raise_for_status()
        return resp.json()["Id"]

    async def _start_container(self, container_id: str) -> None:
        """POST /containers/{id}/start"""
        resp = await self.client.post(f"/containers/{container_id}/start")
        resp.raise_for_status()

    async def _wait_container(self, container_id: str) -> int:
        """POST /containers/{id}/wait"""
        resp = await self.client.post(f"/containers/{container_id}/wait")
        resp.raise_for_status()
        return resp.json()["StatusCode"]

    async def _get_logs(self, container_id: str) -> Tuple[str, str]:
        """GET /containers/{id}/logs"""
        resp = await self.client.get(
            f"/containers/{container_id}/logs",
            params={"stdout": 1, "stderr": 1, "follow": 0, "tail": "all"},
        )
        resp.raise_for_status()
        # Docker API возвращает мультиплексированный поток с 8-байтными заголовками
        return self._demux_logs(resp.content)

    async def _upload_archive(self, container_id: str, files: Dict[str, bytes], target_dir: str) -> None:
        """PUT /containers/{id}/archive"""
        tar_bytes = self._create_tar(files)
        resp = await self.client.put(
            f"/containers/{container_id}/archive",
            params={"path": target_dir},
            content=tar_bytes,
            headers={"Content-Type": "application/x-tar"},
        )
        resp.raise_for_status()

    async def _download_archive(self, container_id: str, source_dir: str) -> Dict[str, bytes]:
        """GET /containers/{id}/archive"""
        resp = await self.client.get(
            f"/containers/{container_id}/archive",
            params={"path": source_dir},
        )
        if resp.status_code == 404:
            return {}  # Директория пуста или не существует

        resp.raise_for_status()
        return self._extract_tar(resp.content)

    async def _remove_container(self, container_id: str) -> None:
        """DELETE /containers/{id}"""
        try:
            await self.client.delete(
                f"/containers/{container_id}",
                params={"force": 1, "v": 1},  # force + remove volumes
            )
        except Exception as e:
            logger.warning("Failed to remove sandbox container", error=str(e))

    # ==========================================================================
    # HELPERS
    # ==========================================================================

    @staticmethod
    def _create_tar(files: Dict[str, bytes]) -> bytes:
        """Создает in-memory tar-архив из словаря файлов."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for name, content in files.items():
                info = tarfile.TarInfo(name=name)
                info.size = len(content)
                info.mode = 0o644
                info.uid = 10000
                info.gid = 10000
                tar.addfile(info, io.BytesIO(content))
        return buf.getvalue()

    @staticmethod
    def _extract_tar(tar_bytes: bytes) -> Dict[str, bytes]:
        """Извлекает файлы из tar-архива в словарь."""
        result = {}
        buf = io.BytesIO(tar_bytes)
        with tarfile.open(fileobj=buf, mode="r") as tar:
            for member in tar.getmembers():
                if member.isfile():
                    f = tar.extractfile(member)
                    if f:
                        result[member.name] = f.read()
        return result

    @staticmethod
    def _demux_logs(raw: bytes) -> Tuple[str, str]:
        """
        Разделяет мультиплексированный лог Docker API.
        Формат: [stream_type(1 byte)][0,0,0][size(4 bytes big-endian)][payload]
        stream_type: 1=stdout, 2=stderr
        """
        stdout, stderr = [], []
        idx = 0
        while idx + 8 <= len(raw):
            stream_type = raw[idx]
            size = int.from_bytes(raw[idx + 4: idx + 8], "big")
            idx += 8
            chunk = raw[idx: idx + size].decode("utf-8", errors="replace")
            if stream_type == 1:
                stdout.append(chunk)
            elif stream_type == 2:
                stderr.append(chunk)
            idx += size
        return "".join(stdout), "".join(stderr)

    @staticmethod
    def _parse_memory(mem_str: str) -> int:
        """Парсит строку памяти (например, '4g') в байты для Docker API."""
        mem_str = mem_str.strip().lower()
        multipliers = {"k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4}
        if mem_str[-1] in multipliers:
            return int(float(mem_str[:-1]) * multipliers[mem_str[-1]])
        return int(mem_str)