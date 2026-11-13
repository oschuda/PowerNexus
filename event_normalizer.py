"""
USV Event Normalization System
Wandelt rohe NUT-Daten in canonical UnifiedEvent-Objekte um.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class UnifiedEventType(str, Enum):
    """Alle möglichen Event-Typen des Systems (45 total)."""
    # Power
    POWER_FAILURE          = "POWER_FAILURE"
    POWER_RESTORED         = "POWER_RESTORED"
    # Battery
    BATTERY_LOW            = "BATTERY_LOW"
    BATTERY_LOW_END        = "BATTERY_LOW_END"
    # Automatisierung (Protokoll der Regel-Ausführungen)
    POLICY_TRIGGERED       = "POLICY_TRIGGERED"
    POLICY_CANCELLED       = "POLICY_CANCELLED"
    POLICY_BLOCKED         = "POLICY_BLOCKED"
    POLICY_ACTION_OK       = "POLICY_ACTION_OK"
    POLICY_ACTION_FAILED   = "POLICY_ACTION_FAILED"
    POLICY_COMPLETED       = "POLICY_COMPLETED"
    # E-Mail
    MAIL_FAILED            = "MAIL_FAILED"
    MAIL_CREDENTIAL_EXPIRING = "MAIL_CREDENTIAL_EXPIRING"
    BATTERY_CRITICAL       = "BATTERY_CRITICAL"
    BATTERY_CHARGED        = "BATTERY_CHARGED"
    BATTERY_CHARGING       = "BATTERY_CHARGING"
    BATTERY_DISCHARGING    = "BATTERY_DISCHARGING"
    BATTERY_DISCHARGING_END = "BATTERY_DISCHARGING_END"
    BATTERY_REPLACE        = "BATTERY_REPLACE"
    BATTERY_REPLACED       = "BATTERY_REPLACED"
    BATTERY_NOT_PRESENT    = "BATTERY_NOT_PRESENT"
    BATTERY_NOT_PRESENT_END = "BATTERY_NOT_PRESENT_END"
    # Load / Output
    OVERLOAD               = "OVERLOAD"
    OVERLOAD_END           = "OVERLOAD_END"
    BYPASS_ACTIVE          = "BYPASS_ACTIVE"
    BYPASS_END             = "BYPASS_END"
    BOOST_ACTIVE           = "BOOST_ACTIVE"
    BOOST_END              = "BOOST_END"
    TRIM_ACTIVE            = "TRIM_ACTIVE"
    TRIM_END               = "TRIM_END"
    CALIBRATING            = "CALIBRATING"
    CALIBRATING_END        = "CALIBRATING_END"
    OUTPUT_OFF             = "OUTPUT_OFF"
    OUTPUT_OFF_END         = "OUTPUT_OFF_END"
    FORCED_SHUTDOWN        = "FORCED_SHUTDOWN"
    # Temperature
    TEMPERATURE_HIGH       = "TEMPERATURE_HIGH"
    TEMPERATURE_NORMAL     = "TEMPERATURE_NORMAL"
    # Alarm
    GENERAL_ALARM          = "GENERAL_ALARM"
    GENERAL_ALARM_END      = "GENERAL_ALARM_END"
    # Communication
    COMMUNICATION_LOST     = "COMMUNICATION_LOST"
    COMMUNICATION_RESTORED = "COMMUNICATION_RESTORED"
    # Misc
    SHUTDOWN_PENDING       = "SHUTDOWN_PENDING"
    TEST_IN_PROGRESS       = "TEST_IN_PROGRESS"
    TEST_COMPLETE          = "TEST_COMPLETE"
    INPUT_VOLTAGE_HIGH     = "INPUT_VOLTAGE_HIGH"
    INPUT_VOLTAGE_LOW      = "INPUT_VOLTAGE_LOW"
    INPUT_VOLTAGE_NORMAL   = "INPUT_VOLTAGE_NORMAL"
    OUTPUT_VOLTAGE_HIGH    = "OUTPUT_VOLTAGE_HIGH"
    OUTPUT_VOLTAGE_LOW     = "OUTPUT_VOLTAGE_LOW"
    OUTPUT_VOLTAGE_NORMAL  = "OUTPUT_VOLTAGE_NORMAL"
    LOAD_HIGH              = "LOAD_HIGH"
    LOAD_NORMAL            = "LOAD_NORMAL"
    RUNTIME_LOW            = "RUNTIME_LOW"
    RUNTIME_NORMAL         = "RUNTIME_NORMAL"
    UNKNOWN_EVENT          = "UNKNOWN_EVENT"
    STARTUP                = "STARTUP"
    # Hardware-Fehler (aus Hersteller-Alarm-Tabelle)
    BATTERY_FAULT          = "BATTERY_FAULT"       # Batterie defekt / bad
    BYPASS_FAULT           = "BYPASS_FAULT"
    SITE_WIRING_FAULT      = "SITE_WIRING_FAULT"
    OUTPUT_SHORT           = "OUTPUT_SHORT"
    INVERTER_FAULT         = "INVERTER_FAULT"
    INVERTER_OVERCURRENT   = "INVERTER_OVERCURRENT"
    INVERTER_OVERLOAD      = "INVERTER_OVERLOAD"
    FAN_FAILURE            = "FAN_FAILURE"
    CHARGER_FAILURE        = "CHARGER_FAILURE"
    DC_BUS_FAULT           = "DC_BUS_FAULT"
    EEPROM_FAULT           = "EEPROM_FAULT"
    SYNC_ERROR             = "SYNC_ERROR"
    PARALLEL_FAULT         = "PARALLEL_FAULT"
    LOAD_CRITICAL          = "LOAD_CRITICAL"       # Last >90 %
    EPO_ACTIVE             = "EPO_ACTIVE"          # Emergency Power Off


class EventSeverity(str, Enum):
    """Event-Schweregrade."""
    EMERGENCY = "EMERGENCY"   # Sofortige Aktion zwingend
    CRITICAL  = "CRITICAL"    # Dringende Maßnahme erforderlich
    ERROR     = "ERROR"       # Fehler, Überwachung nötig
    WARNING   = "WARNING"     # Warnung
    INFO      = "INFO"        # Informativ


# Numerische Ordnung für Schweregrad-Vergleiche (KEIN str-Vergleich!)
SEVERITY_ORDER: dict[str, int] = {
    "INFO":      0,
    "WARNING":   1,
    "ERROR":     2,
    "CRITICAL":  3,
    "EMERGENCY": 4,
}


# Severity-Mapping für alle Event-Typen
SEVERITY_MAP: dict[UnifiedEventType, EventSeverity] = {
    UnifiedEventType.POWER_FAILURE:          EventSeverity.CRITICAL,
    UnifiedEventType.POWER_RESTORED:         EventSeverity.INFO,
    UnifiedEventType.BATTERY_LOW:            EventSeverity.CRITICAL,
    UnifiedEventType.BATTERY_CRITICAL:       EventSeverity.EMERGENCY,
    UnifiedEventType.BATTERY_CHARGED:        EventSeverity.INFO,
    UnifiedEventType.BATTERY_CHARGING:       EventSeverity.INFO,
    UnifiedEventType.BATTERY_DISCHARGING:    EventSeverity.WARNING,
    UnifiedEventType.BATTERY_DISCHARGING_END: EventSeverity.INFO,
    UnifiedEventType.BATTERY_REPLACE:        EventSeverity.WARNING,
    UnifiedEventType.BATTERY_REPLACED:       EventSeverity.INFO,
    UnifiedEventType.BATTERY_LOW_END:        EventSeverity.INFO,
    UnifiedEventType.POLICY_TRIGGERED:       EventSeverity.WARNING,
    UnifiedEventType.POLICY_CANCELLED:       EventSeverity.INFO,
    UnifiedEventType.POLICY_BLOCKED:         EventSeverity.WARNING,
    UnifiedEventType.POLICY_ACTION_OK:       EventSeverity.INFO,
    UnifiedEventType.POLICY_ACTION_FAILED:   EventSeverity.ERROR,
    UnifiedEventType.POLICY_COMPLETED:       EventSeverity.INFO,
    UnifiedEventType.MAIL_FAILED:            EventSeverity.ERROR,
    UnifiedEventType.MAIL_CREDENTIAL_EXPIRING: EventSeverity.WARNING,
    UnifiedEventType.BATTERY_NOT_PRESENT:    EventSeverity.ERROR,
    UnifiedEventType.BATTERY_NOT_PRESENT_END: EventSeverity.INFO,
    UnifiedEventType.OVERLOAD:               EventSeverity.CRITICAL,
    UnifiedEventType.OVERLOAD_END:           EventSeverity.INFO,
    UnifiedEventType.BYPASS_ACTIVE:          EventSeverity.WARNING,
    UnifiedEventType.BYPASS_END:             EventSeverity.INFO,
    UnifiedEventType.BOOST_ACTIVE:           EventSeverity.WARNING,
    UnifiedEventType.BOOST_END:              EventSeverity.INFO,
    UnifiedEventType.TRIM_ACTIVE:            EventSeverity.WARNING,
    UnifiedEventType.TRIM_END:               EventSeverity.INFO,
    UnifiedEventType.CALIBRATING:            EventSeverity.INFO,
    UnifiedEventType.CALIBRATING_END:        EventSeverity.INFO,
    UnifiedEventType.OUTPUT_OFF:             EventSeverity.CRITICAL,
    UnifiedEventType.OUTPUT_OFF_END:         EventSeverity.INFO,
    UnifiedEventType.FORCED_SHUTDOWN:        EventSeverity.EMERGENCY,
    UnifiedEventType.TEMPERATURE_HIGH:       EventSeverity.ERROR,
    UnifiedEventType.TEMPERATURE_NORMAL:     EventSeverity.INFO,
    UnifiedEventType.GENERAL_ALARM:          EventSeverity.WARNING,
    UnifiedEventType.GENERAL_ALARM_END:      EventSeverity.INFO,
    UnifiedEventType.COMMUNICATION_LOST:     EventSeverity.ERROR,
    UnifiedEventType.COMMUNICATION_RESTORED: EventSeverity.INFO,
    UnifiedEventType.SHUTDOWN_PENDING:       EventSeverity.CRITICAL,
    UnifiedEventType.TEST_IN_PROGRESS:       EventSeverity.INFO,
    UnifiedEventType.TEST_COMPLETE:          EventSeverity.INFO,
    UnifiedEventType.INPUT_VOLTAGE_HIGH:     EventSeverity.WARNING,
    UnifiedEventType.INPUT_VOLTAGE_LOW:      EventSeverity.WARNING,
    UnifiedEventType.INPUT_VOLTAGE_NORMAL:   EventSeverity.INFO,
    UnifiedEventType.OUTPUT_VOLTAGE_HIGH:    EventSeverity.WARNING,
    UnifiedEventType.OUTPUT_VOLTAGE_LOW:     EventSeverity.WARNING,
    UnifiedEventType.OUTPUT_VOLTAGE_NORMAL:  EventSeverity.INFO,
    UnifiedEventType.LOAD_HIGH:              EventSeverity.WARNING,
    UnifiedEventType.LOAD_NORMAL:            EventSeverity.INFO,
    UnifiedEventType.RUNTIME_LOW:            EventSeverity.WARNING,
    UnifiedEventType.RUNTIME_NORMAL:         EventSeverity.INFO,
    UnifiedEventType.UNKNOWN_EVENT:          EventSeverity.INFO,
    UnifiedEventType.STARTUP:               EventSeverity.INFO,
    # Hardware-Fehler
    UnifiedEventType.BATTERY_FAULT:         EventSeverity.CRITICAL,
    UnifiedEventType.BYPASS_FAULT:          EventSeverity.ERROR,
    UnifiedEventType.SITE_WIRING_FAULT:     EventSeverity.ERROR,
    UnifiedEventType.OUTPUT_SHORT:          EventSeverity.EMERGENCY,
    UnifiedEventType.INVERTER_FAULT:        EventSeverity.CRITICAL,
    UnifiedEventType.INVERTER_OVERCURRENT:  EventSeverity.CRITICAL,
    UnifiedEventType.INVERTER_OVERLOAD:     EventSeverity.CRITICAL,
    UnifiedEventType.FAN_FAILURE:           EventSeverity.ERROR,
    UnifiedEventType.CHARGER_FAILURE:       EventSeverity.ERROR,
    UnifiedEventType.DC_BUS_FAULT:          EventSeverity.CRITICAL,
    UnifiedEventType.EEPROM_FAULT:          EventSeverity.ERROR,
    UnifiedEventType.SYNC_ERROR:            EventSeverity.WARNING,
    UnifiedEventType.PARALLEL_FAULT:        EventSeverity.ERROR,
    UnifiedEventType.LOAD_CRITICAL:         EventSeverity.CRITICAL,
    UnifiedEventType.EPO_ACTIVE:            EventSeverity.EMERGENCY,
}


@dataclass
class UnifiedEvent:
    """Canonical Event-Darstellung (Version 3)."""
    event_type:     UnifiedEventType
    source:         str                        # "NUT", "WOEHRLE", "APC", etc.
    ups_id:         str                        # NUT-Name, z.B. "ups@localhost"
    timestamp:      datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    event_id:       str = field(
        default_factory=lambda: str(uuid.uuid4())
    )
    severity:       EventSeverity = EventSeverity.INFO
    raw_status:     Optional[str] = None       # Original NUT-Status-String
    raw_message:    Optional[str] = None       # Original Notification-Meldung
    battery_charge: Optional[float] = None    # % 0–100
    battery_runtime: Optional[float] = None   # Sekunden
    input_voltage:  Optional[float] = None    # Volt
    output_voltage: Optional[float] = None    # Volt
    ups_load:       Optional[float] = None    # % 0–100
    temperature:    Optional[float] = None    # °C
    correlation_id: Optional[str] = None      # verknüpftes State-On Event-ID
    resolved_at:    Optional[datetime] = None # gesetzt wenn State-Off eintrifft
    extra:          dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Auto-set severity aus SEVERITY_MAP."""
        self.severity = SEVERITY_MAP.get(self.event_type, EventSeverity.INFO)


# NUT notify_* → UnifiedEventType
NUT_NOTIFY_MAP: dict[str, UnifiedEventType] = {
    "ONLINE":    UnifiedEventType.POWER_RESTORED,
    "ONBATT":    UnifiedEventType.POWER_FAILURE,
    "LOWBATT":   UnifiedEventType.BATTERY_LOW,
    "FSD":       UnifiedEventType.BATTERY_CRITICAL,
    "COMMOK":    UnifiedEventType.COMMUNICATION_RESTORED,
    "COMMBAD":   UnifiedEventType.COMMUNICATION_LOST,
    "SHUTDOWN":  UnifiedEventType.SHUTDOWN_PENDING,
    "REPLBATT":  UnifiedEventType.BATTERY_REPLACE,
    "NOCOMM":    UnifiedEventType.COMMUNICATION_LOST,
    "NOPARENT":  UnifiedEventType.COMMUNICATION_LOST,
}

# ups.status Token → (Event wenn Token erscheint, Event wenn Token verschwindet)
NUT_STATUS_STATE_PAIRS: dict[str, tuple[UnifiedEventType, UnifiedEventType]] = {
    "OB":      (UnifiedEventType.POWER_FAILURE,       UnifiedEventType.POWER_RESTORED),
    "LB":      (UnifiedEventType.BATTERY_LOW,         UnifiedEventType.BATTERY_LOW_END),
    "CHRG":    (UnifiedEventType.BATTERY_CHARGING,    UnifiedEventType.BATTERY_CHARGED),
    "DISCHRG": (UnifiedEventType.BATTERY_DISCHARGING, UnifiedEventType.BATTERY_DISCHARGING_END),
    "RB":      (UnifiedEventType.BATTERY_REPLACE,     UnifiedEventType.BATTERY_REPLACED),
    "OVER":    (UnifiedEventType.OVERLOAD,            UnifiedEventType.OVERLOAD_END),
    "BYPASS":  (UnifiedEventType.BYPASS_ACTIVE,       UnifiedEventType.BYPASS_END),
    "BOOST":   (UnifiedEventType.BOOST_ACTIVE,        UnifiedEventType.BOOST_END),
    "TRIM":    (UnifiedEventType.TRIM_ACTIVE,         UnifiedEventType.TRIM_END),
    "CAL":     (UnifiedEventType.CALIBRATING,         UnifiedEventType.CALIBRATING_END),
    "OFF":     (UnifiedEventType.OUTPUT_OFF,          UnifiedEventType.OUTPUT_OFF_END),
    "FSD":     (UnifiedEventType.FORCED_SHUTDOWN,     UnifiedEventType.FORCED_SHUTDOWN),
    "TEST":    (UnifiedEventType.TEST_IN_PROGRESS,    UnifiedEventType.TEST_COMPLETE),
    "OHT":     (UnifiedEventType.TEMPERATURE_HIGH,    UnifiedEventType.TEMPERATURE_NORMAL),
    "ALARM":   (UnifiedEventType.GENERAL_ALARM,       UnifiedEventType.GENERAL_ALARM_END),
    # kein NUT-Kürzel: USV antwortet nicht (SNMP/upsc fehlgeschlagen)
    "COMMLOST": (UnifiedEventType.COMMUNICATION_LOST, UnifiedEventType.COMMUNICATION_RESTORED),
}

# Kürzel, deren Vorliegen beim ersten Abruf nach dem Start bereits gemeldet wird
PROBLEM_TOKENS = frozenset({"OB", "LB", "RB", "OVER", "BYPASS", "OFF", "FSD", "OHT", "ALARM", "COMMLOST"})


def status_tokens(data: Optional[dict[str, Any]]) -> str:
    """Übersetzt die Daten eines USV-Managers in NUT-Status-Kürzel (z. B. "OB DISCHRG RB").

    USB/NUT liefert die Kürzel direkt in data["status"]; für SNMP werden sie aus
    status_text, UPS-MIB-Batteriestatus und den APC-Zustands-/Batterie-Flags abgeleitet.
    Keine verwertbaren Daten → "COMMLOST".
    """
    from battery_assessment import apc_health_bit, apc_state_flag

    if not data or data.get("status_text") in (None, "UNKNOWN", "OFFLINE"):
        return "COMMLOST"
    if data.get("connection_type") == "usb" and isinstance(data.get("status"), str) and data["status"] != "N/A":
        return data["status"]

    tokens: list[str] = []
    text = data.get("status_text", "")
    if text.startswith("ON BATTERY"):
        tokens += ["OB", "DISCHRG"]
    elif text.startswith("BYPASS"):
        tokens += ["OL", "BYPASS"]
    elif text.startswith("ONLINE"):
        tokens.append("OL")

    flags = data.get("state_flags_apc")
    if str(data.get("battery_status_mib")) in ("3", "4") or apc_state_flag(flags, 3):
        tokens.append("LB")
    if (str(data.get("battery_replace_indicator")) == "2" or apc_state_flag(flags, 5)
            or apc_health_bit(data.get("battery_health_apc"), 2)):
        tokens.append("RB")
    for flag, token in ((7, "BOOST"), (8, "TRIM"), (9, "OVER"), (10, "CAL"), (29, "TEST"), (1, "ALARM")):
        if apc_state_flag(flags, flag):
            tokens.append(token)
    return " ".join(dict.fromkeys(tokens))



EVENT_TEXT: dict[UnifiedEventType, str] = {
    UnifiedEventType.POWER_FAILURE: "Stromausfall – USV läuft auf Batterie",
    UnifiedEventType.POWER_RESTORED: "Netzspannung wieder da",
    UnifiedEventType.BATTERY_LOW: "Batterie schwach",
    UnifiedEventType.BATTERY_LOW_END: "Batterie nicht mehr schwach",
    UnifiedEventType.BATTERY_REPLACE: "Batterie austauschen (Lebensdauer überschritten bzw. Test fehlgeschlagen)",
    UnifiedEventType.BATTERY_REPLACED: "Austauschmeldung der Batterie aufgehoben",
    UnifiedEventType.BATTERY_DISCHARGING: "Batterie wird entladen",
    UnifiedEventType.BATTERY_DISCHARGING_END: "Entladung beendet",
    UnifiedEventType.OVERLOAD: "Überlast",
    UnifiedEventType.OVERLOAD_END: "Überlast beendet",
    UnifiedEventType.BYPASS_ACTIVE: "Bypass aktiv",
    UnifiedEventType.BYPASS_END: "Bypass beendet",
    UnifiedEventType.GENERAL_ALARM: "USV meldet abnormalen Zustand",
    UnifiedEventType.GENERAL_ALARM_END: "Abnormaler Zustand beendet",
    UnifiedEventType.COMMUNICATION_LOST: "USV antwortet nicht",
    UnifiedEventType.COMMUNICATION_RESTORED: "Verbindung zur USV wiederhergestellt",
    UnifiedEventType.TEST_IN_PROGRESS: "Selbsttest läuft",
    UnifiedEventType.TEST_COMPLETE: "Selbsttest beendet",
    UnifiedEventType.CALIBRATING: "Laufzeitkalibrierung läuft",
    UnifiedEventType.CALIBRATING_END: "Laufzeitkalibrierung beendet",
}


class NUTEventMapper:
    """Wandelt rohe NUT-Daten in UnifiedEvent-Objekte um."""

    def map_notification(
        self,
        notify_type: str,
        ups_id: str,
        ups_data: dict[str, Any],
    ) -> UnifiedEvent:
        """Verarbeitet eine NUT notify_* Benachrichtigung."""
        event_type = NUT_NOTIFY_MAP.get(
            notify_type.upper(), UnifiedEventType.UNKNOWN_EVENT
        )
        return UnifiedEvent(
            event_type=event_type,
            source="NUT",
            ups_id=ups_id,
            raw_message=notify_type,
            battery_charge=self._safe_float(ups_data.get("battery.charge")),
            battery_runtime=self._safe_float(ups_data.get("battery.runtime")),
            input_voltage=self._safe_float(ups_data.get("input.voltage")),
            output_voltage=self._safe_float(ups_data.get("output.voltage")),
            ups_load=self._safe_float(ups_data.get("ups.load")),
        )

    def map_status_change(
        self,
        old_status: str,
        new_status: str,
        ups_id: str,
        ups_data: dict[str, Any],
    ) -> list[UnifiedEvent]:
        """Vergleicht alte/neue Status-Token und gibt Delta-Events zurück."""
        old_tokens = set(old_status.split()) if old_status else set()
        new_tokens = set(new_status.split()) if new_status else set()
        added   = new_tokens - old_tokens
        removed = old_tokens - new_tokens
        events: list[UnifiedEvent] = []

        for token in added:
            if token in NUT_STATUS_STATE_PAIRS:
                on_event, _ = NUT_STATUS_STATE_PAIRS[token]
                events.append(self._make_event(on_event, ups_id, ups_data, new_status))

        for token in removed:
            if token in NUT_STATUS_STATE_PAIRS:
                _, off_event = NUT_STATUS_STATE_PAIRS[token]
                events.append(self._make_event(off_event, ups_id, ups_data, new_status))

        return events

    def _make_event(
        self,
        event_type: UnifiedEventType,
        ups_id: str,
        ups_data: dict[str, Any],
        raw_status: str,
    ) -> UnifiedEvent:
        def pick(nut_key: str, snmp_key: str) -> Optional[float]:
            return self._safe_float(ups_data.get(nut_key, ups_data.get(snmp_key)))

        source = "NUT" if "battery.charge" in ups_data or ups_data.get("connection_type") == "usb" else "SNMP"
        profile = ups_data.get("profile")
        return UnifiedEvent(
            event_type=event_type,
            source=source,
            ups_id=ups_id,
            raw_status=raw_status,
            raw_message=f"{profile}: {EVENT_TEXT.get(event_type, event_type.value)}" if profile
                        else EVENT_TEXT.get(event_type, event_type.value),
            battery_charge=pick("battery.charge", "battery_charge"),
            battery_runtime=pick("battery.runtime", "runtime_minutes"),
            input_voltage=pick("input.voltage", "input_voltage_formatted"),
            output_voltage=pick("output.voltage", "output_voltage_formatted"),
            ups_load=pick("ups.load", "output_load"),
        )

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        """Sichere Float-Konvertierung."""
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None


# Explizite State-On/Off-Paare (kein generisches Suffix-Matching)
STATE_PAIRS: dict[UnifiedEventType, UnifiedEventType] = {
    UnifiedEventType.POWER_FAILURE:        UnifiedEventType.POWER_RESTORED,
    UnifiedEventType.BATTERY_LOW:          UnifiedEventType.BATTERY_LOW_END,
    UnifiedEventType.BATTERY_REPLACE:      UnifiedEventType.BATTERY_REPLACED,
    UnifiedEventType.COMMUNICATION_LOST:   UnifiedEventType.COMMUNICATION_RESTORED,
    UnifiedEventType.BATTERY_CHARGING:     UnifiedEventType.BATTERY_CHARGED,
    UnifiedEventType.BATTERY_DISCHARGING:  UnifiedEventType.BATTERY_DISCHARGING_END,
    UnifiedEventType.BATTERY_NOT_PRESENT:  UnifiedEventType.BATTERY_NOT_PRESENT_END,
    UnifiedEventType.OVERLOAD:             UnifiedEventType.OVERLOAD_END,
    UnifiedEventType.BYPASS_ACTIVE:        UnifiedEventType.BYPASS_END,
    UnifiedEventType.BOOST_ACTIVE:         UnifiedEventType.BOOST_END,
    UnifiedEventType.TRIM_ACTIVE:          UnifiedEventType.TRIM_END,
    UnifiedEventType.CALIBRATING:          UnifiedEventType.CALIBRATING_END,
    UnifiedEventType.OUTPUT_OFF:           UnifiedEventType.OUTPUT_OFF_END,
    UnifiedEventType.TEST_IN_PROGRESS:     UnifiedEventType.TEST_COMPLETE,
    UnifiedEventType.TEMPERATURE_HIGH:     UnifiedEventType.TEMPERATURE_NORMAL,
    UnifiedEventType.GENERAL_ALARM:        UnifiedEventType.GENERAL_ALARM_END,
    UnifiedEventType.INPUT_VOLTAGE_HIGH:   UnifiedEventType.INPUT_VOLTAGE_NORMAL,
    UnifiedEventType.INPUT_VOLTAGE_LOW:    UnifiedEventType.INPUT_VOLTAGE_NORMAL,
    UnifiedEventType.OUTPUT_VOLTAGE_HIGH:  UnifiedEventType.OUTPUT_VOLTAGE_NORMAL,
    UnifiedEventType.OUTPUT_VOLTAGE_LOW:   UnifiedEventType.OUTPUT_VOLTAGE_NORMAL,
    UnifiedEventType.LOAD_HIGH:            UnifiedEventType.LOAD_NORMAL,
    UnifiedEventType.RUNTIME_LOW:          UnifiedEventType.RUNTIME_NORMAL,
}

# Reverse mapping: OFF-Event → Liste aller zugehörigen ON-Events
# (mehrere ON-Events können denselben OFF-Typ haben, z.B. INPUT_VOLTAGE_HIGH + LOW → NORMAL)
_STATE_PAIRS_REVERSE: dict[UnifiedEventType, list[UnifiedEventType]] = {}
for _on, _off in STATE_PAIRS.items():
    _STATE_PAIRS_REVERSE.setdefault(_off, []).append(_on)

STALE_STATE_TIMEOUT_SECONDS = 3600  # 1 Stunde


class StateEventTracker:
    """Verknüpft State-On/Off-Events und erkennt veraltete Zustände."""

    def __init__(self) -> None:
        # ups_id → {event_type → UnifiedEvent}
        self._active_states: dict[str, dict[UnifiedEventType, UnifiedEvent]] = {}

    def process(self, event: UnifiedEvent) -> UnifiedEvent:
        """
        Verarbeitet ein Event:
        - State-On  → wird in _active_states gespeichert
        - State-Off → correlation_id + resolved_at aus dem On-Event gesetzt
        """
        ups_states = self._active_states.setdefault(event.ups_id, {})

        if event.event_type in STATE_PAIRS:
            # State-On: speichern
            ups_states[event.event_type] = event

        elif event.event_type in _STATE_PAIRS_REVERSE:
            # State-Off: alle zugehörigen On-Events suchen und das älteste/aktive nehmen
            for on_type in _STATE_PAIRS_REVERSE[event.event_type]:
                on_event = ups_states.pop(on_type, None)
                if on_event is not None:
                    event.correlation_id = on_event.event_id
                    event.resolved_at    = datetime.now(timezone.utc)
                    break  # erstes aktives State-On nimmt Vorrang

        return event

    def get_active_events(self) -> dict[str, list[dict]]:
        """Alle aktiven State-On Events für die UI (serialisierbar)."""
        result: dict[str, list[dict]] = {}
        for ups_id, states in self._active_states.items():
            result[ups_id] = [
                {
                    "event_id":       event.event_id,
                    "event_type":     event.event_type.value,
                    "severity":       event.severity.value,
                    "since":          event.timestamp.isoformat(),
                    "battery_charge":  event.battery_charge,
                    "battery_runtime": event.battery_runtime,
                    "ups_load":        event.ups_load,
                }
                for event in states.values()
            ]
        return result

    def cleanup_stale_states(self) -> list[UnifiedEvent]:
        """
        Entfernt und meldet State-On-Events die älter als STALE_STATE_TIMEOUT_SECONDS sind.
        Snapshot vor Iteration – kein RuntimeError bei dict-Mutation.
        """
        cutoff = datetime.now(timezone.utc).timestamp() - STALE_STATE_TIMEOUT_SECONDS
        stale_events: list[UnifiedEvent] = []

        for ups_id, states in list(self._active_states.items()):
            for event_type, event in list(states.items()):
                if event.timestamp.timestamp() < cutoff:
                    stale_events.append(states.pop(event_type))

        return stale_events


# Hersteller-spezifische Alarm-Mappings
# Quellen: Legacy NUT/SNMP-Alarm-Strings + Hersteller-Alarm-Tabelle (60 canonical events)
MANUFACTURER_EVENT_MAPS: dict[str, dict[str, UnifiedEventType]] = {

    "WOEHRLE": {
        # Legacy NUT/SNMP-Strings (Rückwärtskompatibilität)
        "BATT LOW":                           UnifiedEventType.BATTERY_LOW,
        "BATT FAULT":                         UnifiedEventType.BATTERY_FAULT,
        "OVERLOAD":                           UnifiedEventType.OVERLOAD,
        "REPLACE BATT":                       UnifiedEventType.BATTERY_REPLACE,
        "UPS FAULT":                          UnifiedEventType.GENERAL_ALARM,
        "INPUT FAULT":                        UnifiedEventType.INPUT_VOLTAGE_LOW,
        "OVERTEMP":                           UnifiedEventType.TEMPERATURE_HIGH,
        "BYPASS ON":                          UnifiedEventType.BYPASS_ACTIVE,
        # Hersteller-Alarm-Strings
        "Powerfail":                          UnifiedEventType.POWER_FAILURE,
        "Power restored":                     UnifiedEventType.POWER_RESTORED,
        "Battery low":                        UnifiedEventType.BATTERY_LOW,
        "UPS Battery bad":                    UnifiedEventType.BATTERY_FAULT,
        "UPS Battery Old":                    UnifiedEventType.BATTERY_REPLACE,
        "Utility Low & Battery Disconnect":   UnifiedEventType.BATTERY_NOT_PRESENT,
        "Overtemperature":                    UnifiedEventType.TEMPERATURE_HIGH,
        "UPS Overheat":                       UnifiedEventType.TEMPERATURE_HIGH,
        "Bypass on":                          UnifiedEventType.BYPASS_ACTIVE,
        "Bypass off":                         UnifiedEventType.BYPASS_END,
        "Bypass phase can't lock":            UnifiedEventType.BYPASS_FAULT,
        "Site Wiring Fault":                  UnifiedEventType.SITE_WIRING_FAULT,
        "UPS connection lost":                UnifiedEventType.COMMUNICATION_LOST,
        "UPS connection restored":            UnifiedEventType.COMMUNICATION_RESTORED,
        "Output Short Circuit":               UnifiedEventType.OUTPUT_SHORT,
        "Inverter out of order":              UnifiedEventType.INVERTER_FAULT,
        "Inverter Overcurrent":               UnifiedEventType.INVERTER_OVERCURRENT,
        "Inverter Overload":                  UnifiedEventType.INVERTER_OVERLOAD,
        "Fan out of order":                   UnifiedEventType.FAN_FAILURE,
        "Charger out of order":               UnifiedEventType.CHARGER_FAILURE,
        "DC Bus high/low abnormal":           UnifiedEventType.DC_BUS_FAULT,
        "EEPROM data error":                  UnifiedEventType.EEPROM_FAULT,
        "Sync Signal Error":                  UnifiedEventType.SYNC_ERROR,
        "Parallel comm error":                UnifiedEventType.PARALLEL_FAULT,
        "Load >80%":                          UnifiedEventType.LOAD_HIGH,
        "Load >90%":                          UnifiedEventType.LOAD_CRITICAL,
        "EPO Mode":                           UnifiedEventType.EPO_ACTIVE,
        "System shutdown":                    UnifiedEventType.SHUTDOWN_PENDING,
        "General Alarm":                      UnifiedEventType.GENERAL_ALARM,
        "General Alarm canceled":             UnifiedEventType.GENERAL_ALARM_END,
    },

    "APC": {
        # Legacy NUT/SNMP-Strings (Rückwärtskompatibilität)
        "Low battery":                        UnifiedEventType.BATTERY_LOW,
        "Replace battery":                    UnifiedEventType.BATTERY_REPLACE,
        "Overload":                           UnifiedEventType.OVERLOAD,
        "Over temperature":                   UnifiedEventType.TEMPERATURE_HIGH,
        "On battery":                         UnifiedEventType.POWER_FAILURE,
        "Online":                             UnifiedEventType.POWER_RESTORED,
        "Bypass active":                      UnifiedEventType.BYPASS_ACTIVE,
        "Communication lost":                 UnifiedEventType.COMMUNICATION_LOST,
        "Runtime calibration":                UnifiedEventType.CALIBRATING,
        # Hersteller-Alarm-Strings
        "UPS On Battery":                     UnifiedEventType.POWER_FAILURE,
        "Utility Power Failed":               UnifiedEventType.POWER_FAILURE,
        "Return from Battery":                UnifiedEventType.POWER_RESTORED,
        "Utility Restored":                   UnifiedEventType.POWER_RESTORED,
        "UPS Low Battery":                    UnifiedEventType.BATTERY_LOW,
        "Bad Battery":                        UnifiedEventType.BATTERY_FAULT,
        "Replace Battery":                    UnifiedEventType.BATTERY_FAULT,
        "Replace Battery Soon":               UnifiedEventType.BATTERY_REPLACE,
        "Battery Disconnected":               UnifiedEventType.BATTERY_NOT_PRESENT,
        "UPS Overload":                       UnifiedEventType.OVERLOAD,
        "Internal Temperature High":          UnifiedEventType.TEMPERATURE_HIGH,
        "UPS On Bypass":                      UnifiedEventType.BYPASS_ACTIVE,
        "Exit Bypass":                        UnifiedEventType.BYPASS_END,
        "Bypass not in range":                UnifiedEventType.BYPASS_FAULT,
        "Site Wiring Fault":                  UnifiedEventType.SITE_WIRING_FAULT,
        "Communication Established":          UnifiedEventType.COMMUNICATION_RESTORED,
        "Output Short Circuit":               UnifiedEventType.OUTPUT_SHORT,
        "Inverter Fault":                     UnifiedEventType.INVERTER_FAULT,
        "Inverter Current Fault":             UnifiedEventType.INVERTER_OVERCURRENT,
        "Output Overload":                    UnifiedEventType.INVERTER_OVERLOAD,
        "Fan Fault":                          UnifiedEventType.FAN_FAILURE,
        "Charger Fault":                      UnifiedEventType.CHARGER_FAILURE,
        "DC Bus Fault":                       UnifiedEventType.DC_BUS_FAULT,
        "Internal Fault":                     UnifiedEventType.EEPROM_FAULT,
        "Synchronization Error":              UnifiedEventType.SYNC_ERROR,
        "Parallel System Fault":              UnifiedEventType.PARALLEL_FAULT,
        "Load Warning":                       UnifiedEventType.LOAD_HIGH,
        "Near Overload":                      UnifiedEventType.LOAD_CRITICAL,
        "Emergency Power Off":                UnifiedEventType.EPO_ACTIVE,
        "UPS Shutdown":                       UnifiedEventType.SHUTDOWN_PENDING,
        "Alarm State":                        UnifiedEventType.GENERAL_ALARM,
        "Alarm Cleared":                      UnifiedEventType.GENERAL_ALARM_END,
    },

    "EATON": {
        # Legacy NUT/SNMP-Strings (Rückwärtskompatibilität)
        "Battery low":                        UnifiedEventType.BATTERY_LOW,
        "Battery fault":                      UnifiedEventType.BATTERY_FAULT,
        "Output overload":                    UnifiedEventType.OVERLOAD,
        "On battery":                         UnifiedEventType.POWER_FAILURE,
        "On utility":                         UnifiedEventType.POWER_RESTORED,
        "Bypass":                             UnifiedEventType.BYPASS_ACTIVE,
        "Over temperature":                   UnifiedEventType.TEMPERATURE_HIGH,
        "Replace battery":                    UnifiedEventType.BATTERY_REPLACE,
        # Hersteller-Alarm-Strings
        "UPS ON BATTERY":                     UnifiedEventType.POWER_FAILURE,
        "UPS ON NORMAL":                      UnifiedEventType.POWER_RESTORED,
        "Below Remaining Capacity Limit":     UnifiedEventType.BATTERY_LOW,
        "Battery Fault":                      UnifiedEventType.BATTERY_FAULT,
        "Battery Test Failed":                UnifiedEventType.BATTERY_REPLACE,
        "Batteries Disconnected":             UnifiedEventType.BATTERY_NOT_PRESENT,
        "Output Overload":                    UnifiedEventType.OVERLOAD,
        "UPS Overtemperature":                UnifiedEventType.TEMPERATURE_HIGH,
        "Bypass Mode":                        UnifiedEventType.BYPASS_ACTIVE,
        "Return from Bypass":                 UnifiedEventType.BYPASS_END,
        "Bypass Unable to Trace":             UnifiedEventType.BYPASS_FAULT,
        "Bypass Site Wiring Fault":           UnifiedEventType.SITE_WIRING_FAULT,
        "CAN Comm Fault":                     UnifiedEventType.COMMUNICATION_LOST,
        "Comm Restored":                      UnifiedEventType.COMMUNICATION_RESTORED,
        "Output Fault":                       UnifiedEventType.OUTPUT_SHORT,
        "Inverter Fault":                     UnifiedEventType.INVERTER_FAULT,
        "IGBT Over Current":                  UnifiedEventType.INVERTER_OVERCURRENT,
        "Parallel Overload":                  UnifiedEventType.INVERTER_OVERLOAD,
        "Fan Failure":                        UnifiedEventType.FAN_FAILURE,
        "Charger Failure":                    UnifiedEventType.CHARGER_FAILURE,
        "DC Component Over Limit":            UnifiedEventType.DC_BUS_FAULT,
        "Initialization Fault":               UnifiedEventType.EEPROM_FAULT,
        "System Not Sync to Bypass":          UnifiedEventType.SYNC_ERROR,
        "Parallel Relay Fault":               UnifiedEventType.PARALLEL_FAULT,
        "High Load Warning":                  UnifiedEventType.LOAD_HIGH,
        "Critical Load Warning":              UnifiedEventType.LOAD_CRITICAL,
        "Remote EPO":                         UnifiedEventType.EPO_ACTIVE,
        "Standby Command":                    UnifiedEventType.SHUTDOWN_PENDING,
        "Internal Failure Alarm":             UnifiedEventType.GENERAL_ALARM,
        "Condition Cleared":                  UnifiedEventType.GENERAL_ALARM_END,
    },

    "DELL": {
        # Legacy NUT/SNMP-Strings (Rückwärtskompatibilität)
        "Battery Low":                        UnifiedEventType.BATTERY_LOW,
        "On Battery":                         UnifiedEventType.POWER_FAILURE,
        "On Line":                            UnifiedEventType.POWER_RESTORED,
        "Overload Alarm":                     UnifiedEventType.OVERLOAD,
        "Over Temperature":                   UnifiedEventType.TEMPERATURE_HIGH,
        "Battery Replace":                    UnifiedEventType.BATTERY_REPLACE,
        # Hersteller-Alarm-Strings
        "UPS Low Battery":                    UnifiedEventType.BATTERY_LOW,
        "Bad Input":                          UnifiedEventType.POWER_FAILURE,
        "Input Restored":                     UnifiedEventType.POWER_RESTORED,
        "UPS Bad Battery":                    UnifiedEventType.BATTERY_FAULT,
        "Battery Warning":                    UnifiedEventType.BATTERY_REPLACE,
        "Battery Missing":                    UnifiedEventType.BATTERY_NOT_PRESENT,
        "High Load":                          UnifiedEventType.OVERLOAD,
        "Overload":                           UnifiedEventType.OVERLOAD,
        "Thermal Alarm":                      UnifiedEventType.TEMPERATURE_HIGH,
        "UPS On Bypass":                      UnifiedEventType.BYPASS_ACTIVE,
        "Bypass cleared":                     UnifiedEventType.BYPASS_END,
        "Input Sync Fault":                   UnifiedEventType.BYPASS_FAULT,
        "Input Wiring Fault":                 UnifiedEventType.SITE_WIRING_FAULT,
        "UPS Communication Lost":             UnifiedEventType.COMMUNICATION_LOST,
        "UPS Reachable":                      UnifiedEventType.COMMUNICATION_RESTORED,
        "Output Fault":                       UnifiedEventType.OUTPUT_SHORT,
        "Inverter Failure":                   UnifiedEventType.INVERTER_FAULT,
        "Inverter Current Alarm":             UnifiedEventType.INVERTER_OVERCURRENT,
        "Cooling Fault":                      UnifiedEventType.FAN_FAILURE,
        "Battery Charger Fault":              UnifiedEventType.CHARGER_FAILURE,
        "DC Voltage Fault":                   UnifiedEventType.DC_BUS_FAULT,
        "EEPROM Error":                       UnifiedEventType.EEPROM_FAULT,
        "Sync Failure":                       UnifiedEventType.SYNC_ERROR,
        "Parallel Comm Error":                UnifiedEventType.PARALLEL_FAULT,
        "Critical Load":                      UnifiedEventType.LOAD_CRITICAL,
        "UPS Shutdown":                       UnifiedEventType.EPO_ACTIVE,
        "UPS Alarm":                          UnifiedEventType.GENERAL_ALARM,
        "Alarm Reset":                        UnifiedEventType.GENERAL_ALARM_END,
    },

    "EFFEKTA": {
        # Legacy NUT/SNMP-Strings (Rückwärtskompatibilität)
        "BATT LOW":                           UnifiedEventType.BATTERY_LOW,
        "BATT FAIL":                          UnifiedEventType.BATTERY_NOT_PRESENT,
        "OVERLOAD":                           UnifiedEventType.OVERLOAD,
        "UPS FAULT":                          UnifiedEventType.GENERAL_ALARM,
        "INPUT VOLTAGE HIGH":                 UnifiedEventType.INPUT_VOLTAGE_HIGH,
        "INPUT VOLTAGE LOW":                  UnifiedEventType.INPUT_VOLTAGE_LOW,
        "TEMP HIGH":                          UnifiedEventType.TEMPERATURE_HIGH,
        # Hersteller-Alarm-Strings (Deutsch)
        "Netzfehler":                         UnifiedEventType.POWER_FAILURE,
        "Netz zurück":                        UnifiedEventType.POWER_RESTORED,
        "Batterie schwach":                   UnifiedEventType.BATTERY_LOW,
        "Batteriefehler":                     UnifiedEventType.BATTERY_FAULT,
        "Batterie ersetzen":                  UnifiedEventType.BATTERY_REPLACE,
        "Batterie getrennt":                  UnifiedEventType.BATTERY_NOT_PRESENT,
        "Überlast":                           UnifiedEventType.OVERLOAD,
        "Übertemperatur":                     UnifiedEventType.TEMPERATURE_HIGH,
        "Bypass aktiv":                       UnifiedEventType.BYPASS_ACTIVE,
        "Bypass aus":                         UnifiedEventType.BYPASS_END,
        "Bypassfehler":                       UnifiedEventType.BYPASS_FAULT,
        "Verdrahtungsfehler":                 UnifiedEventType.SITE_WIRING_FAULT,
        "Kommunikationsfehler":               UnifiedEventType.COMMUNICATION_LOST,
        "Kommunikation OK":                   UnifiedEventType.COMMUNICATION_RESTORED,
        "Kurzschluss Ausgang":                UnifiedEventType.OUTPUT_SHORT,
        "Wechselrichterfehler":               UnifiedEventType.INVERTER_FAULT,
        "Wechselrichter Überstrom":           UnifiedEventType.INVERTER_OVERCURRENT,
        "WR Überlast":                        UnifiedEventType.INVERTER_OVERLOAD,
        "Lüfterfehler":                       UnifiedEventType.FAN_FAILURE,
        "Ladegerätfehler":                    UnifiedEventType.CHARGER_FAILURE,
        "Zwischenkreisfehler":                UnifiedEventType.DC_BUS_FAULT,
        "EEPROM Fehler":                      UnifiedEventType.EEPROM_FAULT,
        "Synchronisationsfehler":             UnifiedEventType.SYNC_ERROR,
        "Parallelfehler":                     UnifiedEventType.PARALLEL_FAULT,
        "Lastwarnung":                        UnifiedEventType.LOAD_HIGH,
        "Kritische Last":                     UnifiedEventType.LOAD_CRITICAL,
        "Not-Aus aktiv":                      UnifiedEventType.EPO_ACTIVE,
        "Systemabschaltung":                  UnifiedEventType.SHUTDOWN_PENDING,
        "Sammelalarm":                        UnifiedEventType.GENERAL_ALARM,
        "Sammelalarm quittiert":              UnifiedEventType.GENERAL_ALARM_END,
    },
}


def map_manufacturer_alarm(
    manufacturer: str,
    alarm_string: str,
    ups_id: str,
    ups_data: dict[str, Any],
) -> UnifiedEvent:
    """
    Erstellt ein UnifiedEvent aus einem herstellerspezifischen Alarm-String.
    Fallback: UNKNOWN_EVENT wenn kein Mapping gefunden.
    """
    manufacturer_upper = manufacturer.upper()
    mfr_map = MANUFACTURER_EVENT_MAPS.get(manufacturer_upper, {})
    event_type = mfr_map.get(alarm_string, UnifiedEventType.UNKNOWN_EVENT)
    return UnifiedEvent(
        event_type=event_type,
        source=manufacturer_upper,
        ups_id=ups_id,
        raw_message=alarm_string,
        battery_charge=NUTEventMapper._safe_float(ups_data.get("battery.charge")),
        battery_runtime=NUTEventMapper._safe_float(ups_data.get("battery.runtime")),
        input_voltage=NUTEventMapper._safe_float(ups_data.get("input.voltage")),
        output_voltage=NUTEventMapper._safe_float(ups_data.get("output.voltage")),
        ups_load=NUTEventMapper._safe_float(ups_data.get("ups.load")),
    )
