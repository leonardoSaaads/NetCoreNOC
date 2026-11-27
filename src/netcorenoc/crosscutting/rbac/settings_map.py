"""The appliance's settings: the capability each route requires and its scope posture.

Split from `route_map.py` at the 400-line guard when Microsoft sign-in for a mailbox added two
routes (ADR #510), on the seam the table already had: everything here configures the appliance
itself — the SNMP policy (#444), the mailbox it sends from (#445), a Microsoft sign-in for a mailbox
(#510) and the API reference — and names no network element. `route_map.py` folds both tables into
`ROUTE_PERMISSIONS` and `ROUTE_SCOPE`, which stay the one authority; nothing reads these two names
but it.

Every `"unscoped"` scope entry carries its reason on the line above it, because
`tests/api/test_declaration.py` reads them.
"""

from __future__ import annotations

from typing import Literal

SETTINGS_ROUTE_PERMISSIONS: dict[tuple[str, str], str] = {
    # v0.30.0: the SNMP policy (#444), the SMTP server (#445) and the API reference, settings.
    ("GET", "/api/reference"): "tokens.manage",
    ("GET", "/api/snmp"): "config.read",
    ("POST", "/api/snmp"): "config.write",
    ("GET", "/api/email"): "config.read",
    ("POST", "/api/email"): "config.write",
    ("POST", "/api/email/test"): "config.write",
    # ADR #510: a mailbox signed in with Microsoft — whoever may set one up (checked in the handler:
    # the appliance's settings, or a vault custodian for a customer's).
    ("POST", "/api/mail/oauth/start"): "secrets.write",
    ("POST", "/api/mail/oauth/poll"): "secrets.write",
}

SETTINGS_ROUTE_SCOPE: dict[tuple[str, str], Literal["scoped", "unscoped", "admin_only"]] = {
    ("GET", "/api/reference"): "admin_only",
    ("GET", "/api/snmp"): "admin_only",
    ("POST", "/api/snmp"): "admin_only",
    ("GET", "/api/email"): "admin_only",
    ("POST", "/api/email"): "admin_only",
    ("POST", "/api/email/test"): "admin_only",
    # ADR #510: a Microsoft sign-in for a mailbox — an account at Microsoft, held for the person
    # who started it (`signin_flows`); it names no network element and stores nothing.
    ("POST", "/api/mail/oauth/start"): "unscoped",
    # ADR #510: the same sign-in, asked whether it was approved — the caller's own, or a 404.
    ("POST", "/api/mail/oauth/poll"): "unscoped",
}
