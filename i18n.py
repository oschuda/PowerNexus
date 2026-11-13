#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mehrsprachigkeit (Deutsch, Englisch)

Übersetzungen liegen als flache JSON-Kataloge in i18n/<sprache>.json
(Schlüssel z. B. "policies.title"). Platzhalter im Format {name}.

Die Sprache einer Anfrage wird in einer ContextVar gehalten (gesetzt in app.py
vor jedem Request); Templates nutzen t('schlüssel') und lang().
"""

import json
from contextvars import ContextVar
from pathlib import Path
from typing import Dict, Optional

SUPPORTED = ("de", "en")
DEFAULT = "de"
_DIR = Path(__file__).parent / "i18n"
_catalogs: Dict[str, Dict[str, str]] = {}
_current: ContextVar[str] = ContextVar("usv_lang", default=DEFAULT)


def catalog(lang: str) -> Dict[str, str]:
    lang = lang if lang in SUPPORTED else DEFAULT
    if lang not in _catalogs:
        with open(_DIR / f"{lang}.json", encoding="utf-8") as f:
            _catalogs[lang] = json.load(f)
    return _catalogs[lang]


def t(key: str, lang: Optional[str] = None, **params) -> str:
    """Übersetzung; fehlt der Schlüssel, wird Deutsch und notfalls der Schlüssel selbst verwendet."""
    text = catalog(lang or _current.get()).get(key) or catalog(DEFAULT).get(key) or key
    try:
        return text.format(**params) if params else text
    except (KeyError, IndexError):
        return text


def set_current(lang: str) -> None:
    _current.set(lang if lang in SUPPORTED else DEFAULT)


def current() -> str:
    return _current.get()


def negotiate(accept_language: Optional[str]) -> str:
    """Wählt aus dem Accept-Language-Header die erste unterstützte Sprache."""
    for part in (accept_language or "").split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in SUPPORTED:
            return code
    return DEFAULT
