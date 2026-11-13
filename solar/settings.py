#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PV-Anbindung per MQTT (solar.yaml), Standard: aus

    enabled: true
    host: mqtt.example.lan          # Broker, Name oder IP-Adresse
    port: 1883
    tls: false                      # true: MQTT über TLS (üblich Port 8883)
    tls_verify: true                # Zertifikat des Brokers prüfen
    username: powernexus
    password: <verschlüsselt>
    discovery_prefix: homeassistant
    device: ""                      # nur Sensoren dieses Geräts (Name oder Kennung), leer = alle
    mapping: {ac_power_w: sma/inverter/ac_power}    # Feld → state_topic, übersteuert die Erkennung
    ups_profile: ""                 # USV für den Vergleich PV ↔ Netz
    sample_seconds: 60

Das Passwort liegt nur verschlüsselt vor (secret_store) und verlässt den Server nicht.
"""

import ipaddress
import os
import re
import tempfile
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List

import yaml

import secret_store
from automation.targets import ValidationError
from solar.discovery import FIELDS

DEFAULTS: Dict[str, Any] = {
    "enabled": False, "host": "", "port": 1883, "tls": False, "tls_verify": True, "username": "", "password": None,
    "discovery_prefix": "homeassistant", "device": "", "mapping": {}, "ups_profile": "", "sample_seconds": 60,
}
_HOST_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")
_PREFIX_RE = re.compile(r"^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$")
_TOPIC_RE = re.compile(r"^[^#+\x00]{1,200}$")


def _valid_host(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return bool(_HOST_RE.match(host))


def build_settings(data: Dict[str, Any], existing: Dict[str, Any], known_profiles: List[str]) -> Dict[str, Any]:
    result = deepcopy(existing)
    for key in ("enabled", "tls", "tls_verify"):
        if key in data:
            result[key] = bool(data[key])
    if "host" in data:
        result["host"] = str(data["host"] or "").strip()
    if result["host"] and not _valid_host(result["host"]):
        raise ValidationError("solar.error.host")
    try:
        result["port"] = int(data.get("port") or result["port"])
        result["sample_seconds"] = int(data.get("sample_seconds") or result["sample_seconds"])
    except (TypeError, ValueError):
        raise ValidationError("solar.error.port")
    if not 1 <= result["port"] <= 65535:
        raise ValidationError("solar.error.port")
    if not 10 <= result["sample_seconds"] <= 3600:
        raise ValidationError("solar.error.sample")
    if "username" in data:
        result["username"] = str(data["username"] or "").strip()[:100]
    if data.get("password"):
        result["password"] = secret_store.encrypt(str(data["password"]))
    if data.get("clear_password"):
        result["password"] = None
    if "discovery_prefix" in data:
        result["discovery_prefix"] = str(data["discovery_prefix"] or "").strip().strip("/") or "homeassistant"
    if not _PREFIX_RE.match(result["discovery_prefix"]):
        raise ValidationError("solar.error.prefix")
    if "device" in data:
        result["device"] = str(data["device"] or "").strip()[:100]
    if "mapping" in data:
        mapping = {}
        for field, topic in (data["mapping"] or {}).items():
            topic = str(topic or "").strip()
            if not topic:
                continue
            if field not in FIELDS:
                raise ValidationError("solar.error.field", field=str(field)[:40])
            if not _TOPIC_RE.match(topic):
                raise ValidationError("solar.error.topic", topic=topic[:60])
            mapping[field] = topic
        result["mapping"] = mapping
    if "ups_profile" in data:
        result["ups_profile"] = str(data["ups_profile"] or "")
    if result["ups_profile"] and result["ups_profile"] not in known_profiles:
        raise ValidationError("solar.error.ups", ups=result["ups_profile"])
    if result["enabled"] and not result["host"]:
        raise ValidationError("solar.error.host_missing")
    return result


def public_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    result = deepcopy(settings)
    result["has_password"] = bool(result.pop("password"))
    return result


class SolarSettingsStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._settings = deepcopy(DEFAULTS)
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            self._settings.update({key: raw[key] for key in DEFAULTS if key in raw})

    def get(self) -> Dict[str, Any]:
        with self._lock:
            return deepcopy(self._settings)

    def save(self, settings: Dict[str, Any]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".solar-", suffix=".yaml")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.safe_dump(settings, f, allow_unicode=True, sort_keys=False)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
            self._settings = deepcopy(settings)
