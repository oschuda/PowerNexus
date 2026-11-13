#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Automatisierung: Zielrechner, Regeln, Testmodus, Sprache und Hilfe (ab v22.4.0)

/api/hosts…            Zielrechner (Admin), Verbindung testen (Operator)
/api/policies…         Regeln (Admin), Regel testen und Ausführungen abbrechen (Operator)
/api/notifications…    E-Mail-Einstellungen, Test-E-Mail (Admin)
/api/opcua…            OPC-UA: Client-Zertifikat, vertraute Server-Zertifikate (Admin)
/api/services…         NUT-Server, Selbst-Shutdown (Admin)
/api/config/dry-run    globaler Testmodus
/api/session/lang      Sprache wählen
/api/i18n/{lang}       Übersetzungskatalog
/help/{lang}/{page}    Hilfeseite (Markdown → HTML)

Hinweis: Alle Handler laufen im Event-Loop (sync_to_thread=False). Die Sprache der Anfrage liegt in
einer ContextVar, die Litestar beim Auslagern in einen Thread nicht mitnimmt; die Handler machen nur
kurze Dateizugriffe.
"""

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

from litestar import Controller, Request, Response, delete, get, post, put
from litestar.di import NamedDependency, Provide
from litestar.params import FromPath, JSONBody

import i18n
import users as user_store
from automation import drivers, opcua_pki
from automation.mail_settings import MAIL_EVENTS, M365_AUTH, SMTP_SECURITY, TRANSPORTS, build_settings, public_settings
from automation.mailer import Mailer
from automation.services import build_services, nut_name, public_services
from automation.policies import CANCEL_EVENTS, CONDITIONS, SCOPE_TYPES, TRIGGER_EVENTS, build_policy
from automation.targets import TARGET_TYPES, ValidationError, build_target
from runtime import Runtime, profile_enabled
from web.common import json_response, provide_runtime, requires_admin_mgmt, requires_login, requires_operator

logger = logging.getLogger(__name__)

_RUNTIME = {"runtime": Provide(provide_runtime, sync_to_thread=False)}
HELP_DIR = Path(__file__).resolve().parent.parent / "help"


def _invalid(exc: ValidationError) -> Response:
    return json_response({"error": i18n.t(str(exc), **exc.params), "code": str(exc)}, 400)


def _outcome(outcome: drivers.Outcome) -> Dict[str, Any]:
    return {"success": outcome.success, "message": i18n.t(outcome.message, **outcome.details),
            "code": outcome.message, "command": outcome.command, "dry_run": outcome.dry_run,
            "details": outcome.details}


def _forbidden_target(runtime: Runtime, target) -> None:
    if target.type == "local" and not (runtime.services and runtime.services.self_shutdown_allowed):
        raise ValidationError("services.test.self_shutdown_off")


def _unavailable() -> Response:
    return json_response({"error": i18n.t("automation.unavailable")}, 503)


# ============================================================
# Zielrechner
# ============================================================

class TargetsController(Controller):
    path = "/api/hosts"
    dependencies = _RUNTIME

    @get("/", guards=[requires_admin_mgmt], sync_to_thread=False)
    def list_targets(self, runtime: NamedDependency[Runtime]) -> Response:
        if not runtime.targets:
            return _unavailable()
        hosts = [t.public() for t in runtime.targets.all()]
        return json_response({"success": True, "count": len(hosts), "hosts": hosts,
                              "types": {k: v["actions"] for k, v in TARGET_TYPES.items()}})

    @get("/{name:str}", guards=[requires_admin_mgmt], sync_to_thread=False)
    def get_target(self, runtime: NamedDependency[Runtime], name: FromPath[str]) -> Response:
        target = runtime.targets.get(name) if runtime.targets else None
        if not target:
            return json_response({"error": i18n.t("targets.error.not_found", name=name)}, 404)
        return json_response(target.public())

    @post("/", guards=[requires_admin_mgmt], status_code=201, sync_to_thread=False)
    def create_target(self, runtime: NamedDependency[Runtime], data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
        if not runtime.targets:
            return _unavailable()
        try:
            target = build_target(data or {})
            _forbidden_target(runtime, target)
            if runtime.targets.get(target.name):
                raise ValidationError("targets.error.exists", name=target.name)
            runtime.targets.upsert(target)
        except ValidationError as e:
            return _invalid(e)
        logger.info(f"🖥️ Zielrechner angelegt: {target.name} ({target.type})")
        return json_response({"success": True, "host": target.public()}, 201)

    @put("/{name:str}", guards=[requires_admin_mgmt], sync_to_thread=False)
    def update_target(self, runtime: NamedDependency[Runtime], name: FromPath[str],
                      data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
        existing = runtime.targets.get(name) if runtime.targets else None
        if not existing:
            return json_response({"error": i18n.t("targets.error.not_found", name=name)}, 404)
        try:
            target = build_target(data or {}, existing)
            _forbidden_target(runtime, target)
            if target.name != name and runtime.policies.referencing(name):
                raise ValidationError("targets.error.rename_in_use", name=name)
            runtime.targets.upsert(target, old_name=name)
        except ValidationError as e:
            return _invalid(e)
        return json_response({"success": True, "host": target.public()})

    @delete("/{name:str}", guards=[requires_admin_mgmt], status_code=200, sync_to_thread=False)
    def delete_target(self, runtime: NamedDependency[Runtime], name: FromPath[str]) -> Response:
        if not runtime.targets or not runtime.targets.get(name):
            return json_response({"error": i18n.t("targets.error.not_found", name=name)}, 404)
        used_by = runtime.policies.referencing(name)
        if used_by:
            return json_response({"error": i18n.t("targets.error.in_use", name=name, policies=", ".join(used_by)),
                                  "policies": used_by}, 409)
        runtime.targets.delete(name)
        return json_response({"success": True})

    @post("/test", guards=[requires_operator], status_code=200)
    async def test_unsaved(self, runtime: NamedDependency[Runtime],
                           data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
        """Verbindung mit (noch nicht gespeicherten) Formulardaten testen; Passwort ggf. vom gespeicherten Eintrag."""
        data = dict(data or {})
        existing = runtime.targets.get(data.pop("original_name", "") or "") if runtime.targets else None
        try:
            target = build_target(data, existing)
        except ValidationError as e:
            return _invalid(e)
        return json_response(_outcome(await drivers.test_target(target)))

    @post("/{name:str}/test", guards=[requires_operator], status_code=200)
    async def test_saved(self, runtime: NamedDependency[Runtime], name: FromPath[str]) -> Response:
        target = runtime.targets.get(name) if runtime.targets else None
        if not target:
            return json_response({"error": i18n.t("targets.error.not_found", name=name)}, 404)
        return json_response(_outcome(await drivers.test_target(target)))


# ============================================================
# Regeln
# ============================================================

class PoliciesController(Controller):
    path = "/api/policies"
    dependencies = _RUNTIME

    @get("/", guards=[requires_admin_mgmt], sync_to_thread=False)
    def list_policies(self, runtime: NamedDependency[Runtime]) -> Response:
        if not runtime.policies:
            return _unavailable()
        runs = runtime.policy_engine.runs()
        result = []
        for policy in runtime.policies.all():
            item = policy.to_dict()
            item["last_run"] = next((r for r in runs if r["policy"] == policy.name), None)
            result.append(item)
        return json_response({"success": True, "count": len(result), "policies": result})

    @get("/meta", guards=[requires_login], sync_to_thread=False)
    def meta(self, runtime: NamedDependency[Runtime]) -> Response:
        cm = runtime.config_manager
        profiles = [{"id": k, "name": (cm.get_profile(k) or {}).get("name", k)}
                    for k in cm.list_profiles() if profile_enabled(cm.get_profile(k))]
        groups = []
        if runtime.group_manager:
            groups = [{"id": str(g.id), "name": g.name} for g in runtime.group_manager.get_all_groups()]
        return json_response({
            "trigger_events": TRIGGER_EVENTS, "cancel_events": CANCEL_EVENTS, "scope_types": SCOPE_TYPES,
            "conditions": {k: {"min": v[0], "max": v[1]} for k, v in CONDITIONS.items()},
            "target_types": {k: v["actions"] for k, v in TARGET_TYPES.items()},
            "profiles": profiles, "groups": groups, "mail_lists": runtime.mail_store.list_names(),
        })

    @post("/", guards=[requires_admin_mgmt], status_code=201, sync_to_thread=False)
    def create_policy(self, runtime: NamedDependency[Runtime], data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
        if not runtime.policies:
            return _unavailable()
        try:
            policy = build_policy(data or {}, runtime.targets, runtime.mail_store.list_names())
            runtime.policies.upsert(policy)
        except ValidationError as e:
            return _invalid(e)
        logger.info(f"⚙️ Regel angelegt: {policy.name}")
        return json_response({"success": True, "policy": policy.to_dict()}, 201)

    @put("/{name:str}", guards=[requires_admin_mgmt], sync_to_thread=False)
    def update_policy(self, runtime: NamedDependency[Runtime], name: FromPath[str],
                      data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
        if not runtime.policies or not runtime.policies.get(name):
            return json_response({"error": i18n.t("policies.error.not_found", name=name)}, 404)
        try:
            policy = build_policy(data or {}, runtime.targets, runtime.mail_store.list_names())
            runtime.policies.upsert(policy, old_name=name)
        except ValidationError as e:
            return _invalid(e)
        return json_response({"success": True, "policy": policy.to_dict()})

    @delete("/{name:str}", guards=[requires_admin_mgmt], status_code=200, sync_to_thread=False)
    def delete_policy(self, runtime: NamedDependency[Runtime], name: FromPath[str]) -> Response:
        if not runtime.policies or not runtime.policies.delete(name):
            return json_response({"error": i18n.t("policies.error.not_found", name=name)}, 404)
        return json_response({"success": True})

    @post("/{name:str}/toggle", guards=[requires_admin_mgmt], status_code=200, sync_to_thread=False)
    def toggle_policy(self, runtime: NamedDependency[Runtime], name: FromPath[str]) -> Response:
        policy = runtime.policies.get(name) if runtime.policies else None
        if not policy:
            return json_response({"error": i18n.t("policies.error.not_found", name=name)}, 404)
        policy.enabled = not policy.enabled
        runtime.policies.upsert(policy, old_name=name)
        return json_response({"success": True, "enabled": policy.enabled})

    @post("/simulate", guards=[requires_operator], status_code=200)
    async def simulate(self, runtime: NamedDependency[Runtime],
                       data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
        """Regel testen: Plan im Testmodus + Verbindungsprüfung je Zielrechner; löst nie etwas aus."""
        if not runtime.policy_engine:
            return _unavailable()
        data = dict(data or {})
        ups_id = data.pop("test_ups", None) or runtime.current_profile_name
        try:
            policy = build_policy(data, runtime.targets, runtime.mail_store.list_names())
        except ValidationError as e:
            return _invalid(e)
        report = await runtime.policy_engine.simulate(policy, ups_id)
        for step in report["steps"]:
            for check in step["targets"]:
                check["message"] = i18n.t(check["message"], **check.get("details", {}))
        return json_response(report)

    @post("/{name:str}/trigger", guards=[requires_admin_mgmt], status_code=200)
    async def trigger_policy(self, runtime: NamedDependency[Runtime], name: FromPath[str],
                             data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
        """Löst eine Regel wirklich aus, ohne dass die USV ausfällt. Ohne Testmodus führt das die Schritte aus."""
        if not runtime.policy_engine:
            return _unavailable()
        ups_id = str((data or {}).get("ups_id") or "")
        if not runtime.config_manager.get_profile(ups_id):
            return json_response({"error": i18n.t("policies.error.trigger_ups", ups=ups_id)}, 400)
        engine = runtime.policy_engine
        try:
            run_id = await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(engine.trigger_manually(name, ups_id), engine.loop))
        except ValidationError as e:
            return _invalid(e)
        return json_response({"success": True, "run_id": run_id})

    @get("/runs", guards=[requires_login], sync_to_thread=False)
    def runs(self, runtime: NamedDependency[Runtime]) -> Response:
        if not runtime.policy_engine:
            return _unavailable()
        return json_response({"runs": runtime.policy_engine.runs(), "server_time": time.time()})

    @post("/runs/{run_id:str}/cancel", guards=[requires_operator], status_code=200, sync_to_thread=False)
    def cancel_run(self, runtime: NamedDependency[Runtime], run_id: FromPath[str]) -> Response:
        if not runtime.policy_engine or not runtime.policy_engine.cancel(run_id, reason="manual"):
            return json_response({"error": i18n.t("runs.error.not_cancellable")}, 409)
        return json_response({"success": True})


# ============================================================
# E-Mail
# ============================================================

class NotificationsController(Controller):
    path = "/api/notifications"
    dependencies = _RUNTIME
    guards = [requires_admin_mgmt]

    @get("/", sync_to_thread=False)
    def get_settings(self, runtime: NamedDependency[Runtime]) -> Response:
        if not runtime.mail_store:
            return _unavailable()
        return json_response({"settings": public_settings(runtime.mail_store.get()),
                              "meta": {"transports": TRANSPORTS, "m365_auth": M365_AUTH, "security": SMTP_SECURITY,
                                       "events": MAIL_EVENTS, "languages": i18n.SUPPORTED}})

    @put("/", sync_to_thread=False)
    def save_settings(self, runtime: NamedDependency[Runtime], data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
        if not runtime.mail_store:
            return _unavailable()
        current = runtime.mail_store.get()
        try:
            settings = build_settings(data or {}, current)
            removed = {item["name"] for item in current["lists"]} - {item["name"] for item in settings["lists"]}
            for name in sorted(removed):
                used_by = runtime.policies.referencing_list(name)
                if used_by:
                    raise ValidationError("mail.error.list_in_use", name=name, policies=", ".join(used_by))
        except ValidationError as e:
            return _invalid(e)
        runtime.mail_store.save(settings)
        logger.info(f"✉️ E-Mail-Einstellungen gespeichert (aktiv={settings['enabled']}, {settings['transport']})")
        return json_response({"success": True, "settings": public_settings(settings)})

    @post("/test", status_code=200)
    async def send_test(self, runtime: NamedDependency[Runtime],
                        data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
        """Test-E-Mail mit den Formulardaten (auch ungespeichert); Anmeldung und Versand getrennt geprüft."""
        if not runtime.mail_store:
            return _unavailable()
        data = dict(data or {})
        recipient = str(data.pop("recipient", "") or "").strip()
        try:
            settings = build_settings(data, runtime.mail_store.get())
            build_settings({"lists": [{"name": "test", "recipients": [recipient]}]}, settings)
        except ValidationError as e:
            return _invalid(e)
        steps = await Mailer.test(settings, recipient, i18n.current())
        for step in steps:
            step["message"] = i18n.t(step["code"], **step.pop("params"))
        return json_response({"success": all(s["success"] for s in steps), "steps": steps})


# ============================================================
# OPC-UA
# ============================================================

class OpcuaController(Controller):
    path = "/api/opcua"
    guards = [requires_admin_mgmt]

    @get("/client-certificate")
    async def client_certificate(self) -> Response:
        return json_response(await asyncio.to_thread(opcua_pki.ensure_client_certificate))

    @get("/client-certificate/download")
    async def download_client_certificate(self) -> Response:
        der = await asyncio.to_thread(opcua_pki.client_certificate_der)
        return Response(der, media_type="application/pkix-cert",
                        headers={"Content-Disposition": 'attachment; filename="evolved-powernexus-opcua.der"'})

    @get("/trusted", sync_to_thread=False)
    def trusted(self) -> Response:
        return json_response({"trusted": opcua_pki.list_trusted()})

    @delete("/trusted/{sha1:str}", status_code=200, sync_to_thread=False)
    def remove_trusted(self, sha1: FromPath[str]) -> Response:
        if not opcua_pki.remove_trusted(sha1):
            return json_response({"error": i18n.t("opcua.error.not_trusted")}, 404)
        return json_response({"success": True})

    @post("/trust", status_code=200)
    async def trust(self, data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
        """Vertraut dem Zertifikat, das der Server jetzt vorlegt, wenn es das angezeigte ist."""
        from automation.opcua_driver import choose_secure_endpoint, discover
        data = data or {}
        try:
            endpoints = await discover(str(data.get("address") or ""), int(data.get("port") or 4840), 15, fresh=True)
        except Exception as e:
            return json_response({"error": i18n.t("targets.test.opcua_unreachable", error=f"{type(e).__name__}: {e}"[:200])}, 502)
        endpoint = choose_secure_endpoint(endpoints)
        if endpoint is None or opcua_pki.sha1_of(endpoint.certificate) != data.get("sha1"):
            return json_response({"error": i18n.t("opcua.error.certificate_changed")}, 409)
        return json_response({"success": True, "certificate": opcua_pki.trust(endpoint.certificate)})


# ============================================================
# Optionale Dienste
# ============================================================

class ServicesController(Controller):
    path = "/api/services"
    dependencies = _RUNTIME
    guards = [requires_admin_mgmt]

    @staticmethod
    def _state(runtime: Runtime) -> Dict[str, Any]:
        cm = runtime.config_manager
        profiles = [{"id": key, "name": (cm.get_profile(key) or {}).get("name", key), "nut_name": nut_name(key)}
                    for key in cm.list_profiles() if profile_enabled(cm.get_profile(key))]
        return {"settings": public_services(runtime.services.get()), "status": runtime.services.status(),
                "profiles": profiles}

    @get("/", sync_to_thread=False)
    def get_services(self, runtime: NamedDependency[Runtime]) -> Response:
        if not runtime.services:
            return _unavailable()
        return json_response(self._state(runtime))

    @put("/")
    async def save_services(self, runtime: NamedDependency[Runtime],
                            data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
        if not runtime.services:
            return _unavailable()
        try:
            settings = build_services(data or {}, runtime.services.get(), list(runtime.config_manager.list_profiles()))
        except ValidationError as e:
            return _invalid(e)
        runtime.services.save(settings)
        await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(runtime.services.apply(), runtime.asyncio_loop))
        logger.warning(f"🔌 Dienste gespeichert: NUT={settings['nut']['enabled']}, Selbst-Shutdown={settings['self_shutdown']}")
        return json_response(self._state(runtime))

    @post("/clear-fsd", status_code=200, sync_to_thread=False)
    def clear_fsd(self, runtime: NamedDependency[Runtime]) -> Response:
        if not runtime.services:
            return _unavailable()
        if runtime.services.nut:
            runtime.services.nut.clear_fsd()
        return json_response(self._state(runtime))


# ============================================================
# Testmodus, Sprache, Hilfe
# ============================================================

@get("/api/config/dry-run", guards=[requires_login], sync_to_thread=False, dependencies=_RUNTIME)
def get_dry_run(runtime: NamedDependency[Runtime]) -> Response:
    return json_response({"dry_run": runtime.dry_run, "forced": runtime.dry_run_forced})


@post("/api/config/dry-run", guards=[requires_admin_mgmt], status_code=200, sync_to_thread=False,
      dependencies=_RUNTIME)
def set_dry_run(request: Request, runtime: NamedDependency[Runtime],
                data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
    enabled = bool((data or {}).get("enabled"))
    if not enabled and runtime.dry_run_forced:
        return json_response({"error": i18n.t("dryrun.error.forced")}, 409)
    runtime.set_dry_run(enabled)
    logger.warning(f"🧪 Testmodus {'an' if enabled else 'aus'} – Benutzer: {request.session.get('username')}")
    return json_response({"dry_run": runtime.dry_run, "forced": runtime.dry_run_forced})


@post("/api/session/lang", guards=[requires_login], status_code=200, sync_to_thread=False)
def set_language(request: Request, data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
    lang = (data or {}).get("lang")
    if lang not in i18n.SUPPORTED:
        return json_response({"error": "unsupported language"}, 400)
    session = dict(request.session)
    session["lang"] = lang
    request.set_session(session)
    users = request.app.state.users
    username = session.get("username")
    if username in users:
        user_store.edit_user(users, username, lang=lang)
    return json_response({"lang": lang})


@get("/api/i18n/{lang:str}", sync_to_thread=False)
def get_catalog(lang: str) -> Response:
    if lang not in i18n.SUPPORTED:
        return json_response({"error": "unsupported language"}, 404)
    return json_response(i18n.catalog(lang))


_PAGE_RE = re.compile(r"^[a-z0-9-]{1,40}$")


@get("/help/{lang:str}/{page:str}", guards=[requires_login], sync_to_thread=False)
def help_page(lang: str, page: str) -> Response:
    import markdown
    if lang not in i18n.SUPPORTED or not _PAGE_RE.match(page):
        return Response("not found", status_code=404, media_type="text/plain")
    path = HELP_DIR / lang / f"{page}.md"
    if not path.exists():
        return Response("not found", status_code=404, media_type="text/plain")
    html = markdown.markdown(path.read_text(encoding="utf-8"), extensions=["tables", "fenced_code"])
    return Response(html, media_type="text/html")


route_handlers = [TargetsController, PoliciesController, NotificationsController, OpcuaController, ServicesController, get_dry_run, set_dry_run, set_language, get_catalog,
                  help_page]
