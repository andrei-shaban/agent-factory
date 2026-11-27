#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
init_project.py — Единая точка инициализации проекта
Production Blueprint v5.2.1 — Фабрика агентов и алгоритмов

Объединяет:
  1. Генерацию секретов для ВМ №1 и ВМ №2
  2. Рендеринг конфигураций (Keycloak realm, Redis, SeaweedFS S3)
  3. Синхронизацию общих ключей между ВМ (GATEWAY_DB_PASSWORD, S3_*, LANGFUSE_*)

Запуск:
  python init_project.py            # инициализация; установленные пароли сохраняются
  python init_project.py --rotate   # принудительная ротация всех генерируемых секретов

ВАЖНО о ротации:
  Пароли ролей PostgreSQL и пользователя ClickHouse применяются init-скриптами
  ТОЛЬКО при пустом томе данных. После --rotate тома нужно пересоздать,
  иначе сервисы получат отказ аутентификации (Prisma P1000 / SCRAM failed).

Пути:
  <корень>/vm1  — периметр, шлюзы, observability
  <корень>/vm2  — вычисления, песочницы, данные агентов
"""

import argparse
import base64
import json
import re
import secrets
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Dict, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ==============================================================================
# ПУТИ
# ==============================================================================
BASE_DIR = Path(__file__).parent.resolve()
VM1_DIR = BASE_DIR / "vm1"
VM2_DIR = BASE_DIR / "vm2"

# ==============================================================================
# АЛФАВИТЫ ДЛЯ ГЕНЕРАЦИИ СЕКРЕТОВ
# ==============================================================================
# URL-unreserved символы (RFC 3986): ALPHA / DIGIT / "-" / "_".
# Ни один из них не требует percent-encoding в connection strings (PostgreSQL,
# Redis, S3), не ломает SQL-литералы в init-скриптах и ACL-правила Redis.
# 64 символа × 32 позиции ≈ 192 бита энтропии.
ALPHABET_URLSAFE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
ALPHABET_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
ALPHABET_UPPER_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
ALPHABET_ACL = ALPHABET_ALNUM

# Файлы для рендеринга
VM1_RENDER_FILES = [
    ("keycloak/realm-export.json", "rendered/realm-export.json"),
]

VM2_RENDER_FILES = [
    ("redis/redis.conf", "rendered/redis/redis.conf"),
    ("redis/acl.conf", "rendered/redis/acl.conf"),
    ("seaweedfs/s3.json", "rendered/seaweedfs/s3.json"),
]

# Ключи Langfuse создаются в UI и НЕ генерируются скриптом.
LANGFUSE_UI_KEYS = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
LANGFUSE_PLACEHOLDERS = {"LANGFUSE_PUBLIC_KEY": "pk-lf-...", "LANGFUSE_SECRET_KEY": "sk-lf-..."}

# ==============================================================================
# ЦВЕТА (работает и в PowerShell, и в cmd, и в bash)
# ==============================================================================
class C:
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    BLUE = "\033[0;34m"
    BOLD = "\033[1m"
    NC = "\033[0m"


def log_info(msg: str) -> None:
    print(f"{C.GREEN}[INFO]{C.NC} {msg}")


def log_warn(msg: str) -> None:
    print(f"{C.YELLOW}[WARN]{C.NC} {msg}")


def log_error(msg: str) -> None:
    print(f"{C.RED}[ERROR]{C.NC} {msg}", file=sys.stderr)


def log_step(msg: str) -> None:
    print(f"\n{C.BLUE}{C.BOLD}▶ {msg}{C.NC}")


# ==============================================================================
# ГЕНЕРАЦИЯ СЕКРЕТОВ
# ==============================================================================
def gen_32_pass() -> str:
    """Пароль для БД/сервисов: только URL-unreserved символы, 32 позиции."""
    return "".join(secrets.choice(ALPHABET_URLSAFE) for _ in range(32))


def gen_acl_password(length: int = 32) -> str:
    """Пароль ACL-пользователя Redis: буквенно-цифровой."""
    return "".join(secrets.choice(ALPHABET_ACL) for _ in range(length))


def gen_alnum(length: int, upper_only: bool = False) -> str:
    alphabet = ALPHABET_UPPER_ALNUM if upper_only else ALPHABET_ALNUM
    return "".join(secrets.choice(alphabet) for _ in range(length))


def gen_base64(num_bytes: int) -> str:
    return base64.b64encode(secrets.token_bytes(num_bytes)).decode("ascii")


def gen_hex(num_bytes: int) -> str:
    return secrets.token_hex(num_bytes)


_PLACEHOLDER_RE = re.compile(r"^<[^<>]*>$")


def is_placeholder(value: str) -> bool:
    """
    Любая незаполненная заготовка шаблона, а не только <CHANGE_ME...>.
    Покрывает <64_BYTE_RANDOM>, <RANDOM_SALT>, <32_BYTE_RANDOM_BASE64>,
    <SEAWEED_S3_KEY>, <SEAWEED_S3_SECRET>, <CHANGE_ME_FROM_KEYCLOAK_CLIENT> и т.п.
    """
    v = (value or "").strip()
    if not v:
        return True
    if _PLACEHOLDER_RE.match(v):
        return True
    if v in LANGFUSE_PLACEHOLDERS.values():
        return True
    if "CHANGE_ME" in v.upper():
        return True
    if any(ch.isspace() for ch in v):
        return True
    return False


def configured_value(existing: Dict[str, str], key: str, rotate: bool = False) -> Optional[str]:
    """Сохраняет реально установленное значение; заготовки игнорирует."""
    if rotate:
        return None
    value = existing.get(key, "").strip()
    return None if is_placeholder(value) else value


def urlencode_pairs(secrets_map: Dict[str, str]) -> Dict[str, str]:
    """
    URL-encoded пары для connection strings.
    Для сгенерированных паролей кодирование не требуется (алфавит URL-unreserved),
    но пары сохраняются для совместимости с compose и для унаследованных значений.
    """
    mapping = {
        "CLICKHOUSE_PASSWORD_URLENCODED": "CLICKHOUSE_PASSWORD",
        "REDIS_LANGFUSE_ACL_PASSWORD_URLENCODED": "REDIS_LANGFUSE_ACL_PASSWORD",
        "LANGFUSE_DB_PASSWORD_URLENCODED": "LANGFUSE_DB_PASSWORD",
        "REDIS_FACTORY_ACL_PASSWORD_URLENCODED": "REDIS_FACTORY_ACL_PASSWORD",
        "GATEWAY_DB_PASSWORD_URLENCODED": "GATEWAY_DB_PASSWORD",
    }
    result: Dict[str, str] = {}
    for enc_key, src_key in mapping.items():
        if secrets_map.get(src_key):
            result[enc_key] = urllib.parse.quote(secrets_map[src_key], safe="")
    return result


def build_vm1_secrets(existing: Optional[Dict[str, str]] = None, rotate: bool = False) -> Dict[str, str]:
    """Секреты ВМ №1: установленные сохраняются, отсутствующие генерируются."""
    existing = existing or {}
    generated = {
        # PostgreSQL Auth & Observability
        "POSTGRES_AUTH_PASSWORD": gen_32_pass(),
        # Keycloak (Keycloak 26+ использует KC_BOOTSTRAP_ADMIN_*)
        "KEYCLOAK_ADMIN_PASSWORD": gen_32_pass(),  # Legacy (Keycloak < 26)
        "KC_BOOTSTRAP_ADMIN_PASSWORD": gen_32_pass(),  # Keycloak 26+
        "KEYCLOAK_DB_PASSWORD": gen_32_pass(),
        # oauth2-proxy
        "OAUTH2_PROXY_CLIENT_SECRET": gen_32_pass(),
        "OAUTH2_PROXY_COOKIE_SECRET": gen_alnum(32),
        # Redis Langfuse
        "REDIS_LANGFUSE_PASSWORD": gen_32_pass(),
        "REDIS_LANGFUSE_ACL_PASSWORD": gen_32_pass(),
        # ClickHouse
        "CLICKHOUSE_PASSWORD": gen_32_pass(),
        # Langfuse
        "LANGFUSE_DB_PASSWORD": gen_32_pass(),
        "LANGFUSE_NEXTAUTH_SECRET": gen_base64(64),
        "LANGFUSE_SALT": gen_base64(32),
        "LANGFUSE_ENCRYPTION_KEY": gen_hex(32),
        # Langfuse UI — OIDC-федерация через Keycloak (F2)
        "AUTH_CUSTOM_CLIENT_SECRET": gen_32_pass(),
        # Общие ключи (синхронизируются с ВМ №2)
        "REDIS_FACTORY_ACL_PASSWORD": gen_acl_password(),
        "GATEWAY_DB_PASSWORD": gen_32_pass(),
        "S3_ACCESS_KEY": gen_alnum(20, upper_only=True),
        "S3_SECRET_KEY": gen_alnum(40),
    }

    result = {
        key: (configured_value(existing, key, rotate) or value)
        for key, value in generated.items()
    }
    result.update(urlencode_pairs(result))
    return result


def build_vm2_secrets(
    shared: Dict[str, str],
    existing: Optional[Dict[str, str]] = None,
    rotate: bool = False,
) -> Dict[str, str]:
    """
    Секреты ВМ №2. Общие ключи берутся из ВМ №1 (параметр shared).
    API-ключи Langfuse НЕ фабрикуются — только из UI (или плейсхолдер).
    """
    existing = existing or {}
    generated = {
        "POSTGRES_RUNTIME_PASSWORD": gen_32_pass(),
        "FACTORY_DB_PASSWORD": gen_32_pass(),
        "WORKER_DB_PASSWORD": gen_32_pass(),
        "REDIS_FACTORY_PASSWORD": gen_acl_password(),
        "CORP_PG_PASSWORD": gen_32_pass(),
        "SEAWEEDFS_FILER_SIGNING_KEY": gen_hex(32),
    }

    result = {
        key: (configured_value(existing, key, rotate) or value)
        for key, value in generated.items()
    }

    # Общие ключи: ВМ №1 авторитетна; иначе сохраняем установленное; иначе генерируем.
    for key, fallback in {
        "GATEWAY_DB_PASSWORD": gen_32_pass(),
        "REDIS_FACTORY_ACL_PASSWORD": gen_acl_password(),
        "S3_ACCESS_KEY": gen_alnum(20, upper_only=True),
        "S3_SECRET_KEY": gen_alnum(40),
    }.items():
        result[key] = shared.get(key) or configured_value(existing, key, rotate) or fallback

    # API-ключи Langfuse: только реальные значения из UI, без фабикации.
    for key in LANGFUSE_UI_KEYS:
        candidate = shared.get(key) or existing.get(key, "")
        result[key] = candidate if not is_placeholder(candidate) else LANGFUSE_PLACEHOLDERS[key]

    result.update(urlencode_pairs(result))
    return result


def quote_env_value(value: str) -> str:
    """Безопасное квотирование значения .env (Compose снимает парные кавычки)."""
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    raise ValueError("Значение содержит оба типа кавычек — не может быть записано в .env")


def update_env_file(env_path: Path, new_secrets: Dict[str, str]) -> int:
    """Обновляет .env, заменяя значения известных ключей. Возвращает число замен."""
    if not env_path.exists():
        log_error(f"Файл не найден: {env_path}")
        return 0

    # Резервная копия: vm1/.env -> vm1/.env.bak (в .gitignore)
    backup_path = env_path.with_name(env_path.name + ".bak")
    shutil.copy2(env_path, backup_path)

    # utf-8-sig: снимаем BOM, если он был, и пишем чистый UTF-8 без BOM.
    original_content = env_path.read_text(encoding="utf-8-sig")
    pattern = re.compile(r"^([A-Z0-9_]+)=(.*)$")

    updated_lines = []
    replaced_count = 0
    updated_keys = set()

    for line in original_content.splitlines():
        match = pattern.match(line.strip())
        if match:
            key = match.group(1)
            if key in new_secrets and new_secrets[key] is not None:
                updated_lines.append(f"{key}={quote_env_value(new_secrets[key])}")
                updated_keys.add(key)
                replaced_count += 1
                continue
        updated_lines.append(line)

    missing_keys = [
        key for key, value in new_secrets.items()
        if key not in updated_keys and value is not None
    ]
    if missing_keys:
        if updated_lines and updated_lines[-1].strip():
            updated_lines.append("")
        updated_lines.extend(
            f"{key}={quote_env_value(new_secrets[key])}" for key in missing_keys
        )

    env_path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8", newline="\n")
    return replaced_count


# ==============================================================================
# РЕНДЕРИНГ КОНФИГУРАЦИЙ
# ==============================================================================
def load_env(env_path: Path) -> Dict[str, str]:
    """Загружает .env в словарь (BOM-safe)."""
    if not env_path.exists():
        log_error(f"Файл .env не найден: {env_path}")
        sys.exit(1)

    env_vars: Dict[str, str] = {}
    pattern = re.compile(r"^([A-Z0-9_]+)=(.*)$")

    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        match = pattern.match(line)
        if match:
            value = match.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            env_vars[match.group(1)] = value

    return env_vars


def substitute_vars(template: str, env_vars: Dict[str, str]) -> str:
    """Подстановка ${VAR} в шаблон."""
    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        return env_vars.get(var_name, match.group(0))

    return re.sub(r"\$\{([A-Z0-9_]+)\}", replacer, template)


def clean_json_keys(obj):
    """Рекурсивно удаляет ключи, начинающиеся с '_'."""
    if isinstance(obj, dict):
        return {k: clean_json_keys(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [clean_json_keys(item) for item in obj]
    return obj


def render_file(
    src_path: Path,
    dst_path: Path,
    env_vars: Dict[str, str],
    clean_underscore_keys: bool = False,
    s3_iam_only: bool = False,
) -> bool:
    """Рендеринг одного файла."""
    if not src_path.exists():
        log_error(f"Исходный файл не найден: {src_path}")
        return False

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    content = src_path.read_text(encoding="utf-8-sig")
    rendered = substitute_vars(content, env_vars)

    if src_path.suffix == ".json" and (clean_underscore_keys or s3_iam_only):
        try:
            data = json.loads(rendered)
            cleaned = clean_json_keys(data)

            if s3_iam_only:
                identities = []
                for identity in cleaned.get("identities", []):
                    rendered_identity = {
                        key: identity[key] for key in ("name", "credentials") if key in identity
                    }
                    actions = set()
                    for bucket_rule in identity.get("buckets", []):
                        bucket = bucket_rule.get("name")
                        if bucket:
                            actions.update(
                                f"{action}:{bucket}" for action in bucket_rule.get("actions", [])
                            )
                    if actions:
                        rendered_identity["actions"] = sorted(actions)
                    identities.append(rendered_identity)
                cleaned = {"identities": identities}
                rendered = json.dumps(cleaned, ensure_ascii=False, indent=2)

            json.loads(rendered)  # валидация итога
        except json.JSONDecodeError as e:
            log_error(f"Ошибка JSON в {src_path}: {e}")
            return False

    dst_path.write_text(rendered, encoding="utf-8", newline="\n")
    return True


# ==============================================================================
# ИНИЦИАЛИЗАЦИЯ ВМ
# ==============================================================================
def init_vm1(rotate: bool = False) -> Dict[str, str]:
    """Инициализация ВМ №1; возвращает общие ключи для синхронизации ВМ №2."""
    log_step("Инициализация ВМ №1 (периметр, observability)")

    if not VM1_DIR.exists():
        log_error(f"Директория ВМ №1 не найдена: {VM1_DIR}")
        sys.exit(1)

    env_path = VM1_DIR / ".env"
    if not env_path.exists():
        log_error(f"Файл .env не найден: {env_path}")
        log_error("Скопируйте .env.example в .env и повторите.")
        sys.exit(1)

    existing = load_env(env_path)
    generated = build_vm1_secrets(existing, rotate=rotate)

    new_secrets = dict(existing)
    new_secrets.update(generated)

    # S3-эндпоинт: имя контейнера совпадает с SAN сертификата SeaweedFS.
    # Литерал ${VM2_HOST_IP} внутри значения .env Compose не раскрывает.
    new_secrets["LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT"] = (
        configured_value(existing, "LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT")
        or "https://seaweedfs-s3:8333"
    )

    new_secrets.update(urlencode_pairs(new_secrets))

    replaced = update_env_file(env_path, new_secrets)
    if rotate:
        log_info(f"Ротация секретов ВМ №1 выполнена; обновлено {replaced} значений")
    else:
        log_info(f"Установленные пароли сохранены; инициализировано/синхронизировано {replaced} значений")

    # Рендеринг конфигов
    log_info("Рендеринг конфигураций ВМ №1...")
    env_vars = load_env(env_path)

    required = ["OAUTH2_PROXY_CLIENT_SECRET"]
    missing = [v for v in required if not env_vars.get(v)]
    if missing:
        log_error(f"Отсутствуют переменные в vm1/.env: {', '.join(missing)}")
        sys.exit(1)

    rendered_dir = VM1_DIR / "rendered"
    rendered_dir.mkdir(parents=True, exist_ok=True)

    success_count = 0
    fail_count = 0
    for src_rel, dst_rel in VM1_RENDER_FILES:
        src_path = VM1_DIR / src_rel
        dst_path = VM1_DIR / dst_rel
        if render_file(src_path, dst_path, env_vars, clean_underscore_keys=True):
            log_info(f"  ✓ {src_rel} → {dst_rel}")
            success_count += 1
        else:
            fail_count += 1

    if fail_count > 0:
        log_error(f"Не удалось отрендерить {fail_count} файлов")
        sys.exit(1)

    log_info(f"ВМ №1 готова: {success_count} файлов отрендерено")

    shared_keys = {
        key: env_vars[key]
        for key in ("GATEWAY_DB_PASSWORD", "REDIS_FACTORY_ACL_PASSWORD", "S3_ACCESS_KEY", "S3_SECRET_KEY")
        if env_vars.get(key)
    }
    for key in LANGFUSE_UI_KEYS:
        value = env_vars.get(key, "")
        if not is_placeholder(value):
            shared_keys[key] = value

    return shared_keys


def init_vm2(shared: Dict[str, str], rotate: bool = False) -> None:
    """Инициализация ВМ №2 с синхронизацией общих секретов из ВМ №1."""
    log_step("Инициализация ВМ №2 (вычисления, песочницы)")

    if not VM2_DIR.exists():
        log_error(f"Директория ВМ №2 не найдена: {VM2_DIR}")
        sys.exit(1)

    env_path = VM2_DIR / ".env"
    if not env_path.exists():
        log_error(f"Файл .env не найден: {env_path}")
        log_error("Скопируйте .env.example в .env и повторите.")
        sys.exit(1)

    existing = load_env(env_path)
    generated = build_vm2_secrets(shared, existing, rotate=rotate)

    new_secrets = dict(existing)
    new_secrets.update(generated)

    for key in ("GATEWAY_DB_PASSWORD", "REDIS_FACTORY_ACL_PASSWORD", "S3_ACCESS_KEY", "S3_SECRET_KEY") + LANGFUSE_UI_KEYS:
        if shared.get(key):
            new_secrets[key] = shared[key]

    # Информационные URL собираются из уже разрешённых значений,
    # чтобы в .env не оставались нераскрываемые литералы ${...}.
    worker_db_user = new_secrets.get("WORKER_DB_USER") or "factory_worker"
    new_secrets["WORKER_POSTGRES_URL"] = (
        f"postgresql://{worker_db_user}:{new_secrets['WORKER_DB_PASSWORD']}"
        f"@vm2-postgres:5432/factory"
    )
    new_secrets["WORKER_REDIS_URL"] = (
        f"rediss://agui_svc:{new_secrets['REDIS_FACTORY_ACL_PASSWORD']}"
        f"@redis-factory:6379/0?ssl_ca_certs=/etc/certs/corp-ca.crt"
    )
    new_secrets["LANGFUSE_HOST"] = (
        configured_value(existing, "LANGFUSE_HOST") or "https://langfuse-ingest.corp.example.com"
    )
    new_secrets["SEAWEED_S3_ENDPOINT"] = (
        configured_value(existing, "SEAWEED_S3_ENDPOINT") or "https://seaweedfs-s3:8333"
    )
    new_secrets["POSTGRES_BACKUP_S3_ENDPOINT"] = (
        configured_value(existing, "POSTGRES_BACKUP_S3_ENDPOINT") or "https://seaweedfs-s3:8333"
    )
    new_secrets["PYPI_MIRROR_URL"] = (
        configured_value(existing, "PYPI_MIRROR_URL") or "http://pypiserver:8080"
    )
    new_secrets.setdefault("SANDBOX_DOCKER_SOCKET", "/run/user/1000/docker.sock")

    new_secrets.update(urlencode_pairs(new_secrets))

    replaced = update_env_file(env_path, new_secrets)
    log_info(f"Секреты ВМ №2 обработаны; обновлено {replaced} значений")

    synced = [k for k in shared.keys() if k in new_secrets and shared[k] == new_secrets[k]]
    if synced:
        log_info(f"  🔗 Синхронизировано из ВМ №1: {', '.join(sorted(synced))}")

    # Рендеринг конфигов
    log_info("Рендеринг конфигураций ВМ №2...")
    env_vars = load_env(env_path)

    required = [
        "REDIS_FACTORY_PASSWORD",
        "REDIS_FACTORY_ACL_PASSWORD",
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
        "SEAWEEDFS_FILER_SIGNING_KEY",
    ]
    missing = [v for v in required if is_placeholder(env_vars.get(v, ""))]
    if missing:
        log_error(f"Отсутствуют переменные в vm2/.env: {', '.join(missing)}")
        sys.exit(1)

    rendered_dir = VM2_DIR / "rendered"
    rendered_dir.mkdir(parents=True, exist_ok=True)

    success_count = 0
    fail_count = 0
    for src_rel, dst_rel in VM2_RENDER_FILES:
        src_path = VM2_DIR / src_rel
        dst_path = VM2_DIR / dst_rel
        if render_file(
            src_path, dst_path, env_vars,
            clean_underscore_keys=False,
            s3_iam_only=(src_path.name == "s3.json"),
        ):
            log_info(f"  ✓ {src_rel} → {dst_rel}")
            success_count += 1
        else:
            fail_count += 1

    if fail_count > 0:
        log_error(f"Не удалось отрендерить {fail_count} файлов")
        sys.exit(1)

    log_info(f"ВМ №2 готова: {success_count} файлов отрендерено")

    # Проверка сокета песочниц
    sandbox_socket = env_vars.get("SANDBOX_DOCKER_SOCKET", "").strip()
    if sandbox_socket and not sandbox_socket.startswith("/"):
        log_error("SANDBOX_DOCKER_SOCKET must be an absolute host path")
        sys.exit(1)

    if sandbox_socket:
        if sys.platform.startswith("linux") and not Path(sandbox_socket).exists():
            log_warn("Sandbox socket отсутствует на хосте; поднимите выделенный rootless dockerd перед использованием песочниц")
        else:
            log_info("Sandbox socket настроен; проверьте, что он принадлежит выделенному rootless dockerd ВМ №2")
    else:
        log_warn("SANDBOX_DOCKER_SOCKET не задан; создание песочниц недоступно")

    # GPU: адрес должен резолвиться, маршрут не выдумывается
    gpu_endpoint = env_vars.get("GPU_ENDPOINT_URL", "").strip()
    gpu_node_ip = env_vars.get("GPU_NODE_IP", "").strip()

    if gpu_node_ip:
        try:
            import ipaddress
            ipaddress.ip_address(gpu_node_ip)
            hosts_file = VM2_DIR / "rendered" / "gpu-extra-hosts.txt"
            hosts_file.write_text(
                f"{gpu_node_ip} gpu-node.corp.example.com\n", encoding="utf-8", newline="\n"
            )
            log_info("GPU endpoint override провалидирован, mapping отрендерен")
        except ValueError:
            log_error("GPU_NODE_IP must be a valid address; refusing to invent a GPU route")
            sys.exit(1)
    elif gpu_endpoint:
        from urllib.parse import urlparse
        gpu_host = urlparse(gpu_endpoint).hostname
        if gpu_host:
            try:
                import socket
                socket.getaddrinfo(gpu_host, None)
                log_info("GPU endpoint hostname резолвится через DNS хоста")
            except OSError:
                log_warn(f"GPU endpoint host {gpu_host} здесь не резолвится; маршрут должен обеспечить GPU-узел/сеть")


# ==============================================================================
# MAIN
# ==============================================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="Инициализация проекта «Фабрика агентов и алгоритмов»")
    parser.add_argument(
        "--rotate",
        action="store_true",
        help="принудительно перегенерировать все секреты (тома БД/ClickHouse потребуется пересоздать)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print(f"{C.BOLD}🔧 Инициализация проекта «Фабрика агентов и алгоритмов»{C.NC}")
    print("=" * 60)
    print(f"  Корень проекта: {BASE_DIR}")
    print(f"  ВМ №1: {VM1_DIR}")
    print(f"  ВМ №2: {VM2_DIR}")
    print(f"  Режим: {'РОТАЦИЯ (--rotate)' if args.rotate else 'инициализация (сохранение установленных паролей)'}")

    if not VM1_DIR.exists() or not VM2_DIR.exists():
        log_error("Структура проекта нарушена: отсутствуют директории vm1/ или vm2/")
        return 1

    try:
        shared = init_vm1(rotate=args.rotate)
        init_vm2(shared, rotate=args.rotate)

        network = subprocess.run(
            ["docker", "network", "inspect", "factory-cross-vm"],
            capture_output=True, text=True,
        )
        if network.returncode != 0:
            subprocess.run(["docker", "network", "create", "factory-cross-vm"], check=True)
            log_info("Shared cross-VM Docker network created")

        print()
        print("=" * 60)
        print(f"{C.GREEN}{C.BOLD}✅ Инициализация проекта завершена успешно{C.NC}")
        print("=" * 60)
        print()

        if args.rotate:
            print(f"{C.YELLOW}{C.BOLD}⚠️  РОТАЦИЯ ВЫПОЛНЕНА — ТОМА НУЖНО ПЕРЕСОЗДАТЬ{C.NC}")
            print("  Пароли ролей PostgreSQL и пользователя ClickHouse применяются")
            print("  init-скриптами только при ПУСТОМ томе данных. Без пересоздания")
            print("  сервисы получат отказ аутентификации (Prisma P1000 / SCRAM failed).")
            print()
            print("  docker compose -f vm1/docker-compose.yml down")
            print("  docker compose -f vm2/docker-compose.yml down")
            print("  docker volume rm factory-vm1_postgres-auth-data factory-vm1_clickhouse-data factory-vm1_redis-langfuse-data")
            print("  docker volume rm factory-vm2_postgres-runtime-data factory-vm2_redis-factory-data")
            print("  docker compose -f vm2/docker-compose.yml up -d")
            print("  docker compose -f vm1/docker-compose.yml up -d")
            print()

        print("Следующие шаги:")
        print("  1. Проверьте vm1/.env и vm2/.env (бэкапы .env.bak удалите после проверки)")
        print("  2. Проверьте vm1/rendered/ и vm2/rendered/")
        print("  3. Разместите сертификаты в certs/ (или сгенерируйте dev-сертификаты)")
        print("  4. Запустите стек:")
        print("       docker compose -f vm2/docker-compose.yml up -d")
        print("       docker compose -f vm1/docker-compose.yml up -d")
        print()
        print(f"{C.YELLOW}⚠️  ВАЖНО:{C.NC}")
        print("  • LANGFUSE_PUBLIC_KEY и LANGFUSE_SECRET_KEY создаются в Langfuse UI")
        print("    после первого запуска. Скрипт их НЕ генерирует. Впишите реальные")
        print("    ключи в vm1/.env и повторите init_project.py для синхронизации с ВМ №2.")
        print("  • OAUTH2_PROXY_CLIENT_SECRET и AUTH_CUSTOM_CLIENT_SECRET должны совпадать")
        print("    с секретами клиентов в Keycloak realm-export.json (рендерится автоматически).")
        print()
        return 0

    except KeyboardInterrupt:
        print(f"\n{C.RED}❌ Прервано пользователем{C.NC}")
        return 130
    except Exception as e:
        log_error(f"Непредвиденная ошибка: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())