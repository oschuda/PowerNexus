#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Übernahme der Daten aus dem früheren USV-Dashboard (ups_monitor, Flask, ups_history.db)

    python deploy/migrate_legacy_db.py --source /pfad/ups_history.db --profile <USV-Profil> [--dry-run]

Übernommen werden:
- history    → history (Profil, Standort und Name aus config.json; Laufzeit von Sekunden in Minuten,
               NUT-Status wie OL/OB in ONLINE/ON BATTERY)
- solar_data → solar_data (unverändert)
- events     → events als abgeschlossene Meldungen vom Typ UNKNOWN_EVENT, Quelle LEGACY, Originaltext in raw_message

Nicht übernommen: Benutzer (Evolved PowerNexus verwaltet sie selbst), daily_stats und battery_health (werden aus den
Messwerten neu berechnet).

Das Skript lässt sich wiederholen: Bereits übernommene Zeilen (gleiche Zeit und gleiches Profil bzw. gleicher
Wechselrichter) werden übersprungen. Vorher wird die Zieldatenbank nach data/backups/ gesichert.
Den Dienst währenddessen anhalten (systemctl stop usv-monitor), als Dienstbenutzer ausführen.
"""

import argparse
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Status → Klartext wie in snmp_managers.USBUPSManager. Ältere Versionen speicherten NUT-Kürzel ("OL CHRG",
# "OB DISCHRG"), spätere schon Klartext ("ONLINE", "ONBATTERY"); Kürzel zählen nur als ganze Wörter.
_S = "UPPER(COALESCE(ups_status, ''))"
_WORDS = f"(' ' || {_S} || ' ')"
STATUS_SQL = (f"CASE WHEN {_S} LIKE 'ON%BATT%' OR {_WORDS} LIKE '% OB %' THEN 'ON BATTERY' "
              f"WHEN {_S} LIKE 'ONLINE%' OR {_WORDS} LIKE '% OL %' THEN 'ONLINE' "
              f"WHEN {_S} LIKE 'OFF%' OR {_WORDS} LIKE '% OFF %' THEN 'OFFLINE' ELSE 'UNKNOWN' END")
SOLAR_COLUMNS = ('timestamp', 'inverter_serial', 'inverter_name', 'inverter_status', 'ac_power_w', 'dc_power_w',
                 'pv_yield_day_wh', 'pv_yield_total_wh', 'grid_feed_in_w', 'grid_consumption_w', 'inverter_temperature',
                 'string_voltage_v', 'string_current_a', 'source', 'raw_payload_json')


def columns(conn: sqlite3.Connection, schema: str, table: str) -> set:
    return {row[1] for row in conn.execute(f"PRAGMA {schema}.table_info({table})")}


def col(available: set, name: str, expression: str = None) -> str:
    """Spalte der alten Datenbank oder NULL, wenn es sie in dieser Schema-Version nicht gab."""
    return (expression or name) if name in available else "NULL"


def migrate_history(conn, profile: str, location: str) -> int:
    old = columns(conn, "old", "history")
    if not old:
        return 0
    model = col(old, "ups_model", "COALESCE(h.ups_model, '')")
    status = STATUS_SQL.replace("ups_status", "h.ups_status") if "ups_status" in old else "'UNKNOWN'"
    select = (
        f"SELECT h.timestamp, :profile, :location, {model}, {status}, "
        f"{col(old, 'battery_charge', 'h.battery_charge')}, "
        f"{col(old, 'battery_runtime', 'ROUND(h.battery_runtime / 60.0, 1)')}, "
        f"{col(old, 'battery_temperature', 'h.battery_temperature')}, {col(old, 'battery_voltage', 'h.battery_voltage')}, "
        f"{col(old, 'input_voltage', 'h.input_voltage')}, {col(old, 'output_voltage', 'h.output_voltage')}, "
        f"{col(old, 'ups_load', 'h.ups_load')}, {col(old, 'ups_power', 'h.ups_power')}, NULL, "
        f"{col(old, 'input_frequency', 'NULLIF(h.input_frequency, 0)')} "
        "FROM old.history h WHERE NOT EXISTS (SELECT 1 FROM main.history n WHERE n.profile = :profile AND n.timestamp = h.timestamp)")
    return conn.execute(
        "INSERT INTO main.history (timestamp, profile, location, ups_name, status, battery_charge, battery_runtime, "
        "battery_temperature, battery_voltage, input_voltage, output_voltage, ups_load, ups_power_w, ups_power_va, "
        f"input_frequency) {select}", {"profile": profile, "location": location}).rowcount


def migrate_solar(conn) -> int:
    old = columns(conn, "old", "solar_data")
    if not old:
        return 0
    names = ", ".join(SOLAR_COLUMNS)
    values = ", ".join(col(old, c, f"s.{c}") for c in SOLAR_COLUMNS)
    return conn.execute(
        f"INSERT INTO main.solar_data ({names}) SELECT {values} FROM old.solar_data s WHERE NOT EXISTS ("
        "SELECT 1 FROM main.solar_data n WHERE n.timestamp = s.timestamp "
        "AND COALESCE(n.inverter_serial, '') = COALESCE(s.inverter_serial, ''))").rowcount


def migrate_events(conn, profile_key: str) -> int:
    old = columns(conn, "old", "events")
    if not {"id", "timestamp", "message"} <= old:
        return 0
    kind = "e.type || ': ' || " if "type" in old else ""
    return conn.execute(
        "INSERT OR IGNORE INTO main.events (event_id, correlation_id, sequence, event_type, severity, source, ups_id, "
        "timestamp, raw_message, resolved_at) "
        f"SELECT 'legacy-' || e.id, 'legacy-' || e.id, 0, 'UNKNOWN_EVENT', 'INFO', 'LEGACY', :ups, e.timestamp, "
        f"{kind}e.message, e.timestamp FROM old.events e", {"ups": profile_key}).rowcount


def main() -> int:
    parser = argparse.ArgumentParser(description="Daten aus dem früheren USV-Dashboard übernehmen")
    parser.add_argument("--source", required=True, type=Path, help="alte Datenbank (ups_history.db)")
    parser.add_argument("--profile", required=True, help="USV-Profil (Schlüssel in config.json), dem der Verlauf gehört")
    parser.add_argument("--data", type=Path, default=ROOT / "data", help="Datenordner von Evolved PowerNexus")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--dry-run", action="store_true", help="nur zählen, nichts schreiben")
    args = parser.parse_args()

    if not args.source.is_file():
        print(f"Alte Datenbank nicht gefunden: {args.source}")
        return 2
    if not args.config.is_file():
        print(f"{args.config} fehlt. Evolved PowerNexus einmal starten und das USV-Profil anlegen.")
        return 2

    from config import ConfigManager
    from database import Database

    manager = ConfigManager(args.config.parent)
    profile = manager.get_profile(args.profile)
    if not profile:
        print(f"Profil '{args.profile}' gibt es nicht. Vorhanden: {', '.join(manager.list_profiles()) or '(keine)'}")
        return 2
    name, location = profile.get("name", args.profile), manager.get_profile_location(args.profile)

    # Schema anlegen bzw. auf den aktuellen Stand bringen
    Database(args.data, flush_seconds=0).shutdown()
    target = args.data / "usv_history.db"
    if not args.dry_run:
        backup = args.data / "backups" / f"vor-uebernahme-{time.strftime('%Y%m%d-%H%M%S')}.db"
        backup.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(target) as live, sqlite3.connect(backup) as copy:
            live.backup(copy)
        print(f"Sicherung: {backup}")

    conn = sqlite3.connect(target, timeout=30.0)
    conn.execute("ATTACH DATABASE ? AS old", (str(args.source.resolve()),))   # wird nur gelesen
    try:
        counts = {
            "history": migrate_history(conn, name, location),
            "solar_data": migrate_solar(conn),
            "events": migrate_events(conn, args.profile),
        }
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
    finally:
        conn.close()

    verb = "würden übernommen" if args.dry_run else "übernommen"
    print(f"Profil '{args.profile}' ({name}{', ' + location if location else ''}):")
    for table, count in counts.items():
        print(f"  {table}: {count} Zeilen {verb}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
