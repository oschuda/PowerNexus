#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Anmeldung absichern: Bremse gegen Passwortraten und serverseitig widerrufbare Sitzungen

Beides liegt im Arbeitsspeicher. Ein Neustart des Dienstes meldet daher alle Benutzer ab; das ist gewollt,
denn so bleibt eine Sitzung nie länger gültig, als der Server sie kennt.
"""

import secrets
import threading
import time
from typing import Dict, List, Optional, Tuple

MAX_FAILURES_ACCOUNT = 5        # Fehlversuche je Adresse und Benutzer
MAX_FAILURES_ADDRESS = 30       # Fehlversuche je Adresse über alle Benutzer
LOCK_SECONDS = 900


class LoginThrottle:
    """Sperrt nach Fehlversuchen je Adresse. Andere Adressen bleiben frei, damit niemand ein Konto aussperren kann."""

    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: Dict[Tuple[str, str], List[float]] = {}

    def _recent(self, key: Tuple[str, str]) -> List[float]:
        limit = self._clock() - LOCK_SECONDS
        recent = [t for t in self._failures.get(key, []) if t > limit]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def blocked(self, address: str, username: str) -> bool:
        with self._lock:
            return (len(self._recent((address, username))) >= MAX_FAILURES_ACCOUNT
                    or len(self._recent((address, ""))) >= MAX_FAILURES_ADDRESS)

    def failed(self, address: str, username: str) -> None:
        now = self._clock()
        with self._lock:
            for key in ((address, username), (address, "")):
                self._failures.setdefault(key, []).append(now)

    def succeeded(self, address: str, username: str) -> None:
        with self._lock:
            self._failures.pop((address, username), None)


class SessionRegistry:
    """Kennt alle gültigen Sitzungen. Das Cookie allein berechtigt zu nichts."""

    def __init__(self, max_age: int, clock=time.time):
        self._max_age = max_age
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: Dict[str, Tuple[str, float]] = {}

    def create(self, username: str) -> str:
        sid = secrets.token_urlsafe(32)
        with self._lock:
            now = self._clock()
            self._sessions = {k: v for k, v in self._sessions.items() if v[1] > now}
            self._sessions[sid] = (username, now + self._max_age)
        return sid

    def valid(self, sid: Optional[str], username: Optional[str]) -> bool:
        with self._lock:
            entry = self._sessions.get(sid or "")
            return bool(entry and entry[0] == username and entry[1] > self._clock())

    def revoke(self, sid: Optional[str]) -> None:
        with self._lock:
            self._sessions.pop(sid or "", None)

    def revoke_user(self, username: str, keep: Optional[str] = None) -> None:
        with self._lock:
            self._sessions = {k: v for k, v in self._sessions.items() if v[0] != username or k == keep}
