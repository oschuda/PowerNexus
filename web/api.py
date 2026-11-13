#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Core API (portiert aus routes/api.py, Flask v20.1.4)

Gleiche URLs, Parameter, Statuscodes und JSON-Strukturen wie die Flask-Version,
damit templates/index.html und static/js unverändert weiterlaufen.
"""

import logging
import os
import shutil
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from litestar import Controller, Request, Response, get, post
from litestar.di import NamedDependency, Provide
from litestar.params import FromQuery, JSONBody

import i18n
import secret_store
from automation_state import (
    get_automation_paused, set_automation_paused,
    get_simulation_mode, set_simulation_mode, add_simulation_event, get_simulation_events,
)
from runtime import Runtime, profile_enabled
from web.common import (
    json_response, provide_runtime,
    current_user, requires_login, requires_admin, requires_operator, requires_user_or_admin, use_language,
)

logger = logging.getLogger(__name__)

def _build_profile_config(data: dict) -> dict:
    """Baut ein Profile-Config-Dict aus Request-Daten auf"""
    config = {
        'name': data.get('display_name', data.get('name', '')),
        'manufacturer': data.get('manufacturer', 'APC'),
        'connection_type': data.get('connection_type', 'snmp'),
        'polling_interval': int(data.get('polling_interval', 10)),
    }
    if config['connection_type'] == 'snmp':
        config['ip'] = data.get('ip', '')
        config['community'] = data.get('community', 'public')
        config['snmp_version'] = int(data.get('snmp_version', 2))
        if config['snmp_version'] == 3:
            config['snmp_user'] = str(data.get('snmp_user') or '').strip()
            for field, secret in (('snmp_auth_password', 'snmp_auth_secret'), ('snmp_priv_password', 'snmp_priv_secret')):
                if data.get(field):
                    config[secret] = secret_store.encrypt(str(data[field]))
        if data.get('fallback_temperature'):
            config['fallback_temperature'] = int(data['fallback_temperature'])
    elif config['connection_type'] == 'usb':
        config['ups_name'] = data.get('ups_name', 'ups')
    if data.get('location'):
        config['location'] = data['location']
    if 'enabled' in data:
        config['enabled'] = bool(data['enabled'])
    return config


def _snmp3_incomplete(data: dict, existing: Optional[dict] = None) -> bool:
    """SNMPv3: Benutzer und zwei Passwörter; leere Felder behalten beim Bearbeiten den gespeicherten Wert."""
    if data.get('connection_type', 'snmp') != 'snmp' or int(data.get('snmp_version', 2)) != 3:
        return False
    existing = existing or {}
    if not str(data.get('snmp_user') or '').strip():
        return True
    for field, secret in (('snmp_auth_password', 'snmp_auth_secret'), ('snmp_priv_password', 'snmp_priv_secret')):
        value = str(data.get(field) or '')
        if (value and (len(value) < 8 or "\n" in value)) or (not value and not existing.get(secret)):
            return True
    return False


def _history_name(runtime: Runtime, profile: str) -> str:
    """Profilschlüssel (config.json) → Anzeigename, unter dem history.profile gespeichert ist."""
    profile = (profile or "").strip()
    if not profile:
        return ""
    cfg = runtime.config_manager.get_profile(profile)
    return cfg.get('name', profile) if cfg else profile


def _history_bucket(days: int) -> int:
    """Verdichtung für Diagramme: Rohdaten bis 2 Tage, danach 15-Minuten- bzw. Stundenmittel."""
    if days <= 2:
        return 0
    return 900 if days <= 14 else 3600


def _battery_evaluation(runtime: Runtime, key: str):
    """Batteriebewertung (battery_assessment) und statistischer Trend für ein Profil.

    Returns: (live_data | None, trend | None, assessment)
    trend ist nur gesetzt, wenn ein trainiertes ML-Modell mit ausreichend Messhistorie
    eine Prognose liefert. Die Heuristik (fester Startwert 365 Tage) gilt nicht als Trend.
    """
    from battery_assessment import MIN_TREND_DAYS, assess
    from battery_ml import get_battery_predictor

    profile_cfg = runtime.config_manager.get_profile(key) or {}
    live = runtime.latest_data(key)
    if not live and key == runtime.current_profile_name and runtime.current_ups_data.get('battery_charge') is not None:
        live = runtime.current_ups_data

    history_days, trend = 0.0, None
    try:
        name = profile_cfg.get('name', key)
        history_days = runtime.history_db.history_span_days(name, days=180)
        predictor = get_battery_predictor()
        if history_days >= MIN_TREND_DAYS and predictor.can_predict():
            prediction = predictor.predict(runtime.history_db.get_history(days=180, profile=name), profile=key)
            trend = prediction if prediction.get('method') == 'ml' else None
    except Exception as e:
        logger.warning(f"⚠️ Batterie-Trend für {key} nicht verfügbar: {e}")
    return live, trend, assess(live or {}, profile_cfg, history_days=history_days)


def _assessment_payload(assessment: Dict[str, Any], trend: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Gemeinsamer Teil der Batterie-Antworten: drei Dimensionen + Zusammenfassung."""
    from battery_assessment import MIN_TREND_DAYS
    lifetime = assessment['lifetime']
    return {
        "risk_level": assessment['risk_level'],
        "overall_text": assessment['overall_text'],
        "condition": assessment['condition'],
        "lifetime": lifetime,
        "data_quality": assessment['data_quality'],
        "reasons": assessment['reasons'],
        "hints": assessment['hints'],
        "battery_installed": assessment['battery_installed'],
        "battery_age_years": assessment['battery_age_years'],
        "last_selftest": assessment['last_selftest'],
        "lifespan_remaining_months": lifetime['months'],
        "days_remaining": round(lifetime['months'] * 30.44) if lifetime['months'] is not None else None,
        "trend": {
            "available": trend is not None,
            "days_remaining": trend.get('days_remaining') if trend else None,
            "method": trend.get('method') if trend else None,
            "degradation_rate": trend.get('degradation_rate') if trend else None,
            "note": None if trend else
                i18n.t("errors.trend_note", days=MIN_TREND_DAYS),
        },
        # ohne trainiertes Modell keine Feature-Wichtigkeit anzeigen
        "feature_importances": trend.get('feature_importances', {}) if trend and trend.get('method') == 'ml' else {},
    }


class ApiController(Controller):
    path = "/api"
    guards = [requires_login]
    dependencies = {"runtime": Provide(provide_runtime, sync_to_thread=False)}

    # --------------------------------------------------------
    # Live-Daten & Profile
    # --------------------------------------------------------

    @get("/data", sync_to_thread=False)
    def get_data(self, runtime: NamedDependency[Runtime]) -> Response:
        return json_response(runtime.current_ups_data)

    @get("/switch", sync_to_thread=True)
    def switch_get(self, request: Request, runtime: NamedDependency[Runtime], profile: FromQuery[Optional[str]] = None) -> Response:
        use_language(request)
        return self._switch(runtime, profile)

    @post("/switch", status_code=200, sync_to_thread=True)
    def switch_post(self, request: Request, runtime: NamedDependency[Runtime], data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
        use_language(request)
        return self._switch(runtime, (data or {}).get('profile'))

    @staticmethod
    def _switch(runtime: NamedDependency[Runtime], profile_name: Optional[str]) -> Response:
        if not profile_name:
            return json_response({"error": i18n.t("errors.no_profile_given")}, 400)
        profile = runtime.config_manager.get_profile(profile_name)
        if not profile:
            return json_response({"error": i18n.t("errors.profile_not_found", name=profile_name)}, 404)
        if not profile_enabled(profile):
            return json_response({"error": i18n.t("errors.profile_disabled", name=profile_name)}, 409)

        runtime.config_manager.update_active_profile(profile_name)
        try:
            data = runtime.switch_profile(profile_name, profile)
            return json_response({"success": True, "profile": profile_name, "data": data})
        except Exception as e:
            logger.error(f"❌ Fehler beim Profilwechsel: {e}")
            return json_response({"error": str(e)}, 500)

    @get("/profiles", sync_to_thread=False)
    def profiles(self, request: Request, runtime: NamedDependency[Runtime]) -> Response:
        is_admin = (current_user(request) or {}).get('role') == 'admin'
        cm = runtime.config_manager
        result = []
        for name in cm.list_profiles():
            profile = cm.get_profile(name) or {}
            result.append({
                "name": name,
                "display_name": profile.get('name', name),
                "manufacturer": profile.get('manufacturer', ''),
                "connection_type": profile.get('connection_type', 'snmp'),
                "ip": profile.get('ip', ''),
                "community": profile.get('community', '') if is_admin else '',
                "snmp_version": profile.get('snmp_version', 2),
                "snmp_user": profile.get('snmp_user', '') if is_admin else '',
                "snmp_has_secrets": bool(profile.get('snmp_auth_secret') and profile.get('snmp_priv_secret')),
                "ups_name": profile.get('ups_name', ''),
                "polling_interval": profile.get('polling_interval', 10),
                "fallback_temperature": profile.get('fallback_temperature', 25),
                "location": cm.get_profile_location(name),
                "active": name == runtime.current_profile_name,
                "enabled": profile_enabled(profile),
            })
        return json_response(result)

    @get("/locations", sync_to_thread=False)
    def locations(self, runtime: NamedDependency[Runtime]) -> Response:
        return json_response(runtime.config_manager.list_locations())

    # --------------------------------------------------------
    # Historie, Batterie, Netzqualität
    # --------------------------------------------------------

    @get("/history", sync_to_thread=True)
    def history(self, runtime: NamedDependency[Runtime], days: FromQuery[int] = 7, profile: FromQuery[str] = "") -> Response:
        """Verlauf einer USV (Standard: aktives Profil). ?profile= akzeptiert Schlüssel oder Anzeigename."""
        if not runtime.history_db:
            return json_response({"error": i18n.t("errors.database")}, 500)
        days = max(1, min(days, 365))
        name = _history_name(runtime, profile or runtime.current_profile_name)
        try:
            data = runtime.history_db.get_history(days, name, bucket_seconds=_history_bucket(days))
            return json_response({
                "success": True, "days": days, "profile": name, "data": data,
                "bin_size": _history_bucket(days) or "raw",
            })
        except Exception as e:
            logger.error(f"❌ Fehler beim Abrufen der Historie: {e}")
            return json_response({"error": str(e)}, 500)

    @get("/battery_health", sync_to_thread=True)
    def battery_health(self, runtime: NamedDependency[Runtime]) -> Response:
        if not runtime.battery_analyzer:
            return json_response({"error": "Battery Analyzer nicht verfügbar"}, 500)
        try:
            data = runtime.current_ups_data
            return json_response(runtime.battery_analyzer.analyze(data.get('profile', 'Unknown'), data))
        except Exception as e:
            logger.error(f"❌ Battery Health Fehler: {e}")
            return json_response({"error": str(e)}, 500)

    @staticmethod
    def _location_profiles(runtime: Runtime, location: str) -> List[str]:
        loc_data = runtime.config_manager.get_locations().get(location, {})
        return [p for p in loc_data.get('ups_profiles', [])
                if profile_enabled(runtime.config_manager.get_profile(p))]

    @get("/grid_quality", sync_to_thread=True)
    def grid_quality(self, request: Request, runtime: NamedDependency[Runtime], location: FromQuery[str] = "") -> Response:
        """Netzqualität aus der jüngsten Messung - für einen Standort aus dessen USVs."""
        use_language(request)
        grid = runtime.grid_analyzer
        if not grid:
            return json_response({"error": "Grid Analyzer nicht verfügbar"}, 500)
        location = location.strip()

        def analyze(data):
            frequency = float(data.get('input_frequency') or 50.0)
            voltage = float(data.get('input_voltage_formatted') or data.get('input_voltage') or 230.0)
            amps = float(data.get('input_current_formatted') or 0.0)
            result = grid.assess_grid_status(
                data.get('profile', 'unknown'), frequency=frequency,
                voltage_l1=voltage, voltage_l2=voltage, voltage_l3=voltage, current=amps,
            )
            result['profile'] = data.get('profile')
            result['measured_frequency'] = data.get('input_frequency') is not None
            return result

        try:
            if not location:
                return json_response(analyze(runtime.current_ups_data))

            profiles_at_location = self._location_profiles(runtime, location)
            if not profiles_at_location:
                return json_response({"error": i18n.t("errors.location_empty", location=location)}, 404)
            live = next((d for d in map(runtime.latest_data, profiles_at_location) if d), None)
            if not live:
                return json_response({
                    "error": i18n.t("errors.location_no_data", location=location),
                    "location": location, "profiles_at_location": profiles_at_location,
                }, 503)
            result = analyze(live)
            result['location'] = location
            result['profiles_at_location'] = profiles_at_location
            return json_response(result)
        except Exception as e:
            logger.error(f"❌ Grid Quality Fehler: {e}")
            return json_response({"error": str(e)}, 500)

    @get("/grid_quality_history", sync_to_thread=True)
    def grid_quality_history(
        self, request: Request, runtime: NamedDependency[Runtime], days: FromQuery[int] = 1,
        location: FromQuery[str] = "", profile: FromQuery[str] = "",
    ) -> Response:
        """Gemessener Netzverlauf (Spannung, Frequenz) je USV.

        Auswahl: ?location=… (alle USVs des Standorts), ?profile=… (eine USV)
        oder ohne Parameter das aktive Profil. "series" enthält eine Reihe je USV;
        "data" enthält aus Kompatibilitätsgründen die erste Reihe.
        """
        use_language(request)
        if not runtime.history_db:
            return json_response({"error": i18n.t("errors.database")}, 500)
        if days < 1 or days > 180:
            days = 1
        location, profile = location.strip(), profile.strip()

        if location:
            keys = self._location_profiles(runtime, location)
            if not keys:
                return json_response({"error": i18n.t("errors.location_empty", location=location)}, 404)
        else:
            keys = [profile or runtime.current_profile_name]

        try:
            names = {key: (runtime.config_manager.get_profile(key) or {}).get('name', key) for key in keys}
            history = runtime.history_db.get_grid_history(days, list(names.values()))
            series = [
                {"profile": key, "name": names[key], **history.get(names[key], dict(timestamp=[], voltage=[], frequency=[]))}
                for key in keys
            ]
            first = series[0] if series else {"timestamp": [], "voltage": [], "frequency": []}
            count = sum(len(s_["timestamp"]) for s_ in series)
            response = {
                "success": True, "days": days, "location": location, "profile": profile,
                "series": series,
                "data": {
                    "timestamp": first["timestamp"],
                    "voltage": first["voltage"],
                    "frequency": first["frequency"],
                    "voltage_sag": [round(207 - v, 1) if v < 207 else 0 for v in first["voltage"]],
                },
                "count": count,
                "bin_size": "raw" if days <= 7 else "3600s",
            }
            if count == 0:
                response["message"] = i18n.t("grid.no_values")
            return json_response(response)
        except Exception as e:
            logger.error(f"❌ Grid-Quality History Fehler: {type(e).__name__}: {e}")
            return json_response({"error": i18n.t("errors.internal"), "details": None}, 500)

    @get("/battery_status", sync_to_thread=True)
    def battery_status(self, request: Request, runtime: NamedDependency[Runtime], ups_id: FromQuery[str] = "") -> Response:
        """Batteriestatus einer USV.

        ups_id: Profilschlüssel aus config.json; leer oder "1" = aktive USV.
        Live-Werte stammen aus der letzten Abfrage, Batterie-Einsätze aus der Historie,
        Restlebensdauer/Risiko aus der Batterie-Prognose. Werte, die die USV nicht
        liefert (z. B. State of Health), werden als null zurückgegeben.
        """
        use_language(request)
        if not runtime.history_db:
            return json_response({"error": i18n.t("errors.database")}, 500)
        key = ups_id.strip()
        if key in ("", "1"):
            key = runtime.current_profile_name
        profile_cfg = runtime.config_manager.get_profile(key)
        if not profile_cfg:
            return json_response({"error": i18n.t("errors.profile_not_found", name=key)}, 404)
        name = profile_cfg.get('name', key)

        live, trend, assessment = _battery_evaluation(runtime, key)
        if not live:
            return json_response({
                "success": False, "ups_id": key, "profile": name,
                "message": i18n.t("errors.no_live_data"), "lifetime": assessment['lifetime'],
            }, 503)

        def num(value):
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        try:
            events = runtime.history_db.count_battery_events(name, days=180)
            runtime_value = None if live.get('runtime_available') is False else num(live.get('runtime_minutes'))
            return json_response({
                "success": True,
                "ups_id": key,
                "profile": name,
                "status": live.get('status_text', 'UNKNOWN'),
                "battery_charge": num(live.get('battery_charge')),
                "battery_runtime_minutes": runtime_value,
                "battery_temperature": num(live.get('battery_temp_celsius')),
                "battery_voltage": num(live.get('battery_voltage_formatted')),
                "ups_load": num(live.get('output_load')),
                "cycles_estimated": events['count'],
                "last_battery_event": events['last'],
                "health_soh": None,          # von den USVs per SNMP nicht geliefert
                "wear_degradation": None,
                **_assessment_payload(assessment, trend),
                "timestamp": live.get('timestamp'),
            })
        except Exception as e:
            logger.error(f"❌ Battery Status API Error: {e}")
            return json_response({"error": i18n.t("errors.internal"), "details": None}, 500)

    @post("/battery/installed", guards=[requires_operator], status_code=200, sync_to_thread=False)
    def battery_installed(self, request: Request, runtime: NamedDependency[Runtime],
                          data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
        """Einbaudatum der Batterie ändern; leeres Datum entfernt die Angabe."""
        data = data or {}
        key = str(data.get('ups_id') or runtime.current_profile_name or '')
        text = str(data.get('date') or '').strip()
        installed = None
        if text:
            try:
                installed = date.fromisoformat(text)
            except ValueError:
                return json_response({"error": i18n.t("battery.installed.invalid")}, 400)
            if installed > date.today() or installed.year < 2000:
                return json_response({"error": i18n.t("battery.installed.range")}, 400)
        if not runtime.config_manager.set_battery_installed(key, installed.isoformat() if installed else None):
            return json_response({"error": i18n.t("battery.installed.no_profile", profile=key)}, 404)
        logger.warning(f"🔋 Einbaudatum {key}: {text or 'entfernt'} – Benutzer: {request.session.get('username')}")
        return json_response({"success": True, "profile": key, "date": installed.isoformat() if installed else None})

    @get("/battery/predict", sync_to_thread=True)
    def battery_predict(self, request: Request, runtime: NamedDependency[Runtime], profile: FromQuery[str] = "") -> Response:
        """Batteriebewertung eines Profils (drei Dimensionen); Name aus Kompatibilitätsgründen."""
        use_language(request)
        if not runtime.history_db:
            return json_response({"error": i18n.t("errors.database")}, 500)
        key = profile.strip() or runtime.current_profile_name
        if not runtime.config_manager.get_profile(key):
            return json_response({"error": i18n.t("errors.profile_not_found", name=key)}, 404)
        try:
            live, trend, assessment = _battery_evaluation(runtime, key)
            return json_response({
                "profile": key,
                "live_data": live is not None,
                "timestamp": datetime.now().isoformat(),
                **_assessment_payload(assessment, trend),
            })
        except Exception as e:
            logger.error(f"❌ Battery Predict Fehler: {e}")
            return json_response({"error": str(e)}, 500)

    @get("/battery/health_overview", sync_to_thread=True)
    def battery_health_overview(self, request: Request, runtime: NamedDependency[Runtime]) -> Response:
        use_language(request)
        if not runtime.history_db or not runtime.config_manager:
            return json_response({"error": "Services nicht verfügbar"}, 500)
        try:
            overview = []
            for key in runtime.config_manager.list_profiles():
                if not profile_enabled(runtime.config_manager.get_profile(key)):
                    continue
                live, _trend, assessment = _battery_evaluation(runtime, key)
                months = assessment['lifetime']['months']
                overview.append({
                    "profile": key,
                    "display_name": (runtime.config_manager.get_profile(key) or {}).get('name', key),
                    "live_data": live is not None,
                    "risk_level": assessment['risk_level'],
                    "overall_text": assessment['overall_text'],
                    "condition": assessment['condition']['status'],
                    "lifetime_months": months,
                    "data_quality": assessment['data_quality']['level'],
                    "days_remaining": round(months * 30.44) if months is not None else None,
                })
            return json_response({"profiles": overview, "count": len(overview)})
        except Exception as e:
            logger.error(f"❌ Battery Health Overview Fehler: {e}")
            return json_response({"error": str(e)}, 500)

    # --------------------------------------------------------
    # Konfiguration / Status-Schalter
    # --------------------------------------------------------

    @get("/config/dry-run-status", sync_to_thread=False)
    def dry_run_status(self, runtime: NamedDependency[Runtime]) -> Response:
        return json_response({"dry_run": runtime.dry_run, "forced": runtime.dry_run_forced})

    @get("/config/automation-status", sync_to_thread=False)
    def automation_status(self) -> Response:
        paused = get_automation_paused()
        return json_response({
            "success": True,
            "automation_paused": paused,
            "status_text": "AUTOMATION PAUSIERT" if paused else "AUTOMATION AKTIV",
            "timestamp": int(datetime.now().timestamp()),
        })

    @post("/config/toggle-automation", guards=[requires_operator], status_code=200, sync_to_thread=False)
    def toggle_automation(self, request: Request) -> Response:
        new_state = set_automation_paused(not get_automation_paused())
        status_text = "AUTOMATION PAUSIERT" if new_state else "AUTOMATION AKTIV"
        logger.warning(f"🔘 {status_text} - Benutzer: {request.session.get('username', 'unknown')}")
        return json_response({
            "success": True,
            "automation_paused": new_state,
            "status_text": status_text,
            "message": f"Automation wurde {'pausiert' if new_state else 'aktiviert'}",
            "timestamp": int(datetime.now().timestamp()),
        })

    @post("/database/sync", guards=[requires_operator], status_code=200, sync_to_thread=True)
    def sync_database(self, runtime: NamedDependency[Runtime]) -> Response:
        written = runtime.history_db.flush()
        if written is None:
            return json_response({"success": False, "error": i18n.t("db.sync_failed")}, 500)
        return json_response({"success": True, "written": written, "message": i18n.t("db.sync_done", rows=written),
                              **runtime.history_db.buffer_status()})

    @get("/config/ipmi-status", sync_to_thread=True)
    def ipmi_status(self, runtime: NamedDependency[Runtime]) -> Response:
        try:
            ipmi_hosts = [t.name for t in runtime.targets.all() if t.type == "ipmi"] if runtime.targets else []
            return json_response({
                "ipmi_available": bool(shutil.which("ipmitool")),
                "ipmitool_path": shutil.which("ipmitool"),
                "ipmi_hosts_configured": ipmi_hosts,
            })
        except Exception as e:
            logger.error(f"❌ IPMI Status API Error: {e}")
            return json_response({"error": str(e), "ipmi_available": False}, 500)

    # --------------------------------------------------------
    # Profil-Verwaltung
    # --------------------------------------------------------

    @post("/admin/profile-create", guards=[requires_user_or_admin], status_code=200, sync_to_thread=False)
    def profile_create(self, runtime: NamedDependency[Runtime], data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
        data = data or {}
        name = str(data.get('name', '')).strip()
        if not name:
            return json_response({"error": i18n.t("errors.profile_name_required")}, 400)
        if _snmp3_incomplete(data):
            return json_response({"error": i18n.t("errors.snmp3_incomplete")}, 400)
        try:
            if not runtime.config_manager.add_profile(name, _build_profile_config(data)):
                return json_response({"error": i18n.t("errors.profile_exists", name=name)}, 409)
            return json_response({"success": True, "profile": name})
        except Exception as e:
            logger.error(f"❌ Fehler beim Erstellen des Profils: {e}")
            return json_response({"error": str(e)}, 500)

    @post("/admin/profile-update", guards=[requires_admin], status_code=200, sync_to_thread=False)
    def profile_update(self, runtime: NamedDependency[Runtime], data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
        data = data or {}
        name = str(data.get('name', '')).strip()
        if not name:
            return json_response({"error": i18n.t("errors.profile_name_required")}, 400)
        if _snmp3_incomplete(data, runtime.config_manager.get_profile(name)):
            return json_response({"error": i18n.t("errors.snmp3_incomplete")}, 400)
        try:
            config = _build_profile_config(data)
            if not runtime.config_manager.update_profile(name, config):
                return json_response({"error": i18n.t("errors.profile_not_found", name=name)}, 404)
            if config.get('enabled') is False:
                runtime.disable_profile(name)
            return json_response({"success": True, "profile": name})
        except Exception as e:
            logger.error(f"❌ Fehler beim Aktualisieren des Profils: {e}")
            return json_response({"error": str(e)}, 500)

    @post("/admin/profile-delete", guards=[requires_admin], status_code=200, sync_to_thread=False)
    def profile_delete(self, runtime: NamedDependency[Runtime], data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
        name = (data or {}).get('profile')
        if not name:
            return json_response({"error": i18n.t("errors.no_profile_given")}, 400)
        try:
            if runtime.config_manager.delete_profile(name):
                return json_response({"success": True, "profile": name})
            return json_response({"error": i18n.t("errors.profile_not_found", name=name)}, 404)
        except Exception as e:
            logger.error(f"❌ Fehler beim Löschen des Profils: {e}")
            return json_response({"error": str(e)}, 500)

    @get("/diagnostic/dell-temp", guards=[requires_admin], sync_to_thread=False)
    def dell_temp(self, runtime: NamedDependency[Runtime]) -> Response:
        d = runtime.current_ups_data
        return json_response({
            "profile": d.get('profile'),
            "manufacturer": d.get('manufacturer'),
            "battery_temp": d.get('battery_temp'),
            "battery_temp_celsius": d.get('battery_temp_celsius'),
            "timestamp": datetime.now().isoformat(),
        })

    # --------------------------------------------------------
    # Test & Simulation
    # --------------------------------------------------------

    @get("/test/simulation-status", sync_to_thread=False)
    def simulation_status(self) -> Response:
        events = get_simulation_events()
        return json_response({
            "simulation_mode_active": get_simulation_mode(),
            "simulation_events_count": len(events),
            "recent_events": events[-10:],
            "timestamp": datetime.now().isoformat(),
        })

    @post("/test/simulate-incident", guards=[requires_operator], status_code=200, sync_to_thread=False)
    def simulate_incident(self, data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
        data = data or {}
        scenario = data.get('scenario', 'POWER_FAILURE')
        ups_id = data.get('ups_id', '1')
        message = data.get('message', f'Simulated {scenario} on UPS {ups_id}')
        enable_sim = data.get('enable_simulation', True)

        set_simulation_mode(enable_sim)
        if not enable_sim:
            return json_response({"success": True, "message": "Simulation-Modus deaktiviert", "simulation_active": False})

        logger.warning(f"🧪 SIMULATION: {scenario} auf USV {ups_id} triggered")
        event = {
            "event_id": str(uuid.uuid4()),
            "correlation_id": str(uuid.uuid4()),
            "sequence": 1,
            "scenario": scenario,
            "ups_id": ups_id,
            "message": message,
            "timestamp": datetime.now().isoformat(),
            "simulation": True,
            "status": "SIMULATED_SUCCESS",
        }
        add_simulation_event(event)
        return json_response({
            "success": True,
            "message": f"Incident {scenario} simuliert",
            "simulation_active": True,
            "event": event,
            "policy_engine_triggered": False,    # nur ein Protokolleintrag; Regeln löst POST /api/policies/{name}/trigger aus
            "action_executor_mode": "SIMULATED_SUCCESS",
            "timestamp": datetime.now().isoformat(),
        })

    @post("/test/simulate-cascade", guards=[requires_operator], status_code=200, sync_to_thread=False)
    def simulate_cascade(self, data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
        data = data or {}
        cascade_name = data.get('cascade_name', 'Test_Cascade')
        phases = data.get('phases', [])
        if not phases:
            return json_response({"error": "Keine Phasen angegeben"}, 400)

        set_simulation_mode(True)
        logger.warning(f"🧪 KASKADE SIMULATION: {cascade_name} mit {len(phases)} Phasen")
        correlation_id = str(uuid.uuid4())
        events = []
        for idx, phase in enumerate(phases):
            event = {
                "event_id": str(uuid.uuid4()),
                "correlation_id": correlation_id,
                "sequence": idx + 1,
                "scenario": phase.get('scenario', 'POWER_FAILURE'),
                "ups_id": phase.get('ups_id', '1'),
                "delay_seconds": phase.get('delay', 0),
                "timestamp": datetime.now().isoformat(),
                "simulation": True,
                "status": "SIMULATED_SUCCESS",
            }
            add_simulation_event(event)
            events.append(event)
        return json_response({
            "success": True,
            "cascade_name": cascade_name,
            "correlation_id": correlation_id,
            "phases": len(phases),
            "simulation_active": True,
            "events": events,
            "action_executor_mode": "SIMULATED_SUCCESS",
            "timestamp": datetime.now().isoformat(),
        })

    @post("/test/clear-simulations", guards=[requires_admin], status_code=200, sync_to_thread=False)
    def clear_simulations(self) -> Response:
        set_simulation_mode(False)
        logger.info("🧹 Simulations-Log gelöscht")
        return json_response({
            "success": True,
            "message": "Simulations-Modus deaktiviert und Log geleert",
            "timestamp": datetime.now().isoformat(),
        })
