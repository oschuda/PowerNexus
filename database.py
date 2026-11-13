#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DatabaseModel - SQLite Operationen & Schema Management v20.1.4
Operational Consolidation: Connection Isolation, 256MB RAM Cache, Exponential Backoff
SQLite Performance Tuning für Synology-Umgebungen

v20.1.4 KERNVERBESSERUNGEN:
1. Connection Isolation: Jeder Worker hat EIGENE SQLite-Verbindung (no global conn sharing)
2. RAM-Cache: PRAGMA cache_size = -256000 (256MB statt 64MB) für I/O-Entlastung
3. Exponential Backoff: 3 Versuche mit mathematisch sauberen Timeouts (0ms → 50ms → 200ms)
4. Synology-Optimiert: Thread-Priority Tuning + Busy-Timeout Anpassungen
"""

import os
import sqlite3
import logging
import shutil
import threading
import queue
import time
import uuid
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class ActionState(Enum):
    """Zustandsmaschine für Aktionen"""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class EventSeverity(Enum):
    """Event-Schweregrad"""
    EMERGENCY = "EMERGENCY"
    CRITICAL = "CRITICAL"
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class EventType(Enum):
    """Kritische Event-Typen für Fast-Lane"""
    POWER_FAILURE = "POWER_FAILURE"
    BATTERY_LOW = "BATTERY_LOW"
    SHUTDOWN_INITIATED = "SHUTDOWN_INITIATED"
    RECOVERY_INITIATED = "RECOVERY_INITIATED"
    COMMUNICATION_LOSS = "COMMUNICATION_LOSS"
    UNKNOWN = "UNKNOWN"


CRITICAL_EVENT_TYPES = {EventType.POWER_FAILURE, EventType.BATTERY_LOW, 
                        EventType.SHUTDOWN_INITIATED, EventType.RECOVERY_INITIATED}

# Messwerte werden gesammelt und gemeinsam geschrieben, das schont die SD-Karte.
# Ereignisse und Protokolle gehen weiterhin sofort auf den Datenträger.
DEFAULT_FLUSH_SECONDS = 900
FLUSH_RETRY_SECONDS = 60
MAX_PENDING_ROWS = 20000
# Rohdaten bleiben 24 Monate, danach Stundenwerte. Ereignisse und Sicherheitsprotokoll werden nie gelöscht.
DEFAULT_RETENTION_MONTHS = 24
MAINTENANCE_SECONDS = 24 * 3600
MAINTENANCE_FIRST_DELAY_SECONDS = 3600
MAX_HOURS_PER_RUN = 5000
AVERAGED_COLUMNS = ('battery_charge', 'battery_runtime', 'battery_temperature', 'battery_voltage', 'input_voltage',
                    'output_voltage', 'ups_load', 'ups_power_w', 'ups_power_va', 'input_frequency')
HISTORY_COLUMNS = ('timestamp', 'profile', 'location', 'ups_name', 'status', 'battery_charge', 'battery_runtime',
                   'battery_temperature', 'battery_voltage', 'input_voltage', 'output_voltage', 'ups_load',
                   'ups_power_w', 'ups_power_va', 'input_frequency')
# PV-Wechselrichter (solar/): Leistungen und Messwerte werden beim Verdichten gemittelt, Zählerstände als Maximum übernommen
SOLAR_AVERAGED_COLUMNS = ('ac_power_w', 'dc_power_w', 'grid_feed_in_w', 'grid_consumption_w', 'inverter_temperature',
                          'string_voltage_v', 'string_current_a')
SOLAR_COUNTER_COLUMNS = ('pv_yield_day_wh', 'pv_yield_total_wh')
SOLAR_COLUMNS = ('timestamp', 'inverter_serial', 'inverter_name', 'inverter_status', 'ac_power_w', 'dc_power_w',
                 'pv_yield_day_wh', 'pv_yield_total_wh', 'grid_feed_in_w', 'grid_consumption_w', 'inverter_temperature',
                 'string_voltage_v', 'string_current_a', 'source')
BUFFERED_INSERTS = {
    'history': f"INSERT INTO history ({', '.join(HISTORY_COLUMNS)}) VALUES ({', '.join('?' * len(HISTORY_COLUMNS))})",
    'grid_quality': 'INSERT INTO grid_quality (timestamp, ups_id, voltage, frequency, thd) VALUES (?, ?, ?, ?, ?)',
    'battery_lifecycle': 'INSERT INTO battery_lifecycle (ups_id, timestamp, charge, voltage, temperature) VALUES (?, ?, ?, ?, ?)',
    'solar_data': f"INSERT INTO solar_data ({', '.join(SOLAR_COLUMNS)}) VALUES ({', '.join('?' * len(SOLAR_COLUMNS))})",
}


class DBWriterThread(threading.Thread):
    """
    v20.1.4: Dedizienter Hintergrund-Thread mit CONNECTION ISOLATION.
    
    Jeder Thread öffnet EIGENE SQLite-Verbindung (thread_local), niemals global geteilt.
    Dies verhindert "database is locked" Probleme unter Synology-Last.
    """
    
    def __init__(self, db_path: Path, max_queue_size: int = 10000, on_tick=None):
        super().__init__(daemon=True, name="DBWriterThread")
        self.db_path = db_path
        self.on_tick = on_tick
        self.write_queue: queue.Queue = queue.Queue(maxsize=max_queue_size)
        self.stop_event = threading.Event()
        self.stats = {
            'total_writes': 0,
            'failed_writes': 0,
            'queue_overflows': 0,
            'fast_lane_writes': 0,
            'last_write_time': None
        }
        self.stats_lock = threading.Lock()
        # v20.1.4: Thread-local storage für Connection Isolation
        self._conn_local = threading.local()
    
    def _get_connection(self):
        """v20.1.4: Hole isolierte Connection aus thread-local storage"""
        if not hasattr(self._conn_local, 'conn') or self._conn_local.conn is None:
            self._conn_local.conn = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False)
            self._apply_pragmas(self._conn_local.conn)
        return self._conn_local.conn
    
    def _apply_pragmas(self, conn):
        """v20.1.4: Wende optimale PRAGMA für Synology an"""
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.execute('PRAGMA cache_size=-256000')  # 256MB RAM-Cache
        conn.execute('PRAGMA temp_store=MEMORY')
        conn.execute('PRAGMA busy_timeout=5000')   # 5s timeout für DB-Locks
    
    def run(self):
        logger.info("🔄 DBWriterThread gestartet (v20.1.4: Connection Isolation, 256MB Cache)")
        while not self.stop_event.is_set():
            try:
                if self.on_tick:
                    self.on_tick()
                try:
                    operation = self.write_queue.get(timeout=1.0)
                except queue.Empty:
                    continue
                self._process(operation)
            except Exception as e:
                logger.error(f"❌ DBWriterThread kritischer Fehler: {e}")
                time.sleep(0.1)

        # Bereits angenommene Schreibaufträge nicht verwerfen
        while True:
            try:
                self._process(self.write_queue.get_nowait())
            except queue.Empty:
                break

        # Cleanup
        if hasattr(self._conn_local, 'conn') and self._conn_local.conn:
            self._conn_local.conn.close()
        logger.info("🛑 DBWriterThread beendet")

    def _process(self, operation) -> None:
        operation_type, args, kwargs = operation
        try:
            conn = self._get_connection()

            if operation_type == 'insert_event_buffered':
                self._execute_insert_event(conn, *args, **kwargs)

            conn.commit()

            with self.stats_lock:
                self.stats['total_writes'] += 1
                self.stats['last_write_time'] = datetime.now().isoformat()
        except Exception as e:
            logger.error(f"❌ DBWriterThread Fehler bei {operation_type}: {e}")
            with self.stats_lock:
                self.stats['failed_writes'] += 1

    def _execute_insert_event(self, conn, event_id: str, correlation_id: str, 
                              sequence: int, event_type: str, severity: str,
                              source: str, ups_id: str, timestamp: int, 
                              raw_message: str = None):
        conn.execute('''
            INSERT OR REPLACE INTO events 
            (event_id, correlation_id, sequence, event_type, severity, source, ups_id, 
             timestamp, raw_message) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (event_id, correlation_id, sequence, event_type, severity, 
              source, ups_id, timestamp, raw_message))
    
    def stop(self):
        self.stop_event.set()
        logger.info("🛑 DBWriterThread-Stop-Signal gesendet")
    
    def get_stats(self) -> Dict[str, Any]:
        with self.stats_lock:
            return self.stats.copy()
    
    def enqueue_write(self, operation_type: str, args: Tuple, kwargs: Dict) -> bool:
        """Nicht-blockierendes Einfügen in Write-Queue"""
        try:
            self.write_queue.put_nowait((operation_type, args, kwargs))
            return True
        except queue.Full:
            logger.warning(f"⚠️ DBWriterThread Queue voll - verwerfe {operation_type}")
            with self.stats_lock:
                self.stats['queue_overflows'] += 1
            return False


class Database:
    """Datenbank-Manager mit v20.1.4 Connection Isolation & 256MB RAM Cache"""
    
    def __init__(self, db_path: Path, flush_seconds: Optional[int] = None):
        self.db_path = db_path / "usv_history.db"
        self.backup_dir = db_path / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        # Puffer für Messwerte, siehe flush()
        self.flush_seconds = self._configured_flush_seconds() if flush_seconds is None else flush_seconds
        self._pending: Dict[str, List[Tuple]] = {table: [] for table in BUFFERED_INSERTS}
        self._pending_lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._next_flush = time.monotonic() + self.flush_seconds
        self._last_flush: Optional[int] = None
        self.retention_months = self._configured_retention_months()
        self._next_maintenance = time.monotonic() + MAINTENANCE_FIRST_DELAY_SECONDS

        # Erzwinge WAL-Mode sofort bei der ersten Verbindung
        self._enable_wal_mode()

        # Starte DB-Writer-Thread mit Connection Isolation
        self.db_writer = DBWriterThread(self.db_path, on_tick=self._periodic_work)
        self.db_writer.start()
        
        # Initialisiere Schema
        self._init_db()
        
        # Event-Sequenz-Counter
        self.event_sequence = 0
        self.event_sequence_lock = threading.Lock()
        
        logger.info(f"✅ Database Manager initialisiert (v20.1.4: 256MB Cache, Connection Isolation)")
    
    def _enable_wal_mode(self):
        """v20.1.4: Erzwinge WAL-Mode mit 256MB RAM-Cache"""
        try:
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                conn.execute('PRAGMA journal_mode=WAL')
                conn.execute('PRAGMA synchronous=NORMAL')
                conn.execute('PRAGMA cache_size=-256000')  # 256MB Cache für Synology
                conn.execute('PRAGMA temp_store=MEMORY')
                conn.execute('PRAGMA busy_timeout=5000')
                conn.commit()
                
                mode = conn.execute('PRAGMA journal_mode').fetchone()[0]
                cache = conn.execute('PRAGMA cache_size').fetchone()[0]
                logger.info(f"✅ WAL-Mode v20.1.4: journal_mode={mode}, cache={cache}KB")
        except Exception as e:
            logger.error(f"❌ Fehler beim Aktivieren von WAL-Mode: {e}")
    
    def shutdown(self):
        """Stoppt den DB-Writer-Thread und schreibt den Puffer"""
        if self.db_writer:
            self.db_writer.stop()
            self.db_writer.join(timeout=5.0)
        self.flush()

    # ========================================
    # MESSWERT-PUFFER
    # ========================================

    @staticmethod
    def _configured_flush_seconds() -> int:
        try:
            return max(0, int(os.environ.get('USV_DB_FLUSH_SECONDS', DEFAULT_FLUSH_SECONDS)))
        except ValueError:
            logger.warning(f"⚠️ USV_DB_FLUSH_SECONDS ungültig, verwende {DEFAULT_FLUSH_SECONDS}")
            return DEFAULT_FLUSH_SECONDS

    def _buffer(self, table: str, row: Tuple, urgent: bool = False) -> bool:
        with self._pending_lock:
            self._pending[table].append(row)
        if urgent or not self.flush_seconds:
            return self.flush() is not None
        return True

    def _flush_if_due(self) -> None:
        if self.flush_seconds and time.monotonic() >= self._next_flush:
            self.flush()

    def _periodic_work(self) -> None:
        self._flush_if_due()
        if time.monotonic() >= self._next_maintenance:
            self._next_maintenance = time.monotonic() + MAINTENANCE_SECONDS
            self.apply_retention()

    @staticmethod
    def _configured_retention_months() -> int:
        try:
            return max(0, int(os.environ.get('USV_DB_RETENTION_MONTHS', DEFAULT_RETENTION_MONTHS)))
        except ValueError:
            logger.warning(f"⚠️ USV_DB_RETENTION_MONTHS ungültig, verwende {DEFAULT_RETENTION_MONTHS}")
            return DEFAULT_RETENTION_MONTHS

    def apply_retention(self, now: Optional[int] = None) -> Dict[str, int]:
        """Verdichtet Messwerte, die älter als die Aufbewahrungsfrist sind, zu Stundenwerten.

        Wiederholbar: Eine Stunde mit nur noch einer Zeile bleibt, wie sie ist. Ein Lauf bearbeitet höchstens
        MAX_HOURS_PER_RUN Stunden, damit die Datenbank nicht lange gesperrt ist. grid_quality und
        battery_lifecycle liest das Programm nicht; dort werden alte Zeilen gelöscht.
        """
        result = {'hours': 0, 'removed': 0, 'deleted': 0}
        if not self.retention_months:
            return result
        cutoff = int(now if now is not None else time.time()) - int(self.retention_months * 30.44 * 86400)
        averages = ', '.join(f'AVG({column})' for column in AVERAGED_COLUMNS)
        columns = ', '.join(AVERAGED_COLUMNS)
        placeholders = ', '.join('?' * (len(AVERAGED_COLUMNS) + 5))
        try:
            with sqlite3.connect(self.db_path, timeout=30.0) as conn:
                # Nur vollständig abgelaufene Stunden, sonst würde die laufende immer wieder verdichtet
                hours = conn.execute(
                    'SELECT profile, timestamp / 3600 FROM history WHERE timestamp < ? GROUP BY 1, 2 '
                    'HAVING COUNT(*) > 1 ORDER BY 2 LIMIT ?', (cutoff - cutoff % 3600, MAX_HOURS_PER_RUN)).fetchall()
                for profile, hour in hours:
                    span = (profile, hour * 3600, (hour + 1) * 3600)
                    row = conn.execute(
                        "SELECT MAX(location), MAX(ups_name), MAX(UPPER(COALESCE(status, '')) LIKE 'ON BATTERY%'), "
                        "(SELECT status FROM history WHERE profile = ?1 AND timestamp >= ?2 AND timestamp < ?3 ORDER BY timestamp DESC LIMIT 1), "
                        f"{averages} FROM history WHERE profile = ?1 AND timestamp >= ?2 AND timestamp < ?3", span).fetchone()
                    # Batteriebetrieb bleibt erkennbar, auch wenn die Stunde am Netz endete
                    status = 'ON BATTERY' if row[2] else row[3]
                    removed = conn.execute('DELETE FROM history WHERE profile = ? AND timestamp >= ? AND timestamp < ?', span).rowcount
                    conn.execute(f'INSERT INTO history (timestamp, profile, location, ups_name, status, {columns}) VALUES ({placeholders})',
                                 (hour * 3600, profile, row[0], row[1], status, *row[4:]))
                    result['hours'] += 1
                    result['removed'] += removed - 1
                self._compact_solar(conn, cutoff, result)
                for table in ('grid_quality', 'battery_lifecycle'):
                    result['deleted'] += conn.execute(f'DELETE FROM {table} WHERE timestamp < ?', (cutoff,)).rowcount
                conn.commit()
            if result['hours'] or result['deleted']:
                logger.info(f"🧹 Aufbewahrung: {result['hours']} Stunden verdichtet, {result['removed'] + result['deleted']} Zeilen entfernt")
        except Exception as e:
            logger.error(f"❌ Aufbewahrung fehlgeschlagen: {e}")
        return result

    @staticmethod
    def _compact_solar(conn, cutoff: int, result: Dict[str, int]) -> None:
        """PV-Werte vor der Frist zu Stundenwerten je Wechselrichter verdichten (wie history)."""
        hours = conn.execute(
            "SELECT COALESCE(inverter_serial, ''), timestamp / 3600 FROM solar_data WHERE timestamp < ? GROUP BY 1, 2 "
            'HAVING COUNT(*) > 1 ORDER BY 2 LIMIT ?', (cutoff - cutoff % 3600, MAX_HOURS_PER_RUN)).fetchall()
        aggregates = ', '.join([f'AVG({c})' for c in SOLAR_AVERAGED_COLUMNS] + [f'MAX({c})' for c in SOLAR_COUNTER_COLUMNS])
        columns = ', '.join(SOLAR_AVERAGED_COLUMNS + SOLAR_COUNTER_COLUMNS)
        placeholders = ', '.join('?' * (len(SOLAR_AVERAGED_COLUMNS) + len(SOLAR_COUNTER_COLUMNS) + 5))
        where = "COALESCE(inverter_serial, '') = ?1 AND timestamp >= ?2 AND timestamp < ?3"
        for serial, hour in hours:
            span = (serial, hour * 3600, (hour + 1) * 3600)
            row = conn.execute(
                f'SELECT MAX(inverter_name), MAX(source), (SELECT inverter_status FROM solar_data WHERE {where} '
                f'ORDER BY timestamp DESC LIMIT 1), {aggregates} FROM solar_data WHERE {where}', span).fetchone()
            removed = conn.execute(f'DELETE FROM solar_data WHERE {where}', span).rowcount
            conn.execute(f'INSERT INTO solar_data (timestamp, inverter_serial, inverter_name, source, inverter_status, '
                         f'{columns}) VALUES ({placeholders})', (hour * 3600, serial or None, *row))
            result['hours'] += 1
            result['removed'] += removed - 1

    def history_span_days(self, profile: str, days: int = 180) -> float:
        """Zeitraum der Messhistorie in Tagen, ohne die Messwerte selbst zu laden"""
        cutoff = int(datetime.now().timestamp()) - days * 86400
        try:
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                source = self._history_source(conn)
                low, high = conn.execute(f'SELECT MIN(timestamp), MAX(timestamp) FROM {source} WHERE profile = ? AND timestamp >= ?',
                                         (profile, cutoff)).fetchone()
            return (high - low) / 86400 if low is not None else 0.0
        except Exception as e:
            logger.error(f"❌ Zeitraum der Historie nicht lesbar: {e}")
            return 0.0

    def flush(self) -> Optional[int]:
        """Schreibt alle gepufferten Messwerte in einer Transaktion.

        Returns: Anzahl der Zeilen, None bei einem Fehler (die Werte bleiben im Puffer).
        """
        with self._flush_lock:
            with self._pending_lock:
                batch = {table: list(rows) for table, rows in self._pending.items()}
            total = sum(len(rows) for rows in batch.values())
            try:
                if total:
                    with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                        for table, rows in batch.items():
                            if rows:
                                conn.executemany(BUFFERED_INSERTS[table], rows)
                        conn.commit()
            except Exception as e:
                logger.error(f"❌ Messwerte konnten nicht geschrieben werden, neuer Versuch folgt: {e}")
                self._next_flush = time.monotonic() + min(FLUSH_RETRY_SECONDS, self.flush_seconds or FLUSH_RETRY_SECONDS)
                with self._pending_lock:
                    for rows in self._pending.values():
                        del rows[:-MAX_PENDING_ROWS]
                return None
            with self._pending_lock:
                for table, rows in batch.items():
                    del self._pending[table][:len(rows)]
            self._next_flush = time.monotonic() + self.flush_seconds
            if total:
                self._last_flush = int(time.time())
            return total

    def buffer_status(self) -> Dict[str, Any]:
        with self._pending_lock:
            pending = sum(len(rows) for rows in self._pending.values())
        return {'pending': pending, 'flush_seconds': self.flush_seconds, 'last_flush': self._last_flush}

    def _history_source(self, conn) -> str:
        """Quelle für Lesezugriffe: gespeicherte und gepufferte Messwerte. Die TEMP-Tabelle bleibt im RAM."""
        with self._pending_lock:
            rows = list(self._pending['history'])
        if not rows:
            return 'history'
        columns = ', '.join(HISTORY_COLUMNS)
        conn.execute('PRAGMA temp_store=MEMORY')
        conn.execute(f'CREATE TEMP TABLE pending_history AS SELECT {columns} FROM history WHERE 0')
        conn.executemany(f"INSERT INTO pending_history VALUES ({', '.join('?' * len(HISTORY_COLUMNS))})", rows)
        return f'(SELECT {columns} FROM history UNION ALL SELECT {columns} FROM pending_history)'

    def solar_source(self, conn) -> str:
        """Wie _history_source, für solar_data."""
        with self._pending_lock:
            rows = list(self._pending['solar_data'])
        columns = ', '.join(SOLAR_COLUMNS)
        if not rows:
            return f'(SELECT {columns} FROM solar_data)'
        conn.execute('PRAGMA temp_store=MEMORY')
        conn.execute(f'CREATE TEMP TABLE pending_solar AS SELECT {columns} FROM solar_data WHERE 0')
        conn.executemany(f"INSERT INTO pending_solar VALUES ({', '.join('?' * len(SOLAR_COLUMNS))})", rows)
        return f'(SELECT {columns} FROM solar_data UNION ALL SELECT {columns} FROM pending_solar)'

    def insert_solar(self, sample: Dict[str, Any]) -> bool:
        """Puffert einen PV-Messwert (Schlüssel wie SOLAR_COLUMNS, fehlende sind NULL)."""
        row = tuple(sample.get(column) for column in SOLAR_COLUMNS)
        return self._buffer('solar_data', row)

    def _init_db(self):
        """Initialisiert Datenbankschema mit v20.1.4 Optimierungen"""
        try:
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                conn.execute('PRAGMA journal_mode=WAL')
                conn.execute('PRAGMA cache_size=-256000')
                
                # History-Tabelle
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp INTEGER NOT NULL,
                        profile TEXT DEFAULT '',
                        location TEXT DEFAULT '',
                        ups_name TEXT DEFAULT '',
                        status TEXT DEFAULT 'UNKNOWN',
                        battery_charge REAL DEFAULT 0,
                        battery_runtime REAL DEFAULT 0,
                        battery_temperature REAL DEFAULT 0,
                        battery_voltage REAL DEFAULT 0,
                        input_voltage REAL DEFAULT 0,
                        output_voltage REAL DEFAULT 0,
                        ups_load REAL DEFAULT 0,
                        ups_power_w REAL DEFAULT 0,
                        ups_power_va REAL DEFAULT 0
                    )
                ''')
                
                # Grid-Quality Tabelle
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS grid_quality (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp INTEGER NOT NULL,
                        ups_id TEXT NOT NULL DEFAULT '1',
                        voltage REAL DEFAULT 230.0,
                        frequency REAL DEFAULT 50.0,
                        thd REAL DEFAULT 0.0,
                        transients INTEGER DEFAULT 0,
                        voltage_sag INTEGER DEFAULT 0
                    )
                ''')
                
                # Battery-Lifecycle Tabelle
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS battery_lifecycle (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ups_id TEXT NOT NULL DEFAULT '1',
                        timestamp INTEGER NOT NULL,
                        charge REAL DEFAULT 0.0,
                        voltage REAL DEFAULT 0.0,
                        temperature REAL DEFAULT 0.0,
                        cycle_count INTEGER DEFAULT 0,
                        degradation REAL DEFAULT 0.0,
                        health_soh REAL DEFAULT 100.0
                    )
                ''')
                
                # Events-Tabelle
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS events (
                        event_id TEXT PRIMARY KEY,
                        correlation_id TEXT NOT NULL,
                        sequence INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        source TEXT NOT NULL,
                        ups_id TEXT NOT NULL,
                        timestamp INTEGER NOT NULL,
                        raw_message TEXT,
                        resolved_at INTEGER,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Action-States Tabelle
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS action_states (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        dedupe_key TEXT NOT NULL UNIQUE,
                        correlation_id TEXT NOT NULL,
                        target_host TEXT NOT NULL,
                        action_type TEXT NOT NULL,
                        state TEXT DEFAULT 'PENDING',
                        retry_count INTEGER DEFAULT 0,
                        max_retries INTEGER DEFAULT 3,
                        first_attempt INTEGER NOT NULL,
                        last_attempt INTEGER NOT NULL,
                        expiry_time INTEGER NOT NULL,
                        result_message TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Regel-Ausführungen, die bei einem Neustart noch offen sind
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS pending_runs (
                        id TEXT PRIMARY KEY,
                        policy TEXT NOT NULL,
                        ups_id TEXT NOT NULL,
                        event TEXT NOT NULL,
                        dry_run INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        started_at REAL NOT NULL,
                        execute_at REAL NOT NULL
                    )
                ''')

                # ML-Predictions Tabelle
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS ml_predictions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ups_id TEXT NOT NULL,
                        timestamp INTEGER NOT NULL,
                        rul_months REAL,
                        rul_confidence REAL,
                        anomaly_score REAL,
                        anomaly_is_detected INTEGER DEFAULT 0,
                        prediction_data TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # v21.1 Feature 2: System Audit Trail für ISO 27001 / NIS2 Compliance
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS system_audit_trail (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp INTEGER NOT NULL,
                        timestamp_utc TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        role TEXT,
                        client_ip TEXT,
                        action TEXT NOT NULL,
                        status TEXT NOT NULL,
                        target TEXT,
                        details TEXT,
                        correlation_id TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                # Phase 5.6: UPS-Gruppen (wird von UPSGroupManager benötigt)
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS ups_groups (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        name        TEXT NOT NULL UNIQUE,
                        description TEXT DEFAULT '',
                        created_at  INTEGER DEFAULT (strftime('%s', 'now'))
                    )
                ''')

                # PV-Wechselrichter (solar/), Schema wie im früheren USV-Dashboard (Übernahme: deploy/migrate_legacy_db.py)
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS solar_data (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp INTEGER NOT NULL,
                        inverter_serial TEXT,
                        inverter_name TEXT,
                        inverter_status TEXT,
                        ac_power_w REAL,
                        dc_power_w REAL,
                        pv_yield_day_wh REAL,
                        pv_yield_total_wh REAL,
                        grid_feed_in_w REAL,
                        grid_consumption_w REAL,
                        inverter_temperature REAL,
                        string_voltage_v REAL,
                        string_current_a REAL,
                        source TEXT DEFAULT 'mqtt',
                        raw_payload_json TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                # Erstelle Indizes
                self._create_indices(conn)
                self._migrate_schema(conn)
                
                conn.commit()
                logger.info("✅ Datenbank-Schema v20.1.4 initialisiert")
        except Exception as e:
            logger.error(f"❌ Datenbankfehler: {e}")
            raise
    
    def _create_indices(self, conn):
        """Erstelle Performance-Indizes"""
        indices = [
            'CREATE INDEX IF NOT EXISTS idx_history_timestamp ON history(timestamp)',
            'CREATE INDEX IF NOT EXISTS idx_history_location ON history(location)',
            'CREATE INDEX IF NOT EXISTS idx_grid_quality_ups_id ON grid_quality(ups_id)',
            'CREATE INDEX IF NOT EXISTS idx_battery_ups_id ON battery_lifecycle(ups_id)',
            'CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp DESC)',
            'CREATE INDEX IF NOT EXISTS idx_events_correlation ON events(correlation_id)',
            'CREATE INDEX IF NOT EXISTS idx_action_states_dedupe ON action_states(dedupe_key)',
            'CREATE INDEX IF NOT EXISTS idx_ml_predictions_ups_id ON ml_predictions(ups_id)',
            'CREATE INDEX IF NOT EXISTS idx_solar_data_timestamp ON solar_data(timestamp)',
            # v21.1 Feature 2: Audit-Trail Indizes für schnelle Abfragen
            'CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON system_audit_trail(timestamp)',
            'CREATE INDEX IF NOT EXISTS idx_audit_user_id ON system_audit_trail(user_id)',
            'CREATE INDEX IF NOT EXISTS idx_audit_action ON system_audit_trail(action)',
            'CREATE INDEX IF NOT EXISTS idx_audit_correlation_id ON system_audit_trail(correlation_id)',
        ]
        
        for idx_sql in indices:
            try:
                conn.execute(idx_sql)
            except Exception as e:
                if 'already exists' not in str(e):
                    logger.warning(f"⚠️ Index-Fehler: {e}")
    
    def _migrate_schema(self, conn):
        """Migriere bestehende Datenbank zum neuen Schema"""
        try:
            cursor = conn.execute("PRAGMA table_info(history)")
            columns = {row[1] for row in cursor.fetchall()}
            
            if 'location' not in columns:
                conn.execute('ALTER TABLE history ADD COLUMN location TEXT DEFAULT ""')
            if 'ups_name' not in columns:
                conn.execute('ALTER TABLE history ADD COLUMN ups_name TEXT DEFAULT ""')
            if 'input_frequency' not in columns:
                conn.execute('ALTER TABLE history ADD COLUMN input_frequency REAL')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_history_profile_ts ON history(profile, timestamp)')
            
            conn.commit()
            logger.info("✅ Schema-Migration abgeschlossen")
        except Exception as e:
            logger.warning(f"⚠️ Migration Error: {e}")
    
    def get_next_sequence(self) -> int:
        """Gibt nächste Event-Sequenznummer aus"""
        with self.event_sequence_lock:
            self.event_sequence += 1
            return self.event_sequence
    
    def generate_event_id(self) -> str:
        """Generiert eindeutige event_id (UUID)"""
        return str(uuid.uuid4())
    
    def generate_correlation_id(self) -> str:
        """Generiert correlation_id für Event-Kaskaden"""
        return str(uuid.uuid4())
    
    # ========================================
    # FAST-LANE: Kritische Events mit Exponential Backoff (v20.1.4)
    # ========================================
    
    def insert_event_fast_lane(self, event_id: str, correlation_id: str, 
                               sequence: int, event_type: str, severity: str,
                               source: str, ups_id: str, raw_message: str = None,
                               max_retries: int = 3) -> bool:
        """
        v20.1.4: KRITISCHE FAST-LANE mit mathematisch sauberer Exponential Backoff.
        
        Retry-Strategie:
        - Attempt 1: Immediate (0ms)
        - Attempt 2: 50ms backoff
        - Attempt 3: 200ms backoff (50ms * 4)
        - Fallback: Queue-basiert nach 3. Versuch
        
        Args:
            event_id: Eindeutige UUID
            correlation_id: Cascade-Tracking UUID
            sequence: Sequence-Nummer
            max_retries: Anzahl Versuche (default 3)
        
        Returns:
            True wenn erfolgreich committed, False wenn persistent fehlgeschlagen
        """
        timestamp = int(datetime.now().timestamp())
        backoff_times = [0.0, 0.05, 0.2]  # 0ms, 50ms, 200ms - mathematisch sauberes Backoff
        
        for attempt in range(max_retries):
            try:
                # v20.1.4: Isolierte Verbindung pro Versuch
                with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                    conn.execute('PRAGMA journal_mode=WAL')
                    conn.execute('PRAGMA synchronous=FULL')
                    conn.execute('PRAGMA cache_size=-256000')
                    
                    conn.execute('''
                        INSERT OR REPLACE INTO events 
                        (event_id, correlation_id, sequence, event_type, severity, source, 
                         ups_id, timestamp, raw_message) 
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (event_id, correlation_id, sequence, event_type, severity, 
                          source, ups_id, timestamp, raw_message))
                    
                    conn.commit()
                
                logger.warning(f"🚨 FAST-LANE: {event_type} persistiert (attempt {attempt+1}/{max_retries})")
                return True
            
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e):
                    if attempt < max_retries - 1:
                        backoff_sec = backoff_times[attempt + 1]
                        logger.warning(f"⚠️ DB-Lock (attempt {attempt+1}) → backoff {backoff_sec*1000:.0f}ms")
                        time.sleep(backoff_sec)
                        continue
                    else:
                        logger.error(f"❌ Fast-Lane persistenter Lock nach {max_retries} Versuchen")
                        return self.insert_event_buffered(
                            event_id, correlation_id, sequence, event_type, 
                            severity, source, ups_id, raw_message
                        )
                else:
                    logger.error(f"❌ Fast-Lane OperationalError: {e}")
                    return self.insert_event_buffered(
                        event_id, correlation_id, sequence, event_type, 
                        severity, source, ups_id, raw_message
                    )
            
            except Exception as e:
                logger.error(f"❌ Fast-Lane Fehler: {e}")
                return self.insert_event_buffered(
                    event_id, correlation_id, sequence, event_type, 
                    severity, source, ups_id, raw_message
                )
        
        return False
    
    # ========================================
    # BUFFERED WRITES: Queue-based Async
    # ========================================
    
    def insert_grid_quality(self, ups_id: str, voltage: float = None, 
                           frequency: float = None, thd: float = None) -> bool:
        """Puffert eine Netzqualitätsmessung"""
        return self._buffer('grid_quality', (int(datetime.now().timestamp()), str(ups_id) if ups_id else '1',
                                             float(voltage) if voltage is not None else 230.0,
                                             float(frequency) if frequency is not None else 50.0,
                                             float(thd) if thd is not None else 0.0))
    
    def insert_battery_lifecycle(self, ups_id: str, charge: float, 
                                voltage: float, temperature: float) -> bool:
        """Puffert Batterie-Lebenszyklusdaten"""
        return self._buffer('battery_lifecycle', (str(ups_id) if ups_id else '1', int(datetime.now().timestamp()),
                                                  float(charge) if charge is not None else 0.0,
                                                  float(voltage) if voltage is not None else 0.0,
                                                  float(temperature) if temperature is not None else 0.0))
    
    def insert_event_buffered(self, event_id: str, correlation_id: str,
                             sequence: int, event_type: str, severity: str,
                             source: str, ups_id: str, raw_message: str = None) -> bool:
        """Queue unkritisches Event (asynchron)"""
        return self.db_writer.enqueue_write('insert_event_buffered', 
                                           (event_id, correlation_id, sequence, event_type, severity, 
                                            source, ups_id, int(datetime.now().timestamp()), raw_message), {})
    
    # ========================================
    # ACTION STATE MACHINE: Idempotenz
    # ========================================
    
    def create_action_state(self, correlation_id: str, target_host: str, 
                           action_type: str, max_retries: int = 3) -> str:
        """Erstelle neuen Action-State mit Idempotenz-Schutz"""
        try:
            dedupe_key = f"{correlation_id}:{target_host}:{action_type}"
            now = int(datetime.now().timestamp())
            expiry = now + 3600
            
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                conn.execute('PRAGMA journal_mode=WAL')
                conn.execute('''
                    INSERT OR IGNORE INTO action_states
                    (dedupe_key, correlation_id, target_host, action_type, state,
                     retry_count, max_retries, first_attempt, last_attempt, expiry_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (dedupe_key, correlation_id, target_host, action_type,
                      ActionState.PENDING.value, 0, max_retries, now, now, expiry))
                conn.commit()
            
            logger.info(f"✅ Action-State erstellt: {dedupe_key}")
            return dedupe_key
        except Exception as e:
            logger.error(f"❌ Fehler beim Erstellen von Action-State: {e}")
            return None
    
    def check_action_idempotency(self, dedupe_key: str) -> bool:
        """Idempotenz-Check: Darf diese Aktion ausgeführt werden?"""
        try:
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                cursor = conn.execute(
                    'SELECT state, expiry_time FROM action_states WHERE dedupe_key = ?',
                    (dedupe_key,)
                )
                row = cursor.fetchone()
                
                if not row:
                    return True
                
                state, expiry_time = row
                
                if state in (ActionState.RUNNING.value, ActionState.SUCCEEDED.value):
                    logger.warning(f"🔴 Idempotenz-Block: {dedupe_key} ist bereits {state}")
                    return False
                
                now = int(datetime.now().timestamp())
                if expiry_time < now:
                    logger.warning(f"⚠️ Action {dedupe_key} ist EXPIRED")
                    return False
                
                return True
        except Exception as e:
            logger.error(f"❌ Idempotenz-Check Fehler: {e}")
            return False
    
    def update_action_state(self, dedupe_key: str, new_state: ActionState, 
                           result_message: str = None) -> bool:
        """Update Action-State"""
        try:
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                conn.execute('PRAGMA journal_mode=WAL')
                now = int(datetime.now().timestamp())
                
                conn.execute('''
                    UPDATE action_states
                    SET state = ?, last_attempt = ?, result_message = ?
                    WHERE dedupe_key = ?
                ''', (new_state.value, now, result_message, dedupe_key))
                conn.commit()
            
            logger.info(f"✅ Action-State aktualisiert: {dedupe_key} → {new_state.value}")
            return True
        except Exception as e:
            logger.error(f"❌ Fehler beim Update von Action-State: {e}")
            return False
    
    # ========================================
    # QUERIES
    # ========================================
    
    def insert(self, data: Dict[str, Any]) -> bool:
        """Puffert UPS-Daten für die history-Tabelle. Im Batteriebetrieb wird sofort geschrieben."""
        try:
            status = data.get('status_text', 'UNKNOWN')
            return self._buffer('history', (
                int(datetime.now().timestamp()),
                data.get('profile', ''),
                data.get('location', ''),
                data.get('ups_name', ''),
                status,
                self._to_float(data.get('battery_charge')),
                None if data.get('runtime_available') is False else self._to_float(data.get('runtime_minutes')),
                self._to_float(data.get('battery_temp_celsius')),
                self._to_float(data.get('battery_voltage_formatted')),
                self._to_float(data.get('input_voltage_formatted')),
                self._to_float(data.get('output_voltage_formatted')),
                self._to_float(data.get('output_load')),
                self._to_float(data.get('output_power_w')),
                self._to_float(data.get('output_power_va')),
                self._to_float(data['input_frequency']) if data.get('input_frequency') is not None else None
            ), urgent=str(status).upper().startswith('ON BATTERY'))
        except Exception as e:
            logger.error(f"❌ Fehler beim Dateneinsatz: {e}")
            return False
    
    HISTORY_FIELDS = ('battery_charge', 'battery_runtime', 'battery_temperature', 'battery_voltage',
                      'input_voltage', 'output_voltage', 'ups_load', 'input_frequency')

    def get_history(self, days: int = 7, profile: str = None, bucket_seconds: int = 0) -> Dict[str, List]:
        """Rufe Verlaufsdaten ab (alle Messgrößen der history-Tabelle).

        profile: Anzeigename des Profils (wie in history.profile gespeichert); leer = alle.
        bucket_seconds > 0: Mittelwerte je Zeitblock statt Rohdaten.
        """
        fields = self.HISTORY_FIELDS
        empty = {'timestamp': [], **{f: [] for f in fields}, 'count': 0}
        try:
            cutoff = int(datetime.now().timestamp()) - (days * 86400)
            where = 'WHERE timestamp >= ?'
            params: list = [cutoff]
            if profile:
                where += ' AND profile = ?'
                params.append(profile)

            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                source = self._history_source(conn)
                if bucket_seconds and bucket_seconds > 0:
                    cols = ', '.join(f'AVG({f})' for f in fields)
                    query = (f'SELECT (timestamp / {int(bucket_seconds)}) * {int(bucket_seconds)} AS ts, {cols} '
                             f'FROM {source} {where} GROUP BY ts ORDER BY ts')
                else:
                    query = f'SELECT timestamp, {", ".join(fields)} FROM {source} {where} ORDER BY timestamp'
                rows = conn.execute(query, params).fetchall()

            result = {'timestamp': [int(r[0]) for r in rows], 'count': len(rows)}
            for i, field in enumerate(fields, start=1):
                result[field] = [round(r[i], 2) if r[i] is not None else None for r in rows]
            return result
        except Exception as e:
            logger.error(f"❌ Fehler beim Abrufen der Historie: {e}")
            return empty

    def insert_event(self, event: Any) -> bool:
        """Speichert ein UnifiedEvent; bei State-Off wird das zugehörige State-On als erledigt markiert."""
        try:
            ts = int(event.timestamp.timestamp())
            resolved = int(event.resolved_at.timestamp()) if event.resolved_at else None
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                conn.execute(
                    'INSERT OR IGNORE INTO events (event_id, correlation_id, sequence, event_type, severity, '
                    'source, ups_id, timestamp, raw_message, resolved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (event.event_id, event.correlation_id or event.event_id, self.get_next_sequence(),
                     event.event_type.value, event.severity.value, event.source, event.ups_id, ts,
                     event.raw_message, resolved))
                if event.correlation_id and resolved:
                    conn.execute('UPDATE events SET resolved_at = ? WHERE event_id = ? AND resolved_at IS NULL',
                                 (resolved, event.correlation_id))
                conn.commit()
            self.flush()
            return True
        except Exception as e:
            logger.error(f"❌ Event konnte nicht gespeichert werden: {e}")
            return False

    def save_pending_run(self, row: Dict[str, Any]) -> None:
        with sqlite3.connect(self.db_path, timeout=5.0) as conn:
            conn.execute('INSERT OR REPLACE INTO pending_runs (id, policy, ups_id, event, dry_run, status, started_at, '
                         'execute_at) VALUES (:id, :policy, :ups_id, :event, :dry_run, :status, :started_at, :execute_at)', row)
            conn.commit()

    def delete_pending_run(self, run_id: str) -> None:
        with sqlite3.connect(self.db_path, timeout=5.0) as conn:
            conn.execute('DELETE FROM pending_runs WHERE id = ?', (run_id,))
            conn.commit()

    def list_pending_runs(self) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path, timeout=5.0) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute('SELECT * FROM pending_runs')]

    def resolve_open_events(self, ups_id: str) -> int:
        """Schließt alle offenen Meldungen einer USV (z. B. wenn ihr Profil deaktiviert wird)."""
        try:
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                cur = conn.execute('UPDATE events SET resolved_at = ? WHERE ups_id = ? AND resolved_at IS NULL',
                                   (int(datetime.now().timestamp()), ups_id))
                conn.commit()
                return cur.rowcount
        except Exception as e:
            logger.error(f"❌ Offene Meldungen konnten nicht geschlossen werden: {e}")
            return 0

    def get_open_event(self, ups_id: str, event_type: str) -> Optional[Dict[str, Any]]:
        """Jüngstes noch nicht erledigtes Event eines Typs (für den Neustart der Event-Erkennung)."""
        try:
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    'SELECT * FROM events WHERE ups_id = ? AND event_type = ? AND resolved_at IS NULL '
                    'AND correlation_id = event_id ORDER BY timestamp DESC LIMIT 1',
                    (ups_id, event_type)).fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ Offenes Event konnte nicht gelesen werden: {e}")
            return None

    def count_battery_events(self, profile: str, days: int = 180) -> Dict[str, Any]:
        """Zählt Wechsel in den Batteriebetrieb ('ON BATTERY') aus der gespeicherten Historie.

        Returns: {'count': Anzahl, 'last': Zeitstempel des letzten Wechsels oder None}
        """
        cutoff = int(datetime.now().timestamp()) - days * 86400
        count, last = 0, None
        on_battery = "(UPPER(COALESCE(status, '')) LIKE 'ON BATTERY%')"
        try:
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                source = self._history_source(conn)
                count, last = conn.execute(
                    f'SELECT COUNT(*), MAX(timestamp) FROM (SELECT timestamp, {on_battery} AS now, '
                    f'LAG({on_battery}) OVER (ORDER BY timestamp) AS before FROM {source} '
                    'WHERE profile = ? AND timestamp >= ?) WHERE now = 1 AND before = 0', (profile, cutoff)).fetchone()
        except Exception as e:
            logger.error(f"❌ Fehler beim Zählen der Batterie-Einsätze: {e}")
        return {'count': count, 'last': last}

    def get_grid_history(self, days: int, profiles: List[str]) -> Dict[str, Dict[str, List]]:
        """Netzverlauf (Eingangsspannung, Frequenz) je Profil-Anzeigename.

        Leere/ungültige Messungen (Spannung 0) werden ausgelassen. Bei mehr als
        7 Tagen wird auf Stunden-Mittelwerte verdichtet.
        """
        result: Dict[str, Dict[str, List]] = {}
        if not profiles:
            return result
        cutoff = int(datetime.now().timestamp()) - days * 86400
        bucket = 3600 if days > 7 else 0
        try:
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                source = self._history_source(conn)
                for profile in profiles:
                    if bucket:
                        rows = conn.execute(
                            'SELECT (timestamp / ?) * ? AS ts, AVG(input_voltage), AVG(input_frequency) '
                            f'FROM {source} WHERE profile = ? AND timestamp >= ? AND input_voltage > 0 '
                            'GROUP BY ts ORDER BY ts', (bucket, bucket, profile, cutoff)).fetchall()
                    else:
                        rows = conn.execute(
                            f'SELECT timestamp, input_voltage, input_frequency FROM {source} '
                            'WHERE profile = ? AND timestamp >= ? AND input_voltage > 0 ORDER BY timestamp',
                            (profile, cutoff)).fetchall()
                    result[profile] = {
                        'timestamp': [int(r[0]) for r in rows],
                        'voltage': [round(r[1], 1) for r in rows],
                        'frequency': [round(r[2], 2) if r[2] is not None else None for r in rows],
                    }
        except Exception as e:
            logger.error(f"❌ Fehler beim Abrufen des Netzverlaufs: {e}")
        return result

    def get_events(self, hours: int = 24, ups_id: Optional[str] = None,
                  event_type: Optional[str] = None, limit: int = 500) -> List[Dict[str, Any]]:
        """Rufe Events mit Filtering ab"""
        try:
            since = int(datetime.now().timestamp()) - hours * 3600
            query = "SELECT * FROM events WHERE timestamp >= ?"
            params: list = [since]
            
            if ups_id:
                query += " AND ups_id = ?"
                params.append(ups_id)
            if event_type:
                query += " AND event_type = ?"
                params.append(event_type)
            
            query += f" ORDER BY timestamp DESC LIMIT {int(limit)}"
            
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(query, params)
                rows = cursor.fetchall()
            
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Fehler beim Abrufen von Events: {e}")
            return []
    
    def get_event_cascade(self, correlation_id: str) -> List[Dict[str, Any]]:
        """Rufe alle Events einer Cascade ab"""
        try:
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute('''
                    SELECT * FROM events WHERE correlation_id = ? ORDER BY sequence ASC
                ''', (correlation_id,))
                rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Fehler beim Abrufen von Event-Cascade: {e}")
            return []
    
    def _to_float(self, value: Any, default: float = 0.0) -> float:
        """Konvertiere sicher zu Float"""
        if value is None or value == "N/A":
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default
    
    # ========================================
    # ML PREDICTIONS
    # ========================================
    
    def insert_ml_prediction(self, ups_id: str, rul_months: float, rul_confidence: float,
                            anomaly_score: float, anomaly_is_detected: bool,
                            prediction_data: Optional[str] = None) -> bool:
        """Speichere ML Predictions"""
        if not ups_id:
            ups_id = '1'
        
        timestamp = int(datetime.now().timestamp())
        
        try:
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                conn.execute('PRAGMA journal_mode=WAL')
                conn.execute('''
                    INSERT INTO ml_predictions
                    (ups_id, rul_months, rul_confidence, anomaly_score, 
                     anomaly_is_detected, prediction_data, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (ups_id, rul_months, rul_confidence, anomaly_score,
                      anomaly_is_detected, prediction_data, timestamp))
                conn.commit()
            
            logger.info(f"✅ ML Prediction für UPS {ups_id} gespeichert")
            return True
        
        except Exception as e:
            logger.error(f"❌ Fehler beim Speichern ML-Prediction: {e}")
            return False
    
    def get_ml_prediction(self, ups_id: str) -> Optional[dict]:
        """Hole letzte ML Prediction für eine USV"""
        if not ups_id:
            ups_id = '1'
        
        try:
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                
                cursor = conn.execute('''
                    SELECT * FROM ml_predictions
                    WHERE ups_id = ?
                    ORDER BY timestamp DESC
                    LIMIT 1
                ''', (ups_id,))
                
                row = cursor.fetchone()
                if row:
                    return dict(row)
                
                return None
        
        except Exception as e:
            logger.error(f"❌ Fehler beim Abrufen ML-Prediction: {e}")
            return None
    
    def backup_database(self) -> Optional[Path]:
        """Erstelle Datenbank-Backup"""
        try:
            if not self.db_path.exists():
                logger.warning("⚠️ Datenbank existiert nicht, überspringe Backup")
                return None
            
            self.flush()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = self.backup_dir / f"usv_history_{timestamp}.db"

            shutil.copy2(self.db_path, backup_path)
            logger.info(f"✅ Datenbank-Backup erstellt: {backup_path}")
            
            return backup_path
        except Exception as e:
            logger.error(f"❌ Fehler beim Backup: {e}")
            return None
    
    # ========================================
    # v21.1 FEATURE 2: AUDIT TRAIL (ISO 27001 / NIS2 Compliance)
    # ========================================
    
    def insert_audit_trail(self, user_id: str, action: str, status: str,
                          role: Optional[str] = None, client_ip: Optional[str] = None,
                          target: Optional[str] = None, details: Optional[str] = None,
                          correlation_id: Optional[str] = None) -> Optional[int]:
        """
        Schreibe erweiterten Audit-Trail-Eintrag in system_audit_trail Tabelle
        
        v21.1 Feature 2 für ISO 27001 / NIS2 Compliance:
        - Schreibt synchron (blocking) → wird via run_in_executor() in log_action() aufgerufen
        - Erfasst alle kritischen Metadaten: user_id, role, client_ip, action, status, details
        - Correlation_id für Event-Kaskaden-Tracking
        - Timestamps sowohl als Unix-Timestamp als auch UTC-ISO-String
        
        Args:
            user_id: Benutzer-ID (aus Session)
            action: Action-String (LOGIN, LOGOUT, KEY_ROTATION, CONFIG_CHANGE, etc.)
            status: SUCCESS oder FAILURE
            role: User-Rolle (aus Session)
            client_ip: Client IP-Adresse (aus Request)
            target: Target (z.B. "authentication", "crypto_manager")
            details: JSON-String mit zusätzlichen Details
            correlation_id: Correlation ID für Event-Tracking
        
        Returns:
            audit_id (int) oder None falls Fehler
        """
        try:
            timestamp = int(datetime.now().timestamp())
            timestamp_utc = datetime.utcnow().isoformat()
            
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                conn.execute('PRAGMA journal_mode=WAL')
                
                cursor = conn.execute('''
                    INSERT INTO system_audit_trail
                    (timestamp, timestamp_utc, user_id, role, client_ip, action, status, 
                     target, details, correlation_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (timestamp, timestamp_utc, user_id, role, client_ip, action, 
                      status, target, details, correlation_id))
                
                conn.commit()
                audit_id = cursor.lastrowid
            
            logger.info(
                f"📝 Audit: id={audit_id} | user={user_id} | action={action} | "
                f"status={status} | ip={client_ip}"
            )
            return audit_id
        
        except Exception as e:
            logger.error(f"❌ Fehler beim Schreiben des Audit-Trail: {e}")
            return None
    
    def get_audit_trail(self, hours: int = 24, user_id: Optional[str] = None,
                       action: Optional[str] = None, status: Optional[str] = None,
                       limit: int = 500) -> List[Dict[str, Any]]:
        """
        Rufe Audit-Trail-Einträge mit Filtering ab
        
        v21.1 Feature 2 für IT-Forensik:
        - Ruft Audit-Logs aus den letzten N Stunden ab
        - Filtert optional nach user_id, action, status
        - Returniert als Liste von Dictionaries (leicht zu serialisieren)
        
        Args:
            hours: Nur Logs der letzten N Stunden (default 24)
            user_id: Filter nach spezifischem User
            action: Filter nach spezifischer Action
            status: Filter nach Status (SUCCESS/FAILURE)
            limit: Max. Anzahl Ergebnisse (default 500)
        
        Returns:
            List[Dict] oder leere Liste bei Fehler
        """
        try:
            since = int(datetime.now().timestamp()) - hours * 3600
            query = "SELECT * FROM system_audit_trail WHERE timestamp >= ?"
            params: list = [since]
            
            if user_id:
                query += " AND user_id = ?"
                params.append(user_id)
            if action:
                query += " AND action = ?"
                params.append(action)
            if status:
                query += " AND status = ?"
                params.append(status)
            
            query += f" ORDER BY timestamp DESC LIMIT {int(limit)}"
            
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(query, params)
                rows = cursor.fetchall()
            
            return [dict(row) for row in rows]
        
        except Exception as e:
            logger.error(f"❌ Fehler beim Abrufen von Audit-Trail: {e}")
            return []
    
    def get_audit_trail_by_correlation_id(self, correlation_id: str) -> List[Dict[str, Any]]:
        """
        Rufe alle Audit-Einträge einer Cascade (via correlation_id) ab
        
        Nützlich für Forensik: Alle Aktionen einer Incident-Kaskade
        
        Args:
            correlation_id: Correlation-ID zum Tracking
        
        Returns:
            List[Dict] oder leere Liste
        """
        try:
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    'SELECT * FROM system_audit_trail WHERE correlation_id = ? ORDER BY timestamp ASC',
                    (correlation_id,)
                )
                rows = cursor.fetchall()
            
            return [dict(row) for row in rows]
        
        except Exception as e:
            logger.error(f"❌ Fehler beim Abrufen von Audit-Cascade: {e}")
            return []
