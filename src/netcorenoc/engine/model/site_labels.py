"""Every label this site has produced, in one shape — what the judge and the site search read.

v0.27.0. Three sources assert something about a pair of alarms, and each arrives in its own shape:

* ``labelled_pairs`` — a verdict (`confirm`/`split`) on a situation's bag, through the label path;
* ``gesture_positive_pairs`` — the positive half of a `move` or a `merge` (and so of an accepted
  proposal, which *is* a merge), from the event's two snapshots;
* ``proposal_negative_pairs`` — the negative half of a **rejected** proposal (ADR #428).

v0.26.0 concatenated the first two and read ``verdict`` and ``feedback_id`` off every row, which
the gesture rows do not carry: the first `move` with a captured vector would have raised a
`KeyError` in the site judge. This module is the one place the three are made alike, so every
reader sees ``pair_id``, ``verdict``, ``feedback_id`` (the bag), ``source``, ``confidence``,
``label_at`` and ``incident`` on every row.
"""

from __future__ import annotations

from typing import Any

__all__ = ["label_pairs"]


async def label_pairs(store: Any) -> list[dict[str, Any]]:
    """The three sources, normalised. The caller holds ``store.lock``."""
    out: list[dict[str, Any]] = []
    for row in await store.labelled_pairs():
        out.append({**row, "source": "feedback"})
    for row in await store.gesture_positive_pairs():
        out.append(
            {
                "pair_id": int(row["pair_id"]),
                "verdict": "confirm",
                "feedback_id": int(row["event_id"]),
                "source": "gesture",
                "confidence": row.get("confidence"),
                "label_at": float(row["label_at"]),
                "incident": int(row["situation_id"]),
            }
        )
    out.extend(await store.proposal_negative_pairs())
    return out
