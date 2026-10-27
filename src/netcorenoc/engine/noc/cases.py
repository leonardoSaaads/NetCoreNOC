"""A NOC case: what the NOC does with a situation once the broker hands it over (ADR #459).

**A case is the situation, worked.** A situation is the broker's — phase 1 groups traps into it,
and a trap belongs to one situation only. When a situation becomes `open` (an operator, or
autonomy, accepted the grouping as something to work), the database gives it a case in the same
transaction (`migrations/0034_noc_cases.sql`), so there is never an open situation without one and
never a case without its situation. The case carries what the NOC adds: an owner, a status, the
customer update (title, description, executive summary, current analysis, NOC next step, customer
next step, next update), a feed, and the clocks the NOC is measured by.

The lifecycle follows the NOC handbook's super-states, renamed where the appliance's own words are
clearer:

    new ──► queue ──take──► working ──► pending_* ──(next update / a person posts)──► queue
     │        ▲                │  ▲          │
     └► auto ─┘ (no answer)    │  └──take────┘
                               ├──► resolved ──(24 h)──► closed
                               └──release──► queue

* **`new`** exists for a moment: the case loop (`case_loop.py`) dispatches it to `auto` when an
  automated check applies, otherwise to the **NOC queue**.
* **`queue`** — nobody has taken it. The take clock runs (`kpi.py`).
* **`working`** — someone owns it. The documentation clock runs until the customer update is
  complete.
* **`pending_*`** — waiting on someone else, until the next update. Reaching it requires the
  customer update to be complete and the next update to be in the future.
* **`validate`** — the situation resolved itself while someone owned the case: they confirm.
* **`resolved`** — the NOC says it is over. **`closed`** — a day later, untouched.

Pure: vocabularies, the transition table, and what a case must carry to make a move.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = [
    "CAUSES",
    "CYCLE_REASONS",
    "FIELD_LIMITS",
    "LIVE",
    "PENDING",
    "PRIORITIES",
    "RESOLUTIONS",
    "STATUSES",
    "TEXT_FIELDS",
    "UNOWNED",
    "CaseError",
    "documented",
    "missing",
    "priority_of",
    "super_state",
    "transition",
]


class CaseError(ValueError):
    """A move the case cannot make, with the sentence that says why, and the HTTP status that
    classifies it: 400 the request is wrong, 403 it is not the caller's to make, 409 not now."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


#: Every status, with its label. `migrations/0034_noc_cases.sql` holds the same set in a CHECK,
#: asserted by `tests/noc/test_cases.py`.
STATUSES: dict[str, str] = {
    "new": "New",
    "auto": "Automated checks",
    "queue": "NOC queue",
    "working": "Working",
    "pending_customer": "Pending customer",
    "pending_fiber_provider": "Pending carrier / fiber provider",
    "pending_maintenance": "Pending scheduled maintenance",
    "pending_dispatch": "Pending dispatch (technician)",
    "pending_device_access": "Pending access to the device",
    "pending_connectivity": "Pending connectivity (no ping / SNMP / SSH)",
    "pending_vendor": "Pending equipment vendor",
    "pending_site_access": "Pending site access approval",
    "pending_field_tech": "Pending field technician on site",
    "pending_rca": "Pending root cause analysis",
    "validate": "Validate resolution",
    "resolved": "Resolved",
    "closed": "Closed",
}

#: Waiting on someone outside the NOC: the next update is when the NOC looks again.
PENDING: frozenset[str] = frozenset(s for s in STATUSES if s.startswith("pending_"))
#: Not yet over: the statuses the case loop and the queue read.
LIVE: frozenset[str] = frozenset(STATUSES) - {"resolved", "closed"}
#: Nobody owns the case in these.
UNOWNED: frozenset[str] = frozenset({"new", "auto", "queue"})

#: The NOC handbook's priorities. A situation's severity decides one (`priority_of`).
PRIORITIES: dict[str, str] = {"critical": "Critical", "major": "Major", "minor": "Minor"}

#: Why a case entered the NOC queue — each entry is a cycle the take clock measures.
CYCLE_REASONS: dict[str, str] = {
    "opened": "The situation was opened",
    "automation": "Automated checks did not conclude",
    "customer_post": "A person posted on the feed while it was pending",
    "next_update": "Its next update came",
    "released": "Its owner put it back",
}

#: How a case came to be resolved.
RESOLUTIONS: dict[str, str] = {
    "noc": "Resolved by the NOC",
    "automation": "Resolved by an automated check",
    "cleared": "The situation cleared before anyone took the case",
    "merged": "The situation was merged into another",
}

#: The cause a resolution records — the handbook's closure codes, without the vendor's attribution
#: column. A closed vocabulary because it is what a model will one day learn from.
CAUSES: dict[str, str] = {
    "planned_maintenance": "Planned maintenance",
    "unplanned_maintenance": "Unscheduled maintenance",
    "signal_interference": "Signal interference",
    "force_majeure": "Force majeure / weather",
    "cleared_before_isolation": "Cleared before isolation",
    "no_trouble_found": "No trouble found",
    "duplicate": "Duplicate case",
    "hardware": "Hardware failure",
    "software": "Software / firmware",
    "cpe": "Customer premises equipment",
    "configuration": "Configuration / management",
    "decommissioned": "Decommissioned",
    "disconnected": "Deliberately disconnected",
    "turn_up": "Turn-up",
    "power_commercial": "Power — commercial",
    "power_ups": "Power — UPS / batteries",
    "hvac": "HVAC / temperature",
    "client_fiber_cut": "Client fiber / cable cut",
    "carrier_fiber_cut": "Carrier fiber / cable cut",
    "client_circuit": "Client circuit issue",
    "carrier_circuit": "Carrier circuit issue",
    "overutilization": "Overutilization",
    "security_event": "Security event",
    "human_error": "Human error",
    "no_client_response": "No client response",
    "client_requested_closure": "Client requested closure",
    "other": "Other",
}

#: The customer update and the resolution: each field, its label and its length cap. The database
#: holds the same caps in CHECKs.
FIELD_LIMITS: dict[str, tuple[str, int]] = {
    "title": ("Title", 120),
    "description": ("Description", 4000),
    "executive_summary": ("Executive summary", 200),
    "current_analysis": ("Current analysis", 8000),
    "noc_next_step": ("NOC next step", 4000),
    "customer_next_step": ("Customer next step", 4000),
    "solution": ("Solution", 8000),
}
TEXT_FIELDS: tuple[str, ...] = tuple(FIELD_LIMITS)

#: What a case must carry to wait on someone else: the complete customer update.
_FOR_PENDING = (
    "title",
    "description",
    "executive_summary",
    "current_analysis",
    "noc_next_step",
    "customer_next_step",
    "next_update_at",
)
#: What it must carry to be resolved: what happened, and how it ended.
_FOR_RESOLVED = (
    "title",
    "description",
    "executive_summary",
    "current_analysis",
    "cause",
    "solution",
)


def priority_of(severity: str | None) -> str:
    """The case priority for a situation severity: critical and major map across, anything else
    (minor, warning, indeterminate, none yet) is minor."""
    return severity if severity in ("critical", "major") else "minor"


def super_state(status: str) -> str:
    """The step of the status path a status belongs to (`pending_customer` → `pending`)."""
    if status in PENDING:
        return "pending"
    if status == "new":
        return "queue"
    return status


def missing(case: Mapping[str, Any], target: str, *, now: float) -> list[str]:
    """The fields ``case`` still lacks to reach ``target`` (a pending status or `resolved`)."""
    wanted = _FOR_RESOLVED if target == "resolved" else _FOR_PENDING
    out = []
    for name in wanted:
        value = case.get(name)
        if name == "next_update_at":
            if value is None or float(value) <= now:
                out.append(name)
        elif not (isinstance(value, str) and value.strip()):
            out.append(name)
    return out


def documented(case: Mapping[str, Any], *, now: float) -> bool:
    """Is the customer update complete? The documentation clock stops the first time it is."""
    return not missing(case, "pending_customer", now=now)


#: `act -> statuses it may start from`. The resulting status is the act's own (`transition`).
#: Acts by a person or an agent; the case loop's own acts are below.
_FROM: dict[str, frozenset[str]] = {
    # Taking a case: from the queue, from automation, from a pending wait, to confirm a resolution,
    # or from another owner (a shift change) — the handbook's "change owner".
    "take": frozenset({"new", "auto", "queue", "working", "validate"}) | PENDING,
    "release": frozenset({"working", "validate"}),
    "pend": frozenset({"working"}) | PENDING,
    "resolve": frozenset({"working", "validate"}) | PENDING,
    # -- the case loop's acts --
    "dispatch": frozenset({"new"}),
    "automate": frozenset({"new"}),
    "requeue": frozenset({"auto"}) | PENDING,
    "validate": frozenset({"working"}) | PENDING,
    "auto_resolve": frozenset({"new", "auto", "queue"}),
    # A playbook's leaf that waits on someone: no owner, no take — automation is not the NOC.
    "auto_pend": frozenset({"auto"}),
    "close": frozenset({"resolved"}),
}
_TO: dict[str, str] = {
    "take": "working",
    "release": "queue",
    "resolve": "resolved",
    "dispatch": "queue",
    "automate": "auto",
    "requeue": "queue",
    "validate": "validate",
    "auto_resolve": "resolved",
    "close": "closed",
}


def transition(act: str, status: str, *, to: str | None = None) -> str:
    """The status ``act`` takes a case in ``status`` to, or a :class:`CaseError` saying why not.

    ``pend`` and ``auto_pend`` name their pending status in ``to``; every other act decides its
    own.
    """
    allowed = _FROM.get(act)
    if allowed is None:
        raise CaseError(f"unknown act {act!r}")
    if status not in allowed:
        raise CaseError(
            f"a case in {STATUSES.get(status, status)!r} cannot {act.replace('_', ' ')}"
        )
    if act in ("pend", "auto_pend"):
        if to not in PENDING:
            raise CaseError(f"status: one of {', '.join(sorted(PENDING))}", 400)
        return to
    return _TO[act]
