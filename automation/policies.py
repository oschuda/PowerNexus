#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Regeln (policies.yaml)

    policies:
      - name: "Lage: Server bei Stromausfall herunterfahren"
        enabled: true
        dry_run: false
        trigger: {events: [POWER_FAILURE], scope: {type: group, id: "Lage"}}
        conditions: {battery_charge_lt: 60}
        wait_seconds: 120
        cancel_on: [POWER_RESTORED]
        steps:
          - {targets: [srv-app], action: shutdown, delay_seconds: 0, timeout_seconds: 180, continue_on_error: true}
        repeat_guard_minutes: 30
        notify: [it-team]        # Empfängerlisten (notifications.yaml)

Das alte Format (trigger_event, cancel_on als Einzelwert, actions/cascade_phases) wird beim
Laden übernommen.
"""

import logging
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml

from automation.targets import TargetStore, ValidationError

logger = logging.getLogger(__name__)

# Ereignisse, die die Meldungs-Erkennung erzeugt und die eine Regel auslösen können
TRIGGER_EVENTS = ["POWER_FAILURE", "BATTERY_LOW", "BATTERY_REPLACE", "OVERLOAD",
                  "BYPASS_ACTIVE", "GENERAL_ALARM", "COMMUNICATION_LOST"]
CANCEL_EVENTS = ["POWER_RESTORED", "BATTERY_LOW_END", "BATTERY_REPLACED", "OVERLOAD_END",
                 "BYPASS_END", "GENERAL_ALARM_END", "COMMUNICATION_RESTORED"]
SCOPE_TYPES = ["all", "ups", "group"]
CONDITIONS = {"battery_charge_lt": (0, 100), "battery_runtime_lt": (0, 600), "ups_load_gt": (0, 100)}


@dataclass
class Step:
    targets: List[str]
    action: str
    delay_seconds: int = 0
    timeout_seconds: int = 180
    continue_on_error: bool = True


@dataclass
class Policy:
    name: str
    enabled: bool = True
    dry_run: bool = False
    trigger: Dict[str, Any] = field(default_factory=lambda: {"events": ["POWER_FAILURE"], "scope": {"type": "all"}})
    conditions: Dict[str, float] = field(default_factory=dict)
    wait_seconds: int = 0
    cancel_on: List[str] = field(default_factory=lambda: ["POWER_RESTORED"])
    steps: List[Step] = field(default_factory=list)
    repeat_guard_minutes: int = 30
    notify: List[str] = field(default_factory=list)

    @property
    def events(self) -> List[str]:
        return list(self.trigger.get("events", []))

    @property
    def scope(self) -> Dict[str, Any]:
        return dict(self.trigger.get("scope") or {"type": "all"})

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _int(value, low: int, high: int, key: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValidationError("policies.error.number", field=key)
    if not low <= number <= high:
        raise ValidationError("policies.error.range", field=key, min=low, max=high)
    return number


def build_policy(data: Dict[str, Any], targets: TargetStore, mail_lists: Sequence[str] = ()) -> Policy:
    """Prüft API-Daten einer Regel und erzeugt das Policy-Objekt."""
    name = str(data.get("name", "")).strip()
    if not name or len(name) > 100:
        raise ValidationError("policies.error.name")

    trigger = data.get("trigger") or {}
    events = [e for e in trigger.get("events", []) if e]
    if not events or any(e not in TRIGGER_EVENTS for e in events):
        raise ValidationError("policies.error.events")
    scope = trigger.get("scope") or {"type": "all"}
    if scope.get("type") not in SCOPE_TYPES:
        raise ValidationError("policies.error.scope")
    if scope["type"] != "all" and not str(scope.get("id") or "").strip():
        raise ValidationError("policies.error.scope_id")
    scope = {"type": scope["type"], **({"id": str(scope["id"]).strip()} if scope["type"] != "all" else {})}

    conditions = {}
    for key, value in (data.get("conditions") or {}).items():
        if value in (None, ""):
            continue
        if key not in CONDITIONS:
            raise ValidationError("policies.error.condition", field=key)
        low, high = CONDITIONS[key]
        conditions[key] = _int(value, low, high, key)

    cancel_on = [e for e in data.get("cancel_on", []) if e]
    if any(e not in CANCEL_EVENTS for e in cancel_on):
        raise ValidationError("policies.error.cancel")

    steps = []
    step_count = len(data.get("steps") or [])
    for index, raw in enumerate(data.get("steps") or [], start=1):
        names = [n for n in raw.get("targets", []) if n]
        if not names:
            raise ValidationError("policies.error.step_targets", step=index)
        action = raw.get("action")
        for target_name in names:
            target = targets.get(target_name)
            if target is None:
                raise ValidationError("policies.error.unknown_target", step=index, name=target_name)
            if action not in target.actions:
                raise ValidationError("policies.error.action_unsupported", step=index, name=target_name,
                                      action=action or "-")
            # Danach läuft nichts mehr: der Selbst-Shutdown steht allein im letzten Schritt
            if target.type == "local" and (index != step_count or len(names) != 1):
                raise ValidationError("policies.error.local_last", name=target_name)
        steps.append(Step(targets=names, action=action,
                          delay_seconds=_int(raw.get("delay_seconds", 0), 0, 3600, "delay_seconds"),
                          timeout_seconds=_int(raw.get("timeout_seconds", 180), 10, 3600, "timeout_seconds"),
                          continue_on_error=bool(raw.get("continue_on_error", True))))
    if not steps:
        raise ValidationError("policies.error.steps")

    notify = [n for n in data.get("notify") or [] if n]
    for list_name in notify:
        if list_name not in mail_lists:
            raise ValidationError("policies.error.unknown_list", name=list_name)

    return Policy(
        name=name,
        enabled=bool(data.get("enabled", True)),
        dry_run=bool(data.get("dry_run", False)),
        trigger={"events": events, "scope": scope},
        conditions=conditions,
        wait_seconds=_int(data.get("wait_seconds", 0), 0, 3600, "wait_seconds"),
        cancel_on=cancel_on,
        steps=steps,
        repeat_guard_minutes=_int(data.get("repeat_guard_minutes", 30), 0, 1440, "repeat_guard_minutes"),
        notify=notify,
    )


def _from_yaml(item: Dict[str, Any]) -> Policy:
    if "trigger_event" in item:                      # altes Format
        steps = []
        for action in item.get("actions", []):
            for phase in action.get("cascade_phases", []) or [action]:
                steps.append(Step(targets=list(phase.get("hosts", [])),
                                  action=str(phase.get("action", "shutdown")).lower(),
                                  delay_seconds=int(phase.get("delay_seconds", 0))))
        cancel = item.get("cancel_on")
        source = item.get("trigger_source_id")
        scope = {"type": "all"} if not source else \
            {"type": "group" if item.get("trigger_source_type") == "GROUP" else "ups", "id": str(source)}
        logger.info(f"🔁 Regel '{item['name']}' aus altem Format übernommen")
        return Policy(name=item["name"], enabled=bool(item.get("enabled", True)),
                      trigger={"events": [item["trigger_event"]], "scope": scope},
                      conditions={k: v for k, v in (item.get("conditions") or {}).items() if k in CONDITIONS},
                      cancel_on=[cancel] if cancel else [], steps=steps)
    item = dict(item)
    item["steps"] = [Step(**s) for s in item.get("steps", [])]
    return Policy(**item)


class PolicyStore:
    """Liest und schreibt policies.yaml (atomar, mit Sicherungskopie)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._policies: Dict[str, Policy] = {}
        self.load()

    def load(self) -> None:
        with self._lock:
            self._policies = {}
            if not self.path.exists():
                return
            with open(self.path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            for item in raw.get("policies", []):
                try:
                    policy = _from_yaml(item)
                    self._policies[policy.name] = policy
                except Exception as e:
                    logger.error(f"❌ Regel in {self.path} ungültig: {item.get('name')}: {e}")

    def save(self) -> None:
        with self._lock:
            data = {"policies": [p.to_dict() for p in self._policies.values()]}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                self.path.replace(self.path.with_suffix(self.path.suffix + ".bak"))
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".policies-", suffix=".yaml")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
            os.replace(tmp, self.path)

    def all(self) -> List[Policy]:
        with self._lock:
            return sorted(self._policies.values(), key=lambda p: p.name.lower())

    def get(self, name: str) -> Optional[Policy]:
        with self._lock:
            return self._policies.get(name)

    def upsert(self, policy: Policy, old_name: Optional[str] = None) -> None:
        with self._lock:
            if old_name and old_name != policy.name:
                if policy.name in self._policies:
                    raise ValidationError("policies.error.exists", name=policy.name)
                self._policies.pop(old_name, None)
            elif not old_name and policy.name in self._policies:
                raise ValidationError("policies.error.exists", name=policy.name)
            self._policies[policy.name] = policy
            self.save()

    def delete(self, name: str) -> bool:
        with self._lock:
            if self._policies.pop(name, None) is None:
                return False
            self.save()
            return True

    def referencing(self, target_name: str) -> List[str]:
        """Regeln, die einen Zielrechner verwenden."""
        with self._lock:
            return [p.name for p in self._policies.values()
                    if any(target_name in s.targets for s in p.steps)]

    def referencing_list(self, list_name: str) -> List[str]:
        """Regeln, die eine Empfängerliste verwenden."""
        with self._lock:
            return [p.name for p in self._policies.values() if list_name in p.notify]
