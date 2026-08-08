"""Assembling and rendering the shadow-mode report.

`shadow_eval.py` computes the metrics; this reads the store, puts the document together, and turns
it into the text an operator reads. Output is **byte-stable** for a given database — fixed field
widths, fixed float precision, no clock — because `tests/test_shadow.py` compares it against a
frozen expectation.

**The prose is not decoration.** This report is where a 99.8 % number would look like a triumph, and
nearly half the module is the sentences that stop it: that pairwise accuracy is the base rate, that
a `split` bag supports no truth partition, that a policy-B fit has no negative class at all, and
that a calibrated probability may route what an operator is asked and may never authorise autonomy.

**It leads with the sufficiency verdict**, before any coefficient, because *"not yet, and here is
when"* is the release's most likely and most useful result.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from netcorenoc import shadow, shadow_eval, training
from netcorenoc.challenger import FEATURE_NAMES, Coefficients, LogisticScorer
from netcorenoc.scoring import AdditiveScorer, LinkFeatures

if TYPE_CHECKING:  # pragma: no cover - type-only, no runtime edge
    from netcorenoc.store import Store

__all__ = ["ADMISSION_BUDGET_RATIO", "collect", "render"]

_WIDTH = 62

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
    run = await store.latest_challenger_run()
    stats = shadow.corpus_stats(bags)
    floors, floors_warning = training.resolve_floors(await store.get_meta(shadow.FLOORS_META_KEY))
    verdict = training.assess(stats, floors)
    # `sum(m * (n - m))` over labels carrying an exclusion: the pairs an operator ASSERTED
    # negative, and a quantity that was zero for every corpus before v0.9.1.
    cur = await store.conn.execute(
        "SELECT COALESCE(SUM(excluded_count * (member_count - excluded_count)), 0) FROM feedback "
        "WHERE excluded_count IS NOT NULL AND member_count IS NOT NULL"
    )
    negatives = await cur.fetchone()

    out: dict[str, Any] = {
        "run": run,
        "stats": stats,
        "floors": floors,
        "floors_warning": floors_warning,
        "verdict": verdict,
        # v0.9.1 OBSERVATIONS, not floors — see the prose in `render`. Printed beside the
        # floors and flooring nothing, because replacing a registered floor after seeing the data
        # is what pre-registration exists to prevent (HONEST-JUDGE §5).
        "split_and_mixed": sum(
            1 for b in bags if b["verdict"] == "split" and 0 < int(b["accepted"]) < int(b["pairs"])
        ),
        "asserted_negatives": 0 if negatives is None else int(negatives[0]),
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


def _ok(flag: bool) -> str:
    return "SUFFICIENT" if flag else "INSUFFICIENT"


def _num(value: float | None, spec: str = "8.4f") -> str:
    """A fixed-width number, or a fixed-width `n/a`.

    Padded to the same width either way: a report whose columns move when a cell is empty is a
    report a reader cannot scan, and this one is compared byte-for-byte.
    """
    width = int(spec.split(".", 1)[0].lstrip("<>^"))
    return f"{'n/a':>{width}}" if value is None else f"{value:{spec}}"


def render(m: dict[str, Any]) -> str:
    """The report, as fixed-width text. Byte-stable for a given database."""
    lines: list[str] = []
    add = lines.append
    stats: training.CorpusStats = m["stats"]
    floors: training.Floors = m["floors"]
    verdict: training.Sufficiency = m["verdict"]

    add("NetCoreNOC shadow-mode report")
    add("=" * _WIDTH)
    add("")
    add("A challenger runs beside the champion and writes its opinion where")
    add("nobody acts on it. THE BUILT-IN SCORER DECIDES EVERYTHING. There is no")
    add("promotion mechanism in this release, deliberately: a release that could")
    add("promote would be judged by the only metric it had, which would be")
    add("agreement with the champion.")
    add("")

    add("-- SUFFICIENCY (read this first) " + "-" * (_WIDTH - 33))
    add(f"  {'verdict':<34}{_ok(verdict.ok):>16}")
    add("")
    add(f"  {'floor':<26}{'required':>10}{'observed':>10}")
    for name, required, observed in (
        ("split bags", floors.split_bags, stats.split_bags),
        ("mixed bags", floors.mixed_bags, stats.mixed_bags),
        ("merge-aware incidents", floors.incidents, stats.incidents),
        ("distinct operators", floors.operators, stats.operators),
    ):
        add(f"  {name:<26}{required:>10}{observed:>10}")
    add(
        f"  {'top-operator share (max)':<26}{floors.top_operator_share_pct:>9.1f}%"
        f"{stats.top_operator_share_pct:>9.1f}%"
    )
    add("")
    add("  ADDITIONAL OBSERVATIONS — not floors, and not substituted for one:")
    add(f"  {'split AND mixed bags':<26}{'(no floor)':>10}{m['split_and_mixed']:>10}")
    add(f"  {'asserted negative pairs':<26}{'(no floor)':>10}{m['asserted_negatives']:>10}")
    add("  `split AND mixed` is the population SECURITY-REVIEW-0.9.0 §5.4 argued")
    add("  the floor should be over — a `split` on a UNIFORM bag only says the")
    add("  champion should reject what it already rejects — and it floors NOTHING")
    add("  here: replacing a registered floor after seeing the data is what")
    add("  pre-registration prevents, so v0.10.0's plan decides it in advance.")
    add("")
    if verdict.unmet:
        add("  UNMET, and how long until they would be met:")
        for name, projection in sorted(verdict.projections.items()):
            add(f"    {name:<24}{projection}")
        add("")
        add("  NOTHING WAS FITTED, AND THAT IS A RESULT. A release that trained on")
        add("  a corpus below its own pre-registered floors would have produced a")
        add("  number nobody could defend, and the floors were registered before")
        add("  any of this data was looked at.")
    else:
        add("  Every floor met. The floors were pre-registered before any result")
        add("  existed; a deployment may harden them and can never soften them.")
    if m["floors_warning"]:
        add("")
        add("  WARNING: the stored floor policy was unreadable and was ignored.")
    add("")

    add("-- the corpus " + "-" * (_WIDTH - 14))
    add(f"  {'labelled bags (n)':<34}{stats.bags:>12}")
    add(f"  {'  confirm / split':<34}{f'{stats.confirm_bags} / {stats.split_bags}':>12}")
    add(f"  {'  MIXED (pairs both sides)':<34}{stats.mixed_bags:>12}")
    add(f"  {'merge-aware incidents':<34}{stats.incidents:>12}")
    add(f"  {'distinct operators':<34}{stats.operators:>12}")
    add(f"  {'promoted pairs behind them':<34}{stats.pairs:>12}")
    add(f"  {'label span (days)':<34}{stats.span_days:>12.2f}")
    add(f"  {'legacy_capture rows included':<34}{m['include_legacy']!s:>12}")
    add("")
    add("  *n* IS BAGS, NOT PAIRS. Pairs from one alarm share a side; pairs from")
    add("  one situation are strongly correlated; two labels on situations that")
    add("  were merged are ONE incident. A MIXED bag is the only kind that")
    add("  contained a decision the champion could have got wrong.")
    add("")

    _render_partition(add, "THE CHAMPION", m["champion_partition"])
    for policy in sorted(m["policies"]):
        _render_policy(add, policy, m["policies"][policy])
    if not m["policies"]:
        add("-- the challenger " + "-" * (_WIDTH - 18))
        add("  No model was fitted. See SUFFICIENCY above.")
        add("")

    _render_admission(add, m["admission"])
    _render_skew(add, m["skew"], m["shadow_stats"])

    add("-- what this report CANNOT tell you " + "-" * (_WIDTH - 36))
    add("  * whether the challenger should be promoted. There is no promotion")
    add("    mechanism here and no evaluator worth trusting yet; that is v0.10.0")
    add("    and v0.11.0, in that order and for that reason.")
    add("  * whether either scorer is RIGHT. The truth here is what an operator")
    add("    said about a grouping they were shown, at bag granularity.")
    add("  * anything from a pairwise accuracy. The champion accepts 99.83% of")
    add("    evaluated pairs, so a constant 'link' scores 99.83%. No pairwise")
    add("    accuracy is reported anywhere above, deliberately.")
    add("  * how a model would behave on a different network. The held-out split")
    add("    this release uses is a reporting device over an n in the tens, and")
    add("    it is NOT v0.10.0's split.")
    add("")
    return "\n".join(lines) + "\n"


def _render_partition(add: Any, title: str, score: shadow_eval.PartitionScore) -> None:
    add(f"-- {title}: partition vs the human verdicts " + "-" * max(0, _WIDTH - len(title) - 38))
    add(f"  {'over_merge_rate':<34}{_num(score.over_merge)}")
    add(f"  {'under_merge_rate':<34}{_num(score.under_merge)}")
    add(f"  {'split_bag_intact_rate':<34}{_num(score.split_bag_intact_rate)}")
    add(
        f"  {'  over confirm bags / alarms':<34}"
        f"{f'{score.confirm_bags} / {score.confirm_alarms}':>8}"
    )
    add(f"  {'  split bags scored':<34}{score.split_bags:>8}")
    add("")


def _render_policy(add: Any, policy: str, result: dict[str, Any]) -> None:
    label = "split -> all pairs negative" if policy == "A" else "split discarded"
    add(f"-- CHALLENGER, policy {policy}: {label} " + "-" * max(0, _WIDTH - len(label) - 24))
    coefficients: Coefficients = result["coefficients"]
    add(f"  {'intercept':<34}{coefficients.intercept:>12.6f}")
    for name, weight in zip(FEATURE_NAMES, coefficients.weights, strict=True):
        add(f"  {name:<34}{weight:>12.6f}")
    add(f"  {'fingerprint':<34}{result['fingerprint'][:12]:>12}")
    add(f"  {'training rows / bags':<34}{f'{result["rows"]} / {result["bags_used"]}':>12}")
    add(
        f"  {'positive / negative mass':<34}"
        f"{f'{result["positive_mass"]:.2f} / {result["negative_mass"]:.2f}':>12}"
    )
    add(f"  {'training log loss (NOT quality)':<34}{result['log_loss']:>12.6f}")
    if result["single_class"]:
        add("")
        add("  >>> DEGENERATE: THIS POLICY DERIVED ONLY ONE CLASS. <<<")
        add("  With bag-level labels, discarding `split` discards the only source")
        add("  of negatives, so the target is constant and the best achievable")
        add("  model predicts 'link' unconditionally. The draft called policy B")
        add("  'throws away the minority class'; measured, it throws away the")
        add("  only class that could have taught anything. Reported rather than")
        add("  suppressed, because a policy whose failure is invisible is one a")
        add("  later release picks by default.")
    add("")
    _render_partition(add, f"policy {policy}", result["partition"])
    add(f"  calibration at BAG level, Brier {result['brier']}")
    add(f"  {'bin':<12}{'n':>6}{'mean predicted':>18}{'observed':>12}")
    for row in result["calibration"]:
        add(
            f"  {row['bin']:<12}{row['n']:>6}"
            f"{_num(row['mean_predicted'], '18.4f')}{_num(row['observed'], '12.4f')}"
        )
    add("")
    add("  CALIBRATION IS NOT IMPLIED BY ACCURACY. A model can rank perfectly and")
    add("  be badly calibrated, which is exactly where a threshold does the most")
    add("  damage. And the half that is a trap: CONFIDENCE MAY ROUTE WHAT THE")
    add("  OPERATOR IS ASKED; IT MAY NEVER AUTHORISE AUTONOMY. The autonomy")
    add("  trigger is measured agreement with humans, never a model's own")
    add("  certainty — a confidently wrong model is more dangerous than an")
    add("  uncertainly wrong one.")
    add("")


def _render_admission(add: Any, admission: dict[str, Any]) -> None:
    add("-- the admission filter " + "-" * (_WIDTH - 24))
    if not admission:
        add("  Not run: no model was fitted.")
        add("")
        return
    add(f"  {'':<20}{'median us':>12}{'p99 us':>12}{'terms':>8}{'det':>6}{'mem':>6}")
    for who in ("champion", "challenger"):
        row = admission[who]
        add(
            f"  {row['scorer_id']:<20}{row['median_us']:>12.3f}{row['p99_us']:>12.3f}"
            f"{row['terms']:>8}{row['deterministic']!s:>6}{row['memory_stable']!s:>6}"
        )
    add("")
    add(f"  {'admitted':<20}{admission['admitted']!s:>12}")
    for reason in admission["reasons"]:
        add(f"    - {reason}")
    add("")
    add("  THE CHAMPION IS MEASURED AGAINST THE SAME FILTER, on the same samples,")
    add("  in the same process. A filter nobody has run against the incumbent is a")
    add("  filter whose budget is a guess. The speed budget is a RATIO of the")
    add("  champion's p99, not a microsecond figure: Phase 0 measured the")
    add("  champion's own p99 moving 2.6x between two runs on one machine.")
    add("")


def _render_skew(add: Any, skew: dict[str, Any], stats: dict[str, Any]) -> None:
    add("-- training/serving skew " + "-" * (_WIDTH - 25))
    add(f"  {'sampled online opinions':<34}{stats['shadow_opinion']:>12}")
    add(f"  {'challenger runs recorded':<34}{stats['challenger_run']:>12}")
    add(f"  {'compared against reconstruction':<34}{skew['compared']:>12}")
    add(f"  {'unmatched (no captured pair)':<34}{skew['unmatched']:>12}")
    add(f"  {'DIVERGED':<34}{skew['diverged']:>12}")
    add(f"  {'divergence rate':<34}{_num(skew['rate_pct'], '11.4f')}%")
    add("")
    add("  THE TWO MECHANISMS ARE NOT ALTERNATIVES. Offline reconstruction")
    add("  measures quality at no ingest cost and CANNOT measure skew by")
    add("  construction — recomputing from the stored features is tautologically")
    add("  consistent with them. Online shadow measures real latency and real")
    add("  behaviour and says nothing further about quality. THEIR DIVERGENCE IS")
    add("  THE TEST, compared with == on the float rather than a tolerance.")
    add("  The pre-registered expectation is 0.0000%. Any non-zero rate is a")
    add("  DEFECT, and it means the quality figures above describe features that")
    add("  were never served.")
    add("")
