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
    python init_project.py

Пути (статичные):
    D:\\agent-factory\\vm1  — периметр, шлюзы, observability
    D:\\agent-factory\\vm2  — вычисления, песочницы, данные агентов
"""

import base64
import json
import re
import secrets
import shutil
import sys
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ==============================================================================
# СТАТИЧНЫЕ ПУТИ
# ==============================================================================
BASE_DIR = Path(__file__).parent.resolve()
VM1_DIR = BASE_DIR / "vm1"
VM2_DIR = BASE_DIR / "vm2"

# Алфавиты для генерации секретов
ALPHABET_32_PASS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_+=-"
ALPHABET_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
ALPHABET_UPPER_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
ALPHABET_ACL = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

# Файлы для рендеринга
VM1_RENDER_FILES = [
    ("keycloak/realm-export.json", "rendered/realm-export.json"),
]

VM2_RENDER_FILES = [
    ("redis/redis.conf", "rendered/redis/redis.conf"),
    ("redis/acl.conf", "rendered/redis/acl.conf"),
    ("seaweedfs/s3.json", "rendered/seaweedfs/s3.json"),
]

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
    return "".join(secrets.choice(ALPHABET_32_PASS) for _ in range(32))


def gen_acl_password(length: int = 32) -> str:
    return "".join(secrets.choice(ALPHABET_ACL) for _ in range(length))


def gen_alnum(length: int, upper_only: bool = False) -> str:
    alphabet = ALPHABET_UPPER_ALNUM if upper_only else ALPHABET_ALNUM
    return "".join(secrets.choice(alphabet) for _ in range(length))


def gen_base64(num_bytes: int) -> str:
    return base64.b64encode(secrets.token_bytes(num_bytes)).decode("ascii")


def gen_hex(num_bytes: int) -> str:
    return secrets.token_hex(num_bytes)


def build_vm1_secrets() -> Dict[str, str]:
    """Полный набор секретов для ВМ №1."""
    new_secrets = {
        # PostgreSQL Auth & Observability
        "POSTGRES_AUTH_PASSWORD": gen_32_pass(),
        # Keycloak
        "KEYCLOAK_ADMIN_PASSWORD": gen_32_pass(),
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
        # Общие ключи (синхронизируются с ВМ №2)
        "REDIS_FACTORY_ACL_PASSWORD": gen_acl_password(),
        "GATEWAY_DB_PASSWORD": gen_32_pass(),
        "S3_ACCESS_KEY": gen_alnum(20, upper_only=True),
        "S3_SECRET_KEY": gen_alnum(40),
    }

    # URL-encoded пары для connection strings
    urlencoded = {
        "CLICKHOUSE_PASSWORD_URLENCODED": "CLICKHOUSE_PASSWORD",
        "REDIS_LANGFUSE_ACL_PASSWORD_URLENCODED": "REDIS_LANGFUSE_ACL_PASSWORD",
        "LANGFUSE_DB_PASSWORD_URLENCODED": "LANGFUSE_DB_PASSWORD",
        "REDIS_FACTORY_ACL_PASSWORD_URLENCODED": "REDIS_FACTORY_ACL_PASSWORD",
        "GATEWAY_DB_PASSWORD_URLENCODED": "GATEWAY_DB_PASSWORD",
    }
    for enc_key, src_key in urlencoded.items():
        new_secrets[enc_key] = urllib.parse.quote(new_secrets[src_key], safe="")

    return new_secrets


def build_vm2_secrets(shared: Dict[str, str]) -> Dict[str, str]:
    """
    Набор секретов для ВМ №2.
    Общие ключи берутся из ВМ №1 (передаются параметром shared).
    """
    new_secrets = {
        # Локальные ключи ВМ №2
        "POSTGRES_RUNTIME_PASSWORD": gen_32_pass(),
        "FACTORY_DB_PASSWORD": gen_32_pass(),
        "WORKER_DB_PASSWORD": gen_32_pass(),
        "REDIS_FACTORY_PASSWORD": gen_acl_password(),
        "CORP_PG_PASSWORD": gen_32_pass(),
    }

    # Общие ключи: из ВМ №1 или генерируем заново (если ВМ №1 не инициализирована)
    new_secrets["GATEWAY_DB_PASSWORD"] = shared.get("GATEWAY_DB_PASSWORD", gen_32_pass())
    new_secrets["REDIS_FACTORY_ACL_PASSWORD"] = shared.get(
        "REDIS_FACTORY_ACL_PASSWORD", gen_acl_password()
    )
    new_secrets["S3_ACCESS_KEY"] = shared.get("S3_ACCESS_KEY", gen_alnum(20, upper_only=True))
    new_secrets["S3_SECRET_KEY"] = shared.get("S3_SECRET_KEY", gen_alnum(40))
    new_secrets["LANGFUSE_PUBLIC_KEY"] = shared.get(
        "LANGFUSE_PUBLIC_KEY", f"pk-lf-{gen_alnum(16)}"
    )
    new_secrets["LANGFUSE_SECRET_KEY"] = shared.get(
        "LANGFUSE_SECRET_KEY", f"sk-lf-{gen_alnum(48)}"
    )

    return new_secrets


def update_env_file(env_path: Path, new_secrets: Dict[str, str]) -> int:
    """Обновляет .env, заменяя значения известных ключей. Возвращает число замен."""
    if not env_path.exists():
        log_error(f"Файл не найден: {env_path}")
        return 0

    # Резервная копия
    backup_path = env_path.with_suffix(".env.bak")
    shutil.copy2(env_path, backup_path)

    original_content = env_path.read_text(encoding="utf-8")
    pattern = re.compile(r"^([A-Z0-9_]+)=(.*)$")

    updated_lines = []
    replaced_count = 0

    for line in original_content.splitlines():
        match = pattern.match(line.strip())
        if match:
            key = match.group(1)
            if key in new_secrets:
                updated_lines.append(f"{key}='{new_secrets[key]}'")
                replaced_count += 1
                continue
        updated_lines.append(line)

    env_path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
    return replaced_count


# ==============================================================================
# РЕНДЕРИНГ КОНФИГУРАЦИЙ
# ==============================================================================
def load_env(env_path: Path) -> Dict[str, str]:
    """Загружает .env в словарь."""
    if not env_path.exists():
        log_error(f"Файл .env не найден: {env_path}")
        sys.exit(1)

    env_vars: Dict[str, str] = {}
    pattern = re.compile(r"^([A-Z0-9_]+)='?([^']*)'?\s*$")

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = pattern.match(line)
        if match:
            env_vars[match.group(1)] = match.group(2)

    return env_vars


def substitute_vars(template: str, env_vars: Dict[str, str]) -> str:
    """Подстановка ${VAR} в шаблоне."""
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
) -> bool:
    """Рендеринг одного файла."""
    if not src_path.exists():
        log_error(f"Исходный файл не найден: {src_path}")
        return False

    dst_path.parent.mkdir(parents=True, exist_ok=True)

    content = src_path.read_text(encoding="utf-8")
    rendered = substitute_vars(content, env_vars)

    if src_path.suffix == ".json" and clean_underscore_keys:
        try:
            data = json.loads(rendered)
            cleaned = clean_json_keys(data)
            rendered = json.dumps(cleaned, ensure_ascii=False, indent=2)
            json.loads(rendered)  # валидация
        except json.JSONDecodeError as e:
            log_error(f"Ошибка JSON в {src_path}: {e}")
            return False

    dst_path.write_text(rendered, encoding="utf-8")
    return True


# ==============================================================================
# ИНИЦИАЛИЗАЦИЯ ВМ
# ==============================================================================
def init_vm1() -> Dict[str, str]:
    """Инициализация ВМ №1. Возвращает словарь общих секретов для синхронизации с ВМ №2."""
    log_step("Инициализация ВМ №1 (периметр, observability)")

    if not VM1_DIR.exists():
        log_error(f"Директория ВМ №1 не найдена: {VM1_DIR}")
        sys.exit(1)

    env_path = VM1_DIR / ".env"
    if not env_path.exists():
        log_error(f"Файл .env не найден: {env_path}")
        log_error("Скопируйте .env.example в .env и повторите.")
        sys.exit(1)

    # 1. Генерация секретов
    log_info("Генерация секретов ВМ №1...")
    new_secrets = build_vm1_secrets()
    replaced = update_env_file(env_path, new_secrets)
    log_info(f"Обновлено секретов в vm1/.env: {replaced}")

    # 2. Рендеринг конфигов
    log_info("Рендеринг конфигураций ВМ №1...")
    env_vars = load_env(env_path)

    # Проверка обязательных переменных
    required = ["OAUTH2_PROXY_CLIENT_SECRET"]
    missing = [v for v in required if not env_vars.get(v)]
    if missing:
        log_error(f"Отсутствуют переменные в vm1/.env: {', '.join(missing)}")
        sys.exit(1)

    # Очистка старой rendered/
    rendered_dir = VM1_DIR / "rendered"
    if rendered_dir.exists():
        shutil.rmtree(rendered_dir)
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

    # Возвращаем общие секреты для синхронизации с ВМ №2
    shared_keys = {
        "GATEWAY_DB_PASSWORD": new_secrets["GATEWAY_DB_PASSWORD"],
        "REDIS_FACTORY_ACL_PASSWORD": new_secrets["REDIS_FACTORY_ACL_PASSWORD"],
        "S3_ACCESS_KEY": new_secrets["S3_ACCESS_KEY"],
        "S3_SECRET_KEY": new_secrets["S3_SECRET_KEY"],
    }
    # LANGFUSE_* ключи могут быть ещё не заданы (создаются в UI), но если есть — берём
    if env_vars.get("LANGFUSE_PUBLIC_KEY") and not env_vars["LANGFUSE_PUBLIC_KEY"].startswith("pk-lf-..."):
        shared_keys["LANGFUSE_PUBLIC_KEY"] = env_vars["LANGFUSE_PUBLIC_KEY"]
    if env_vars.get("LANGFUSE_SECRET_KEY") and not env_vars["LANGFUSE_SECRET_KEY"].startswith("sk-lf-..."):
        shared_keys["LANGFUSE_SECRET_KEY"] = env_vars["LANGFUSE_SECRET_KEY"]

    return shared_keys


def init_vm2(shared: Dict[str, str]) -> None:
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

    # 1. Генерация секретов с синхронизацией из ВМ №1
    log_info("Генерация секретов ВМ №2 (с синхронизацией общих ключей из ВМ №1)...")
    new_secrets = build_vm2_secrets(shared)
    replaced = update_env_file(env_path, new_secrets)
    log_info(f"Обновлено секретов в vm2/.env: {replaced}")

    synced = [k for k in shared.keys() if k in new_secrets and shared[k] == new_secrets[k]]
    if synced:
        log_info(f"  🔗 Синхронизировано из ВМ №1: {', '.join(sorted(synced))}")

    # 2. Рендеринг конфигов
    log_info("Рендеринг конфигураций ВМ №2...")
    env_vars = load_env(env_path)

    required = [
        "REDIS_FACTORY_PASSWORD",
        "REDIS_FACTORY_ACL_PASSWORD",
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
    ]
    missing = [v for v in required if not env_vars.get(v)]
    if missing:
        log_error(f"Отсутствуют переменные в vm2/.env: {', '.join(missing)}")
        sys.exit(1)

    rendered_dir = VM2_DIR / "rendered"
    if rendered_dir.exists():
        shutil.rmtree(rendered_dir)
    rendered_dir.mkdir(parents=True, exist_ok=True)

    success_count = 0
    fail_count = 0
    for src_rel, dst_rel in VM2_RENDER_FILES:
        src_path = VM2_DIR / src_rel
        dst_path = VM2_DIR / dst_rel
        if render_file(src_path, dst_path, env_vars, clean_underscore_keys=False):
            log_info(f"  ✓ {src_rel} → {dst_rel}")
            success_count += 1
        else:
            fail_count += 1

    if fail_count > 0:
        log_error(f"Не удалось отрендерить {fail_count} файлов")
        sys.exit(1)

    log_info(f"ВМ №2 готова: {success_count} файлов отрендерено")


# ==============================================================================
# MAIN
# ==============================================================================
def main() -> int:
    print("=" * 60)
    print(f"{C.BOLD}🔧 Инициализация проекта «Фабрика агентов и алгоритмов»{C.NC}")
    print("=" * 60)
    print(f"  Корень проекта: {BASE_DIR}")
    print(f"  ВМ №1: {VM1_DIR}")
    print(f"  ВМ №2: {VM2_DIR}")

    if not VM1_DIR.exists() or not VM2_DIR.exists():
        log_error("Структура проекта нарушена: отсутствуют директории vm1/ или vm2/")
        return 1

    try:
        # Инициализация ВМ №1 (возвращает общие секреты)
        shared = init_vm1()

        # Инициализация ВМ №2 (синхронизирует общие секреты)
        init_vm2(shared)

        print()
        print("=" * 60)
        print(f"{C.GREEN}{C.BOLD}✅ Инициализация проекта завершена успешно{C.NC}")
        print("=" * 60)
        print()
        print("Следующие шаги:")
        print("  1. Проверьте vm1/.env и vm2/.env (удалите .env.bak после проверки)")
        print("  2. Проверьте vm1/rendered/ и vm2/rendered/")
        print("  3. Разместите сертификаты в certs/ (или сгенерируйте dev-сертификаты)")
        print("  4. Запустите стек:")
        print("       cd vm1 && docker compose up -d")
        print("       cd vm2 && docker compose up -d")
        print()
        print(f"{C.YELLOW}⚠️  ВАЖНО:{C.NC}")
        print("  • LANGFUSE_PUBLIC_KEY и LANGFUSE_SECRET_KEY создаются в Langfuse UI")
        print("    после первого запуска. Замените плейсхолдеры на реальные ключи")
        print("    и перезапустите init_project.py для синхронизации с ВМ №2.")
        print("  • OAUTH2_PROXY_CLIENT_SECRET должен совпадать с секретом клиента")
        print("    в Keycloak realm-export.json.")
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