"""**The simulated operator**, and the plan is explicit that the report may never call it one.

Named `labelling` and not `operator` because `eval/simulation/` lands on `sys.path` when a script in
it is run directly, and a module called `operator.py` there **shadows the standard library's** — the
first thing `collections` imports. The failure is a circular-import traceback from inside `enum`,
fifty lines from anything this package wrote, which is a good reason to never take that name.

`PREREGISTRATION-0.14.0.md` §5.2:

> **A label's content is a decision function of the generator's ground truth and is recorded as
> such.** It is a *simulated operator*, and the report never calls it an operator.

Split from `drive.py` at the 400-line rule, on the seam the package already had: this module answers
*what a label says and who says it*, and `drive.py` answers *what the loop does with the answer*.
`drive_http.py` implements the same rule against the HTTP route, and the two are compared rather
than assumed equal — which is the only reason having them both is worth the duplication.

## The rule, fixed before any verdict was seen and unchangeable after

* every member of the bag carries **one** truth key -> **`confirm`**;
* the members carry **two or more** -> **`split`**, marking the members of the **minority** key as
  excluded.

The marking is the whole point: `excluded_reconciled >= 1` on a `split` is what makes a bag
*asserting*, which is the unit `PREREGISTRATION-0.10.0.md` §2.2 floors on. A `split` with nothing
marked asserts that the bag is wrong and says nothing about *which* pairs, so it cannot support the
fourth quantity — and §5.4 forbids changing this rule now that a verdict has been observed.

Three principals, round-robin, so the operator-concentration ceiling of `PREREGISTRATION-0.9.0.md`
is met **by construction rather than by luck**: a third each, whatever the corpus size.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "eval"))

from netcorenoc.engine.dataset.labels import Exclusion, LabelContext, LabelScope  # noqa: E402
from simulation.generator import generate  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - type-only
    from netcorenoc.main import Engine
    from netcorenoc.store import Store

__all__ = ["PRINCIPALS", "label_increment", "members_with_truth", "truth_of"]

PRINCIPALS = ("sim-alice", "sim-bob", "sim-carol")


def truth_of(increment: int) -> dict[tuple[str, str], str]:
    """`(device, trap OID) -> situation_key` for one increment. **Ground truth, for labelling.**

    Keyed on **(device, class)** rather than on the entity key. The generator sets a discriminator
    varbind to its entity key, but what the engine *stores* in `alarm.instance` is its own heuristic
    at level 0 and the promoted discriminator only after a promotion — so a lookup by entity key
    misses and every member falls back to a per-device placeholder.

    **That correction did not change any number**, and saying so is the point of recording it: the
    loop was re-run after the change and produced byte-identical output, so the shortfall it was
    meant to explain was never caused by it. What causes the shortfall is measured in
    `docs/gates/v0.14.0-phase-7.md` §3 and is nothing to do with this key. The key is still the
    right
    one — `(device, class)` is unique per truth key in every registered shape, including
    `flap_during_incident` where two truth keys share one NE and are told apart only by trap class —
    but a fix that fixes nothing is worth exactly one sentence saying so.
    """
    out: dict[tuple[str, str], str] = {}
    for event in generate(increment)["events"]:
        out[(event["source"], event["trap_oid"])] = event["truth"]["situation_key"]
    return out


async def members_with_truth(
    store: Store, situation: int, truth: dict[tuple[str, str], str]
) -> list[tuple[int, str]]:
    """`[(alarm id, truth situation_key)]` for one formed situation, in the server's own order.

    A member whose truth does not resolve gets a placeholder keyed on its own device and class, so
    it counts as **its own** key rather than joining someone else's. An unresolved lookup that
    silently merged into a neighbour would turn a mixed bag into a pure one, which is the direction
    that hides a problem.
    """
    cursor = await store.conn.execute(
        "SELECT sa.alarm_id, d.ip, c.oid FROM situation_alarm sa "
        "JOIN alarm a ON a.id = sa.alarm_id JOIN device d ON d.id = a.device_id "
        "JOIN alarm_class c ON c.id = a.class_id "
        "WHERE sa.situation_id = ? ORDER BY sa.alarm_id",
        (situation,),
    )
    out: list[tuple[int, str]] = []
    for alarm_id, ip, oid in await cursor.fetchall():
        key = truth.get((str(ip), str(oid)))
        out.append((int(alarm_id), key or f"unknown-{ip}-{oid}"))
    return out


async def label_increment(
    engine: Engine, store: Store, truth: dict[tuple[str, str], str], at: float, start: int
) -> dict[str, int]:
    """One verdict per unlabelled situation, through `engine.apply_feedback`.

    **`apply_feedback` is what `POST /api/situations/{sid}/feedback` calls**, with a real
    `LabelContext` carrying a real `Exclusion` — so the `source = 'server'` reconciliation runs and
    `excluded_reconciled` is the server's own number rather than the client's word for it. F46 is
    the finding that made that distinction load-bearing and F48 is its demonstration.

    `drive_http.py` does the same over a socket, through the route, with a bearer token and an audit
    row. This path exists so ten increments can run without half an hour of wall clock, and the two
    are compared in `docs/gates/v0.14.0-phase-7.md` §2 rather than assumed to agree.
    """
    cursor = await store.conn.execute(
        "SELECT s.id FROM situation s "
        "WHERE s.merged_into IS NULL "
        "  AND NOT EXISTS (SELECT 1 FROM feedback f WHERE f.situation_id = s.id) "
        "ORDER BY s.id"
    )
    situations = [int(r[0]) for r in await cursor.fetchall()]
    counts = {"confirm": 0, "split": 0, "marked": 0, "bags": 0}
    for index, situation in enumerate(situations):
        members = await members_with_truth(store, situation, truth)
        if not members:
            continue
        by_key: dict[str, list[int]] = defaultdict(list)
        for alarm_id, key in members:
            by_key[key].append(alarm_id)
        exclusion = None
        if len(by_key) > 1:
            verdict = "split"
            minority = min(by_key.values(), key=lambda ids: (len(ids), ids[0]))
            exclusion = Exclusion.accept(sorted(minority), False)
            counts["marked"] += len(minority)
        else:
            verdict = "confirm"
        counts[verdict] += 1
        counts["bags"] += 1
        label = LabelContext(
            scope=LabelScope(policy_id=None, restricted=False),
            client=None,
            exclusion=exclusion,
            channel="organic",
        )
        async with store.lock:
            await engine.apply_feedback(
                situation,
                verdict,
                at + index,
                principal_ref=PRINCIPALS[(start + index) % len(PRINCIPALS)],
                role="editor",
                label=label,
            )
    return counts
