#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Treiber je Schnittstelle: SSH, PowerShell Remoting (WinRM), IPMI, Redfish, HTTP-API, OPC-UA (opcua_driver.py)

Jeder Treiber bietet:
- describe(action): der Befehl, der ausgeführt würde (Protokoll, Testmodus)
- test():           Verbindung und Berechtigung prüfen, OHNE etwas auszulösen
- _execute(action): die Aktion wirklich ausführen

Einstieg ist ausschließlich run_action(). Dort wird der Testmodus geprüft, bevor ein
Treiber eine Verbindung aufbauen darf – es gibt keinen zweiten Ausführungsweg.
"""

import asyncio
import hashlib
import logging
import os
import shutil
import ssl
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from automation.targets import Target, status_matches

logger = logging.getLogger(__name__)

SYSTEM_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"
X509_V_FLAG_NO_CHECK_TIME = 0x200000   # OpenSSL; bei angeheftetem Zertifikat zählt nur der Schlüssel


@dataclass
class Outcome:
    success: bool
    message: str
    command: str = ""
    dry_run: bool = False
    details: Dict[str, Any] = field(default_factory=dict)


class Driver:
    def __init__(self, target: Target):
        self.target = target

    def describe(self, action: str) -> str:
        raise NotImplementedError

    async def test(self) -> Outcome:
        raise NotImplementedError

    async def _execute(self, action: str) -> Outcome:
        raise NotImplementedError


# ------------------------------------------------------------------
# SSH (Ubuntu Server, Arch Linux, …)
# ------------------------------------------------------------------

class SSHDriver(Driver):
    COMMANDS = {"shutdown": "/usr/bin/systemctl --no-block poweroff",
                "reboot": "/usr/bin/systemctl --no-block reboot"}

    def describe(self, action: str) -> str:
        cmd = self.COMMANDS[action]
        if self.target.options.get("sudo", True):
            cmd = "sudo -n " + cmd
        return f"ssh {self.target.username}@{self.target.address}:{self.target.port} '{cmd}'"

    def _command(self, action: str) -> str:
        cmd = self.COMMANDS[action]
        return ("sudo -n " + cmd) if self.target.options.get("sudo", True) else cmd

    async def _connect(self):
        import asyncssh
        t = self.target
        kwargs: Dict[str, Any] = {"host": t.address, "port": t.port, "username": t.username,
                                  "known_hosts": None, "connect_timeout": t.timeout_seconds}
        if t.options.get("key_path"):
            kwargs["client_keys"] = [t.options["key_path"]]
        password = t.password()
        if password:
            kwargs["password"] = password
        conn = await asyncssh.connect(**kwargs)
        fingerprint = conn.get_server_host_key().get_fingerprint("sha256")
        return conn, fingerprint

    def _pinned_ok(self, fingerprint: str) -> bool:
        return bool(self.target.options.get("host_key")) and self.target.options["host_key"] == fingerprint

    async def test(self) -> Outcome:
        conn, fingerprint = await self._connect()
        async with conn:
            details = {"fingerprint": fingerprint, "fingerprint_trusted": self._pinned_ok(fingerprint)}
            if self.target.options.get("host_key") and not details["fingerprint_trusted"]:
                return Outcome(False, "targets.test.fingerprint_mismatch", details=details)
            if self.target.options.get("sudo", True):
                # prüft genau den Befehl, der später ausgeführt wird (sudoers vergleicht Argumente exakt)
                check = await conn.run(f"sudo -n -l {self.COMMANDS['shutdown']}", check=False)
                details["permission"] = check.exit_status == 0
                if check.exit_status != 0:
                    return Outcome(False, "targets.test.ssh_sudo_missing", details=details)
            if not details["fingerprint_trusted"]:
                return Outcome(True, "targets.test.fingerprint_confirm", details=details)
            return Outcome(True, "targets.test.ok", details=details)

    async def _execute(self, action: str) -> Outcome:
        conn, fingerprint = await self._connect()
        async with conn:
            if not self._pinned_ok(fingerprint):
                return Outcome(False, "targets.test.fingerprint_not_trusted", details={"fingerprint": fingerprint})
            result = await conn.run(self._command(action), check=False)
            ok = result.exit_status == 0
            return Outcome(ok, "actions.done" if ok else "actions.failed",
                           details={"exit_code": result.exit_status,
                                    "stderr": (result.stderr or "")[:500]})


# ------------------------------------------------------------------
# PowerShell Remoting / WinRM (Windows)
# ------------------------------------------------------------------

class WinRMDriver(Driver):
    # shutdown.exe kehrt sofort zurück; die Antwort erreicht uns, bevor Windows herunterfährt
    COMMANDS = {"shutdown": ["/s", "/f", "/t", "10", "/c", "Evolved PowerNexus"],
                "reboot": ["/r", "/f", "/t", "10", "/c", "Evolved PowerNexus"]}

    def describe(self, action: str) -> str:
        return (f"WinRM https://{self.target.address}:{self.target.port}/wsman "
                f"shutdown.exe {' '.join(self.COMMANDS[action])}")

    def _session(self):
        import winrm
        t = self.target
        kwargs = {}
        # System-CA-Liste verwenden, damit Zertifikate einer internen CA (update-ca-certificates) gelten
        if os.path.exists(SYSTEM_CA_BUNDLE):
            kwargs["ca_trust_path"] = SYSTEM_CA_BUNDLE
        return winrm.Session(
            f"https://{t.address}:{t.port}/wsman",
            auth=(t.username, t.password() or ""),
            transport=t.options.get("transport", "ntlm"),
            server_cert_validation="validate",
            operation_timeout_sec=t.timeout_seconds,
            read_timeout_sec=t.timeout_seconds + 10,
            **kwargs,
        )

    async def test(self) -> Outcome:
        result = await asyncio.to_thread(lambda: self._session().run_cmd("hostname"))
        ok = result.status_code == 0
        return Outcome(ok, "targets.test.ok" if ok else "targets.test.failed",
                       details={"hostname": result.std_out.decode(errors="replace").strip()})

    async def _execute(self, action: str) -> Outcome:
        result = await asyncio.to_thread(lambda: self._session().run_cmd("shutdown", self.COMMANDS[action]))
        ok = result.status_code == 0
        return Outcome(ok, "actions.done" if ok else "actions.failed",
                       details={"exit_code": result.status_code,
                                "stderr": result.std_err.decode(errors="replace")[:500]})


# ------------------------------------------------------------------
# IPMI over LAN (BMC)
# ------------------------------------------------------------------

class IPMIDriver(Driver):
    COMMANDS = {"shutdown": ["chassis", "power", "soft"],
                "force_off": ["chassis", "power", "off"],
                "power_on": ["chassis", "power", "on"]}

    def _args(self, command) -> list:
        t = self.target
        return ["ipmitool", "-I", "lanplus", "-H", t.address, "-p", str(t.port), "-U", t.username,
                "-E", "-L", "OPERATOR", *command]

    def describe(self, action: str) -> str:
        return " ".join(self._args(self.COMMANDS[action]))

    async def _run(self, command) -> Outcome:
        if not shutil.which("ipmitool"):
            return Outcome(False, "targets.test.ipmitool_missing")
        env = dict(os.environ, IPMI_PASSWORD=self.target.password() or "")
        proc = await asyncio.create_subprocess_exec(*self._args(command), env=env,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        ok = proc.returncode == 0
        return Outcome(ok, "targets.test.ok" if ok else "targets.test.failed",
                       details={"output": stdout.decode(errors="replace").strip()[:200],
                                "stderr": stderr.decode(errors="replace").strip()[:500]})

    async def test(self) -> Outcome:
        return await self._run(["chassis", "power", "status"])

    async def _execute(self, action: str) -> Outcome:
        outcome = await self._run(self.COMMANDS[action])
        outcome.message = "actions.done" if outcome.success else "actions.failed"
        return outcome


# ------------------------------------------------------------------
# TLS für Redfish und HTTP: System-CA oder angeheftetes Zertifikat
# ------------------------------------------------------------------

class CertificateMismatch(Exception):
    def __init__(self, fingerprint: str):
        super().__init__(fingerprint)
        self.fingerprint = fingerprint


def _host(address: str) -> str:
    return f"[{address}]" if ":" in address and not address.startswith("[") else address


def certificate_fingerprint(der: bytes) -> str:
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, 64, 2))


async def fetch_certificate(address: str, port: int, timeout: float) -> bytes:
    """Holt das Serverzertifikat, ohne Daten (Zugangsdaten) zu senden."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    _, writer = await asyncio.wait_for(asyncio.open_connection(address.strip("[]"), port, ssl=ctx), timeout)
    try:
        return writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
    finally:
        writer.close()


async def tls_context(target: Target) -> ssl.SSLContext:
    """Angeheftet: nur genau dieses Zertifikat gilt (Hostname/Ablaufdatum egal, Schlüssel entscheidet).
    Sonst: Prüfung gegen die System-CA-Liste (interne CA über update-ca-certificates)."""
    pin = target.options.get("cert_fingerprint")
    if not pin:
        return ssl.create_default_context()
    der = await fetch_certificate(target.address, target.port, target.timeout_seconds)
    found = certificate_fingerprint(der)
    if found != pin:
        raise CertificateMismatch(found)
    ctx = ssl.create_default_context(cadata=ssl.DER_cert_to_PEM_cert(der))
    ctx.check_hostname = False
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN | X509_V_FLAG_NO_CHECK_TIME
    return ctx


def _cert_verify_failed(exc: BaseException) -> bool:
    while exc is not None:
        if isinstance(exc, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(exc):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


async def _tls_problem(target: Target, exc: BaseException) -> Optional[Outcome]:
    """Übersetzt Zertifikatsfehler in eine Meldung mit Fingerprint zum Übernehmen."""
    if isinstance(exc, CertificateMismatch):
        return Outcome(False, "targets.test.cert_mismatch",
                       details={"fingerprint": exc.fingerprint, "fingerprint_trusted": False})
    if _cert_verify_failed(exc):
        der = await fetch_certificate(target.address, target.port, target.timeout_seconds)
        return Outcome(False, "targets.test.cert_untrusted",
                       details={"fingerprint": certificate_fingerprint(der), "fingerprint_trusted": False})
    return None


# ------------------------------------------------------------------
# Redfish (iDRAC, iLO, OpenBMC/TERRA, Supermicro, …)
# ------------------------------------------------------------------

class RedfishDriver(Driver):
    RESET_TYPES = {"shutdown": "GracefulShutdown", "reboot": "GracefulRestart",
                   "force_off": "ForceOff", "power_on": "On"}

    def describe(self, action: str) -> str:
        t = self.target
        system = t.options.get("system_id") or "<auto>"
        return (f"Redfish POST https://{_host(t.address)}:{t.port}/redfish/v1/Systems/{system}"
                f"/Actions/ComputerSystem.Reset {{\"ResetType\": \"{self.RESET_TYPES[action]}\"}}")

    async def _client(self):
        import httpx
        t = self.target
        return httpx.AsyncClient(base_url=f"https://{_host(t.address)}:{t.port}", verify=await tls_context(t),
                                 auth=(t.username, t.password() or ""), timeout=t.timeout_seconds,
                                 headers={"Accept": "application/json", "OData-Version": "4.0"})

    @staticmethod
    def _check(response, what: str) -> Dict[str, Any]:
        if response.status_code == 401:
            raise RedfishError("targets.test.auth_failed")
        if response.status_code == 403:
            raise RedfishError("targets.test.forbidden")
        if response.status_code >= 400:
            raise RedfishError("targets.test.http_status", status=response.status_code, url=what)
        return response.json()

    async def _system(self, client) -> Dict[str, Any]:
        system_id = self.target.options.get("system_id")
        if not system_id:
            members = self._check(await client.get("/redfish/v1/Systems"), "/redfish/v1/Systems").get("Members", [])
            ids = [m["@odata.id"].rstrip("/").rsplit("/", 1)[-1] for m in members if "@odata.id" in m]
            if len(ids) != 1:
                raise RedfishError("targets.test.redfish_systems", systems=", ".join(ids) or "-")
            system_id = ids[0]
        path = f"/redfish/v1/Systems/{system_id}"
        system = self._check(await client.get(path), path)
        system["_id"] = system_id
        return system

    async def _reset_info(self, client, system: Dict[str, Any]):
        """Ziel-URL und erlaubte Reset-Typen (direkt oder über ActionInfo, z. B. OpenBMC)."""
        action = system.get("Actions", {}).get("#ComputerSystem.Reset", {})
        target = action.get("target") or f"/redfish/v1/Systems/{system['_id']}/Actions/ComputerSystem.Reset"
        allowed = action.get("ResetType@Redfish.AllowableValues")
        if allowed is None and action.get("@Redfish.ActionInfo"):
            info = self._check(await client.get(action["@Redfish.ActionInfo"]), action["@Redfish.ActionInfo"])
            for parameter in info.get("Parameters", []):
                if parameter.get("Name") == "ResetType":
                    allowed = parameter.get("AllowableValues")
        return target, allowed

    async def test(self) -> Outcome:
        try:
            async with await self._client() as client:
                root = self._check(await client.get("/redfish/v1/"), "/redfish/v1/")
                system = await self._system(client)
                _, allowed = await self._reset_info(client, system)
        except RedfishError as e:
            return Outcome(False, e.key, details=e.params)
        except Exception as e:
            problem = await _tls_problem(self.target, e)
            if problem:
                return problem
            raise
        details = {"redfish_version": root.get("RedfishVersion", "?"), "system_id": system["_id"],
                   "model": " ".join(filter(None, [system.get("Manufacturer"), system.get("Model")])) or "-",
                   "power_state": system.get("PowerState", "?"), "reset_types": ", ".join(allowed or ["?"]),
                   "fingerprint_trusted": True}
        missing = [a for a, r in self.RESET_TYPES.items() if allowed is not None and r not in allowed]
        if missing:
            details["unsupported"] = ", ".join(missing)
        return Outcome(True, "targets.test.redfish_ok", details=details)

    async def _execute(self, action: str) -> Outcome:
        reset_type = self.RESET_TYPES[action]
        try:
            async with await self._client() as client:
                system = await self._system(client)
                state = system.get("PowerState")
                if action in ("shutdown", "force_off") and state == "Off" or action == "power_on" and state == "On":
                    return Outcome(True, "actions.already", details={"power_state": state})
                target, allowed = await self._reset_info(client, system)
                if allowed is not None and reset_type not in allowed:
                    return Outcome(False, "actions.reset_type_unsupported",
                                   details={"reset_type": reset_type, "allowed": ", ".join(allowed)})
                response = await client.post(target, json={"ResetType": reset_type})
        except RedfishError as e:
            return Outcome(False, e.key, details=e.params)
        except CertificateMismatch as e:
            return Outcome(False, "targets.test.cert_mismatch", details={"fingerprint": e.fingerprint})
        ok = response.status_code in (200, 202, 204)
        return Outcome(ok, "actions.done" if ok else "actions.failed",
                       details={"status": response.status_code, "power_state": state,
                                "stderr": "" if ok else response.text[:500]})


class RedfishError(Exception):
    def __init__(self, key: str, **params):
        super().__init__(key)
        self.key, self.params = key, params


# ------------------------------------------------------------------
# HTTP-API allgemein (NAS, Proxmox, Home Assistant, Node-RED, …)
# ------------------------------------------------------------------

class HTTPDriver(Driver):
    def _url(self, path: str) -> str:
        t = self.target
        return f"{t.options.get('scheme', 'https')}://{_host(t.address)}:{t.port}{path}"

    def _headers(self) -> Dict[str, str]:
        o = self.target.options
        headers = {name: value for name, value in o.get("headers", [])}
        if o.get("body"):
            headers["Content-Type"] = o.get("content_type") or "application/json"
        secret = self.target.password() or ""
        if o.get("auth") == "bearer":
            headers["Authorization"] = f"Bearer {secret}"
        elif o.get("auth") == "header":
            headers[o.get("auth_header") or "Authorization"] = secret
        return headers

    def describe(self, action: str) -> str:
        o = self.target.options
        auth = {"basic": " [Basic-Auth]", "bearer": " [Bearer-Token]",
                "header": f" [{o.get('auth_header')}: ***]"}.get(o.get("auth"), "")
        body = o.get("body") or ""
        body = f" Body: {body[:200]}{'…' if len(body) > 200 else ''}" if body else ""
        return f"HTTP {o.get('method', 'POST')} {self._url(o.get('path', '/'))}{auth}{body}"

    async def _client(self):
        import httpx
        t = self.target
        https = t.options.get("scheme", "https") == "https"
        auth = (t.username, t.password() or "") if t.options.get("auth") == "basic" else None
        return httpx.AsyncClient(verify=await tls_context(t) if https else False, auth=auth,
                                 timeout=t.timeout_seconds, follow_redirects=False)

    async def test(self) -> Outcome:
        t = self.target
        test_path = t.options.get("test_path")
        try:
            if t.options.get("scheme", "https") == "https" and not test_path:
                # nur TLS-Verbindung prüfen; der konfigurierte Aufruf wird nicht gesendet
                ctx = await tls_context(t)
                _, writer = await asyncio.wait_for(asyncio.open_connection(t.address.strip("[]"), t.port, ssl=ctx),
                                                   t.timeout_seconds)
                writer.close()
                return Outcome(True, "targets.test.http_reachable", details={"fingerprint_trusted": True})
            if not test_path:
                _, writer = await asyncio.wait_for(asyncio.open_connection(t.address.strip("[]"), t.port),
                                                   t.timeout_seconds)
                writer.close()
                return Outcome(True, "targets.test.http_reachable", details={})
            async with await self._client() as client:
                response = await client.get(self._url(test_path), headers=self._headers())
        except Exception as e:
            problem = await _tls_problem(t, e)
            if problem:
                return problem
            raise
        ok = 200 <= response.status_code < 300
        return Outcome(ok, "targets.test.http_ok" if ok else "targets.test.http_status",
                       details={"status": response.status_code, "url": test_path, "fingerprint_trusted": True})

    async def _execute(self, action: str) -> Outcome:
        o = self.target.options
        try:
            async with await self._client() as client:
                response = await client.request(o.get("method", "POST"), self._url(o.get("path", "/")),
                                                headers=self._headers(),
                                                content=(o.get("body") or None) and o["body"].encode("utf-8"))
        except CertificateMismatch as e:
            return Outcome(False, "targets.test.cert_mismatch", details={"fingerprint": e.fingerprint})
        ok = status_matches(o.get("expected_status", "200-299"), response.status_code)
        return Outcome(ok, "actions.done" if ok else "actions.failed",
                       details={"status": response.status_code, "stderr": "" if ok else response.text[:500]})


DRIVERS = {"ssh": SSHDriver, "winrm": WinRMDriver, "ipmi": IPMIDriver,
           "redfish": RedfishDriver, "http": HTTPDriver}


def driver_for(target: Target) -> Driver:
    if target.type == "opcua":
        from automation.opcua_driver import OPCUADriver
        return OPCUADriver(target)
    if target.type in ("local", "nut_fsd"):
        from automation.service_drivers import LocalDriver, NutFsdDriver
        return LocalDriver(target) if target.type == "local" else NutFsdDriver(target)
    return DRIVERS[target.type](target)


async def run_action(target: Target, action: str, dry_run: bool) -> Outcome:
    """Einziger Weg, eine Aktion auszulösen. Im Testmodus wird keine Verbindung aufgebaut."""
    if action not in target.actions:
        return Outcome(False, "actions.unsupported", details={"action": action, "type": target.type})
    driver = driver_for(target)
    command = driver.describe(action)
    if dry_run:
        logger.info(f"🧪 [TESTMODUS] {target.name}: {command}")
        return Outcome(True, "actions.simulated", command=command, dry_run=True)
    if not target.enabled:
        return Outcome(False, "actions.target_disabled", command=command)
    try:
        outcome = await asyncio.wait_for(driver._execute(action), timeout=target.timeout_seconds + 15)
    except asyncio.TimeoutError:
        outcome = Outcome(False, "actions.timeout")
    except Exception as e:
        outcome = Outcome(False, "actions.error", details={"error": f"{type(e).__name__}: {e}"})
    outcome.command = command
    logger.warning(f"⚡ {target.name}: {command} → {'OK' if outcome.success else 'FEHLER'} {outcome.details}")
    return outcome


async def test_target(target: Target) -> Outcome:
    """Verbindung und Berechtigung prüfen – löst nie eine Aktion aus."""
    driver = driver_for(target)
    try:
        return await asyncio.wait_for(driver.test(), timeout=target.timeout_seconds + 15)
    except asyncio.TimeoutError:
        return Outcome(False, "targets.test.timeout")
    except ImportError as e:
        return Outcome(False, "targets.test.package_missing", details={"error": str(e)})
    except Exception as e:
        return Outcome(False, "targets.test.error", details={"error": f"{type(e).__name__}: {e}"})
