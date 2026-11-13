#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gemeinsame Bausteine der Litestar-Web-Schicht:
- json_response(): JSON mit beliebigem Statuscode (wie Flask jsonify + Status)
- ApiError + Exception-Handler: Fehler im bisherigen Format {"error": "..."}
- Guards für Login und Rollen (gleiche Statuscodes/Meldungen wie die Flask-Decorators)
"""

from typing import Any, Dict, Iterable, Optional

import i18n
from litestar import Request, Response
from litestar.connection import ASGIConnection
from litestar.exceptions import HTTPException
from litestar.handlers.base import BaseRouteHandler

from runtime import Runtime


def json_response(content: Any, status: int = 200) -> Response:
    return Response(content=content, status_code=status, media_type="application/json")


class ApiError(HTTPException):
    """Fehler, der als {"error": message} mit gegebenem Status ausgeliefert wird."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(detail=message, status_code=status_code)


def request_language(request: Request) -> str:
    """Sprache einer Anfrage: Sitzung → Benutzerprofil → Browser → Deutsch."""
    lang = None
    try:
        session = request.session
        lang = session.get('lang')
        if not lang and session.get('username'):
            lang = (request.app.state.users.get(session['username']) or {}).get('lang')
    except Exception:
        lang = None
    return lang or i18n.negotiate(request.headers.get('accept-language'))


def use_language(request: Request) -> None:
    """Für Handler mit sync_to_thread=True, die Texte erzeugen: Die Sprache kommt nicht von selbst in den Thread."""
    i18n.set_current(request_language(request))


def api_error_handler(request: Request, exc: ApiError) -> Response:
    # Guards laufen vor dem Sprach-Hook; detail ist ein Katalogschlüssel
    return json_response({"error": i18n.t(exc.detail, lang=request_language(request))}, exc.status_code)


def provide_runtime(request: Request) -> Runtime:
    return request.app.state.runtime


# ------------------------------------------------------------
# Guards
# ------------------------------------------------------------

def current_user(connection: ASGIConnection) -> Optional[Dict[str, Any]]:
    """Der angemeldete Benutzer laut Server. Rolle und Sperren gelten sofort, nicht erst nach Ablauf des Cookies."""
    session = connection.session
    username = session.get('username')
    if not session.get('logged_in') or not connection.app.state.sessions.valid(session.get('sid'), username):
        return None
    return connection.app.state.users.get(username)


def requires_login(connection: ASGIConnection, _: BaseRouteHandler) -> None:
    user = current_user(connection)
    if user is None:
        raise ApiError(401, "errors.not_authenticated")
    if user.get('must_change_password'):
        raise ApiError(403, "errors.password_change_required")


def requires_role(roles: Iterable[str], message: str):
    allowed = tuple(roles)

    def guard(connection: ASGIConnection, _: BaseRouteHandler) -> None:
        requires_login(connection, _)
        if current_user(connection).get('role') not in allowed:
            raise ApiError(403, message)

    guard.__name__ = f"requires_role_{'_'.join(allowed)}"
    return guard


# Meldungen wie in den bisherigen Flask-Blueprints
requires_admin = requires_role(('admin',), "errors.admin_only")                       # routes/api.py, auth.py
requires_admin_mgmt = requires_role(('admin',), "errors.admin_required")         # policies/hosts/groups
requires_operator = requires_role(('admin', 'operator'), "errors.operator_required")
requires_user_or_admin = requires_role(('admin', 'user', 'operator'), "errors.no_access")
