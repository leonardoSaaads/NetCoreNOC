"""Field-level authorization: one role-keyed serializer for response shaping (F7, §A.3).

Route authorization (`rbac.py`) decides *which endpoints* a role may call. This module decides
*which fields within a response* a role may see — deny-by-default extended from routes to fields.
It is the single place that shapes response bodies by role, never scattered ``if role ==`` checks.

A viewer running the read-only operations view does not need the raw source IP of every monitored
element, the community-grouping tag, or the source IP a session connected from; those are sensitive
network detail that a lower role should not receive merely because the endpoint is viewer-readable.
Following the stricter-wins rule, such fields are **coarsened** (an IP → its network) or **dropped**
for roles below a declared minimum. The engine, the store, and the audit log always keep full
fidelity — shaping is presentation-only.

New endpoints that return any protected field MUST pass their body through :func:`shape` (or a
helper here); a field with no rule is emitted unchanged, so add a rule when you add such a field.

The *other* axis — which **rows** a principal may see — is :mod:`netcorenoc.shaping.scope`.
"""

from __future__ import annotations

import ipaddress
from typing import Any, overload

from netcorenoc.crosscutting.rbac import ROLE_RANK
from netcorenoc.crosscutting.shaping.naming import coarsen_situation_name

# Minimum role that may see each protected field in full; below it the field is coarsened
# (transform) or dropped (transform is None). Keys are matched by field name anywhere in a
# (possibly nested) response body.
_COARSEN = "coarsen"
_DROP = "drop"

#: Fields whose value CONTAINS addresses rather than being one. They take the same rule and a
#: different coarsener, and the set is named here so a reader of `FIELD_RULES` can see at a glance
#: which entries are not a bare address.
_COMPOSITE = frozenset({"derived_name"})
FIELD_RULES: dict[str, tuple[str, str]] = {
    "ip": ("editor", _COARSEN),  # device / NE address on graph + entity views
    "device_ip": ("editor", _COARSEN),  # alarm rows in a situation detail
    "device": ("editor", _COARSEN),  # timeline marks (label-or-ip; a label passes through)
    # v0.16.0: the server-derived situation name is BUILT from device addresses, so it needs the
    # same rule they have — otherwise `Storm -> 127.0.0.2` carries past a rule that coarsens
    # `127.0.0.2` two fields away. `operator_name` is deliberately absent: it is free text a person
    # typed, like a device label, and a label passes through (see `device` above).
    "derived_name": ("editor", _COARSEN),
    "source_ip": ("admin", _DROP),  # who connected from where (audit / session detail)
    "community_tag": ("editor", _DROP),  # SNMP community grouping tag (F4)
}


def _rank(role: str | None) -> int:
    return ROLE_RANK.get(role or "", -1)


def _allowed(role: str | None, min_role: str) -> bool:
    return _rank(role) >= ROLE_RANK[min_role]


def coarsen_ip(value: Any) -> Any:
    """An IP → its /24 (v4) or /48 (v6) network; a non-IP string (a label) is returned as-is."""
    if not isinstance(value, str) or not value:
        return value
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return value  # already a hostname/label, or empty — nothing to coarsen
    prefix = 24 if isinstance(ip, ipaddress.IPv4Address) else 48
    return str(ipaddress.ip_network(f"{value}/{prefix}", strict=False))


@overload
def shape(obj: dict[str, Any], role: str | None) -> dict[str, Any]: ...
@overload
def shape(obj: list[Any], role: str | None) -> list[Any]: ...
@overload
def shape[T](obj: T, role: str | None) -> T: ...
def shape(obj: Any, role: str | None) -> Any:
    """Return a copy of ``obj`` projected for ``role``: protected fields coarsened or dropped.

    Recurses through lists and dicts; scalars pass through. Field rules match by key name at any
    depth, so the same rule shapes ``ip`` on a graph node and ``device_ip`` on an alarm row.
    """
    if isinstance(obj, list):
        return [shape(item, role) for item in obj]
    if not isinstance(obj, dict):
        return obj
    out: dict[str, Any] = {}
    for key, value in obj.items():
        rule = FIELD_RULES.get(key)
        if rule is not None:
            min_role, action = rule
            if not _allowed(role, min_role):
                if action == _DROP:
                    continue  # omit the field entirely for this role
                # A composite field carries addresses INSIDE a string, so coarsening it means
                # coarsening each address in it; `coarsen_ip` alone returns such a string
                # unchanged, which is exactly how v0.16.0's derived name leaked one.
                out[key] = (
                    coarsen_situation_name(value, coarsen_ip)
                    if key in _COMPOSITE
                    else coarsen_ip(value)
                )
                continue
        out[key] = shape(value, role) if isinstance(value, (dict, list)) else value
    return out
