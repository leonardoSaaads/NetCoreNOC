"""Autonomy's verdicts: what the operators did after an act, read as agreement or disagreement.

Split out of `autonomy.py` in v0.26.0 at the 400-line module guard, on the seam between **acting**
and **judging** (ADR #412). Nothing here acts: it reads the gestures recorded after each pending
decision and writes one verdict per decision, which `autonomy.sweep` then counts against the
agreement floor.

* **grouping** — an operator's move, merge or split on the situation disagrees; any other gesture
  that works it agrees;
* **naming** — an operator's rename disagrees;
* **severity** — an operator's severity over the model's disagrees;
* **closing** — judged by the network, not a person: a stale-cleared alarm raising again
  disagrees; none within :data:`CLOSE_JUDGE_S` agrees.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type-only, no runtime edge
    from netcorenoc.engine.operate.engine import Engine

__all__ = ["CLOSE_JUDGE_S", "judge"]

CLOSE_JUDGE_S = 3600.0  # a stale-clear that holds this long agreed with the network


async def judge(engine: Engine, now: float) -> None:
    """Give every pending decision a verdict from what happened after it, where something has."""
    store = engine.store
    for d in await store.autonomy_decisions(pending=True, limit=500):
        sid, at, grade = int(d["situation_id"]), float(d["at"]), str(d["grade"])
        if grade == "closing":
            raised = await store.any_reactivated(d["explanation"].get("stale_cleared", []), at)
            if raised:
                await store.judge_autonomy_decision(
                    int(d["id"]), "disagreed", now, "network", "a stale-cleared alarm raised again"
                )
            elif now - at >= CLOSE_JUDGE_S:
                await store.judge_autonomy_decision(
                    int(d["id"]),
                    "agreed",
                    now,
                    "network",
                    "no stale-cleared alarm raised again within an hour",
                )
            continue
        after = await store.situation_gestures_after(sid, at)
        if grade == "severity":
            row = await store.situation_severity(sid)
            if row is not None and row[1] is not None and not str(row[1]).startswith("model:"):
                await store.judge_autonomy_decision(
                    int(d["id"]),
                    "disagreed",
                    now,
                    str(row[1]),
                    "an operator set a different severity",
                )
                continue
        if not after:
            continue
        who = str(after[0]["actor"])
        contradicting = {
            "grouping": {"move", "merge", "operator_split", "split"},
            "naming": {"rename"},
            "severity": set(),
        }[grade]
        kinds = {
            ("split" if g["kind"] == "verdict" and g.get("verdict") == "split" else g["kind"])
            for g in after
        }
        if kinds & contradicting:
            await store.judge_autonomy_decision(
                int(d["id"]),
                "disagreed",
                now,
                who,
                f"an operator then did {sorted(kinds & contradicting)}",
            )
        else:
            await store.judge_autonomy_decision(
                int(d["id"]),
                "agreed",
                now,
                who,
                f"an operator worked it without contradicting it ({sorted(kinds)})",
            )
