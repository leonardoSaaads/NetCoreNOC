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

⚠ **Scoping is a presentation control and is NOT tenant isolation.** Correlation learns per
broker (ADR #495); within one it learns across every NE, and a situation may still form across a
boundary a principal cannot see — it is then redacted, not prevented. See
`docs/architecture/DESIGN.md` (v0.7.0) and `SCOPE-0.7.md`.

**The F35 invariant lives here, with the code that depends on it** (v0.7.4, DECISIONS #95): no
input to :func:`visible_nes` may be writable by a scopable role. The comment blocks on
:func:`visible_nes` and :func:`_matches` that say why moved with the functions, unedited, because a
justification separated from the code it justifies is a justification nobody re-reads.
`tests/security/test_governance.py::test_f35_no_resolver_input_is_writable_by_a_scopable_role`
asserts it.
"""

from __future__ import annotations

import fnmatch
import ipaddress
import json
from dataclasses import dataclass
from typing import Any


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

    Everything is filtered by ``ne_ids``: an element is (broker, address), so an address names an
    element only within a broker (ADR #494), and the graph filters its nodes by element id too.
    ``ips`` is kept for the one count keyed on a source address — the quarantine's, whose rows were
    refused before any element existed.
    """

    unrestricted: bool
    ne_ids: frozenset[int] = frozenset()
    ips: frozenset[str] = frozenset()
    #: The organizations (customers) whose NOC records this principal sees: those its policy names
    #: with ``org:<id>``, and the owner of every element it sees (ADR #499).
    orgs: frozenset[int] = frozenset()
    #: Only those its policy names: whose every broker it sees, even one with no element yet.
    named_orgs: frozenset[int] = frozenset()
    #: ADR #508: the brokers its policy names (`broker:<id>`), listed even before their first trap.
    named_brokers: frozenset[int] = frozenset()
    #: ADR #508: the NOC plane's own view, when the policy grants the NOC separately (`noc:`
    #: selectors); None when it does not, and the NOC follows the elements above as before.
    noc: Scope | None = None
    #: ADR #508: False when the policy names brokers or customers and no broker at all — the
    #: person has no Broker plane, and the console does not show one.
    broker_plane: bool = True

    def for_noc(self) -> Scope:
        """The scope a NOC read or write is decided by (`/api/noc/...`, ADR #508)."""
        return self.noc if self.noc is not None else self

    @property
    def noc_plane(self) -> bool:
        """Whether this principal has a NOC plane at all: unrestricted, or some customer in it."""
        noc = self.for_noc()
        return noc.unrestricted or bool(noc.orgs)

    def allows_ne(self, ne_id: int | None) -> bool:
        if self.unrestricted:
            return True
        return ne_id is not None and ne_id in self.ne_ids

    def allows_org(self, org_id: int | None) -> bool:
        """A customer's own records — its book, routines, credentials. A record that belongs to no
        customer (a shared routine, a provider key) is not one a scoped principal sees."""
        if self.unrestricted:
            return True
        return org_id is not None and int(org_id) in self.orgs


UNRESTRICTED = Scope(unrestricted=True)
DENY_ALL = Scope(unrestricted=False)

#: ADR #508: the NOC plane's selectors — every customer, one customer, or none at all.
NOC_ALL = "noc:*"
NOC_NONE = "noc:none"
BROKER_ALL = "broker:*"


def is_scopable(role: str) -> bool:
    """False for the roles scoping never applies to. **Admin is never scoped** (DECISIONS #58).

    The exemption lives here, next to the resolver that enforces it, so `api.py` never needs to
    compare a role — a comparison there would be the second decision site F28 forbids, and it
    would be invisible to the generated authorization matrix. Callers use this only to skip the
    inventory query; :func:`visible_nes` re-applies the same rule regardless, so forgetting it is
    a wasted query rather than a disclosure.
    """
    return role != "admin"


def limits_admin(
    role: str, principal_ref: str | None, is_owner: bool, policy: ScopePolicy | None
) -> bool:
    """ADR #508: an admin the visibility policy limits to brokers or customers — by its own entry,
    never a role-wide one — and never the super admin."""
    return (
        role == "admin"
        and not is_owner
        and policy is not None
        and not policy.malformed
        and principal_ref is not None
        and principal_ref in policy.principals
    )


def never_scoped(
    role: str, principal_ref: str | None, is_owner: bool, policy: ScopePolicy | None
) -> bool:
    """ADR #508: the super admin, and an admin the policy does not limit, see everything."""
    return is_owner or (role == "admin" and not limits_admin(role, principal_ref, False, policy))


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


def _matches(
    selector: str,
    ne_id: int,
    ip: str | None,
    broker_id: int | None = None,
    org_id: int | None = None,
) -> bool:
    """Does one selector name this NE? **Identity, broker and address only** (v0.7.1, F35).

    Six forms, tried in order of specificity: ``ne:<id>``, ``broker:<id>`` (every element of one
    monitored network, ADR #496), ``org:<id>`` (every element one customer owns, ADR #499), an
    exact address, a CIDR, and a glob over the address (``10.0.*``). An unparseable selector
    matches nothing — a typo hides NEs rather than revealing them. An address selector matches that
    address **in every broker**: two customers may reuse one range, and ``broker:<id>`` is how a
    policy names one of them.

    v0.7.0 also matched the glob against the **operator label**, and the operator label is written
    by ``POST /api/labels``, an `editor` route. That made the scoped role an author of its own
    scope: labelling an out-of-scope device ``core-pwned`` under a policy of ``{"editor":
    ["core-*"]}`` widened the editor's own visibility. **Authorization must never read data the
    constrained party can write** — so the label is not read here, and not merely guarded at the
    one write path that reaches it today (DECISIONS #66). Every value this function sees is
    engine- or admin-written: `ne.id` and `ne.ip` come from the trap stream and no API route can
    set them; `ne.broker_id` is the routing's answer, moved only by `brokers.write` (admin).

    The cost is deliberate and stated: a label glob in an existing policy now matches by address or
    not at all. :func:`scope_policy_errors` warns on a selector matching zero NEs so an admin finds
    out at write time, and `MIGRATION.md` says so in plain language.
    """
    if selector.startswith("ne:"):
        return selector[3:].strip() == str(ne_id)
    if selector == BROKER_ALL:  # ADR #508: every broker's elements
        return True
    if selector.startswith("noc:"):  # ADR #508: the NOC plane's, never an element of the Broker's
        return False
    if selector.startswith("broker:"):
        return broker_id is not None and selector[7:].strip() == str(broker_id)
    if selector.startswith("org:"):  # ADR #499: every element a customer owns, in every broker
        return org_id is not None and selector[4:].strip() == str(org_id)
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

    ADR #508: an **admin** is limited by its own entry only — a role-wide `admin` entry is refused
    at write time and ignored here — and the super admin never has one (the write path refuses it,
    and `scope_for` answers before this function for them).
    """
    if policy is None:
        return UNRESTRICTED
    if role == "admin":
        if policy.malformed or principal_ref is None or principal_ref not in policy.principals:
            return UNRESTRICTED  # the admin who must repair a broken policy is never locked out
    elif policy.malformed:
        return DENY_ALL
    layers = (
        _layers(policy, role, principal_ref)
        if role != "admin"
        else [policy.principals[principal_ref or ""]]
    )
    if not layers:
        return UNRESTRICTED
    selectors = {selector for layer in layers for selector in layer}
    ne_ids: set[int] = set()
    ips: set[str] = set()
    named = frozenset(
        int(s[4:].strip()) for s in selectors if s.startswith("org:") and s[4:].strip().isdigit()
    )
    brokers = frozenset(
        int(s[7:].strip()) for s in selectors if s.startswith("broker:") and s[7:].strip().isdigit()
    )
    orgs: set[int] = set(named)
    for ne in nes:
        ne_id = int(ne["id"])
        ip = ne.get("ip")
        broker = None if ne.get("broker_id") is None else int(ne["broker_id"])
        org = None if ne.get("organization_id") is None else int(ne["organization_id"])
        if any(_matches(selector, ne_id, ip, broker, org) for selector in selectors):
            ne_ids.add(ne_id)
            if ip:
                ips.add(str(ip))
            if org is not None:
                orgs.add(org)
    broker_side = BROKER_ALL in selectors or any(
        not s.startswith(("noc:", "org:")) for s in selectors
    )
    return Scope(
        unrestricted=False,
        ne_ids=frozenset(ne_ids),
        ips=frozenset(ips),
        orgs=frozenset(orgs),
        named_orgs=named,
        named_brokers=brokers,
        noc=_noc_scope(selectors, named, nes),
        broker_plane=broker_side or bool(named),
    )


def _noc_scope(
    selectors: set[str], named: frozenset[int], nes: list[dict[str, Any]]
) -> Scope | None:
    """ADR #508: the NOC plane's view, when the policy grants it on its own (`noc:` selectors).

    `noc:*` is every customer; `noc:<id>` that customer — its book, cases, routines and the
    credentials it holds, and every element it owns, in whichever broker; `noc:none` is no NOC. A
    legacy `org:<id>` still reaches that customer on both planes. With no `noc:` selector at all the
    NOC follows the Broker plane's elements, as it did (ADR #499) — None.
    """
    if not any(s.startswith("noc:") for s in selectors):
        return None
    if NOC_ALL in selectors:
        return UNRESTRICTED
    customers = set(named) | {
        int(s[4:].strip()) for s in selectors if s.startswith("noc:") and s[4:].strip().isdigit()
    }
    ne_ids = {
        int(ne["id"])
        for ne in nes
        if ne.get("organization_id") is not None and int(ne["organization_id"]) in customers
    }
    ips = {str(ne["ip"]) for ne in nes if int(ne["id"]) in ne_ids and ne.get("ip")}
    return Scope(
        unrestricted=False,
        ne_ids=frozenset(ne_ids),
        ips=frozenset(ips),
        orgs=frozenset(customers),
        named_orgs=frozenset(customers),
    )


def within(scope: Scope, nes: list[dict[str, Any]]) -> Scope:
    """`scope` narrowed to these elements — one broker's, for its own screens (ADR #496).

    A **view, never a grant**: every element kept is one `scope` already allows, so the result can
    only hide. It is what makes a broker's map, Timeline and Entities show that network alone,
    through the same predicate every read path already applies to a person's visibility.
    """
    ne_ids = {int(ne["id"]) for ne in nes if scope.allows_ne(int(ne["id"]))}
    ips = {str(ne["ip"]) for ne in nes if int(ne["id"]) in ne_ids and ne.get("ip")}
    return Scope(
        unrestricted=False,
        ne_ids=frozenset(ne_ids),
        ips=frozenset(ips),
        orgs=scope.orgs,
        named_orgs=scope.named_orgs,
        named_brokers=scope.named_brokers,
        noc=scope.noc,
        broker_plane=scope.broker_plane,
    )
