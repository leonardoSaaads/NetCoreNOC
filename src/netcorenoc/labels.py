"""The verdict side of feedback capture: the bag, its provenance, and promotion.

Split from `capture.py` because these are **two different paths**, not two halves of one. Capture
proper runs on the ingest path, once per activation, under the batch lock, bounded by
`MAX_CANDIDATES`. Everything here runs once per **operator verdict** — thousands of times rarer,
on the HTTP write path, and reasoning about a human judgement rather than a correlation decision.
Keeping them in one module put the ingest path's hot loop and the label's provenance rules in the
same file for no reason beyond both being "capture".

**What is recorded here, and why each part exists**, is
`docs/architecture/FEEDBACK-DATASET-0.8-DRAFT.md` §2.2a, §5, §5b, §5c and §6.3.

The security rule that governs the whole module: the client-reported half is **untrusted input on a
write path that already produced F34, F35 and F39**. It is bounded, never rejected, and never used
to validate the existence of anything.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from netcorenoc.store.dataset import MAX_CLIENT_MEMBERS

if TYPE_CHECKING:  # pragma: no cover - type-only, no runtime edge
    from netcorenoc.capture import Capture
    from netcorenoc.store import FeedbackResult, Store

__all__ = [
    "MAX_CLIENT_MEMBERS",
    "ClientFingerprint",
    "LabelScope",
    "coverage",
    "member_digest",
    "record_label",
]


@dataclass(frozen=True)
class LabelScope:
    """The scope fingerprint of a label: **what the operator could actually see.**

    A scoped editor labels a *partial view* and cannot say which part, because the redaction
    deliberately carries no NE id, address or entity key. Without this the label is uninterpretable
    — and the resulting noise is **systematic, not random**: it correlates with the scope policy, so
    it does not average out with more data. It teaches a model the shape of the policy.
    """

    policy_id: int | None = None
    restricted: bool = False
    redacted_members: int = 0


@dataclass(frozen=True)
class ClientFingerprint:
    """What the UI says it rendered. **Evidence, and the untrusted half of the record (§5.4b).**

    Bounded, with the truncation *recorded* rather than silently applied — a silence would make the
    bound itself invisible in the data. Never rejected: rejection is the wrong primitive for an
    observation, and this is that argument one level down. And **never used to validate the
    existence of anything**: an alarm id the principal cannot see, or that does not exist, is
    recorded exactly as reported and changes nothing about the response, its status or its timing.
    """

    alarm_ids: list[int]
    updated_at: float | None = None
    truncated: bool = False

    @classmethod
    def accept(cls, alarm_ids: list[int], updated_at: float | None) -> ClientFingerprint:
        """Bound the report. **The only thing that can happen to it is truncation**, and that is
        recorded — there is no path here that raises, rejects, or inspects an id's meaning."""
        bounded = alarm_ids[:MAX_CLIENT_MEMBERS]
        return cls(bounded, updated_at, truncated=len(alarm_ids) > MAX_CLIENT_MEMBERS)


def coverage(bag: list[int], promoted: int) -> dict[str, Any]:
    """§6.3's three cases, as facts on the label row. **Never silent.**

    A bag of *n* members implies `n(n-1)/2` pairs. The sliding window and `MAX_CANDIDATES` mean
    some of them **may never have been evaluated** — a nine-member situation has thirty-six pairs
    and not all passed through `score_link`. A naive join would drop those silently and v0.9.0 would
    train believing it saw a whole situation, which is the same censoring this release exists to
    prevent, one level up.
    """
    expected = len(bag) * (len(bag) - 1) // 2
    if promoted == 0:
        state = "none"
    elif promoted >= expected:
        state = "full"
    else:
        state = "partial"
    return {"coverage": state, "coverage_found": promoted, "coverage_expected": expected}


def member_digest(alarm_ids: list[int]) -> str:
    """A stable digest over an **ordered** bag of member ids.

    Order is part of the record, so the digest is over the ordered list rather than a set: two bags
    with the same members in a different order are different observations of what the operator saw,
    and collapsing them would discard that. Comma-joined decimal rather than JSON so the digest
    never moves if a serializer's spacing changes.
    """
    return hashlib.sha256(",".join(str(a) for a in alarm_ids).encode()).hexdigest()


async def record_label(
    capture: Capture,
    store: Store,
    recorded: FeedbackResult,
    situation_id: int,
    ts: float,
    bag: list[int],
    *,
    scope: LabelScope | None,
    client: ClientFingerprint | None,
) -> None:
    """S4 + S6: the label's provenance, its bag, and promotion — recorded, never guessed.

    **The bag is the server's own**, taken from engine state at the instant of the verdict. It does
    not depend on the client, and it survives the merge that Phase 0 proved destroys the referent
    entirely: afterwards `feedback JOIN situation_alarm` returns nothing and the surviving situation
    holds the union of both bags with nothing distinguishing them.

    **An empty bag is written as empty.** A verdict posted to an already-merged situation answers
    200, writes a `feedback` row, and hands `learn.penalize()` an empty list — measured in Phase 0
    §2. Recording zero members makes that population countable for the first time, instead of
    indistinguishable from a real label.

    Degrades like every other capture path: a failure here loses the **annotation**, never the
    operator's verdict, which the caller has already recorded.
    """
    if not capture.enabled or recorded.id is None:
        return
    try:
        await store.add_feedback_members(recorded.id, "server", bag)
        promoted, _observations = await store.promote_for_situation(situation_id, ts)
        fields: dict[str, Any] = {
            "member_digest": member_digest(bag),
            "member_count": len(bag),
            # v0.8.0 writes `organic` on every row. The column exists so that if a later release
            # ever *solicits* labels, the two populations stay separable — solicited labels have a
            # deliberately different distribution, and mixing them destroys the bias
            # characterisation retroactively, for rows already written (§5b).
            "acquisition_channel": "organic",
            # Everything this release writes was acquired over the path v0.7.5 repaired.
            "capture_provenance": "current",
            "situation_opened_at": await store.situation_opened_at(situation_id),
            # Situation identity is NOT stable under `sid = min(sids)`, so the id at label time is
            # a fact that has to be recorded rather than read back later (§3.5c).
            "situation_id_at_label": situation_id,
            **coverage(bag, promoted),
        }
        if scope is not None:
            fields.update(
                scope_policy_id=scope.policy_id,
                scope_restricted=1 if scope.restricted else 0,
                scope_redacted_members=scope.redacted_members,
            )
        if client is not None:
            fields.update(
                client_member_digest=member_digest(client.alarm_ids),
                client_member_count=len(client.alarm_ids),
                client_updated_at=client.updated_at,
                client_truncated=1 if client.truncated else 0,
            )
            # Recorded exactly as reported. **Never validated against anything**: an id the
            # principal cannot see, or that does not exist, is written unchanged and changes nothing
            # about the response, its status or its timing. That is what keeps this from being an
            # existence oracle (§5a) — a security requirement, not a nicety.
            await store.add_feedback_members(recorded.id, "client", client.alarm_ids)
        await store.annotate_feedback(recorded.id, **fields)
    except Exception as exc:
        capture._degrade(exc)
