#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PV-Wechselrichter per MQTT (Home-Assistant-Discovery)

Der Dienst abonniert <Präfix>/+/+/config und <Präfix>/+/+/+/config, liest daraus die Sensoren (discovery.py) und
abonniert deren state_topic. Die Einstellung "mapping" (Feld → Topic) übersteuert die automatische Zuordnung.
Im Abstand sample_seconds wird aus den aktuellen Werten ein Messwert gebildet und gepuffert gespeichert.

paho-mqtt arbeitet in einem eigenen Thread und verbindet sich nach einer Unterbrechung selbst neu.
"""

import logging
import socket
import ssl
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from solar import discovery
from solar.discovery import NUMERIC_FIELDS
from solar.settings import SolarSettingsStore

import secret_store

logger = logging.getLogger(__name__)

MAX_SENSORS = 300
# Mehrere Sensoren eines Felds werden summiert (z. B. DC-Leistung je String), sonst gilt der jüngste Wert
SUMMED_FIELDS = ('dc_power_w',)


def _client_id() -> str:
    return ("powernexus-" + socket.gethostname())[:23]


def _create_client(settings: Dict[str, Any]):
    import paho.mqtt.client as mqtt

    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id=_client_id())
    if settings.get("username"):
        client.username_pw_set(settings["username"], secret_store.decrypt(settings.get("password")))
    if settings.get("tls"):
        verify = settings.get("tls_verify", True)
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED if verify else ssl.CERT_NONE)
        if not verify:
            client.tls_insecure_set(True)
    client.reconnect_delay_set(min_delay=2, max_delay=120)
    return client


class SolarService:
    def __init__(self, store: SolarSettingsStore, database, client_factory: Callable = _create_client):
        self.store = store
        self.database = database
        self._client_factory = client_factory
        self._lock = threading.RLock()
        self._client = None
        self._settings: Dict[str, Any] = store.get()
        self._sensors: Dict[str, Dict[str, Any]] = {}          # config_topic → Sensor
        self._values: Dict[str, Dict[str, Tuple[Any, float]]] = {}   # Feld → {Quelle: (Wert, Zeit)}
        self._availability: Dict[str, str] = {}                 # availability-Topic → letzte Nachricht
        self._connected = False
        self._error = ""
        self._last_message = 0.0
        self._last_sample = 0.0
        self._stop = threading.Event()
        self._sampler: Optional[threading.Thread] = None

    # ------------------------------------------------------------
    # Start / Stop / neue Einstellungen
    # ------------------------------------------------------------

    def start(self) -> None:
        self._sampler = threading.Thread(target=self._sample_loop, daemon=True, name="Solar-Sampler")
        self._sampler.start()
        self.apply()

    def stop(self) -> None:
        self._stop.set()
        self._disconnect()

    def apply(self) -> None:
        """Einstellungen (neu) übernehmen: alte Verbindung trennen, bei Bedarf neu verbinden."""
        self._disconnect()
        settings = self.store.get()
        with self._lock:
            self._settings = settings
            self._sensors.clear()
            self._values.clear()
            self._availability.clear()
            self._error = ""
        if not settings["enabled"] or not settings["host"]:
            return
        try:
            client = self._client_factory(settings)
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_message = self._on_message
            client.connect_async(settings["host"], int(settings["port"]), keepalive=60)
            client.loop_start()
            with self._lock:
                self._client = client
            logger.info(f"☀️ PV: verbinde mit MQTT-Broker {settings['host']}:{settings['port']}")
        except Exception as e:
            with self._lock:
                self._error = str(e)[:200]
            logger.error(f"❌ PV: MQTT-Verbindung nicht möglich: {e}")

    def _disconnect(self) -> None:
        with self._lock:
            client, self._client, self._connected = self._client, None, False
        if client is not None:
            try:
                client.disconnect()
                client.loop_stop()
            except Exception as e:
                logger.debug(f"PV: Trennen: {e}")

    # ------------------------------------------------------------
    # MQTT-Rückrufe (Thread von paho)
    # ------------------------------------------------------------

    def _subscriptions(self) -> List[str]:
        prefix = self._settings["discovery_prefix"]
        topics = [f"{prefix}/+/+/config", f"{prefix}/+/+/+/config"]
        # Kennungen ohne "/" meinen einen Sensor (unique_id/object_id), keine Topics
        return topics + [t for t in self._settings["mapping"].values() if "/" in t and t not in topics]

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        if getattr(reason_code, "is_failure", False):
            with self._lock:
                self._connected, self._error = False, str(reason_code)[:200]
            logger.error(f"❌ PV: Broker lehnt die Verbindung ab: {reason_code}")
            return
        with self._lock:
            self._connected, self._error = True, ""
            topics = self._subscriptions() + sorted({t for s in self._sensors.values()
                                                     for t in [s["state_topic"], *s["availability_topics"]]})
        for topic in topics:
            client.subscribe(topic)
        logger.info("✅ PV: mit MQTT-Broker verbunden")

    def _on_disconnect(self, client, userdata, flags=None, reason_code=None, properties=None) -> None:
        with self._lock:
            self._connected = False
        if reason_code is not None and getattr(reason_code, "is_failure", False):
            logger.warning(f"⚠️ PV: Verbindung zum Broker getrennt ({reason_code}), neuer Versuch folgt")

    def _on_message(self, client, userdata, message) -> None:
        try:
            self.handle_message(message.topic, message.payload, client)
        except Exception as e:
            logger.error(f"❌ PV: Nachricht {message.topic} nicht verarbeitet: {e}")

    # ------------------------------------------------------------
    # Nachrichten (ohne Netzwerk testbar)
    # ------------------------------------------------------------

    def handle_message(self, topic: str, payload: bytes, client=None) -> None:
        prefix = self._settings["discovery_prefix"]
        with self._lock:
            self._last_message = time.time()
        if topic.startswith(prefix + "/") and topic.endswith("/config"):
            self._handle_config(topic, payload, client)
            return
        with self._lock:
            if any(topic in s["availability_topics"] for s in self._sensors.values()):
                self._availability[topic] = payload[:32].decode("utf-8", "replace").strip()
                return
        self._handle_state(topic, payload)

    def _device_matches(self, sensor: Dict[str, Any]) -> bool:
        wanted = self._settings["device"].lower()
        return not wanted or wanted in sensor["device_name"].lower() or wanted == sensor["device_id"].lower()

    def _handle_config(self, topic: str, payload: bytes, client) -> None:
        sensor = discovery.parse_config(topic, payload, self._settings["discovery_prefix"])
        with self._lock:
            if sensor is None:
                self._sensors.pop(topic, None)      # leere Nachricht: Sensor wurde entfernt
                return
            if not self._device_matches(sensor):
                return
            if topic not in self._sensors and len(self._sensors) >= MAX_SENSORS:
                return
            sensor["suggested"] = discovery.suggest_field(sensor)
            known = {t for s in self._sensors.values() for t in [s["state_topic"], *s["availability_topics"]]}
            previous = self._sensors.get(topic, {})
            sensor["value"], sensor["updated"] = previous.get("value"), previous.get("updated")
            self._sensors[topic] = sensor
        if client is not None:
            for wanted in [sensor["state_topic"], *sensor["availability_topics"]]:
                if wanted not in known:
                    client.subscribe(wanted)

    def _available(self, sensor: Optional[Dict[str, Any]]) -> bool:
        """Nicht verfügbar, sobald eines seiner availability-Topics den Wert für „nicht verfügbar“ meldet."""
        if not sensor:
            return True
        offline = sensor["payload_not_available"].lower()
        return all(self._availability.get(t, "").lower() != offline for t in sensor["availability_topics"])

    def _refers_to(self, mapped: str, sensor: Optional[Dict[str, Any]], topic: str) -> bool:
        """Eine Zuordnung nennt einen Sensor über unique_id oder object_id, oder über das Topic, wenn nur er dort liegt
        (viele Geräte senden alle Werte als ein JSON auf ein gemeinsames Topic)."""
        if sensor is None:
            return mapped == topic
        if mapped in (sensor["unique_id"], sensor["object_id"]) and mapped:
            return True
        shared = sum(1 for s in self._sensors.values() if s["state_topic"] == sensor["state_topic"]) > 1
        return mapped == sensor["state_topic"] and not shared

    def _fields_for(self, sensor: Optional[Dict[str, Any]], topic: str) -> List[str]:
        mapping = self._settings["mapping"]
        fields = [field for field, mapped in mapping.items() if self._refers_to(mapped, sensor, topic)]
        if sensor and sensor.get("suggested") and sensor["suggested"] not in mapping:
            fields.append(sensor["suggested"])
        return fields

    def _mapped_topics(self) -> set:
        """Zuordnungen, die keinen bekannten Sensor nennen, sind Topics ohne Discovery."""
        known = {key for s in self._sensors.values() for key in (s["unique_id"], s["object_id"]) if key}
        return {mapped for mapped in self._settings["mapping"].values() if mapped not in known}

    def _handle_state(self, topic: str, payload: bytes) -> None:
        now = time.time()
        with self._lock:
            sensors = [(key, s) for key, s in self._sensors.items() if s["state_topic"] == topic]
            if not sensors and topic in self._mapped_topics():
                sensors = [(topic, None)]
            for key, sensor in sensors:
                text = discovery.render_value(sensor, payload) if sensor else discovery.render_value({}, payload)
                if text is None:
                    continue
                if sensor is not None:
                    sensor["value"], sensor["updated"] = text[:64], int(now)
                for field in self._fields_for(sensor, topic):
                    if field == "inverter_status":
                        value = discovery.normalize_status(text)
                    else:
                        value = discovery.to_number(field, text, (sensor or {}).get("unit", ""))
                    if value is not None:
                        self._values.setdefault(field, {})[key] = (value, now)

    # ------------------------------------------------------------
    # Messwerte
    # ------------------------------------------------------------

    def _max_age(self) -> float:
        return max(3 * int(self._settings["sample_seconds"]), 300)

    def snapshot(self) -> Optional[Dict[str, Any]]:
        """Aktuelle Werte aller Felder, None wenn nichts Frisches vorliegt."""
        now = time.time()
        with self._lock:
            result: Dict[str, Any] = {}
            newest = 0.0
            for field, sources in self._values.items():
                # Werte eines nicht verfügbaren Geräts sind veraltet, auch wenn der Broker sie gerade (retained) geliefert hat
                fresh = [(value, at) for key, (value, at) in sources.items()
                         if now - at <= self._max_age() and self._available(self._sensors.get(key))]
                if not fresh:
                    continue
                newest = max(newest, *(at for _, at in fresh))
                if field in SUMMED_FIELDS:
                    result[field] = round(sum(value for value, _ in fresh), 3)
                else:
                    result[field] = max(fresh, key=lambda item: item[1])[0]
            if not result:
                return None
            device = next((s for s in self._sensors.values() if s["device_name"] or s["device_id"]), None)
        result["timestamp"] = int(newest)
        result["inverter_name"] = device["device_name"] if device else None
        result["inverter_serial"] = (device["serial_number"] or device["device_id"]) if device else None
        result.setdefault("inverter_status", "ONLINE" if any(f in result for f in NUMERIC_FIELDS) else "UNKNOWN")
        result["source"] = "mqtt"
        return result

    def sample(self) -> bool:
        snapshot = self.snapshot()
        if not snapshot or not any(field in snapshot for field in NUMERIC_FIELDS):
            return False
        snapshot["timestamp"] = int(time.time())
        self._last_sample = time.time()
        return self.database.insert_solar(snapshot)

    def _sample_loop(self) -> None:
        while not self._stop.wait(timeout=int(self._settings["sample_seconds"])):
            try:
                self.sample()
            except Exception as e:
                logger.error(f"❌ PV: Messwert nicht gespeichert: {e}")

    # ------------------------------------------------------------
    # Auskunft für die Oberfläche
    # ------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        with self._lock:
            mapped = sorted({field for field, sources in self._values.items() if sources})
            watched = [s for s in self._sensors.values() if s["availability_topics"]]
            available = all(self._available(s) for s in watched) if watched else None
            return {"enabled": self._settings["enabled"], "connected": self._connected, "error": self._error,
                    "sensors": len(self._sensors), "fields": mapped, "available": available,
                    "last_message": int(self._last_message) or None, "last_sample": int(self._last_sample) or None}

    def sensors(self) -> List[Dict[str, Any]]:
        mapping = self._settings["mapping"]
        with self._lock:
            result = []
            for sensor in self._sensors.values():
                fields = self._fields_for(sensor, sensor["state_topic"])
                shared = sum(1 for s in self._sensors.values() if s["state_topic"] == sensor["state_topic"]) > 1
                result.append({key: sensor[key] for key in ("name", "state_topic", "unit", "device_class", "device_name",
                                                            "suggested", "value", "updated")}
                              | {"field": fields[0] if fields else None, "available": self._available(sensor),
                                 # womit sich der Sensor von Hand zuordnen lässt
                                 "reference": (sensor["unique_id"] or sensor["object_id"]) if shared else sensor["state_topic"]})
        return sorted(result, key=lambda s: (s["device_name"], s["name"]))


def probe_connection(settings: Dict[str, Any], timeout: float = 8.0, listen: float = 3.0,
                     client_factory: Callable = _create_client) -> Dict[str, Any]:
    """Verbindung mit den angegebenen Einstellungen versuchen und zählen, wie viele Discovery-Nachrichten kommen."""
    done = threading.Event()
    result: Dict[str, Any] = {"success": False, "error": "", "sensors": 0}
    prefix = settings["discovery_prefix"]

    def on_connect(client, userdata, flags, reason_code, properties=None):
        if getattr(reason_code, "is_failure", False):
            result["error"] = str(reason_code)
            done.set()
            return
        result["success"] = True
        client.subscribe(f"{prefix}/+/+/config")
        client.subscribe(f"{prefix}/+/+/+/config")
        done.set()

    def on_message(client, userdata, message):
        if message.payload:
            result["sensors"] += 1

    client = client_factory(settings)
    client.on_connect, client.on_message = on_connect, on_message
    try:
        client.connect(settings["host"], int(settings["port"]), keepalive=10)
        client.loop_start()
        if not done.wait(timeout):
            result["error"] = "timeout"
        elif result["success"]:
            time.sleep(listen)      # gespeicherte (retained) Discovery-Nachrichten kommen gleich nach dem Abonnieren
    except Exception as e:
        result["error"] = str(e)[:200]
    finally:
        try:
            client.disconnect()
            client.loop_stop()
        except Exception:
            pass
    return result
