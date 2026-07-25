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

**v0.7.0 — resource scoping, a second axis in the same module.** `shape()` decides *which fields*
of a row a role may see. :func:`visible_nes` decides *which rows* a principal may see at all: a
stored, admin-managed policy naming the network elements a viewer or editor is shown. The two are
composed at every read as **authorize → read → scope-project → field-shape**, and each axis has
exactly one decision site.

Three rules govern the whole of it:

- **Admin is never scoped** (DECISIONS #58). Checked first, before any policy is read. This is what
  makes every fail-closed branch recoverable rather than terminal.
- **Unset means "no opinion"; set — even to nothing — means "exactly these"** (DECISIONS #63). No
  policy at all ⇒ every NE ⇒ v0.6.0 exactly.
- **Fail closed, never fail open.** An unreadable scope policy shows viewers and editors *nothing*,
  never everything.

⚠ **Scoping is a presentation control and is NOT tenant isolation.** Correlation still learns
across every NE, and a situation may still form across a boundary a principal cannot see — it is
then redacted, not prevented. See `docs/architecture/DESIGN.md` (v0.7.0) and `SCOPE-0.7.md`.
"""

from __future__ import annotations

import fnmatch
import ipaddress
import json
from dataclasses import dataclass
from typing import Any, overload

from netcorenoc.rbac import ROLE_RANK

# Minimum role that may see each protected field in full; below it the field is coarsened
# (transform) or dropped (transform is None). Keys are matched by field name anywhere in a
# (possibly nested) response body.
_COARSEN = "coarsen"
_DROP = "drop"
FIELD_RULES: dict[str, tuple[str, str]] = {
    "ip": ("editor", _COARSEN),  # device / NE address on graph + entity views
    "device_ip": ("editor", _COARSEN),  # alarm rows in a situation detail
    "device": ("editor", _COARSEN),  # timeline marks (label-or-ip; a label passes through)
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
                out[key] = coarsen_ip(value)
                continue
        out[key] = shape(value, role) if isinstance(value, (dict, list)) else value
    return out


# -- resource scoping: which NEs a principal may see (v0.7.0) ------------------------------


@dataclass(frozen=True)
class ScopePolicy:
    """A parsed visibility policy: per-role and per-principal NE selector sets.

    An **absent** subject expresses no opinion; a subject present with an **empty** list says
    "exactly nothing" (DECISIONS #54, #63). ``malformed`` marks a document that could not be
    understood; the resolver then denies for viewer/editor — never widens — and the operator
    warning plus the ``governance.fallback`` audit row make the degradation visible
    (DECISIONS #55).
    """

    roles: dict[str, tuple[str, ...]]
    principals: dict[str, tuple[str, ...]]
    malformed: bool = False
    reason: str = ""


@dataclass(frozen=True)
class Scope:
    """A resolved view of the NE inventory: which ids and which addresses are visible.

    ``unrestricted`` is the parity case and the admin case — every read path checks it first and
    then runs its unmodified v0.6.0 query, so an appliance with no policy pays nothing for this
    feature.

    Both ``ne_ids`` and ``ips`` are carried because the two things being filtered are keyed
    differently: alarms and entities reference ``ne_id``, while the graph is a projection of the
    ``device`` table joined to ``ne`` by address. Matching on address rather than assuming the two
    tables' ids coincide keeps the filter correct through the open ``device_id`` → ``ne_id``
    cutover.
    """

    unrestricted: bool
    ne_ids: frozenset[int] = frozenset()
    ips: frozenset[str] = frozenset()
    # Operator labels of the in-scope NEs. Needed because the timeline projects a device as
    # ``COALESCE(label, ip)``, so a labelled NE never appears in a response under its address.
    labels: frozenset[str] = frozenset()

    def allows_ne(self, ne_id: int | None) -> bool:
        if self.unrestricted:
            return True
        return ne_id is not None and ne_id in self.ne_ids

    def allows_ip(self, ip: str | None) -> bool:
        if self.unrestricted:
            return True
        return ip is not None and ip in self.ips


UNRESTRICTED = Scope(unrestricted=True)
DENY_ALL = Scope(unrestricted=False)


def is_scopable(role: str) -> bool:
    """False for the roles scoping never applies to. **Admin is never scoped** (DECISIONS #58).

    The exemption lives here, next to the resolver that enforces it, so `api.py` never needs to
    compare a role — a comparison there would be the second decision site F28 forbids, and it
    would be invisible to the generated authorization matrix. Callers use this only to skip the
    inventory query; :func:`visible_nes` re-applies the same rule regardless, so forgetting it is
    a wasted query rather than a disclosure.
    """
    return role != "admin"


def _selector_lists(raw: Any) -> dict[str, tuple[str, ...]] | None:
    """``{subject: [selector, ...]}`` → parsed, or None if the shape is wrong."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        return None
    out: dict[str, tuple[str, ...]] = {}
    for subject, selectors in raw.items():
        if not isinstance(subject, str) or not isinstance(selectors, list):
            return None
        if not all(isinstance(s, str) for s in selectors):
            return None
        out[subject] = tuple(selectors)
    return out


def parse_scope_policy(document: str) -> ScopePolicy:
    """Parse a stored scope document. **Never raises** — a bad document is `malformed`.

    Like :func:`netcorenoc.rbac.parse_capability_policy`, this runs on the request path, where an
    exception would be an outage rather than a degradation.
    """
    try:
        parsed = json.loads(document)
    except (ValueError, TypeError):
        return ScopePolicy({}, {}, malformed=True, reason="scope policy is not valid JSON")
    if not isinstance(parsed, dict):
        return ScopePolicy({}, {}, malformed=True, reason="scope policy is not an object")
    version = parsed.get("version", 1)
    if version != 1:
        return ScopePolicy(
            {}, {}, malformed=True, reason=f"unsupported scope policy version {version!r}"
        )
    roles = _selector_lists(parsed.get("roles"))
    principals = _selector_lists(parsed.get("principals"))
    if roles is None or principals is None:
        return ScopePolicy(
            {},
            {},
            malformed=True,
            reason="scope policy roles/principals must map a subject to a list of strings",
        )
    return ScopePolicy(roles, principals)


def _matches(selector: str, ne_id: int, ip: str | None, label: str | None) -> bool:
    """Does one selector name this NE?

    Four forms, tried in order of specificity: ``ne:<id>``, an exact address, a CIDR, and a glob.
    The glob is matched against the operator label when the NE has one and against the address
    otherwise, so ``core-*`` works for a labelled estate and ``10.0.*`` for an unlabelled one.
    An unparseable selector matches nothing — a typo hides NEs rather than revealing them.
    """
    if selector.startswith("ne:"):
        return selector[3:].strip() == str(ne_id)
    if ip:
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            address = None
        if address is not None:
            if "/" in selector:
                try:
                    return address in ipaddress.ip_network(selector, strict=False)
                except ValueError:
                    return False
            try:
                return address == ipaddress.ip_address(selector)
            except ValueError:
                pass  # not an address literal — fall through to the glob
    return fnmatch.fnmatchcase(label or ip or "", selector)


def _layers(policy: ScopePolicy, role: str, principal_ref: str | None) -> list[tuple[str, ...]]:
    """The selector lists of the layers that express an opinion (DECISIONS #63)."""
    out: list[tuple[str, ...]] = []
    role_selectors = policy.roles.get(role)
    if role_selectors is not None:
        out.append(role_selectors)
    if principal_ref is not None:
        principal_selectors = policy.principals.get(principal_ref)
        if principal_selectors is not None:
            out.append(principal_selectors)
    return out


def visible_nes(
    role: str,
    principal_ref: str | None,
    policy: ScopePolicy | None,
    nes: list[dict[str, Any]],
) -> Scope:
    """**THE** scope decision: which NEs this principal may see.

    ``nes`` is the inventory as ``[{id, ip, label}, ...]``; selectors are resolved against it on
    every request rather than materialised at write time, because NetCoreNOC discovers NEs
    continuously and a stale snapshot would silently hide an NE whose address a CIDR plainly
    covers (DECISIONS #57).

    Order matters and is deliberate:

    1. **admin ⇒ unrestricted**, before any policy is consulted (DECISIONS #58);
    2. **no policy ⇒ unrestricted** — parity, and the reason an un-configured appliance pays
       nothing for this feature;
    3. **malformed ⇒ deny**, never widen (DECISIONS #55);
    4. otherwise the **union of the layers that express an opinion**; if neither does, unrestricted.
    """
    if role == "admin":
        return UNRESTRICTED
    if policy is None:
        return UNRESTRICTED
    if policy.malformed:
        return DENY_ALL
    layers = _layers(policy, role, principal_ref)
    if not layers:
        return UNRESTRICTED
    selectors = {selector for layer in layers for selector in layer}
    ne_ids: set[int] = set()
    ips: set[str] = set()
    labels: set[str] = set()
    for ne in nes:
        ne_id = int(ne["id"])
        ip = ne.get("ip")
        label = ne.get("label")
        if any(_matches(selector, ne_id, ip, label) for selector in selectors):
            ne_ids.add(ne_id)
            if ip:
                ips.add(str(ip))
            if label:
                labels.add(str(label))
    return Scope(
        unrestricted=False,
        ne_ids=frozenset(ne_ids),
        ips=frozenset(ips),
        labels=frozenset(labels),
    )


def scope_policy_errors(policy: ScopePolicy) -> list[str]:
    """Operator-facing problems with a scope policy an admin is *writing*. Usability, not security.

    Nothing here can widen a scope — an unmatched selector simply selects nothing — so these are
    reported so an admin learns immediately that a line will not do what they meant.
    """
    if policy.malformed:
        return [policy.reason or "policy is malformed"]
    problems: list[str] = []
    for role in policy.roles:
        if role not in ROLE_RANK:
            problems.append(f"unknown role {role!r}")
        elif role == "admin":
            problems.append(
                "role 'admin' cannot be scoped: an admin must never be locked out of the data "
                "they are responsible for, and this entry would have no effect"
            )
    for subject in policy.principals:
        if not subject.startswith(("user:", "token:")):
            problems.append(f"principal {subject!r} must be 'user:<id>' or 'token:<id>'")
    return problems


# -- projections: applying a resolved scope to a response body -----------------------------


def filter_rows(rows: list[dict[str, Any]], scope: Scope, *, ne_key: str) -> list[dict[str, Any]]:
    """Keep only the rows whose NE is in scope. ``ne_key`` names the id column on the row."""
    if scope.unrestricted:
        return rows
    return [row for row in rows if scope.allows_ne(_as_int(row.get(ne_key)))]


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def project_graph(snapshot: dict[str, Any], scope: Scope) -> dict[str, Any]:
    """Graph nodes restricted to in-scope devices; an edge survives only if **both** ends do.

    Keeping a half-visible edge would disclose the existence of a neighbour the caller may not see
    — the graph would draw a line to nothing — so an edge with one end out of scope is dropped
    entirely rather than truncated.
    """
    if scope.unrestricted:
        return snapshot
    nodes = [node for node in snapshot.get("nodes", []) if scope.allows_ip(node.get("ip"))]
    visible_ids = {_as_int(node.get("id")) for node in nodes}
    edges = [
        edge
        for edge in snapshot.get("edges", [])
        if _as_int(edge.get("a_id")) in visible_ids and _as_int(edge.get("b_id")) in visible_ids
    ]
    return {**snapshot, "nodes": nodes, "edges": edges}


def project_timeline(marks: list[dict[str, Any]], scope: Scope) -> list[dict[str, Any]]:
    """Timeline marks for in-scope NEs only.

    A mark's ``device`` is ``COALESCE(label, ip)``, so it matches either the in-scope address set
    or an in-scope label. A mark that matches neither belongs to an NE the caller cannot see.
    """
    if scope.unrestricted:
        return marks
    return [mark for mark in marks if _mark_visible(mark, scope)]


def _mark_visible(mark: dict[str, Any], scope: Scope) -> bool:
    device = mark.get("device")
    if not isinstance(device, str):
        return False
    return device in scope.ips or device in scope.labels


def project_situation_detail(
    detail: dict[str, Any], scope: Scope, *, member_ne_ids: dict[int, int | None]
) -> dict[str, Any] | None:
    """Project one situation for a scoped reader, or **None** if none of it is visible.

    Returning ``None`` is how the 404 happens: the caller hands it to the handler's existing
    ``if detail is None: raise HTTPException(404)`` branch, so "out of your scope" and "no such
    situation" are the same code path, the same body, and the same timing — indistinguishable by
    construction rather than by two branches that happen to agree (DECISIONS #60).

    A situation survives if **at least one** member is in scope. Out-of-scope members are not
    deleted but **redacted to a count and a type** (DECISIONS #59): silently showing "3 alarms" for
    a 40-alarm cross-boundary incident would leave an operator confidently wrong about what they
    are looking at, which is the failure mode this project refuses elsewhere too. The redaction
    carries no NE id, address, entity key, or varbind — only how many members lie outside, and
    which alarm classes they belong to, which is strictly less than the situation id and
    ``updated_at`` the reader can already see.
    """
    if scope.unrestricted:
        return detail
    alarms = detail.get("alarms", [])
    visible: list[dict[str, Any]] = []
    hidden: list[dict[str, Any]] = []
    for alarm in alarms:
        ne_id = member_ne_ids.get(_as_int(alarm.get("id")) or -1)
        (visible if scope.allows_ne(ne_id) else hidden).append(alarm)
    if not visible:
        return None  # nothing of this situation is yours: indistinguishable from "no such id"
    visible_ids = {_as_int(alarm.get("id")) for alarm in visible}
    links = [
        link
        for link in detail.get("links", [])
        if _as_int(link.get("alarm_a")) in visible_ids
        and _as_int(link.get("alarm_b")) in visible_ids
    ]
    out = {**detail, "alarms": visible, "links": links}
    # The root hint names an alarm; suppress it when that alarm is one the reader may not see.
    if _as_int(out.get("root_alarm_id")) not in visible_ids:
        out["root_alarm_id"] = None
    if hidden:
        out["redacted_members"] = {
            "count": len(hidden),
            "classes": sorted(
                {str(a.get("class_name") or a.get("class_oid") or "") for a in hidden}
            ),
            "note": (
                f"{len(hidden)} further member(s) of this situation are outside your visibility "
                "scope and are not shown. Scoping hides them from you; it does not stop them "
                "correlating."
            ),
        }
    return out
