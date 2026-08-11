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
    "Exclusion",
    "LabelContext",
    "LabelScope",
    "coverage",
    "member_digest",
    "record_label",
    "server_bag",
]

Item = tuple[int, int]  # (class_id, device_id) — `learn.Item`, restated to avoid the import


@dataclass(frozen=True)
class LabelScope:
    """The scope fingerprint of a label: **what the operator could actually see.**

    A scoped editor labels a *partial view* and cannot say which part, because the redaction
    deliberately carries no NE id, address or entity key. Without this the label is uninterpretable
    — and the resulting noise is **systematic, not random**: it correlates with the scope policy, so
    it does not average out with more data. It teaches a model the shape of the policy.

    **v0.9.2 (F47): it also carries WHICH members were hidden**, not only how many. A scoped editor
    may mark an id they were never shown — the redaction deliberately carries no alarm id, so the
    ids are guessable — and the resulting assertion is one they were not in a position to make.
    `hidden_members` is what lets `record_label` say how much of the *assertion* was blind, and it
    comes from the **same** `Perimeter.hidden_member_ids` read that produced `redacted_members`, so
    the two can never disagree (DECISIONS #137).

    `hidden_members` is transient: it is used to derive a count and is never itself stored. An
    empty set means *nothing was hidden* on a restricted scope as well as on an unrestricted one,
    and `restricted` is what distinguishes those.
    """

    policy_id: int | None = None
    restricted: bool = False
    redacted_members: int = 0
    hidden_members: frozenset[int] = frozenset()


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


@dataclass(frozen=True)
class Exclusion:
    """**Which members do not belong** — the assertion v0.9.1 exists to capture.

    A `split` says *"these members are at least two situations"* and does not say which. This says
    which, and it says **only** that: the pairs `marked`-by-`rest` are asserted negative, and the
    pairs *within* the remainder and *within* the marked set are **unknown** (DECISIONS #124). The
    arithmetic closes — `m*r + r(r-1)/2 + m(m-1)/2 = n(n-1)/2` — which is what makes "and nothing
    else" checkable rather than merely promised.

    Hostile input, on a write path that has already produced F34, F35 and F39, so it gets the
    treatment `ClientFingerprint` gets and for the same reasons: **bounded, never rejected, and
    never used to validate the existence of anything.** A marked id the principal cannot see, or
    that does not exist, is recorded exactly as reported and changes nothing about the response,
    its status or its timing.

    `remainder_together` is the operator's *separate* assertion about the rest, and `None` means
    **not asserted** — it is never inferred from the marking.
    """

    alarm_ids: list[int]
    remainder_together: bool | None = None
    truncated: bool = False

    @classmethod
    def accept(cls, alarm_ids: list[int], remainder_together: bool | None) -> Exclusion:
        """Bound the report. **The only thing that can happen to it is truncation**, and that is
        recorded — there is no path here that raises, rejects, or inspects an id's meaning."""
        bounded = alarm_ids[:MAX_CLIENT_MEMBERS]
        return cls(bounded, remainder_together, truncated=len(alarm_ids) > MAX_CLIENT_MEMBERS)

    def marked_positions(self, bag: list[int]) -> frozenset[int]:
        """The positions **in the server's own bag** that the operator marked.

        The intersection is where the untrusted half meets the trusted one, and it is deliberately
        silent: an id that is not in the bag — because it does not exist, or because the caller
        guessed it — simply contributes to nothing. It is still *recorded* verbatim as evidence;
        it just cannot assert anything about a pair that does not exist.
        """
        marked = set(self.alarm_ids)
        return frozenset(i for i, alarm_id in enumerate(bag) if alarm_id in marked)


@dataclass(frozen=True)
class LabelContext:
    """Everything about a verdict that is not the verdict itself.

    One object rather than four parameters threaded through two layers, so that adding a fifth is
    a change here instead of a change in `engine.apply_feedback`'s signature, its call site, and
    every test that constructs one. `engine.py` is `COHESION_EXEMPT` at a ceiling equal to its
    exact size, so a parameter list that grows once per release is a real cost there.
    """

    scope: LabelScope | None = None
    client: ClientFingerprint | None = None
    exclusion: Exclusion | None = None
    channel: str = "organic"

    def for_verdict(self, verdict: str) -> LabelContext:
        """Drop an exclusion that arrived on a `confirm`.

        A `confirm` already asserts **every** pair positive, so attaching negatives to one would
        record a contradiction as though it were evidence (DECISIONS #124). The request still
        succeeds and its status, body and timing are unchanged — the exclusion is simply not part
        of what a `confirm` asserts, which is the §10 rule that ambiguity about what the operator
        asserted resolves to *less*.
        """
        if verdict == "split" or self.exclusion is None:
            return self
        return LabelContext(self.scope, self.client, None, self.channel)

    def marked_positions(self, bag: list[int]) -> frozenset[int] | None:
        """The positions in the server's bag the operator marked, or `None` for no assertion.

        `None` is what keeps the plain-split path byte-identical to v0.9.0 (DECISIONS #125).
        """
        return None if self.exclusion is None else self.exclusion.marked_positions(bag)


async def server_bag(
    members: list[Any] | None, store: Store, situation_id: int
) -> tuple[list[Item], list[int]]:
    """**The bag the server holds at this instant**, from engine state or from the store.

    Two readings of one fact, and the fallback is not a nicety: `engine.members` holds only *open*
    situations, so a verdict on a closed or merged one has to come from `situation_alarm`. Both
    return the same two lists — the `(class, device)` items the learner works in, and the ordered
    alarm ids that are the label's evidence.

    Lives here rather than in `engine.py` because it is about **what a label records**, which is
    this module's subject, and because `engine.py`'s cohesion exemption covers the ingest path
    rather than code that happens to sit near it (DECISIONS #121's reasoning, applied again).
    """
    if members is not None:
        return [(m.class_id, m.device_id) for m in members], [m.alarm_id for m in members]
    rows = await store.situation_members(situation_id)
    return (
        [(int(r["class_id"]), int(r["device_id"])) for r in rows],
        [int(r["id"]) for r in rows],
    )


def coverage(bag: list[int], promoted: int) -> dict[str, Any]:
    """§6.3's three cases, as facts on the label row. **Never silent.**

    A bag of *n* members implies `n(n-1)/2` pairs. The sliding window and `MAX_CANDIDATES` mean
    some of them **may never have been evaluated** — a nine-member situation has thirty-six pairs
    and not all passed through `score_link`. A naive join would drop those silently and v0.9.0 would
    train believing it saw a whole situation, which is the same censoring this release exists to
    prevent, one level up.

    A **fourth** state the specification's three do not cover, found by
    `tests/test_bias.py::test_the_report_counts_the_zero_member_bag`: an **empty** bag. The three
    cases all presuppose a bag with members, and an empty one is the degenerate case Phase 0
    discovered — a verdict posted to an already-merged situation. Reported as `full` (which
    `promoted >= expected` makes vacuously true when `expected` is zero) it would tell a reader a
    complete situation was captured; reported as `none` it would suggest pairs existed and were not
    found. Neither is true, so it gets its own name and the report counts it separately.
    """
    if not bag:
        return {"coverage": "empty", "coverage_found": promoted, "coverage_expected": 0}
    expected = len(bag) * (len(bag) - 1) // 2
    if promoted == 0:
        state = "none"
    elif promoted >= expected:
        state = "full"
    else:
        state = "partial"
    return {"coverage": state, "coverage_found": promoted, "coverage_expected": expected}


def _assertion(exclusion: Exclusion, bag: list[int], scope: LabelScope | None) -> dict[str, Any]:
    """**The evidence boundary, as one function** (v0.9.2; F46, F47).

    Five columns, and the whole release is which side of the boundary each of them comes from.
    `docs/architecture/EVIDENCE-BOUNDARY-0.9.2.md` is the argument; this is the arithmetic.

    **Tier 1 — what the client said.** `excluded_count` and `excluded_truncated` are unchanged from
    v0.9.1, byte for byte. `excluded_count` is the length of the reported list and it is a
    measurement **of the client**, never of the evidence. It is NULL when the operator marked
    nothing, because "marked nothing" is a PLAIN split rather than an assertion about an empty set,
    and 0 would be indistinguishable from one.

    **Tier 2 — what the server reconciled.** `excluded_reconciled` is `|reported ∩ the server's own
    bag|`, and it is **exactly `len(marked_positions(bag))`** — the same value `learn.penalize` has
    always used. That is not a coincidence to be maintained but the point: the report and the
    learner now read one quantity, so they agree by construction. Distinct by position, hence by
    alarm id, so a client that sends `[5, 5, 5]` has three evidence rows and **one** mark.

    `excluded_reconciled = 0` with `excluded_count = 30` is a legal and meaningful row: the
    client reported thirty marks and **none of them named a member of the bag**. That is the label
    Gate 0 §1 measured moving a corpus total by +900 while asserting nothing, and after this release
    it asserts 0 and says so.

    **Tier 3 — whether the assertion could have been made.** `excluded_reconciled_out_of_scope`
    counts the reconciled marks that named a member the labeller could not observe. It is written
    only when a scope was resolved: `None` means **unknown**, and unknown is not zero. Zero is a
    real and common answer — an unrestricted scope hid nothing, so no mark could have been blind —
    and it must stay distinguishable from a label whose scope was never recorded at all.
    """
    reported = len(exclusion.alarm_ids)
    marked = exclusion.marked_positions(bag)
    fields: dict[str, Any] = {
        "excluded_count": reported or None,
        "excluded_reconciled": len(marked) if reported else None,
        "excluded_reconciled_source": "live" if reported else None,
        "excluded_truncated": 1 if exclusion.truncated else 0,
        # Three states, and NULL is the one that matters: the operator did not say.
        # Never inferred from the marking (DECISIONS #124).
        "remainder_together": (
            None if exclusion.remainder_together is None else int(exclusion.remainder_together)
        ),
    }
    if reported and scope is not None:
        fields["excluded_reconciled_out_of_scope"] = sum(
            1 for i in marked if bag[i] in scope.hidden_members
        )
    return fields


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
    label: LabelContext,
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
    scope, client, exclusion = label.scope, label.client, label.exclusion
    try:
        await store.add_feedback_members(recorded.id, "server", bag)
        promoted, _observations = await store.promote_for_situation(situation_id, ts)
        fields: dict[str, Any] = {
            "member_digest": member_digest(bag),
            "member_count": len(bag),
            # v0.8.0 wrote `organic` on every row and built this column for the day a second
            # channel existed. v0.9.1 is that day: a verdict recorded through `close` writes
            # `close`, because closing selects for RESOLVED incidents and that is a different
            # population from the one an operator browses and labels spontaneously
            # (DECISIONS #126). The two are reported separately and never averaged — mixing them
            # would destroy the bias characterisation retroactively, for rows already written.
            "acquisition_channel": label.channel,
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
        if exclusion is not None:
            fields.update(_assertion(exclusion, bag, scope))
            await store.add_feedback_exclusion(recorded.id, exclusion.alarm_ids)
        await store.annotate_feedback(recorded.id, **fields)
    except Exception as exc:
        capture._degrade(exc)
