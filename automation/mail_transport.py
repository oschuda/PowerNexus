#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
E-Mail-Versand: Microsoft 365 (Graph API, SMTP mit OAuth 2.0) und SMTP allgemein

Anmeldung bei Microsoft 365 per App-Registrierung (Client-Credentials). Die Token-Abfrage ist ein eigener
Schritt, damit der Test Anmeldung und Versand getrennt beurteilen kann. Fehler werden als MailError mit
i18n-Schlüssel gemeldet; die Ursache steht dann in der Sprache des Benutzers in der Oberfläche.
"""

import asyncio
import base64
import logging
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any, Dict, List

import httpx

import secret_store

logger = logging.getLogger(__name__)

GRAPH_SCOPE = "https://graph.microsoft.com/.default"
GRAPH_URL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"
EXCHANGE_SCOPE = "https://outlook.office365.com/.default"
OFFICE365_SMTP = ("smtp.office365.com", 587)
TIMEOUT_SECONDS = 30

# Häufige Antworten von Entra ID → verständliche Ursache
AADSTS_CAUSES = {
    "AADSTS7000215": "mail.cause.secret_invalid",
    "AADSTS7000222": "mail.cause.secret_expired",
    "AADSTS700016": "mail.cause.app_not_found",
    "AADSTS90002": "mail.cause.tenant_not_found",
    "AADSTS900023": "mail.cause.tenant_not_found",
    "AADSTS65001": "mail.cause.consent_missing",
    "AADSTS700027": "mail.cause.certificate_invalid",
    "AADSTS53003": "mail.cause.conditional_access",
}


@dataclass
class Mail:
    subject: str
    body: str
    recipients: List[str]


class MailError(Exception):
    def __init__(self, key: str, **params):
        super().__init__(key)
        self.key, self.params = key, params


# ------------------------------------------------------------
# Microsoft 365: Token
# ------------------------------------------------------------

def _credential(m365: Dict[str, Any]) -> Any:
    if m365["auth"] == "secret":
        return secret_store.decrypt(m365["secret"])
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import pkcs12
    password = secret_store.decrypt(m365.get("certificate_password"))
    key, _, _ = pkcs12.load_key_and_certificates(base64.b64decode(secret_store.decrypt(m365["certificate"])),
                                                 password.encode("utf-8") if password else None)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode("ascii")
    return {"private_key": pem, "thumbprint": m365["certificate_thumbprint"]}


def _acquire_token(m365: Dict[str, Any], scope: str) -> Dict[str, Any]:
    import msal
    app = msal.ConfidentialClientApplication(
        m365["client_id"], client_credential=_credential(m365),
        authority=f"https://login.microsoftonline.com/{m365['tenant_id']}", timeout=TIMEOUT_SECONDS)
    return app.acquire_token_for_client(scopes=[scope])


async def get_token(settings: Dict[str, Any], scope: str) -> str:
    try:
        result = await asyncio.wait_for(asyncio.to_thread(_acquire_token, settings["m365"], scope),
                                        TIMEOUT_SECONDS + 5)
    except asyncio.TimeoutError:
        raise MailError("mail.cause.network", error="timeout")
    except ValueError as e:                 # msal: Tenant nicht auffindbar, ungültiger Schlüssel
        raise MailError("mail.cause.tenant_not_found") if "tenant" in str(e).lower() else \
            MailError("mail.cause.other", error=str(e)[:300])
    except ImportError as e:
        raise MailError("mail.cause.package_missing", error=str(e))
    except Exception as e:
        raise MailError("mail.cause.network", error=f"{type(e).__name__}: {e}"[:300])
    if "access_token" in result:
        return result["access_token"]
    description = str(result.get("error_description") or result.get("error") or "")
    for code, key in AADSTS_CAUSES.items():
        if code in description:
            raise MailError(key)
    raise MailError("mail.cause.other", error=description[:300])


# ------------------------------------------------------------
# Versandwege
# ------------------------------------------------------------

async def send_graph(settings: Dict[str, Any], mail: Mail, token: str) -> None:
    payload = {"message": {"subject": mail.subject,
                           "body": {"contentType": "Text", "content": mail.body},
                           "toRecipients": [{"emailAddress": {"address": r}} for r in mail.recipients]},
               "saveToSentItems": False}
    url = GRAPH_URL.format(sender=settings["sender"])
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=payload, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as e:
        raise MailError("mail.cause.network", error=f"{type(e).__name__}: {e}"[:300])
    if response.status_code == 202:
        return
    try:
        code = response.json().get("error", {}).get("code", "")
    except ValueError:
        code = ""
    if response.status_code in (401, 403):
        raise MailError("mail.cause.graph_denied", code=code or response.status_code)
    if response.status_code == 404:
        raise MailError("mail.cause.mailbox_not_found", sender=settings["sender"])
    raise MailError("mail.cause.other", error=f"HTTP {response.status_code} {code}")


def _message(settings: Dict[str, Any], mail: Mail) -> EmailMessage:
    message = EmailMessage()
    message["From"] = formataddr(("Evolved PowerNexus", settings["sender"]))
    message["To"] = ", ".join(mail.recipients)
    message["Subject"] = mail.subject
    message.set_content(mail.body)
    return message


async def _send_smtp(host: str, port: int, security: str, sender_action, message: EmailMessage) -> None:
    import aiosmtplib
    smtp = aiosmtplib.SMTP(hostname=host.strip("[]"), port=port, timeout=TIMEOUT_SECONDS,
                           use_tls=security == "ssl", start_tls=security == "starttls",
                           tls_context=ssl.create_default_context() if security != "none" else None)
    try:
        await smtp.connect()
        await sender_action(smtp)
        await smtp.send_message(message)
    except aiosmtplib.SMTPAuthenticationError as e:
        raise MailError("mail.cause.smtp_auth", error=str(e)[:300])
    except aiosmtplib.SMTPRecipientsRefused as e:
        raise MailError("mail.cause.smtp_recipients", error=str(e)[:300])
    except (aiosmtplib.SMTPConnectError, aiosmtplib.SMTPConnectTimeoutError, aiosmtplib.SMTPTimeoutError,
            aiosmtplib.SMTPServerDisconnected, OSError) as e:
        if isinstance(e.__cause__, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(e):
            raise MailError("mail.cause.smtp_certificate")
        raise MailError("mail.cause.network", error=f"{type(e).__name__}: {e}"[:300])
    except aiosmtplib.SMTPException as e:
        raise MailError("mail.cause.other", error=f"{type(e).__name__}: {e}"[:300])
    finally:
        if smtp.is_connected:
            try:
                await smtp.quit()
            except Exception:
                smtp.close()


async def send_smtp(settings: Dict[str, Any], mail: Mail) -> None:
    cfg = settings["smtp"]

    async def login(smtp):
        if cfg.get("username"):
            await smtp.login(cfg["username"], secret_store.decrypt(cfg.get("password")) or "")

    await _send_smtp(cfg["host"], int(cfg["port"]), cfg["security"], login, _message(settings, mail))


async def send_smtp_oauth(settings: Dict[str, Any], mail: Mail, token: str) -> None:
    import aiosmtplib

    async def login(smtp):
        raw = f"user={settings['sender']}\x01auth=Bearer {token}\x01\x01".encode("utf-8")
        response = await smtp.execute_command(b"AUTH", b"XOAUTH2", base64.b64encode(raw))
        if response.code != 235:
            raise aiosmtplib.SMTPAuthenticationError(response.code, response.message)

    await _send_smtp(*OFFICE365_SMTP, "starttls", login, _message(settings, mail))


def token_scope(settings: Dict[str, Any]) -> str:
    return EXCHANGE_SCOPE if settings["transport"] == "smtp_oauth" else GRAPH_SCOPE


async def deliver(settings: Dict[str, Any], mail: Mail) -> None:
    """Sendet eine E-Mail über den eingestellten Versandweg; wirft MailError."""
    if settings["transport"] == "smtp":
        await send_smtp(settings, mail)
        return
    token = await get_token(settings, token_scope(settings))
    if settings["transport"] == "graph":
        await send_graph(settings, mail, token)
    else:
        await send_smtp_oauth(settings, mail, token)
