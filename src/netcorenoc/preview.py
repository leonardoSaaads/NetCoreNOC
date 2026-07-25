"""Scorer preview: a bounded, read-only "what would these parameters do?" over recent alarms.

DECISIONS #48. Preview re-runs **only** the scoring and connected-components step over a bounded
snapshot of alarms already in the database. It does not re-ingest, does not re-learn, does not
import ``eval/`` (the corpus harness stays the dev/CI gate and never becomes a runtime
dependency), and **writes nothing** — no link, no situation, no learned state, no config row.

The learned matrices are an *input* to a what-if, not an output of it, so they are held fixed:
preview answers "how would this parameter set group my recent alarms *today*", not "where would
the system end up after the matrices adapt". That limit is real, is stated in the UI, and is why
the answer is called directional rather than authoritative.

It runs on the HTTP side, admin-only, rate-limited, bounded by both an alarm cap and a hard
wall-clock budget, and never touches the ingest path.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from netcorenoc.correlate import MAX_CANDIDATES, WINDOW_S, select_candidates
from netcorenoc.scoring import LinkFeatures, LinkScorer

MAX_PREVIEW_ALARMS = 5000  # bounded work: the newest N alarms, never the whole history
PREVIEW_TIMEOUT_S = 5.0  # hard wall-clock budget; a partial answer is refused, not truncated
# v0.7.0: **aliases**, not copies. These were independent literals in v0.6.0 that happened to equal
# the engine's; retuning `correlate.WINDOW_S` alone would then have left the what-if replaying a
# different window from the engine it claims to predict — a preview that lies is worse than none.
# The equality is now structural, and `select_candidates` makes the *rule* shared too
# (DECISIONS #61).
PREVIEW_WINDOW_S = WINDOW_S  # the correlation window preview replays over
PREVIEW_MAX_CANDIDATES = MAX_CANDIDATES  # the engine's per-event candidate cap


class PreviewTimeoutError(RuntimeError):
    """The what-if exceeded its wall-clock budget and was abandoned without an answer."""


@dataclass(frozen=True)
class PreviewAlarm:
    """The five facts preview needs about one stored alarm. Read-only, no payload."""

    alarm_id: int
    class_id: int
    ne_id: int
    entity_id: int
    ts: float


class _AffinityLookup(Protocol):
    """The learned-matrix reads preview needs — satisfied by ``learn.Learner``."""

    def class_affinity(self, a: int, b: int) -> float: ...

    def entity_affinity(self, a_entity: int, a_ne: int, b_entity: int, b_ne: int) -> float: ...


def partition(
    alarms: list[PreviewAlarm],
    scorer: LinkScorer,
    affinities: _AffinityLookup,
    *,
    deadline: float | None = None,
) -> tuple[dict[int, int], int]:
    """Re-partition ``alarms`` under ``scorer``: (alarm_id -> component id, link count).

    A faithful, read-only reproduction of what the engine does with the same alarms — and since
    v0.7.0 that fidelity is **structural**, not a coincidence: candidate selection is
    :func:`netcorenoc.correlate.select_candidates`, the same function the engine calls, over the
    same window length and the same cap (DECISIONS #61). What remains here is preview's own part:
    one score per candidate pair and union-find over the accepted links. Component ids are the
    smallest member alarm id, so the labelling is stable and two runs over the same input are
    identical.

    ``live`` is not passed: a preview snapshot is immutable and every entry in it is a candidate,
    whereas the engine's deque carries tombstones. That difference is the reason the helper takes
    a liveness set at all, and it is why this reproduces the engine's partition exactly on a
    tombstone-free snapshot — which is what ``store.recent_alarms_for_preview()`` returns.

    Deterministic by construction: the alarm order is fixed by the caller, no wall clock enters
    the scored path, and ``deadline`` (monotonic) only aborts — it never changes an answer.
    """
    parent: dict[int, int] = {a.alarm_id: a.alarm_id for a in alarms}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    links = 0
    window: list[PreviewAlarm] = []
    for i, new in enumerate(alarms):
        if deadline is not None and i % 64 == 0 and time.monotonic() > deadline:
            raise PreviewTimeoutError("preview exceeded its time budget")
        candidates = select_candidates(
            window,
            now=new.ts,
            window_s=PREVIEW_WINDOW_S,
            max_candidates=PREVIEW_MAX_CANDIDATES,
        )
        for old in candidates:
            result = scorer.score(
                LinkFeatures(
                    delta_t_s=abs(new.ts - old.ts),
                    class_i=new.class_id,
                    class_j=old.class_id,
                    class_affinity=affinities.class_affinity(new.class_id, old.class_id),
                    ne_i=new.ne_id,
                    ne_j=old.ne_id,
                    entity_affinity=affinities.entity_affinity(
                        new.entity_id, new.ne_id, old.entity_id, old.ne_id
                    ),
                )
            )
            if result.linked:
                links += 1
                union(new.alarm_id, old.alarm_id)
        window.append(new)
    return {a.alarm_id: find(a.alarm_id) for a in alarms}, links


def _groups(labels: dict[int, int]) -> dict[int, frozenset[int]]:
    grouped: dict[int, set[int]] = {}
    for alarm_id, component in labels.items():
        grouped.setdefault(component, set()).add(alarm_id)
    return {component: frozenset(members) for component, members in grouped.items()}


def diff_partitions(before: dict[int, int], after: dict[int, int]) -> dict[str, object]:
    """The structural delta between two partitions of the same alarms.

    Returns aggregate structure only — counts, and which groups merge or split, by alarm id.
    No alarm payload, no varbind, no device address: nothing the caller could not already read.
    """
    before_groups = _groups(before)
    after_groups = _groups(after)
    before_sets = {members: key for key, members in before_groups.items()}

    merged: list[dict[str, object]] = []
    split: list[dict[str, object]] = []
    for members in after_groups.values():
        sources = {frozenset(g) for g in before_groups.values() if g & members}
        if len(sources) > 1:
            merged.append(
                {
                    "size": len(members),
                    "from_groups": sorted(min(s) for s in sources),
                }
            )
    for members in before_groups.values():
        destinations = {frozenset(g) for g in after_groups.values() if g & members}
        if len(destinations) > 1:
            split.append(
                {
                    "size": len(members),
                    "into_sizes": sorted(len(d & members) for d in destinations),
                }
            )
    unchanged = sum(1 for members in after_groups.values() if members in before_sets)
    return {
        "situations_before": len(before_groups),
        "situations_after": len(after_groups),
        "unchanged_groups": unchanged,
        "merged": sorted(merged, key=lambda m: (-int(str(m["size"])), str(m["from_groups"]))),
        "split": sorted(split, key=lambda s: -int(str(s["size"]))),
    }
