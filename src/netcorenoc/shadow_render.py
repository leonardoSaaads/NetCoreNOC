"""Rendering the shadow-mode report: a mapping in, fixed-width text out.

Split from `shadow_report.py` in v0.9.2 (DECISIONS #139). That module had reached exactly the
400-line guard, and the evidence boundary needed one more line in the rendered output — so the
module was split on the seam `bias.py` / `bias_report.py` has always used rather than the guard
being raised to fit a corrective release.

**The prose is not decoration.** This report is where a 99.8 % number would look like a triumph, and
nearly half the module is the sentences that stop it: that pairwise accuracy is the base rate, that
a `split` bag supports no truth partition, that a policy-B fit has no negative class at all, and
that a calibrated probability may route what an operator is asked and may never authorise autonomy.

**Byte-stable for a given mapping** — fixed field widths, fixed float precision, no clock — because
`tests/test_shadow.py` compares this output against a frozen expectation.

This module reads nothing. It takes no `Store`, opens no connection, and holds no state; everything
it prints was decided by `shadow_report.collect`.
"""

from __future__ import annotations

from typing import Any

from netcorenoc import shadow_eval, training
from netcorenoc.challenger import FEATURE_NAMES, Coefficients
from netcorenoc.judge import Judgement
from netcorenoc.seal import SealSummary

__all__ = ["render"]

_WIDTH = 62


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
    # Two lines, and the difference between them is a diagnostic (v0.9.2, F46). The middle column
    # names WHOSE MEASUREMENT each is. `(server)` is the reported marks intersected with the
    # server's own bag, and it is the only one a floor could honestly be expressed over; `(client)`
    # is what the browser said. Both sit under "not floors", which the heading already says. They
    # are equal on every corpus the shipped UI has written, and a gap means something else wrote.
    add(f"  {'asserted negative pairs':<26}{'(server)':>10}{m['asserted_negatives']:>10}")
    add(f"  {'client-reported marks':<26}{'(client)':>10}{m['client_reported_marks']:>10}")
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

    judgement: Judgement = m["judgement"]
    add("-- THE VERDICT " + "-" * (_WIDTH - 15))
    add(f"  {'verdict':<34}{judgement.verdict.value:>26}")
    # §2.5's STRUCTURAL MITIGATION: a floor evaluation is NEVER printed without the detection
    # threshold for the same n beside it. A reader who sees "floors met" must not be able to read
    # "the evaluation is trustworthy" — the two are different claims and the second needs this
    # number. `tests/test_judge.py` fails if one is emitted without the other.
    add(f"  {'floors met':<34}{('yes' if judgement.floors_met else 'no'):>26}")
    add(f"  {'minimum detectable difference':<34}{judgement.detectable_difference:>26.3f}")
    add(f"  {'  at n incidents':<34}{judgement.incidents:>26}")
    add(f"  {'holdout queries':<34}{judgement.query_count:>26}")
    add("  projection")
    add(f"    {judgement.projection}")
    if judgement.triggers:
        add("")
        add("  INSUFFICIENT_EVIDENCE because:")
        for trigger in judgement.triggers:
            add(f"    - {trigger.value}")
    for note in judgement.notes:
        add(f"  {note}")
    add("")
    if not judgement.decisive:
        add("  NO QUALITY CLAIM IS AVAILABLE FROM THIS CORPUS, and none is made.")
        add("  INSUFFICIENT_EVIDENCE is a MEASUREMENT of the corpus — not an error,")
        add("  not a failure, and NOT a finding that the challenger is no better.")
        add("  Those are opposite claims and this release does not conflate them.")
        add("")
    add("  THE FLOORS AND THE THRESHOLD ARE TWO DIFFERENT CLAIMS. Floors met")
    add("  says a fit would not be degenerate and not one person's opinion.")
    add("  The detection threshold says whether a difference, if real, could")
    add("  be RESOLVED at this n. A corpus can clear every floor and still be")
    add("  unable to decide anything, which is why the second is a verdict")
    add("  trigger that no deployment may harden or disable.")
    add("")

    holdout: SealSummary = m["holdout"]
    add("-- THE SEALED HOLDOUT " + "-" * (_WIDTH - 22))
    if not holdout.exists:
        add("  no seal has been constructed yet — one is cut on the first")
        add("  training tick after a labelled corpus exists")
    else:
        add(f"  {'sealed incidents':<34}{holdout.incident_count:>12}")
        add(f"  {'of a corpus of':<34}{holdout.corpus_incidents:>12}")
        add(f"  {'digest':<20}{holdout.digest[:24]}...")
    # **THE HEADLINE, and it is printed as one.** Every holdout number ever published carries its
    # query count (§4.3(4)) so a reader can apply the inflation table without being told to:
    # 12 queries on 37 incidents inflate a rate by a median +11.1 p.p. when every candidate is
    # equally good. `attempts` includes refusals and plan registrations; `queries` counts only
    # granted reads of the membership, which is what "spending the holdout" means.
    add(f"  {'QUERIES (times spent)':<34}{holdout.query_count:>12}")
    add(f"  {'state':<34}{('SPENT' if holdout.spent else 'INTACT'):>12}")
    add(f"  {'access-log rows (incl. refusals)':<34}{m['holdout_attempts']:>12}")
    add(
        "  The holdout is "
        + ("CONSTRUCTED AND NOT SPENT." if holdout.exists else "NOT YET CUT.")
        + " Reserving later is"
    )
    add("  impossible; spending later is always possible. Adaptive selection")
    add("  over 12 queries on 37 incidents inflates a reported rate by a median")
    add("  +11.1 points when every candidate is equally good, so four releases")
    add("  tuning against one holdout would report an improvement produced")
    add("  entirely by looking.")
    add("")

    add("-- the corpus " + "-" * (_WIDTH - 14))
    add(f"  {'labelled bags (n)':<34}{stats.bags:>12}")
    add(f"  {'  confirm / split':<34}{f'{stats.confirm_bags} / {stats.split_bags}':>12}")
    add(f"  {'  MIXED (pairs both sides)':<34}{stats.mixed_bags:>12}")
    add(f"  {'merge-aware incidents':<34}{stats.incidents:>12}")
    # v0.10.0 (Workstream 1). BOTH answers, and their difference, always. Until this release the
    # incident was `COALESCE(merged_into, situation_id)` — ONE HOP — and a merge chain can be
    # longer. The one-hop count is printed beside the resolved one so the correction is visible
    # even when it is zero, which is what this project's own corpus produces: every chain in it is
    # exactly one hop. A number that only appears when it differs is a number nobody checks.
    add(f"  {'  one-hop count (superseded)':<34}{stats.incidents_one_hop:>12}")
    add(f"  {'  reduction from one hop':<34}{stats.reduction_from_one_hop:>12}")
    # A cycle or a chain past the depth bound. NEVER silently collapsed: the plan's §7.9 makes
    # these `unknown` and a verdict trigger, so a zero here is a claim and not a default.
    add(f"  {'  unsound merge chains':<34}{stats.unsound_chains:>12}")
    # Merged before `0008` existed, so the destination was never written and no migration can
    # reconstruct one. Such a situation LOOKS independent and is not, and no column tells them
    # apart. §3.3: counted, never assumed absent.
    add(f"  {'pre-v0.8.0 merges (unrecoverable)':<34}{m['pre_v080_merges']:>12}")
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
