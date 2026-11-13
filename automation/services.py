#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Optionale Dienste (services.yaml), Standard: beide aus

    self_shutdown: false            # USV-Server darf sich per Regel selbst herunterfahren
    nut: {enabled: false, port: 3493, networks: [172.16.11.0/24], username: usvclient,
          password_hash: <Hash>, ups: [APC-SmartUPS]}

Das Passwort der NUT-Clients liegt nur als Hash vor; NUT überträgt es im Klartext und es wird nur verglichen.
"""

import asyncio
import ipaddress
import logging
import os
import re
import tempfile
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml
from werkzeug.security import generate_password_hash

from automation.nut_server import NutServer, nut_name
from automation.targets import ValidationError

logger = logging.getLogger(__name__)

DEFAULTS: Dict[str, Any] = {
    "self_shutdown": False,
    "nut": {"enabled": False, "port": 3493, "networks": [], "username": "", "password_hash": None, "ups": []},
}
_USER_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")

_current: Optional["ServiceManager"] = None


def current() -> Optional["ServiceManager"]:
    """Die Dienste des laufenden Programms (Treiber prüfen hier die Freigabe unmittelbar vor der Aktion)."""
    return _current


def build_services(data: Dict[str, Any], existing: Dict[str, Any], known_profiles: List[str]) -> Dict[str, Any]:
    result = deepcopy(existing)
    if "self_shutdown" in data:
        result["self_shutdown"] = bool(data["self_shutdown"])
    raw, nut = data.get("nut") or {}, result["nut"]
    if "enabled" in raw:
        nut["enabled"] = bool(raw["enabled"])
    try:
        nut["port"] = int(raw.get("port") or nut["port"])
    except (TypeError, ValueError):
        raise ValidationError("services.error.port")
    if not 1024 <= nut["port"] <= 65535:
        raise ValidationError("services.error.port")
    if "networks" in raw:
        items = raw["networks"]
        items = re.split(r"[\s,;]+", items) if isinstance(items, str) else items
        networks = []
        for item in items:
            if not str(item).strip():
                continue
            try:
                network = ipaddress.ip_network(str(item).strip(), strict=False)
            except ValueError:
                raise ValidationError("services.error.network", network=str(item))
            if network.version != 4 or network.prefixlen == 0:
                raise ValidationError("services.error.network", network=str(item))
            networks.append(str(network))
        nut["networks"] = networks
    if "username" in raw:
        nut["username"] = str(raw["username"] or "").strip()
    if raw.get("password"):
        nut["password_hash"] = generate_password_hash(str(raw["password"]))
    if "ups" in raw:
        nut["ups"] = [str(key) for key in raw["ups"] or []]
    unknown = [key for key in nut["ups"] if key not in known_profiles]
    if unknown:
        raise ValidationError("services.error.ups", ups=", ".join(unknown))
    if nut["enabled"]:
        if not nut["networks"]:
            raise ValidationError("services.error.networks_missing")
        if not nut["ups"]:
            raise ValidationError("services.error.ups_missing")
        if not _USER_RE.match(nut["username"]) or not nut["password_hash"]:
            raise ValidationError("services.error.credentials_missing")
    return result


def public_services(settings: Dict[str, Any]) -> Dict[str, Any]:
    result = deepcopy(settings)
    result["nut"]["has_password"] = bool(result["nut"].pop("password_hash"))
    return result


class ServiceManager:
    def __init__(self, path: Path, loop: asyncio.AbstractEventLoop,
                 data_provider: Callable[[str], Optional[Dict[str, Any]]],
                 tokens_of: Callable[[Optional[Dict[str, Any]]], str], description_of: Callable[[str], str],
                 nut_host: str = "0.0.0.0"):
        global _current
        self.path = Path(path)
        self.loop = loop
        self._data, self._tokens_of, self._description_of = data_provider, tokens_of, description_of
        self._nut_host = nut_host
        self._lock = threading.RLock()
        self._settings = deepcopy(DEFAULTS)
        self.nut: Optional[NutServer] = None
        self.nut_error = ""
        self._load()
        _current = self

    def _load(self) -> None:
        if not self.path.exists():
            return
        with open(self.path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        self._settings["self_shutdown"] = bool(raw.get("self_shutdown", False))
        self._settings["nut"].update(raw.get("nut") or {})

    def get(self) -> Dict[str, Any]:
        with self._lock:
            return deepcopy(self._settings)

    @property
    def self_shutdown_allowed(self) -> bool:
        return bool(self._settings["self_shutdown"])

    def save(self, settings: Dict[str, Any]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".services-", suffix=".yaml")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.safe_dump(settings, f, allow_unicode=True, sort_keys=False)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
            self._settings = deepcopy(settings)

    async def apply(self) -> None:
        """NUT-Server gemäß Einstellung (neu) starten. Läuft im Event-Loop der Automatisierung."""
        previous_fsd = dict(self.nut._fsd) if self.nut else {}
        if self.nut:
            await self.nut.stop()
            self.nut = None
        self.nut_error = ""
        config = self.get()["nut"]
        if not config["enabled"]:
            return
        server = NutServer(config, self._data, self._tokens_of, self._description_of, host=self._nut_host)
        server._fsd.update(previous_fsd)
        try:
            await server.start()
            self.nut = server
        except OSError as e:
            self.nut_error = str(e)[:200]
            logger.error(f"❌ NUT-Server konnte nicht starten: {e}")

    def start(self) -> None:
        asyncio.run_coroutine_threadsafe(self.apply(), self.loop)

    def stop(self, timeout: float = 3.0) -> None:
        if self.nut:
            asyncio.run_coroutine_threadsafe(self.nut.stop(), self.loop).result(timeout)

    def status(self) -> Dict[str, Any]:
        status = self.nut.status() if self.nut else {"running": False, "clients": 0, "logins": {}, "fsd": []}
        return {**status, "error": self.nut_error}


__all__ = ["ServiceManager", "build_services", "public_services", "current", "nut_name"]
