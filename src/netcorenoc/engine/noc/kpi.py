"""The clocks a NOC is measured by, per case and over a window (ADR #460).

Two clocks run on every **cycle** — one entry of a case into the NOC queue (`noc_case_cycle`):

* **Take** — from entering the queue to someone taking the case. The target is the priority's:
  critical 25 minutes, major one hour, minor two hours. Past it, the clock keeps counting and the
  overrun is what is reported: a case taken at 40 minutes is a 15-minute miss, not a "missed".
* **Document** — from the take to the customer update being complete (title, description, executive
  summary, current analysis, both next steps, next update): 30 minutes.

A cycle that ends without a take — the situation cleared, automation resolved it — is
**withdrawn**: no clock is charged for it.

The **update cadence** is the handbook's: how far ahead a next update may be for a working or a
pending case of each priority. It is advice the console shows beside the date, not a refusal — a
maintenance window three days out is a legitimate reason to look again in three days, and the
person who chose that date can say so in the feed.

Pure: rows in, numbers out.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping
from typing import Any

from netcorenoc.engine.noc.cases import PENDING

__all__ = [
    "CLOSE_AFTER_S",
    "DOCUMENT_TARGET_S",
    "TAKE_TARGET_S",
    "UPDATE_CADENCE_S",
    "cadence",
    "document",
    "summary",
    "take",
]

#: From entering the queue to being taken, per priority.
TAKE_TARGET_S: dict[str, float] = {"critical": 25 * 60.0, "major": 60 * 60.0, "minor": 120 * 60.0}
#: From being taken to a complete customer update.
DOCUMENT_TARGET_S = 30 * 60.0
#: The handbook's update frequency: the furthest ahead a next update should be.
UPDATE_CADENCE_S: dict[str, dict[str, float]] = {
    "working": {"critical": 30 * 60.0, "major": 2 * 3600.0, "minor": 6 * 3600.0},
    "pending": {"critical": 3600.0, "major": 3 * 3600.0, "minor": 6 * 3600.0},
}
#: A resolved case nobody touches is closed after this long.
CLOSE_AFTER_S = 24 * 3600.0


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def _clock(start: float, stop: float | None, now: float, target: float) -> dict[str, Any]:
    elapsed = (stop if stop is not None else now) - start
    if stop is None:
        state = "late" if elapsed > target else "running"
    else:
        state = "met" if elapsed <= target else "missed"
    return {
        "target_s": target,
        "due_at": start + target,
        "elapsed_s": round(max(0.0, elapsed), 3),
        "overrun_s": round(max(0.0, elapsed - target), 3),
        "state": state,
    }


def take(cycle: Mapping[str, Any], now: float) -> dict[str, Any]:
    """The take clock of one cycle: running, late (running past its target), met, missed, or
    withdrawn (the cycle ended with nobody taking it)."""
    target = TAKE_TARGET_S[str(cycle["priority"])]
    entered, taken = float(cycle["entered_at"]), _f(cycle.get("taken_at"))
    if taken is None and cycle.get("ended_at") is not None:
        out = _clock(entered, _f(cycle["ended_at"]), now, target)
        return {**out, "overrun_s": 0.0, "state": "withdrawn"}
    return _clock(entered, taken, now, target)


def document(cycle: Mapping[str, Any], now: float) -> dict[str, Any] | None:
    """The documentation clock of one cycle, or ``None`` before anyone took it. A stint that ends
    — released, or the case left working — without a complete update is a miss."""
    taken = _f(cycle.get("taken_at"))
    if taken is None:
        return None
    done, left = _f(cycle.get("documented_at")), _f(cycle.get("left_at"))
    out = _clock(taken, done if done is not None else left, now, DOCUMENT_TARGET_S)
    if done is None and left is not None:
        out["state"] = "missed"
    return out


def cadence(case: Mapping[str, Any], now: float) -> dict[str, Any] | None:
    """The next update against the handbook's cadence, for a working or pending case."""
    status = str(case["status"])
    phase = "pending" if status in PENDING else "working" if status == "working" else None
    when = _f(case.get("next_update_at"))
    if phase is None or when is None:
        return None
    limit = UPDATE_CADENCE_S[phase][str(case["priority"])]
    return {
        "next_update_at": when,
        "overdue": when <= now,
        "cadence_s": limit,
        "beyond_cadence": when - now > limit,
    }


def _rate(hits: int, total: int) -> float | None:
    return round(100.0 * hits / total, 1) if total else None


def summary(cycles: Iterable[Mapping[str, Any]], now: float) -> dict[str, Any]:
    """Every cycle in a window, reduced to what a NOC dashboard shows."""
    takes = {"met": 0, "missed": 0, "running": 0, "late": 0, "withdrawn": 0}
    docs = {"met": 0, "missed": 0, "running": 0, "late": 0}
    take_s: list[float] = []
    doc_s: list[float] = []
    overrun = 0.0
    by_priority: dict[str, dict[str, int]] = {
        p: {"met": 0, "missed": 0, "open": 0} for p in TAKE_TARGET_S
    }
    count = 0
    for cycle in cycles:
        count += 1
        t = take(cycle, now)
        takes[t["state"]] += 1
        overrun += t["overrun_s"]
        row = by_priority[str(cycle["priority"])]
        if t["state"] in ("met", "missed"):
            take_s.append(t["elapsed_s"])
            row[t["state"]] += 1
        elif t["state"] != "withdrawn":
            row["open"] += 1
        d = document(cycle, now)
        if d is not None:
            docs[d["state"]] += 1
            if d["state"] in ("met", "missed") and cycle.get("documented_at") is not None:
                doc_s.append(d["elapsed_s"])
    judged = takes["met"] + takes["missed"] + takes["late"]
    doc_judged = docs["met"] + docs["missed"] + docs["late"]
    return {
        "cycles": count,
        "take": {
            **takes,
            "met_pct": _rate(takes["met"], judged),
            "median_s": round(statistics.median(take_s), 1) if take_s else None,
            "overrun_s": round(overrun, 1),
        },
        "document": {
            **docs,
            "met_pct": _rate(docs["met"], doc_judged),
            "median_s": round(statistics.median(doc_s), 1) if doc_s else None,
        },
        "by_priority": by_priority,
        "targets": {"take_s": TAKE_TARGET_S, "document_s": DOCUMENT_TARGET_S},
    }
