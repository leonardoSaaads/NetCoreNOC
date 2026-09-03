"""What an operator gesture asserts, and the append-only record of it.

**The release's subject, and `PREREGISTRATION-0.16.0.md` §2 is its specification.** Five gestures,
and the whole of this module is which of them says something about a *grouping* and which says
something about an *alarm*:

====================  ==========================================================  ==============
`confirm` / `split`   the existing verdict surface, unchanged                     bag / subset
**`move`**            A against members(S1) NEGATIVE; A against members(S2)       pair, both
                      POSITIVE                                                    signs
**`merge`**           every cross pair positive                                   bag by bag
**`operator_split`**  every cross pair negative                                   bag by bag
`manual_clear`        **nothing about the grouping**                              NO ROW
self-clear            **nothing about the grouping**                              NO ROW
====================  ==========================================================  ==============

**The last two are the ones a build gets wrong.** They speak about the *alarm lifecycle*, not about
*correlation*. Feeding them to the link scorer would be a signal about a different question doing
the work of a measurement about this one — the `incumbent_linked` prohibition in a new register, and
§1 of the plan extends it to them by name. They are recorded in full and produce **no link-training
row**, and `tests/test_evidence_boundary.py` fails if one ever appears.

## What each gesture writes, and why the two are not the same table

Every gesture writes a `situation_event`: who, when, what, the confidence, the bag provenance, and
**the membership at the instant of the gesture**. That record is complete and it is append-only.

Some gestures *also* write a `feedback` row, through the same `record_label` path a verdict has used
since v0.8.0 — but only where the gesture's bag-level assertion is **exactly** what that shape
means. A `move` and an `operator_split` say *"these members do not belong with the rest, and nothing
else"*, which is a `split` carrying marked members and is precisely DECISIONS #124's reading. A
`merge` has no such shape: a `confirm` on the merged situation would assert every pair inside each
*original* bag positive, which the operator did not say, so a merge writes no label and §10's rule
— ambiguity about what the operator asserted resolves to **less** — decides it.

That is also what makes the census move without the judge changing. `Store.asserting_bag_rows` is
untouched, and it now sees more rows because there are more gestures whose assertion has the shape
it counts.

## The one limitation, stated rather than worked around

`feedback` is `UNIQUE (situation_id, verdict)` — F36's bound, which caps a situation's total
influence on learned state at two applications however many times anyone posts. **A second move out
of the same situation therefore records its event and no second label.** That is a real loss and it
is not repaired here: F36 is deliberate, the second move asserts about a *different* bag (the first
one changed it), and loosening the index inside a feature release would trade a measured invariant
for an unmeasured one. `docs/findings.md` F89 carries it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from netcorenoc.engine.dataset.labels import member_digest
from netcorenoc.engine.dataset.provenance import BagProvenance, provenance
from netcorenoc.engine.model import confidence as confidence_rules
from netcorenoc.store.situation_events import ASSERTING_KINDS

if TYPE_CHECKING:  # pragma: no cover - type-only, no runtime edge (tests/test_layers.py)
    from netcorenoc.store import Store

__all__ = ["CHANNEL_OF", "Gesture", "Snapshot", "record", "snapshot"]

#: Gesture kind -> the `acquisition_channel` its label carries. **Extended, never repeated**
#: (DECISIONS #126): `move`, `merge` and `operator_split` join `organic`, `close`, and the two
#: verdict values, and every one of them is reported separately and never averaged.
#:
#: The two the appliance performs itself and the zombie clear map to `None`: a channel names how a
#: *label* was acquired, and these acquire none.
CHANNEL_OF: dict[str, str | None] = {
    "verdict": "organic",
    "move": "move",
    "merge": "merge",
    "operator_split": "operator_split",
    "manual_clear": None,
    "self_clear": None,
    "idle_close": None,
    "operator_close": None,
    "rename": None,
}


@dataclass(frozen=True)
class Snapshot:
    """One bag as it stood at the instant of a gesture: the ordered ids, and how it was held.

    Both halves are irrecoverable one second later — `situation_alarm` is mutated by the very
    gesture being recorded, and the scores behind `provenance` decay — which is `0008`'s first rule
    and the reason this is a record rather than a query.
    """

    situation_id: int
    alarm_ids: tuple[int, ...]
    provenance: BagProvenance


@dataclass(frozen=True)
class Gesture:
    """One operator gesture, before it is written. **Says what happened, decides nothing.**"""

    kind: str
    situation_id: int
    at: float
    actor: str | None = None
    role: str | None = None
    confidence: float | None = None
    peer_situation_id: int | None = None
    alarm_id: int | None = None
    #: The label row this gesture produced, when its assertion had a shape the label surface can
    #: hold. `None` is the common case and is not a failure: a `merge` deliberately writes none.
    feedback_id: int | None = None

    @property
    def asserts_about_grouping(self) -> bool:
        """Does this gesture say anything about **correlation**? The plan's §1 question.

        Two conditions, and both are the plan's: the *kind* must be one that speaks about a
        grouping at all, and the confidence must clear the registered floor. A zombie clear fails
        the first however certain the operator is, which is the point — certainty about a fact
        concerning an alarm is not evidence about a grouping.
        """
        return self.kind in ASSERTING_KINDS and confidence_rules.admits(self.confidence)


async def snapshot(store: Store, situation_id: int) -> Snapshot:
    """Read one bag and how it was held together, **before** the gesture mutates it.

    Three bounded reads on the shared connection. The caller holds `store.lock` — this runs inside
    `write_txn`, like every other write path — and it must run before the mutation, because after
    it the membership this records is gone.
    """
    members = await store.situation_member_ids(situation_id)
    edges, scores = await store.bag_links(situation_id)
    threshold = await store.situation_threshold(situation_id)
    return Snapshot(
        situation_id=situation_id,
        alarm_ids=tuple(members),
        provenance=provenance(members, edges, scores, threshold),
    )


async def record(
    store: Store,
    gesture: Gesture,
    subject: Snapshot,
    peer: Snapshot | None = None,
) -> int:
    """Append the event, its two membership snapshots, and its bag provenance. Returns the id.

    **`produces_training_rows` is written, not derived on read**, so the plan's prohibition is a
    value a query can count and a guard can assert rather than a rule restated in a `CASE`
    expression wherever the corpus is read. `manual_clear` and `self_clear` write 0 here whatever
    else is true of them, and `tests/test_evidence_boundary.py` injects the opposite to prove the
    guard sees it.

    The provenance recorded is the **subject's**: for a `move` that is the situation the alarm is
    leaving, whose grouping the operator is contradicting; for a `merge` it is the surviving
    situation. The peer's is not recorded a second time, because the peer's own gestures record it
    and a bag's provenance is a property of the bag rather than of the pair of them.
    """
    event_id = await store.add_situation_event(
        situation_id=gesture.situation_id,
        kind=gesture.kind,
        actor=gesture.actor,
        role=gesture.role,
        at=gesture.at,
        confidence=gesture.confidence,
        acquisition_channel=CHANNEL_OF[gesture.kind],
        feedback_id=gesture.feedback_id,
        peer_situation_id=gesture.peer_situation_id,
        alarm_id=gesture.alarm_id,
        member_count=len(subject.alarm_ids),
        member_digest=_digest(subject.alarm_ids),
        peer_member_count=None if peer is None else len(peer.alarm_ids),
        peer_member_digest=None if peer is None else _digest(peer.alarm_ids),
        produces_training_rows=int(gesture.asserts_about_grouping),
        **subject.provenance.as_columns(),
    )
    await store.add_event_members(event_id, "server", list(subject.alarm_ids))
    if peer is not None:
        await store.add_event_members(event_id, "peer", list(peer.alarm_ids))
    # **Promotion, for the same reason a label promotes.** A pair still in the sink has `lifecycle
    # = 'sink'` and the training join does not read it, so an assertion about pairs nobody promoted
    # would be an assertion about rows the corpus does not hold. `record_label` already promotes the
    # situation a *label* names; a gesture names one or two situations and neither is necessarily
    # that one — a merge writes no label at all — so the promotion happens here, once, for the bags
    # this gesture actually asserts about.
    #
    # Only when the gesture asserts something: a `manual_clear` and a `self_clear` promote nothing,
    # because promotion is what makes a pair readable by training and they may not produce a row.
    if gesture.asserts_about_grouping:
        await store.promote_for_situation(subject.situation_id, gesture.at)
        if peer is not None:
            await store.promote_for_situation(peer.situation_id, gesture.at)
    return event_id


def _digest(alarm_ids: tuple[int, ...]) -> str:
    """The bag's digest, through **the same function the label surface uses**.

    Imported rather than reimplemented: two digests over the same bag that disagreed because one
    joined with a comma and the other with a space would be a silent, permanent inconsistency
    between `feedback_member` and `situation_event_member`, and nothing would go red.
    """
    return member_digest(list(alarm_ids))
