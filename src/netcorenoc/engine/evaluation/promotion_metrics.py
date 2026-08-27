"""The four named quantities, for **both arms**, with a cluster bootstrap over incidents.

v0.14.0. `PREREGISTRATION-0.10.0.md` §5 names them and §2.6(d) fixes how the fourth is aggregated;
`PREREGISTRATION-0.11.0.md` §2 item 4 requires both arms to come from **one code path**, because
a challenger number with no champion number beside it is not a comparison.

## Why this module exists at all

v0.11.0's `routes_promotion._derived_inputs` returned four **degenerate** quantities — every rate
0.0, every interval a point — and said so honestly in `unavailable`: this project's corpus supplies
zero asserted negative pairs, so nothing could be computed and inventing a number would have been
fabrication. That was correct then and it is the reason the gate has never returned anything but
`INSUFFICIENT_EVIDENCE`.

v0.14.0 generates a corpus that **does** supply them, so the quantities have to be computed for
real. This is that computation, and it is a separate module rather than more of
`routes_promotion.py` for the reason `promotion.py` and `evaluation_folds.py` are separate: the HTTP
surface owns *what a request may assert* (nothing), and this owns *what the server derives*.

## Three properties the plan fixes and this implements literally

* **Per bag, never pooled over pairs.** One 501-member storm with 250 marks contributes 62 750
  pairs; a pooled rate would be that storm's rate wearing the corpus's name (§2.6(d)). Every
  quantity here is computed per bag or per incident and aggregated as a mean over the cluster.
* **The bootstrap resamples INCIDENTS**, never pairs and never bags — `shadow_cv.cluster_bootstrap`
  does that and this module's only job is to hand it `{incident: [values]}`.
* **The four are never composed.** There is no expression here that adds, averages or ranks them
  against each other. `promotion.deciding_quantity` picks one to *name*; nothing combines them.

## The champion's decisions come from `incumbent_linked`, which is legitimate here and only here

`shadow_eval.champion_decisions` reads it as a **comparison basis**. The target throughout is the
operator's verdict, and the invariant — *no metric that decides promotion may be computed against
`incumbent_linked`* — is about using it as **truth**, not about putting the champion's partition
beside the challenger's. `shadow_eval.py`'s own docstring draws that line and this module stays on
the same side of it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from netcorenoc.engine.dataset.census import resolve_identity
from netcorenoc.engine.evaluation import shadow_assertions, shadow_eval
from netcorenoc.engine.evaluation.promotion import QUANTITY_NAMES, Metrics, Quantity
from netcorenoc.engine.evaluation.shadow_cv import Interval, cluster_bootstrap

if TYPE_CHECKING:  # pragma: no cover - type-only, no runtime edge (tests/test_layers.py)
    from netcorenoc.engine.correlate.scorer_contract import LinkScorer
    from netcorenoc.store import Store

__all__ = ["Measured", "measure"]

_EMPTY = Interval(0.0, 0.0, 0.0, 0)


class Measured:
    """The four quantities for both arms, plus the diagnostics a report may not omit."""

    __slots__ = ("diagnostics", "metrics", "unavailable")

    def __init__(
        self, metrics: Metrics, diagnostics: dict[str, int], unavailable: list[str]
    ) -> None:
        self.metrics = metrics
        self.diagnostics = diagnostics
        self.unavailable = unavailable


def _by_incident(
    rows: list[dict[str, Any]], key: str, incident_of: Mapping[int, int]
) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        situation = int(row[key])
        out[incident_of.get(situation, situation)].append(row)
    return out


def _partition_rates(
    bags_by_incident: dict[int, list[dict[str, Any]]],
    pairs_by_incident: dict[int, list[dict[str, Any]]],
    accepted: dict[int, bool],
) -> tuple[dict[int, list[float]], dict[int, list[float]], dict[int, list[float]]]:
    """Three `{incident: [rate]}` maps — over-merge, under-merge, split-bag-intact.

    Scored **per incident** rather than over the whole corpus, because the bootstrap resamples
    incidents and a single corpus-wide number has nothing to resample. An incident whose bags cannot
    support a rate — no `confirm` bag for the merge rates, no `split` bag for the intact rate —
    contributes **nothing** rather than a zero: a rate that is *not computable* is not a rate of
    zero, and `docs/ROADMAP.md`'s "Found while building v0.11.0" records what conflating the two
    cost this project once.
    """
    over: dict[int, list[float]] = {}
    under: dict[int, list[float]] = {}
    intact: dict[int, list[float]] = {}
    for incident, bags in sorted(bags_by_incident.items()):
        score = shadow_eval.evaluate(bags, pairs_by_incident.get(incident, []), accepted)
        if score.over_merge is not None:
            over.setdefault(incident, []).append(score.over_merge)
        if score.under_merge is not None:
            under.setdefault(incident, []).append(score.under_merge)
        if score.split_bag_intact_rate is not None:
            intact.setdefault(incident, []).append(score.split_bag_intact_rate)
    return over, under, intact


def _components_for(
    asserting: list[shadow_assertions.AssertingBag],
    pairs_by_bag: dict[int, list[dict[str, Any]]],
    accepted: dict[int, bool],
) -> dict[int, dict[int, int]]:
    """`feedback_id -> {alarm -> component}` under one arm's decisions.

    `shadow_eval.partition` is the union-find both arms go through, so the challenger's partition
    and the champion's are the same function of different decisions — which is the property that
    makes the two numbers comparable at all.
    """
    out: dict[int, dict[int, int]] = {}
    for bag in asserting:
        rows = pairs_by_bag.get(bag.feedback_id, [])
        edges = [
            (int(r["alarm_a"]), int(r["alarm_b"]))
            for r in rows
            if accepted.get(int(r["pair_id"]), False)
        ]
        out[bag.feedback_id] = shadow_eval.partition(edges, set(bag.members))
    return out


async def _asserting_bags(
    store: Store, incident_of: Mapping[int, int]
) -> tuple[list[shadow_assertions.AssertingBag], dict[int, list[int]]]:
    """The asserting bags, built from the **server-derived** columns only (F46).

    `excluded_reconciled` rather than `excluded_count`, and the marked set is recovered from the
    bag's own member order — `Exclusion.marked_positions` is what wrote the count, so the positions
    are the first `excluded_reconciled` members the server itself reconciled.
    """
    rows = await store.asserting_bag_rows()
    out: list[shadow_assertions.AssertingBag] = []
    members_of: dict[int, list[int]] = {}
    for row in rows:
        situation = int(row["situation_id"])
        cursor = await store.conn.execute(
            "SELECT alarm_id FROM situation_alarm WHERE situation_id=? ORDER BY alarm_id",
            (situation,),
        )
        members = [int(r[0]) for r in await cursor.fetchall()]
        marked_count = int(row["excluded_reconciled"])
        hidden_count = int(row["scope_redacted_members"] or 0)
        out.append(
            shadow_assertions.AssertingBag(
                feedback_id=int(row["feedback_id"]),
                incident=incident_of.get(situation, situation),
                members=tuple(members),
                marked=frozenset(members[:marked_count]),
                hidden=frozenset(members[len(members) - hidden_count :] if hidden_count else []),
                coverage=str(row["coverage"]),
            )
        )
        members_of[int(row["feedback_id"])] = members
    return out, members_of


async def measure(store: Store, scorer: LinkScorer) -> Measured:
    """The four quantities for both arms. **The server derives every one of them.**

    Returns degenerate intervals and a stated reason where a quantity genuinely cannot be computed —
    never an invented number. *Inventing a quantity whose input is gone is fabrication and must be
    recorded as absent*, which is `routes_promotion`'s own rule and the one this module inherits.
    """
    bags = await store.labelled_bags()
    pairs = await store.labelled_pairs()
    identity = await resolve_identity(store, bags, pairs)
    incident_of = identity.incident_of

    unavailable: list[str] = []
    if not pairs:
        unavailable.append(
            "the four named quantities: this corpus supplies no promoted pairs, so no rate can be "
            "computed for either arm. They are recorded as absent rather than as zero."
        )
        empty = tuple(Quantity(name, _EMPTY, _EMPTY) for name in QUANTITY_NAMES)
        return Measured(Metrics(quantities=empty), {"pairs": 0}, unavailable)

    champion = shadow_eval.champion_decisions(pairs)
    # `challenger_decisions` is typed for `LogisticScorer` and works structurally for any
    # `LinkScorer`: it reads `.score(features).linked` and nothing else. The probability it also
    # returns is discarded here — it applies `sigmoid` unconditionally, which is right for a logit
    # and wrong for a tree whose output is already in [0, 1], and **no quantity below reads it**.
    challenger, _probability = shadow_eval.challenger_decisions(scorer, pairs)  # type: ignore[arg-type]

    bags_by_incident = _by_incident(bags, "situation_id", incident_of)
    pairs_by_incident = _by_incident(pairs, "situation_id", incident_of)
    pairs_by_bag: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for pair in pairs:
        pairs_by_bag[int(pair["feedback_id"])].append(pair)

    asserting, _members = await _asserting_bags(store, incident_of)
    eligible = [bag for bag in asserting if bag.eligible]
    if not eligible:
        unavailable.append(
            "asserted_negative_respected_rate: no bag carries an observable assertion with usable "
            "coverage, so the fourth quantity is absent rather than zero."
        )

    arms: dict[str, dict[str, Interval]] = {}
    for arm, accepted in (("challenger", challenger), ("champion", champion)):
        over, under, intact = _partition_rates(bags_by_incident, pairs_by_incident, accepted)
        respected, _diagnostics = shadow_assertions.asserted_negative_respected_rate(
            eligible, _components_for(eligible, pairs_by_bag, accepted)
        )
        arms[arm] = {
            "over_merge_rate": cluster_bootstrap(over),
            "under_merge_rate": cluster_bootstrap(under),
            "split_bag_intact_rate": cluster_bootstrap(intact),
            "asserted_negative_respected_rate": cluster_bootstrap(respected),
        }

    metrics = Metrics(
        quantities=tuple(
            Quantity(name, arms["challenger"][name], arms["champion"][name])
            for name in QUANTITY_NAMES
        )
    )
    diagnostics = {
        "bags": len(bags),
        "pairs": len(pairs),
        "incidents": len(bags_by_incident),
        "asserting_bags": len(asserting),
        "asserting_bags_eligible": len(eligible),
        "asserting_incidents": len({bag.incident for bag in eligible}),
    }
    return Measured(metrics, diagnostics, unavailable)
