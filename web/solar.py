#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PV-Wechselrichter (ab v22.22.0)

/api/solar/live|history|day|month|events|correlation   Werte und Auswertungen (angemeldet)
/api/solar/settings                                    MQTT-Anbindung lesen und speichern (Admin)
/api/solar/test                                        Verbindung zum Broker prüfen (Admin)
"""

import asyncio
import logging
from typing import Any, Dict, Optional

from litestar import Controller, Response, get, post, put
from litestar.di import NamedDependency, Provide
from litestar.params import FromQuery, JSONBody

import i18n
from automation.targets import ValidationError
from runtime import Runtime, profile_enabled
from solar import store
from solar.discovery import FIELDS
from solar.mqtt import probe_connection
from solar.settings import build_settings, public_settings
from web.common import json_response, provide_runtime, requires_admin_mgmt, requires_login

logger = logging.getLogger(__name__)

_RUNTIME = {"runtime": Provide(provide_runtime, sync_to_thread=False)}


def _unavailable() -> Response:
    return json_response({"error": i18n.t("solar.unavailable")}, 503)


def _invalid(exc: ValidationError) -> Response:
    return json_response({"error": i18n.t(str(exc), **exc.params), "code": str(exc)}, 400)


def _profile_name(runtime: Runtime, key: str) -> str:
    profile = runtime.config_manager.get_profile(key) if key else None
    return profile.get("name", key) if profile else key


class SolarController(Controller):
    path = "/api/solar"
    dependencies = _RUNTIME
    guards = [requires_login]

    @get("/live", sync_to_thread=True)
    def live(self, runtime: NamedDependency[Runtime]) -> Response:
        if not runtime.solar:
            return _unavailable()
        snapshot = runtime.solar.snapshot()
        data = store.with_derived(snapshot) if snapshot else store.latest(runtime.history_db)
        status = runtime.solar.status()
        return json_response({"live": bool(snapshot), "data": data,
                              "status": {key: status[key] for key in ("enabled", "connected", "last_message")}})

    @get("/history", sync_to_thread=True)
    def history(self, runtime: NamedDependency[Runtime], days: FromQuery[int] = 1) -> Response:
        if not runtime.solar:
            return _unavailable()
        return json_response(store.series(runtime.history_db, max(1, min(days, 365))))

    @get("/day", sync_to_thread=False)
    def day(self, runtime: NamedDependency[Runtime], date: FromQuery[str] = "") -> Response:
        if not runtime.solar:
            return _unavailable()
        try:
            return json_response(store.day_summary(runtime.history_db, date or None))
        except ValueError:
            return json_response({"error": i18n.t("solar.error.date")}, 400)

    @get("/month", sync_to_thread=False)
    def month(self, runtime: NamedDependency[Runtime], month: FromQuery[str] = "") -> Response:
        if not runtime.solar:
            return _unavailable()
        try:
            return json_response(store.month_summary(runtime.history_db, month or None))
        except ValueError:
            return json_response({"error": i18n.t("solar.error.date")}, 400)

    @get("/events", sync_to_thread=True)
    def events(self, runtime: NamedDependency[Runtime], days: FromQuery[int] = 30) -> Response:
        if not runtime.solar:
            return _unavailable()
        return json_response({"events": store.events(runtime.history_db, max(1, min(days, 365)))})

    @get("/correlation", sync_to_thread=True)
    def correlation(self, runtime: NamedDependency[Runtime], days: FromQuery[int] = 7,
                    profile: FromQuery[str] = "") -> Response:
        if not runtime.solar:
            return _unavailable()
        key = profile or runtime.solar.store.get()["ups_profile"] or runtime.current_profile_name
        return json_response(store.correlation(runtime.history_db, max(1, min(days, 90)), _profile_name(runtime, key)))

    # ------------------------------------------------------------
    # Einstellungen (Admin)
    # ------------------------------------------------------------

    @staticmethod
    def _state(runtime: Runtime) -> Dict[str, Any]:
        cm = runtime.config_manager
        profiles = [{"id": key, "name": (cm.get_profile(key) or {}).get("name", key)}
                    for key in cm.list_profiles() if profile_enabled(cm.get_profile(key))]
        return {"settings": public_settings(runtime.solar.store.get()), "status": runtime.solar.status(),
                "sensors": runtime.solar.sensors(), "fields": list(FIELDS), "profiles": profiles}

    @get("/settings", guards=[requires_admin_mgmt], sync_to_thread=False)
    def get_settings(self, runtime: NamedDependency[Runtime]) -> Response:
        if not runtime.solar:
            return _unavailable()
        return json_response(self._state(runtime))

    @put("/settings", guards=[requires_admin_mgmt], sync_to_thread=False)
    def save_settings(self, runtime: NamedDependency[Runtime],
                      data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
        if not runtime.solar:
            return _unavailable()
        try:
            settings = build_settings(data or {}, runtime.solar.store.get(), list(runtime.config_manager.list_profiles()))
        except ValidationError as e:
            return _invalid(e)
        runtime.solar.store.save(settings)
        runtime.solar.apply()
        logger.warning(f"☀️ PV-Anbindung gespeichert: an={settings['enabled']}, Broker={settings['host']}:{settings['port']}")
        return json_response(self._state(runtime))

    @post("/test", guards=[requires_admin_mgmt], status_code=200)
    async def test(self, runtime: NamedDependency[Runtime], data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
        """Prüft die eingegebenen (noch nicht gespeicherten) Einstellungen; ein leeres Passwort nimmt das gespeicherte."""
        if not runtime.solar:
            return _unavailable()
        try:
            settings = build_settings({**(data or {}), "enabled": True}, runtime.solar.store.get(),
                                      list(runtime.config_manager.list_profiles()))
        except ValidationError as e:
            return _invalid(e)
        result = await asyncio.to_thread(probe_connection, settings)
        if result["success"]:
            message = i18n.t("solar.test.ok", sensors=result["sensors"])
        else:
            message = i18n.t("solar.test.failed", error=result["error"] or "?")
        return json_response({**result, "message": message})


route_handlers = [SolarController]
