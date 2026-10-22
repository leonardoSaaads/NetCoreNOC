"""The security-posture warnings: **defined once, here, and classified by where they are defined**.

The bell shows every operator warning, and v0.22.0 lets an operator snooze one (ADR #387). A
security-posture warning — traffic accepted from anywhere, a console served in clear text — is
treated differently from an operational one: its snooze always expires and it stays counted where a
snoozed warning is still visible. So the appliance has to know which warnings are which, and the
answer is not a second list kept in step with the first: the texts are defined here, the runner
emits them from here, and :func:`is_security_posture` asks the same set.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

ALLOWLIST_EMPTY = "Trap allowlist is empty: all sources are accepted. Set an allowlist to enforce."
CLEAR_TEXT_HTTP = (
    "HTTP is not using TLS on a non-loopback bind. Set NETCORENOC_TLS_CERT/KEY or "
    "front NetCoreNOC with a TLS reverse proxy."
)

UNVERIFIED_DATABASE = (
    "The database is on another host and its certificate is not verified, so whoever sits on the "
    "path can read or change every row: add sslmode=verify-full&sslrootcert=<CA file> to "
    "NETCORENOC_DATABASE_URL (docs/configure.md)."
)

#: Every security-posture warning this appliance can emit.
SECURITY_POSTURE: frozenset[str] = frozenset(
    {ALLOWLIST_EMPTY, CLEAR_TEXT_HTTP, UNVERIFIED_DATABASE}
)


def database_warnings(url: str) -> list[str]:
    """ADR #453: a database reached across a network without a verified certificate. Loopback, a
    Unix socket and a single-label host (Compose's `postgres`, on its internal network) are not
    across one; `sslmode=require` encrypts but would talk to any server that answers."""
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    host = (query.get("host") or [parts.hostname or ""])[-1]
    local = host.startswith("/") or host in ("", "localhost", "127.0.0.1", "::1")
    if local or ("." not in host and ":" not in host):
        return []
    verified = (query.get("sslmode") or [""])[-1] in ("verify-full", "verify-ca")
    return [] if verified else [UNVERIFIED_DATABASE]


def is_security_posture(text: str) -> bool:
    """Is this warning about the appliance's security posture rather than its operation?"""
    return text in SECURITY_POSTURE
