#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Evolved PowerNexus - Litestar ASGI Application (Version: version.py)

Portierung der Flask-Version v20.1.1 (app_flask.py + routes/*) nach Litestar.
Gleiche URLs, gleiche JSON-Antworten, gleiches Login-Verhalten - templates/
und static/ laufen unverändert.

Start:
    python app.py --port 5000
    (oder: uvicorn app:app --host 0.0.0.0 --port 5000 --workers 1)

WICHTIG: Immer genau 1 Worker-Prozess. Polling-Thread, Live-Daten und
Event-Engine leben im Prozess-Speicher.

Umgebungsvariablen:
    USV_SECRET_KEY   Schlüssel für die verschlüsselten Session-Cookies
    USV_USERS_FILE   JSON-Datei mit den Benutzern (siehe users.py)
    HTTPS=on         Session-Cookie nur über HTTPS senden (hinter Reverse-Proxy)
    LOG_LEVEL        Standard: INFO
"""


import argparse
import hashlib
import logging
import os
import secrets
from pathlib import Path

from litestar import Litestar, Request
from litestar.datastructures import MutableScopeHeaders
from litestar.plugins.jinja import JinjaTemplateEngine
from litestar.middleware.session.client_side import CookieBackendConfig
from litestar.static_files import create_static_files_router
from litestar.template.config import TemplateConfig

import i18n
import json
import users as user_store
from version import __version__
from runtime import Runtime
from web import api, audit, automation, management, pages, solar
from web.common import request_language, ApiError, api_error_handler
from web import tls
from web.security import LoginThrottle, SessionRegistry

logging.basicConfig(
    level=os.environ.get('LOG_LEVEL', 'INFO').upper(),
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger("usv")

APP_DIR = Path(__file__).parent.absolute()
SESSION_MAX_AGE = 8 * 3600
CONTENT_SECURITY_POLICY = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
                           "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; "
                           "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


def _session_secret() -> bytes:
    key = os.environ.get('USV_SECRET_KEY')
    if not key:
        logger.warning("⚠️ USV_SECRET_KEY nicht gesetzt - zufälliger Schlüssel, Sessions enden beim Neustart")
        return secrets.token_bytes(32)
    return hashlib.sha256(key.encode('utf-8')).digest()


async def set_request_language(request: Request) -> None:
    """Muss async sein: Synchrone Hooks führt Litestar in einem Thread aus, die ContextVar wäre danach im Handler nicht gesetzt."""
    i18n.set_current(request_language(request))


def _template_globals(engine) -> None:
    env = engine.engine
    env.globals.update(
        version=__version__,
        t=lambda key, **params: i18n.t(key, **params),
        lang=i18n.current,
        i18n_json=lambda: json.dumps(i18n.catalog(i18n.current()), ensure_ascii=False),
    )


def on_startup(app: Litestar) -> None:
    app.state.users = user_store.load_users()
    app.state.login_throttle = LoginThrottle()
    app.state.sessions = SessionRegistry(SESSION_MAX_AGE)
    runtime = Runtime()
    runtime.start()
    app.state.runtime = runtime
    logger.info(f"🚀 Evolved PowerNexus {__version__} (Litestar) bereit")


def on_shutdown(app: Litestar) -> None:
    runtime = getattr(app.state, 'runtime', None)
    if runtime:
        runtime.stop()


async def add_security_headers(message, scope) -> None:
    """Für alle Antworten, auch statische Dateien und Fehler."""
    if message["type"] != "http.response.start":
        return
    headers = MutableScopeHeaders.from_message(message)
    headers["X-Content-Type-Options"] = "nosniff"
    headers["X-Frame-Options"] = "DENY"
    headers["Referrer-Policy"] = "no-referrer"
    headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    if scope["path"].startswith("/api/"):
        headers["Cache-Control"] = "no-store"
    # Ein selbstsigniertes Zertifikat ließe sich mit HSTS im Browser nicht mehr bestätigen
    if tls.own_certificate_configured():
        headers["Strict-Transport-Security"] = "max-age=31536000"


def create_app() -> Litestar:
    session_config = CookieBackendConfig(
        secret=_session_secret(),
        key="usv_session",
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=not tls.http_allowed(),
    )
    return Litestar(
        route_handlers=[
            *pages.route_handlers,
            api.ApiController,
            *management.route_handlers,
            *automation.route_handlers,
            *audit.route_handlers,
            *solar.route_handlers,
            create_static_files_router(path="/static", directories=[APP_DIR / "static"]),
        ],
        middleware=[session_config.middleware],
        template_config=TemplateConfig(
            directory=APP_DIR / "templates",
            engine=JinjaTemplateEngine,
            # In allen Seiten verfügbar: {{ version }}, {{ t('schlüssel') }}, {{ lang() }}, {{ i18n_json() }}
            engine_callback=_template_globals,
        ),
        exception_handlers={ApiError: api_error_handler},
        before_request=set_request_language,
        before_send=[add_security_headers, audit.record_request],
        on_startup=[on_startup],
        on_shutdown=[on_shutdown],
    )


app = create_app()


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="Evolved PowerNexus (Litestar)")
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=5000, help="leitet auf HTTPS um (mit USV_ALLOW_HTTP: das Dashboard)")
    parser.add_argument('--https-port', type=int, default=5443)
    args = parser.parse_args()

    log_level = os.environ.get('LOG_LEVEL', 'info').lower()
    if tls.http_allowed():
        logger.warning("⚠️ USV_ALLOW_HTTP gesetzt: Dashboard läuft unverschlüsselt. Nur für die Entwicklung!")
        uvicorn.run(app, host=args.host, port=args.port, workers=1, log_level=log_level)
    else:
        cert_path, key_path = tls.ensure_certificate()
        tls.start_redirect_server(args.host, args.port, args.https_port)
        uvicorn.run(app, host=args.host, port=args.https_port, workers=1, log_level=log_level,
                    ssl_certfile=str(cert_path), ssl_keyfile=str(key_path))
