#!/usr/bin/env python3
"""
generate_pki.py — Генерация PKI-инфраструктуры для Factory Agents Platform
Production Blueprint v5.2.1

Назначение:
Генерация корпоративного Root CA и всех необходимых сертификатов
для mTLS-интеграций между компонентами платформы (ВМ №1, ВМ №2, GPU-узел).

Структура вывода (D:\agent-factory\certs\):
├── ca\
│   ├── corp-ca.crt          # Корпоративный Root CA (публичный, для доверия)
│   └── corp-ca.key          # Приватный ключ Root CA (ХРАНИТЬ В СЕКРЕТЕ!)
├── vm1\
│   └── nginx\
│       ├── server.crt       # TLS-сертификат nginx (SAN: factory + langfuse)
│       └── server.key
├── vm2\
│   ├── client\
│   │   ├── vm2-client.crt   # Клиентский сертификат воркера (для GPU, ВМ №4)
│   │   └── vm2-client.key
│   ├── pg\
│   │   ├── pg-server.crt    # Серверный сертификат PostgreSQL ВМ №2
│   │   └── pg-server.key
│   └── redis\
│       ├── redis.crt        # Серверный сертификат Redis ВМ №2
│       ├── redis.key
│       └── ca.crt           # Копия Root CA для валидации клиентов
├── gpu-node\
│   ├── gpu-server.crt       # Серверный сертификат mTLS front GPU-узла
│   ├── gpu-server.key
│   └── ca.crt               # Копия Root CA
└── browser\ (опционально)
    └── admin-client.pfx     # Клиентский сертификат для браузера (mTLS)

Использование:
  python generate_pki.py
  python generate_pki.py --certs-dir D:\agent-factory\certs
  python generate_pki.py --ingest-only  # Только сертификат для Langfuse ingest
  python generate_pki.py --no-pfx       # Без генерации PFX-бандлов для Windows

Требования:
  - Python 3.10+
  - cryptography >= 40.0.0 (pip install cryptography)
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
CA_CURVE = ec.SECP384R1()  # ECC P-384 для CA (современно, быстро)
CA_HASH_ALGO = hashes.SHA384()
CERT_KEY_SIZE = 2048  # RSA 2048 для серверных/клиентских сертификатов
CERT_HASH_ALGO = hashes.SHA256()

CA_VALIDITY_DAYS = 3650  # 10 лет для Root CA
CERT_VALIDITY_DAYS = 397  # ~13 месяцев для leaf-сертификатов

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
    """Генерирует PFX-бандл (PKCS#12) с современным шифрованием (AES-256)."""
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
        print(f"   [PFX] {path.name} (Algorithm: AES-256 + SHA-256 PBES2)")
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
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                content_commitment=False, key_encipherment=False, data_encipherment=False,
                key_agreement=False, encipher_only=False, decipher_only=False,
            ), critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
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
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
    )

    # SAN (Subject Alternative Name)
    san_entries = [x509.DNSName(name) for name in dns_names]
    san_entries.extend([x509.IPAddress(ip) for ip in ip_addresses])
    if san_entries:
        builder = builder.add_extension(x509.SubjectAlternativeName(san_entries), critical=False)

    # Extended Key Usage
    eku_usages = []
    if is_server:
        eku_usages.append(ExtendedKeyUsageOID.SERVER_AUTH)
    if is_client:
        euk_usages = ExtendedKeyUsageOID.CLIENT_AUTH  # Опечатка исправлена ниже
        eku_usages.append(ExtendedKeyUsageOID.CLIENT_AUTH)
    if eku_usages:
        builder = builder.add_extension(x509.ExtendedKeyUsage(eku_usages), critical=False)

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
        ), critical=True,
    )

    cert = builder.sign(ca_key, CERT_HASH_ALGO)
    return key, cert


# ==============================================================================
# ОСНОВНАЯ ЛОГИКА
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="PKI generator for Factory Agents Platform (Blueprint v5.2.1)")
    parser.add_argument("--certs-dir", type=Path, default=DEFAULT_CERTS_DIR, help="Directory for certificates")
    parser.add_argument("--organization", type=str, default="Factory Agents Platform", help="Organization name")
    parser.add_argument("--no-archive", action="store_true", help="Disable archiving of old certificates")
    parser.add_argument("--pfx-password", type=str, default=None, help="Password for PFX bundles")
    parser.add_argument("--no-pfx", action="store_true", help="Disable PFX generation")
    parser.add_argument("--ingest-only", action="store_true",
                        help="Generate ONLY the VM1 Langfuse OTel-ingest server cert using existing Root CA")
    parser.add_argument("--seaweedfs-only", action="store_true",
                        help="Generate ONLY the VM2 SeaweedFS S3 TLS certificate/key using existing Root CA")
    parser.add_argument("--refresh-leaf-certs", action="store_true",
                        help="Refresh leaf certificates while preserving the existing Root CA")
    args = parser.parse_args()

    certs_dir: Path = args.certs_dir
    archive_dir = certs_dir / "archive"
    organization = args.organization
    pfx_password = args.pfx_password or secrets.token_urlsafe(18)
    generate_pfx_files = not args.no_pfx

    # ==========================================================================
    # СПЕЦИАЛЬНЫЙ РЕЖИМ: --ingest-only
    # ==========================================================================
    if args.ingest_only:
        ca_key_path = certs_dir / "ca" / "corp-ca.key"
        ca_cert_path = certs_dir / "ca" / "corp-ca.crt"
        if not ca_key_path.is_file() or not ca_cert_path.is_file():
            raise FileNotFoundError(
                "Existing Root CA key/certificate are required for --ingest-only. Run without this flag first.")

        ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)
        ca_cert = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())

        output_dir = certs_dir / "vm1" / "nginx"
        output_dir.mkdir(parents=True, exist_ok=True)

        archive_if_exists(output_dir / "langfuse-ingest.crt", certs_dir / "archive")
        archive_if_exists(output_dir / "langfuse-ingest.key", certs_dir / "archive")

        key, cert = build_node_certificate(
            ca_key=ca_key, ca_cert=ca_cert,
            common_name="langfuse-ingest.corp.example.com",
            organization=organization,
            dns_names=["langfuse-ingest.corp.example.com", "localhost"],
            ip_addresses=[], is_server=True, is_client=False,
        )
        save_private_key(key, output_dir / "langfuse-ingest.key")
        save_certificate(cert, output_dir / "langfuse-ingest.crt")

        print("\n[OK] Generated VM1 Langfuse OTel-ingest server certificate")
        print(f"     Certificate: {output_dir / 'langfuse-ingest.crt'}")
        print(f"     Private key: {output_dir / 'langfuse-ingest.key'}")
        print(f"     Fingerprint: {fingerprint(cert)}")
        return

    # ==========================================================================
    # ПОЛНАЯ ГЕНЕРАЦИЯ PKI
    # ==========================================================================
    print(f"\n[PKI] Generator :: Factory Agents Platform (Blueprint v5.2.1)")
    print(f"   Directory: {certs_dir.resolve()}\n")

    if args.seaweedfs_only:
        ca_key_path = certs_dir / "ca" / "corp-ca.key"
        ca_cert_path = certs_dir / "ca" / "corp-ca.crt"
        if not ca_key_path.is_file() or not ca_cert_path.is_file():
            raise FileNotFoundError("Existing Root CA files required for --seaweedfs-only")
        ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)
        ca_cert = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())
        output_dir = certs_dir / "vm2" / "seaweedfs"
        output_dir.mkdir(parents=True, exist_ok=True)
        archive_if_exists(output_dir / "s3-server.crt", certs_dir / "archive")
        archive_if_exists(output_dir / "s3-server.key", certs_dir / "archive")
        key, cert = build_node_certificate(
            ca_key=ca_key, ca_cert=ca_cert, common_name="seaweedfs-s3",
            organization=organization, dns_names=["seaweedfs-s3", "localhost"],
            ip_addresses=[], is_server=True, is_client=False,
        )
        save_private_key(key, output_dir / "s3-server.key")
        save_certificate(cert, output_dir / "s3-server.crt")
        print("[OK] Generated VM2 SeaweedFS S3 TLS certificate with existing Root CA")
        return

    existing_ca = None
    if args.refresh_leaf_certs:
        ca_key_path = certs_dir / "ca" / "corp-ca.key"
        ca_cert_path = certs_dir / "ca" / "corp-ca.crt"
        if not ca_key_path.is_file() or not ca_cert_path.is_file():
            raise FileNotFoundError("Existing Root CA key/certificate are required for --refresh-leaf-certs")
        existing_ca = (
            serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None),
            x509.load_pem_x509_certificate(ca_cert_path.read_bytes()),
        )
    if generate_pfx_files:
        print(f"[PFX] Password for bundles (save this!): {pfx_password}\\n")

    if not args.no_archive and certs_dir.exists():
        print("[ARCHIVE] Existing files...")
        files_to_archive = []
        if not args.refresh_leaf_certs:
            files_to_archive.extend(["ca/corp-ca.key", "ca/corp-ca.crt"])
        for ext in ["*.key", "*.crt", "*.pfx"]:
            for f in certs_dir.rglob(ext):
                rel = f.relative_to(certs_dir)
                if rel.parts[:1] == ("ca",):
                    continue
                files_to_archive.append(str(rel))
        archived_count = sum(1 for fname in set(files_to_archive) if archive_if_exists(certs_dir / fname, archive_dir))
        print(f"   Archived files: {archived_count}\\n")

    # Создание структуры директорий
    (certs_dir / "ca").mkdir(parents=True, exist_ok=True)
    (certs_dir / "vm1" / "nginx").mkdir(parents=True, exist_ok=True)
    (certs_dir / "vm2" / "client").mkdir(parents=True, exist_ok=True)
    (certs_dir / "vm2" / "pg").mkdir(parents=True, exist_ok=True)
    (certs_dir / "vm2" / "redis").mkdir(parents=True, exist_ok=True)
    (certs_dir / "vm2" / "seaweedfs").mkdir(parents=True, exist_ok=True)
    (certs_dir / "gpu-node").mkdir(parents=True, exist_ok=True)
    (certs_dir / "browser").mkdir(parents=True, exist_ok=True)

    # 1. Corporate Root CA: preserve existing trust anchor when refreshing leaf certificates.
    if existing_ca:
        ca_key, ca_cert = existing_ca
        print("[1/8] Reusing existing Corporate Root CA (not rotated).")
    else:
        print("[1/7] Generating Corporate Root CA (ECC P-384)...")
        ca_key, ca_cert = build_root_ca(common_name="Factory Agents Platform Root CA", organization=organization)
        save_private_key(ca_key, certs_dir / "ca" / "corp-ca.key")
        save_certificate(ca_cert, certs_dir / "ca" / "corp-ca.crt")
        print(f"   CN: Factory Agents Platform Root CA")
        print(f"   Fingerprint: {fingerprint(ca_cert)}")
        print(f"   [!] Храните {certs_dir / 'ca' / 'corp-ca.key'} в секрете!\\n")

    # 2. NGINX TLS (ВМ №1)
    print("[2/7] Generating nginx TLS certificate (ВМ №1)...")
    nginx_key, nginx_cert = build_node_certificate(
        ca_key=ca_key, ca_cert=ca_cert, common_name="factory.corp.example.com",
        organization=organization,
        dns_names=["factory.corp.example.com", "langfuse.corp.example.com", "localhost"],
        ip_addresses=[], is_server=True, is_client=False,
    )
    save_private_key(nginx_key, certs_dir / "vm1" / "nginx" / "server.key")
    save_certificate(nginx_cert, certs_dir / "vm1" / "nginx" / "server.crt")
    print(f"   CN: factory.corp.example.com | SAN: factory, langfuse, localhost\n")

    # 3. КЛИЕНТСКИЙ СЕРТИФИКАТ WORKER (ВМ №2)
    print("[3/7] Generating worker client certificate (ВМ №2)...")
    worker_key, worker_cert = build_node_certificate(
        ca_key=ca_key, ca_cert=ca_cert, common_name="vm2-worker",
        organization=organization, dns_names=[], ip_addresses=[],
        is_server=False, is_client=True,
    )
    save_private_key(worker_key, certs_dir / "vm2" / "client" / "vm2-client.key")
    save_certificate(worker_cert, certs_dir / "vm2" / "client" / "vm2-client.crt")
    if generate_pfx_files:
        generate_pfx(worker_key, worker_cert, ca_cert, certs_dir / "vm2" / "client" / "vm2-client.pfx", pfx_password,
                     "Factory Worker (mTLS Client)")
    print(f"   CN: vm2-worker (ClientAuth)\n")

    # 4. СЕРВЕРНЫЙ СЕРТИФИКАТ POSTGRESQL (ВМ №2)
    print("[4/7] Generating PostgreSQL server certificate (ВМ №2)...")
    pg_key, pg_cert = build_node_certificate(
        ca_key=ca_key, ca_cert=ca_cert, common_name="vm2-postgres",
        organization=organization, dns_names=["vm2-postgres", "localhost"], ip_addresses=[],
        is_server=True, is_client=False,
    )
    save_private_key(pg_key, certs_dir / "vm2" / "pg" / "pg-server.key")
    save_certificate(pg_cert, certs_dir / "vm2" / "pg" / "pg-server.crt")
    print(f"   CN: vm2-postgres | SAN: vm2-postgres, localhost\n")

    # 5. СЕРВЕРНЫЙ СЕРТИФИКАТ REDIS (ВМ №2)
    print("[5/7] Generating Redis server certificate (ВМ №2)...")
    redis_key, redis_cert = build_node_certificate(
        ca_key=ca_key, ca_cert=ca_cert, common_name="redis-factory",
        organization=organization, dns_names=["redis-factory", "localhost"], ip_addresses=[],
        is_server=True, is_client=False,
    )
    save_private_key(redis_key, certs_dir / "vm2" / "redis" / "redis.key")
    save_certificate(redis_cert, certs_dir / "vm2" / "redis" / "redis.crt")
    shutil.copy2(certs_dir / "ca" / "corp-ca.crt", certs_dir / "vm2" / "redis" / "ca.crt")
    print(f"   CN: redis-factory | SAN: redis-factory, localhost\n")

    # 6. SEAWEEDFS S3 SERVER CERTIFICATE (ВМ №2)
    print("[6/8] Generating SeaweedFS S3 server certificate (ВМ №2)...")
    s3_key, s3_cert = build_node_certificate(
        ca_key=ca_key, ca_cert=ca_cert, common_name="seaweedfs-s3",
        organization=organization, dns_names=["seaweedfs-s3", "localhost"], ip_addresses=[],
        is_server=True, is_client=False,
    )
    save_private_key(s3_key, certs_dir / "vm2" / "seaweedfs" / "s3-server.key")
    save_certificate(s3_cert, certs_dir / "vm2" / "seaweedfs" / "s3-server.crt")
    print("   CN: seaweedfs-s3 | SAN: seaweedfs-s3, localhost\\n")

    # 7. СЕРВЕРНЫЙ СЕРТИФИКАТ GPU-УЗЛА (Узел №3)
    print("[7/8] Generating GPU Node server certificate (Узел №3)...")
    gpu_key, gpu_cert = build_node_certificate(
        ca_key=ca_key, ca_cert=ca_cert, common_name="gpu-node.corp.example.com",
        organization=organization, dns_names=["gpu-node.corp.example.com", "localhost"], ip_addresses=[],
        is_server=True, is_client=False,
    )
    save_private_key(gpu_key, certs_dir / "gpu-node" / "gpu-server.key")
    save_certificate(gpu_cert, certs_dir / "gpu-node" / "gpu-server.crt")
    shutil.copy2(certs_dir / "ca" / "corp-ca.crt", certs_dir / "gpu-node" / "ca.crt")
    print(f"   CN: gpu-node.corp.example.com | SAN: gpu-node, localhost\n")

    # 7. КЛИЕНТСКИЙ СЕРТИФИКАТ ДЛЯ БРАУЗЕРА (Опционально, для mTLS админ-доступа)
    print("[8/8] Generating browser client certificate (Опционально)...")
    browser_key, browser_cert = build_node_certificate(
        ca_key=ca_key, ca_cert=ca_cert, common_name="browser-admin",
        organization=organization, dns_names=[], ip_addresses=[],
        is_server=False, is_client=True,
    )
    if generate_pfx_files:
        generate_pfx(browser_key, browser_cert, ca_cert, certs_dir / "browser" / "admin-client.pfx", pfx_password,
                     "Browser Admin (mTLS Client)")
        print(f"   Установите {certs_dir / 'browser' / 'admin-client.pfx'} в браузер для mTLS-аутентификации.\n")
    else:
        print("   Пропущено (--no-pfx)\n")

    # ИТОГ
    print("=" * 70)
    print("[OK] PKI infrastructure successfully generated!")
    print("=" * 70)
    print("\nСледующие шаги:")
    print("  1. Импортируйте `certs/ca/corp-ca.crt` в доверенные корневые центры сертификации вашей ОС/браузера.")
    print("  2. Убедитесь, что docker-compose.yml монтирует файлы из `../certs/` (уже настроено).")
    print("  3. Запустите стек: docker compose up -d\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Interrupted")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        raise