#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HTTPS für das Dashboard

Zertifikat: USV_TLS_CERT und USV_TLS_KEY (eigenes Zertifikat, z. B. interne CA), sonst selbstsigniert in USV_TLS_DIR.
Der Klartext-Port antwortet nur noch mit einer Umleitung auf HTTPS. Mit USV_ALLOW_HTTP=true läuft alles unverschlüsselt
(nur für Entwicklung und Tests).
"""

import datetime
import ipaddress
import logging
import os
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import List, Optional, Tuple

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

logger = logging.getLogger(__name__)

VALID_DAYS = 825
RENEW_DAYS = 30
_HOST_RE = re.compile(r"^(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]{1,253})(:\d{1,5})?$")


def http_allowed() -> bool:
    return os.environ.get('USV_ALLOW_HTTP', '').lower() in ('1', 'true', 'yes', 'on')


def own_certificate_configured() -> bool:
    return bool(os.environ.get('USV_TLS_CERT') and os.environ.get('USV_TLS_KEY'))


def _tls_dir() -> Path:
    configured = os.environ.get('USV_TLS_DIR')
    return Path(configured) if configured else Path(__file__).resolve().parent.parent / 'data' / 'tls'


def _ipv4_addresses() -> List[str]:
    from automation.opcua_pki import _ipv4_addresses as addresses
    return addresses()


def _needs_renewal(path: Path, hostname: str) -> bool:
    if not path.exists():
        return True
    certificate = x509.load_pem_x509_certificate(path.read_bytes())
    remaining = certificate.not_valid_after_utc - datetime.datetime.now(datetime.timezone.utc)
    if remaining.days < RENEW_DAYS:
        return True
    san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return hostname not in san.get_values_for_type(x509.DNSName)


def _create_self_signed(cert_path: Path, key_path: Path, hostname: str) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname), x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Evolved PowerNexus")])
    now = datetime.datetime.now(datetime.timezone.utc)
    names: List[x509.GeneralName] = [x509.DNSName(hostname), x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    names += [x509.IPAddress(ipaddress.ip_address(a)) for a in _ipv4_addresses()]
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.unlink(missing_ok=True)
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))


def fingerprint(cert_path: Path) -> str:
    digest = x509.load_pem_x509_certificate(cert_path.read_bytes()).fingerprint(hashes.SHA256()).hex().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def ensure_certificate() -> Tuple[Path, Path]:
    """Pfade zu Zertifikat und Schlüssel; ein fehlendes oder ablaufendes selbstsigniertes Zertifikat wird neu erzeugt."""
    if own_certificate_configured():
        return Path(os.environ['USV_TLS_CERT']), Path(os.environ['USV_TLS_KEY'])
    folder = _tls_dir()
    cert_path, key_path = folder / 'dashboard.crt', folder / 'dashboard.key'
    hostname = socket.gethostname()
    if _needs_renewal(cert_path, hostname) or not key_path.exists():
        _create_self_signed(cert_path, key_path, hostname)
        logger.warning(f"🔐 Selbstsigniertes HTTPS-Zertifikat erzeugt: {cert_path}")
    logger.info(f"🔐 HTTPS-Zertifikat, SHA-256: {fingerprint(cert_path)}")
    return cert_path, key_path


def redirect_target(host_header: Optional[str], path: str, https_port: int, fallback: str = "localhost") -> str:
    """Ziel der Umleitung. Ein ungültiger Host-Kopf wird nicht übernommen."""
    host = host_header or ""
    if not _HOST_RE.match(host):
        host = fallback
    host = re.sub(r":\d+$", "", host)
    if not path.startswith("/") or path.startswith("//"):
        path = "/"
    suffix = "" if https_port == 443 else f":{https_port}"
    return f"https://{host}{suffix}{path}"


def start_redirect_server(host: str, port: int, https_port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        server_version = "USV"
        sys_version = ""

        def _redirect(self) -> None:
            self.send_response(308)
            self.send_header("Location", redirect_target(self.headers.get("Host"), self.path, https_port))
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = _redirect

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="https-redirect").start()
    logger.info(f"↪️ Port {port} leitet auf HTTPS-Port {https_port} um")
    return server
