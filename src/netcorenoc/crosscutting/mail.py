"""Outgoing email: the SMTP server the appliance sends recovery links through (v0.30.0, ADR #445).

**Plug-and-play for people who have never configured SMTP**: an admin picks their provider —
Gmail, Microsoft 365, Yahoo, iCloud, Zoho, SendGrid, Amazon SES, Mailgun — types an address and an
app password, and the server, port and security come from :data:`PROVIDERS`. **Custom SMTP** exposes
every field, the way Zabbix's media types do: host, port, security (none, STARTTLS, TLS),
authentication, sender, certificate verification and a timeout.

The standard library's `smtplib` and `ssl` do the work, so this adds no dependency. Sending is
blocking I/O and is only ever called through `asyncio.to_thread` — never on the event loop, never on
the trap path.

**The password is stored in the database** (in `meta`, beside the rest of the configuration) because
the appliance has to present it to the server. It is never returned by the API, never logged and
never put in an audit row. `NETCORENOC_SMTP_PASSWORD`, when set, is used instead and nothing is
stored — for an operator who keeps secrets out of the database file and its backups.
"""

from __future__ import annotations

import json
import re
import smtplib
import ssl
from dataclasses import asdict, dataclass, replace
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Any
from urllib.parse import urlsplit

from netcorenoc.crosscutting.settings import read_env

META_KEY = "config.email"
SECURITY_MODES = ("none", "starttls", "tls")
MAX_FIELD = 254
TIMEOUT_RANGE = (3.0, 60.0)

_ADDRESS = re.compile(r"^[^@\s<>\"',;:]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$")
_HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}$|^\[[0-9A-Fa-f:.]+\]$")


@dataclass(frozen=True)
class Provider:
    """A well-known mail service: where its SMTP server is, and the one thing people get wrong."""

    label: str
    host: str
    port: int
    security: str
    #: What goes in the username field when it is not the address itself.
    username: str | None
    note: str


PROVIDERS: dict[str, Provider] = {
    "gmail": Provider(
        "Gmail / Google Workspace",
        "smtp.gmail.com",
        587,
        "starttls",
        None,
        "Use an app password, not your Google password: Google Account → Security → 2-Step "
        "Verification → App passwords. Sending as your own address works at once.",
    ),
    "microsoft": Provider(
        "Microsoft 365 / Outlook.com",
        "smtp.office365.com",
        587,
        "starttls",
        None,
        "SMTP AUTH must be allowed for the mailbox (Microsoft 365 admin center → the user → Mail "
        "→ Authenticated SMTP). With security defaults on, use an app password.",
    ),
    "yahoo": Provider(
        "Yahoo Mail",
        "smtp.mail.yahoo.com",
        465,
        "tls",
        None,
        "Generate an app password: Yahoo Account Security → Generate app password.",
    ),
    "icloud": Provider(
        "iCloud Mail",
        "smtp.mail.me.com",
        587,
        "starttls",
        None,
        "Use an app-specific password from appleid.apple.com → Sign-In and Security.",
    ),
    "zoho": Provider(
        "Zoho Mail",
        "smtp.zoho.com",
        465,
        "tls",
        None,
        "Use an application-specific password if two-factor sign-in is on. EU accounts use "
        "smtp.zoho.eu.",
    ),
    "sendgrid": Provider(
        "SendGrid",
        "smtp.sendgrid.net",
        587,
        "starttls",
        "apikey",
        "The username is the word apikey and the password is an API key with Mail Send access. "
        "The sender must be a verified sender identity.",
    ),
    "ses": Provider(
        "Amazon SES",
        "email-smtp.us-east-1.amazonaws.com",
        587,
        "starttls",
        "",
        "Change the region in the server name to yours, and use the SMTP credentials created in "
        "the SES console (not your AWS keys). The sender must be verified.",
    ),
    "mailgun": Provider(
        "Mailgun",
        "smtp.mailgun.org",
        587,
        "starttls",
        "",
        "Use the SMTP login and password of your sending domain (Sending → Domain settings). EU "
        "domains use smtp.eu.mailgun.org.",
    ),
    "custom": Provider("Custom SMTP server", "", 587, "starttls", "", "Every field is yours."),
}


@dataclass(frozen=True)
class MailConfig:
    """Everything needed to send one message. Immutable; the routes replace it whole."""

    enabled: bool = False
    provider: str = "custom"
    host: str = ""
    port: int = 587
    security: str = "starttls"
    username: str = ""
    password: str = ""
    sender: str = ""
    sender_name: str = "NetCoreNOC"
    verify_tls: bool = True
    timeout_s: float = 15.0
    #: The console's address as people reach it, for the link in a recovery email. Never taken
    #: from a request's Host header — that would let anyone point a reset link at their own site.
    public_url: str = ""
    recovery: bool = True

    @property
    def effective_password(self) -> str:
        return read_env("SMTP_PASSWORD") or self.password

    @property
    def has_password(self) -> bool:
        return bool(read_env("SMTP_PASSWORD") or self.password)

    @property
    def password_source(self) -> str:
        if read_env("SMTP_PASSWORD"):
            return "environment"
        return "database" if self.password else "none"

    @property
    def can_recover(self) -> bool:
        """Whether the sign-in screen may offer recovery: configured, switched on, and linkable."""
        linkable = bool(self.host and self.sender and self.public_url)
        return self.enabled and self.recovery and linkable


class MailError(Exception):
    """A send that failed, in a sentence an admin can act on — never a stack trace."""


def valid_address(text: str) -> bool:
    return len(text) <= MAX_FIELD and bool(_ADDRESS.match(text))


def problems(config: MailConfig) -> list[str]:
    """What stops this configuration from working, checked before it is saved."""
    found: list[str] = []
    if config.provider not in PROVIDERS:
        found.append(f"unknown provider {config.provider!r}")
    if config.security not in SECURITY_MODES:
        found.append(f"security must be one of {', '.join(SECURITY_MODES)}")
    if not 1 <= config.port <= 65535:
        found.append("the port must be between 1 and 65535")
    if not TIMEOUT_RANGE[0] <= config.timeout_s <= TIMEOUT_RANGE[1]:
        found.append(f"the timeout must be {TIMEOUT_RANGE[0]:g} to {TIMEOUT_RANGE[1]:g} seconds")
    if config.enabled:
        if not _HOST.match(config.host):
            found.append("the SMTP server must be a host name or an address")
        if not valid_address(config.sender):
            found.append("the sender must be an email address, for example noc@example.com")
    if any(ch in value for value in (config.sender_name, config.username) for ch in "\r\n"):
        found.append("names may not contain line breaks")
    if config.public_url:
        parts = urlsplit(config.public_url)
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.query:
            found.append("the console address must look like https://noc.example.com")
    return found


def to_document(config: MailConfig) -> str:
    return json.dumps({"version": 1, **asdict(config)}, sort_keys=True, separators=(",", ":"))


def from_document(text: str | None) -> MailConfig:
    """The stored configuration, or the default. Unknown keys are ignored; a bad file is off."""
    if not text:
        return MailConfig()
    try:
        raw = json.loads(text)
        fields = {k: raw[k] for k in MailConfig.__dataclass_fields__ if k in raw}
        config = MailConfig(**fields)
    except (ValueError, TypeError):
        return MailConfig()
    return config if not problems(config) else replace(config, enabled=False)


def public_view(config: MailConfig) -> dict[str, Any]:
    """What the API returns: everything but the password, and where the password comes from."""
    view = asdict(config)
    view.pop("password")
    view["password_set"] = config.has_password
    view["password_source"] = config.password_source
    view["recovery_available"] = config.can_recover
    return view


def compose(config: MailConfig, to: str, subject: str, body: str) -> EmailMessage:
    """A plain-text message. Headers go through `email`'s policy, which refuses injection."""
    message = EmailMessage()
    message["From"] = formataddr((config.sender_name or "NetCoreNOC", config.sender))
    message["To"] = to
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=config.sender.rsplit("@", 1)[-1])
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(body)
    return message


def _context(config: MailConfig) -> ssl.SSLContext:
    context = ssl.create_default_context()
    if not config.verify_tls:
        # An admin's explicit choice, for a relay with a self-signed certificate (Zabbix offers the
        # same switch). The console states the risk beside it; the default verifies.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # nosec B501 - opt-in, stated on the screen
    return context


def send(config: MailConfig, message: EmailMessage) -> None:
    """Deliver one message. **Blocking**: call through `asyncio.to_thread`. Raises MailError."""
    if not config.host:
        raise MailError("no SMTP server is configured")
    try:
        if config.security == "tls":
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                config.host, config.port, timeout=config.timeout_s, context=_context(config)
            )
        else:
            client = smtplib.SMTP(config.host, config.port, timeout=config.timeout_s)
        with client:
            client.ehlo()
            if config.security == "starttls":
                client.starttls(context=_context(config))
                client.ehlo()
            if config.username:
                client.login(config.username, config.effective_password)
            client.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError(
            "the server refused the username or password. Gmail, Microsoft 365, Yahoo and iCloud "
            "need an app password rather than the account's own."
        ) from exc
    except smtplib.SMTPNotSupportedError as exc:
        raise MailError(f"the server does not support what was asked: {exc}") from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise MailError("the server refused the recipient address") from exc
    except smtplib.SMTPSenderRefused as exc:
        raise MailError(
            "the server refused the sender address; most providers only send as the signed-in "
            "account or a verified sender"
        ) from exc
    except ssl.SSLError as exc:
        raise MailError(
            f"the TLS handshake failed ({exc.reason or exc}). Check the security mode and port: "
            "465 is TLS, 587 is STARTTLS."
        ) from exc
    except TimeoutError as exc:
        raise MailError(f"no answer from {config.host}:{config.port} within the timeout") from exc
    except (OSError, smtplib.SMTPException) as exc:
        raise MailError(f"could not send through {config.host}:{config.port}: {exc}") from exc
