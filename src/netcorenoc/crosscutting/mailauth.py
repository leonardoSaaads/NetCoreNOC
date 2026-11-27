"""Signing a mailbox in with Microsoft, for SMTP and IMAP (ADR #510).

Outlook.com and Hotmail stopped accepting a password — an app password too — for SMTP and IMAP:
the server answers `535 5.7.139 basic authentication is disabled`, which the console used to report
as a wrong password. Microsoft 365 is following. What they accept is **OAuth 2.0**: the mailbox's
owner signs in at Microsoft once, and the appliance presents a short-lived access token (`XOAUTH2`)
it renews with a refresh token.

The sign-in is Microsoft's **device code** flow, the one made for an appliance with no browser of
its own: the console shows a short code and `microsoft.com/devicelogin`; the owner opens it on any
device, signs in and approves; the appliance asks Microsoft, every few seconds, whether that has
happened. No redirect URL has to reach the appliance, so it works behind NAT and without a public
name. It needs an **application (client) ID** from the owner's own Microsoft Entra registration
(free; "personal Microsoft accounts" allowed, "public client flows" on, the two Outlook scopes
below) — the console walks through it.

**Secrets**: the refresh token is a password by another name. It is kept where a password would be
— sealed — and never returned by the API, logged or put in an audit row. The device code never
leaves the server either: the browser only ever holds the user code it shows.

Blocking I/O (`urllib`): call through `asyncio.to_thread`, never on the event loop.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

AUTHORITY = "https://login.microsoftonline.com"
#: Sending, and reading replies — the two Outlook permissions a registration must hold.
SMTP_SCOPE = "https://outlook.office.com/SMTP.Send"
IMAP_SCOPE = "https://outlook.office.com/IMAP.AccessAsUser.All"
SCOPES = (SMTP_SCOPE, IMAP_SCOPE, "offline_access")
#: Personal accounts (Outlook.com, Hotmail, Live); `organizations` or a tenant id for Microsoft 365.
TENANTS = ("consumers", "organizations", "common")
TIMEOUT_S = 15.0
#: An access token is renewed this long before it expires.
EARLY_S = 120.0


class AuthError(Exception):
    """A sign-in or a renewal that failed, in a sentence the owner can act on."""


def valid_client_id(value: str) -> bool:
    """An Entra application (client) ID: a GUID."""
    parts = value.split("-")
    return [len(p) for p in parts] == [8, 4, 4, 4, 12] and all(
        all(c in "0123456789abcdefABCDEF" for c in p) for p in parts
    )


def valid_tenant(value: str) -> bool:
    return value in TENANTS or valid_client_id(value)


def _post(url: str, form: dict[str, str]) -> dict[str, Any]:
    """POST a form to Microsoft's identity platform; its JSON answer, error bodies included."""
    data = urllib.parse.urlencode(form).encode("ascii")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # nosec B310
            return dict(json.loads(response.read().decode("utf-8")))
    except urllib.error.HTTPError as exc:  # 400 carries the OAuth error object
        try:
            return dict(json.loads(exc.read().decode("utf-8")))
        except (ValueError, UnicodeDecodeError):
            raise AuthError(f"Microsoft answered HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AuthError(
            "the appliance could not reach login.microsoftonline.com: allow it outbound HTTPS"
        ) from exc


def _endpoint(tenant: str, path: str) -> str:
    if not valid_tenant(tenant):
        raise AuthError("the tenant is consumers, organizations, common or a tenant id")
    return f"{AUTHORITY}/{tenant}/oauth2/v2.0/{path}"


@dataclass
class DeviceFlow:
    """One sign-in in progress. Held by the server; the browser sees only `public()`."""

    client_id: str
    tenant: str
    device_code: str
    user_code: str
    verification_uri: str
    expires_at: float
    interval: float
    message: str = ""
    key: str = field(default_factory=lambda: secrets.token_urlsafe(18))
    next_poll: float = 0.0

    def public(self) -> dict[str, Any]:
        return {
            "flow": self.key,
            "user_code": self.user_code,
            "verification_uri": self.verification_uri,
            "expires_at": self.expires_at,
            "interval": self.interval,
            "message": self.message,
        }


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: float
    account: str = ""


def start(client_id: str, tenant: str, now: float | None = None) -> DeviceFlow:
    """Ask Microsoft for a device code. Raises AuthError with Microsoft's own reason."""
    if not valid_client_id(client_id):
        raise AuthError("the application (client) ID is a GUID such as 1a2b3c4d-…")
    answer = _post(
        _endpoint(tenant, "devicecode"),
        {"client_id": client_id, "scope": " ".join((*SCOPES, "openid", "email"))},
    )
    if "device_code" not in answer:
        raise AuthError(_reason(answer))
    now = time.time() if now is None else now
    return DeviceFlow(
        client_id=client_id,
        tenant=tenant,
        device_code=str(answer["device_code"]),
        user_code=str(answer["user_code"]),
        verification_uri=str(answer.get("verification_uri") or "https://microsoft.com/devicelogin"),
        expires_at=now + float(answer.get("expires_in", 900)),
        interval=float(answer.get("interval", 5)),
        message=str(answer.get("message", "")),
    )


def poll(flow: DeviceFlow, now: float | None = None) -> Tokens | None:
    """Has the owner approved? Tokens when yes, None while waiting; AuthError when it ended."""
    now = time.time() if now is None else now
    if now >= flow.expires_at:
        raise AuthError("the code expired before it was used: start the sign-in again")
    if now < flow.next_poll:
        return None
    answer = _post(
        _endpoint(flow.tenant, "token"),
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": flow.client_id,
            "device_code": flow.device_code,
        },
    )
    error = answer.get("error")
    if error in ("authorization_pending", "slow_down"):
        flow.interval += 5.0 if error == "slow_down" else 0.0
        flow.next_poll = now + flow.interval
        return None
    if error:
        raise AuthError(_reason(answer))
    return _tokens(answer, now)


def refresh(client_id: str, tenant: str, refresh_token: str, now: float | None = None) -> Tokens:
    """A new access token (and, as Microsoft rotates them, a new refresh token)."""
    now = time.time() if now is None else now
    answer = _post(
        _endpoint(tenant, "token"),
        {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
            "scope": " ".join(SCOPES),
        },
    )
    if answer.get("error"):
        raise AuthError(_reason(answer))
    tokens = _tokens(answer, now)
    return (
        tokens
        if tokens.refresh_token
        else Tokens(tokens.access_token, refresh_token, tokens.expires_at, tokens.account)
    )


#: Per stored refresh token (by digest): the latest refresh token Microsoft issued for it and the
#: access token in hand. Memory only; `on_rotation` is how a new refresh token reaches storage.
_HELD: dict[str, Tokens] = {}
_LOCK = threading.Lock()
#: Called with (the stored refresh token, the one that replaces it) when Microsoft rotates it.
on_rotation: Callable[[str, str], None] | None = None


def access_token(client_id: str, tenant: str, stored_refresh: str, now: float | None = None) -> str:
    """An access token for this sign-in, renewed when it is about to expire. Blocking."""
    if not stored_refresh:
        raise AuthError("nobody has signed in with Microsoft yet")
    now = time.time() if now is None else now
    key = hashlib.sha256(f"{client_id}|{stored_refresh}".encode()).hexdigest()
    with _LOCK:
        held = _HELD.get(key)
        if held is not None and held.expires_at - EARLY_S > now:
            return held.access_token
        current = held.refresh_token if held is not None else stored_refresh
        tokens = refresh(client_id, tenant, current, now)
        _HELD[key] = tokens
    if tokens.refresh_token != stored_refresh and on_rotation is not None:
        on_rotation(stored_refresh, tokens.refresh_token)
    return tokens.access_token


def latest(client_id: str, stored_refresh: str) -> str | None:
    """The refresh token that replaced `stored_refresh`, when Microsoft rotated it; else None."""
    key = hashlib.sha256(f"{client_id}|{stored_refresh}".encode()).hexdigest()
    with _LOCK:
        held = _HELD.get(key)
    if held is None or not held.refresh_token or held.refresh_token == stored_refresh:
        return None
    return held.refresh_token


def xoauth2(user: str, access_token: str) -> str:
    """The SASL XOAUTH2 initial response, base64: what `AUTH XOAUTH2` sends."""
    raw = f"user={user}\x01auth=Bearer {access_token}\x01\x01"
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


def _tokens(answer: dict[str, Any], now: float) -> Tokens:
    if "access_token" not in answer:
        raise AuthError(_reason(answer))
    return Tokens(
        access_token=str(answer["access_token"]),
        refresh_token=str(answer.get("refresh_token") or ""),
        expires_at=now + float(answer.get("expires_in", 3600)),
        account=_account(str(answer.get("id_token") or "")),
    )


def _account(id_token: str) -> str:
    """The signed-in address from the ID token's claims — shown, never trusted for access."""
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return ""
    return str(claims.get("email") or claims.get("preferred_username") or "")


#: Microsoft's error codes an owner meets, in words they can act on.
_REASONS = {
    "invalid_client": "Microsoft does not know that application (client) ID, or public client "
    "flows are off in its registration (Authentication → Allow public client flows → Yes)",
    "unauthorized_client": "that application is not allowed this sign-in: under Authentication, "
    "allow public client flows, and allow personal Microsoft accounts for Outlook.com",
    "invalid_scope": "the registration lacks the Outlook permissions SMTP.Send and "
    "IMAP.AccessAsUser.All (API permissions → Add → APIs my organization uses → Office 365 "
    "Exchange Online)",
    "authorization_declined": "the sign-in was declined at Microsoft",
    "expired_token": "the code expired before it was used: start the sign-in again",  # nosec B105
    "bad_verification_code": "Microsoft did not recognise the code: start the sign-in again",
    "invalid_grant": "the sign-in is no longer valid (the password changed, or access was "
    "removed): sign in with Microsoft again",
}


def _reason(answer: dict[str, Any]) -> str:
    code = str(answer.get("error") or "")
    if code in _REASONS:
        return _REASONS[code]
    description = str(answer.get("error_description") or "").split("\r\n")[0].split("\n")[0]
    return description[:300] or "Microsoft refused the sign-in"
