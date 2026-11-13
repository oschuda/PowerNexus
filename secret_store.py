#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Verschlüsselung von Zugangsdaten (Passwörter, Client-Secrets)

Ein Fernet-Schlüssel in einer Datei (Standard /etc/usv-monitor/secret.key über
USV_SECRET_KEY_FILE, sonst data/secret.key), beim ersten Zugriff erzeugt, Rechte 600.
Gespeichert wird nur das verschlüsselte Token; entschlüsselt wird erst unmittelbar
vor der Verwendung.
"""

import os
import threading
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

_lock = threading.Lock()
_fernet: Optional[Fernet] = None


def _key_file() -> Path:
    configured = os.environ.get("USV_SECRET_KEY_FILE")
    return Path(configured) if configured else Path(__file__).parent / "data" / "secret.key"


def _cipher() -> Fernet:
    global _fernet
    with _lock:
        if _fernet is None:
            path = _key_file()
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(Fernet.generate_key())
            _fernet = Fernet(path.read_bytes().strip())
        return _fernet


def reset_cache() -> None:
    """Für Tests: Schlüssel beim nächsten Zugriff neu laden."""
    global _fernet
    with _lock:
        _fernet = None


def encrypt(plaintext: str) -> str:
    return _cipher().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    try:
        return _cipher().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Gespeichertes Geheimnis kann nicht entschlüsselt werden (anderer Schlüssel?)") from exc
