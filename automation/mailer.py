#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
E-Mail-Benachrichtigung: Inhalte, Versand mit Wiederholung, Ablaufwarnung

Auslöser:
- Meldungen, die eine Empfängerliste abonniert hat (unabhängig von Regeln)
- Regeln mit Empfängerlisten: Beginn der Wartezeit und Ergebnis je Zielrechner
- Anmeldedaten für Microsoft 365 laufen ab (ab 30 Tage vorher)

Der Versand läuft als eigene Aufgabe im Event-Loop und kann eine Regel weder blockieren noch abbrechen.
Endgültige Fehlschläge erscheinen als Meldung.
"""

import asyncio
import datetime
import logging
from typing import Any, Callable, Dict, List, Optional, Sequence

import i18n
from automation.mail_settings import MailStore, expiry_date, validate_for_sending
from automation.mail_transport import Mail, MailError, deliver, get_token, token_scope
from automation.targets import ValidationError

logger = logging.getLogger(__name__)

RETRY_DELAYS = (15, 60, 300, 900)     # Sekunden; bei Stromausfall ist das Netz oft kurz gestört
EXPIRY_WARN_DAYS = 30
EXPIRY_CHECK_SECONDS = 3600
SYSTEM_SOURCE = "system"
STATUS_EVENT = {"completed": "mail.subject.completed", "failed": "mail.subject.failed",
                "cancelled": "mail.subject.cancelled", "blocked": "mail.subject.blocked"}


def _one_line(text: Any) -> str:
    return " ".join(str(text).split())[:200]


class Mailer:
    def __init__(self, loop: asyncio.AbstractEventLoop, store: MailStore,
                 record: Callable[[str, str, str], None],
                 ups_info: Callable[[str], Dict[str, Any]],
                 retry_delays: Sequence[float] = RETRY_DELAYS):
        self.loop = loop
        self.store = store
        self._record = record
        self._ups_info = ups_info
        self._retry_delays = retry_delays
        self._tasks = set()

    # ------------------------------------------------------------
    # Eingang
    # ------------------------------------------------------------

    def on_event(self, event) -> None:
        """Aus dem Polling-Thread: Meldung an die Listen, die sie abonniert haben."""
        self.loop.call_soon_threadsafe(self._event_mails, event)

    def _event_mails(self, event) -> None:
        settings = self.store.get()
        if not settings["enabled"]:
            return
        event_type = getattr(event.event_type, "value", event.event_type)
        for item in settings["lists"]:
            if event_type in item["events"]:
                self._queue(item["recipients"], self._compose_event(event, event_type, item["language"], settings))

    def on_run_started(self, policy, run) -> None:
        self._run_mails(policy, run, finished=False)

    def on_run_finished(self, policy, run) -> None:
        self._run_mails(policy, run, finished=True)

    def _run_mails(self, policy, run, finished: bool) -> None:
        settings = self.store.get()
        if not settings["enabled"] or not policy.notify:
            return
        for item in settings["lists"]:
            if item["name"] in policy.notify:
                self._queue(item["recipients"], self._compose_run(policy, run, finished, item["language"], settings))

    def stop(self, timeout: float = 2.0) -> None:
        """Beim Herunterfahren des Dienstes: Ablaufprüfung und offene Wiederholungen beenden."""
        async def cancel_all():
            tasks = list(self._tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        asyncio.run_coroutine_threadsafe(cancel_all(), self.loop).result(timeout)

    # ------------------------------------------------------------
    # Inhalte
    # ------------------------------------------------------------

    def _ups_lines(self, ups_id: str, lang: str) -> List[str]:
        info = self._ups_info(ups_id) or {}
        lines = [i18n.t("mail.body.ups", lang=lang, name=info.get("name") or ups_id)]
        if info.get("location"):
            lines.append(i18n.t("mail.body.location", lang=lang, location=info["location"]))
        for key, label in (("battery_charge", "mail.body.charge"), ("runtime_minutes", "mail.body.runtime"),
                           ("output_load", "mail.body.load"), ("input_voltage", "mail.body.input_voltage")):
            if info.get(key) not in (None, ""):
                lines.append(i18n.t(label, lang=lang, value=info[key]))
        return lines

    def _footer(self, settings: Dict[str, Any], lang: str) -> List[str]:
        url = settings.get("dashboard_url")
        return ["", i18n.t("mail.body.dashboard", lang=lang, url=url)] if url else []

    def _compose_event(self, event, event_type: str, lang: str, settings: Dict[str, Any]) -> Mail:
        severity = getattr(event.severity, "value", event.severity)
        text = i18n.t(f"events.{event_type}", lang=lang)
        subject = i18n.t("mail.subject.prefix", lang=lang,
                         severity=i18n.t(f"severity.{severity}", lang=lang), text=text)
        when = event.timestamp.astimezone().strftime("%d.%m.%Y %H:%M:%S")
        info = self._ups_info(event.ups_id) or {}
        subject += f" – {_one_line(info.get('name') or event.ups_id)}"
        body = [text, i18n.t("mail.body.time", lang=lang, time=when), *self._ups_lines(event.ups_id, lang)]
        return Mail(_one_line(subject), "\n".join(body + self._footer(settings, lang)), [])

    def _compose_run(self, policy, run, finished: bool, lang: str, settings: Dict[str, Any]) -> Mail:
        key = STATUS_EVENT.get(run.status, "mail.subject.completed") if finished else "mail.subject.started"
        text = i18n.t(key, lang=lang, policy=policy.name)
        subject = ("[TEST] " if run.dry_run else "") + i18n.t(
            "mail.subject.prefix", lang=lang,
            severity=i18n.t("severity.WARNING" if not finished or run.status != "completed" else "severity.INFO",
                            lang=lang), text=text)
        body = [text, i18n.t("mail.body.trigger", lang=lang, event=i18n.t(f"events.{run.event}", lang=lang)),
                *self._ups_lines(run.ups_id, lang)]
        if run.dry_run:
            body.insert(0, i18n.t("mail.body.test_mode", lang=lang))
        if not finished:
            when = datetime.datetime.fromtimestamp(run.execute_at).strftime("%H:%M:%S")
            body.append(i18n.t("mail.body.executes_at", lang=lang, time=when))
            for number, step in enumerate(policy.steps, start=1):
                body.append(i18n.t("mail.body.planned", lang=lang, step=number, action=i18n.t(
                    f"actions.{step.action}", lang=lang), targets=", ".join(step.targets)))
        else:
            if run.reason:
                body.append(i18n.t("mail.body.reason", lang=lang, reason=run.reason))
            for result in run.results:
                body.append(i18n.t("mail.body.result", lang=lang, step=result["step"], target=result["target"],
                                   action=i18n.t(f"actions.{result['action']}", lang=lang),
                                   result=("✓ " if result["success"] else "✗ ") +
                                   i18n.t(result["message"], lang=lang, **result.get("details", {}))))
        return Mail(_one_line(subject), "\n".join(body + self._footer(settings, lang)), [])

    # ------------------------------------------------------------
    # Versand mit Wiederholung
    # ------------------------------------------------------------

    def _queue(self, recipients: List[str], mail: Mail) -> None:
        mail.recipients = list(recipients)
        task = asyncio.ensure_future(self._send_with_retry(mail), loop=self.loop)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send_with_retry(self, mail: Mail) -> None:
        error = "?"
        for attempt in range(len(self._retry_delays) + 1):
            if attempt:
                await asyncio.sleep(self._retry_delays[attempt - 1])
            try:
                await deliver(self.store.get(), mail)
                logger.info(f"✉️ E-Mail gesendet: {mail.subject}")
                return
            except MailError as e:
                error = i18n.t(e.key, lang=i18n.DEFAULT, **e.params)
            except Exception as e:
                error = f"{type(e).__name__}: {e}"
            logger.warning(f"⚠️ E-Mail-Versand fehlgeschlagen (Versuch {attempt + 1}): {error}")
        self._log("MAIL_FAILED", i18n.t("mail.msg.failed", lang=i18n.DEFAULT, subject=mail.subject, error=error))

    def _log(self, event_type: str, message: str) -> None:
        try:
            self._record(event_type, SYSTEM_SOURCE, message)
        except Exception as e:
            logger.error(f"❌ Protokollierung fehlgeschlagen: {e}")

    # ------------------------------------------------------------
    # Test: Anmeldung und Versand getrennt prüfen
    # ------------------------------------------------------------

    @staticmethod
    async def test(settings: Dict[str, Any], recipient: str, lang: str) -> List[Dict[str, Any]]:
        """Prüft die übergebenen Einstellungen; gibt je Schritt success/code/params zurück."""
        steps: List[Dict[str, Any]] = []
        try:
            validate_for_sending(settings)
        except ValidationError as e:
            return [{"step": "settings", "success": False, "code": str(e), "params": e.params}]
        mail = Mail(i18n.t("mail.test.subject", lang=lang),
                    i18n.t("mail.test.body", lang=lang, transport=i18n.t(f"mail.transport.{settings['transport']}",
                                                                         lang=lang)),
                    [recipient])
        if settings["transport"] != "smtp":
            try:
                await get_token(settings, token_scope(settings))
                steps.append({"step": "token", "success": True, "code": "mail.test.token_ok", "params": {}})
            except MailError as e:
                steps.append({"step": "token", "success": False, "code": e.key, "params": e.params})
                return steps
        try:
            await deliver(settings, mail)
            steps.append({"step": "send", "success": True, "code": "mail.test.send_ok",
                          "params": {"recipient": recipient}})
        except MailError as e:
            steps.append({"step": "send", "success": False, "code": e.key, "params": e.params})
        return steps

    # ------------------------------------------------------------
    # Ablaufwarnung für Geheimnis/Zertifikat
    # ------------------------------------------------------------

    def start_expiry_watch(self) -> None:
        self.loop.call_soon_threadsafe(self._start_expiry_task)

    def _start_expiry_task(self) -> None:
        task = asyncio.ensure_future(self._expiry_loop(), loop=self.loop)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _expiry_loop(self) -> None:
        while True:
            try:
                self.check_expiry()
            except Exception as e:
                logger.error(f"❌ Ablaufprüfung E-Mail: {e}")
            await asyncio.sleep(EXPIRY_CHECK_SECONDS)

    def check_expiry(self, today: Optional[datetime.date] = None) -> bool:
        """Ab 30 Tage vorher: Meldung + E-Mail an alle Listen, wöchentlich, in der letzten Woche täglich."""
        today = today or datetime.date.today()
        settings = self.store.get()
        expires = expiry_date(settings)
        if not settings["enabled"] or not expires:
            return False
        days = (datetime.date.fromisoformat(expires) - today).days
        if days > EXPIRY_WARN_DAYS:
            return False
        last = settings.get("last_expiry_warning")
        if last and (today - datetime.date.fromisoformat(last)).days < (7 if days > 7 else 1):
            return False
        settings["last_expiry_warning"] = today.isoformat()
        self.store.save(settings)
        key = "mail.msg.expiring" if days >= 0 else "mail.msg.expired"
        self._log("MAIL_CREDENTIAL_EXPIRING", i18n.t(key, lang=i18n.DEFAULT, date=expires, days=days))
        for item in settings["lists"]:
            lang = item["language"]
            text = i18n.t(key, lang=lang, date=expires, days=days)
            subject = i18n.t("mail.subject.prefix", lang=lang, severity=i18n.t("severity.WARNING", lang=lang), text=text)
            self._queue(item["recipients"], Mail(_one_line(subject), text + "\n" + i18n.t("mail.body.renew", lang=lang), []))
        return True
