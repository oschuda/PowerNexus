#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zielrechner (hosts.yaml)

Ein Zielrechner beschreibt, WIE ein Rechner gesteuert wird (Typ = Schnittstelle).
Zugangsdaten werden nur verschlüsselt gespeichert (secret_store) und nie über die
API ausgeliefert.

Format:
    targets:
      - name: srv-db-1
        type: ssh               # ssh | winrm | ipmi | redfish | http | opcua
        address: 10.0.0.5
        port: 22
        username: usvmon
        secret: <Fernet-Token>  # optional
        enabled: true
        timeout_seconds: 30
        description: ""
        options: {sudo: true, host_key: "SHA256:..."}

TLS (redfish, http mit https): Ist options.cert_fingerprint gesetzt, gilt genau dieses Zertifikat
(SHA-256, angeheftet); sonst wird das Zertifikat gegen die System-CA-Liste geprüft. Eine Verbindung
ohne Zertifikatsprüfung gibt es nicht.

Das alte Format (hosts: [{host_type: linux|windows|ipmi, ...}]) wird beim Laden übernommen.
"""

import logging
import os
import re
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

import secret_store

logger = logging.getLogger(__name__)

# Schnittstellen und die dort möglichen Aktionen
TARGET_TYPES: Dict[str, Dict[str, Any]] = {
    "ssh":   {"port": 22,   "actions": ["shutdown", "reboot"]},
    "winrm": {"port": 5986, "actions": ["shutdown", "reboot"]},
    "ipmi":  {"port": 623,  "actions": ["shutdown", "force_off", "power_on"]},
    "redfish": {"port": 443, "actions": ["shutdown", "reboot", "force_off", "power_on"]},
    "http":  {"port": 443,  "actions": ["shutdown"]},
    "opcua": {"port": 4840, "actions": ["shutdown"]},
    # ohne Verbindung: der USV-Server selbst und der eingebaute NUT-Server
    "local": {"port": 0, "actions": ["shutdown"], "virtual": True},
    "nut_fsd": {"port": 0, "actions": ["fsd"], "virtual": True},
}

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
HTTP_AUTH = ("none", "basic", "bearer", "header")
OPCUA_INT_RANGES = {"Int16": (-2**15, 2**15 - 1), "Int32": (-2**31, 2**31 - 1),
                    "UInt16": (0, 2**16 - 1), "UInt32": (0, 2**32 - 1)}
OPCUA_TYPES = ("Boolean", *OPCUA_INT_RANGES, "Float", "String")

_LEGACY_TYPES = {"linux": "ssh", "macos": "ssh", "windows": "winrm", "ipmi": "ipmi"}
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,62}$")
_ADDRESS_RE = re.compile(r"^[A-Za-z0-9.:\-\[\]]{1,253}$")
_SYSTEM_ID_RE = re.compile(r"^[A-Za-z0-9._-]{0,64}$")
_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$")
_NODE_ID_RE = re.compile(r"^(ns=\d+;)?(i=\d+|s=.+|g=[0-9A-Fa-f-]{36}|b=[A-Za-z0-9+/=]+)$")
_STATUS_RE = re.compile(r"^\s*\d{3}(\s*-\s*\d{3})?(\s*,\s*\d{3}(\s*-\s*\d{3})?)*\s*$")


class ValidationError(ValueError):
    """Ungültige Eingabe; message ist ein i18n-Schlüssel, params die Platzhalter."""

    def __init__(self, message: str, **params):
        super().__init__(message)
        self.params = params


@dataclass
class Target:
    name: str
    type: str
    address: str
    port: int = 0
    username: str = ""
    secret: Optional[str] = None
    enabled: bool = True
    timeout_seconds: int = 30
    description: str = ""
    options: Dict[str, Any] = field(default_factory=dict)

    @property
    def actions(self) -> List[str]:
        return TARGET_TYPES.get(self.type, {}).get("actions", [])

    def password(self) -> Optional[str]:
        return secret_store.decrypt(self.secret)

    def public(self) -> Dict[str, Any]:
        """Darstellung für die API: ohne Geheimnis."""
        data = asdict(self)
        data.pop("secret")
        data["has_password"] = bool(self.secret)
        data["actions"] = self.actions
        return data


def normalize_fingerprint(value: Any) -> str:
    """SHA-256-Fingerprint vereinheitlichen: 'AB:CD:…' (32 Byte). Leer bleibt leer."""
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"^sha-?256\s*[:=]?\s*", "", text, flags=re.IGNORECASE)
    hex_digits = re.sub(r"[\s:]", "", text)
    if not re.fullmatch(r"[0-9A-Fa-f]{64}", hex_digits):
        raise ValidationError("targets.error.fingerprint")
    hex_digits = hex_digits.upper()
    return ":".join(hex_digits[i:i + 2] for i in range(0, 64, 2))


def parse_headers(text: Any) -> List[List[str]]:
    """Zusätzliche HTTP-Header aus Zeilen 'Name: Wert' (keine Geheimnisse – dafür gibt es auth)."""
    if isinstance(text, list):
        lines = [f"{k}: {v}" for k, v in text]
    else:
        lines = str(text or "").splitlines()
    headers = []
    for line in lines:
        if not line.strip():
            continue
        name, sep, value = line.partition(":")
        name, value = name.strip(), value.strip()
        if not sep or not _HEADER_NAME_RE.match(name) or any(c in value for c in "\r\n"):
            raise ValidationError("targets.error.http_headers")
        headers.append([name, value])
    if len(headers) > 20:
        raise ValidationError("targets.error.http_headers")
    return headers


def status_matches(spec: str, status: int) -> bool:
    """'200-299' oder '200,204' → passt der Statuscode?"""
    for part in spec.split(","):
        low, _, high = part.strip().partition("-")
        if int(low) <= status <= int(high or low):
            return True
    return False


def parse_opcua_value(data_type: str, value: Any) -> Any:
    """Wert aus dem Formular in den OPC-UA-Datentyp wandeln."""
    text = str(value if value is not None else "").strip()
    if data_type == "Boolean":
        if text.lower() in ("true", "1"):
            return True
        if text.lower() in ("false", "0"):
            return False
    elif data_type in OPCUA_INT_RANGES:
        low, high = OPCUA_INT_RANGES[data_type]
        if re.fullmatch(r"[+-]?\d+", text) and low <= int(text) <= high:
            return int(text)
    elif data_type == "Float":
        try:
            number = float(text)
            if number == number and abs(number) != float("inf"):
                return number
        except ValueError:
            pass
    elif data_type == "String" and len(text) <= 1000:
        return text
    raise ValidationError("targets.error.opcua_value", type=data_type)


def _clean_options(target_type: str, options: Dict[str, Any]) -> Dict[str, Any]:
    options = dict(options or {})
    if target_type == "ssh":
        return {"sudo": bool(options.get("sudo", True)),
                "host_key": str(options.get("host_key") or "").strip(),
                "key_path": str(options.get("key_path") or "").strip()}
    if target_type == "winrm":
        transport = options.get("transport", "ntlm")
        if transport not in ("ntlm", "kerberos"):
            raise ValidationError("targets.error.transport")
        return {"transport": transport}
    if target_type == "ipmi":
        return {"interface": "lanplus", "privilege": "OPERATOR"}
    if target_type == "redfish":
        system_id = str(options.get("system_id") or "").strip()
        if not _SYSTEM_ID_RE.match(system_id):
            raise ValidationError("targets.error.system_id")
        return {"system_id": system_id,
                "cert_fingerprint": normalize_fingerprint(options.get("cert_fingerprint"))}
    if target_type == "http":
        scheme = options.get("scheme", "https")
        method = str(options.get("method") or "POST").upper()
        auth = options.get("auth", "none")
        path = str(options.get("path") or "/").strip()
        test_path = str(options.get("test_path") or "").strip()
        expected = str(options.get("expected_status") or "200-299").strip()
        body = str(options.get("body") or "")
        if scheme not in ("https", "http"):
            raise ValidationError("targets.error.http_scheme")
        if method not in HTTP_METHODS:
            raise ValidationError("targets.error.http_method")
        if auth not in HTTP_AUTH:
            raise ValidationError("targets.error.http_auth")
        for value in (path, test_path):
            if value and (not value.startswith("/") or any(c in value for c in " \r\n\t")):
                raise ValidationError("targets.error.http_path")
        if not _STATUS_RE.match(expected):
            raise ValidationError("targets.error.http_status")
        if len(body) > 4000:
            raise ValidationError("targets.error.http_body")
        auth_header = str(options.get("auth_header") or "Authorization").strip()
        if auth == "header" and not _HEADER_NAME_RE.match(auth_header):
            raise ValidationError("targets.error.http_headers")
        return {"scheme": scheme, "method": method, "path": path, "body": body,
                "content_type": str(options.get("content_type") or "application/json").strip()[:100],
                "headers": parse_headers(options.get("headers")),
                "auth": auth, "auth_header": auth_header if auth == "header" else "",
                "expected_status": expected.replace(" ", ""), "test_path": test_path,
                "cert_fingerprint": normalize_fingerprint(options.get("cert_fingerprint")) if scheme == "https" else ""}
    if target_type == "nut_fsd":
        return {"ups": str(options.get("ups") or "").strip()}
    if target_type == "opcua":
        security = options.get("security", "sign_and_encrypt")
        auth = options.get("auth", "anonymous")
        node_id = str(options.get("node_id") or "").strip()
        data_type = options.get("data_type", "Boolean")
        if security not in ("none", "sign_and_encrypt"):
            raise ValidationError("targets.error.opcua_security")
        if auth not in ("anonymous", "user"):
            raise ValidationError("targets.error.opcua_auth")
        if not _NODE_ID_RE.match(node_id):
            raise ValidationError("targets.error.opcua_node")
        if data_type not in OPCUA_TYPES:
            raise ValidationError("targets.error.opcua_type")
        value = str(options.get("value") if options.get("value") is not None else "true").strip()
        parse_opcua_value(data_type, value)
        return {"security": security, "auth": auth, "node_id": node_id, "data_type": data_type, "value": value,
                "verify_write": bool(options.get("verify_write", True))}
    return {}


def build_target(data: Dict[str, Any], existing: Optional[Target] = None) -> Target:
    """Erzeugt/aktualisiert einen Zielrechner aus API-Daten und prüft ihn."""
    name = str(data.get("name", existing.name if existing else "")).strip()
    if not _NAME_RE.match(name):
        raise ValidationError("targets.error.name")
    target_type = data.get("type", existing.type if existing else "")
    if target_type not in TARGET_TYPES:
        raise ValidationError("targets.error.type")
    virtual = TARGET_TYPES[target_type].get("virtual", False)
    address = "localhost" if virtual else str(data.get("address", existing.address if existing else "")).strip()
    if not _ADDRESS_RE.match(address):
        raise ValidationError("targets.error.address")
    try:
        port = int(data.get("port") or (existing.port if existing and existing.type == target_type else 0)
                   or TARGET_TYPES[target_type]["port"])
        timeout = int(data.get("timeout_seconds") or (existing.timeout_seconds if existing else 30))
    except (TypeError, ValueError):
        raise ValidationError("targets.error.number")
    if not (virtual or 1 <= port <= 65535) or not 5 <= timeout <= 600:
        raise ValidationError("targets.error.number")

    secret = existing.secret if existing else None
    if data.get("clear_password"):
        secret = None
    elif data.get("password"):
        secret = secret_store.encrypt(str(data["password"]))

    return Target(
        name=name,
        type=target_type,
        address=address,
        port=port,
        username=str(data.get("username", existing.username if existing else "")).strip(),
        secret=secret,
        enabled=bool(data.get("enabled", existing.enabled if existing else True)),
        timeout_seconds=timeout,
        description=str(data.get("description", existing.description if existing else "")).strip(),
        options=_clean_options(target_type, data.get("options", existing.options if existing else {})),
    )


class TargetStore:
    """Liest und schreibt hosts.yaml (atomar, mit Sicherungskopie)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._targets: Dict[str, Target] = {}
        self.load()

    def load(self) -> None:
        with self._lock:
            self._targets = {}
            if not self.path.exists():
                return
            with open(self.path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            for item in raw.get("targets", []):
                try:
                    self._targets[item["name"]] = Target(**item)
                except Exception as e:
                    logger.error(f"❌ Zielrechner in {self.path} ungültig: {item.get('name')}: {e}")
            for item in raw.get("hosts", []):          # altes Format
                new_type = _LEGACY_TYPES.get(str(item.get("host_type", "")).lower())
                if not new_type or not item.get("name"):
                    logger.warning(f"⚠️ Alter Zielrechner '{item.get('name')}' (Typ {item.get('host_type')}) "
                                   f"nicht übernommen")
                    continue
                self._targets[item["name"]] = Target(
                    name=item["name"], type=new_type, address=item.get("address", ""),
                    port=int(item.get("winrm_port", 5986) if new_type == "winrm" else item.get("port", 22)),
                    username=item.get("username") or "", enabled=True,
                    options=_clean_options(new_type, {"key_path": item.get("ssh_key_path")}),
                )
                logger.info(f"🔁 Zielrechner '{item['name']}' aus altem Format übernommen (Passwort neu eingeben)")

    def save(self) -> None:
        with self._lock:
            data = {"targets": [asdict(t) for t in self._targets.values()]}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                self.path.replace(self.path.with_suffix(self.path.suffix + ".bak"))
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".hosts-", suffix=".yaml")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)

    def all(self) -> List[Target]:
        with self._lock:
            return sorted(self._targets.values(), key=lambda t: t.name.lower())

    def get(self, name: str) -> Optional[Target]:
        with self._lock:
            return self._targets.get(name)

    def upsert(self, target: Target, old_name: Optional[str] = None) -> None:
        with self._lock:
            if old_name and old_name != target.name:
                if target.name in self._targets:
                    raise ValidationError("targets.error.exists", name=target.name)
                self._targets.pop(old_name, None)
            self._targets[target.name] = target
            self.save()

    def delete(self, name: str) -> bool:
        with self._lock:
            if self._targets.pop(name, None) is None:
                return False
            self.save()
            return True
