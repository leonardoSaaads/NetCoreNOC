"""The sealed holdout: **constructed once, and deliberately not spent.**

v0.10.0, Workstream 2. `PREREGISTRATION-0.10.0.md` §3.3 fixes the construction and §4.3 fixes the
protections, both ratified at
`c03aef0181554c0c71482e57d03677f25964c3a5ac20a7bf1b1d74bff1ba1e01` before any of this existed.

## Why a holdout is reserved and then not opened

Measured, not assumed (`docs/gates/v0.10.0-phase-1.md` §3): adaptive selection over 12 queries on 37
incidents inflates a reported rate by a **median +11.1 percentage points when every candidate is
equally good** — when the entire gain is selection noise and there is no real difference to find.
Four releases (v0.10.0 → v0.13.0) tuning against one holdout is that scenario exactly.

> **Reserving later is impossible; spending later is always possible.**

At 37 incidents, opening the envelope buys a number with a ±14.5 p.p. interval against a detection
threshold of 25 p.p. — 30 p.p. by this build's own closed form — and costs the one-shot property
permanently. The asymmetry is the whole argument. **v0.10.0's query count is 0.**

## The four protections, and why each is structural

1. **Constructed once.** `holdout_seal.singleton` is `UNIQUE` and pinned to 1, so a second
   construction fails at SQLite. *A seal that can be rebuilt is a seal that can be rebuilt after
   seeing a result*, and the person rebuilding it would have a reason that felt good at the time.
2. **Unreadable from the estimator.** `netcorenoc.shadow_cv` — the estimator — does not import this
   module, and `tests/test_seal.py` asserts that by parsing the tree. The seal is built in its own
   phase, **before** the estimator, so the estimator is written against an interface that never
   offered the sealed ids: reading them is not a rule it must obey but a thing it cannot express
   (DECISIONS #145).
3. **Every access is a row.** Granted or refused, with the release, the plan hash, the purpose and
   the outcome. `HONEST-JUDGE-0.10-DRAFT.md` §4 could offer only *"a counter maintained by hand, in
   prose"*; this is that counter, made durable and unable to disagree with itself.
4. **A read requires a ratified plan already on record.** A release cannot look first and register
   after. What this cannot do is stop a determined author registering and reading in the same
   minute — and it does not pretend to. What it does is make that sequence **permanently visible**
   in an append-only log, which is the whole of what a process hazard can be defended against by
   mechanism.

Nothing here holds a lock; the caller holds `store.lock`, exactly as every other slow-loop step
does.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type-only, no runtime edge (tests/test_layers.py)
    from netcorenoc.store import Store

__all__ = [
    "HOLDOUT_FRACTION",
    "Access",
    "SealSummary",
    "construct",
    "digest_of",
    "plan_ordered_incidents",
    "ratify",
    "spend",
    "summary",
]

# The last third by earliest label, per §3.3(3). Fixed here and not varied afterwards: a fraction
# chosen after a result was seen would be a fraction chosen to suit it.
HOLDOUT_FRACTION = 3


def digest_of(incident_ids: list[int]) -> str:
    """SHA-256 over the **ordered** membership, comma-joined decimal.

    Ordered rather than set-based because the order is part of the construction (§3.3(2): earliest
    label, ties by incident id), so a seal holding the same incidents in another order was cut by a
    different rule and should not hash the same.

    Comma-joined decimal rather than JSON, for `labels.member_digest`'s reason one module over: the
    digest never moves because a serializer's spacing changed.
    """
    return hashlib.sha256(",".join(str(i) for i in incident_ids).encode()).hexdigest()


def plan_ordered_incidents(first_label_at: dict[int, float]) -> list[int]:
    """Every incident, ordered by the timestamp of its **earliest** label, ties by incident id.

    §3.3(2), and the tie-break is not a detail: label timestamps in this project's own corpus are
    one second apart and can collide outright, so an order that depended on a stable sort of equal
    keys would depend on the row order the store happened to return. Ties by id make the ordering a
    property of the data.
    """
    return sorted(first_label_at, key=lambda incident: (first_label_at[incident], incident))


def sealed_third(ordered: list[int]) -> list[int]:
    """The **last third** by that order — §3.3(3).

    `len - len // 3` rather than `2 * len // 3`, so the *kept* portion is the one that rounds up and
    the sealed portion never exceeds a third. On 37 incidents that is 12 sealed and 25 kept.
    """
    return ordered[len(ordered) - len(ordered) // HOLDOUT_FRACTION :]


@dataclass(frozen=True)
class SealSummary:
    """What a report may know about the seal **without** learning which incidents are in it.

    Every field here is safe to print. The membership is not, and it is not reachable from this
    object — a report that wanted it would have to call :func:`spend`, which logs.
    """

    exists: bool
    release: str = ""
    plan_sha256: str = ""
    created_at: float = 0.0
    digest: str = ""
    incident_count: int = 0
    corpus_incidents: int = 0
    query_count: int = 0

    @property
    def spent(self) -> bool:
        """**The release's headline discipline.** False in v0.10.0, asserted by test."""
        return self.query_count > 0


@dataclass(frozen=True)
class Access:
    """The outcome of asking for the sealed membership. `ids` is `None` unless `granted`."""

    granted: bool
    outcome: str
    ids: list[int] | None = None


async def summary(store: Store) -> SealSummary:
    """The seal as a report may describe it. **Reads no membership and logs nothing.**

    Logging a summary read would make the query count meaningless: the report prints the seal's
    state on every run, so a counter that moved when a report ran would be counting reports rather
    than counting the times the holdout was spent.
    """
    row = await store.seal_row()
    count = await store.query_count()
    if row is None:
        return SealSummary(exists=False, query_count=count)
    return SealSummary(
        exists=True,
        release=str(row["release"]),
        plan_sha256=str(row["plan_sha256"]),
        created_at=float(row["created_at"]),
        digest=str(row["digest"]),
        incident_count=int(row["incident_count"]),
        corpus_incidents=int(row["corpus_incidents"]),
        query_count=count,
    )


async def construct(
    store: Store,
    *,
    release: str,
    plan_sha256: str,
    now: float,
    first_label_at: dict[int, float],
) -> SealSummary:
    """Cut the seal. **Once. A second call is refused, not silently re-derived.**

    The refusal is the schema's, so it holds against a later method and against anyone with a SQLite
    prompt. This does not check-then-insert: it inserts, and the database says no. A check-then-act
    would be a race and, worse, a rule a later caller could forget.

    Raises whatever SQLite raises on the second attempt; the caller — the maintenance pass — is
    expected to treat that as *"already sealed"* rather than as an error, because it is.
    """
    ordered = plan_ordered_incidents(first_label_at)
    sealed = sealed_third(ordered)
    await store.construct_seal(
        release=release,
        plan_sha256=plan_sha256,
        created_at=now,
        digest=digest_of(sealed),
        incident_ids=sealed,
        corpus_incidents=len(ordered),
    )
    return await summary(store)


async def ratify(store: Store, *, release: str, plan_sha256: str, now: float) -> None:
    """Record a ratified pre-registration, so that release may later read the seal.

    Append-only and timestamped, so *"registered before the read"* is a durable fact rather than a
    claim. **Not counted as a query** — registering a plan is not spending the holdout, and
    :meth:`Store.query_count` excludes these rows explicitly.
    """
    await store.record_access(
        at=now,
        release=release,
        plan_sha256=plan_sha256,
        purpose="ratify",
        granted=True,
        outcome=f"pre-registration {plan_sha256[:12]}… recorded for {release}",
    )


async def spend(
    store: Store, *, release: str, plan_sha256: str, purpose: str, now: float
) -> Access:
    """**Read the sealed membership.** Every call appends exactly one row, granted or not.

    Three refusals, each logged with its own reason so the log distinguishes them:

    * **no seal** — nothing has been constructed, so there is nothing to spend;
    * **no ratified plan on record** — §4.3(3). A release cannot look first and register after;
    * *(the caller's own reasons, which are not this function's business)*.

    The name is `spend` rather than `read` deliberately. Reading a holdout is not a retrieval, it is
    an **expenditure**: it costs the one-shot property permanently, and a function called `read`
    invites a caller to do it twice.
    """
    row = await store.seal_row()
    if row is None:
        outcome = "refused: no seal has been constructed"
        await store.record_access(
            at=now,
            release=release,
            plan_sha256=plan_sha256,
            purpose=purpose,
            granted=False,
            outcome=outcome,
        )
        return Access(granted=False, outcome=outcome)

    if plan_sha256 not in await store.ratified_plan_hashes():
        outcome = (
            f"refused: no ratified pre-registration on record for {plan_sha256[:12]}…. "
            "A release cannot look first and register after."
        )
        await store.record_access(
            at=now,
            release=release,
            plan_sha256=plan_sha256,
            purpose=purpose,
            granted=False,
            outcome=outcome,
        )
        return Access(granted=False, outcome=outcome)

    ids = await store.sealed_incident_ids(plan_sha256=plan_sha256)
    await store.record_access(
        at=now,
        release=release,
        plan_sha256=plan_sha256,
        purpose=purpose,
        granted=True,
        outcome=f"granted: {len(ids)} sealed incident id(s) returned",
    )
    return Access(granted=True, outcome=f"granted: {len(ids)} incident(s)", ids=ids)
