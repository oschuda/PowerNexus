#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Home-Assistant-MQTT-Discovery für PV-Wechselrichter

Ein Gerät meldet jeden Sensor unter <Präfix>/<Komponente>/[<Knoten>/]<Objekt>/config mit einer JSON-Beschreibung
(state_topic, unit_of_measurement, device_class, value_template, device). Hier wird diese Beschreibung gelesen,
ein Messwert daraus berechnet und der Sensor einem Feld der Tabelle solar_data zugeordnet.

Die Beschreibungen kommen vom Broker, also von außen: value_template läuft in der Jinja-Sandbox, Länge und
Anzahl sind begrenzt.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from jinja2.sandbox import ImmutableSandboxedEnvironment

logger = logging.getLogger(__name__)

# Felder, die ein Sensor füllen kann (Spalten in solar_data)
FIELDS = ('ac_power_w', 'dc_power_w', 'grid_feed_in_w', 'grid_consumption_w', 'pv_yield_day_wh', 'pv_yield_total_wh',
          'inverter_temperature', 'string_voltage_v', 'string_current_a', 'inverter_status')
NUMERIC_FIELDS = FIELDS[:-1]

MAX_TEMPLATE_LENGTH = 500
MAX_PAYLOAD_BYTES = 16384

# Home Assistant erlaubt Abkürzungen der Schlüssel; nur die hier benötigten
_ABBREVIATIONS = {
    'stat_t': 'state_topic', 'unit_of_meas': 'unit_of_measurement', 'dev_cla': 'device_class',
    'val_tpl': 'value_template', 'uniq_id': 'unique_id', 'dev': 'device', 'stat_cla': 'state_class',
    'json_attr_t': 'json_attributes_topic', 'avty_t': 'availability_topic', 'avty': 'availability',
    'pl_not_avail': 'payload_not_available', 'obj_id': 'object_id',
}
_DEVICE_ABBREVIATIONS = {'ids': 'identifiers', 'mf': 'manufacturer', 'mdl': 'model', 'sw': 'sw_version', 'sn': 'serial_number'}

_POWER_UNITS = {'w': 1.0, 'kw': 1000.0, 'mw': 1_000_000.0}
_ENERGY_UNITS = {'wh': 1.0, 'kwh': 1000.0, 'mwh': 1_000_000.0}

_FEED_IN = ('feed', 'einspeis', 'supplied', 'export', 'lieferung')
_CONSUMPTION = ('consum', 'bezug', 'absorbed', 'import', 'purchase', 'verbrauch')
_DC = ('pv_power', 'pv power', 'dc', 'string', 'mpp', 'pv-leistung')
_DAY = ('day', 'daily', 'today', 'tag', 'heute')
_TOTAL = ('total', 'gesamt', 'lifetime')
_PV_SIDE = ('pv', 'dc', 'string', 'mpp')
_STATUS = ('status', 'condition', 'zustand', 'state', 'betrieb')
_IDENTIFIER = re.compile(r'(?:^|[\s_-])id(?:$|[\s_-])')    # z. B. operating_status_id: Zahlencode neben dem Klartext
_MAX_AVAILABILITY_TOPICS = 4

_environment = ImmutableSandboxedEnvironment(autoescape=False)


def _expand(config: Dict[str, Any], table: Dict[str, str]) -> Dict[str, Any]:
    return {table.get(key, key): value for key, value in config.items()}


def parse_config(topic: str, payload: bytes, prefix: str) -> Optional[Dict[str, Any]]:
    """Discovery-Nachricht → Sensorbeschreibung, None wenn unbrauchbar oder gelöscht (leere Nachricht)."""
    if not payload or len(payload) > MAX_PAYLOAD_BYTES:
        return None
    try:
        raw = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    config = _expand(raw, _ABBREVIATIONS)
    base = config.get('~')
    state_topic = config.get('state_topic')
    if isinstance(base, str) and isinstance(state_topic, str):
        state_topic = state_topic.replace('~', base) if state_topic.startswith('~') or state_topic.endswith('~') else state_topic
    if not isinstance(state_topic, str) or not state_topic or '#' in state_topic or '+' in state_topic:
        return None
    device = config.get('device') if isinstance(config.get('device'), dict) else {}
    device = _expand(device, _DEVICE_ABBREVIATIONS)
    identifiers = device.get('identifiers')
    if isinstance(identifiers, list):
        identifiers = identifiers[0] if identifiers else ''
    template = config.get('value_template')
    parts = topic[len(prefix) + 1:].split('/') if topic.startswith(prefix + '/') else topic.split('/')
    return {
        'config_topic': topic,
        'component': parts[0] if parts else '',
        'object_id': str(config.get('object_id') or (parts[-2] if len(parts) > 1 else ''))[:100],
        'state_topic': state_topic,
        'availability_topics': _availability_topics(config, base),
        'payload_not_available': str(config.get('payload_not_available') or 'offline')[:32],
        'name': str(config.get('name') or config.get('object_id') or (parts[-2] if len(parts) > 1 else topic))[:100],
        'unique_id': str(config.get('unique_id') or '')[:100],
        'unit': str(config.get('unit_of_measurement') or '')[:16],
        'device_class': str(config.get('device_class') or '')[:32],
        'value_template': template[:MAX_TEMPLATE_LENGTH] if isinstance(template, str) else '',
        'device_name': str(device.get('name') or '')[:100],
        'device_id': str(identifiers or '')[:100],
        'serial_number': str(device.get('serial_number') or '')[:100],
        'manufacturer': str(device.get('manufacturer') or '')[:100],
        'model': str(device.get('model') or '')[:100],
    }


def _availability_topics(config: Dict[str, Any], base: Any) -> List[str]:
    """availability_topic oder availability: [{topic: …}]; meldet ein Topic "offline", gilt der Sensor als nicht verfügbar."""
    entries = [config.get('availability_topic')]
    if isinstance(config.get('availability'), list):
        entries += [item.get('topic') or item.get('t') for item in config['availability'] if isinstance(item, dict)]
    topics = []
    for topic in entries:
        if not isinstance(topic, str) or not topic or '#' in topic or '+' in topic:
            continue
        if isinstance(base, str) and (topic.startswith('~') or topic.endswith('~')):
            topic = topic.replace('~', base)
        if topic not in topics:
            topics.append(topic[:200])
    return topics[:_MAX_AVAILABILITY_TOPICS]


def render_value(sensor: Dict[str, Any], payload: bytes) -> Optional[str]:
    """Zustandsnachricht → Wert als Text (über value_template, sonst die Nachricht selbst)."""
    if len(payload) > MAX_PAYLOAD_BYTES:
        return None
    try:
        text = payload.decode('utf-8').strip()
    except UnicodeDecodeError:
        return None
    template = sensor.get('value_template')
    if not template:
        return text
    try:
        value_json = json.loads(text)
    except ValueError:
        value_json = None
    try:
        return _environment.from_string(template).render(value=text, value_json=value_json).strip()
    except Exception as e:
        logger.debug(f"PV: value_template von {sensor.get('name')} nicht auswertbar: {e}")
        return None


def _words(sensor: Dict[str, Any]) -> str:
    # Nicht das state_topic: Teilen sich alle Sensoren eines Geräts ein JSON-Topic (…/state), sagt es nichts über den Sensor
    return ' '.join(str(sensor.get(key) or '') for key in ('name', 'unique_id', 'object_id')).lower()


def suggest_field(sensor: Dict[str, Any]) -> Optional[str]:
    """Feld in solar_data, das der Sensor vermutlich misst (die Einstellung kann es übersteuern)."""
    if sensor.get('component', 'sensor') != 'sensor':
        return None                 # binary_sensor u. a. sind keine Messwerte
    unit = (sensor.get('unit') or '').lower()
    device_class = (sensor.get('device_class') or '').lower()
    words = _words(sensor)
    if unit in _POWER_UNITS or device_class == 'power':
        if any(w in words for w in _FEED_IN):
            return 'grid_feed_in_w'
        if any(w in words for w in _CONSUMPTION):
            return 'grid_consumption_w'
        if any(w in words for w in _DC):
            return 'dc_power_w'
        return 'ac_power_w'
    if unit in _ENERGY_UNITS or device_class == 'energy':
        if any(w in words for w in _FEED_IN + _CONSUMPTION):
            return None
        if any(w in words for w in _DAY):
            return 'pv_yield_day_wh'
        if any(w in words for w in _TOTAL):
            return 'pv_yield_total_wh'
        return None
    if device_class == 'temperature' or unit in ('°c', 'c'):
        return 'inverter_temperature'
    if device_class == 'voltage' or unit == 'v':
        return 'string_voltage_v' if any(w in words for w in _PV_SIDE) else None
    if device_class == 'current' or unit == 'a':
        return 'string_current_a' if any(w in words for w in _PV_SIDE) else None
    if not unit and device_class in ('', 'enum') and any(w in words for w in _STATUS) and not _IDENTIFIER.search(words):
        return 'inverter_status'
    return None


def to_number(field: str, value: str, unit: str) -> Optional[float]:
    """Messwert in die Einheit des Felds umrechnen (W, Wh, °C, V, A)."""
    try:
        number = float(str(value).replace(',', '.'))
    except (TypeError, ValueError):
        return None
    if number != number or number in (float('inf'), float('-inf')):
        return None
    unit = (unit or '').lower()
    if field.endswith('_w'):
        number *= _POWER_UNITS.get(unit, 1.0)
    elif field.endswith('_wh'):
        number *= _ENERGY_UNITS.get(unit, 1.0)
    elif field == 'inverter_temperature' and unit in ('°f', 'f'):
        number = (number - 32) * 5 / 9
    return round(number, 3)


# SMA-Zustandscodes (Modbus/Speedwire) und übliche Texte
_STATUS_CODES = {'307': 'ONLINE', '35': 'FAULT', '303': 'OFFLINE', '455': 'WARNING', '381': 'OFFLINE', '1392': 'FAULT'}
_STATUS_WORDS = (
    (('fault', 'fehler', 'error', 'störung'), 'FAULT'),
    (('warn',), 'WARNING'),
    (('off', 'aus', 'standby', 'stop', 'sleep', 'night', 'nacht'), 'OFFLINE'),
    (('ok', 'online', 'mpp', 'run', 'betrieb', 'on', 'normal'), 'ONLINE'),
)


def normalize_status(value: str) -> str:
    text = str(value or '').strip()
    if not text:
        return 'UNKNOWN'
    if text in _STATUS_CODES:
        return _STATUS_CODES[text]
    lowered = text.lower()
    for words, status in _STATUS_WORDS:
        if any(re.search(rf'\b{re.escape(w)}', lowered) for w in words):
            return status
    return re.sub(r'[^A-Z0-9_ -]', '', text.upper())[:32] or 'UNKNOWN'
