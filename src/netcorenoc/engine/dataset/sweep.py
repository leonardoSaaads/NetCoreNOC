"""The dataset's maintenance sweep: the retention bounds, and the drift verification.

Split out of :mod:`netcorenoc.engine.dataset.capture` in v0.20.0, at the 400-line guard and on a
seam that module's own first sentence draws: it is about *"turning one correlation decision into
rows, and never failing ingest"*. Neither of the two functions here does that. They run on the
`PRUNE_EVERY_TICKS` cadence, outside the capture path entirely, and what they are about is what
the appliance **destroys** and what it **checks** — a different question with different rules.

**Functions taking the capture object, not a mixin.** `Capture` is a dataclass, so a mixin's
annotations would become fields on it, and a mixin that read attributes it did not declare would
be untyped under `mypy --strict`. A :class:`Protocol` names exactly what these two need — three
attributes and the degradation hook — and `Capture` satisfies
it structurally with nothing added. The methods stay on `Capture` as two-line delegations because
`engine.py` and five tests call them there, and moving a call site is not what this split is
for.

**Named `sweep` and not `maintenance`** because `engine/operate/maintenance.py` already exists
and is the engine's maintenance *tick*. `tests/util.py` resolves a guard's subject by basename
and refuses an ambiguous one, which is the right refusal: two `maintenance.py` files make
*"which module does this guard read?"* unanswerable.

Both degrade the way everything in capture degrades: the error is counted and surfaced through
operator warnings, and the caller's next statement runs. A maintenance pass that raised would
also skip the learned-state flush behind it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime, types only
    from netcorenoc.engine.dataset.retention_policy import RetentionPolicy
    from netcorenoc.store import Store


class CaptureState(Protocol):
    """What the maintenance pass reads and writes on the capture object, and nothing more."""

    enabled: bool
    audit_swept: dict[str, int]
    drift_rows: int

    def _degrade(self, exc: Exception) -> None: ...


async def prune(
    capture: CaptureState, store: Store, now: float, retention: RetentionPolicy
) -> None:
    """The maintenance-time dataset pass: the policy's two **background** bounds, then one
    **verification**.

    The bounds are the sink's dual bound (age, then a row cap — unchanged from v0.8.0) and the
    **audit bound**, the outer edge of the data's life and the only background path that may
    delete a human label.

    **The training tier is deliberately absent**: it *selects* rather than deletes
    (DECISIONS #110). v0.8.0's directive 9 — this loop must never *silently* destroy labels —
    is satisfied rather than repealed, because the audit sweep destroys nothing the operator
    did not configure a bound for, and every deletion is counted here and reported.

    Degrades exactly as capture does. A sweep that failed is a disk-space problem; a
    maintenance pass that raised would also skip the learned-state flush behind it.

    **Why the verification's call site is here** (v0.9.2): it belongs to the maintenance
    cadence, it must run inside the lock the pass already holds, and `engine.py` is
    `COHESION_EXEMPT` at a ceiling equal to its exact size, so that release could not add a call
    site to it. This is the one method the maintenance pass already calls on the dataset, on the
    `PRUNE_EVERY_TICKS` schedule the verification wants. Named here rather than left to be
    discovered.
    """
    if not capture.enabled:
        return
    try:
        await store.prune_sink(now - retention.sink_days * 86400.0, retention.sink_rows)
        swept = await store.prune_dataset_audit(now - retention.audit_days * 86400.0)
    except Exception as exc:
        capture._degrade(exc)
    else:
        for key, count in swept.items():
            capture.audit_swept[key] = capture.audit_swept.get(key, 0) + count
    await verify_evidence(capture, store)


async def verify_evidence(capture: CaptureState, store: Store) -> None:
    """Recompute the reconciled exclusion count from the child tables and **report** drift.

    The denormalized `feedback.excluded_reconciled` is a **rebuildable copy**;
    `feedback_exclusion` and `feedback_member(source='server')` remain the source of truth. So
    the system carries a reconciliation query and drift monitoring rather than trusting the
    copy — which is the ordinary discipline for a denormalized aggregate, applied literally.

    **It does not correct, and that is the decision rather than an omission** (DECISIONS #134).
    A disagreement means a **write path is broken**. Repairing the row silently would destroy
    the evidence of that, which is the entire reason v0.9.2 exists: had this check shipped
    in v0.9.1 as a corrector, F46 would have been invisible — every hostile row quietly repaired
    on the next pass, the reports looking right, and the write path staying broken indefinitely.

    Surfaced through `Capture.warnings`, and counted durably by `dataset bias`, which recomputes
    it from the database on every run. **No audit row**: the audit catalog is frozen and this
    adds no action to it, and a detection that changes no behaviour is not an event in the sense
    the catalog records. The report is the durable record; the warning is the alert.

    Degrades like everything else here. A verification that raised would take the maintenance
    pass with it, which would be a worse outcome than an unverified sweep.
    """
    if not capture.enabled:
        return
    try:
        capture.drift_rows = len(await store.reconciliation_drift())
    except Exception as exc:
        capture._degrade(exc)
