#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sicherheitsprotokoll (Tabelle system_audit_trail)

Jede ändernde API-Anfrage wird zentral erfasst, auch abgewiesene. So kann kein neuer Endpunkt vergessen werden, und weil nur
Methode, Pfad und Ergebnis gespeichert werden, nie der Inhalt, gelangen keine Passwörter in das Protokoll. Anmeldungen und
Abläufe von Regeln kommen ausdrücklich hinzu. Das Protokoll lässt sich über die Oberfläche nicht ändern oder löschen.
"""

import csv
import io
import logging
from typing import Any, Dict, List, Tuple

from litestar import Controller, Request, Response, get
from litestar.params import FromQuery

import i18n
import users as user_store
from web import tls
from web.common import json_response, requires_admin

logger = logging.getLogger(__name__)

AUDITED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
NOT_AUDITED = {"/api/session/lang"}
MAX_EXPORT_ROWS = 100000
CSV_FIELDS = ("timestamp_utc", "user_id", "role", "client_ip", "action", "status", "target", "details")


def _result(status_code: int) -> str:
    if status_code < 400:
        return "OK"
    return "DENIED" if status_code in (401, 403, 429) else "FAILED"


def _write(app: Any, **entry: Any) -> None:
    runtime = getattr(app.state, "runtime", None)
    database = getattr(runtime, "history_db", None)
    if database is not None:
        database.insert_audit_trail(**entry)


def record_login(request: Request, username: str, status: str) -> None:
    """Anmeldung, Abmeldung: hier zählt der Benutzer, auch wenn es keine Sitzung gibt."""
    _write(request.app, user_id=username or "?", action="LOGIN", status=status, target=username,
           client_ip=request.client.host if request.client else None)


async def record_request(message: Dict[str, Any], scope: Dict[str, Any]) -> None:
    """before_send-Hook: läuft für jede Antwort, nach dem Handler."""
    if message["type"] != "http.response.start" or scope.get("type") != "http":
        return
    method, path = scope.get("method"), scope.get("path", "")
    exported = method == "GET" and path == "/api/audit/export"
    if not exported and (method not in AUDITED_METHODS or not path.startswith("/api/") or path in NOT_AUDITED):
        return
    try:
        app = scope["app"]
        session = scope.get("session")
        session = session if isinstance(session, dict) else {}
        username = session.get("username")
        user = app.state.users.get(username) if username else None
        client = scope.get("client")
        _write(app, user_id=username or "anonym", role=(user or {}).get("role"), client_ip=client[0] if client else None,
               action=f"{method} {path}", status=_result(message["status"]), details=f"HTTP {message['status']}")
    except Exception as e:       # das Protokoll darf keine Anfrage scheitern lassen, muss aber auffallen
        logger.error(f"❌ Sicherheitsprotokoll: {e}")


def security_findings(runtime: Any) -> List[Tuple[str, Dict[str, Any]]]:
    """Unsichere Einstellungen, die der Betreiber bewusst gewählt hat oder kennen sollte."""
    findings: List[Tuple[str, Dict[str, Any]]] = []
    if tls.http_allowed():
        findings.append(("security.http_dashboard", {}))
    elif not tls.own_certificate_configured():
        findings.append(("security.self_signed", {}))
    if user_store.demo_active():
        findings.append(("security.demo_users", {}))
    manager = runtime.config_manager
    for key in manager.list_profiles():
        profile = manager.get_profile(key) or {}
        if profile.get("connection_type", "snmp") != "snmp" or profile.get("enabled") is False:
            continue
        name = profile.get("name", key)
        if profile.get("snmp_version", 2) != 3:
            findings.append(("security.snmp_plain", {"name": name}))
            if profile.get("community", "public") == "public":
                findings.append(("security.snmp_public", {"name": name}))
    for target in (runtime.targets.all() if runtime.targets else []):
        options = target.options
        if target.type == "opcua":
            if options.get("security") == "none":
                findings.append(("security.opcua_plain", {"name": target.name}))
            if options.get("auth") == "anonymous":
                findings.append(("security.opcua_anonymous", {"name": target.name}))
        if target.type == "http" and options.get("scheme") == "http":
            findings.append(("security.http_plain", {"name": target.name}))
    if runtime.services and runtime.services.get()["nut"]["enabled"]:
        findings.append(("security.nut_plain", {}))
    solar = runtime.solar.store.get() if getattr(runtime, "solar", None) else {}
    if solar.get("enabled"):
        if not solar.get("tls"):
            findings.append(("security.mqtt_plain", {"host": solar["host"]}))
        elif not solar.get("tls_verify", True):
            findings.append(("security.mqtt_unverified", {"host": solar["host"]}))
    return findings


def _safe_cell(value: Any) -> str:
    """Tabellenprogramme führen Zellen mit =, +, - oder @ als Formel aus; Benutzernamen kommen von außen."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


class AuditController(Controller):
    path = "/api/audit"
    guards = [requires_admin]

    @staticmethod
    def _entries(request: Request, hours: int, limit: int) -> list:
        database = getattr(request.app.state.runtime, "history_db", None)
        return database.get_audit_trail(hours=max(1, hours), limit=limit) if database else []

    @get("/", sync_to_thread=True)
    def list_entries(self, request: Request, hours: FromQuery[int] = 168, limit: FromQuery[int] = 200) -> Response:
        return json_response({"entries": self._entries(request, hours, min(max(1, limit), 1000))})

    @get("/overview", sync_to_thread=False)
    def overview(self, request: Request) -> Response:
        items = [i18n.t(key, **params) for key, params in security_findings(request.app.state.runtime)]
        return json_response({"findings": items, "ok_text": i18n.t("security.all_good")})

    @get("/export", sync_to_thread=True)
    def export(self, request: Request, hours: FromQuery[int] = 24 * 365) -> Response:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(CSV_FIELDS)
        for entry in self._entries(request, hours, MAX_EXPORT_ROWS):
            writer.writerow([_safe_cell(entry.get(field)) for field in CSV_FIELDS])
        return Response(buffer.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="evolved-powernexus-sicherheitsprotokoll.csv"'})


route_handlers = [AuditController]
