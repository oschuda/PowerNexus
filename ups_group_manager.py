#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UPS Groups Manager (Phase 5.6)
Verwaltet logische Gruppen von USVs für skalierbare Infra-Management und Regel-Ausführung
"""

import logging
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class UPSGroup:
    """UPS Group definition."""
    id: Optional[int]
    name: str
    description: str = ""
    members: List[str] = None  # List of UPS IDs
    
    def __post_init__(self):
        if self.members is None:
            self.members = []


class UPSGroupManager:
    """Manages UPS groups and their membership mapping."""
    
    def __init__(self, db_path: Path):
        """
        Initialize UPS Group Manager
        
        Args:
            db_path: Path to usv_history.db
        """
        self.db_path = db_path
        self._group_cache = {}  # {group_id: UPSGroup}
        self._members_cache = {}  # {ups_id: [group_ids]}
        self._load_groups()
    
    def _load_groups(self):
        """Lädt alle Gruppen aus der Datenbank in den Cache."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Load groups
                cursor = conn.execute(
                    "SELECT id, name, description FROM ups_groups ORDER BY id"
                )
                for row in cursor.fetchall():
                    group_id, name, description = row
                    self._group_cache[group_id] = UPSGroup(
                        id=group_id,
                        name=name,
                        description=description,
                        members=[]
                    )
                
                # Load group memberships (falls eine Mapping-Tabelle existiert)
                # TODO: Implementiere Mapping-Logik wenn nötig
                logger.info(f"✅ {len(self._group_cache)} UPS-Gruppen geladen")
        except Exception as e:
            logger.error(f"❌ Fehler beim Laden von Gruppen: {e}")
    
    def create_group(self, name: str, description: str = "") -> Optional[int]:
        """
        Erstellt eine neue UPS-Gruppe
        
        Args:
            name: Eindeutiger Gruppenname
            description: Optionale Beschreibung
            
        Returns:
            Group ID oder None bei Fehler
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "INSERT INTO ups_groups (name, description) VALUES (?, ?)",
                    (name, description)
                )
                group_id = cursor.lastrowid
                conn.commit()
                
                # Update cache
                self._group_cache[group_id] = UPSGroup(
                    id=group_id,
                    name=name,
                    description=description,
                    members=[]
                )
                
                logger.info(f"✅ UPS-Gruppe '{name}' erstellt (ID: {group_id})")
                return group_id
        except sqlite3.IntegrityError:
            logger.error(f"❌ Gruppenname '{name}' existiert bereits")
            return None
        except Exception as e:
            logger.error(f"❌ Fehler beim Erstellen der Gruppe: {e}")
            return None
    
    def get_group(self, group_id: int) -> Optional[UPSGroup]:
        """Ruft eine Gruppe ab (aus Cache)."""
        return self._group_cache.get(group_id)
    
    def get_group_by_name(self, name: str) -> Optional[UPSGroup]:
        """Ruft eine Gruppe nach Namen ab."""
        for group in self._group_cache.values():
            if group.name == name:
                return group
        return None
    
    def get_all_groups(self) -> List[UPSGroup]:
        """Ruft alle Gruppen ab."""
        return list(self._group_cache.values())
    
    def update_group(self, group_id: int, name: str = None, description: str = None) -> bool:
        """Aktualisiert eine Gruppe."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                if name is not None and description is not None:
                    conn.execute(
                        "UPDATE ups_groups SET name = ?, description = ? WHERE id = ?",
                        (name, description, group_id)
                    )
                elif name is not None:
                    conn.execute(
                        "UPDATE ups_groups SET name = ? WHERE id = ?",
                        (name, group_id)
                    )
                elif description is not None:
                    conn.execute(
                        "UPDATE ups_groups SET description = ? WHERE id = ?",
                        (description, group_id)
                    )
                conn.commit()
                
                # Invalidate cache
                if group_id in self._group_cache:
                    if name:
                        self._group_cache[group_id].name = name
                    if description is not None:
                        self._group_cache[group_id].description = description
                
                logger.info(f"✅ Gruppe {group_id} aktualisiert")
                return True
        except Exception as e:
            logger.error(f"❌ Fehler beim Aktualisieren der Gruppe: {e}")
            return False
    
    def delete_group(self, group_id: int) -> bool:
        """Löscht eine Gruppe."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM ups_groups WHERE id = ?", (group_id,))
                conn.commit()
                
                # Invalidate cache
                self._group_cache.pop(group_id, None)
                
                logger.info(f"✅ Gruppe {group_id} gelöscht")
                return True
        except Exception as e:
            logger.error(f"❌ Fehler beim Löschen der Gruppe: {e}")
            return False
    
    def add_member(self, group_id: int, ups_id: str) -> bool:
        """Fügt eine UPS zu einer Gruppe hinzu."""
        try:
            group = self._group_cache.get(group_id)
            if not group:
                logger.error(f"Gruppe {group_id} nicht gefunden")
                return False
            
            if ups_id not in group.members:
                group.members.append(ups_id)
                # Update members cache
                if ups_id not in self._members_cache:
                    self._members_cache[ups_id] = []
                self._members_cache[ups_id].append(group_id)
                
                logger.info(f"✅ UPS '{ups_id}' zu Gruppe '{group.name}' hinzugefügt")
                return True
            return False
        except Exception as e:
            logger.error(f"❌ Fehler beim Hinzufügen des Members: {e}")
            return False
    
    def remove_member(self, group_id: int, ups_id: str) -> bool:
        """Entfernt eine UPS aus einer Gruppe."""
        try:
            group = self._group_cache.get(group_id)
            if not group:
                logger.error(f"Gruppe {group_id} nicht gefunden")
                return False
            
            if ups_id in group.members:
                group.members.remove(ups_id)
                # Update members cache
                if ups_id in self._members_cache:
                    self._members_cache[ups_id].remove(group_id)
                
                logger.info(f"✅ UPS '{ups_id}' aus Gruppe '{group.name}' entfernt")
                return True
            return False
        except Exception as e:
            logger.error(f"❌ Fehler beim Entfernen des Members: {e}")
            return False
    
    def get_group_members(self, group_id: int) -> List[str]:
        """Ruft alle UPS-IDs einer Gruppe ab."""
        group = self._group_cache.get(group_id)
        return group.members if group else []
    
    def get_ups_groups(self, ups_id: str) -> List[int]:
        """Ruft alle Gruppen einer UPS ab."""
        return self._members_cache.get(ups_id, [])
