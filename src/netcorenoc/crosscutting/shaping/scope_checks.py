"""Write-time checks on a visibility policy: what an admin is told before it is saved.

Split out of `scope.py` (ADR #508) on the seam the two already had: that module decides **at every
request** which rows a principal sees; this one tells an admin, **when a policy is written**, that a
line will not do what they meant. Nothing here can widen a scope — an unmatched selector selects
nothing — so these are usability, never security.
"""

from __future__ import annotations

from netcorenoc.crosscutting.rbac import ROLE_RANK
from netcorenoc.crosscutting.shaping.scope import BROKER_ALL, NOC_ALL, NOC_NONE, ScopePolicy

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
    if selector in (BROKER_ALL, NOC_ALL, NOC_NONE):  # ADR #508
        return False
    if selector.startswith("noc:"):
        return not selector[4:].strip().isdigit()  # a NOC customer is named by its id
    if selector.startswith("broker:"):
        return not selector[7:].strip().isdigit()  # a broker is named by its id, as on its page
    if selector.startswith("org:"):
        return not selector[4:].strip().isdigit()  # a customer is named by its id too
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
                    "selects nothing. Use an address, a CIDR, 'ne:<id>', 'broker:<id>' or "
                    "'org:<id>' (see MIGRATION.md)."
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
