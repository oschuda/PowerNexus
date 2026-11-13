#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
E-Mail-Einstellungen (notifications.yaml)

    enabled: true
    transport: graph              # graph | smtp_oauth | smtp
    sender: powernexus@firma.de
    dashboard_url: http://usv.firma.de:5000
    m365:
      tenant_id: …
      client_id: …
      auth: secret                # secret | certificate
      secret: <Fernet-Token>
      secret_expires: 2027-03-31
      certificate: <Fernet-Token> # PFX, Base64
      certificate_password: <Fernet-Token>
      certificate_expires: 2027-03-31
      certificate_thumbprint: AB12…
    smtp: {host: relay.firma.de, port: 587, security: starttls, username: "", password: <Fernet-Token>}
    lists:
      - {name: it-team, recipients: [a@firma.de], language: de, events: [POWER_FAILURE]}

Geheimnisse liegen nur verschlüsselt in der Datei (Rechte 600) und werden nie über die API ausgeliefert.
"""

import base64
import datetime
import logging
import os
import re
import tempfile
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

import i18n
import secret_store
from automation.policies import CANCEL_EVENTS, TRIGGER_EVENTS
from automation.targets import ValidationError

logger = logging.getLogger(__name__)

TRANSPORTS = ("graph", "smtp_oauth", "smtp")
M365_AUTH = ("secret", "certificate")
SMTP_SECURITY = ("starttls", "ssl", "none")
MAIL_EVENTS = TRIGGER_EVENTS + CANCEL_EVENTS

_ADDRESS_RE = re.compile(r"^[^@\s,;<>\"']+@[^@\s,;<>\"']+\.[^@\s,;<>\"']+$")
_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
_TENANT_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")          # GUID oder firma.onmicrosoft.com
_HOST_RE = re.compile(r"^[A-Za-z0-9.:\-\[\]]{1,253}$")
_LIST_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,62}$")

DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "transport": "graph",
    "sender": "",
    "dashboard_url": "",
    "m365": {"tenant_id": "", "client_id": "", "auth": "secret", "secret": None, "secret_expires": "",
             "certificate": None, "certificate_password": None, "certificate_expires": "",
             "certificate_thumbprint": ""},
    "smtp": {"host": "", "port": 587, "security": "starttls", "username": "", "password": None},
    "lists": [],
    "last_expiry_warning": "",
}


def _address(value: Any) -> str:
    text = str(value or "").strip()
    if not _ADDRESS_RE.match(text):
        raise ValidationError("mail.error.address", address=text or "-")
    return text


def _date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.date.fromisoformat(text).isoformat()
    except ValueError:
        raise ValidationError("mail.error.date")


def _read_pfx(data_b64: str, password: Optional[str]) -> Dict[str, str]:
    """Prüft eine PFX-Datei; liefert Ablaufdatum und Thumbprint des Zertifikats."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.serialization import pkcs12
    try:
        key, cert, _ = pkcs12.load_key_and_certificates(base64.b64decode(data_b64),
                                                        password.encode("utf-8") if password else None)
    except Exception:
        raise ValidationError("mail.error.certificate")
    if key is None or cert is None:
        raise ValidationError("mail.error.certificate")
    return {"certificate_expires": cert.not_valid_after_utc.date().isoformat(),
            "certificate_thumbprint": cert.fingerprint(hashes.SHA1()).hex().upper()}


def _build_m365(data: Dict[str, Any], old: Dict[str, Any], required: bool) -> Dict[str, Any]:
    result = deepcopy(old)
    result["tenant_id"] = str(data.get("tenant_id", old["tenant_id"]) or "").strip()
    result["client_id"] = str(data.get("client_id", old["client_id"]) or "").strip()
    result["auth"] = data.get("auth", old["auth"])
    result["secret_expires"] = _date(data.get("secret_expires", old["secret_expires"]))
    if result["auth"] not in M365_AUTH:
        raise ValidationError("mail.error.m365_auth")
    if data.get("clear_secret"):
        result["secret"] = None
    elif data.get("secret"):
        result["secret"] = secret_store.encrypt(str(data["secret"]))
    if data.get("clear_certificate"):
        result.update(certificate=None, certificate_password=None, certificate_expires="", certificate_thumbprint="")
    elif data.get("certificate"):
        password = str(data.get("certificate_password") or "")
        result.update(_read_pfx(str(data["certificate"]), password))
        result["certificate"] = secret_store.encrypt(str(data["certificate"]))
        result["certificate_password"] = secret_store.encrypt(password) if password else None
    if required:
        if not _TENANT_RE.match(result["tenant_id"]):
            raise ValidationError("mail.error.tenant")
        if not _GUID_RE.match(result["client_id"]):
            raise ValidationError("mail.error.client")
        if result["auth"] == "secret" and not result["secret"]:
            raise ValidationError("mail.error.secret_missing")
        if result["auth"] == "certificate" and not result["certificate"]:
            raise ValidationError("mail.error.certificate_missing")
    return result


def _build_smtp(data: Dict[str, Any], old: Dict[str, Any], required: bool) -> Dict[str, Any]:
    result = deepcopy(old)
    result["host"] = str(data.get("host", old["host"]) or "").strip()
    result["security"] = data.get("security", old["security"])
    result["username"] = str(data.get("username", old["username"]) or "").strip()
    try:
        result["port"] = int(data.get("port") or old["port"])
    except (TypeError, ValueError):
        raise ValidationError("mail.error.port")
    if not 1 <= result["port"] <= 65535:
        raise ValidationError("mail.error.port")
    if result["security"] not in SMTP_SECURITY:
        raise ValidationError("mail.error.security")
    if data.get("clear_password"):
        result["password"] = None
    elif data.get("password"):
        result["password"] = secret_store.encrypt(str(data["password"]))
    if required:
        if not _HOST_RE.match(result["host"]):
            raise ValidationError("mail.error.host")
        if result["security"] == "none" and (result["username"] or result["password"]):
            raise ValidationError("mail.error.plain_credentials")    # nie Zugangsdaten im Klartext
    return result


def _build_lists(raw: Any) -> List[Dict[str, Any]]:
    lists, seen = [], set()
    for item in raw or []:
        name = str(item.get("name") or "").strip()
        if not _LIST_NAME_RE.match(name):
            raise ValidationError("mail.error.list_name")
        if name in seen:
            raise ValidationError("mail.error.list_duplicate", name=name)
        seen.add(name)
        recipients = item.get("recipients") or []
        if isinstance(recipients, str):
            recipients = re.split(r"[\s,;]+", recipients)
        recipients = [_address(r) for r in recipients if str(r).strip()]
        if not recipients:
            raise ValidationError("mail.error.list_empty", name=name)
        language = item.get("language", i18n.DEFAULT)
        if language not in i18n.SUPPORTED:
            raise ValidationError("mail.error.language")
        events = [e for e in item.get("events") or [] if e]
        if any(e not in MAIL_EVENTS for e in events):
            raise ValidationError("mail.error.events")
        lists.append({"name": name, "recipients": recipients, "language": language, "events": events})
    return lists


def build_settings(data: Dict[str, Any], existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Prüft API-Daten. Leere Geheimnis-Felder lassen den gespeicherten Wert unverändert."""
    old = deepcopy(existing) if existing else deepcopy(DEFAULTS)
    transport = data.get("transport", old["transport"])
    if transport not in TRANSPORTS:
        raise ValidationError("mail.error.transport")
    enabled = bool(data.get("enabled", old["enabled"]))
    sender = str(data.get("sender", old["sender"]) or "").strip()
    if sender or enabled:
        _address(sender)
    url = str(data.get("dashboard_url", old["dashboard_url"]) or "").strip()
    if url and not re.match(r"^https?://[^\s]+$", url):
        raise ValidationError("mail.error.url")
    return {
        "enabled": enabled,
        "transport": transport,
        "sender": sender,
        "dashboard_url": url,
        "m365": _build_m365(data.get("m365") or {}, old["m365"], required=enabled and transport != "smtp"),
        "smtp": _build_smtp(data.get("smtp") or {}, old["smtp"], required=enabled and transport == "smtp"),
        "lists": _build_lists(data["lists"]) if "lists" in data else old["lists"],
        "last_expiry_warning": old.get("last_expiry_warning", ""),
    }


def validate_for_sending(settings: Dict[str, Any]) -> None:
    """Vollständigkeit prüfen (auch wenn der Versand noch nicht eingeschaltet ist, z. B. beim Test)."""
    build_settings({"enabled": True}, settings)


def expiry_date(settings: Dict[str, Any]) -> str:
    """Ablaufdatum der Microsoft-365-Anmeldung, die gerade verwendet wird ('' = keins bekannt)."""
    if settings.get("transport") == "smtp":
        return ""
    m365 = settings["m365"]
    return m365["certificate_expires"] if m365["auth"] == "certificate" else m365["secret_expires"]


def public_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Darstellung für die API: Geheimnisse nur als „vorhanden“."""
    result = deepcopy(settings)
    result.pop("last_expiry_warning", None)
    m365, smtp = result["m365"], result["smtp"]
    m365["has_secret"] = bool(m365.pop("secret"))
    m365["has_certificate"] = bool(m365.pop("certificate"))
    m365.pop("certificate_password")
    smtp["has_password"] = bool(smtp.pop("password"))
    return result


class MailStore:
    """Liest und schreibt notifications.yaml (atomar, mit Sicherungskopie, Rechte 600)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._settings = deepcopy(DEFAULTS)
        self.load()

    def load(self) -> None:
        with self._lock:
            self._settings = deepcopy(DEFAULTS)
            if not self.path.exists():
                return
            with open(self.path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            for key, value in raw.items():
                if isinstance(value, dict) and isinstance(self._settings.get(key), dict):
                    self._settings[key].update(value)
                else:
                    self._settings[key] = value

    def get(self) -> Dict[str, Any]:
        with self._lock:
            return deepcopy(self._settings)

    def save(self, settings: Dict[str, Any]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                self.path.replace(self.path.with_suffix(self.path.suffix + ".bak"))
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".notifications-", suffix=".yaml")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.safe_dump(settings, f, allow_unicode=True, sort_keys=False)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
            self._settings = deepcopy(settings)

    def list_names(self) -> List[str]:
        with self._lock:
            return [item["name"] for item in self._settings["lists"]]
