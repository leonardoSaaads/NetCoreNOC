"""The brokers' routes (ADR #494): which capability each requires, and its scope posture.

Split from :mod:`netcorenoc.crosscutting.rbac.route_map` at the 400-line guard, as `noc_map.py`
and `account_map.py` were. Nothing here decides anything: `route_map.py` merges these tables into
the one authorization map, and the same tests read them there.

The proxy's door, `POST /api/brokers/ingest`, is not here: no person or account calls it, so it is
one of `route_map.PUBLIC_ROUTES`, authenticated by the broker's own token in the handler.
"""

from __future__ import annotations

from typing import Literal

__all__ = ["BROKER_ROUTE_PERMISSIONS", "BROKER_ROUTE_SCOPE"]

# Every role reads the brokers it can see — the sidebar names them; only an admin adds, changes or
# removes one, issues a proxy's token or moves an element: a broker's rows feed the visibility
# selector `broker:<id>`, and F35 keeps every input of the scope resolver admin-written.
BROKER_ROUTE_PERMISSIONS: dict[tuple[str, str], str] = {
    ("GET", "/api/brokers"): "brokers.read",
    ("GET", "/api/brokers/{broker_id}"): "brokers.read",
    ("POST", "/api/brokers"): "brokers.write",
    ("POST", "/api/brokers/{broker_id}"): "brokers.write",
    ("POST", "/api/brokers/{broker_id}/token"): "brokers.write",
    ("DELETE", "/api/brokers/{broker_id}"): "brokers.write",
    ("POST", "/api/elements/{ne_id}/broker"): "brokers.write",
}

BROKER_ROUTE_SCOPE: dict[tuple[str, str], Literal["scoped", "unscoped", "admin_only"]] = {
    # The list and one broker answer what this principal can see: a person limited to some brokers
    # (`broker:<id>`) is shown those, counted over their own elements (F32); a broker none of whose
    # elements they see is the 404 of one that does not exist (DECISIONS #60).
    ("GET", "/api/brokers"): "scoped",
    ("GET", "/api/brokers/{broker_id}"): "scoped",
    ("POST", "/api/brokers"): "admin_only",
    ("POST", "/api/brokers/{broker_id}"): "admin_only",
    ("POST", "/api/brokers/{broker_id}/token"): "admin_only",
    ("DELETE", "/api/brokers/{broker_id}"): "admin_only",
    ("POST", "/api/elements/{ne_id}/broker"): "admin_only",
}

assert set(BROKER_ROUTE_SCOPE) == set(BROKER_ROUTE_PERMISSIONS), "the two broker tables must agree"
