#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Seiten und Authentifizierung (portiert aus app_flask.py index() und routes/auth.py)

GET  /                       Dashboard (Login erforderlich)
GET  /login, POST /login     Login-Formular
GET  /logout                 Session löschen
GET  /api/session            Session-Info
GET  /admin/users            Benutzerverwaltung (Admin)
POST /api/admin/user-add|user-edit|user-delete
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union

import i18n
from litestar import Request, Response, get, post
from litestar.params import JSONBody, URLEncodedBody
from litestar.response import File, Redirect, Template

import users as user_store
from web import audit
from web.common import current_user, use_language, json_response, requires_login, requires_admin

logger = logging.getLogger(__name__)


def _users(request: Request) -> Dict[str, Dict[str, Any]]:
    return request.app.state.users


@get("/", sync_to_thread=False)
def index(request: Request) -> Union[Template, Redirect]:
    user = current_user(request)
    if user is None:
        return Redirect(path="/login")
    if user.get('must_change_password'):
        return Redirect(path="/account/password")
    return Template(template_name="index.html")


@get("/favicon.ico", sync_to_thread=False, include_in_schema=False)
def favicon() -> File:
    return File(path=Path(__file__).resolve().parent.parent / "favicon.ico", media_type="image/x-icon")


@get("/login", sync_to_thread=False)
def login_page() -> Template:
    return Template(template_name="login.html", context={"demo_users": user_store.demo_active()})


@post("/login", status_code=200, sync_to_thread=True)
def login(
    request: Request,
    data: URLEncodedBody[Dict[str, str]],
) -> Union[Template, Redirect]:
    use_language(request)
    username = (data or {}).get('username', '')
    password = (data or {}).get('password', '')
    address = request.client.host if request.client else "?"
    logger.info(f"🔐 Login-Versuch: {username}")

    throttle = request.app.state.login_throttle
    if throttle.blocked(address, username):
        logger.warning(f"🚫 Login gesperrt: {username} von {address}")
        audit.record_login(request, username, "DENIED")
        return Template(template_name="login.html", status_code=429,
                        context={"error_key": "login.locked", "demo_users": user_store.demo_active()})

    users = _users(request)
    if user_store.verify_password(users, username, password):
        throttle.succeeded(address, username)
        request.set_session({'logged_in': True, 'username': username, 'sid': request.app.state.sessions.create(username)})
        audit.record_login(request, username, "OK")
        logger.info(f"✅ Benutzer '{username}' erfolgreich angemeldet")
        return Redirect(path="/")

    throttle.failed(address, username)
    audit.record_login(request, username, "FAILED")

    logger.warning(f"❌ Login-Fehler für: {username} (Passwort falsch oder Benutzer nicht gefunden)")
    return Template(template_name="login.html", context={"error_key": "login.invalid", "demo_users": user_store.demo_active()})


@get("/logout", sync_to_thread=False)
def logout(request: Request) -> Redirect:
    username = request.session.get('username', 'unbekannt')
    request.app.state.sessions.revoke(request.session.get('sid'))
    request.clear_session()
    logger.info(f"✅ Benutzer '{username}' abgemeldet")
    return Redirect(path="/login")


@get("/api/version", sync_to_thread=False)
def api_version() -> Response:
    from version import __version__
    return json_response({"version": __version__})


@get("/api/session", sync_to_thread=False)
def api_session(request: Request) -> Response:
    user = current_user(request)
    if user is None:
        return json_response({"authenticated": False, "user": None}, 401)
    return json_response({
        "authenticated": True,
        "user": request.session.get('username'),
        "role": user.get('role'),
        "timestamp": datetime.now().isoformat(),
    })


@get("/account/password", sync_to_thread=False)
def password_page(request: Request) -> Union[Template, Redirect]:
    user = current_user(request)
    if user is None:
        return Redirect(path="/login")
    return Template(template_name="change_password.html", context={"forced": bool(user.get('must_change_password'))})


@post("/api/account/password", status_code=200, sync_to_thread=True)
def change_password(request: Request, data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
    """Eigenes Passwort ändern; auch mit vorläufigem Passwort erlaubt. Andere Sitzungen des Kontos enden."""
    use_language(request)
    if current_user(request) is None:
        return json_response({"error": i18n.t("errors.not_authenticated")}, 401)
    data = data or {}
    username = request.session['username']
    current, password = str(data.get('current') or ''), str(data.get('password') or '')
    users = _users(request)
    if not user_store.verify_password(users, username, current):
        return json_response({"error": i18n.t("account.wrong_current")}, 400)
    violation = user_store.validate_password(password, username)
    if violation:
        return json_response({"error": i18n.t(violation[0], **violation[1])}, 400)
    if password == current:
        return json_response({"error": i18n.t("account.same")}, 400)
    user_store.change_own_password(users, username, password)
    request.app.state.sessions.revoke_user(username, keep=request.session.get('sid'))
    logger.info(f"🔑 Passwort geändert: {username}")
    return json_response({"success": True})


@get("/admin/users", sync_to_thread=False)
def admin_users(request: Request) -> Union[Template, Response]:
    user = current_user(request)
    if user is None or user.get('must_change_password') or user.get('role') != 'admin':
        logger.warning(f"❌ Unauthorized admin access attempt by {request.session.get('username')}")
        return json_response({"error": i18n.t("errors.admin_only")}, 403)

    current = request.session.get('username')
    users = [
        {
            "username": username,
            "name": data.get('name', username),
            "role": data.get('role', 'user'),
            "status": "online" if username == current else "offline",
        }
        for username, data in _users(request).items()
    ]
    return Template(template_name="admin_users.html", context={"users": users})


@post("/api/admin/user-add", guards=[requires_login, requires_admin], status_code=200, sync_to_thread=False)
def user_add(request: Request, data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
    data = data or {}
    username = str(data.get('username', '')).strip()
    name = str(data.get('name', '')).strip()
    role = data.get('role', 'user')
    password = str(data.get('password', '')).strip()

    if not username or not password:
        return json_response({"error": i18n.t("errors.user_password_required")}, 400)
    if role not in user_store.ROLES:
        return json_response({"error": i18n.t("errors.invalid_role")}, 400)
    violation = user_store.validate_password(password, username)
    if violation:
        return json_response({"error": i18n.t(violation[0], **violation[1])}, 400)

    users = _users(request)
    if username in users:
        return json_response({"error": i18n.t("errors.user_exists", name=username)}, 409)

    user_store.add_user(users, username, password, role, name)
    logger.info(f"✅ Benutzer hinzugefügt: {username} (Rolle: {role})")
    return json_response({"success": True, "username": username})


@post("/api/admin/user-edit", guards=[requires_login, requires_admin], status_code=200, sync_to_thread=False)
def user_edit(request: Request, data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
    data = data or {}
    username = str(data.get('username', '')).strip()

    users = _users(request)
    if username not in users:
        return json_response({"error": i18n.t("errors.user_not_found", name=username)}, 404)

    if data.get('password'):
        violation = user_store.validate_password(str(data['password']), username)
        if violation:
            return json_response({"error": i18n.t(violation[0], **violation[1])}, 400)

    user_store.edit_user(
        users, username,
        name=str(data['name']).strip() if data.get('name') else None,
        role=data.get('role'),
        password=data.get('password') or None,
    )
    if data.get('password') or data.get('role'):
        request.app.state.sessions.revoke_user(username, keep=request.session.get('sid'))
    logger.info(f"✅ Benutzer aktualisiert: {username}")
    return json_response({"success": True, "username": username})


@post("/api/admin/user-delete", guards=[requires_login, requires_admin], status_code=200, sync_to_thread=False)
def user_delete(request: Request, data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
    data = data or {}
    username = str(data.get('username', '')).strip()

    if username == request.session.get('username'):
        return json_response({"error": i18n.t("errors.delete_self")}, 400)

    users = _users(request)
    if username not in users:
        return json_response({"error": i18n.t("errors.user_not_found", name=username)}, 404)

    user_store.delete_user(users, username)
    request.app.state.sessions.revoke_user(username)
    logger.info(f"🗑️ Benutzer gelöscht: {username}")
    return json_response({"success": True, "username": username})


route_handlers = [password_page, change_password, index, favicon, api_version, login_page, login, logout, api_session, admin_users, user_add, user_edit, user_delete]
