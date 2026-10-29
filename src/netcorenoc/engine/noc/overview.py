"""The NOC at a glance: how many cases wait, are worked and wait on someone — and where (ADR #470).

The Broker's overview answers "how bad is it" with active alarms per band; the NOC's answers it
with its own three numbers — **Queue** (nobody has taken it: `new`, `auto`, `queue`), **Working**
(`working`, `validate`) and **Pending** (on the customer, a provider, a window…) — then splits
each the ways a NOC lead asks:

* **by level**: the agents' tier the case has climbed to (1-4), or the NOC team once it is past
  the agents (`with_people`);
* **by priority**, the case's own, which follows the Broker's severity until a person sets it;
* **by holder**: a person who took it, an agent it is routed to, or nobody;
* **who holds what**: each agent and each person, with their cases in each state.

Two clocks a queue cannot show sit beside them: the takes running late (nobody took the case
within its priority's target) and the next updates overdue or due within the hour.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from netcorenoc.engine.noc import kpi
from netcorenoc.engine.noc.cases import PENDING, PRIORITIES

__all__ = ["LEVELS", "STATES", "level_of", "state_of", "summary"]

#: The three states the overview counts, each a set of case statuses.
STATES: dict[str, frozenset[str]] = {
    "queue": frozenset({"new", "auto", "queue"}),
    "working": frozenset({"working", "validate"}),
    "pending": PENDING,
}
#: Where a case is: an agents' tier, or the NOC team once it is past the agents.
LEVELS: dict[str, str] = {
    "1": "Tier 1",
    "2": "Tier 2",
    "3": "Tier 3",
    "4": "Tier 4",
    "team": "NOC team",
}
#: A running take this far into its target is near its limit.
NEAR = 0.75


def state_of(status: str) -> str | None:
    return next((name for name, statuses in STATES.items() if status in statuses), None)


def level_of(row: Mapping[str, Any]) -> str:
    return "team" if row.get("with_people") else str(int(row.get("tier") or 1))


def _zero(keys: Iterable[str]) -> dict[str, int]:
    return dict.fromkeys(keys, 0)


def _load(entry: Mapping[str, Any]) -> tuple[int, str]:
    """Most cases first, then by name."""
    return (-sum(int(entry[state]) for state in STATES), str(entry["name"]))


def _holders(census: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    agents: dict[int, dict[str, Any]] = {}
    people: dict[str, dict[str, Any]] = {}
    for row in census:
        state, n = state_of(str(row["status"])), int(row["n"])
        if state is None:
            continue
        if row.get("person_ref"):
            ref = str(row["person_ref"])
            entry = people.setdefault(
                ref,
                {
                    "ref": ref,
                    "user_id": int(ref[5:]) if ref[5:].isdigit() else None,
                    "name": row.get("person_name"),
                    "avatar": row.get("person_avatar"),
                    **_zero(STATES),
                },
            )
            entry[state] += n
        if row.get("agent_id") is not None:
            aid = int(row["agent_id"])
            entry = agents.setdefault(
                aid, {"id": aid, "name": row.get("agent_name"), **_zero(STATES)}
            )
            entry[state] += n
    return {
        "agents": sorted(agents.values(), key=_load),
        "people": sorted(people.values(), key=_load),
    }


def _takes(takes: Iterable[Mapping[str, Any]], now: float) -> dict[str, int]:
    out = {"running": 0, "near": 0, "late": 0}
    for take in takes:
        target = kpi.TAKE_TARGET_S[str(take["priority"])]
        share = (now - float(take["entered_at"])) / target
        out["late" if share >= 1 else "near" if share >= NEAR else "running"] += 1
    return out


def summary(
    census: Iterable[Mapping[str, Any]],
    takes: Iterable[Mapping[str, Any]],
    moments: Mapping[str, int],
    now: float,
) -> dict[str, Any]:
    """`store.noc_case_census`, `noc_open_takes` and `noc_case_moments`, as the overview draws
    them. Every count is the caller's scope's: the store filtered the rows."""
    rows = list(census)
    states: dict[str, dict[str, Any]] = {
        name: {
            "total": 0,
            "by_level": _zero(LEVELS),
            "by_priority": _zero(PRIORITIES),
            "by_holder": _zero(("person", "agent", "none")),
        }
        for name in STATES
    }
    for row in rows:
        state = state_of(str(row["status"]))
        if state is None:
            continue
        n, bucket = int(row["n"]), states[state]
        bucket["total"] += n
        bucket["by_level"][level_of(row)] += n
        bucket["by_priority"][str(row["priority"])] += n
        bucket["by_holder"][str(row["holder"])] += n
    return {
        "now": now,
        "states": states,
        "levels": LEVELS,
        "priorities": PRIORITIES,
        "takes": _takes(takes, now),
        "updates": {
            "overdue": int(moments.get("overdue", 0)),
            "due_soon": int(moments.get("due_soon", 0)),
        },
        "resolved": {
            "total": int(moments.get("resolved", 0)),
            "by_agents": int(moments.get("resolved_by_agents", 0)),
            "without_noc": int(moments.get("resolved_without_noc", 0)),
        },
        **_holders(rows),
    }
