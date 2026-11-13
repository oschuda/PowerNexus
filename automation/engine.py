#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Automatisierungs-Engine: Regeln gegen Meldungen auswerten und Schritte ausführen.

Ablauf je Auslösung (Run):
1. Ereignis passt zu Regel (Ereignistyp, Geltungsbereich, Bedingungen, Mehrfachauslösung)
2. Wartezeit – abbrechbar durch Gegenereignis, API oder Pause-Schalter
3. Bedingungen erneut prüfen (Werte können sich geändert haben)
4. Schritte nacheinander, Zielrechner eines Schritts parallel, je Aufruf mit Zeitlimit
5. Jeder Abschnitt wird als Meldung protokolliert, Empfängerlisten der Regel erhalten eine E-Mail

Testmodus: global (Einstellung), je Regel oder bei "Regel testen". Ausgeführt wird
ausschließlich über drivers.run_action(), das im Testmodus keine Verbindung aufbaut.

Wiederanlauf: Runs stehen bis zum Ende in der Datenbank. Eine nach einem Neustart noch offene Wartezeit läuft weiter,
wenn das Ereignis laut erster Abfrage weiter besteht (mindestens RESUME_GRACE_SECONDS, damit noch abgebrochen werden
kann). War ein Run schon in der Ausführung, wird er nur gemeldet und nicht wiederholt: Ob ein Schritt schon gewirkt
hat, ist nicht bekannt.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

import i18n
from automation import drivers
from automation.policies import Policy, PolicyStore
from automation.targets import TargetStore, ValidationError

logger = logging.getLogger(__name__)

MAX_RUNS_KEPT = 50
RESUME_GRACE_SECONDS = 30
RESUME_MAX_AGE_SECONDS = 3600     # länger überfällig: Lage unbekannt, nicht mehr automatisch ausführen


@dataclass
class Run:
    id: str
    policy: str
    ups_id: str
    event: str
    dry_run: bool
    status: str = "waiting"          # waiting | running | completed | failed | cancelled | blocked
    started_at: float = field(default_factory=time.time)
    execute_at: float = 0.0
    finished_at: Optional[float] = None
    reason: str = ""
    results: List[Dict[str, Any]] = field(default_factory=list)
    resumed: bool = False

    def public(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in ("id", "policy", "ups_id", "event", "dry_run", "status",
                                               "started_at", "execute_at", "finished_at", "reason", "results", "resumed")}


class AutomationEngine:
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        targets: TargetStore,
        policies: PolicyStore,
        record: Callable[[str, str, str], None],          # (event_type, ups_id, message)
        data_provider: Callable[[str], Optional[Dict[str, Any]]],
        groups_provider: Callable[[str], List[str]],
        dry_run_provider: Callable[[], bool],
        paused_provider: Callable[[], bool],
        notifier: Optional[Any] = None,                   # on_run_started/on_run_finished (Mailer)
        run_store: Optional[Any] = None,                  # save/delete/list_pending_runs (Database)
    ):
        self.loop = loop
        self.targets = targets
        self.policies = policies
        self._record = record
        self._data = data_provider
        self._groups = groups_provider
        self._global_dry_run = dry_run_provider
        self._paused = paused_provider
        self._notifier = notifier
        self._run_store = run_store
        self._stopping = False
        self._leftover: Dict[str, Dict[str, Any]] = {}  # Wartezeiten vom letzten Lauf, warten auf die erste Abfrage
        self._tasks: Dict[str, asyncio.Task] = {}       # run_id → Task
        self._runs: Dict[str, Run] = {}
        self._last_trigger: Dict[str, float] = {}       # "policy:ups" → Zeitpunkt

    # ------------------------------------------------------------
    # Eingang: Meldungen (aus dem Polling-Thread)
    # ------------------------------------------------------------

    def on_event(self, event) -> None:
        asyncio.run_coroutine_threadsafe(self.handle_event(event.event_type.value, event.ups_id), self.loop)

    async def handle_event(self, event_type: str, ups_id: str) -> List[str]:
        """Wertet ein Ereignis aus; gibt die IDs neu gestarteter Runs zurück."""
        started = []
        for policy in self.policies.all():
            if not policy.enabled:
                continue
            if event_type in policy.cancel_on:
                for run in self._active_runs(policy.name, ups_id):
                    self.cancel(run.id, reason=f"event:{event_type}")
                continue
            if event_type not in policy.events or not self.applies_to(policy, ups_id):
                continue
            ok, _ = self.check_conditions(policy, ups_id)
            if not ok:
                logger.info(f"⊘ Regel '{policy.name}' für {ups_id}: Bedingungen nicht erfüllt")
                continue
            guard_key = f"{policy.name}:{ups_id}"
            last = self._last_trigger.get(guard_key, 0)
            if self._active_runs(policy.name, ups_id) or \
                    time.time() - last < policy.repeat_guard_minutes * 60:
                logger.info(f"⊘ Regel '{policy.name}' für {ups_id}: bereits ausgelöst (Mehrfachschutz)")
                continue
            self._last_trigger[guard_key] = time.time()
            started.append(self.start_run(policy, ups_id, event_type))
        return started

    async def trigger_manually(self, policy_name: str, ups_id: str) -> str:
        """Startet genau diese Regel wie bei ihrem ersten Ereignis; für Tests ohne echten Ausfall.

        Wartezeit, Abbruch, Testmodus und die erneute Prüfung der Bedingungen gelten wie im Ernstfall.
        """
        policy = self.policies.get(policy_name)
        if not policy:
            raise ValidationError("policies.error.not_found", name=policy_name)
        if not policy.enabled:
            raise ValidationError("policies.error.trigger_disabled", name=policy_name)
        if not self.applies_to(policy, ups_id):
            raise ValidationError("policies.error.trigger_scope", name=policy_name, ups=ups_id)
        if self._active_runs(policy.name, ups_id):
            raise ValidationError("policies.error.trigger_active", name=policy_name)
        self._last_trigger[f"{policy.name}:{ups_id}"] = time.time()
        logger.warning(f"🧪 Regel '{policy.name}' für {ups_id} von Hand ausgelöst")
        return self.start_run(policy, ups_id, policy.events[0])

    def applies_to(self, policy: Policy, ups_id: str) -> bool:
        scope = policy.scope
        if scope["type"] == "all":
            return True
        if scope["type"] == "ups":
            return scope.get("id") == ups_id
        return str(scope.get("id")) in [str(g) for g in self._groups(ups_id)]

    def check_conditions(self, policy: Policy, ups_id: str) -> (bool, Dict[str, Any]):
        data = self._data(ups_id) or {}
        values = {"battery_charge_lt": data.get("battery_charge"),
                  "battery_runtime_lt": data.get("runtime_minutes"),
                  "ups_load_gt": data.get("output_load")}
        report = {}
        for key, limit in policy.conditions.items():
            try:
                value = float(values.get(key))
            except (TypeError, ValueError):
                report[key] = {"limit": limit, "value": None, "ok": False}
                continue
            ok = value > limit if key.endswith("_gt") else value < limit
            report[key] = {"limit": limit, "value": value, "ok": ok}
        return all(r["ok"] for r in report.values()), report

    # ------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------

    def _active_runs(self, policy_name: str, ups_id: str) -> List[Run]:
        return [r for r in self._runs.values()
                if r.policy == policy_name and r.ups_id == ups_id and r.status in ("waiting", "running")]

    def runs(self) -> List[Dict[str, Any]]:
        return [r.public() for r in sorted(self._runs.values(), key=lambda r: r.started_at, reverse=True)]

    def start_run(self, policy: Policy, ups_id: str, event_type: str) -> str:
        dry_run = self._global_dry_run() or policy.dry_run
        run = Run(id=uuid.uuid4().hex[:12], policy=policy.name, ups_id=ups_id, event=event_type,
                  dry_run=dry_run, execute_at=time.time() + policy.wait_seconds)
        self._log("POLICY_TRIGGERED", run, i18n.t("runs.msg.triggered", lang=i18n.DEFAULT, policy=policy.name,
                                                   event=event_type, wait=policy.wait_seconds))
        self._launch(policy, run, announce=policy.wait_seconds > 0)
        return run.id

    def _launch(self, policy: Policy, run: Run, announce: bool) -> None:
        self._runs[run.id] = run
        self._trim()
        self._store("save_pending_run", self._row(run))
        if announce:
            self._notify("on_run_started", policy, run)
        task = asyncio.ensure_future(self._execute(policy, run))
        self._tasks[run.id] = task
        task.add_done_callback(lambda _t, rid=run.id: self._tasks.pop(rid, None))

    @staticmethod
    def _row(run: Run) -> Dict[str, Any]:
        return {"id": run.id, "policy": run.policy, "ups_id": run.ups_id, "event": run.event, "dry_run": run.dry_run,
                "status": run.status, "started_at": run.started_at, "execute_at": run.execute_at}

    def _store(self, method: str, *args) -> Any:
        """Der Wiederanlauf ist eine Zugabe: Ein Datenbankfehler darf keine Regel aufhalten."""
        if self._run_store is None:
            return None
        try:
            return getattr(self._run_store, method)(*args)
        except Exception as e:
            logger.error(f"❌ Run konnte nicht gespeichert werden: {e}")
            return None

    # ------------------------------------------------------------
    # Wiederanlauf nach einem Neustart
    # ------------------------------------------------------------

    async def recover(self) -> None:
        """Beim Start: Wartezeiten vormerken, unterbrochene Ausführungen melden."""
        for row in self._store("list_pending_runs") or []:
            if row["status"] == "waiting":
                self._leftover[row["id"]] = row
                continue
            self._finish_leftover(row, "interrupted", "runs.msg.interrupted")

    def has_leftover(self, ups_id: str) -> bool:
        return any(row["ups_id"] == ups_id for row in self._leftover.values())

    def resume_pending(self, ups_id: str, active_events: Set[str], unknown: bool) -> None:
        """Aus dem Polling-Thread, sobald der Zustand der USV nach dem Neustart bekannt ist."""
        asyncio.run_coroutine_threadsafe(self.resume(ups_id, active_events, unknown), self.loop)

    async def resume(self, ups_id: str, active_events: Set[str], unknown: bool) -> None:
        for row in [r for r in self._leftover.values() if r["ups_id"] == ups_id]:
            event_active = row["event"] in active_events
            if unknown and not event_active:
                continue                                 # USV gerade nicht erreichbar: nächste Abfrage entscheidet
            del self._leftover[row["id"]]
            policy = self.policies.get(row["policy"])
            now = time.time()
            if policy is None or not policy.enabled:
                self._finish_leftover(row, "policy_gone", "runs.msg.not_resumed_policy")
            elif not event_active:
                self._finish_leftover(row, "event_ended", "runs.msg.not_resumed_event")
            elif now - row["execute_at"] > RESUME_MAX_AGE_SECONDS:
                self._finish_leftover(row, "too_old", "runs.msg.not_resumed_old")
            elif self._active_runs(policy.name, ups_id):
                self._store("delete_pending_run", row["id"])
            else:
                run = Run(id=row["id"], policy=policy.name, ups_id=ups_id, event=row["event"],
                          dry_run=bool(row["dry_run"]) or self._global_dry_run() or policy.dry_run,
                          started_at=row["started_at"], resumed=True,
                          execute_at=max(row["execute_at"], now + RESUME_GRACE_SECONDS))
                self._last_trigger[f"{policy.name}:{ups_id}"] = now
                self._log("POLICY_TRIGGERED", run, i18n.t("runs.msg.resumed", lang=i18n.DEFAULT, policy=policy.name,
                                                           wait=int(run.execute_at - now)))
                self._launch(policy, run, announce=True)

    def _finish_leftover(self, row: Dict[str, Any], reason: str, message_key: str) -> None:
        run = Run(id=row["id"], policy=row["policy"], ups_id=row["ups_id"], event=row["event"],
                  dry_run=bool(row["dry_run"]), status="cancelled", started_at=row["started_at"],
                  execute_at=row["execute_at"], finished_at=time.time(), reason=reason)
        self._runs[run.id] = run
        self._trim()
        self._store("delete_pending_run", run.id)
        self._log("POLICY_CANCELLED", run, i18n.t(message_key, lang=i18n.DEFAULT, policy=run.policy))
        policy = self.policies.get(run.policy)
        if policy is not None:
            self._notify("on_run_finished", policy, run)

    def shutdown(self) -> None:
        """Dienst wird beendet: Offene Runs bleiben für den Wiederanlauf in der Datenbank."""
        self._stopping = True

    def cancel(self, run_id: str, reason: str = "manual") -> bool:
        run = self._runs.get(run_id)
        task = self._tasks.get(run_id)
        if not run or run.status not in ("waiting", "running") or not task:
            return False
        run.reason = reason
        # Tasks laufen im Event-Loop der Runtime; Abbruch kann aus einem anderen Thread kommen
        self.loop.call_soon_threadsafe(task.cancel)
        return True

    def _trim(self) -> None:
        finished = [r for r in self._runs.values() if r.status not in ("waiting", "running")]
        for run in sorted(finished, key=lambda r: r.started_at)[:max(0, len(self._runs) - MAX_RUNS_KEPT)]:
            self._runs.pop(run.id, None)

    def _log(self, event_type: str, run: Run, message: str) -> None:
        prefix = "[TEST] " if run.dry_run else ""
        try:
            self._record(event_type, run.ups_id, prefix + message)
        except Exception as e:
            logger.error(f"❌ Protokollierung fehlgeschlagen: {e}")

    def _notify(self, method: str, policy: Policy, run: Run) -> None:
        """E-Mail anstoßen; ein Fehler dabei darf die Regel nie beeinflussen."""
        if self._notifier is None:
            return
        try:
            getattr(self._notifier, method)(policy, run)
        except Exception as e:
            logger.error(f"❌ E-Mail-Benachrichtigung: {e}")

    async def _execute(self, policy: Policy, run: Run) -> None:
        try:
            delay = run.execute_at - time.time()
            if delay > 0:
                await asyncio.sleep(delay)
            run.status = "running"
            self._store("save_pending_run", self._row(run))
            if self._paused():
                run.status, run.reason = "blocked", "paused"
                self._log("POLICY_BLOCKED", run, i18n.t("runs.msg.blocked", lang=i18n.DEFAULT, policy=policy.name))
                return
            ok, _ = self.check_conditions(policy, run.ups_id)
            if policy.conditions and not ok:
                run.status, run.reason = "cancelled", "conditions"
                self._log("POLICY_CANCELLED", run, i18n.t("runs.msg.conditions", lang=i18n.DEFAULT,
                                                           policy=policy.name))
                return

            failed = False
            for number, step in enumerate(policy.steps, start=1):
                if step.delay_seconds:
                    await asyncio.sleep(step.delay_seconds)
                results = await asyncio.gather(*[self._run_target(name, step.action, run.dry_run,
                                                                  step.timeout_seconds)
                                                 for name in step.targets])
                for name, outcome in zip(step.targets, results):
                    run.results.append({"step": number, "target": name, "action": step.action,
                                        "success": outcome.success, "message": outcome.message,
                                        "command": outcome.command, "details": outcome.details})
                    self._log("POLICY_ACTION_OK" if outcome.success else "POLICY_ACTION_FAILED", run,
                              i18n.t("runs.msg.action", lang=i18n.DEFAULT, policy=policy.name, target=name,
                                     action=step.action,
                                     result=i18n.t(outcome.message, lang=i18n.DEFAULT, **outcome.details)))
                if not all(o.success for o in results):
                    failed = True
                    if not step.continue_on_error:
                        break
            run.status = "failed" if failed else "completed"
            self._log("POLICY_COMPLETED" if not failed else "POLICY_ACTION_FAILED", run,
                      i18n.t("runs.msg.completed" if not failed else "runs.msg.completed_errors",
                             lang=i18n.DEFAULT, policy=policy.name))
        except asyncio.CancelledError:
            if self._stopping:
                raise
            run.status = "cancelled"
            self._log("POLICY_CANCELLED", run, i18n.t("runs.msg.cancelled", lang=i18n.DEFAULT, policy=policy.name,
                                                       reason=run.reason or "-"))
        except Exception as e:
            run.status, run.reason = "failed", f"{type(e).__name__}: {e}"
            logger.error(f"❌ Regel '{policy.name}': {e}", exc_info=True)
        finally:
            if not self._stopping:
                run.finished_at = time.time()
                self._store("delete_pending_run", run.id)
                self._notify("on_run_finished", policy, run)

    async def _run_target(self, name: str, action: str, dry_run: bool, timeout: int) -> drivers.Outcome:
        target = self.targets.get(name)
        if target is None:
            return drivers.Outcome(False, "actions.unknown_target")
        try:
            return await asyncio.wait_for(drivers.run_action(target, action, dry_run), timeout=timeout)
        except asyncio.TimeoutError:
            return drivers.Outcome(False, "actions.timeout", command=drivers.driver_for(target).describe(action))

    # ------------------------------------------------------------
    # "Regel testen": Simulation + Verbindungsprüfung, nie Ausführung
    # ------------------------------------------------------------

    async def simulate(self, policy: Policy, ups_id: Optional[str], event_type: Optional[str] = None) -> Dict[str, Any]:
        event_type = event_type or (policy.events[0] if policy.events else "")
        conditions_ok, conditions = self.check_conditions(policy, ups_id) if ups_id else (None, {})
        async def check(name: str, action: str) -> Dict[str, Any]:
            target = self.targets.get(name)
            if target is None:
                return {"target": name, "success": False, "message": "actions.unknown_target"}
            planned = await drivers.run_action(target, action, dry_run=True)
            test = await drivers.test_target(target)
            return {"target": name, "type": target.type, "command": planned.command,
                    "success": test.success, "message": test.message, "details": test.details}

        # alle Verbindungstests parallel – ein nicht erreichbarer Rechner hält die anderen nicht auf
        results = await asyncio.gather(*[check(name, step.action)
                                         for step in policy.steps for name in step.targets])
        steps, index = [], 0
        for number, step in enumerate(policy.steps, start=1):
            checks = list(results[index:index + len(step.targets)])
            index += len(step.targets)
            steps.append({"step": number, "action": step.action, "delay_seconds": step.delay_seconds,
                          "targets": checks})
        return {
            "policy": policy.name,
            "event": event_type,
            "ups_id": ups_id,
            "applies": self.applies_to(policy, ups_id) if ups_id else None,
            "conditions_ok": conditions_ok,
            "conditions": conditions,
            "wait_seconds": policy.wait_seconds,
            "notify": list(policy.notify),
            "dry_run_global": self._global_dry_run(),
            "steps": steps,
            "ready": all(t["success"] for s in steps for t in s["targets"]),
        }
