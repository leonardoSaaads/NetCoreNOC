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

**Reachability** (ADR #476) is the one thing here no case says: which equipment the NOC can no
longer reach over management. A switch that loses power or its management path sends no trap —
nothing is left to send one — so no alarm, situation or case ever names it; the vault's endpoint
checks (`probe.py`) are what notice. `reachability` reads them per element: its worst endpoint,
and since when the failures run.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from netcorenoc.engine.noc import health, kpi
from netcorenoc.engine.noc.cases import PENDING, PRIORITIES
from netcorenoc.engine.noc.kinds import KINDS

__all__ = ["LEVELS", "STATES", "level_of", "reachability", "state_of", "summary"]

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


#: Worst first: an element is as reachable as its least reachable endpoint.
_REACH_ORDER = {"down": 0, "degraded": 1, "up": 2}


def _failing_since(checks: Sequence[Mapping[str, Any]]) -> float | None:
    """When the unbroken run of failures that the newest check ends began (checks newest first)."""
    since = None
    for check in checks:
        if check["ok"]:
            break
        since = float(check["at"])
    return since


def reachability(
    credentials: Iterable[Mapping[str, Any]],
    checks: Mapping[int, Sequence[Mapping[str, Any]]],
    elements: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Per host, the worst of its monitored device endpoints: ``monitored`` hosts, how many answer,
    and ``trouble`` — those down (worst and longest first) or intermittent. ``elements`` maps an
    address to its element (``id``, ``label``), so a host reads as the name the NOC gave it.

    Only what a check can see counts: a device credential, monitored, of a kind with a check (SNMP
    has none: it answers only a request carrying the secret, and a check never opens the seal).
    """
    worst: dict[str, dict[str, Any]] = {}
    for row in credentials:
        kind = KINDS.get(str(row["kind"]))
        if kind is None or kind.group != "device" or kind.check is None or not row["monitor"]:
            continue
        mine = list(checks.get(int(row["id"]), ()))
        light = health.status(mine, checkable=True, monitored=True)
        if light["state"] not in _REACH_ORDER:
            continue  # never checked yet: nothing known
        host = str(row["host"])
        element = elements.get(host) or {}
        entry = {
            "host": host,
            "port": int(row["port"]),
            "protocol": kind.check.upper(),
            "element": element.get("label"),
            "ne_id": element.get("id"),
            "state": light["state"],
            "reason": light["reason"],
            "last_checked_at": light["last_checked_at"],
            "since": _failing_since(mine) if light["state"] == "down" else None,
            "secret_id": int(row["id"]),
        }
        held = worst.get(host)
        if held is None or _REACH_ORDER[entry["state"]] < _REACH_ORDER[held["state"]]:
            worst[host] = entry
    rows = sorted(
        worst.values(), key=lambda e: (_REACH_ORDER[e["state"]], e["since"] or 0.0, e["host"])
    )
    return {
        "monitored": len(rows),
        "answering": sum(1 for e in rows if e["state"] == "up"),
        "trouble": [e for e in rows if e["state"] != "up"],
    }
