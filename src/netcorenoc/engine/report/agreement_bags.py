"""What a labelled bag **is**, and how one is read — split from `agreement.py` (v0.10.1, B1).

The seam is a real one rather than a size accident, and B1 is what made it load-bearing.
`agreement.py` owns **what is measured over a set of bags** — confirm rates, the cluster bootstrap,
the six conditional cuts, the operator anonymisation. This module owns **what a bag is and where one
comes from**: the row shape, the size bucketing, the query, and — since v0.10.1 — the resolution of
incident identity that reading one now requires.

Before B1 the second half was a `COALESCE` in the select list and there was nothing to own. The
query decided what an incident was, one hop deep, and no consumer could tell. Now reading a bag
means reading rows, reading merge edges, and resolving them through `netcorenoc.incidents` — a
different job from computing a rate, and the third time this project has split on this seam
(`bias.py`/`bias_labels.py`, `shadow.py`/`census.py`).

`agreement.py` re-exports `Bag`, `size_bucket` and `SIZE_ORDER`, because `agreement_report.py` and
`tests/test_agreement.py` have imported them from there since v0.9.0 and **a split is not a reason
to move a caller's import** — the courtesy `bias_labels.py` was given for `pct` (DECISIONS #139).

Aggregates only, and no row leaves this module in a shape that names anything: the query selects
counts, verdicts, a bucketed size and an anonymisable reference, and never an NE, an address, an OID
or a varbind.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from netcorenoc.incidents import resolve_all, stamp

if TYPE_CHECKING:  # pragma: no cover - type-only, no runtime edge (tests/test_layers.py)
    from netcorenoc.store import Store

__all__ = ["SIZE_ORDER", "Bag", "load_bags", "size_bucket"]


@dataclass(frozen=True)
class Bag:
    """One labelled bag, with everything the six cuts need and nothing else.

    ``pairs``/``accepted``/``storm_pairs`` are over the bag's **promoted** pairs. A bag with zero
    promoted pairs is not an error — `coverage='none'` and `coverage='empty'` are both real, both
    counted, and both excluded from the cuts that need pairs to have a meaning.
    """

    feedback_id: int
    verdict: str
    size: int
    operator: str
    scope_restricted: bool
    coverage: str
    provenance: str
    incident: int
    pairs: int
    accepted: int
    storm_pairs: int
    # v0.9.1. `organic` is a label given while browsing; `close` is one given while RESOLVING a
    # situation, which selects for resolved incidents — a different population, never averaged
    # with the first (DECISIONS #126).
    channel: str = "organic"

    @property
    def agreed(self) -> bool:
        """The champion agreed with this operator iff the operator confirmed the grouping."""
        return self.verdict == "confirm"

    @property
    def mixedness(self) -> str:
        """**The strongest cut.** Did this bag contain a decision the champion could have got wrong?

        `uniform-accept` means every pair scored above the threshold: the operator confirmed an
        outcome the champion could not have reached differently, so the confirm says nothing about
        the scorer's judgement. `mixed` means the bag spans the threshold — there the champion
        made a call.
        """
        if self.pairs == 0:
            return "no-pairs"
        if self.accepted == 0:
            return "uniform-reject"
        if self.accepted == self.pairs:
            return "uniform-accept"
        return "mixed"

    @property
    def storm_state(self) -> str:
        """Three states, not a majority rule: a threshold here would be an invented number."""
        if self.pairs == 0:
            return "no-pairs"
        if self.storm_pairs == 0:
            return "quiet"
        if self.storm_pairs == self.pairs:
            return "storm"
        return "partly"


def size_bucket(n: int) -> str:
    """Bag-size buckets. Fixed edges, because a data-dependent binning is not reproducible."""
    if n == 0:
        return "0 (empty bag)"
    if n == 1:
        return "1"
    if n == 2:
        return "2"
    if n <= 5:
        return "3-5"
    if n <= 10:
        return "6-10"
    if n <= 50:
        return "11-50"
    return "51+"


# The bucket order, fixed in code. Sorting the labels would give "1", "11-50", "2", "3-5", "51+" —
# alphabetical, deterministic, and wrong for a reader looking for a trend along size.
SIZE_ORDER = ["0 (empty bag)", "1", "2", "3-5", "6-10", "11-50", "51+", "unrecorded"]

_BAG_QUERY = """
SELECT f.id                                        AS feedback_id,
       f.verdict                                   AS verdict,
       COALESCE(f.member_count, -1)                AS member_count,
       COALESCE(f.principal_ref, '(unattributed)') AS principal,
       COALESCE(f.scope_restricted, 0)             AS scope_restricted,
       COALESCE(f.coverage, 'unrecorded')          AS coverage,
       COALESCE(f.capture_provenance, 'unrecorded') AS provenance,
       COALESCE(f.acquisition_channel, 'unrecorded') AS channel,
       f.situation_id                              AS situation_id,
       COUNT(p.id)                                 AS pairs,
       COALESCE(SUM(p.incumbent_linked), 0)        AS accepted,
       COALESCE(SUM(p.storm), 0)                   AS storm_pairs
FROM feedback f
LEFT JOIN dataset_pair p
       ON p.situation_id = f.situation_id AND p.lifecycle = 'dataset'
GROUP BY f.id
ORDER BY f.id
"""


async def load_bags(store: Store) -> list[Bag]:
    """Every labelled bag, with its promoted-pair summary. One query, `ORDER BY`-stable.

    ``member_count`` is `-1` where the column was never written — a pre-v0.8.0 label. That is a
    distinguishable state rather than a zero, because zero is itself meaningful here (a verdict on
    an already-merged situation) and conflating the two would put unrecorded bags in the
    `0 (empty bag)` bucket, which is a claim about them nobody measured.

    **The query no longer decides what an incident is** (v0.10.1, B1). It selected
    `COALESCE(s.merged_into, f.situation_id) AS incident`, which is **one hop**, and v0.10.0 named
    it as the last consumer still doing so rather than fixing it inside a move. Now it returns
    `situation_id` and the merge **edges** are resolved once by `netcorenoc.incidents.resolve_all` —
    the same function `store/shadow.py`, `census.py` and `bias.py` use, so the four cannot disagree
    about which incidents exist. The `situation` join is gone with it: nothing else here needed it.

    The hazard this closes is silent by construction. All four consumers agree at 37 on this corpus
    because every merge chain in it is exactly one hop; a corpus with a longer chain would make them
    differ with **nothing going red**, and two of the four are the estimator and the seal.
    """
    cur = await store.conn.execute(_BAG_QUERY)
    rows = [dict(r) for r in await cur.fetchall()]
    edges = await store.merge_edges()
    identity = resolve_all(sorted({int(r["situation_id"]) for r in rows}), edges)
    stamp(rows, identity)
    return [
        Bag(
            feedback_id=int(r["feedback_id"]),
            verdict=str(r["verdict"]),
            size=int(r["member_count"]),
            operator=str(r["principal"]),
            scope_restricted=bool(int(r["scope_restricted"])),
            coverage=str(r["coverage"]),
            provenance=str(r["provenance"]),
            channel=str(r["channel"]),
            incident=int(r["incident"]),
            pairs=int(r["pairs"]),
            accepted=int(r["accepted"]),
            storm_pairs=int(r["storm_pairs"]),
        )
        for r in rows
    ]
