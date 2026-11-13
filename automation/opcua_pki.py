#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OPC-UA-Zertifikate: eigenes Client-Zertifikat und vertraute Server-Zertifikate

Ablage (USV_PKI_DIR, auf dem Pi /etc/usv-monitor/pki, sonst data/pki):
    own/client.der, own/client_key.pem   eigenes Zertifikat, privater Schlüssel (Rechte 600;
                                     asyncua erkennt PEM an der Endung)
    trusted/<sha1>.der               vertraute Server-Zertifikate
    rejected/<sha1>.der              zuletzt gesehene, nicht vertraute Server-Zertifikate

Das Client-Zertifikat folgt „OPC-UA-Zertifikate für die Bosch Rexroth XM42“ (v9.6.36): Ohne keyCertSign,
AuthorityKeyIdentifier oder die ApplicationUri als ersten SAN-Eintrag lehnt die Steuerung es ab.
Ändert sich der Hostname, stimmt die ApplicationUri nicht mehr; das Zertifikat wird neu erzeugt und muss
auf der Steuerung erneut vertraut werden.
"""

import datetime
import ipaddress
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_SHA1_RE = re.compile(r"^[0-9A-F]{40}$")
VALID_YEARS = 5


def pki_dir() -> Path:
    configured = os.environ.get("USV_PKI_DIR")
    return Path(configured) if configured else Path(__file__).resolve().parent.parent / "data" / "pki"


def _own(name: str) -> Path:
    return pki_dir() / "own" / name


def certificate_path() -> Path:
    return _own("client.der")


def key_path() -> Path:
    return _own("client_key.pem")


def application_uri(hostname: Optional[str] = None) -> str:
    return f"urn:{hostname or socket.gethostname()}:USVMonitor:OPCUA"


def _ipv4_addresses() -> List[str]:
    found = set()
    try:
        output = subprocess.run(["hostname", "-I"], capture_output=True, text=True, errors="replace", timeout=3).stdout
        found.update(output.split())
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        found.update(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    result = []
    for text in sorted(found):
        try:
            address = ipaddress.ip_address(text)
        except ValueError:
            continue
        if address.version == 4 and not address.is_loopback:
            result.append(text)
    return result


# ------------------------------------------------------------
# Eigenes Client-Zertifikat
# ------------------------------------------------------------

def _build_certificate(hostname: str, addresses: List[str]):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"USVMonitor@{hostname}"),
                         x509.NameAttribute(NameOID.ORGANIZATION_NAME, "USV Monitor")])
    # 16 Byte, positiv: oberstes Bit 0, nächstes Bit 1 (sonst wäre die DER-Kodierung kürzer)
    serial = secrets.randbits(126) | (1 << 126)
    now = datetime.datetime.now(datetime.timezone.utc)
    ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    names = [x509.UniformResourceIdentifier(application_uri(hostname)), x509.DNSName(hostname)]
    names += [x509.IPAddress(ipaddress.ip_address(a)) for a in addresses]
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(subject).public_key(key.public_key()).serial_number(serial)
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365 * VALID_YEARS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(ski, critical=False)
        .add_extension(x509.AuthorityKeyIdentifier(key_identifier=ski.digest,
                                                   authority_cert_issuer=[x509.DirectoryName(subject)],
                                                   authority_cert_serial_number=serial), critical=False)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=True,
                                     data_encipherment=True, key_agreement=False, key_cert_sign=True,
                                     crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]),
                       critical=True)
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, certificate


def self_check(certificate) -> Optional[bool]:
    """Entspricht `openssl verify -CAfile c.pem -purpose sslclient c.pem`; None = openssl nicht vorhanden."""
    openssl = shutil.which("openssl")
    if not openssl:
        return None
    with tempfile.TemporaryDirectory() as folder:
        pem = Path(folder) / "c.pem"
        pem.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        result = subprocess.run([openssl, "verify", "-CAfile", str(pem), "-purpose", "sslclient", str(pem)],
                                capture_output=True, text=True, errors="replace", timeout=15)
    if result.returncode != 0:
        logger.error(f"❌ OPC-UA-Client-Zertifikat besteht die Selbstprüfung nicht: {result.stdout} {result.stderr}")
    return result.returncode == 0


def _write_private(path: Path, data: bytes) -> None:
    path.unlink(missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def _replace_reason() -> Optional[str]:
    """Warum das vorhandene Zertifikat nicht mehr passt (None = es passt)."""
    if not certificate_path().exists() or not key_path().exists():
        return "missing"
    certificate = x509.load_der_x509_certificate(certificate_path().read_bytes())
    if certificate.not_valid_after_utc <= datetime.datetime.now(datetime.timezone.utc):
        return "expired"
    san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    if application_uri() not in san.get_values_for_type(x509.UniformResourceIdentifier):
        return "hostname"
    return None


def ensure_client_certificate() -> Dict[str, Any]:
    """Erzeugt das Zertifikat bei Bedarf; liefert die Beschreibung für die Oberfläche."""
    with _lock:
        reason = _replace_reason()
        if reason:
            own = certificate_path().parent
            own.mkdir(parents=True, exist_ok=True)
            previous = None
            if certificate_path().exists():
                previous = sha1_of(certificate_path().read_bytes())
            key, certificate = _build_certificate(socket.gethostname(), _ipv4_addresses())
            check = self_check(certificate)
            if check is False:
                raise RuntimeError("OPC-UA-Client-Zertifikat besteht die Selbstprüfung nicht")
            _write_private(key_path(), key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                         serialization.NoEncryption()))
            certificate_path().write_bytes(certificate.public_bytes(serialization.Encoding.DER))
            if previous:
                _own("previous.txt").write_text(f"{reason} {previous}", encoding="ascii")
            logger.warning(f"🔐 OPC-UA-Client-Zertifikat erzeugt ({reason}), SHA-1 {sha1_of(certificate_path().read_bytes())}")
        info = describe_certificate(certificate_path().read_bytes())
        info["application_uri"] = application_uri()
        notice = _own("previous.txt")
        if notice.exists():
            info["replaced_reason"], info["replaced_sha1"] = notice.read_text(encoding="ascii").split()
        return info


def client_certificate_der() -> bytes:
    ensure_client_certificate()
    return certificate_path().read_bytes()


# ------------------------------------------------------------
# Beschreibung von Zertifikaten
# ------------------------------------------------------------

def first_certificate(data: bytes) -> bytes:
    """Server dürfen eine Kette liefern (aneinandergehängtes DER); gültig ist das erste Zertifikat."""
    if len(data) < 4 or data[0] != 0x30:
        return data
    if data[1] < 0x80:
        return data[:2 + data[1]]
    size = data[1] & 0x7F
    return data[:2 + size + int.from_bytes(data[2:2 + size], "big")]


def sha1_of(der: bytes) -> str:
    certificate = x509.load_der_x509_certificate(der)
    return certificate.fingerprint(hashes.SHA1()).hex().upper()


def describe_certificate(der: bytes) -> Dict[str, Any]:
    certificate = x509.load_der_x509_certificate(der)
    public_key = certificate.public_key()
    bits = getattr(public_key, "key_size", 0)
    algorithm = certificate.signature_hash_algorithm.name if certificate.signature_hash_algorithm else "?"
    return {"subject": certificate.subject.rfc4514_string(),
            "sha1": sha1_of(der),
            "not_before": certificate.not_valid_before_utc.date().isoformat(),
            "not_after": certificate.not_valid_after_utc.date().isoformat(),
            "signature": algorithm.upper(),
            "key_bits": bits,
            "weak": algorithm.lower() == "sha1" or bits < 2048}


# ------------------------------------------------------------
# Vertraute Server-Zertifikate
# ------------------------------------------------------------

def trusted_path(der: bytes) -> Path:
    return pki_dir() / "trusted" / f"{sha1_of(der)}.der"


def is_trusted(der: bytes) -> bool:
    path = trusted_path(der)
    return path.exists() and path.read_bytes() == der      # ganzes Zertifikat vergleichen, nicht nur SHA-1


def trust(der: bytes) -> Dict[str, Any]:
    path = trusted_path(der)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(der)
    (pki_dir() / "rejected" / path.name).unlink(missing_ok=True)
    logger.warning(f"🔐 OPC-UA-Server-Zertifikat vertraut: SHA-1 {sha1_of(der)}")
    return describe_certificate(der)


def remember_rejected(der: bytes) -> None:
    path = pki_dir() / "rejected" / f"{sha1_of(der)}.der"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(der)


def list_trusted() -> List[Dict[str, Any]]:
    folder = pki_dir() / "trusted"
    if not folder.exists():
        return []
    return [describe_certificate(path.read_bytes()) for path in sorted(folder.glob("*.der"))]


def remove_trusted(sha1: str) -> bool:
    if not _SHA1_RE.match(sha1):
        return False
    path = pki_dir() / "trusted" / f"{sha1}.der"
    if not path.exists():
        return False
    path.unlink()
    return True
