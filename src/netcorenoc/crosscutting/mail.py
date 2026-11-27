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

from netcorenoc.crosscutting import mailauth
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
    #: ADR #510: how this service signs in — "password" (an app password, usually) or "oauth"
    #: (Microsoft's sign-in; a password is refused). "either" offers both.
    auth: str = "password"
    #: The steps to the password it takes, in the order a person follows them.
    steps: tuple[str, ...] = ()


PROVIDERS: dict[str, Provider] = {
    "gmail": Provider(
        "Gmail / Google Workspace",
        "smtp.gmail.com",
        587,
        "starttls",
        None,
        "Use an app password, not your Google password: Google Account → Security → 2-Step "
        "Verification → App passwords. Sending as your own address works at once.",
        steps=(
            "Turn on 2-Step Verification: myaccount.google.com → Security.",
            "Open myaccount.google.com/apppasswords and create one named NetCoreNOC.",
            "Paste the 16 letters here (the spaces do not matter).",
        ),
    ),
    "outlook": Provider(
        "Outlook.com / Hotmail / Live",
        "smtp-mail.outlook.com",
        587,
        "starttls",
        None,
        "Microsoft no longer accepts a password — an app password neither — for SMTP on "
        'personal accounts: it answers 5.7.139 "basic authentication is disabled". Sign in '
        "with Microsoft.",
        auth="oauth",
    ),
    "microsoft": Provider(
        "Microsoft 365 (work or school)",
        "smtp.office365.com",
        587,
        "starttls",
        None,
        "Sign in with Microsoft, or — while the tenant still allows it — a password: SMTP AUTH "
        "must be on for the mailbox (Microsoft 365 admin center → the user → Mail → "
        "Authenticated SMTP).",
        auth="either",
    ),
    "yahoo": Provider(
        "Yahoo Mail",
        "smtp.mail.yahoo.com",
        465,
        "tls",
        None,
        "Generate an app password: Yahoo Account Security → Generate app password.",
        steps=(
            "Open login.yahoo.com/account/security.",
            "Choose Generate app password, name it NetCoreNOC.",
            "Paste it here.",
        ),
    ),
    "icloud": Provider(
        "iCloud Mail",
        "smtp.mail.me.com",
        587,
        "starttls",
        None,
        "Use an app-specific password from appleid.apple.com → Sign-In and Security.",
        steps=(
            "Sign in at account.apple.com → Sign-In and Security → App-Specific Passwords.",
            "Create one named NetCoreNOC and paste it here.",
            "The sender is your @icloud.com address (or a custom domain set up in iCloud).",
        ),
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


def catalogue() -> dict[str, dict[str, Any]]:
    """Every provider as the console's wizard shows it (ADR #510): where, how it signs in."""
    return {
        key: {
            "label": p.label,
            "host": p.host,
            "port": p.port,
            "security": p.security,
            "username": p.username,
            "note": p.note,
            "auth": p.auth,
            "steps": list(p.steps),
        }
        for key, p in PROVIDERS.items()
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
    #: ADR #510: "password", or "oauth" — Microsoft's sign-in, renewed with `oauth_refresh`.
    auth: str = "password"
    oauth_client_id: str = ""
    oauth_tenant: str = "consumers"
    #: The refresh token Microsoft issued at sign-in: a password by another name, kept as one.
    oauth_refresh: str = ""

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
    def signed_in(self) -> bool:
        """ADR #510: a Microsoft sign-in is held (the refresh token is never shown)."""
        return self.auth == "oauth" and bool(self.oauth_client_id and self.oauth_refresh)

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
    if config.auth not in ("password", "oauth"):
        found.append("sign-in must be a password or Microsoft's")
    elif config.auth == "oauth" and config.enabled:
        if not mailauth.valid_client_id(config.oauth_client_id):
            found.append("Microsoft sign-in needs the application (client) ID of your registration")
        if not mailauth.valid_tenant(config.oauth_tenant):
            found.append("the Microsoft tenant is consumers, organizations, common or a tenant id")
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
    view.pop("oauth_refresh")
    view["signed_in"] = config.signed_in
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


def send(
    config: MailConfig,
    message: EmailMessage,
    *,
    password: str | None = None,
    envelope: list[str] | None = None,
) -> None:
    """Deliver one message. **Blocking**: call through `asyncio.to_thread`. Raises MailError.

    ``password`` is a customer's own SMTP password, opened from the vault for this send (ADR
    #481); without it the appliance's (`effective_password`) logs in. ``envelope`` is every
    recipient, blind copies included, when the headers do not name them all (ADR #500)."""
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
            if config.auth == "oauth":
                # ADR #510: Microsoft's sign-in — a fresh access token, presented as XOAUTH2.
                token = mailauth.access_token(
                    config.oauth_client_id, config.oauth_tenant, config.oauth_refresh
                )
                user = config.username or config.sender
                code, reply = client.docmd("AUTH", "XOAUTH2 " + mailauth.xoauth2(user, token))
                if code != 235:
                    raise smtplib.SMTPAuthenticationError(code, reply)
            elif config.username:
                client.login(
                    config.username, config.effective_password if password is None else password
                )
            client.send_message(message, to_addrs=envelope or None)
    except mailauth.AuthError as exc:
        raise MailError(f"Microsoft sign-in: {exc}") from exc
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError(refusal(config, exc)) from exc
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


def refusal(config: MailConfig, exc: smtplib.SMTPAuthenticationError) -> str:
    """What a refused sign-in means for this server, in a sentence that says what to do (#510)."""
    said = exc.smtp_error.decode("utf-8", "replace") if isinstance(exc.smtp_error, bytes) else ""
    if "5.7.139" in said or "basic authentication is disabled" in said.lower():
        return (
            "Microsoft refused the password: basic authentication is disabled for this mailbox. "
            "Outlook.com and Hotmail accept only Microsoft's sign-in now — choose Outlook.com and "
            "Sign in with Microsoft. (smtp-mail.outlook.com and smtp.office365.com both answer so.)"
        )
    if config.auth == "oauth":
        return (
            "the server refused the Microsoft sign-in: check that the registration holds SMTP.Send "
            "and that SMTP AUTH is allowed for the mailbox, then sign in again"
        )
    hint = {
        "gmail": "Google wants an app password (2-Step Verification on), not the account's own.",
        "yahoo": "Yahoo wants an app password generated in Account Security.",
        "icloud": "iCloud wants an app-specific password from account.apple.com.",
        "microsoft": "Microsoft 365 may refuse passwords for this tenant: sign in with Microsoft.",
        "outlook": "Outlook.com refuses passwords: sign in with Microsoft.",
    }.get(
        config.provider,
        "Gmail, Yahoo and iCloud want an app password rather than the account's own, and "
        "Outlook.com accepts only Microsoft's sign-in.",
    )
    return f"the server refused the username or password ({said[:120] or exc.smtp_code}). {hint}"
