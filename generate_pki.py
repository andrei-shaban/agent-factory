#!/usr/bin/env python3
"""
generate_pki.py — Генерация PKI-инфраструктуры для Factory Agents Platform
Production Blueprint v5.2.1

Назначение:
  Генерация корпоративного Root CA и всех необходимых сертификатов
  для mTLS-интеграций между компонентами платформы.

Структура вывода:
  D:\agent-factory\certs\
  ├── ca\
  │   ├── corp-ca.crt          # Корпоративный Root CA (публичный)
  │   └── corp-ca.key          # Приватный ключ Root CA (ХРАНИТЬ В СЕКРЕТЕ!)
  ├── vm1\nginx\
  │   ├── server.crt           # TLS-сертификат nginx (SAN: factory. + langfuse.)
  │   └── server.key
  └── vm2\
      ├── client\
      │   ├── vm2-client.crt   # Клиентский сертификат воркера (для GPU, ВМ №4, АРМ)
      │   └── vm2-client.key
      ├── pg\
      │   ├── pg-server.crt    # Серверный сертификат PostgreSQL
      │   └── pg-server.key
      └── redis\
          ├── redis.crt        # Серверный сертификат Redis
          ├── redis.key
          └── ca.crt           # Копия Root CA для проверки клиентов

Использование:
    python generate_pki.py
    python generate_pki.py --certs-dir D:\agent-factory\certs
    python generate_pki.py --no-archive  # без архивации старых сертификатов
    python generate_pki.py --no-pfx      # без генерации PFX-бандлов

Требования:
    - Python 3.14+
    - cryptography >= 50.0.0

Blueprint-соответствие:
    - §1.8: Принцип автономности артефактов (сертификаты монтируются, не вшиваются)
    - §3.2: mTLS-интеграции (воркер ↔ GPU, воркер ↔ ВМ №4, воркер ↔ АРМ)
    - §3.4: TLS для Redis и PostgreSQL
"""

import argparse
import datetime
import secrets
import shutil
import sys
from pathlib import Path
from typing import Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

# ==============================================================================
# КОНСТАНТЫ
# ==============================================================================
# Криптографические параметры
CA_CURVE = ec.SECP384R1()  # ECC P-384 для CA (современно, быстро)
CA_HASH_ALGO = hashes.SHA384()
CERT_KEY_SIZE = 2048  # RSA 2048 для серверных сертификатов (совместимость)
CERT_HASH_ALGO = hashes.SHA256()

# Сроки действия
CA_VALIDITY_DAYS = 3650  # 10 лет для Root CA
CERT_VALIDITY_DAYS = 397  # ~13 месяцев для серверных/клиентских сертификатов

# Пути по умолчанию
DEFAULT_CERTS_DIR = Path(r"D:\agent-factory\certs")


# ==============================================================================
# УТИЛИТЫ
# ==============================================================================
def archive_if_exists(path: Path, archive_dir: Path) -> Optional[Path]:
    """Архивирует существующий файл с временной меткой."""
    if not path.exists():
        return None
    archive_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    archived_path = archive_dir / f"{path.name}.{timestamp}"
    shutil.move(str(path), str(archived_path))
    print(f"  [ARCHIVE] {path.name} -> {archived_path}")
    return archived_path


def fingerprint(cert: x509.Certificate) -> str:
    """Вычисляет SHA-256 fingerprint сертификата."""
    digest = cert.fingerprint(hashes.SHA256())
    return ":".join(f"{b:02X}" for b in digest)


def save_private_key(key, path: Path) -> None:
    """Сохраняет приватный ключ в PEM-формате с правами 0600."""
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path.write_bytes(pem)
    try:
        path.chmod(0o600)
    except (OSError, AttributeError):
        pass  # Windows не поддерживает chmod


def save_certificate(cert: x509.Certificate, path: Path) -> None:
    """Сохраняет сертификат в PEM-формате с правами 0644."""
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    try:
        path.chmod(0o644)
    except (OSError, AttributeError):
        pass


def generate_pfx(
    key,
    cert: x509.Certificate,
    ca_cert: x509.Certificate,
    path: Path,
    password: str,
    friendly_name: str,
) -> None:
    """Генерирует PFX-бандл (PKCS#12) с современным шифрованием."""
    encryption = serialization.BestAvailableEncryption(password.encode("utf-8"))
    try:
        pfx_data = pkcs12.serialize_key_and_certificates(
            name=friendly_name.encode("utf-8"),
            key=key,
            cert=cert,
            cas=[ca_cert],
            encryption_algorithm=encryption,
        )
        path.write_bytes(pfx_data)
        try:
            path.chmod(0o600)
        except (OSError, AttributeError):
            pass
        print(f"   [PFX] {path.name}")
    except Exception as e:
        print(f"   [!] Ошибка генерации PFX: {e}")
        raise


# ==============================================================================
# ГЕНЕРАЦИЯ СЕРТИФИКАТОВ
# ==============================================================================
def build_root_ca(common_name: str, organization: str) -> tuple:
    """Генерация корпоративного Root CA (ECC P-384)."""
    key = ec.generate_private_key(curve=CA_CURVE)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "RU"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization),
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(hours=2))
        .not_valid_after(now + datetime.timedelta(days=CA_VALIDITY_DAYS))
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, CA_HASH_ALGO)
    )
    return key, cert


def build_node_certificate(
    ca_key,
    ca_cert: x509.Certificate,
    common_name: str,
    organization: str,
    dns_names: list,
    ip_addresses: list,
    is_server: bool = False,
    is_client: bool = True,
) -> tuple:
    """Генерация серверного или клиентского сертификата (RSA 2048)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=CERT_KEY_SIZE)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "RU"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization),
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(hours=2))
        .not_valid_after(now + datetime.timedelta(days=CERT_VALIDITY_DAYS))
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
    )
    # SAN (Subject Alternative Name)
    san_entries = [x509.DNSName(name) for name in dns_names]
    san_entries.extend([x509.IPAddress(ip) for ip in ip_addresses])
    if san_entries:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(san_entries),
            critical=False,
        )
    # Extended Key Usage
    eku_usages = []
    if is_server:
        eku_usages.append(ExtendedKeyUsageOID.SERVER_AUTH)
    if is_client:
        eku_usages.append(ExtendedKeyUsageOID.CLIENT_AUTH)
    if eku_usages:
        builder = builder.add_extension(
            x509.ExtendedKeyUsage(eku_usages),
            critical=False,
        )
    # Key Usage
    builder = builder.add_extension(
        x509.KeyUsage(
            digital_signature=True,
            key_encipherment=is_server,
            key_agreement=False,
            content_commitment=False,
            data_encipherment=False,
            key_cert_sign=False,
            crl_sign=False,
            encipher_only=False,
            decipher_only=False,
        ),
        critical=True,
    )
    cert = builder.sign(ca_key, CERT_HASH_ALGO)
    return key, cert


# ==============================================================================
# ОСНОВНАЯ ЛОГИКА
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="PKI generator for Factory Agents Platform (Blueprint v5.2.1)",
    )
    parser.add_argument(
        "--certs-dir",
        type=Path,
        default=DEFAULT_CERTS_DIR,
        help="Directory for certificates (default: D:\\agent-factory\\certs)",
    )
    parser.add_argument(
        "--organization",
        type=str,
        default="Factory Agents Platform",
        help="Organization name",
    )
    parser.add_argument(
        "--no-archive",
        action="store_true",
        help="Disable archiving of old certificates",
    )
    parser.add_argument(
        "--pfx-password",
        type=str,
        default=None,
        help="Password for PFX bundles. When omitted, a random one is generated.",
    )
    parser.add_argument(
        "--no-pfx",
        action="store_true",
        help="Disable PFX generation",
    )
    args = parser.parse_args()

    certs_dir: Path = args.certs_dir
    archive_dir = certs_dir / "archive"
    organization = args.organization
    pfx_password = args.pfx_password or secrets.token_urlsafe(18)
    pfx_password_supplied = args.pfx_password is not None
    generate_pfx_files = not args.no_pfx

    print("\n[PKI] Generator :: Factory Agents Platform (Blueprint v5.2.1)")
    print(f"   Directory: {certs_dir.resolve()}\n")

    if generate_pfx_files:
        if pfx_password_supplied:
            print("[PFX] Using the supplied password for all bundles.")
        else:
            print("[PFX] No --pfx-password given, generated a random one:")
            print(f"      Password (shown once, not stored): {pfx_password}")
        print()

    # Архивация старых сертификатов
    if not args.no_archive and certs_dir.exists():
        print("[ARCHIVE] Existing files...")
        files_to_archive = ["ca/corp-ca.key", "ca/corp-ca.crt"]
        # Рекурсивно собираем все .key и .crt
        for ext in ["*.key", "*.crt", "*.pfx"]:
            for f in certs_dir.rglob(ext):
                rel_path = f.relative_to(certs_dir)
                files_to_archive.append(str(rel_path))
        archived_count = 0
        for fname in files_to_archive:
            if archive_if_exists(certs_dir / fname, archive_dir):
                archived_count += 1
        print(f"   Archived files: {archived_count}\n")

    # Создание структуры директорий
    (certs_dir / "ca").mkdir(parents=True, exist_ok=True)
    (certs_dir / "vm1" / "nginx").mkdir(parents=True, exist_ok=True)
    (certs_dir / "vm2" / "client").mkdir(parents=True, exist_ok=True)
    (certs_dir / "vm2" / "pg").mkdir(parents=True, exist_ok=True)
    (certs_dir / "vm2" / "redis").mkdir(parents=True, exist_ok=True)

    # ==========================================================================
    # 1. КОРПОРАТИВНЫЙ ROOT CA
    # ==========================================================================
    print("[CA] Generating Corporate Root CA (ECC P-384)...")
    ca_key, ca_cert = build_root_ca(
        common_name="Factory Agents Platform Root CA",
        organization=organization,
    )
    save_private_key(ca_key, certs_dir / "ca" / "corp-ca.key")
    save_certificate(ca_cert, certs_dir / "ca" / "corp-ca.crt")
    print("   CN: Factory Agents Platform Root CA")
    print(f"   Fingerprint: {fingerprint(ca_cert)}")
    print(f"   [!] {certs_dir / 'ca' / 'corp-ca.key'} is the trust anchor:")
    print("       Store it securely and keep it out of version control.\n")

    # ==========================================================================
    # 2. NGINX TLS (ВМ №1) — SAN на оба домена
    # ==========================================================================
    print("[CERT] Generating nginx TLS certificate (ВМ №1)...")
    nginx_key, nginx_cert = build_node_certificate(
        ca_key=ca_key,
        ca_cert=ca_cert,
        common_name="factory.corp.example.com",
        organization=organization,
        dns_names=[
            "factory.corp.example.com",
            "langfuse.corp.example.com",
            "localhost",
        ],
        ip_addresses=[],
        is_server=True,
        is_client=False,
    )
    save_private_key(nginx_key, certs_dir / "vm1" / "nginx" / "server.key")
    save_certificate(nginx_cert, certs_dir / "vm1" / "nginx" / "server.crt")
    print("   CN: factory.corp.example.com")
    print("   SAN DNS: factory.corp.example.com, langfuse.corp.example.com, localhost")
    print(f"   Fingerprint: {fingerprint(nginx_cert)}")
    print("   EKU: ServerAuth\n")

    # ==========================================================================
    # 3. КЛИЕНТСКИЙ СЕРТИФИКАТ ВОКЕРА (ВМ №2)
    # ==========================================================================
    print("[CERT] Generating worker client certificate (ВМ №2)...")
    worker_key, worker_cert = build_node_certificate(
        ca_key=ca_key,
        ca_cert=ca_cert,
        common_name="vm2-worker",
        organization=organization,
        dns_names=[],
        ip_addresses=[],
        is_server=False,
        is_client=True,
    )
    save_private_key(worker_key, certs_dir / "vm2" / "client" / "vm2-client.key")
    save_certificate(worker_cert, certs_dir / "vm2" / "client" / "vm2-client.crt")
    print("   CN: vm2-worker")
    print(f"   Fingerprint: {fingerprint(worker_cert)}")
    print("   EKU: ClientAuth")
    if generate_pfx_files:
        generate_pfx(
            key=worker_key,
            cert=worker_cert,
            ca_cert=ca_cert,
            path=certs_dir / "vm2" / "client" / "vm2-client.pfx",
            password=pfx_password,
            friendly_name="Factory Worker (mTLS Client)",
        )
    print()

    # ==========================================================================
    # 4. СЕРВЕРНЫЙ СЕРТИФИКАТ POSTGRESQL (ВМ №2)
    # ==========================================================================
    print("[CERT] Generating PostgreSQL server certificate (ВМ №2)...")
    pg_key, pg_cert = build_node_certificate(
        ca_key=ca_key,
        ca_cert=ca_cert,
        common_name="vm2-postgres",
        organization=organization,
        dns_names=["vm2-postgres", "localhost"],
        ip_addresses=[],
        is_server=True,
        is_client=False,
    )
    save_private_key(pg_key, certs_dir / "vm2" / "pg" / "pg-server.key")
    save_certificate(pg_cert, certs_dir / "vm2" / "pg" / "pg-server.crt")
    print("   CN: vm2-postgres")
    print("   SAN DNS: vm2-postgres, localhost")
    print(f"   Fingerprint: {fingerprint(pg_cert)}")
    print("   EKU: ServerAuth\n")

    # ==========================================================================
    # 5. СЕРВЕРНЫЙ СЕРТИФИКАТ REDIS (ВМ №2)
    # ==========================================================================
    print("[CERT] Generating Redis server certificate (ВМ №2)...")
    redis_key, redis_cert = build_node_certificate(
        ca_key=ca_key,
        ca_cert=ca_cert,
        common_name="redis-factory",
        organization=organization,
        dns_names=["redis-factory", "localhost"],
        ip_addresses=[],
        is_server=True,
        is_client=False,
    )
    save_private_key(redis_key, certs_dir / "vm2" / "redis" / "redis.key")
    save_certificate(redis_cert, certs_dir / "vm2" / "redis" / "redis.crt")
    # Копируем Root CA в директорию Redis для проверки клиентов
    shutil.copy2(certs_dir / "ca" / "corp-ca.crt", certs_dir / "vm2" / "redis" / "ca.crt")
    print("   CN: redis-factory")
    print("   SAN DNS: redis-factory, localhost")
    print(f"   Fingerprint: {fingerprint(redis_cert)}")
    print("   EKU: ServerAuth")
    print(f"   [COPIED] ca/corp-ca.crt -> vm2/redis/ca.crt\n")

    # ==========================================================================
    # ИТОГ
    # ==========================================================================
    print("=" * 70)
    print("[OK] PKI infrastructure successfully generated!")
    print("=" * 70)
    print()
    print("Generated certificates:")
    print(f"  ✓ {certs_dir / 'ca' / 'corp-ca.crt'} (Root CA)")
    print(f"  ✓ {certs_dir / 'vm1' / 'nginx' / 'server.crt'} (nginx TLS)")
    print(f"  ✓ {certs_dir / 'vm2' / 'client' / 'vm2-client.crt'} (worker client)")
    print(f"  ✓ {certs_dir / 'vm2' / 'pg' / 'pg-server.crt'} (PostgreSQL server)")
    print(f"  ✓ {certs_dir / 'vm2' / 'redis' / 'redis.crt'} (Redis server)")
    print()
    print("Next steps:")
    print("  1. Verify certificates: openssl x509 -in <cert> -text -noout")
    print("  2. Place certificates in docker-compose.yml (bind mounts)")
    print("  3. Restart services: docker compose restart")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Interrupted")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        raise