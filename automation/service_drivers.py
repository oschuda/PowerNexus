#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Treiber für die optionalen Dienste: Selbst-Shutdown des USV-Servers, FSD an NUT-Clients

Beide prüfen die Freigabe unmittelbar vor der Aktion. Im Testmodus kommt run_action() gar nicht bis hierher.
"""

import asyncio
import logging

from automation import services
from automation.drivers import Driver, Outcome
from automation.nut_server import nut_name

logger = logging.getLogger(__name__)

LOGIN1 = ["org.freedesktop.login1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager"]


class LocalDriver(Driver):
    """Fährt den USV-Server herunter (systemd-logind; Dienstkonto braucht dafür die polkit-Regel)."""

    def describe(self, action: str) -> str:
        return "busctl call " + " ".join(LOGIN1) + " PowerOff b false"

    @staticmethod
    async def _busctl(*args):
        proc = await asyncio.create_subprocess_exec("busctl", "call", *LOGIN1, *args, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        return proc.returncode, stdout.decode(errors="replace").strip(), stderr.decode(errors="replace").strip()

    @staticmethod
    def _allowed() -> bool:
        manager = services.current()
        return bool(manager and manager.self_shutdown_allowed)

    async def test(self) -> Outcome:
        if not self._allowed():
            return Outcome(False, "services.test.self_shutdown_off")
        code, output, error = await self._busctl("CanPowerOff")
        if code == 0 and output == 's "yes"':
            return Outcome(True, "services.test.poweroff_ok")
        return Outcome(False, "services.test.poweroff_denied", details={"answer": output or error})

    async def _execute(self, action: str) -> Outcome:
        if not self._allowed():
            return Outcome(False, "services.test.self_shutdown_off")
        code, _, error = await self._busctl("PowerOff", "b", "false")
        return Outcome(code == 0, "actions.done" if code == 0 else "actions.failed", details={"stderr": error[:500]})


class NutFsdDriver(Driver):
    """Setzt FSD: Die NUT-Clients der USV fahren sich daraufhin selbst herunter."""

    def describe(self, action: str) -> str:
        ups = self.target.options.get("ups")
        return f"NUT FSD {nut_name(ups) if ups else '*'}"

    async def test(self) -> Outcome:
        manager = services.current()
        if not manager or not manager.nut:
            return Outcome(False, "services.test.nut_off")
        status = manager.nut.status()
        return Outcome(True, "services.test.nut_ok", details={"clients": status["clients"],
                                                              "logins": sum(status["logins"].values())})

    async def _execute(self, action: str) -> Outcome:
        manager = services.current()
        if not manager or not manager.nut:
            return Outcome(False, "services.test.nut_off")
        names = manager.nut.set_fsd(self.target.options.get("ups") or "")
        if not names:
            return Outcome(False, "services.test.nut_ups_not_shared")
        return Outcome(True, "actions.done", details={"ups": ", ".join(names)})
