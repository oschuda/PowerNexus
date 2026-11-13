#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NUT-Server (optional): stellt überwachte USVs im Netz per NUT-Protokoll bereit

Rechner mit NUT-Client (upsmon, Rolle „secondary“) fahren sich bei niedrigem Akku oder auf Anweisung (FSD) selbst
herunter. Der Server läuft im Dienst selbst und verwendet dessen Messwerte, daher kein zweiter SNMP-Abfrager und kein
Paket nut-server. Nur lesend: SET, INSTCMD und FSD von Clients werden abgelehnt, FSD setzt nur eine Regel.

Das NUT-Protokoll ist unverschlüsselt (STARTTLS wird nicht angeboten), deshalb nur für die erlaubten Netze.
"""

import asyncio
import ipaddress
import logging
import re
import shlex
import time
from typing import Any, Callable, Dict, List, Optional, Set

from werkzeug.security import check_password_hash

logger = logging.getLogger(__name__)

MAX_CLIENTS = 64
IDLE_SECONDS = 600
FSD_HOLD_SECONDS = 600     # so lange bleibt FSD mindestens stehen; danach, sobald die USV nicht mehr auf Batterie läuft
LOGIN_FAIL_DELAY = 1.0
PROTOCOL_VERSION = "1.3"


def nut_name(profile_key: str) -> str:
    """NUT-Namen enthalten keine Leerzeichen."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", profile_key).strip("_") or "ups"


def _quote(value: Any) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


class NutServer:
    def __init__(self, config: Dict[str, Any], data_provider: Callable[[str], Optional[Dict[str, Any]]],
                 tokens_of: Callable[[Optional[Dict[str, Any]]], str], description_of: Callable[[str], str],
                 host: str = "0.0.0.0"):
        self.config = config
        self.host = host
        self._data = data_provider
        self._tokens_of = tokens_of
        self._description_of = description_of
        self._server: Optional[asyncio.AbstractServer] = None
        self._clients: Set[asyncio.StreamWriter] = set()
        self._logins: Dict[str, Set[asyncio.StreamWriter]] = {}
        self._fsd: Dict[str, float] = {}       # NUT-Name → Zeitpunkt
        self._profiles = {nut_name(key): key for key in config.get("ups", [])}
        self._networks = [ipaddress.ip_network(n, strict=False) for n in config.get("networks", [])]

    # ------------------------------------------------------------
    # Betrieb
    # ------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._server is not None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, int(self.config["port"]), limit=1024)
        logger.warning(f"🔌 NUT-Server aktiv auf Port {self.config['port']}: {sorted(self._profiles)}")

    async def stop(self) -> None:
        server, self._server = self._server, None
        if server:
            server.close()
        for writer in list(self._clients):
            writer.close()
        if server:
            await server.wait_closed()

    def status(self) -> Dict[str, Any]:
        return {"running": self.running, "clients": len(self._clients),
                "logins": {name: len(users) for name, users in self._logins.items() if users},
                "fsd": sorted(name for name in self._profiles if self._fsd_active(name))}

    # ------------------------------------------------------------
    # FSD
    # ------------------------------------------------------------

    def set_fsd(self, profile_key: str = "") -> List[str]:
        """FSD für eine freigegebene USV oder alle; gibt die NUT-Namen zurück."""
        names = [nut_name(profile_key)] if profile_key else list(self._profiles)
        names = [name for name in names if name in self._profiles]
        for name in names:
            self._fsd[name] = time.time()
        logger.warning(f"🔌 NUT: FSD gesetzt für {names}")
        return names

    def clear_fsd(self) -> None:
        self._fsd.clear()

    def _fsd_active(self, name: str) -> bool:
        since = self._fsd.get(name)
        if since is None:
            return False
        if time.time() - since < FSD_HOLD_SECONDS:
            return True
        # Sonst würden Rechner nach Netzrückkehr beim Hochfahren gleich wieder herunterfahren
        if "OB" in self._tokens_of(self._data(self._profiles[name])).split():
            return True
        del self._fsd[name]
        return False

    # ------------------------------------------------------------
    # Werte
    # ------------------------------------------------------------

    def _variables(self, name: str) -> Optional[Dict[str, Any]]:
        """Variablen einer USV; None = keine aktuellen Daten (DATA-STALE)."""
        key = self._profiles[name]
        data = self._data(key)
        tokens = [t for t in self._tokens_of(data).split() if t != "COMMLOST"]
        fsd = self._fsd_active(name)
        if not tokens and not fsd:
            return None
        data = data or {}
        variables: Dict[str, Any] = {"device.type": "ups", "ups.status": " ".join((["FSD"] if fsd else []) + tokens),
                                     "ups.model": self._description_of(key)}
        for var, source, factor in (("battery.charge", "battery_charge", 1), ("battery.runtime", "runtime_minutes", 60),
                                    ("input.voltage", "input_voltage", 1), ("output.voltage", "output_voltage", 1),
                                    ("input.frequency", "input_frequency", 1), ("ups.load", "output_load", 1)):
            try:
                value = float(data[source]) * factor
            except (KeyError, TypeError, ValueError):
                continue
            variables[var] = int(value) if value == int(value) else round(value, 1)
        return variables

    # ------------------------------------------------------------
    # Protokoll
    # ------------------------------------------------------------

    def _allowed(self, writer: asyncio.StreamWriter) -> bool:
        peer = writer.get_extra_info("peername")
        try:
            address = ipaddress.ip_address(peer[0])
        except (TypeError, ValueError):
            return False
        return any(address in network for network in self._networks)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        if not self._allowed(writer) or len(self._clients) >= MAX_CLIENTS:
            logger.warning(f"🔌 NUT: Verbindung von {peer} abgelehnt")
            writer.close()
            return
        self._clients.add(writer)
        session = {"username": None, "password": None}
        try:
            while True:
                line = await asyncio.wait_for(reader.readline(), IDLE_SECONDS)
                if not line:
                    break
                try:
                    args = shlex.split(line.decode("utf-8", "replace"))
                except ValueError:
                    args = ["?"]
                if not args:
                    continue
                reply, done = await self._command(args, session, writer)
                writer.write(reply.encode("utf-8"))
                await writer.drain()
                if done:
                    break
        except (asyncio.TimeoutError, ConnectionError, OSError, ValueError) as e:     # ValueError: Zeile zu lang
            logger.debug(f"NUT {peer}: {e}")
        finally:
            self._clients.discard(writer)
            for users in self._logins.values():
                users.discard(writer)
            writer.close()

    def _check_credentials(self, session: Dict[str, Any]) -> bool:
        stored = self.config.get("password_hash")
        return bool(stored and session["username"] == self.config.get("username")
                    and session["password"] is not None and check_password_hash(stored, session["password"]))

    async def _command(self, args: List[str], session: Dict[str, Any], writer) -> tuple:
        verb = args[0].upper()
        rest = args[1:]
        if verb == "GET":
            return self._get(rest), False
        if verb == "LIST":
            return self._list(rest), False
        if verb in ("USERNAME", "PASSWORD") and len(rest) == 1:
            session[verb.lower()] = rest[0]
            return "OK\n", False
        if verb == "LOGIN" and len(rest) == 1:
            if rest[0] not in self._profiles:
                return "ERR UNKNOWN-UPS\n", False
            if not self._check_credentials(session):
                await asyncio.sleep(LOGIN_FAIL_DELAY)
                return "ERR ACCESS-DENIED\n", False
            self._logins.setdefault(rest[0], set()).add(writer)
            return "OK\n", False
        if verb == "LOGOUT":
            return "OK Goodbye\n", True
        if verb == "VER":
            return "Evolved PowerNexus NUT server\n", False
        if verb == "NETVER":
            return f"{PROTOCOL_VERSION}\n", False
        if verb == "STARTTLS":
            return "ERR FEATURE-NOT-CONFIGURED\n", False
        if verb in ("SET", "INSTCMD", "FSD", "MASTER", "PRIMARY"):
            return "ERR ACCESS-DENIED\n", False
        return "ERR UNKNOWN-COMMAND\n", False

    def _get(self, args: List[str]) -> str:
        kind = args[0].upper() if args else ""
        name = args[1] if len(args) > 1 else ""
        if name not in self._profiles:
            return "ERR UNKNOWN-UPS\n"
        if kind == "UPSDESC":
            return f"UPSDESC {name} {_quote(self._description_of(self._profiles[name]))}\n"
        if kind == "NUMLOGINS":
            return f"NUMLOGINS {name} {len(self._logins.get(name, ()))}\n"
        if kind in ("VAR", "TYPE", "DESC") and len(args) == 3:
            variables = self._variables(name)
            if variables is None:
                return "ERR DATA-STALE\n"
            if args[2] not in variables:
                return "ERR VAR-NOT-SUPPORTED\n"
            if kind == "VAR":
                return f"VAR {name} {args[2]} {_quote(variables[args[2]])}\n"
            if kind == "TYPE":
                return f"TYPE {name} {args[2]} NUMBER\n" if not isinstance(variables[args[2]], str) else \
                    f"TYPE {name} {args[2]} STRING:64\n"
            return f'DESC {name} {args[2]} "Unavailable"\n'
        return "ERR INVALID-ARGUMENT\n"

    def _list(self, args: List[str]) -> str:
        kind = args[0].upper() if args else ""
        if kind == "UPS" and len(args) == 1:
            lines = [f"UPS {name} {_quote(self._description_of(key))}" for name, key in self._profiles.items()]
            return "\n".join(["BEGIN LIST UPS", *lines, "END LIST UPS"]) + "\n"
        name = args[1] if len(args) > 1 else ""
        if kind in ("VAR", "RW", "CMD", "CLIENT") and len(args) == 2:
            if name not in self._profiles:
                return "ERR UNKNOWN-UPS\n"
            lines: List[str] = []
            if kind == "VAR":
                variables = self._variables(name)
                if variables is None:
                    return "ERR DATA-STALE\n"
                lines = [f"VAR {name} {var} {_quote(value)}" for var, value in variables.items()]
            return "\n".join([f"BEGIN LIST {kind} {name}", *lines, f"END LIST {kind} {name}"]) + "\n"
        return "ERR INVALID-ARGUMENT\n"
