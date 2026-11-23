import os
import re
import secrets
import base64
import urllib.parse
from pathlib import Path

# Путь к вашему файлу (по умолчанию ищет .env или .env.example)
INPUT_FILE = Path(".env")
BACKUP_FILE = Path(".env.bak")

# Алфавиты для генерации
ALPHABET_32_PASS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_+=-"
ALPHABET_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
ALPHABET_UPPER_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

def gen_32_pass() -> str:
    """Генерирует 32-символьный пароль со спецсимволами (+, =, _, -)."""
    return "".join(secrets.choice(ALPHABET_32_PASS) for _ in range(32))

def gen_alnum(length: int, upper_only: bool = False) -> str:
    """Генерирует буквенно-цифровой ключ заданной длины."""
    alphabet = ALPHABET_UPPER_ALNUM if upper_only else ALPHABET_ALNUM
    return "".join(secrets.choice(alphabet) for _ in range(length))

def gen_base64(num_bytes: int) -> str:
    """Аналог openssl rand -base64 <num_bytes>."""
    return base64.b64encode(secrets.token_bytes(num_bytes)).decode("ascii")

def rotate_env_file():
    if not INPUT_FILE.exists():
        print(f"Ошибка: Файл {INPUT_FILE} не найден. Укажите правильный путь в INPUT_FILE.")
        return

    # 1. Генерируем новый набор согласованных секретов
    new_secrets = {
        # Стандартные 32-символьные пароли
        "POSTGRES_AUTH_PASSWORD": gen_32_pass(),
        "KEYCLOAK_ADMIN_PASSWORD": gen_32_pass(),
        "KEYCLOAK_DB_PASSWORD": gen_32_pass(),
        "OAUTH2_PROXY_CLIENT_SECRET": gen_32_pass(),
        "REDIS_LANGFUSE_PASSWORD": gen_32_pass(),
        "REDIS_LANGFUSE_ACL_PASSWORD": gen_32_pass(),
        "CLICKHOUSE_PASSWORD": gen_32_pass(),
        "LANGFUSE_DB_PASSWORD": gen_32_pass(),
        "REDIS_FACTORY_ACL_PASSWORD": gen_32_pass(),
        "GATEWAY_DB_PASSWORD": gen_32_pass(),

        # Криптографические соли и ключи сессий
        "LANGFUSE_NEXTAUTH_SECRET": gen_base64(64),
        "LANGFUSE_SALT": gen_base64(32),
        "OAUTH2_PROXY_COOKIE_SECRET": gen_alnum(32), # Ровно 32 байта для AES-256

        # Ключи объектного хранилища S3 (SeaweedFS)
        "S3_ACCESS_KEY": gen_alnum(20, upper_only=True),
        "S3_SECRET_KEY": gen_alnum(40),
    }

    # 2. Автоматически формируем URL-encoded пары (заменяя +, = и т.д.)
    urlencoded_mapping = {
        "CLICKHOUSE_PASSWORD_URLENCODED": "CLICKHOUSE_PASSWORD",
        "REDIS_LANGFUSE_ACL_PASSWORD_URLENCODED": "REDIS_LANGFUSE_ACL_PASSWORD",
        "LANGFUSE_DB_PASSWORD_URLENCODED": "LANGFUSE_DB_PASSWORD",
        "REDIS_FACTORY_ACL_PASSWORD_URLENCODED": "REDIS_FACTORY_ACL_PASSWORD",
        "GATEWAY_DB_PASSWORD_URLENCODED": "GATEWAY_DB_PASSWORD",
    }

    for encoded_key, source_key in urlencoded_mapping.items():
        raw_val = new_secrets[source_key]
        new_secrets[encoded_key] = urllib.parse.quote(raw_val, safe="")

    # 3. Читаем исходный файл и делаем резервную копию
    original_content = INPUT_FILE.read_text(encoding="utf-8")
    BACKUP_FILE.write_text(original_content, encoding="utf-8")

    updated_lines = []
    replaced_count = 0

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
                continue
        updated_lines.append(line)

    # 4. Записываем обновленный файл
    INPUT_FILE.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")

    print(f"Успешно обновлено секретов: {replaced_count}")
    print(f"Старая версия сохранена в: {BACKUP_FILE.resolve()}")
    print("ВАЖНО: Удалите .env.bak после применения изменений в инфраструктуре!")

if __name__ == "__main__":
    rotate_env_file()
