#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Benutzerverwaltung (framework-neutral)

Benutzer liegen als JSON in USV_USERS_FILE (sonst data/users.json):
    {"admin": {"password_hash": "...", "role": "admin", "name": "Administrator"}}

Passwörter werden als werkzeug-Hash gespeichert. Klartext-Einträge ("password")
werden beim ersten erfolgreichen Login automatisch auf Hash umgestellt.
"""

import json
import logging
import os
import secrets
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from werkzeug.security import generate_password_hash, check_password_hash

logger = logging.getLogger(__name__)

ROLES = ('admin', 'operator', 'user', 'guest')

# Nur ohne USV_USERS_FILE aktiv (Entwicklung)
DEMO_USERS = {
    "admin": {"password": "admin123", "role": "admin", "name": "Administrator"},
    "user1": {"password": "user123", "role": "user", "name": "Standard User"},
    "guest": {"password": "guest123", "role": "guest", "name": "Gast"}
}

_lock = threading.RLock()
_DUMMY_HASH = generate_password_hash("kein-konto")

MIN_PASSWORD_LENGTH = 12
COMMON_PASSWORDS = {"123456789012", "1234567890123", "passwortpasswort", "passwordpassword", "administrator", "adminadminadmin",
                    "qwertzuiopasdf", "qwertyuiopasdf", "changemechangeme", "letmeinletmein", "welcomewelcome", "willkommen1234"}


def users_path() -> Path:
    configured = os.environ.get('USV_USERS_FILE')
    return Path(configured) if configured else Path(__file__).parent / 'data' / 'users.json'


def initial_password_path() -> Path:
    return users_path().with_name('initial-admin-password.txt')


def demo_active() -> bool:
    """Demo-Benutzer gelten nur nach ausdrücklicher Freigabe für die Entwicklung, nie von selbst."""
    return os.environ.get('USV_DEMO_USERS', '').lower() in ('1', 'true', 'yes', 'on')


def load_users() -> Dict[str, Dict[str, Any]]:
    """Lädt die Benutzer; beim ersten Start wird ein Admin mit zufälligem Einmal-Passwort angelegt."""
    if demo_active():
        logger.warning("⚠️ USV_DEMO_USERS gesetzt: Demo-Benutzer aktiv. Nur für die Entwicklung!")
        return {name: dict(data) for name, data in DEMO_USERS.items()}
    path = users_path()
    if path.is_file():
        with open(path, encoding='utf-8') as f:
            users = json.load(f)
        logger.info(f"👤 {len(users)} Benutzer aus {path} geladen")
        return users
    return _create_initial_admin()


def _create_initial_admin() -> Dict[str, Dict[str, Any]]:
    password = secrets.token_urlsafe(16)
    users: Dict[str, Dict[str, Any]] = {"admin": {"role": "admin", "name": "Administrator", "must_change_password": True}}
    set_password(users["admin"], password)
    users_path().parent.mkdir(parents=True, exist_ok=True)
    save_users(users)
    target = initial_password_path()
    target.unlink(missing_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(password + "\n")
    logger.warning(f"🔑 Erster Start: Admin-Konto angelegt. Benutzer: admin, Einmal-Passwort in {target}")
    return users


def validate_password(password: str, username: str = "") -> Optional[Tuple[str, Dict[str, Any]]]:
    """Prüft die Passwortregeln; gibt (Katalogschlüssel, Parameter) zurück, wenn sie verletzt sind."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return "password.too_short", {"min": MIN_PASSWORD_LENGTH}
    if username and username.lower() in password.lower():
        return "password.is_username", {}
    if password.lower() in COMMON_PASSWORDS or len(set(password)) < 4:
        return "password.common", {}
    return None


def save_users(users: Dict[str, Dict[str, Any]]) -> None:
    """Schreibt die Benutzer atomar in die Benutzerdatei (Rechte 600). Demo-Benutzer werden nie gespeichert."""
    if demo_active():
        return
    users_file = str(users_path())
    with _lock:
        directory = os.path.dirname(os.path.abspath(users_file))
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix='.users-', suffix='.json')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(users, f, indent=2, ensure_ascii=False)
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, users_file)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise


def set_password(user: Dict[str, Any], password: str) -> None:
    """Speichert nur den Hash, nie das Klartext-Passwort."""
    user['password_hash'] = generate_password_hash(password)
    user.pop('password', None)


def verify_password(users: Dict[str, Dict[str, Any]], username: str, password: str) -> bool:
    """Prüft das Passwort; Klartext-Einträge werden beim ersten Login auf Hash umgestellt."""
    with _lock:
        user = users.get(username)
        if not user or not password:
            check_password_hash(_DUMMY_HASH, password or "")     # gleiche Laufzeit wie bei vorhandenem Konto
            return False
        if 'password_hash' in user:
            return check_password_hash(user['password_hash'], password)
        if user.get('password') == password:
            set_password(user, password)
            try:
                save_users(users)
                logger.info(f"🔐 Passwort von '{username}' auf Hash umgestellt")
            except Exception as e:
                logger.error(f"❌ Benutzerdatei konnte nicht gespeichert werden: {e}")
            return True
        return False


def add_user(users, username: str, password: str, role: str, name: str) -> None:
    with _lock:
        # Der Admin kennt das Passwort: Der Benutzer legt beim ersten Login ein eigenes fest
        users[username] = {"role": role, "name": name or username, "must_change_password": True}
        set_password(users[username], password)
        save_users(users)


def edit_user(users, username: str, name=None, role=None, password=None, lang=None) -> None:
    with _lock:
        if name:
            users[username]['name'] = name
        if role in ROLES:
            users[username]['role'] = role
        if password:
            set_password(users[username], password)
            users[username]['must_change_password'] = True
        if lang:
            users[username]['lang'] = lang
        save_users(users)


def change_own_password(users, username: str, password: str) -> None:
    with _lock:
        set_password(users[username], password)
        users[username].pop('must_change_password', None)
        save_users(users)
    initial_password_path().unlink(missing_ok=True)


def delete_user(users, username: str) -> None:
    with _lock:
        del users[username]
        save_users(users)
