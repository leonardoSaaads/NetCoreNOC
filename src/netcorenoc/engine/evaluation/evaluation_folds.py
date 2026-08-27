"""The fold assignment an evaluation stores, so a promotion can cite rows rather than a memory.

v0.11.0, Phase 4. `DATA-LINEAGE.md` §4 specifies it; this module implements exactly that and nothing
adjacent.

## Why this is a separate module from `promotion.py`

**Split by size, and the seam it fell on is a real one** — which is the honest order of those two
facts (DECISIONS #165). `promotion.py` reached 415 lines against a 400-line guard, and the guard's
own failure message says *split the module*, not *allowlist it*. What made the split clean rather
than arbitrary is that the two halves answer to different specifications and change for different
reasons: this one to `DATA-LINEAGE.md` §4, and the gate to `PREREGISTRATION-0.11.0.md` §3 and §4.

## The measurement that makes this necessary

Fold assignment is a **pure function of the incident ids**; incident identity is
`incidents.resolve_all` over the merge edges; **the merge edges change when situations merge**; and
**no snapshot of the merge graph is retained anywhere**. `situation.merged_into` holds the *current*
state, there is no history table, no `merged_at`, and no audit event recording the edge.

So a bag labelled today belongs to incident *I*; tomorrow its situation is merged and it belongs to
*J*; `assign_folds` over a different id set produces a different rotation; **the bag moves to a
different fold**, and every fold-level number moves with it.

Until v0.11.0 nothing decided, so nothing broke. From this release **an admin approves a swap citing
an evaluation**, and a citation to a number nobody can reproduce is not evidence.

## What this buys, and what it does not

It makes the **stored** assignment stop moving. It does **not** reproduce the merge graph, so it
cannot explain *why* an incident moved, and it does **not** make the seal's own membership
reproducible — the second-order hazard `DATA-LINEAGE.md` §4 names, where the sealed set and the
estimator's exclusions are computed from two different maps taken at two different times.

Snapshotting the merge edges would fix all three. `DATA-LINEAGE.md` §5 names its cost — a second
copy of a live table, with the drift risk that implies — and **this release deliberately does not
choose it**. The deferral is recorded rather than left as an omission.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from netcorenoc.engine.evaluation.shadow_cv import FOLDS, REPEATS, assign_folds

if TYPE_CHECKING:  # pragma: no cover - type-only, no runtime edge (tests/test_layers.py)
    from netcorenoc.store import Store

__all__ = ["materialise_folds", "run_id_for"]


def run_id_for(incident_ids: list[int], params_hash: str) -> str:
    """A deterministic identifier for one evaluation run: **what was evaluated**, and **what
    evaluated it**.

    No clock and no counter, so two runs over the same corpus with the same model produce the same
    id and re-running is idempotent. A run id containing a timestamp would make an evaluation
    impossible to recognise as the one already stored.
    """
    ordered = ",".join(str(i) for i in sorted(set(incident_ids)))
    return hashlib.sha256(f"{params_hash}\n{ordered}".encode()).hexdigest()[:32]


async def materialise_folds(
    store: Store, *, incident_ids: list[int], params_hash: str, now: float
) -> str:
    """Write this evaluation's fold assignment and return its `run_id`. **Idempotent.**

    `DATA-LINEAGE.md` §4 is the argument and it is a measurement rather than a worry: fold
    assignment is a pure function of the incident ids; incident identity is `incidents.resolve_all`
    over the merge edges; **the merge edges change when situations merge**; and no snapshot of the
    merge graph is retained anywhere. A bag labelled today can belong to a different incident
    tomorrow, land in a different fold, and move every fold-level number with it.

    Until now nothing decided. From this release an admin approves a swap citing an evaluation, and
    a citation to a number nobody can reproduce is not evidence.

    **What this buys, stated honestly:** the *stored* assignment stops moving. It does not reproduce
    the merge graph, so it does not explain *why* an incident moved, and it does not make the seal's
    own membership reproducible. Snapshotting the edges would; `DATA-LINEAGE.md` §5 names that
    cost, and this release deliberately does not choose it.
    """
    run_id = run_id_for(incident_ids, params_hash)
    if await store.fold_assignment(run_id):
        return run_id  # already materialised; the ids and the model are the same, so is the answer
    assignments: list[tuple[int, int, int]] = []
    for repeat in range(REPEATS):
        for incident, fold in assign_folds(incident_ids, folds=FOLDS, repeat=repeat).items():
            assignments.append((incident, repeat, fold))
    await store.write_fold_assignment(run_id=run_id, assignments=assignments, created_at=now)
    return run_id
