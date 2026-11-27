# vm2/worker/app/sandbox.py
"""
Sandbox Client — Управление изолированными песочницами gVisor.
Production Blueprint v5.3.1 — ВМ №2

Реализует создание, запуск и уничтожение контейнеров через Docker Socket Proxy.
Строго соблюдает требования безопасности Blueprint:
  - gVisor runtime (runsc)
  - Сеть отключена (network: none)
  - Read-only корневая ФС + tmpfs
  - Drop всех Linux capabilities
  - Непривилегированный пользователь (UID 10000)
  - Лимиты CPU, Memory, PIDs

E1/E2: входные файлы и артефакты доставляются через bind-mount per-run каталогов
хоста (/srv/factory/runs/{run_id}/input/, /output/), а не через Docker API archive.
Это исключает потерю данных при монтировании tmpfs поверх PUT и при остановке
контейнера (tmpfs снимается).

E3: wheel-cache — bind-mount хостового каталога, видимого и воркером, и sandbox-демоном.

E7: toolset-образы используются по digest, а не по тегу.
"""

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

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
        self.base_url = self.settings.docker_host.replace("tcp://", "http://")
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.settings.sandbox_timeout + 30,
        )
        # E3: bind-mount хостового wheel-cache (общий с воркером)
        self.wheel_cache_host_path = self.settings.wheel_cache_host_path
        # E1/E2: корень per-run каталогов на хосте
        self.runs_root_host_path = self.settings.sandbox_runs_host_path

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
            image: Docker-образ toolset (digest, например factory/toolset-excel@sha256:...).
            cmd: Команда для выполнения.
            input_files: Словарь {имя_файла: содержимое_байты} для записи в /app/input.
            env: Дополнительные переменные окружения.
        """
        container_id = None
        run_id = env.get("RUN_ID", "unknown") if env else "unknown"

        # E1/E2: per-run каталоги на хосте
        run_dir = Path(self.runs_root_host_path) / run_id
        input_dir = run_dir / "input"
        output_dir = run_dir / "output"

        try:
            # Подготовка каталогов на хосте
            input_dir.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            # Запись входных файлов на хост (E1: они не теряются при start)
            if input_files:
                for name, content in input_files.items():
                    (input_dir / name).write_bytes(content)

            # 1. Создание контейнера с bind-mount
            container_id = await self._create_container(image, cmd, env, run_dir)
            logger.info("Sandbox container created", container_id=container_id[:12], run_id=run_id)

            # 2. Запуск
            await self._start_container(container_id)

            # 3. Ожидание завершения
            exit_code = await self._wait_container(container_id)

            # 4. Получение логов
            stdout, stderr = await self._get_logs(container_id)

            # 5. Чтение артефактов из хостового каталога (E2: они не теряются при stop)
            artifacts = self._read_output_artifacts(output_dir)

            success = exit_code == 0
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
                success=False,
                exit_code=-1,
                stdout="",
                stderr="",
                error_message=f"Timeout after {self.settings.sandbox_timeout}s",
            )

        except Exception as e:
            logger.error("Sandbox execution failed", error=str(e))
            return SandboxResult(
                success=False,
                exit_code=-1,
                stdout="",
                stderr="",
                error_message=str(e),
            )

        finally:
            # 6. Гарантированное уничтожение контейнера
            if container_id:
                await self._remove_container(container_id)

            # 7. Очистка per-run каталогов на хосте
            if run_dir.exists():
                shutil.rmtree(run_dir, ignore_errors=True)

    # ==========================================================================
    # DOCKER API WRAPPERS
    # ==========================================================================

    async def _create_container(
        self,
        image: str,
        cmd: List[str],
        env: Optional[Dict[str, str]],
        run_dir: Path,
    ) -> str:
        """POST /containers/create с bind-mount per-run каталогов."""
        container_env = [
            "PYTHONDONTWRITEBYTECODE=1",
            "HF_HUB_OFFLINE=1",
            "TRANSFORMERS_OFFLINE=1",
        ]
        if env:
            container_env.extend([f"{k}={v}" for k, v in env.items()])

        input_dir = run_dir / "input"
        output_dir = run_dir / "output"

        payload = {
            "Image": image,
            "Cmd": cmd,
            "Env": container_env,
            "User": "10000:10000",
            "WorkingDir": "/app",
            "Tty": False,
            "OpenStdin": False,
            "HostConfig": {
                "Runtime": "runsc",
                "NetworkMode": "none",
                "ReadonlyRootfs": True,
                "SecurityOpt": ["no-new-privileges:true"],
                "CapDrop": ["ALL"],
                "Tmpfs": {
                    "/tmp": "size=512m,uid=10000,gid=10000",
                },
                "NanoCpus": int(self.settings.sandbox_cpu_limit * 1e9),
                "Memory": self._parse_memory(self.settings.sandbox_memory_limit),
                "PidsLimit": self.settings.sandbox_pids_limit,
                "Mounts": [
                    # E1: bind-mount input (read-only)
                    {
                        "Type": "bind",
                        "Source": str(input_dir),
                        "Target": "/app/input",
                        "ReadOnly": True,
                    },
                    # E2: bind-mount output (read-write)
                    {
                        "Type": "bind",
                        "Source": str(output_dir),
                        "Target": "/app/output",
                        "ReadOnly": False,
                    },
                    # E3: bind-mount wheel-cache (read-only, общий с воркером)
                    {
                        "Type": "bind",
                        "Source": self.wheel_cache_host_path,
                        "Target": "/app/wheel-cache",
                        "ReadOnly": True,
                    },
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

    async def _get_logs(self, container_id: str) -> tuple[str, str]:
        """GET /containers/{id}/logs"""
        resp = await self.client.get(
            f"/containers/{container_id}/logs",
            params={"stdout": 1, "stderr": 1, "follow": 0, "tail": "all"},
        )
        resp.raise_for_status()
        return self._demux_logs(resp.content)

    async def _remove_container(self, container_id: str) -> None:
        """DELETE /containers/{id}"""
        try:
            await self.client.delete(
                f"/containers/{container_id}",
                params={"force": 1, "v": 1},
            )
        except Exception as e:
            logger.warning("Failed to remove sandbox container", error=str(e))

    # ==========================================================================
    # HELPERS
    # ==========================================================================

    @staticmethod
    def _read_output_artifacts(output_dir: Path) -> Dict[str, bytes]:
        """E2: чтение артефактов из хостового каталога после завершения контейнера."""
        artifacts = {}
        if output_dir.exists():
            for file_path in output_dir.iterdir():
                if file_path.is_file():
                    artifacts[file_path.name] = file_path.read_bytes()
        return artifacts

    @staticmethod
    def _demux_logs(raw: bytes) -> tuple[str, str]:
        """Разделяет мультиплексированный лог Docker API."""
        stdout, stderr = [], []
        idx = 0
        while idx + 8 <= len(raw):
            stream_type = raw[idx]
            size = int.from_bytes(raw[idx + 4 : idx + 8], "big")
            idx += 8
            chunk = raw[idx : idx + size].decode("utf-8", errors="replace")
            if stream_type == 1:
                stdout.append(chunk)
            elif stream_type == 2:
                stderr.append(chunk)
            idx += size
        return "".join(stdout), "".join(stderr)

    @staticmethod
    def _parse_memory(mem_str: str) -> int:
        """Парсит строку памяти (например, '4g') в байты."""
        mem_str = mem_str.strip().lower()
        multipliers = {"k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}
        if mem_str[-1] in multipliers:
            return int(float(mem_str[:-1]) * multipliers[mem_str[-1]])
        return int(mem_str)