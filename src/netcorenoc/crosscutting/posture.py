"""The security-posture warnings: **defined once, here, and classified by where they are defined**.

The bell shows every operator warning, and v0.22.0 lets an operator snooze one (ADR #387). A
security-posture warning — traffic accepted from anywhere, a console served in clear text — is
treated differently from an operational one: its snooze always expires and it stays counted where a
snoozed warning is still visible. So the appliance has to know which warnings are which, and the
answer is not a second list kept in step with the first: the texts are defined here, the runner
emits them from here, and :func:`is_security_posture` asks the same set.
"""

from __future__ import annotations

ALLOWLIST_EMPTY = "Trap allowlist is empty: all sources are accepted. Set an allowlist to enforce."
CLEAR_TEXT_HTTP = (
    "HTTP is not using TLS on a non-loopback bind. Set NETCORENOC_TLS_CERT/KEY or "
    "front NetCoreNOC with a TLS reverse proxy."
)

#: Every security-posture warning this appliance can emit.
SECURITY_POSTURE: frozenset[str] = frozenset({ALLOWLIST_EMPTY, CLEAR_TEXT_HTTP})


def is_security_posture(text: str) -> bool:
    """Is this warning about the appliance's security posture rather than its operation?"""
    return text in SECURITY_POSTURE
