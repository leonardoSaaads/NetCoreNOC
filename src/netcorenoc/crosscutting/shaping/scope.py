"""Resource scoping: which network elements a principal may see (v0.7.0).

`fields.py` decides *which fields* of a row a role may see. This module decides **which rows** a
principal may see at all: a stored, admin-managed policy naming the network elements a viewer or
editor is shown. The two axes are composed at every read as
**authorize → read → scope-project → field-shape**, and each has exactly one decision site.

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

**The F35 invariant lives here, with the code that depends on it** (v0.7.4, DECISIONS #95): no
input to :func:`visible_nes` may be writable by a scopable role. The comment blocks on
:func:`visible_nes` and :func:`_matches` that say why moved with the functions, unedited, because a
justification separated from the code it justifies is a justification nobody re-reads.
`tests/test_governance.py::test_f35_no_resolver_input_is_writable_by_a_scopable_role` asserts it.
"""

from __future__ import annotations

import fnmatch
import ipaddress
import json
from dataclasses import dataclass
from typing import Any

from netcorenoc.crosscutting.rbac import ROLE_RANK


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


def _matches(selector: str, ne_id: int, ip: str | None) -> bool:
    """Does one selector name this NE? **Identity and address only** (v0.7.1, F35).

    Four forms, tried in order of specificity: ``ne:<id>``, an exact address, a CIDR, and a glob
    over the address (``10.0.*``). An unparseable selector matches nothing — a typo hides NEs
    rather than revealing them.

    v0.7.0 also matched the glob against the **operator label**, and the operator label is written
    by ``POST /api/labels``, an `editor` route. That made the scoped role an author of its own
    scope: labelling an out-of-scope device ``core-pwned`` under a policy of ``{"editor":
    ["core-*"]}`` widened the editor's own visibility. **Authorization must never read data the
    constrained party can write** — so the label is not read here, and not merely guarded at the
    one write path that reaches it today (DECISIONS #66). Every value this function sees is
    engine-written: `ne.id` and `ne.ip` come from the trap stream and no API route can set them.

    The cost is deliberate and stated: a label glob in an existing policy now matches by address or
    not at all. :func:`scope_policy_errors` warns on a selector matching zero NEs so an admin finds
    out at write time, and `MIGRATION.md` says so in plain language.
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
    return fnmatch.fnmatchcase(ip or "", selector)


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

    ``nes`` is the inventory as ``[{id, ip}, ...]``; selectors are resolved against it on every
    request rather than materialised at write time, because NetCoreNOC discovers NEs continuously
    and a stale snapshot would silently hide an NE whose address a CIDR plainly covers
    (DECISIONS #57).

    **Every input to this function is admin-written or engine-written, and that is a release
    invariant, not an accident** (v0.7.1, F35). `role` and `principal_ref` come from identities only
    an admin can create; `policy` is `scope.write`, admin-only with no delegation; `nes` is `id` and
    `ip` from the trap stream. v0.7.0 also passed the operator **label**, which `editor` writes —
    that was the escalation. Anything added to this signature in future must satisfy the same test:
    `test_f35_no_resolver_input_is_writable_by_a_scopable_role`.

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
    for ne in nes:
        ne_id = int(ne["id"])
        ip = ne.get("ip")
        if any(_matches(selector, ne_id, ip) for selector in selectors):
            ne_ids.add(ne_id)
            if ip:
                ips.add(str(ip))
    return Scope(unrestricted=False, ne_ids=frozenset(ne_ids), ips=frozenset(ips))


# Characters that can appear in a textual IP address: hex digits, the IPv4 dot, the IPv6 colon,
# and the CIDR slash. A glob whose literal parts contain anything else can never match an address.
_ADDRESS_CHARS = frozenset("0123456789abcdefABCDEF.:/")
_GLOB_CHARS = frozenset("*?[]!-")


def _can_never_match(selector: str) -> bool:
    """True if this selector cannot match **any** address, now or ever (v0.7.1, F35).

    A *static* property of the selector, deliberately **not** "matches nothing right now". Scope
    selectors are resolved against the live inventory on every request precisely because NetCoreNOC
    discovers NEs continuously (DECISIONS #57), so a CIDR covering a range that is empty today is a
    perfectly good forward-looking policy and must not be rejected. What *is* always wrong is a
    selector made of characters an address never contains — ``core-*``, ``POP-SUL`` — which is
    exactly the label glob that v0.7.0 resolved against the operator label and v0.7.1 does not.
    Reporting it at write time is how an admin upgrading from v0.7.0 finds out (see `MIGRATION.md`)
    rather than discovering it from behaviour.
    """
    if selector.startswith("ne:"):
        return False
    literal = "".join(c for c in selector if c not in _GLOB_CHARS)
    return bool(literal) and not set(literal) <= _ADDRESS_CHARS


def scope_policy_errors(policy: ScopePolicy) -> list[str]:
    """Operator-facing problems with a scope policy an admin is *writing*. Usability, not security.

    Nothing here can widen a scope — an unmatched selector simply selects nothing — so these are
    reported so an admin learns immediately that a line will not do what they meant.
    """
    if policy.malformed:
        return [policy.reason or "policy is malformed"]
    problems: list[str] = []
    for subject, selectors in (*policy.roles.items(), *policy.principals.items()):
        for selector in selectors:
            if _can_never_match(selector):
                problems.append(
                    f"selector {selector!r} for {subject!r} can never match a network element. "
                    "Since v0.7.1 a selector resolves against NE id and address only — never the "
                    "operator label, which the role being scoped can write (F35) — so a label glob "
                    "selects nothing. Use an address, a CIDR, or 'ne:<id>' (see MIGRATION.md)."
                )
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
