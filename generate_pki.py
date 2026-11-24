#generate_pki.py
import argparse
import datetime
import secrets
import shutil
import sys
from pathlib import Path
from typing import Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

CURVE = ec.SECP384R1()
HASH_ALGO = hashes.SHA384()
CA_VALIDITY_DAYS = 3650
CERT_VALIDITY_DAYS = 397


def archive_if_exists(path: Path, archive_dir: Path) -> Optional[Path]:
    if not path.exists():
        return None
    archive_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    archived_path = archive_dir / f"{path.name}.{timestamp}"
    shutil.move(str(path), str(archived_path))
    print(f"  [ARCHIVE] {path.name} -> {archived_path}")
    return archived_path


def generate_private_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(curve=CURVE)


def save_private_key(key: ec.EllipticCurvePrivateKey, path: Path) -> None:
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path.write_bytes(pem)
    try:
        path.chmod(0o600)
    except (OSError, AttributeError):
        pass


def save_certificate(cert: x509.Certificate, path: Path) -> None:
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    try:
        path.chmod(0o644)
    except (OSError, AttributeError):
        pass


def fingerprint(cert: x509.Certificate) -> str:
    digest = cert.fingerprint(hashes.SHA256())
    return ":".join(f"{b:02X}" for b in digest)


def generate_pfx(
        key: ec.EllipticCurvePrivateKey,
        cert: x509.Certificate,
        ca_cert: x509.Certificate,
        path: Path,
        password: str,
        friendly_name: str,
        legacy: bool = False,
) -> None:
    # Используем современное безопасное шифрование (AES-256 + SHA-256 / PBES2).
    # Аргументы hmac_hash и key_cert_algorithm намеренно удалены, так как они
    # вызывают TypeError в cryptography >= 38.x. Современный Windows (10/11/Server 2019+)
    # нативно поддерживает этот формат.
    encryption = serialization.BestAvailableEncryption(password.encode("utf-8"))

    try:
        pfx_data = pkcs12.serialize_key_and_certificates(
            name=friendly_name.encode("utf-8"),
            key=key,
            cert=cert,
            cas=[ca_cert],
            encryption_algorithm=encryption,
        )
        algorithm = "AES-256 + SHA-256 (PBES2, стандарт)"
    except Exception as e:
        print(f"   [!] Ошибка генерации PFX: {e}")
        raise

    path.write_bytes(pfx_data)
    try:
        path.chmod(0o600)
    except (OSError, AttributeError):
        pass
    print(f"   [PFX] {path.name}")
    print(f"      Algorithm: {algorithm}")
    print(f"      Chain: Client Cert + Root CA")


def build_root_ca(common_name: str, organization: str) -> tuple:
    key = generate_private_key()
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
        .sign(key, HASH_ALGO)
    )
    return key, cert


def build_node_certificate(
        ca_key: ec.EllipticCurvePrivateKey,
        ca_cert: x509.Certificate,
        common_name: str,
        organization: str,
        dns_names: list,
        ip_addresses: list,
        is_server: bool = False,
        is_client: bool = True,
) -> tuple:
    key = generate_private_key()
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

    san_entries = [x509.DNSName(name) for name in dns_names]
    san_entries.extend([x509.IPAddress(ip) for ip in ip_addresses])
    if san_entries:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(san_entries),
            critical=False,
        )

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

    builder = builder.add_extension(
        x509.KeyUsage(
            digital_signature=True,
            key_encipherment=is_server,
            key_agreement=True,
            content_commitment=False,
            data_encipherment=False,
            key_cert_sign=False,
            crl_sign=False,
            encipher_only=False,
            decipher_only=False,
        ),
        critical=True,
    )

    cert = builder.sign(ca_key, HASH_ALGO)
    return key, cert


def main():
    parser = argparse.ArgumentParser(
        description="PKI generator for Llm-ritm (mTLS Zero Trust)",
    )
    parser.add_argument(
        "--certs-dir",
        type=Path,
        default=Path("certs"),
        help="Directory for certificates",
    )
    parser.add_argument(
        "--organization",
        type=str,
        default="Ritm Platform",
        help="Organization name",
    )
    parser.add_argument(
        "--no-archive",
        action="store_true",
        help="Disable archiving",
    )
    parser.add_argument(
        "--pfx-password",
        type=str,
        default=None,
        help="Password for PFX bundles. When omitted, a random one is generated.",
    )
    parser.add_argument(
        "--pfx-legacy",
        action="store_true",
        help="Ignored in modern cryptography. Modern AES-256 (PBES2) is used.",
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

    print("\n[PKI] Generator :: Llm-ritm (Synchronized)")
    print(f"   Directory: {certs_dir.resolve()}\n")

    if generate_pfx_files:
        if pfx_password_supplied:
            print("[PFX] Using the supplied password for all bundles.")
        else:
            print("[PFX] No --pfx-password given, generated a random one:")
            print(f"      Password (shown once, not stored): {pfx_password}")
        print()

    if not args.no_archive and certs_dir.exists():
        print("[ARCHIVE] Existing files...")
        files_to_archive = ["ca.key", "ca.crt"]
        for name in ["gateway", "browser_client", "gpu-node", "vm2-client", "vm3-client"]:
            files_to_archive.extend([f"{name}.key", f"{name}.crt", f"{name}.pfx"])

        archived_count = 0
        for fname in files_to_archive:
            if archive_if_exists(certs_dir / fname, archive_dir):
                archived_count += 1
        print(f"   Archived files: {archived_count}\n")

    certs_dir.mkdir(parents=True, exist_ok=True)

    print("[CA] Generating Root CA...")
    ca_key, ca_cert = build_root_ca(
        common_name="Ritm Internal Root CA",
        organization=organization,
    )
    save_private_key(ca_key, certs_dir / "ca.key")
    save_certificate(ca_cert, certs_dir / "ca.crt")
    print("   CN: Ritm Internal Root CA")
    print(f"   [!] {certs_dir / 'ca.key'} is the trust anchor: move it to offline storage")
    print("       and keep it out of the delivery tree (BLUEPRINT S3).\n")

    print("[CERT] Generating server certificate (API Gateway)...")
    gw_key, gw_cert = build_node_certificate(
        ca_key=ca_key,
        ca_cert=ca_cert,
        common_name="llm-ritm.local",
        organization=organization,
        dns_names=["llm-ritm.local", "gateway.llm-ritm.local", "localhost"],
        ip_addresses=[],
        is_server=True,
        is_client=False,
    )
    save_private_key(gw_key, certs_dir / "gateway.key")
    save_certificate(gw_cert, certs_dir / "gateway.crt")
    print("   CN: llm-ritm.local")
    print("   SAN DNS: llm-ritm.local, gateway.llm-ritm.local, localhost")
    print("   EKU: ServerAuth\n")

    print("[CERT] Generating browser client certificate...")
    browser_key, browser_cert = build_node_certificate(
        ca_key=ca_key,
        ca_cert=ca_cert,
        common_name="Ritm-User-Edge",  # <-- Идеально совпадает с ~*Ritm-User-Edge в nginx.conf
        organization=organization,
        dns_names=[],
        ip_addresses=[],
        is_server=False,
        is_client=True,
    )
    save_private_key(browser_key, certs_dir / "browser_client.key")
    save_certificate(browser_cert, certs_dir / "browser_client.crt")
    if generate_pfx_files:
        generate_pfx(
            key=browser_key,
            cert=browser_cert,
            ca_cert=ca_cert,
            path=certs_dir / "browser_client.pfx",
            password=pfx_password,
            friendly_name="Ritm Browser User (mTLS Client)",
            legacy=args.pfx_legacy,
        )

    print("\n[CERT] Node identity certificates...")
    nodes = [
        ("gpu-node", "gpu-node.llm-ritm.local", []),
        ("vm2-client", "vm2-client.llm-ritm.local", []),
        ("vm3-client", "vm3-client.llm-ritm.local", []),
    ]
    for name, cn, extra_dns in nodes:
        node_key, node_cert = build_node_certificate(
            ca_key=ca_key,
            ca_cert=ca_cert,
            common_name=cn,
            organization=organization,
            dns_names=[cn] + extra_dns,
            ip_addresses=[],
            is_server=False,
            is_client=True,
        )
        save_private_key(node_key, certs_dir / f"{name}.key")
        save_certificate(node_cert, certs_dir / f"{name}.crt")
        print(f"   Generated: {name}")
        if generate_pfx_files:
            generate_pfx(
                key=node_key,
                cert=node_cert,
                ca_cert=ca_cert,
                path=certs_dir / f"{name}.pfx",
                password=pfx_password,
                friendly_name=f"Ritm {name} (mTLS Client)",
                legacy=args.pfx_legacy,
            )

    print("\n" + "=" * 70)
    print("[OK] PKI infrastructure successfully generated!")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        raise