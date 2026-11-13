#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Framework-neutraler Laufzeit-Zustand für Automation-Pause und Test-Simulation.

Wird von der Web-Schicht (Flask und Litestar) und vom ActionExecutor gemeinsam
genutzt. Bewusst ohne Web-Framework-Abhängigkeit, damit der Pause-Schalter auch
greift, wenn Flask nicht installiert ist.
"""

import threading
from datetime import datetime

# ============================================================
# AUTOMATION PAUSE STATE (Thread-Safe)
# ============================================================
# Wenn True: Policies werden erkannt aber NICHT ausgeführt
_automation_paused = False
_automation_lock = threading.Lock()


def get_automation_paused() -> bool:
    """Lese automation_paused Status thread-safe"""
    with _automation_lock:
        return _automation_paused


def set_automation_paused(value: bool) -> bool:
    """Setze automation_paused Status thread-safe"""
    global _automation_paused
    with _automation_lock:
        _automation_paused = value
        return _automation_paused


# ============================================================
# SIMULATION STATE (für Tests)
# ============================================================
_simulation_mode_active = False
_simulation_lock = threading.Lock()
_simulation_events = []


def get_simulation_mode() -> bool:
    """Lese simulation mode Status thread-safe"""
    with _simulation_lock:
        return _simulation_mode_active


def set_simulation_mode(value: bool) -> bool:
    """Setze simulation mode Status thread-safe"""
    global _simulation_mode_active
    with _simulation_lock:
        _simulation_mode_active = value
        if not value:
            _simulation_events.clear()
        return _simulation_mode_active


def add_simulation_event(event_dict: dict) -> None:
    """Füge ein Event zum Simulations-Log hinzu"""
    with _simulation_lock:
        event_dict['logged_at'] = datetime.now().isoformat()
        _simulation_events.append(event_dict)


def get_simulation_events() -> list:
    """Hole alle simulierten Events"""
    with _simulation_lock:
        return _simulation_events.copy()
