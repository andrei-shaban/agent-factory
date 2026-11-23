#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Secrets Generator для подсистемы Llm-ritm (Audit Layer)

Контракт формата (проверен по потребителю ritm-audit-service):
  - HMAC-ключ хранится как HEX-строка; HMACSigner._load_key() делает bytes.fromhex()
    и требует ровно HMAC_KEY_EXPECTED_BYTES = 48 байт после декодирования.
  - Файл читается со .strip(), поэтому отсутствующий завершающий \n допустим.
  - Fingerprint = SHA256 по ASCII-hex представлению ключа: совпадает с
    HMACSigner._compute_key_fingerprint() (key.hex()), т.е. отпечаток из этой консоли
    можно сверять с логом сервиса "HMACSigner initialized: fingerprint=...".
"""

import argparse
import datetime
import secrets
import shutil
import string
import sys
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import hashes


# 48 байт = размер отпечатка SHA-384; на диске это 96 hex-символов.
# Потребитель декодирует hex и проверяет длину ровно в 48 байт - не менять на 32/64.
HMAC_KEY_BYTES = 48
DB_PASSWORD_LENGTH = 32
DB_PASSWORD_ALPHABET = string.ascii_letters + string.digits

HMAC_KEY_FILE = "audit_hmac.key"
DB_PASSWORD_FILE = "audit_db_password.txt"

# Имена Docker-секретов на стороне ritm-audit-service отличаются от имён файлов:
# монтаж требует переименования, иначе сервис упадёт с "HMAC key file not found".
MOUNT_TARGETS = {
    HMAC_KEY_FILE: "/run/secrets/audit_hmac_key",
    DB_PASSWORD_FILE: "/run/secrets/audit_pg_password",
}


def archive_if_exists(path: Path, archive_dir: Path) -> Optional[Path]:
    if not path.exists():
        return None
    archive_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    archived_path = archive_dir / f"{path.name}.{timestamp}"
    shutil.move(str(path), str(archived_path))
    print(f"  📦 Архивировано: {path.name}")
    return archived_path


def fingerprint_hex(hex_string: str) -> str:
    digest = hashes.Hash(hashes.SHA256())
    digest.update(hex_string.encode("ascii"))
    h = digest.finalize().hex()
    return ":".join(h[i:i+2].upper() for i in range(0, len(h), 2))


def generate_hmac_key() -> str:
    return secrets.token_hex(HMAC_KEY_BYTES)


def generate_db_password(length: int = DB_PASSWORD_LENGTH) -> str:
    return "".join(secrets.choice(DB_PASSWORD_ALPHABET) for _ in range(length))


def save_secret(value: str, path: Path) -> None:
    path.write_text(value, encoding="ascii")
    try:
        path.chmod(0o600)
    except (OSError, AttributeError):
        pass


def main():
    parser = argparse.ArgumentParser(description="Secrets Generator для Llm-ritm")
    parser.add_argument("--secrets-dir", type=Path, default=Path("secrets"))
    parser.add_argument("--with-db-password", action="store_true")
    parser.add_argument("--no-archive", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Разрешить замену уже существующих секретов (иначе генерация отказывается).",
    )
    args = parser.parse_args()

    secrets_dir: Path = args.secrets_dir
    archive_dir = secrets_dir / "archive"

    print(f"\n🔑 Secrets Generator :: Llm-ritm")
    print(f"   Директория: {secrets_dir.resolve()}\n")

    targets = [secrets_dir / HMAC_KEY_FILE]
    if args.with_db_password:
        targets.append(secrets_dir / DB_PASSWORD_FILE)

    # Замена HMAC-ключа не «исправляет» ничего: ранее подписанные записи аудита
    # перестают верифицироваться (обрывается hash chain), поэтому по умолчанию
    # существующий секрет не трогаем.
    existing = [p for p in targets if p.exists()]
    if existing and not args.force:
        for p in existing:
            print(f"   ⛔ {p.name} уже существует")
        raise SystemExit(
            "❌ Перезапись существующих секретов запрещена без --force.\n"
            "   Смена HMAC-ключа делает непроверяющими уже подписанные строки аудита.\n"
            "   Сохраните текущий каталог (или сверьте fingerprint) и повторите с --force."
        )

    if not args.no_archive:
        for path in existing:
            archive_if_exists(path, archive_dir)

    secrets_dir.mkdir(parents=True, exist_ok=True)

    print("🔐 Генерация HMAC-SHA384 ключа...")
    hmac_key = generate_hmac_key()
    save_secret(hmac_key, secrets_dir / HMAC_KEY_FILE)
    print(f"   Fingerprint: {fingerprint_hex(hmac_key)}\n")
    written = [secrets_dir / HMAC_KEY_FILE]

    if args.with_db_password:
        print("🗄️  Генерация пароля PostgreSQL...")
        db_password = generate_db_password()
        save_secret(db_password, secrets_dir / DB_PASSWORD_FILE)
        print(f"   Fingerprint: {fingerprint_hex(db_password)}\n")
        written.append(secrets_dir / DB_PASSWORD_FILE)

    print("📦 Монтаж в ritm-audit-service (имя файла -> имя Docker-секрета):")
    for path in written:
        print(f"   {path}  ->  {MOUNT_TARGETS[path.name]}")
    print("   Дополнительно сервис требует: /run/secrets/ca_cert, vm3_node_crt, vm3_node_key")
    print("   (см. scripts/generate_pki.py) и CN клиента из whitelist ALLOWED_CLIENT_CNS.")

    print("\n✅ Секреты успешно сгенерированы!")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n❌ Прервано")
        sys.exit(1)
