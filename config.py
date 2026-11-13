#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config.py - Zentrale Konfigurationsverwaltung
Refaktoriert aus app.py für bessere Verwaltung und Environment Variable Support
Version 2.1 - Phase 4.1: With Cache Configuration
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class ConfigManager:
    """Verwaltet Konfiguration aus config.json + Environment Variables"""
    
    def __init__(self, script_dir: Path):
        """
        Initialisiert ConfigManager
        
        Args:
            script_dir: Verzeichnis der Hauptanwendung
        """
        self.script_dir = script_dir
        self.config_path = script_dir / "config.json"
        self.config = self._load_config()
    
    def _load_config(self) -> Dict[str, Any]:
        """
        Lädt Konfiguration aus config.json, fällt auf Defaults zurück
        
        Returns:
            Konfigurationsdictionary
        """
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                logger.info(f"✅ Konfiguration geladen: {self.config_path}")
                logger.info(f"   Aktives Profil: {config.get('active_profile', 'keins')}")
                logger.info(f"   Profile: {', '.join(config.get('profiles', {}).keys())}")
                return config
            except json.JSONDecodeError as e:
                logger.error(f"❌ JSON Fehler in config.json: {e}")
                return self._get_default_config()
            except Exception as e:
                logger.error(f"❌ Fehler beim Lesen: {e}")
                return self._get_default_config()
        else:
            logger.warning(f"⚠️ config.json nicht gefunden, erstelle mit Defaults")
            return self._create_default_config()
    
    def _get_default_config(self) -> Dict[str, Any]:
        """Gibt Default-Konfiguration zurück"""
        return {
            "active_profile": "",
            "web_port": 8080,
            "demo_mode": False,
            "profiles": {},
        }

    def _create_default_config(self) -> Dict[str, Any]:
        """Erstellt Default-config.json"""
        config = self._get_default_config()
        self.save_config(config)
        return config
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Liest Konfigurationswert
        Priority: Environment Variable > config.json > default
        
        Args:
            key: Konfigurationsschlüssel (z.B. 'web_port', 'active_profile')
            default: Fallback-Wert
            
        Returns:
            Konfigurationswert
        """
        # Environment Variable prüfen (mit UPS_ Präfix)
        env_key = f"UPS_{key.upper()}"
        env_value = os.getenv(env_key)
        if env_value is not None:
            # Typ-Konvertierung
            if env_value.lower() in ('true', 'false'):
                return env_value.lower() == 'true'
            elif env_value.isdigit():
                return int(env_value)
            return env_value
        
        # config.json prüfen
        value = self.config.get(key, default)
        return value
    
    def save_config(self, config: Dict[str, Any]) -> bool:
        """
        Speichert Konfiguration in config.json
        
        Args:
            config: Zu speicherndes Dictionary
            
        Returns:
            True bei Erfolg
        """
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            logger.info(f"💾 Konfiguration gespeichert: {self.config_path}")
            self.config = config
            return True
        except Exception as e:
            logger.error(f"❌ Fehler beim Speichern: {e}")
            return False
    
    def update_active_profile(self, profile_name: str) -> bool:
        """
        Wechselt aktives Profil
        
        Args:
            profile_name: Name des neuen Profils
            
        Returns:
            True bei Erfolg
        """
        if profile_name not in self.config.get('profiles', {}):
            logger.error(f"❌ Profil '{profile_name}' nicht gefunden")
            return False
        
        self.config['active_profile'] = profile_name
        return self.save_config(self.config)
    
    def get_profile(self, profile_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Ruft Profil ab
        
        Args:
            profile_name: Name des Profils (oder aktives Profil wenn None)
            
        Returns:
            Profil-Dictionary oder None
        """
        if profile_name is None:
            profile_name = self.get('active_profile', 'APC Back-UPS')
        
        return self.config.get('profiles', {}).get(profile_name)
    
    def list_profiles(self) -> list:
        """Gibt Liste aller verfügbaren Profile"""
        return list(self.config.get('profiles', {}).keys())

    def add_profile(self, profile_name: str, profile_config: Dict[str, Any]) -> bool:
        """Fügt neues Profil hinzu und speichert config.json"""
        if profile_name in self.config.get('profiles', {}):
            logger.error(f"❌ Profil '{profile_name}' existiert bereits")
            return False
        if 'profiles' not in self.config:
            self.config['profiles'] = {}
        self.config['profiles'][profile_name] = profile_config
        # Standort-Zuweisung falls angegeben
        location = profile_config.pop('location', None)
        if location:
            self._assign_profile_to_location(profile_name, location)
        logger.info(f"✅ Profil hinzugefügt: {profile_name}")
        return self.save_config(self.config)

    def update_profile(self, profile_name: str, profile_config: Dict[str, Any]) -> bool:
        """Aktualisiert vorhandenes Profil und speichert config.json"""
        if profile_name not in self.config.get('profiles', {}):
            logger.error(f"❌ Profil '{profile_name}' nicht gefunden")
            return False
        # Standort separat behandeln
        location = profile_config.pop('location', None)
        self.config['profiles'][profile_name].update(profile_config)
        if location is not None:
            self._assign_profile_to_location(profile_name, location)
        logger.info(f"✅ Profil aktualisiert: {profile_name}")
        return self.save_config(self.config)

    def _assign_profile_to_location(self, profile_name: str, location_name: str) -> None:
        """Weist Profil einem Standort zu (entfernt aus anderen Standorten)"""
        locations = self.config.get('locations', {})
        # Aus alten Standorten entfernen
        for loc in locations.values():
            ups_list = loc.get('ups_profiles', [])
            if profile_name in ups_list:
                ups_list.remove(profile_name)
        # Neuem Standort hinzufügen (falls nicht leer)
        if location_name and location_name in locations:
            ups_list = locations[location_name].setdefault('ups_profiles', [])
            if profile_name not in ups_list:
                ups_list.append(profile_name)

    def set_battery_installed(self, profile_name: str, installed: Optional[str]) -> bool:
        """Einbaudatum der Batterie speichern (ISO-Datum); None entfernt die Angabe."""
        profile = self.config.get('profiles', {}).get(profile_name)
        if profile is None:
            return False
        if installed:
            profile['battery_installed'] = installed
        else:
            profile.pop('battery_installed', None)
        return self.save_config(self.config)

    def get_profile_location(self, profile_name: str) -> str:
        """Gibt den Standort eines Profils zurück (oder leer)"""
        for loc_name, loc in self.config.get('locations', {}).items():
            if profile_name in loc.get('ups_profiles', []):
                return loc_name
        return ""

    def delete_profile(self, profile_name: str) -> bool:
        """Löscht Profil (aktives Profil kann nicht gelöscht werden)"""
        if profile_name not in self.config.get('profiles', {}):
            logger.error(f"❌ Profil '{profile_name}' nicht gefunden")
            return False
        if self.config.get('active_profile') == profile_name:
            logger.error(f"❌ Aktives Profil kann nicht gelöscht werden: {profile_name}")
            return False
        del self.config['profiles'][profile_name]
        logger.info(f"🗑️ Profil gelöscht: {profile_name}")
        return self.save_config(self.config)

    def get_web_port(self) -> int:
        """Gibt Web-Server Port"""
        return self.get('web_port', 8080)
    
    def is_demo_mode(self) -> bool:
        """Gibt Demo-Mode Status"""
        return self.get('demo_mode', False)
    
    def to_dict(self) -> Dict[str, Any]:
        """Exportiert gesamte Konfiguration"""
        return self.config.copy()
    
    # ===== Phase 3: Location Support =====
    
    def get_locations(self) -> Dict[str, Any]:
        """
        Gibt alle Standorte zurück
        
        Returns:
            Dictionary mit allen konfigurierten Standorten
        """
        locations = self.config.get('locations', {})
        if not locations:
            logger.warning("⚠️ Keine Standorte konfiguriert")
        return locations
    
    def get_location(self, location_name: str) -> Optional[Dict[str, Any]]:
        """
        Gibt Standort-Konfiguration zurück
        
        Args:
            location_name: Name des Standorts
            
        Returns:
            Standort-Dictionary oder None
        """
        locations = self.get_locations()
        location = locations.get(location_name)
        if not location:
            logger.warning(f"⚠️ Standort '{location_name}' nicht gefunden")
        return location
    
    def list_locations(self) -> list:
        """
        Gibt Liste aller Standort-Namen
        
        Returns:
            List of location names
        """
        return list(self.get_locations().keys())
    
    def assign_ups_to_location(self, location_name: str, ups_profile: str) -> bool:
        """
        Ordnet UPS-Profil einem Standort zu
        
        Args:
            location_name: Name des Standorts
            ups_profile: Name des UPS-Profils
            
        Returns:
            True bei Erfolg
        """
        locations = self.config.get('locations', {})
        
        if location_name not in locations:
            logger.error(f"❌ Standort '{location_name}' nicht gefunden")
            return False
        
        if ups_profile not in self.config.get('profiles', {}):
            logger.error(f"❌ Profil '{ups_profile}' nicht gefunden")
            return False
        
        profiles = locations[location_name].get('ups_profiles', [])
        if ups_profile not in profiles:
            profiles.append(ups_profile)
            locations[location_name]['ups_profiles'] = profiles
            self.config['locations'] = locations
            return self.save_config(self.config)
        
        return False
    
    def get_notifications_config(self) -> Dict[str, Any]:
        """
        Gibt Benachrichtigungs-Konfiguration zurück
        
        Returns:
            Notifications config mit email, telegram, discord settings
        """
        return self.config.get('notifications', {
            "email": {"enabled": False},
            "telegram": {"enabled": False},
            "discord": {"enabled": False}
        })


# Globale Instanz (Singleton-Pattern)
_config_manager: Optional[ConfigManager] = None


def get_config_manager(script_dir: Path) -> ConfigManager:
    """
    Gibt globale ConfigManager Instanz
    
    Args:
        script_dir: Verzeichnis der Hauptanwendung
        
    Returns:
        ConfigManager Instanz
    """
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager(script_dir)
    return _config_manager


# Convenience-Funktionen für Abwärtskompatibilität mit app.py

def load_config() -> Dict[str, Any]:
    """
    Lädt Konfiguration (Wrapper für Abwärtskompatibilität)
    
    Returns:
        Konfigurationsdictionary
    """
    from app import SCRIPT_DIR
    manager = get_config_manager(SCRIPT_DIR)
    return manager.to_dict()


def save_config(config: Dict[str, Any]) -> bool:
    """
    Speichert Konfiguration (Wrapper für Abwärtskompatibilität)
    
    Args:
        config: Zu speicherndes Dictionary
        
    Returns:
        True bei Erfolg
    """
    from app import SCRIPT_DIR
    manager = get_config_manager(SCRIPT_DIR)
    return manager.save_config(config)
