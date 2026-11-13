#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Management-APIs (portiert aus routes/events_api.py, policies_api.py,
hosts_api.py und groups_api.py)

/api/events/*      Event-Historie und aktive Events (Login)
/api/ups-groups/*  USV-Gruppen (Admin)
"""

import logging
from typing import Any, Dict, Optional

import i18n
from litestar import Controller, Response, delete, get, post, put
from litestar.di import NamedDependency, Provide
from litestar.params import FromPath, FromQuery, JSONBody

from runtime import Runtime
from web.common import json_response, provide_runtime, requires_login, requires_admin_mgmt

logger = logging.getLogger(__name__)

_RUNTIME = {"runtime": Provide(provide_runtime, sync_to_thread=False)}


# ============================================================
# Events
# ============================================================

class EventsController(Controller):
    path = "/api/events"
    guards = [requires_login]
    dependencies = _RUNTIME

    @get("/history", sync_to_thread=True)
    def history(
        self, runtime: NamedDependency[Runtime], hours: FromQuery[int] = 24, ups_id: FromQuery[Optional[str]] = None,
        event_type: FromQuery[Optional[str]] = None, severity: FromQuery[Optional[str]] = None, limit: FromQuery[int] = 500,
    ) -> Response:
        if not runtime.history_db:
            return json_response({"error": i18n.t("errors.database")}, 503)
        events = runtime.history_db.get_events(hours=hours, ups_id=ups_id, event_type=event_type, limit=limit)
        if severity:
            events = [e for e in events if e.get('severity') == severity]
        return json_response({'success': True, 'count': len(events), 'hours': hours, 'events': events})

    @get("/active", sync_to_thread=False)
    def active(self, runtime: NamedDependency[Runtime]) -> Response:
        if not runtime.state_event_tracker:
            return json_response({"error": "Event Tracker nicht verfügbar"}, 503)
        return json_response({'success': True, 'active_events': runtime.state_event_tracker.get_active_events()})

    @get("/stats", sync_to_thread=True)
    def stats(self, runtime: NamedDependency[Runtime]) -> Response:
        if not runtime.history_db:
            return json_response({"error": i18n.t("errors.database")}, 503)
        stats = {'total_events': 0, 'by_severity': {}, 'by_type': {}, 'by_ups': {}}
        events = runtime.history_db.get_events(hours=24, limit=10000)
        stats['total_events'] = len(events)
        for evt in events:
            for key, field, default in (('by_severity', 'severity', 'UNKNOWN'),
                                        ('by_type', 'event_type', 'UNKNOWN'),
                                        ('by_ups', 'ups_id', 'unknown')):
                value = evt.get(field, default)
                stats[key][value] = stats[key].get(value, 0) + 1
        return json_response({'success': True, 'period_hours': 24, 'stats': stats})


# ============================================================
# UPS-Gruppen
# ============================================================

def _group_dict(group) -> Dict[str, Any]:
    return {'id': group.id, 'name': group.name, 'description': group.description, 'members': group.members}


class GroupsController(Controller):
    path = "/api/ups-groups"
    guards = [requires_admin_mgmt]
    dependencies = _RUNTIME

    @get("/", sync_to_thread=False)
    def list_groups(self, runtime: NamedDependency[Runtime]) -> Response:
        try:
            groups = runtime.group_manager.get_all_groups()
            return json_response({'groups': [
                {'id': g.id, 'name': g.name, 'description': g.description,
                 'member_count': len(g.members), 'members': g.members}
                for g in groups
            ]})
        except Exception as e:
            logger.error(f"❌ Fehler beim Abrufen von Gruppen: {e}")
            return json_response({'error': str(e)}, 500)

    @post("/", status_code=201, sync_to_thread=False)
    def create_group(self, runtime: NamedDependency[Runtime], data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
        data = data or {}
        name = str(data.get('name', '')).strip()
        description = str(data.get('description', '')).strip()
        if not name:
            return json_response({'error': 'Gruppenname erforderlich'}, 400)
        try:
            group_id = runtime.group_manager.create_group(name, description)
            if group_id is None:
                return json_response({'error': f"Gruppenname '{name}' existiert bereits"}, 409)
            logger.info(f"✅ Gruppe '{name}' erstellt (ID: {group_id})")
            return json_response({'id': group_id, 'name': name, 'description': description, 'members': []}, 201)
        except Exception as e:
            logger.error(f"❌ Fehler beim Erstellen der Gruppe: {e}")
            return json_response({'error': str(e)}, 500)

    @get("/{group_id:int}", sync_to_thread=False)
    def get_group(self, runtime: NamedDependency[Runtime], group_id: FromPath[int]) -> Response:
        group = runtime.group_manager.get_group(group_id)
        if not group:
            return json_response({'error': 'Gruppe nicht gefunden'}, 404)
        return json_response(_group_dict(group))

    @put("/{group_id:int}", sync_to_thread=False)
    def update_group(self, runtime: NamedDependency[Runtime], group_id: FromPath[int], data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
        data = data or {}
        gm = runtime.group_manager
        name = str(data.get('name', '')).strip() if 'name' in data else None
        description = str(data.get('description', '')).strip() if 'description' in data else None
        if not gm.get_group(group_id):
            return json_response({'error': 'Gruppe nicht gefunden'}, 404)
        try:
            if not gm.update_group(group_id, name, description):
                return json_response({'error': 'Fehler beim Aktualisieren der Gruppe'}, 500)
            logger.info(f"✅ Gruppe {group_id} aktualisiert")
            return json_response(_group_dict(gm.get_group(group_id)))
        except Exception as e:
            logger.error(f"❌ Fehler beim Aktualisieren der Gruppe: {e}")
            return json_response({'error': str(e)}, 500)

    @delete("/{group_id:int}", status_code=200, sync_to_thread=False)
    def delete_group(self, runtime: NamedDependency[Runtime], group_id: FromPath[int]) -> Response:
        gm = runtime.group_manager
        group = gm.get_group(group_id)
        if not group:
            return json_response({'error': 'Gruppe nicht gefunden'}, 404)
        try:
            if not gm.delete_group(group_id):
                return json_response({'error': i18n.t('errors.group_delete')}, 500)
            logger.info(f"✅ Gruppe '{group.name}' gelöscht")
            return json_response({'message': 'Gruppe gelöscht'})
        except Exception as e:
            logger.error(f"❌ Fehler beim Löschen der Gruppe: {e}")
            return json_response({'error': str(e)}, 500)

    @get("/{group_id:int}/members", sync_to_thread=False)
    def list_members(self, runtime: NamedDependency[Runtime], group_id: FromPath[int]) -> Response:
        group = runtime.group_manager.get_group(group_id)
        if not group:
            return json_response({'error': 'Gruppe nicht gefunden'}, 404)
        return json_response({'group_id': group_id, 'group_name': group.name, 'members': group.members})

    @post("/{group_id:int}/members", status_code=201, sync_to_thread=False)
    def add_member(self, runtime: NamedDependency[Runtime], group_id: FromPath[int], data: JSONBody[Optional[Dict[str, Any]]] = None) -> Response:
        gm = runtime.group_manager
        ups_id = str((data or {}).get('ups_id', '')).strip()
        if not ups_id:
            return json_response({'error': 'UPS-ID erforderlich'}, 400)
        group = gm.get_group(group_id)
        if not group:
            return json_response({'error': 'Gruppe nicht gefunden'}, 404)
        try:
            if not gm.add_member(group_id, ups_id):
                return json_response({'error': i18n.t('errors.group_member')}, 500)
            logger.info(f"✅ UPS '{ups_id}' zu Gruppe '{group.name}' hinzugefügt")
            return json_response({'message': 'Member hinzugefügt', 'group_id': group_id, 'ups_id': ups_id,
                                  'members': gm.get_group(group_id).members}, 201)
        except Exception as e:
            logger.error(f"❌ Fehler beim Hinzufügen des Members: {e}")
            return json_response({'error': str(e)}, 500)

    @delete("/{group_id:int}/members/{ups_id:str}", status_code=200, sync_to_thread=True)
    def remove_member(self, runtime: NamedDependency[Runtime], group_id: FromPath[int], ups_id: FromPath[str]) -> Response:
        gm = runtime.group_manager
        group = gm.get_group(group_id)
        if not group:
            return json_response({'error': 'Gruppe nicht gefunden'}, 404)
        try:
            if not gm.remove_member(group_id, ups_id):
                return json_response({'error': 'Fehler beim Entfernen des Members'}, 500)
            logger.info(f"✅ UPS '{ups_id}' aus Gruppe '{group.name}' entfernt")
            return json_response({'message': 'Member entfernt', 'group_id': group_id, 'ups_id': ups_id,
                                  'members': gm.get_group(group_id).members})
        except Exception as e:
            logger.error(f"❌ Fehler beim Entfernen des Members: {e}")
            return json_response({'error': str(e)}, 500)


route_handlers = [EventsController, GroupsController]
