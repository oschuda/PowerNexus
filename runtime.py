#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Evolved PowerNexus Runtime (framework-neutral)

Hält alle Fachobjekte (Konfiguration, Datenbank, Analyzer, Event-/Policy-Engine)
und den Polling-Thread.

Polling: Alle Profile werden in jeder Runde abgefragt. Das aktive Profil
liefert die Live-Daten des Dashboards. Für jedes Profil werden Historie und
Netzqualität gespeichert und Statuswechsel als Meldungen (Events) erfasst.
Die Policy-Engine (automatische Aktionen) erhält nur die Events des aktiven
Profils. Wird von der Litestar-App (app.py) beim Start erzeugt
und beim Herunterfahren gestoppt.

Portiert aus app_flask.py (v20.1.1): main() + polling_worker().
"""

import asyncio
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Optional

from snmp_managers import USBUPSManager, SNMPManager

logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent.absolute()
CONFIG_PATH = SCRIPT_DIR / "config.json"
DATA_PATH = SCRIPT_DIR / "data"

DATA_CACHE_TIMEOUT = 300   # Nach 5 Min ohne erfolgreichen Abruf als OFFLINE markieren
POLL_INTERVAL = 60         # Sekunden pro Polling-Runde
POLL_TIMEOUT = 55          # Timeout pro Abfrage (< Intervall)
SAVE_INTERVAL = 60         # History alle 60 s speichern


def create_ups_manager(profile: Dict, demo_mode: bool = False):
    if profile.get('connection_type', 'snmp') == 'usb':
        return USBUPSManager(profile, demo_mode)
    return SNMPManager(profile, demo_mode)


def profile_enabled(profile: Optional[Dict[str, Any]]) -> bool:
    """Profil wird abgefragt, solange es nicht ausdrücklich mit "enabled": false deaktiviert ist."""
    return bool(profile) and profile.get('enabled', True) is not False


def _to_float(value) -> Optional[float]:
    try:
        return float(str(value).replace('V', '').strip())
    except (TypeError, ValueError):
        return None


class Runtime:
    """Laufzeit-Container für alle Fachobjekte und den Polling-Thread."""

    def __init__(self) -> None:
        self.config_manager = None
        self.history_db = None
        self.battery_analyzer = None
        self.grid_analyzer = None
        self.demo_mode = False

        # Phase 5 (Event & Policy Engine)
        self.event_loop_thread = None
        self.asyncio_loop = None
        self.nut_event_mapper = None
        self.state_event_tracker = None
        self.policy_engine = None
        self.group_manager = None
        self.targets = None
        self.policies = None
        self.mail_store = None
        self.mailer = None
        self.services = None
        self.solar = None

        # Polling-Zustand
        self.current_ups_data: Dict[str, Any] = {}
        self._cached_ups_data: Dict[str, Any] = {}
        self._last_data_update = 0.0
        self.last_successful_poll = 0.0
        self.current_profile_name = ""
        self._ups_manager = None
        self._prev_status: Dict[str, str] = {}   # Profil-Schlüssel → letzte Status-Kürzel
        self._last_save_time: Dict[str, int] = {}
        # Letzte erfolgreiche Messung je Profil-Schlüssel (für Netzqualität je Standort)
        self.latest_by_profile: Dict[str, Dict[str, Any]] = {}

        self._lock = threading.RLock()
        self._shutdown = threading.Event()
        self._wake = threading.Event()
        self._poll_thread: Optional[threading.Thread] = None
        self._executor: Optional[ThreadPoolExecutor] = None

    # ------------------------------------------------------------
    # Konfiguration
    # ------------------------------------------------------------

    @staticmethod
    def load_config() -> Dict[str, Any]:
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"❌ config.json konnte nicht geladen werden: {e}")
            return {"active_profile": "", "demo_mode": False, "profiles": {}}

    # ------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------

    def start(self) -> None:
        from config import ConfigManager
        from battery_health import BatteryHealthAnalyzer
        from grid_quality import GridQualityAnalyzer
        from database import Database

        DATA_PATH.mkdir(parents=True, exist_ok=True)
        self.config_manager = ConfigManager(SCRIPT_DIR)      # legt beim ersten Start config.json an
        config = self.load_config()
        self.history_db = Database(DATA_PATH)            # muss vor GridQualityAnalyzer existieren
        self.battery_analyzer = BatteryHealthAnalyzer()
        self.grid_analyzer = GridQualityAnalyzer(self.history_db)
        self.demo_mode = config.get('demo_mode', False)
        logger.info(f"✅ Manager-Objekte initialisiert (demo_mode={self.demo_mode})")

        self._init_event_engine()
        self._init_solar()

        self.current_profile_name = config.get('active_profile', '')
        profile = config.get('profiles', {}).get(self.current_profile_name)
        if profile_enabled(profile):
            self._ups_manager = create_ups_manager(profile, self.demo_mode)
        elif profile:
            logger.warning(f"⚠️ Aktives Profil '{self.current_profile_name}' ist deaktiviert – keine Abfrage")
        else:
            logger.warning(f"⚠️ Aktives Profil '{self.current_profile_name}' nicht in config.json")

        # Deaktivierte Profile: noch offene Meldungen schließen
        for key, cfg in config.get('profiles', {}).items():
            if not profile_enabled(cfg):
                closed = self.history_db.resolve_open_events(key)
                logger.info(f"⏸️ Profil '{key}' deaktiviert" + (f" – {closed} offene Meldung(en) geschlossen" if closed else ""))

        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="PollerThread")
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True, name="UPS-PollingWorker")
        self._poll_thread.start()
        logger.info(f"✅ Polling-Worker gestartet (Profil: {self.current_profile_name})")

        # Batterie-Prognose (scikit-learn) vorladen: der erste Aufruf dauert auf dem Pi ~5 s
        threading.Thread(target=self._warm_up_battery_predictor, daemon=True, name="ML-WarmUp").start()

    @staticmethod
    def _warm_up_battery_predictor() -> None:
        try:
            from battery_ml import get_battery_predictor
            get_battery_predictor()
            logger.info("✅ Batterie-Prognose geladen")
        except Exception as e:
            logger.warning(f"⚠️ Batterie-Prognose konnte nicht vorgeladen werden: {e}")

    def _init_event_engine(self) -> None:
        try:
            from event_loop_thread import EventLoopThread
            from event_normalizer import NUTEventMapper, StateEventTracker
            from ups_group_manager import UPSGroupManager
            from automation.engine import AutomationEngine
            from automation.mail_settings import MailStore
            from automation.mailer import Mailer
            from automation.services import ServiceManager
            from event_normalizer import status_tokens
            from automation.policies import PolicyStore
            from automation.targets import TargetStore
            from automation_state import get_automation_paused

            self.event_loop_thread = EventLoopThread()
            self.asyncio_loop = self.event_loop_thread.start()
            self.nut_event_mapper = NUTEventMapper()
            self.state_event_tracker = StateEventTracker()
            self.group_manager = UPSGroupManager(DATA_PATH / "usv_history.db")

            self.targets = TargetStore(SCRIPT_DIR / "hosts.yaml")
            self.policies = PolicyStore(SCRIPT_DIR / "policies.yaml")
            self.mail_store = MailStore(SCRIPT_DIR / "notifications.yaml")
            self.mailer = Mailer(loop=self.asyncio_loop, store=self.mail_store,
                                 record=self._record_automation_event, ups_info=self._ups_info_for)
            self.mailer.start_expiry_watch()
            self.policy_engine = AutomationEngine(
                loop=self.asyncio_loop,
                targets=self.targets,
                policies=self.policies,
                record=self._record_automation_event,
                data_provider=self._data_for,
                groups_provider=self._groups_for,
                dry_run_provider=lambda: self.dry_run,
                paused_provider=get_automation_paused,
                notifier=self.mailer,
                run_store=self.history_db,
            )
            asyncio.run_coroutine_threadsafe(self.policy_engine.recover(), self.asyncio_loop).result(10)
            self.services = ServiceManager(SCRIPT_DIR / "services.yaml", self.asyncio_loop, self._data_for, status_tokens,
                                           self._profile_title)
            self.services.start()
            logger.info(f"✅ Automatisierung: {len(self.targets.all())} Zielrechner, "
                        f"{len(self.policies.all())} Regeln, Testmodus={'an' if self.dry_run else 'aus'}")
        except Exception as e:
            logger.error(f"❌ Fehler bei der Initialisierung von Meldungen/Automatisierung: {e}", exc_info=True)
            self.event_loop_thread = None
            self.asyncio_loop = None
            self.nut_event_mapper = None
            self.state_event_tracker = None
            self.policy_engine = None
            self.mailer = None
            self.services = None

    def _init_solar(self) -> None:
        """PV-Wechselrichter per MQTT (solar.yaml). Ein Fehler hier darf die USV-Überwachung nicht aufhalten."""
        try:
            from solar.mqtt import SolarService
            from solar.settings import SolarSettingsStore
            self.solar = SolarService(SolarSettingsStore(SCRIPT_DIR / "solar.yaml"), self.history_db)
            self.solar.start()
        except Exception as e:
            logger.error(f"❌ PV-Anbindung nicht verfügbar: {e}", exc_info=True)
            self.solar = None

    # ------------------------------------------------------------
    # Automatisierung: Testmodus, Datenquellen, Protokoll
    # ------------------------------------------------------------

    @property
    def dry_run(self) -> bool:
        """Globaler Testmodus: Einstellung in config.json oder erzwungen per UPS_DRY_RUN."""
        import os
        if os.environ.get('UPS_DRY_RUN', '').lower() in ('true', '1', 'yes', 'on'):
            return True
        return bool(self.config_manager and self.config_manager.config.get('dry_run', False))

    @property
    def dry_run_forced(self) -> bool:
        import os
        return os.environ.get('UPS_DRY_RUN', '').lower() in ('true', '1', 'yes', 'on')

    def set_dry_run(self, enabled: bool) -> None:
        self.config_manager.config['dry_run'] = bool(enabled)
        self.config_manager.save_config(self.config_manager.config)
        logger.warning(f"🧪 Testmodus {'EINGESCHALTET' if enabled else 'AUSGESCHALTET'}")

    def _data_for(self, profile_key: str) -> Optional[Dict[str, Any]]:
        data = self.latest_data(profile_key)
        if not data and profile_key == self.current_profile_name:
            data = self.current_ups_data
        return data

    def _ups_info_for(self, profile_key: str) -> Dict[str, Any]:
        """Name, Standort und aktuelle Messwerte einer USV für E-Mails."""
        cm = self.config_manager
        profile = cm.get_profile(profile_key) or {}
        return {**(self._data_for(profile_key) or {}), "name": profile.get("name", profile_key),
                "location": cm.get_profile_location(profile_key)}

    def _profile_title(self, profile_key: str) -> str:
        return (self.config_manager.get_profile(profile_key) or {}).get("name", profile_key)

    def _groups_for(self, profile_key: str) -> list:
        if not self.group_manager:
            return []
        result = []
        for group_id in self.group_manager.get_ups_groups(profile_key):
            result.append(str(group_id))
            group = self.group_manager.get_group(group_id)
            if group:
                result.append(group.name)
        return result

    def _record_automation_event(self, event_type: str, ups_id: str, message: str) -> None:
        from event_normalizer import UnifiedEvent, UnifiedEventType
        event = UnifiedEvent(event_type=UnifiedEventType(event_type), source="AUTOMATION",
                             ups_id=ups_id, raw_message=message)
        self.history_db.insert_event(event)
        self.history_db.insert_audit_trail(user_id="system", action=event_type, status=event.severity.value, target=ups_id, details=message)
        logger.warning(f"🤖 {message}")

    def stop(self) -> None:
        self._shutdown.set()
        self._wake.set()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=5.0)
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)
        if self.solar is not None:
            try:
                self.solar.stop()
            except Exception as e:
                logger.debug(f"Fehler beim Beenden der PV-Anbindung: {e}")
        if self.history_db:
            try:
                self.history_db.shutdown()
            except Exception as e:
                logger.debug(f"Fehler beim Beenden des DB-Writers: {e}")
        if self.policy_engine is not None:
            self.policy_engine.shutdown()
        if self.services is not None:
            try:
                self.services.stop()
            except Exception as e:
                logger.debug(f"Fehler beim Beenden der Dienste: {e}")
        if self.mailer is not None:
            try:
                self.mailer.stop()
            except Exception as e:
                logger.debug(f"Fehler beim Beenden des E-Mail-Versands: {e}")
        if self.event_loop_thread is not None:
            try:
                self.event_loop_thread.stop()
            except Exception as e:
                logger.debug(f"Fehler beim Beenden des Event Loop: {e}")
        logger.info("✅ Runtime heruntergefahren")

    # ------------------------------------------------------------
    # Profilwechsel
    # ------------------------------------------------------------

    def switch_profile(self, profile_name: str, profile: Dict[str, Any]) -> Dict[str, Any]:
        """Wechselt das abgefragte Profil, fragt einmal sofort ab und übergibt an den Polling-Thread."""
        manager = create_ups_manager(profile, self.demo_mode)
        data = manager.get_all_data()
        with self._lock:
            self._ups_manager = manager
            self.current_profile_name = profile_name
            self._cached_ups_data = {}
            self._last_data_update = 0.0
            if data and data.get('status_text') != "UNKNOWN":
                self._cached_ups_data = data.copy()
                self._last_data_update = time.time()
                self.last_successful_poll = time.time()
            self.current_ups_data = dict(data or {})
            self.current_ups_data["is_cached"] = False
        logger.info(f"✅ Profil gewechselt zu: {profile_name}")
        return data

    def disable_profile(self, profile_key: str) -> None:
        """Profil wurde deaktiviert: Live-Daten, Event-Zustand und offene Meldungen verwerfen."""
        with self._lock:
            self.latest_by_profile.pop(profile_key, None)
            self._prev_status.pop(profile_key, None)
            if self.state_event_tracker is not None:
                self.state_event_tracker._active_states.pop(profile_key, None)
            if profile_key == self.current_profile_name:
                self._ups_manager = None
        self.history_db.resolve_open_events(profile_key)

    def latest_data(self, profile_key: str) -> Optional[Dict[str, Any]]:
        """Letzte erfolgreiche Messung eines Profils, wenn nicht älter als DATA_CACHE_TIMEOUT."""
        with self._lock:
            entry = self.latest_by_profile.get(profile_key)
        if not entry or time.time() - entry['_polled_at'] > DATA_CACHE_TIMEOUT:
            return None
        return entry

    # ------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------

    def _poll_loop(self) -> None:
        logger.info("▶️ Polling-Worker gestartet")
        while not self._shutdown.is_set():
            try:
                self._poll_once()
            except Exception as e:
                logger.error(f"❌ Polling-Worker Fehler: {e}", exc_info=True)
            self._wake.wait(timeout=POLL_INTERVAL)
            self._wake.clear()
        logger.info("🛑 Polling-Worker beendet")

    def _poll_once(self) -> None:
        with self._lock:
            manager = self._ups_manager
            active_key = self.current_profile_name

        # Alle übrigen Profile parallel mit abfragen (nur Historie/Netzqualität)
        others = {}
        for key in self.config_manager.list_profiles():
            if key == active_key:
                continue
            profile = self.config_manager.get_profile(key)
            if profile_enabled(profile):
                others[key] = self._executor.submit(create_ups_manager(profile, self.demo_mode).get_all_data)

        if manager is not None:
            self._poll_active(manager, active_key)

        for key, future in others.items():
            try:
                data = future.result(timeout=POLL_TIMEOUT)
            except Exception as e:
                logger.debug(f"Polling {key}: {e}")
                data = None
            ok = bool(data) and data.get('status_text') != "UNKNOWN"
            with self._lock:
                if ok:
                    self.latest_by_profile[key] = {**data, '_polled_at': time.time()}
                    self._maybe_save(key, data, battery_lifecycle=False)
                self._detect_events(key, data if ok else None)

    def _poll_active(self, manager, active_key: str) -> None:
        data = None
        try:
            future = self._executor.submit(manager.get_all_data)
            data = future.result(timeout=POLL_TIMEOUT)
        except Exception as e:
            logger.warning(f"⚠️ Polling Timeout/Error: {e}")

        with self._lock:
            if manager is not self._ups_manager:
                return  # Profil wurde während der Abfrage gewechselt

            if data and data.get('status_text') != "UNKNOWN":
                self._cached_ups_data = data.copy()
                self._last_data_update = time.time()
                self.last_successful_poll = time.time()
                self.current_ups_data = dict(data)
                self.current_ups_data["is_cached"] = False
                self.latest_by_profile[active_key] = {**data, '_polled_at': time.time()}
                self._detect_events(active_key, data)
                self._maybe_save(active_key, data, battery_lifecycle=True)
            else:
                self._detect_events(active_key, None)
                age = time.time() - self._last_data_update
                if self._cached_ups_data and age < DATA_CACHE_TIMEOUT:
                    self.current_ups_data = self._cached_ups_data.copy()
                    self.current_ups_data["is_cached"] = True
                    self.current_ups_data["data_age_seconds"] = int(age)
                    self.current_ups_data["status_text"] = (
                        f"{self.current_ups_data.get('status_text', 'CACHED')} (Cache: {int(age)}s)"
                    )
                elif self._cached_ups_data:
                    self.current_ups_data = self._cached_ups_data.copy()
                    self.current_ups_data["is_cached"] = False
                    self.current_ups_data["status_text"] = "OFFLINE"
                    logger.warning("❌ Cache zu alt, OFFLINE")
                else:
                    self.current_ups_data["status_text"] = "UNKNOWN"
                    logger.warning("⚠️ Kein Cache, UNKNOWN")

            d = self.current_ups_data
            logger.info(
                f"📡 Status={d.get('status_text', 'Unknown')}, Batterie={d.get('battery_charge', 0)}%, "
                f"Laufzeit={d.get('runtime_minutes', 0)}min"
            )

    def _detect_events(self, key: str, data: Optional[Dict[str, Any]]) -> None:
        """Statuswechsel eines Profils als Meldungen erfassen (ups_id = Profil-Schlüssel).

        Beim ersten Abruf nach dem Start werden bestehende Problemzustände (z. B. Batterie
        austauschen, Stromausfall) gemeldet, sofern dazu noch keine offene Meldung existiert.
        """
        if self.nut_event_mapper is None or self.state_event_tracker is None:
            return
        from event_normalizer import NUT_STATUS_STATE_PAIRS, PROBLEM_TOKENS, UnifiedEvent, status_tokens

        tokens = status_tokens(data)
        prev = self._prev_status.get(key)
        self._prev_status[key] = tokens
        if self.policy_engine is not None and self.policy_engine.has_leftover(key):
            active = {NUT_STATUS_STATE_PAIRS[t][0].value for t in tokens.split() if t in NUT_STATUS_STATE_PAIRS}
            self.policy_engine.resume_pending(key, active, unknown=tokens == "COMMLOST")
        profile_cfg = self.config_manager.get_profile(key) or {}
        info = data or {'profile': profile_cfg.get('name', key),
                        'connection_type': profile_cfg.get('connection_type', 'snmp')}

        if prev is None:
            events = []
            for token in tokens.split():
                if token not in PROBLEM_TOKENS:
                    continue
                on_type = NUT_STATUS_STATE_PAIRS[token][0]
                open_row = self.history_db.get_open_event(key, on_type.value)
                if open_row:
                    # Offene Meldung aus der Datenbank wieder als aktiven Zustand übernehmen
                    restored = UnifiedEvent(event_type=on_type, source=open_row['source'], ups_id=key,
                                            event_id=open_row['event_id'], raw_message=open_row['raw_message'])
                    self.state_event_tracker.process(restored)
                else:
                    events += self.nut_event_mapper.map_status_change("", token, key, info)
        elif prev != tokens:
            events = self.nut_event_mapper.map_status_change(prev, tokens, key, info)
        else:
            return

        for event in events:
            enriched = self.state_event_tracker.process(event)
            self.history_db.insert_event(enriched)
            logger.warning(f"📣 Meldung {key}: {enriched.raw_message} ({enriched.severity.value})")
            # Regeln und E-Mails gelten für alle überwachten USVs, nicht nur für die angezeigte
            for receiver in (self.policy_engine, self.mailer):
                if receiver is None:
                    continue
                try:
                    receiver.on_event(enriched)
                except Exception as e:
                    logger.error(f"❌ Weitergabe der Meldung fehlgeschlagen: {e}")

    def _maybe_save(self, profile_key: str, data: Dict[str, Any], battery_lifecycle: bool) -> None:
        now = int(time.time())
        if now - self._last_save_time.get(profile_key, 0) < SAVE_INTERVAL:
            return
        if not self.history_db.insert(data):
            return
        self._last_save_time[profile_key] = now

        voltage = _to_float(data.get('input_voltage_formatted')) or _to_float(data.get('input_voltage'))
        frequency = _to_float(data.get('input_frequency'))
        if voltage:
            try:
                self.history_db.insert_grid_quality(ups_id=profile_key, voltage=voltage, frequency=frequency, thd=None)
            except Exception as e:
                logger.debug(f"⚠️ Grid-Quality: {e}")
        if not battery_lifecycle:
            return
        try:
            self.history_db.insert_battery_lifecycle(
                ups_id=str(data.get('ups_name', '1')),
                charge=float(data.get('battery_charge', 0)),
                voltage=float(data.get('battery_voltage_formatted', 0)),
                temperature=float(data.get('battery_temp_celsius', 0)),
            )
        except Exception as e:
            logger.debug(f"⚠️ Battery-Lifecycle: {e}")
