#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SNMP & USB UPS Manager Classes
Extracted from app.py for better modularity
"""

import tempfile
import os
import subprocess
import random
import logging
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


# ups.test.result von NUT, abgebildet auf upsTestResultsSummary der UPS-MIB (siehe battery_assessment)
NUT_SELFTEST_RESULTS = {"done and passed": 1, "done and warning": 2, "done and error": 3, "aborted": 4,
                        "in progress": 5, "no test initiated": 6}


class USBUPSManager:
    """USB-basierte UPS via NUT (Network UPS Tools)"""
    
    def __init__(self, profile: Dict, demo_mode: bool = False):
        self.profile = profile
        self.demo_mode = demo_mode
        self.ups_name = profile.get('ups_name', 'ups')
        self.manufacturer = profile.get('manufacturer', 'Generic')
        self.ip = "USB (lokal)"
        self.polling_interval = profile.get('polling_interval', 10)
        logger.info(f"USB UPS Manager für {profile.get('name')} (NUT: {self.ups_name})")
    
    def _upsc_get(self, key: str) -> Optional[Any]:
        if self.demo_mode:
            return self._get_demo_value(key)
        
        try:
            cmd = ["upsc", self.ups_name, key]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
            if result.returncode != 0:
                return None
            value = result.stdout.strip()
            if not value:
                return None
            parts = value.split()
            if parts:
                try:
                    return float(parts[0])
                except ValueError:
                    return value
            return value
        except Exception as e:
            logger.debug(f"upsc Fehler für {key}: {e}")
            return None
    
    def _get_demo_value(self, key: str) -> Any:
        demo_values = {
            "ups.status": "OL",
            "ups.load": random.randint(10, 40),
            "battery.charge": random.randint(85, 100),
            "battery.runtime": random.randint(1800, 5400),
            "battery.voltage": random.randint(240, 260) / 10,
            "battery.temperature": random.randint(25, 35),
            "input.voltage": random.randint(225, 235),
            "output.voltage": random.randint(225, 235),
            "ups.mfr": "Demo USB UPS",
            "ups.model": "Demo Model",
            "ups.serial": "DEMO-USB-001",
        }
        return demo_values.get(key, None)
    
    def get_all_data(self) -> Dict[str, Any]:
        data = {
            "profile": self.profile.get('name', 'Unknown'),
            "manufacturer": self.manufacturer,
            "ip": self.ip,
            "timestamp": datetime.now().isoformat(),
            "demo_mode": self.demo_mode,
            "connection_type": "usb",
            "last_test": "N/A",
            "firmware": "N/A",
            "input_current_formatted": 0,
            "output_current_formatted": 0
        }
        
        logger.info(f"Abrufen USB-USV-Daten von {self.ups_name}...")
        
        keys = {
            "ups.status": "status",
            "ups.load": "output_load",
            "battery.charge": "battery_charge",
            "battery.runtime": "runtime",
            "battery.voltage": "battery_voltage",
            "battery.temperature": "battery_temp",
            "input.voltage": "input_voltage",
            "output.voltage": "output_voltage",
            "ups.mfr": "manufacturer_name",
            "ups.model": "model",
            "ups.serial": "serial",
            "ups.firmware": "firmware",
        }
        
        for nut_key, data_key in keys.items():
            value = self._upsc_get(nut_key)
            data[data_key] = value if value is not None else "N/A"

        # Netzfrequenz nur übernehmen, wenn die USV einen Zahlenwert liefert
        frequency = self._upsc_get("input.frequency")
        if isinstance(frequency, (int, float)):
            data["input_frequency"] = frequency

        self._read_battery_diagnosis(data)

        # Status interpretieren
        status_val = str(data.get("status", ""))
        if "OL" in status_val:
            data["status_text"] = "ONLINE"
            data["status_icon"] = "✅"
            data["status_class"] = "online"
        elif "OB" in status_val:
            data["status_text"] = "ON BATTERY"
            data["status_icon"] = "⚠️"
            data["status_class"] = "battery"
        elif "OFF" in status_val:
            data["status_text"] = "OFFLINE"
            data["status_icon"] = "🔴"
            data["status_class"] = "unknown"
        else:
            data["status_text"] = "UNKNOWN"
            data["status_icon"] = "❌"
            data["status_class"] = "unknown"
        
        self._calculate_derived_values(data)
        
        return data
    
    def _read_battery_diagnosis(self, data: Dict[str, Any]) -> None:
        """Selbsttest und Batteriedatum für die Batteriebewertung, wie sie NUT meldet"""
        result = str(self._upsc_get("ups.test.result") or "").strip().lower()
        for text, code in NUT_SELFTEST_RESULTS.items():
            if result.startswith(text):
                data["selftest_result_mib"] = code
                break
        battery_date = self._upsc_get("battery.date")
        if battery_date:
            data["battery_replace_date"] = str(battery_date)
            # Viele USVs melden hier das Herstellungsdatum, bis jemand es nach einem Tausch ändert
            data["battery_date_unconfirmed"] = battery_date == self._upsc_get("battery.mfr.date")

    def _calculate_derived_values(self, data: Dict[str, Any]):
        """Berechne abgeleitete Werte"""
        # Runtime (Sekunden -> Minuten)
        if isinstance(data.get("runtime"), (int, float)):
            data["runtime_minutes"] = round(data["runtime"] / 60, 1)
            mins = int(data["runtime_minutes"])
            secs = int(data["runtime"] % 60)
            data["runtime_formatted"] = f"{mins}:{secs:02d}"
        else:
            data["runtime_minutes"] = 0
            data["runtime_formatted"] = "0:00"
        
        # Temperatur
        if isinstance(data.get("battery_temp"), (int, float)):
            data["battery_temp_celsius"] = data["battery_temp"]
        else:
            data["battery_temp_celsius"] = 0
        
        # Spannungen
        for key in ["battery_voltage", "input_voltage", "output_voltage"]:
            if isinstance(data.get(key), (int, float)):
                data[f"{key}_formatted"] = data[key]
            else:
                data[f"{key}_formatted"] = 0
        
        # Leistung aus Last berechnen (400W Nennleistung für Back-UPS CS 650)
        if isinstance(data.get("output_load"), (int, float)):
            load_percent = data["output_load"]
            data["output_power_w"] = round(400 * load_percent / 100)
            data["output_power_va"] = round(400 * load_percent / 100)
        else:
            data["output_power_w"] = 0
            data["output_power_va"] = 0


class SNMPManager:
    """SNMP-basierte UPS mit Multi-Vendor Support"""
    
    def __init__(self, profile: Dict, demo_mode: bool = False):
        self.profile = profile
        self.demo_mode = demo_mode
        self.manufacturer = profile.get('manufacturer', 'APC')
        self.ip = profile.get('ip')
        self.community = profile.get('community', 'public')
        self.snmp_version = profile.get('snmp_version', 2)
        self.snmp_timeout = profile.get('snmp_timeout', 8)
        self.snmp_retries = profile.get('snmp_retries', 2)
        self.polling_interval = profile.get('polling_interval', 10)
        
        self._snmp_conf_dir = None
        if self.snmp_version == 3:
            self.version_str = "3"
        elif self.snmp_version == 2:
            self.version_str = "2c"
        else:
            self.version_str = "1"
        
        logger.info(f"SNMP Manager für {profile.get('name')} ({self.ip}) mit SNMPv{self.snmp_version}")
    
    def _security_args(self) -> list:
        if self.snmp_version != 3:
            return ["-v", self.version_str, "-c", self.community]
        return ["-v", "3", "-l", "authPriv", "-u", str(self.profile.get('snmp_user', '')), "-a", "SHA", "-x", "AES"]

    def _snmp_env(self) -> Optional[dict]:
        """SNMPv3-Passwörter stehen in einer Datei (Rechte 600), nicht auf der Kommandozeile: Die sähe jeder lokale Benutzer."""
        if self.snmp_version != 3:
            return None
        if self._snmp_conf_dir is None:
            import secret_store
            folder = tempfile.mkdtemp(prefix="usv-snmp-")
            fd = os.open(os.path.join(folder, "snmp.conf"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(f"defAuthPassphrase {secret_store.decrypt(self.profile.get('snmp_auth_secret'))}\n")
                f.write(f"defPrivPassphrase {secret_store.decrypt(self.profile.get('snmp_priv_secret'))}\n")
                f.write(f"persistentDir {folder}\n")
            self._snmp_conf_dir = folder
        return dict(os.environ, SNMPCONFPATH=self._snmp_conf_dir)

    def _snmp_get(self, oid: str) -> Optional[Any]:
        """SNMP GET mit verbessertem Error Handling"""
        if not oid or self.demo_mode:
            return None
        
        try:
            cmd = [
                "snmpget", *self._security_args(),
                "-Oqvt",   # nur Wert; Timeticks als Zahl (Hundertstelsekunden)
                "-t", str(self.snmp_timeout),
                "-r", str(self.snmp_retries),
                self.ip, oid
            ]
            
            result = subprocess.run(
                cmd, 
                capture_output=True, 
                text=True, 
                timeout=self.snmp_timeout + 2,
                env=self._snmp_env(),
            )
            
            if result.returncode != 0:
                if result.returncode == 124:
                    logger.warning(f"⏱️ SNMP Timeout für OID {oid} nach {self.snmp_timeout}s")
                else:
                    logger.debug(f"⚠️ SNMP Fehler (Code {result.returncode}): {result.stderr.strip()}")
                return None
            
            raw_value = result.stdout.strip()
            if raw_value.startswith('"'):
                # SNMP-Zeichenkette: unverändert lassen (z. B. Flag-Felder "0010…", Datumsangaben)
                return raw_value.strip('"') or None
            value = raw_value
            if not value:
                return None
            
            # Wert konvertieren
            if value.lstrip('-').isdigit():
                return int(value)
            try:
                return float(value)
            except ValueError:
                return value
                
        except subprocess.TimeoutExpired:
            logger.error(f"❌ SNMP subprocess timeout für OID {oid}")
            return None
        except FileNotFoundError:
            logger.error(f"❌ snmpget nicht gefunden - bitte NUT/SNMP-Tools installieren")
            return None
        except Exception as e:
            logger.error(f"❌ SNMP Fehler bei OID {oid}: {e}")
            return None
    
    def _get_demo_value(self, oid: str) -> Any:
        """Demo-Werte für Testing ohne SNMP-Hardware"""
        if 'runtime' in oid:
            return max(0, random.gauss(30, 5)) * 60
        if 'charge' in oid:
            return max(0, min(100, random.gauss(85, 10)))
        if 'temp' in oid:
            return max(15, min(45, random.gauss(25, 3)))
        if 'voltage' in oid:
            return max(210, min(250, random.gauss(230, 5))) * 10
        if 'load' in oid:
            return max(0, min(100, random.gauss(35, 15)))
        if 'output_source' in oid:
            return 3
        return None
    
    def get_all_data(self) -> Dict[str, Any]:
        """Hole alle Daten von der UPS"""
        data = {
            "profile": self.profile.get('name', 'Unknown'),
            "manufacturer": self.manufacturer,
            "ip": self.ip,
            "timestamp": datetime.now().isoformat(),
            "demo_mode": self.demo_mode,
            "connection_type": "snmp",
            "last_test": "N/A",
            "firmware": "N/A",
            "input_current_formatted": 0,
            "output_current_formatted": 0
        }
        
        logger.info(f"Abrufen SNMP-Daten von {self.ip}...")
        
        # Output Source (Status) abfragen
        output_source = self._snmp_get("1.3.6.1.2.1.33.1.4.1.0")
        data["output_source"] = output_source if output_source is not None else 0
        logger.info(f"  output_source = {data['output_source']}")
        
        # OID-Maps pro Hersteller
        oid_maps = {
            "APC": {
                "battery_charge": "1.3.6.1.2.1.33.1.2.4.0",
                "battery_voltage": "1.3.6.1.2.1.33.1.2.5.0",
                "battery_temp": "1.3.6.1.2.1.33.1.2.7.0",
                "runtime_raw": "1.3.6.1.4.1.318.1.1.1.2.2.3.0",
                "input_voltage": "1.3.6.1.2.1.33.1.3.3.1.3.1",
                "input_frequency_raw": "1.3.6.1.2.1.33.1.3.3.1.2.1",  # UPS-MIB upsInputFrequency (0,1 Hz)
                "battery_status_mib": "1.3.6.1.2.1.33.1.2.1.0",     # 1=unknown 2=normal 3=low 4=depleted
                "selftest_result_mib": "1.3.6.1.2.1.33.1.7.3.0",    # 1=pass 2=warning 3=error 4=aborted 5=running 6=none
                "output_voltage": "1.3.6.1.2.1.33.1.4.4.1.2.1",
                "output_load": "1.3.6.1.2.1.33.1.4.4.1.5.1",
                "model": "1.3.6.1.2.1.33.1.1.2.0",
                "serial_std": "1.3.6.1.2.1.33.1.1.3.0",
                "serial_apc": "1.3.6.1.4.1.318.1.1.1.1.2.3.0",
                "firmware": "1.3.6.1.4.1.318.1.1.1.1.2.1.0",
                "model_apc": "1.3.6.1.4.1.318.1.1.1.1.1.1.0",
                "temperature_apc": "1.3.6.1.4.1.318.1.1.1.2.2.2.0",
                "battery_replace_date": "1.3.6.1.4.1.318.1.1.1.2.1.3.0",       # mm/dd/yyyy
                "battery_replace_indicator": "1.3.6.1.4.1.318.1.1.1.2.2.4.0",  # 1=ok 2=austauschen
                "selftest_result_apc": "1.3.6.1.4.1.318.1.1.1.7.2.3.0",        # 1=ok 2=failed 3=invalid 4=running
                "selftest_date_apc": "1.3.6.1.4.1.318.1.1.1.7.2.4.0",          # mm/dd/yyyy
                "calibration_result_apc": "1.3.6.1.4.1.318.1.1.1.7.2.6.0",     # 1=ok 2=invalid 3=running
                "state_flags_apc": "1.3.6.1.4.1.318.1.1.1.11.1.1.0",            # upsBasicStateOutputState (64 Flags)
                "battery_health_apc": "1.3.6.1.4.1.318.1.1.1.2.3.11.0",         # upsHighPrecBatteryHealth (Bits, ab AOS 6/7)
                "battery_replace_recommended_apc": "1.3.6.1.4.1.318.1.1.1.2.2.21.0",  # upsAdvBatteryRecommendedReplaceDate
                "battery_sku_apc": "1.3.6.1.4.1.318.1.1.1.2.2.19.0",            # upsAdvBatteryInternalSKU, z. B. RBC43
                "calibration_date_apc": "1.3.6.1.4.1.318.1.1.1.7.2.7.0",        # upsAdvTestCalibrationDate
            },
            "Woehrle": {
                "battery_charge": "1.3.6.1.2.1.33.1.2.4.0",
                "battery_voltage": "1.3.6.1.2.1.33.1.2.5.0",
                "battery_temp": "1.3.6.1.2.1.33.1.2.7.0",
                "runtime": "1.3.6.1.2.1.33.1.2.3.0",
                "input_voltage": "1.3.6.1.2.1.33.1.3.3.1.3.1",
                "input_frequency_raw": "1.3.6.1.2.1.33.1.3.3.1.2.1",  # UPS-MIB upsInputFrequency (0,1 Hz)
                "battery_status_mib": "1.3.6.1.2.1.33.1.2.1.0",     # 1=unknown 2=normal 3=low 4=depleted
                "selftest_result_mib": "1.3.6.1.2.1.33.1.7.3.0",    # 1=pass 2=warning 3=error 4=aborted 5=running 6=none
                "input_current": "1.3.6.1.2.1.33.1.3.3.1.4.1",
                "output_voltage": "1.3.6.1.2.1.33.1.4.4.1.2.1",
                "output_current": "1.3.6.1.2.1.33.1.4.4.1.3.1",
                "output_power_w": "1.3.6.1.2.1.33.1.4.4.1.4.1",
                "output_load": "1.3.6.1.2.1.33.1.4.4.1.5.1",
                "output_power_va": "1.3.6.1.2.1.33.1.4.4.1.6.1",
                "model": "1.3.6.1.2.1.33.1.1.2.0",
                "serial": "1.3.6.1.2.1.33.1.1.3.0",
                "firmware": "1.3.6.1.2.1.33.1.1.4.0",
                "last_test": "1.3.6.1.2.1.33.1.2.9.0",
            },
            "Dell": {
                "battery_charge": "1.3.6.1.2.1.33.1.2.4.0",
                "battery_voltage": "1.3.6.1.2.1.33.1.2.5.0",
                "battery_temp": "1.3.6.1.2.1.33.1.2.7.0",
                "battery_temp_dell_verified": "1.3.6.1.4.1.674.10902.2.120.7.3.5.0",
                "runtime": "1.3.6.1.2.1.33.1.2.3.0",
                "input_voltage": "1.3.6.1.2.1.33.1.3.3.1.3.1",
                "input_frequency_raw": "1.3.6.1.2.1.33.1.3.3.1.2.1",  # UPS-MIB upsInputFrequency (0,1 Hz)
                "battery_status_mib": "1.3.6.1.2.1.33.1.2.1.0",     # 1=unknown 2=normal 3=low 4=depleted
                "selftest_result_mib": "1.3.6.1.2.1.33.1.7.3.0",    # 1=pass 2=warning 3=error 4=aborted 5=running 6=none
                "output_voltage": "1.3.6.1.2.1.33.1.4.4.1.2.1",
                "output_current": "1.3.6.1.2.1.33.1.4.4.1.3.1",
                "output_power_w": "1.3.6.1.2.1.33.1.4.4.1.4.1",
                "output_load": "1.3.6.1.2.1.33.1.4.4.1.5.1",
                "model": "1.3.6.1.2.1.33.1.1.2.0",
                "serial": "1.3.6.1.2.1.33.1.1.3.0",
                "firmware": "1.3.6.1.2.1.33.1.1.4.0",
            }
        }
        
        # Standard OIDs falls Hersteller nicht bekannt
        if self.manufacturer not in oid_maps:
            oid_maps[self.manufacturer] = {
                "battery_charge": "1.3.6.1.2.1.33.1.2.4.0",
                "battery_voltage": "1.3.6.1.2.1.33.1.2.5.0",
                "battery_temp": "1.3.6.1.2.1.33.1.2.7.0",
                "input_voltage": "1.3.6.1.2.1.33.1.3.3.1.3.1",
                "input_frequency_raw": "1.3.6.1.2.1.33.1.3.3.1.2.1",  # UPS-MIB upsInputFrequency (0,1 Hz)
                "battery_status_mib": "1.3.6.1.2.1.33.1.2.1.0",     # 1=unknown 2=normal 3=low 4=depleted
                "selftest_result_mib": "1.3.6.1.2.1.33.1.7.3.0",    # 1=pass 2=warning 3=error 4=aborted 5=running 6=none
                "output_voltage": "1.3.6.1.2.1.33.1.4.4.1.2.1",
                "output_load": "1.3.6.1.2.1.33.1.4.4.1.5.1",
                "model": "1.3.6.1.2.1.33.1.1.2.0",
                "serial": "1.3.6.1.2.1.33.1.1.3.0",
                "firmware": "1.3.6.1.2.1.33.1.1.4.0",
            }
        
        oids = oid_maps.get(self.manufacturer, oid_maps["APC"])
        
        # Hole alle OID-Werte
        for key, oid in oids.items():
            if self.demo_mode:
                value = self._get_demo_value(oid)
            else:
                value = self._snmp_get(oid)
            
            if self.manufacturer == "Dell" and "dell" in key.lower():
                logger.info(f"🔍 DELL OID {key} ({oid}) → {value}")
            
            data[key] = value if value is not None else "N/A"
        
        # DELL: Temperatur-Fallback wenn Standard-OID = 0
        if self.manufacturer == "Dell":
            battery_temp = data.get("battery_temp")
            if battery_temp == 0 or battery_temp == "0":
                logger.info("🔧 DELL USV: Standard battery_temp=0, versuche Fallback-OIDs...")
                
                # Prüfe erst Custom-OID aus Config
                custom_oid = self.profile.get('custom_temp_oid')
                if custom_oid:
                    logger.info(f"  Versuche Custom-OID aus Config: {custom_oid}")
                    custom_temp = self._snmp_get(custom_oid)
                    if custom_temp and custom_temp != "N/A" and custom_temp != 0 and custom_temp != "0":
                        data["battery_temp"] = custom_temp
                        logger.info(f"✅ DELL battery_temp von Custom-OID: {custom_temp}°C")
                
                # Wenn Custom-OID nicht funktioniert: versuche verified OID
                if data.get("battery_temp") == 0 or data.get("battery_temp") == "0":
                    verified_temp = data.get("battery_temp_dell_verified")
                    if verified_temp and verified_temp != "N/A" and verified_temp != 0:
                        data["battery_temp"] = verified_temp
                        logger.info(f"✅ DELL battery_temp von verified OID: {verified_temp}°C")
                    else:
                        fallback_temp = self.profile.get('fallback_temperature', 25)
                        logger.warning(f"⚠️ DELL: Keine Temperatur-OID verfügbar, verwende Fallback ({fallback_temp}°C)")
                        data["battery_temp"] = fallback_temp
            
            # Entferne interne Fallback-Schlüssel
            data.pop("battery_temp_dell_verified", None)
        
        self._calculate_derived_values(data)
        
        if self.demo_mode:
            data["status_text"] = "ONLINE"
            data["battery_charge"] = random.randint(85, 100)
            data["runtime_minutes"] = random.randint(30, 180)
        
        return data
    
    def _calculate_derived_values(self, data: Dict[str, Any]):
        """Berechne abgeleitete Werte"""
        # Status aus output_source (RFC 1628)
        output_source = data.get("output_source")
        if output_source == 5:
            data["status_text"] = "ON BATTERY"
            data["status_icon"] = "⚠️"
            data["status_class"] = "battery"
            logger.info(f"  Status: ON BATTERY")
        elif output_source in [3, 6, 7]:
            data["status_text"] = "ONLINE"
            data["status_icon"] = "✅"
            data["status_class"] = "online"
            logger.info(f"  Status: ONLINE")
        elif output_source == 4:
            data["status_text"] = "BYPASS"
            data["status_icon"] = "🔄"
            data["status_class"] = "online"
            logger.info(f"  Status: BYPASS")
        else:
            data["status_text"] = "UNKNOWN"
            data["status_icon"] = "❌"
            data["status_class"] = "unknown"
            logger.info(f"  Status: UNKNOWN")
        
        # Runtime
        if self.manufacturer == "APC":
            runtime_raw = data.get("runtime_raw")
            data["runtime_available"] = isinstance(runtime_raw, (int, float))
            if isinstance(runtime_raw, (int, float)) and runtime_raw > 0:
                runtime_minutes = runtime_raw / 100 / 60
                data["runtime_minutes"] = round(runtime_minutes, 1)
                total_seconds = runtime_raw / 100
                mins = int(total_seconds // 60)
                secs = int(total_seconds % 60)
                data["runtime_formatted"] = f"{mins}:{secs:02d}"
            else:
                data["runtime_minutes"] = 0
                data["runtime_formatted"] = "0:00"
        elif self.manufacturer in ["Woehrle", "Dell"]:
            if isinstance(data.get("runtime"), (int, float)) and data["runtime"] > 0:
                data["runtime_minutes"] = data["runtime"]
                mins = int(data["runtime_minutes"])
                secs = int((data["runtime_minutes"] % 1) * 60)
                data["runtime_formatted"] = f"{mins}:{secs:02d}"
            else:
                data["runtime_minutes"] = 0
                data["runtime_formatted"] = "0:00"
        else:
            data["runtime_minutes"] = 0
            data["runtime_formatted"] = "0:00"
        
        # APC Spezial-Behandlungen
        if self.manufacturer == "APC":
            if data.get("serial_apc") and data["serial_apc"] != "N/A":
                data["serial"] = data["serial_apc"]
            elif data.get("serial_std") and data["serial_std"] != "N/A":
                data["serial"] = data["serial_std"]
            else:
                data["serial"] = "N/A"
            
            if data.get("model_apc") and data["model_apc"] != "N/A":
                data["model"] = data["model_apc"]
            
            if data.get("temperature_apc") and data["temperature_apc"] != "N/A":
                data["battery_temp"] = data["temperature_apc"]
        
        # Temperatur
        if isinstance(data.get("battery_temp"), (int, float)):
            data["battery_temp_celsius"] = data["battery_temp"]
        else:
            data["battery_temp_celsius"] = 0
        
        # Spannungen
        for key in ["battery_voltage", "input_voltage", "output_voltage"]:
            if isinstance(data.get(key), (int, float)):
                if key == "battery_voltage" and data[key] > 100:
                    data[f"{key}_formatted"] = round(data[key] / 10, 1)
                else:
                    data[f"{key}_formatted"] = data[key]
            else:
                data[f"{key}_formatted"] = 0
        
        # Netzfrequenz (UPS-MIB liefert 0,1 Hz)
        if isinstance(data.get("input_frequency_raw"), (int, float)) and data["input_frequency_raw"] > 0:
            data["input_frequency"] = round(data["input_frequency_raw"] / 10, 1)
        
        # Ströme
        for key in ["input_current", "output_current"]:
            if isinstance(data.get(key), (int, float)):
                if data[key] > 10:
                    data[f"{key}_formatted"] = round(data[key] / 10, 1)
                else:
                    data[f"{key}_formatted"] = data[key]
            else:
                data[f"{key}_formatted"] = 0
        
        # Leistung
        if self.manufacturer == "APC":
            if isinstance(data.get("output_load"), (int, float)):
                load_percent = data["output_load"]
                data["output_power_w"] = round(2700 * load_percent / 100)
                data["output_power_va"] = round(3000 * load_percent / 100)
            else:
                data["output_power_w"] = 0
                data["output_power_va"] = 0
        else:
            data["output_power_w"] = data.get("output_power_w", 0)
            data["output_power_va"] = data.get("output_power_va", data.get("output_power_w", 0))
        
        # Warnung: ON BATTERY + Runtime = 0
        data["runtime_warning"] = (data["status_text"] == "ON BATTERY" and data["runtime_minutes"] == 0)
        
        logger.info(f"  Final: {data['status_text']} | Batterie={data.get('battery_charge', 0)}% | Laufzeit={data.get('runtime_minutes', 0)}min | Temperatur={data.get('battery_temp_celsius', 0)}°C")


def create_ups_manager(profile: Dict, demo_mode: bool = False):
    """Factory für UPS-Manager"""
    conn_type = profile.get('connection_type', 'snmp')
    if conn_type == 'usb':
        return USBUPSManager(profile, demo_mode)
    else:
        return SNMPManager(profile, demo_mode)
