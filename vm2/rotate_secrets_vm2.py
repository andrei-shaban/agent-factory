#!/usr/bin/env python3
"""
Скрипт генерации секретов для ВМ №2 (Фабрика агентов и алгоритмов).
Аналогичен rotate_secrets.py для ВМ №1.

Особенность: общие ключи (GATEWAY_DB_PASSWORD, REDIS_FACTORY_ACL_PASSWORD,
S3_ACCESS_KEY, S3_SECRET_KEY, LANGFUSE_*) автоматически читаются из
../vm1/.env, если он существует, чтобы обеспечить синхронность ВМ №1 ↔ ВМ №2.

Использование:
    python rotate_secrets_vm2.py                  # использовать ../vm1/.env
    python rotate_secrets_vm2.py --vm1-env /path/to/vm1/.env
    python rotate_secrets_vm2.py --no-sync        # генерировать ВСЕ ключи заново
"""

import re
import secrets
import base64
import argparse
import shutil
from pathlib import Path

# ==============================================================================
# ПУТИ
# ==============================================================================
INPUT_FILE = Path(".env")
BACKUP_FILE = Path(".env.bak")
DEFAULT_VM1_ENV = Path("..") / "vm1" / ".env"

# ==============================================================================
# АЛФАВИТЫ
# ==============================================================================
ALPHABET_32_PASS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_+=-"
ALPHABET_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
ALPHABET_UPPER_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
# Пароль для ACL Redis: только буквы и цифры, без спецсимволов,
# чтобы не было проблем в URL `rediss://agui_svc:<pass>@host:port`
ALPHABET_ACL = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def gen_32_pass() -> str:
    """Генерирует 32-символьный пароль со спецсимволами."""
    return "".join(secrets.choice(ALPHABET_32_PASS) for _ in range(32))


def gen_acl_password(length: int = 32) -> str:
    """Генерирует пароль для ACL Redis (только буквы и цифры)."""
    return "".join(secrets.choice(ALPHABET_ACL) for _ in range(length))


def gen_alnum(length: int, upper_only: bool = False) -> str:
    """Генерирует буквенно-цифровой ключ заданной длины."""
    alphabet = ALPHABET_UPPER_ALNUM if upper_only else ALPHABET_ALNUM
    return "".join(secrets.choice(alphabet) for _ in range(length))


def gen_base64(num_bytes: int) -> str:
    """Аналог openssl rand -base64 <num_bytes>."""
    return base64.b64encode(secrets.token_bytes(num_bytes)).decode("ascii")


def read_vm1_secrets(vm1_env_path: Path) -> dict:
    """
    Читает общие секреты из .env ВМ №1.
    Возвращает словарь {ключ: значение} для ключей, которые должны совпадать.
    """
    shared_keys = {
        "GATEWAY_DB_PASSWORD",
        "REDIS_FACTORY_ACL_PASSWORD",
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
    }

    result = {}
    if not vm1_env_path.exists():
        print(f"  ⚠️  Файл {vm1_env_path} не найден — общие ключи будут сгенерированы заново.")
        print(f"     ВАЖНО: они должны совпадать с ВМ №1 для работы межсерверных маршрутов!")
        return result

    try:
        content = vm1_env_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"  ⚠️  Не удалось прочитать {vm1_env_path}: {e}")
        return result

    pattern = re.compile(r"^([A-Z0-9_]+)='?([^']*)'?\s*$")
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = pattern.match(line)
        if match:
            key, value = match.group(1), match.group(2)
            if key in shared_keys and value and not value.startswith("<"):
                result[key] = value

    found = [k for k in shared_keys if k in result]
    missing = shared_keys - set(found)
    if found:
        print(f"  ✅ Прочитано из ВМ №1: {', '.join(sorted(found))}")
    if missing:
        print(f"  ⚠️  Отсутствуют в ВМ №1: {', '.join(sorted(missing))}")

    return result


def rotate_env_file(no_sync: bool = False, vm1_env: str | None = None):
    if not INPUT_FILE.exists():
        print(f"Ошибка: Файл {INPUT_FILE} не найден.")
        print("Убедитесь, что запускаете скрипт из директории ВМ №2 (там, где лежит .env).")
        return

    # -------------------------------------------------------------------------
    # 1. Общие ключи: читаем из ВМ №1 или генерируем
    # -------------------------------------------------------------------------
    vm1_path = Path(vm1_env) if vm1_env else DEFAULT_VM1_ENV
    shared = {} if no_sync else read_vm1_secrets(vm1_path)

    # -------------------------------------------------------------------------
    # 2. Генерируем полный набор секретов для ВМ №2
    # -------------------------------------------------------------------------
    new_secrets = {
        # === Локальные ключи ВМ №2 (генерируются всегда) ===
        # Суперпользователь контейнера PostgreSQL
        "POSTGRES_RUNTIME_PASSWORD": gen_32_pass(),
        # Владелец БД factory
        "FACTORY_DB_PASSWORD": gen_32_pass(),
        # Пользователь Worker (LangGraph, чекпоинты)
        "WORKER_DB_PASSWORD": gen_32_pass(),
        # Пароль default user в Redis фабрики
        "REDIS_FACTORY_PASSWORD": gen_acl_password(),
        # Учётная запись для чтения мастер-данных (ВМ №4)
        "CORP_PG_PASSWORD": gen_32_pass(),
    }

    # === Общие ключи: из ВМ №1 или генерируем заново ===
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

    # -------------------------------------------------------------------------
    # 3. Читаем исходный файл и делаем резервную копию
    # -------------------------------------------------------------------------
    original_content = INPUT_FILE.read_text(encoding="utf-8")
    BACKUP_FILE.write_text(original_content, encoding="utf-8")

    # -------------------------------------------------------------------------
    # 4. Замена значений построчно
    # -------------------------------------------------------------------------
    updated_lines = []
    replaced_count = 0
    synced_from_vm1 = []

    # Регулярное выражение для поиска строк вида KEY='value' или KEY=value
    pattern = re.compile(r"^([A-Z0-9_]+)=(.*)$")

    for line in original_content.splitlines():
        match = pattern.match(line.strip())
        if match:
            key = match.group(1)
            if key in new_secrets:
                # Сохраняем формат с одинарными кавычками, как в оригинале
                updated_lines.append(f"{key}='{new_secrets[key]}'")
                replaced_count += 1
                if key in shared:
                    synced_from_vm1.append(key)
                continue
        updated_lines.append(line)

    # -------------------------------------------------------------------------
    # 5. Записываем обновлённый файл
    # -------------------------------------------------------------------------
    INPUT_FILE.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")

    print()
    print(f"  📝 Файл: {INPUT_FILE.resolve()}")
    print(f"  🔄 Обновлено секретов: {replaced_count}")
    print(f"  💾 Старая версия сохранена в: {BACKUP_FILE.resolve()}")
    print()

    if synced_from_vm1:
        print(f"  🔗 Синхронизировано из ВМ №1 ({len(synced_from_vm1)}):")
        for k in sorted(synced_from_vm1):
            print(f"     • {k}")
        print()

    gen_only = set(new_secrets.keys()) - set(synced_from_vm1)
    if gen_only:
        print(f"  🎲 Сгенерировано заново ({len(gen_only)}):")
        for k in sorted(gen_only):
            print(f"     • {k}")
        print()

    print("  ⚠️  ВАЖНО:")
    print("     1. Удалите .env.bak после применения изменений в инфраструктуре.")
    print("     2. Ключи, помеченные как общие, должны совпадать с ВМ №1.")
    print("     3. LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY создаются в Langfuse UI")
    print("        после первого запуска. Если вы уже получили реальные ключи из")
    print("        Langfuse UI — замените сгенерированные значения вручную.")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Генерация секретов для ВМ №2")
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Не читать общие ключи из ВМ №1, генерировать всё заново",
    )
    parser.add_argument(
        "--vm1-env",
        type=str,
        default=None,
        help="Путь к .env файла ВМ №1 (по умолчанию: ../vm1/.env)",
    )
    args = parser.parse_args()

    print("🔑 Генерация секретов для ВМ №2 (Фабрика агентов и алгоритмов)")
    print("=" * 60)
    print()
    rotate_env_file(no_sync=args.no_sync, vm1_env=args.vm1_env)