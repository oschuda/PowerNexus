#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OPC-UA: Herunterfahren = eine Variable schreiben

Unverschlüsselt oder SignAndEncrypt (bevorzugt Aes256_Sha256_RsaPss, sonst Basic256Sha256), anonym oder
mit Benutzer. Verschlüsselt wird ausschließlich gegen ein Server-Zertifikat aus trusted/ verbunden; die
Bibliothek bricht ab, wenn der Server in der Sitzung ein anderes vorlegt.

Schonender Umgang mit der Steuerung (Lehre aus der XM42): nur bei Aktion und Test verbinden, danach immer
trennen, keine parallelen Sitzungen je Endpunkt, Discovery 5 Minuten zwischenspeichern.
"""

import asyncio
import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from automation import opcua_pki
from automation.drivers import Driver, Outcome, _host
from automation.targets import parse_opcua_value

logger = logging.getLogger(__name__)
logging.getLogger("asyncua").setLevel(logging.ERROR)        # Fehler erscheinen als Meldung; sonst viele Zeilen je Verbindung

DISCOVERY_SECONDS = 300
POLICY_AES256 = "http://opcfoundation.org/UA/SecurityPolicy#Aes256_Sha256_RsaPss"
POLICY_BASIC256 = "http://opcfoundation.org/UA/SecurityPolicy#Basic256Sha256"
PREFERRED_POLICIES = (POLICY_AES256, POLICY_BASIC256)

# Statuscode → Meldung; die Meldung nennt die verantwortliche Seite
STATUS_MESSAGES = {
    "BadSecurityChecksFailed": "targets.test.opcua_client_rejected",
    "BadCertificateUntrusted": "targets.test.opcua_server_not_trusted",
    "BadCertificatePolicyCheckFailed": "targets.test.opcua_cert_policy",
    "BadIdentityTokenRejected": "targets.test.opcua_login",
    "BadIdentityTokenInvalid": "targets.test.opcua_login",
    "BadUserAccessDenied": "targets.test.opcua_login",
    "BadNodeIdUnknown": "targets.test.opcua_node",
    "BadNodeIdInvalid": "targets.test.opcua_node",
    "BadNotWritable": "targets.test.opcua_not_writable",
    "BadTypeMismatch": "targets.test.opcua_type_mismatch",
}


@dataclass
class Endpoint:
    mode: str              # None_ | Sign | SignAndEncrypt
    policy: str
    certificate: bytes


_discovery: Dict[Tuple[str, int], Tuple[float, List[Endpoint]]] = {}
_sessions: Dict[Tuple[str, int], threading.Lock] = {}


class _Exclusive:
    """Eine Sitzung je Endpunkt, auch über Event-Loops hinweg (Oberfläche und Regeln haben je einen)."""

    def __init__(self, key: Tuple[str, int]):
        self.lock = _sessions.setdefault(key, threading.Lock())

    async def __aenter__(self):
        while not self.lock.acquire(blocking=False):
            await asyncio.sleep(0.1)

    async def __aexit__(self, *exc):
        self.lock.release()


def forget_discovery(address: str, port: int) -> None:
    _discovery.pop((address, port), None)


async def discover(address: str, port: int, timeout: float, fresh: bool = False) -> List[Endpoint]:
    from asyncua import Client
    key = (address, port)
    cached = _discovery.get(key)
    if cached and not fresh and time.time() - cached[0] < DISCOVERY_SECONDS:
        return cached[1]
    client = Client(url=f"opc.tcp://{_host(address)}:{port}", timeout=timeout)
    endpoints = [Endpoint(item.SecurityMode.name, item.SecurityPolicyUri, opcua_pki.first_certificate(item.ServerCertificate or b""))
                 for item in await client.connect_and_get_server_endpoints()]
    _discovery[key] = (time.time(), endpoints)
    return endpoints


def choose_secure_endpoint(endpoints: List[Endpoint]) -> Optional[Endpoint]:
    for policy in PREFERRED_POLICIES:
        for endpoint in endpoints:
            if endpoint.mode == "SignAndEncrypt" and endpoint.policy == policy and endpoint.certificate:
                return endpoint
    return None


def _offered(endpoints: List[Endpoint]) -> str:
    return ", ".join(sorted({f"{e.policy.rsplit('#', 1)[-1]}/{e.mode.rstrip('_')}" for e in endpoints})) or "-"


class _Problem(Exception):
    """Vorhersehbarer Abbruch mit fertiger Meldung."""

    def __init__(self, outcome: Outcome):
        super().__init__(outcome.message)
        self.outcome = outcome


class OPCUADriver(Driver):
    @property
    def _url(self) -> str:
        return f"opc.tcp://{_host(self.target.address)}:{self.target.port}"

    @property
    def _encrypted(self) -> bool:
        return self.target.options.get("security") == "sign_and_encrypt"

    def describe(self, action: str) -> str:
        o = self.target.options
        security = "SignAndEncrypt" if self._encrypted else "None"
        login = f"user={self.target.username}" if o.get("auth") == "user" else "anonymous"
        return f"OPC-UA {self._url} [{security}, {login}] Write {o.get('node_id')} = {o.get('value')} ({o.get('data_type')})"

    # ------------------------------------------------------------
    # Verbindung
    # ------------------------------------------------------------

    async def _prepare(self, fresh: bool) -> Tuple[Any, Dict[str, Any]]:
        """Discovery und Vertrauensprüfung; liefert den fertig eingestellten, noch nicht verbundenen Client."""
        from asyncua import Client
        from asyncua.crypto import security_policies
        from asyncua import ua
        t = self.target
        endpoints = await discover(t.address, t.port, t.timeout_seconds, fresh=fresh)
        client = Client(url=self._url, timeout=t.timeout_seconds)
        client.application_uri = opcua_pki.application_uri()
        if t.options.get("auth") == "user":
            client.set_user(t.username)
            client.set_password(t.password() or "")
        details: Dict[str, Any] = {"security_policy": "None"}
        if not self._encrypted:
            if not any(e.mode == "None_" for e in endpoints):
                raise _Problem(Outcome(False, "targets.test.opcua_no_plain", details={"offered": _offered(endpoints)}))
            return client, details

        endpoint = choose_secure_endpoint(endpoints)
        if endpoint is None:
            raise _Problem(Outcome(False, "targets.test.opcua_no_secure", details={"offered": _offered(endpoints)}))
        summary = opcua_pki.describe_certificate(endpoint.certificate)
        details.update(security_policy=endpoint.policy.rsplit("#", 1)[-1], server_certificate=summary,
                       sha1=summary["sha1"], server_trusted=opcua_pki.is_trusted(endpoint.certificate))
        if not details["server_trusted"]:
            opcua_pki.remember_rejected(endpoint.certificate)
            raise _Problem(Outcome(False, "targets.test.opcua_server_not_trusted", details=details))
        await asyncio.to_thread(opcua_pki.ensure_client_certificate)
        policy = (security_policies.SecurityPolicyAes256Sha256RsaPss if endpoint.policy == POLICY_AES256
                  else security_policies.SecurityPolicyBasic256Sha256)
        await client.set_security(policy, str(opcua_pki.certificate_path()), str(opcua_pki.key_path()),
                                  server_certificate=str(opcua_pki.trusted_path(endpoint.certificate)),
                                  mode=ua.MessageSecurityMode.SignAndEncrypt)
        return client, details

    def _translate(self, error: BaseException, details: Dict[str, Any]) -> Optional[Outcome]:
        from asyncua import ua
        if isinstance(error, _Problem):
            return error.outcome
        if isinstance(error, ua.UaStatusCodeError):
            name = type(error).__name__
            key = STATUS_MESSAGES.get(name)
            if key:
                return Outcome(False, key, details={**details, "opcua_status": name})
            return Outcome(False, "targets.test.opcua_status", details={**details, "opcua_status": name})
        if isinstance(error, (OSError, ConnectionError)):             # auch Zeitüberschreitung
            forget_discovery(self.target.address, self.target.port)
            return Outcome(False, "targets.test.opcua_unreachable",
                           details={**details, "error": f"{type(error).__name__}: {error}"[:200]})
        return None

    # ------------------------------------------------------------
    # Verbindung testen: liest nur, schreibt nie
    # ------------------------------------------------------------

    async def test(self) -> Outcome:
        details: Dict[str, Any] = {}
        t = self.target
        async with _Exclusive((t.address, t.port)):
            try:
                client, details = await self._prepare(fresh=True)
                await client.connect()
                try:
                    details.update(await self._inspect(client))
                finally:
                    await client.disconnect()
            except Exception as error:
                outcome = self._translate(error, details)
                if outcome is None:
                    raise
                return outcome
        if not details["writable"]:
            return Outcome(False, "targets.test.opcua_not_writable", details=details)
        if details["actual_type"] != t.options["data_type"]:
            return Outcome(False, "targets.test.opcua_type_mismatch", details=details)
        return Outcome(True, "targets.test.opcua_ok", details=details)

    async def _inspect(self, client) -> Dict[str, Any]:
        from asyncua import ua
        node = client.get_node(self.target.options["node_id"])
        value = await node.read_data_value()
        access = await node.get_user_access_level()
        return {"current_value": str(value.Value.Value), "actual_type": value.Value.VariantType.name,
                "writable": ua.AccessLevel.CurrentWrite in access}

    # ------------------------------------------------------------
    # Aktion
    # ------------------------------------------------------------

    async def _execute(self, action: str) -> Outcome:
        from asyncua import ua
        t, o = self.target, self.target.options
        value = parse_opcua_value(o["data_type"], o["value"])
        variant = ua.Variant(value, getattr(ua.VariantType, o["data_type"]))
        details: Dict[str, Any] = {}
        async with _Exclusive((t.address, t.port)):
            try:
                client, details = await self._prepare(fresh=False)
                await client.connect()
                try:
                    node = client.get_node(o["node_id"])
                    # ohne Zeitstempel: Steuerungen lehnen fremde Zeitstempel oft ab
                    await node.write_value(ua.DataValue(variant))
                    return await self._after_write(node, value, details)
                finally:
                    await self._disconnect_quietly(client)
            except Exception as error:
                outcome = self._translate(error, details)
                if outcome is None:
                    raise
                return outcome

    async def _after_write(self, node, value: Any, details: Dict[str, Any]) -> Outcome:
        if not self.target.options.get("verify_write", True):
            return Outcome(True, "actions.done", details=details)
        try:
            actual = await node.read_value()
        except Exception as e:
            # Die Steuerung darf nach dem Schreiben sofort herunterfahren
            logger.warning(f"⚠️ {self.target.name}: Rücklesen nicht möglich: {e}")
            return Outcome(True, "actions.opcua_written_unverified", details=details)
        same = math.isclose(actual, value, rel_tol=1e-6) if isinstance(value, float) else actual == value
        if not same:
            return Outcome(False, "actions.opcua_verify_failed", details={**details, "actual": str(actual)})
        return Outcome(True, "actions.done", details=details)

    @staticmethod
    async def _disconnect_quietly(client) -> None:
        try:
            await client.disconnect()
        except Exception as e:
            logger.debug(f"OPC-UA: Trennen fehlgeschlagen: {e}")
