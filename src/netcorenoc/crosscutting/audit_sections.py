"""The audit log's sections: every action in the catalogue, filed under one place in the console
(ADR #486).

An auditor asks "what happened to sign-in this week" or "who changed the NOC's vault", not "show
me `twofactor.recovery_codes`". Each section names the parts of the product it covers; an action
belongs to the section whose prefix its own first word matches. A test asserts that every action in
:data:`netcorenoc.crosscutting.audit.ACTIONS` lands in exactly one section, so a new action cannot
be left out of the filter.

Pure.
"""

from __future__ import annotations

__all__ = ["SECTIONS", "section_of"]

#: name -> (what the console calls it, the first words of its actions)
SECTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "sign-in": ("Sign-in & accounts", ("login", "logout", "password", "twofactor", "profile")),
    "people": ("People, roles & tokens", ("user", "role", "token", "rbac", "scope", "governance")),
    "situations": (
        "Situations & alarms",
        ("situation", "alarm", "label", "feedback", "severity", "entity", "maintenance", "notice"),
    ),
    "models": (
        "Models & learning",
        ("promotion", "league", "decider", "search", "seal", "autonomy", "scorer"),
    ),
    "settings": (
        "Settings & data",
        ("config", "snmp", "email", "catalogue", "retention", "db", "ingest"),
    ),
    "noc": ("NOC", ("noc",)),
    "audit": ("Audit & quarantine", ("audit", "quarantine", "prune")),
}


def section_of(action: str) -> str | None:
    """The section ``action`` is filed under, or None for a word no section names."""
    head = action.split(".", 1)[0]
    for name, (_label, words) in SECTIONS.items():
        if head in words:
            return name
    return None
