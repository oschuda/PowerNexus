#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Abfragen auf solar_data: letzter Wert, Verlauf, Tag, Monat, abgeleitete Ereignisse, Vergleich PV ↔ USV

Gelesen wird über Database.solar_source(), damit auch gepufferte, noch nicht geschriebene Werte erscheinen.
Übernommen aus dem früheren USV-Dashboard (ups_monitor/database/solar_repository.py).
"""

import logging
import sqlite3
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PV_ACTIVE_W = 50
HIGH_TEMPERATURE_C = 65
FEED_IN_PEAK_W = 1000
SERIES_COLUMNS = ('timestamp', 'inverter_status', 'ac_power_w', 'dc_power_w', 'pv_yield_day_wh', 'pv_yield_total_wh',
                  'grid_feed_in_w', 'grid_consumption_w', 'inverter_temperature', 'string_voltage_v', 'string_current_a')


def _connect(db) -> sqlite3.Connection:
    conn = sqlite3.connect(db.db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def bucket_for(days: float) -> int:
    """< 2 Tage Rohdaten, bis 14 Tage 10-Minuten-, darüber Stundenmittel"""
    return 0 if days < 2 else (600 if days <= 14 else 3600)


def latest(db) -> Optional[Dict[str, Any]]:
    try:
        with _connect(db) as conn:
            row = conn.execute(f'SELECT * FROM {db.solar_source(conn)} ORDER BY timestamp DESC LIMIT 1').fetchone()
    except Exception as e:
        logger.error(f"❌ PV: letzter Wert nicht lesbar: {e}")
        return None
    return with_derived(dict(row)) if row else None


def with_derived(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    feed_in = float(snapshot.get('grid_feed_in_w') or 0)
    consumption = float(snapshot.get('grid_consumption_w') or 0)
    snapshot['pv_active'] = float(snapshot.get('ac_power_w') or 0) > PV_ACTIVE_W
    snapshot['grid_direction'] = 'EXPORT' if feed_in > 0 else ('IMPORT' if consumption > 0 else 'BALANCED')
    return snapshot


def series(db, days: float) -> Dict[str, Any]:
    bucket = bucket_for(days)
    start = int(time.time() - days * 86400)
    try:
        with _connect(db) as conn:
            source = db.solar_source(conn)
            if bucket:
                averaged = ', '.join(f'ROUND(AVG({c}), 2)' if not c.startswith('pv_yield') else f'ROUND(MAX({c}), 2)'
                                     for c in SERIES_COLUMNS[2:])
                rows = conn.execute(
                    f'SELECT (timestamp / {bucket}) * {bucket} AS ts, MAX(inverter_status), {averaged} FROM {source} '
                    'WHERE timestamp >= ? GROUP BY ts ORDER BY ts', (start,)).fetchall()
            else:
                rows = conn.execute(f"SELECT {', '.join(SERIES_COLUMNS)} FROM {source} WHERE timestamp >= ? ORDER BY timestamp",
                                    (start,)).fetchall()
    except Exception as e:
        logger.error(f"❌ PV: Verlauf nicht lesbar: {e}")
        rows = []
    result: Dict[str, Any] = {column: [row[i] for row in rows] for i, column in enumerate(SERIES_COLUMNS)}
    result.update(count=len(rows), bucket_seconds=bucket)
    return result


def _summary(conn, source: str, start: int, end: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        f'''SELECT COUNT(*) AS samples, ROUND(AVG(ac_power_w), 1) AS average_ac_power_w,
                   ROUND(MAX(ac_power_w), 1) AS peak_ac_power_w, ROUND(AVG(grid_feed_in_w), 1) AS average_feed_in_w,
                   ROUND(AVG(grid_consumption_w), 1) AS average_consumption_w,
                   ROUND(AVG(inverter_temperature), 1) AS average_temperature,
                   MIN(pv_yield_total_wh) AS total_start, MAX(pv_yield_total_wh) AS total_end,
                   SUM(CASE WHEN COALESCE(ac_power_w, 0) > {PV_ACTIVE_W} THEN 1 ELSE 0 END) AS active_samples,
                   SUM(CASE WHEN inverter_status = 'ONLINE' THEN 1 ELSE 0 END) AS online_samples,
                   MIN(timestamp) AS first_timestamp, MAX(timestamp) AS last_timestamp
            FROM {source} WHERE timestamp >= ? AND timestamp < ?''', (start, end)).fetchone()


def _day_yields(conn, source: str, start: int, end: int) -> float:
    """Summe der Tageszähler (je Kalendertag das Maximum), falls der Gesamtzähler fehlt"""
    row = conn.execute(
        f"SELECT SUM(m) FROM (SELECT MAX(pv_yield_day_wh) AS m FROM {source} WHERE timestamp >= ? AND timestamp < ? "
        "GROUP BY date(timestamp, 'unixepoch', 'localtime'))", (start, end)).fetchone()
    return float(row[0] or 0)


def _summarize(db, start_dt: datetime, end_dt: datetime) -> Dict[str, Any]:
    start, end = int(start_dt.timestamp()), int(end_dt.timestamp())
    try:
        with _connect(db) as conn:
            source = db.solar_source(conn)
            row = _summary(conn, source, start, end)
            day_yields = _day_yields(conn, source, start, end)
    except Exception as e:
        logger.error(f"❌ PV: Zusammenfassung nicht lesbar: {e}")
        return {'samples': 0, 'yield_wh': 0.0}
    samples = int(row['samples'] or 0)
    if not samples:
        return {'samples': 0, 'yield_wh': 0.0}
    counter = float(row['total_end'] or 0) - float(row['total_start'] or 0)
    result = {key: row[key] for key in ('average_ac_power_w', 'peak_ac_power_w', 'average_feed_in_w',
                                         'average_consumption_w', 'average_temperature', 'first_timestamp', 'last_timestamp')}
    result.update(samples=samples, yield_wh=round(counter if counter > 0 else day_yields, 1),
                  pv_active_ratio=round(int(row['active_samples'] or 0) / samples, 3),
                  availability_ratio=round(int(row['online_samples'] or 0) / samples, 3))
    return result


def day_summary(db, date_value: Optional[str] = None) -> Dict[str, Any]:
    """date_value: JJJJ-MM-TT, leer = heute. ValueError bei ungültigem Datum."""
    start = datetime.strptime(date_value, '%Y-%m-%d') if date_value else datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0)
    end = datetime.fromordinal(start.toordinal() + 1)
    return {'date': start.strftime('%Y-%m-%d'), **_summarize(db, start, end)}


def month_summary(db, month_value: Optional[str] = None) -> Dict[str, Any]:
    """month_value: JJJJ-MM, leer = aktueller Monat. ValueError bei ungültigem Monat."""
    start = datetime.strptime(month_value, '%Y-%m') if month_value else datetime.now().replace(
        day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return {'month': start.strftime('%Y-%m'), **_summarize(db, start, end)}


def events(db, days: int = 30, limit: int = 50) -> List[Dict[str, Any]]:
    """Ereignisse aus den Messwerten: Statuswechsel, hohe Temperatur, starke Einspeisung, Leistungseinbruch.

    type ist zugleich der Textschlüssel (solar.event.<type>) der Oberfläche, params dessen Platzhalter.
    """
    start = int(time.time()) - days * 86400
    try:
        with _connect(db) as conn:
            rows = conn.execute(
                f'SELECT timestamp, inverter_status, ac_power_w, grid_feed_in_w, inverter_temperature '
                f'FROM {db.solar_source(conn)} WHERE timestamp >= ? ORDER BY timestamp', (start,)).fetchall()
    except Exception as e:
        logger.error(f"❌ PV: Ereignisse nicht lesbar: {e}")
        return []
    result: List[Dict[str, Any]] = []
    previous_status, previous_power = None, None
    hot = peak = False
    for row in rows:
        status = row['inverter_status'] or 'UNKNOWN'
        power = float(row['ac_power_w'] or 0)
        feed_in = float(row['grid_feed_in_w'] or 0)
        temperature = row['inverter_temperature']
        ts = row['timestamp']
        if previous_status is not None and status != previous_status:
            result.append({'timestamp': ts, 'type': 'status', 'severity': 'warning' if status in ('FAULT', 'OFFLINE', 'WARNING') else 'info',
                           'params': {'old': previous_status, 'new': status}})
        # Temperatur und Einspeisung nur beim Überschreiten melden, nicht bei jedem Messwert darüber
        now_hot = temperature is not None and float(temperature) >= HIGH_TEMPERATURE_C
        if now_hot and not hot:
            result.append({'timestamp': ts, 'type': 'temperature', 'severity': 'warning', 'params': {'value': round(float(temperature), 1)}})
        now_peak = feed_in >= FEED_IN_PEAK_W
        if now_peak and not peak:
            result.append({'timestamp': ts, 'type': 'feed_in', 'severity': 'info', 'params': {'value': round(feed_in)}})
        if previous_power is not None and previous_power > 500 and power < PV_ACTIVE_W:
            result.append({'timestamp': ts, 'type': 'drop', 'severity': 'info',
                           'params': {'old': round(previous_power), 'new': round(power)}})
        previous_status, previous_power, hot, peak = status, power, now_hot, now_peak
    return list(reversed(result))[:limit]


def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    if len(xs) < 3:
        return None
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denominator = (sum((x - mean_x) ** 2 for x in xs) * sum((y - mean_y) ** 2 for y in ys)) ** 0.5
    return round(numerator / denominator, 3) if denominator else None


def correlation(db, days: int, profile: str) -> Dict[str, Any]:
    """PV-Leistung und Einspeisung neben Netzspannung, Last und Batteriebetrieb einer USV.

    profile: Anzeigename wie in history.profile gespeichert.
    """
    bucket = 600 if days <= 2 else 3600
    start = int(time.time()) - days * 86400
    try:
        with _connect(db) as conn:
            ups = conn.execute(
                f"SELECT (timestamp / {bucket}) * {bucket} AS ts, AVG(NULLIF(input_voltage, 0)), AVG(ups_load), "
                f"MAX(UPPER(COALESCE(status, '')) LIKE 'ON BATTERY%') FROM {db._history_source(conn)} "
                'WHERE profile = ? AND timestamp >= ? GROUP BY ts', (profile, start)).fetchall()
            pv = conn.execute(
                f'SELECT (timestamp / {bucket}) * {bucket} AS ts, AVG(ac_power_w), AVG(grid_feed_in_w), AVG(grid_consumption_w) '
                f'FROM {db.solar_source(conn)} WHERE timestamp >= ? GROUP BY ts', (start,)).fetchall()
    except Exception as e:
        logger.error(f"❌ PV: Vergleich mit USV nicht lesbar: {e}")
        ups, pv = [], []
    ups_by_ts = {int(r[0]): r for r in ups}
    pv_by_ts = {int(r[0]): r for r in pv}
    timestamps = sorted(set(ups_by_ts) | set(pv_by_ts))

    def column(rows, index):
        return [round(rows[ts][index], 2) if ts in rows and rows[ts][index] is not None else None for ts in timestamps]

    result: Dict[str, Any] = {
        'timestamp': timestamps, 'bucket_seconds': bucket, 'profile': profile,
        'input_voltage': column(ups_by_ts, 1), 'ups_load': column(ups_by_ts, 2),
        'on_battery': [bool(ups_by_ts[ts][3]) if ts in ups_by_ts else False for ts in timestamps],
        'ac_power_w': column(pv_by_ts, 1), 'grid_feed_in_w': column(pv_by_ts, 2), 'grid_consumption_w': column(pv_by_ts, 3),
    }
    pairs = [(v, p) for v, p in zip(result['input_voltage'], result['ac_power_w']) if v is not None and p is not None]
    pv_values = [p for p in result['ac_power_w'] if p is not None]
    result['summary'] = {
        'pairs': len(pairs),
        'voltage_pv_correlation': _pearson([v for v, _ in pairs], [p for _, p in pairs]),
        'pv_active_ratio': round(sum(1 for p in pv_values if p > PV_ACTIVE_W) / len(pv_values), 3) if pv_values else 0.0,
        'feed_in_peak_w': max((f for f in result['grid_feed_in_w'] if f is not None), default=0.0),
        'on_battery_with_pv': sum(1 for b, p in zip(result['on_battery'], result['ac_power_w']) if b and p and p > PV_ACTIVE_W),
    }
    return result
