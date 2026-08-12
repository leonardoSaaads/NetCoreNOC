"""Assembling the shadow-mode report: everything it states, read from the store.

`shadow_eval.py` computes the metrics; this reads the store and puts the document together.
**Rendering lives in `shadow_render.py`** — split there in v0.9.2 (DECISIONS #139) when this module
sat at exactly its 400-line ceiling and the evidence boundary needed one more line in the rendered
output. The seam is the one `bias.py` / `bias_report.py` has always used: a reader that produces a
mapping, and a renderer that turns a mapping into text. Neither half imports the other.

Output is **byte-stable** for a given database — no clock, no unordered aggregate — because
`tests/test_shadow.py` compares the rendered result against a frozen expectation.

**It leads with the sufficiency verdict**, before any coefficient, because *"not yet, and here is
when"* is the release's most likely and most useful result.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from netcorenoc import census, shadow, shadow_eval, training
from netcorenoc.challenger import Coefficients, LogisticScorer
from netcorenoc.scoring import AdditiveScorer, LinkFeatures
from netcorenoc.seal import summary as seal_summary

if TYPE_CHECKING:  # pragma: no cover - type-only, no runtime edge
    from netcorenoc.store import Store

__all__ = ["ADMISSION_BUDGET_RATIO", "collect"]

# The challenger's p99 may not exceed this multiple of the champion's, measured in the same process
# at the same time. A RATIO rather than a microsecond budget because Phase 0 §5 measured the
# champion's own p99 moving 2.6x between two runs on one machine — an absolute budget would be a
# measurement of the scheduler. Ten is generous on purpose: the filter exists to exclude a model
# that threatens ingestion, not to hold a stopwatch to arithmetic.
ADMISSION_BUDGET_RATIO = 10.0


async def collect(store: Store, *, include_legacy: bool = False) -> dict[str, Any]:
    """Everything the report states. Caller holds ``store.lock``. **Trains nothing.**

    This is a *reader*. It re-derives the challenger's opinion offline from stored features and
    from the coefficients a run recorded; it never fits, and it never writes.
    """
    bags = await store.labelled_bags(include_legacy=include_legacy)
    pairs = await store.labelled_pairs(include_legacy=include_legacy)
    # Workstream 1: incident identity is resolved ONCE, through the one implementation, and
    # stamped onto both lists from the same map. This call site and `shadow.Shadow.run`'s are
    # the two the plan's §3.3 warns must not compute it separately.
    identity = await census.resolve_identity(store, bags, pairs)
    pre_v080_merges = await store.pre_v080_merges()
    # The seal SUMMARY, never its membership. `seal.summary` reads no member row and
    # logs nothing — a counter that moved when a report ran would be counting reports
    # rather than counting the times the holdout was spent.
    holdout = await seal_summary(store)
    holdout_attempts = len(await store.access_log())
    run = await store.latest_challenger_run()
    stats = census.corpus_stats(bags, identity)
    floors, floors_warning = training.resolve_floors(await store.get_meta(shadow.FLOORS_META_KEY))
    verdict = training.assess(stats, floors)
    # `sum(m * (n - m))` over labels carrying an exclusion: the pairs an operator ASSERTED
    # negative, and a quantity that was zero for every corpus before v0.9.1.
    #
    # **`m` is the RECONCILED count from v0.9.2 (F46)**, never the client's list length. This was
    # the third of the three consumers that multiplied untrusted input, and it is the one closest
    # to a decision: it prints beside the pre-registered floors, where v0.10.0 is most likely to
    # promote it into one. The client's own figure is printed beside it, under a name that says
    # whose measurement it is (`docs/architecture/EVIDENCE-BOUNDARY-0.9.2.md` §3).
    cur = await store.conn.execute(
        "SELECT COALESCE(SUM(excluded_reconciled * (member_count - excluded_reconciled)), 0), "
        "       COALESCE(SUM(excluded_count), 0) FROM feedback "
        "WHERE excluded_reconciled IS NOT NULL AND member_count IS NOT NULL"
    )
    negatives = await cur.fetchone()

    out: dict[str, Any] = {
        "run": run,
        "stats": stats,
        "floors": floors,
        "pre_v080_merges": pre_v080_merges,
        "holdout": holdout,
        "holdout_attempts": holdout_attempts,
        "floors_warning": floors_warning,
        "verdict": verdict,
        # v0.9.1 OBSERVATIONS, not floors — see the prose in `render`. Printed beside the
        # floors and flooring nothing, because replacing a registered floor after seeing the data
        # is what pre-registration exists to prevent (HONEST-JUDGE §5).
        "split_and_mixed": sum(
            1 for b in bags if b["verdict"] == "split" and 0 < int(b["accepted"]) < int(b["pairs"])
        ),
        "asserted_negatives": 0 if negatives is None else int(negatives[0]),
        "client_reported_marks": 0 if negatives is None else int(negatives[1]),
        "include_legacy": include_legacy,
        "shadow_stats": await store.shadow_stats(),
        "skew": _skew(await store.shadow_skew_rows(), await store.challenger_runs(limit=50)),
        "policies": {},
        "admission": {},
    }

    champion_linked = shadow_eval.champion_decisions(pairs)
    out["champion_partition"] = shadow_eval.evaluate(bags, pairs, champion_linked)

    coefficients = _coefficients_of(run)
    if coefficients is not None:
        for policy in ("A", "B"):
            rows, diagnostics = training.derive(
                [shadow.labelled_pair(row) for row in pairs],
                policy,
            )
            fitted, fit_diagnostics = await training.fit(rows)
            scorer = LogisticScorer(fitted)
            linked, probability = shadow_eval.challenger_decisions(scorer, pairs)
            curve, brier = shadow_eval.calibration(
                bags, shadow_eval.bag_probabilities(pairs, probability)
            )
            out["policies"][policy] = {
                "coefficients": fitted,
                "fingerprint": scorer.params_fingerprint(),
                "partition": shadow_eval.evaluate(bags, pairs, linked),
                "calibration": curve,
                "brier": brier,
                **diagnostics,
                **fit_diagnostics,
            }
        samples = _samples(pairs)
        if samples:
            champion = shadow_eval.admission(
                AdditiveScorer(), samples, budget_ratio=ADMISSION_BUDGET_RATIO
            )
            challenger = shadow_eval.admission(
                LogisticScorer(coefficients), samples, budget_ratio=ADMISSION_BUDGET_RATIO
            )
            admitted, reasons = shadow_eval.verdict(challenger, champion)
            out["admission"] = {
                "champion": champion,
                "challenger": challenger,
                "admitted": admitted,
                "reasons": reasons,
            }
    return out


def _coefficients_of(run: dict[str, Any] | None) -> Coefficients | None:
    if run is None or not run.get("coefficients"):
        return None
    import json

    return Coefficients.from_dict(json.loads(str(run["coefficients"])))


def _samples(pairs: list[dict[str, Any]]) -> list[LinkFeatures]:
    """A bounded, deterministic feature sample for the admission filter — every Nth stored pair."""
    step = max(1, len(pairs) // 5000)
    return [
        LinkFeatures(
            delta_t_s=float(p["delta_t_s"]),
            class_i=0,
            class_j=0,
            class_affinity=float(p["class_affinity"]),
            ne_i=0,
            ne_j=0,
            entity_affinity=float(p["entity_affinity"]),
        )
        for p in pairs[::step]
    ]


def _skew(rows: list[dict[str, Any]], runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare each sampled **online** opinion against its **offline** reconstruction.

    Reconstructed with the coefficients of the run that *wrote* the opinion, not the current ones —
    a re-fit between the two would otherwise show up as skew, which would be a false positive on
    the one test in this release that must not have any.

    Compared with `==` on the float, not with a tolerance. The pre-registered expectation is
    **0.0000 %**, and a non-zero rate means the model-quality figures elsewhere in this report
    describe features that were never served.
    """
    by_run = {int(r["id"]): _coefficients_of(r) for r in runs if r.get("coefficients") is not None}
    compared = diverged = unmatched = 0
    for row in rows:
        if row.get("delta_t_s") is None:
            unmatched += 1
            continue
        coefficients = by_run.get(int(row["challenger_run_id"]))
        if coefficients is None:
            unmatched += 1
            continue
        offline, _linked = shadow_eval.reconstruct(LogisticScorer(coefficients), row)
        compared += 1
        if offline != float(row["online_score"]):
            diverged += 1
    return {
        "opinions": len(rows),
        "compared": compared,
        "diverged": diverged,
        "unmatched": unmatched,
        "rate_pct": (100.0 * diverged / compared) if compared else None,
    }
