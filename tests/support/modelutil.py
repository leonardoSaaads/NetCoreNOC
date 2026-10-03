"""A small hand-written link model and the helpers the decider and autonomy tests share.

The model stands in for the shipped one so those tests check *mechanics* — which family runs, what
it groups, what autonomy does with it — and never the shipped model's quality, which `make train`
measures and `tests/model/test_league.py` pins.
"""

from __future__ import annotations

import hashlib
import json

from netcorenoc.engine.model import gam
from netcorenoc.engine.operate.engine import Engine
from netcorenoc.ingest.events import TrapEvent, Varbind
from netcorenoc.store import Store

__all__ = ["TEST_MODEL", "T", "ingest", "manifest", "open_groups", "trap"]

T = 1_800_000_000.0

TEST_MODEL = json.dumps(
    {
        "features": ["dt", "same_ne", "same_class", "oid_arcs"],
        "format": gam.FORMAT,
        "grouping": {"join_bias": 0.0, "merge_bias": 1.0, "merge_min_pairs": 3.0},
        "interactions": [],
        "intercept": -1.0,
        "shapes": [
            {"edges": [5.0, 60.0, 600.0], "feature": "dt", "scores": [2.0, 1.0, 0.0, -1.0]},
            {"edges": [0.5], "feature": "same_ne", "scores": [-1.0, 2.5]},
            {"edges": [0.5], "feature": "same_class", "scores": [-0.5, 1.5]},
            # 1271.2.1.x vs 1271.2.1.y share 9 arcs; incident A (1271.2.1) vs B (1271.2.9) share 8.
            {"edges": [8.5], "feature": "oid_arcs", "scores": [-1.5, 1.0]},
        ],
        "threshold": 0.0,
    },
    sort_keys=True,
    separators=(",", ":"),
)


def manifest(document: str) -> str:
    """The least manifest the loader accepts: the document's SHA-256."""
    digest = hashlib.sha256(document.encode()).hexdigest()
    return json.dumps({"artifact": {"sha256": digest, "kind": gam.KIND}})


def trap(device: str, oid: str, instance: str, ts: float) -> TrapEvent:
    return TrapEvent(
        device=device,
        trap_oid=oid,
        instance=instance,
        ts=ts,
        varbinds=[Varbind(oid="1.3.6.1.4.1.1271.9.1", kind="str", value=instance)],
    )


async def ingest(engine: Engine, store: Store, traps: list[TrapEvent]) -> None:
    async with store.lock:
        for one in traps:
            await engine._process(one)
        await store.commit()


async def open_groups(store: Store) -> list[set[int]]:
    """Every live situation's alarm ids."""
    cur = await store.conn.execute(
        "SELECT sa.situation_id, sa.alarm_id FROM situation_alarm sa JOIN situation s "
        "ON s.id = sa.situation_id WHERE s.status IN ('new','open') ORDER BY 1, 2"
    )
    out: dict[int, set[int]] = {}
    for sid, aid in await cur.fetchall():
        out.setdefault(int(sid), set()).add(int(aid))
    return list(out.values())
