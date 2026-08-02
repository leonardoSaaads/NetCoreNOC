"""Rendering the bias report: the presentation half, and the prose that makes it honest.

Split from `bias.py` at the seam the `eval/` tooling already uses — `metrics.py` computes and the
harness renders. `bias.collect` produces numbers; this turns them into the text an operator reads.

**The prose is not decoration.** Nearly half this module is the paragraphs under each heading, and
they are the part that stops a number being misread: that *n* is bags and not pairs, that
`incumbent_linked` is not a target, that `legacy_capture` rows are of unknown rather than known-bad
quality, that `partial` coverage is ordinary. A report of bare figures would be measured and still
misleading, which is the failure this release exists to prevent one level up.

Output is **byte-stable** for a given set of measurements — fixed field widths, fixed float
precision, no clock — because `tests/test_bias.py` compares it against a frozen expectation.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.bias import pct

__all__ = ["render"]


def render(m: dict[str, Any]) -> str:
    """The report, as fixed-width text. **Byte-stable for a given dataset** — that is what lets
    `make qa` compare it against a frozen expectation and go red the day capture changes shape."""
    lines: list[str] = []
    add = lines.append

    add("NetCoreNOC feedback-dataset bias report")
    add("=" * 62)
    add("")
    add("This report MEASURES. It trains nothing, fits nothing, and recommends")
    add("no model. Every figure below is an aggregate.")
    add("")

    add("-- corpus ----------------------------------------------------")
    add(f"pairs, sink (awaiting a label)        {m['pairs_sink']:>12}")
    add(f"pairs, dataset (promoted by a label)  {m['pairs_dataset']:>12}")
    add(f"observations, sink                    {m['observations_sink']:>12}")
    add(f"observations, dataset                 {m['observations_dataset']:>12}")
    add(f"capture runs                          {m['capture_runs']:>12}")
    add("")
    add(f"{'  of those, inside the training window':<38}{m['pairs_in_training_window']:>12}")
    add(f"{'  outside it (held, not selectable)':<38}{m['pairs_outside_training_window']:>12}")
    add(f"{'  ORPHANED: promoted, label since gone':<38}{m['orphaned_pairs']:>12}")
    add("")
    add("  The training window SELECTS; it does not delete. Nothing dies at that")
    add("  boundary — a training-retention delete would destroy evidence to express")
    add("  a modelling preference, and selection is a WHERE clause. So the corpus")
    add("  has two sizes: what it HOLDS and what a model may READ.")
    add("  An ORPHANED pair is a promoted pair whose label no longer exists — a")
    add("  feature nothing can interpret. It is counted, never collected: the")
    add("  corpus is not corrupt, its USABLE size is just smaller than its row")
    add("  count. Sources are an audit-bound reduction landing between a label and")
    add("  its features, and any pre-v0.8.1 database that lost labels to F44.")
    add("")

    add("-- the machine's decision (NOT a label) -----------------------")
    total_pairs = int(m["pairs_sink"]) + int(m["pairs_dataset"])
    add(
        f"pairs the incumbent linked            {m['incumbent_linked']:>12}"
        f"  ({pct(int(m['incumbent_linked']), total_pairs)})"
    )
    add(f"accepted links dropped by the cap     {m['truncated_pairs']:>12}")
    add(
        f"pairs evaluated during a storm        {m['storm_pairs']:>12}"
        f"  ({pct(int(m['storm_pairs']), total_pairs)})"
    )
    add("")
    add("  `incumbent_linked` is the champion's decision at that instant. It is")
    add("  provenance and a legitimate feature. It is NOT a target, and no metric")
    add("  that decides promotion may be computed against it: a challenger judged")
    add("  by the champion can only converge on it. Its class balance is also a")
    add("  property of the TRAFFIC, not of the scorer — measured at 0% accept on")
    add("  quiet corpus traffic and 100% in a storm.")
    add("")

    add("-- human labels ----------------------------------------------")
    for verdict in sorted(m["verdicts"]):
        add(f"{verdict:<38}{m['verdicts'][verdict]:>12}")
    add(f"{'total':<38}{m['labels_total']:>12}")
    add("")

    add("-- EFFECTIVE SAMPLE SIZE -------------------------------------")
    add(f"independent bags (n)                  {m['bags']:>12}")
    add(f"distinct operators                    {m['distinct_operators']:>12}")
    add(f"distinct incidents (merge-aware)      {m['distinct_incidents']:>12}")
    add(f"labels with no recorded operator      {m['labels_without_operator']:>12}")
    add("")
    add("  *n* IS THE NUMBER OF INDEPENDENT BAGS, NOT THE NUMBER OF PAIRS.")
    add("  Pairs from one alarm share a side; pairs from one situation are strongly")
    add("  correlated; situations from one operator share a criterion. A report")
    add("  quoting the pair count would produce confidence intervals wrong by a")
    add("  factor nobody checks.")
    add("")

    add("-- bag sizes -------------------------------------------------")
    add(_dist_line("members per labelled bag", m["bag_sizes"]))
    add(f"{'bags with zero members':<38}{m['empty_bags']:>12}")
    add("")
    add("  CONFIRMATION STRENGTH DECAYS WITH BAG SIZE. An operator confirming a")
    add("  twenty-member situation did not verify one hundred and ninety pairs;")
    add("  one confirming two members verified the only pair there was. This")
    add("  distribution is what lets a later release weight rather than assume.")
    add("  A zero-member bag is a verdict on an already-merged situation: real,")
    add("  recorded, and not interchangeable with the rest.")
    add("")

    add("-- operator concentration ------------------------------------")
    add(f"{'share from the single top operator':<38}{m['top_operator_share']:>12}")
    add(f"{'share from the top three':<38}{m['top3_operator_share']:>12}")
    add("")
    add("  A dataset that is three people's judgement is a dataset about three")
    add("  people.")
    add("")

    add("-- visibility scope ------------------------------------------")
    add(f"{'labels made under a restricting scope':<38}{m['labels_under_restricting_scope']:>12}")
    add(_dist_line("redacted members among those", m["redacted_members"]))
    add("")
    add("  A scoped editor labels a PARTIAL VIEW and cannot say which part. The")
    add("  resulting noise is SYSTEMATIC, not random: it correlates with the scope")
    add("  policy, so it does not average out with more data.")
    add("")

    add("-- label latency ---------------------------------------------")
    add(_dist_line("seconds, situation open to verdict", m["latency_s"]))
    add("")
    add("  Also how the sink's 21-day default gets tested against reality. That")
    add("  default is a conservative GUESS; this distribution is what replaces it.")
    add("")

    add("-- coverage --------------------------------------------------")
    add(f"{'situations known (live + labelled)':<38}{m['situations_total']:>12}")
    add(f"{'situations that received a label':<38}{m['situations_labelled']:>12}")
    add(f"{'  as a rate':<38}{m['situation_label_rate']:>12}")
    add(f"{'evaluated pairs ever promoted':<38}{m['pair_promotion_rate']:>12}")
    for state in ("full", "partial", "none", "empty"):
        add(f"{'per-label coverage: ' + state:<38}{m['per_label_coverage'].get(state, 0):>12}")
    add("")
    add("  The denominator is every situation this database has EVIDENCE of: those")
    add("  still in `situation`, plus those named by a surviving label. It is not")
    add("  `COUNT(*) FROM situation`, which the operational prune shrinks while")
    add("  labels outlive it — that denominator printed 300.0% before v0.8.1. This")
    add("  one contains its own numerator by construction, so no clamp is needed.")
    add("")
    add("  `partial` is NOT an edge case. The sliding window and MAX_CANDIDATES")
    add("  mean pairs within a labelled situation may never have been evaluated —")
    add("  a nine-member situation has thirty-six pairs. Recorded rather than")
    add("  silently dropped, which is the censoring this release exists to end.")
    add("  `empty` is a verdict on a situation with no members — a fourth state the")
    add("  specification's three did not anticipate, because all three presuppose a bag.")
    add("")

    add("-- provenance (NEVER averaged together) -----------------------")
    for key in sorted(m["provenance"]):
        add(f"{key:<38}{m['provenance'][key]:>12}")
    add("")
    add("  `legacy_capture` rows were acquired before v0.7.5 repaired the click")
    add("  path. They are NOT known to be bad — they are of UNKNOWN QUALITY, which")
    add("  is a weaker and different claim. Excluded from training by default,")
    add("  includable by an explicit choice, and never blended into the figures")
    add("  above without saying so.")
    add("")
    add("-- acquisition channel ---------------------------------------")
    for key in sorted(m["acquisition"]):
        add(f"{key:<38}{m['acquisition'][key]:>12}")
    add("")

    add("-- membership divergence -------------------------------------")
    add(f"{'labels carrying a client report':<38}{m['client_reported']:>12}")
    add(f"{'client bag differs from the server':<38}{m['client_diverged']:>12}")
    add(f"{'client report truncated':<38}{m['client_truncated']:>12}")
    add("")
    add("  This is a METRIC, not an error: the residual race v0.7.5 narrowed but")
    add("  did not eliminate, measured rather than assumed. The server's bag is")
    add("  authoritative; the client's is evidence.")
    add("")

    add("-- leakage exposure ------------------------------------------")
    add(f"{'dataset pairs evaluated after label 1':<38}{m['pairs_after_first_label']:>12}")
    add(f"{'distinct matrix epochs in the dataset':<38}{m['distinct_a_epochs']:>12}")
    add(f"{'observations':<38}{int(m['observations_sink']) + int(m['observations_dataset']):>12}")
    add(f"{'  distinct alarms among them':<38}{m['observations_distinct_alarms']:>12}")
    add("")
    add("  A human verdict mutates A and E, so a pair evaluated afterwards carries")
    add("  features that already contain information from an earlier label. Note")
    add("  the mechanism: the label does NOT advance the epoch (since v0.7.1's F36")
    add("  fix, only a closed situation does) — the contamination travels through")
    add("  the MASS VALUES. Observations sharing an alarm id are re-fires of one")
    add("  deduplicated alarm and are near-duplicates, not independent samples.")
    add("")

    add("-- retention state -------------------------------------------")
    if m["retention"] is None:
        add("no capture run recorded")
    else:
        for key in ("sink_days", "sink_rows", "training_days", "audit_days"):
            add(f"{key:<38}{m['retention'][key]:>12}")
    add(f"{'span of surviving labels (days)':<38}{m['oldest_label_age_days']!s:>12}")
    add("")
    add("  Pruning a training corpus is CENSORING BY POLICY. It is legitimate and")
    add("  it must be visible: a model trained under twelve months' retention and")
    add("  one trained under three are not comparable, and nothing else would")
    add("  record which was which.")
    add("")

    add("-- what this report CANNOT tell you --------------------------")
    add("  * whether a verdict was correct. There is no ground truth here, only")
    add("    what an operator said.")
    add("  * whether the labels are representative of the network. Operators")
    add("    label what they open, and this measures what they labelled.")
    add("  * which of the `legacy_capture` rows are actually bad. Nothing")
    add("    recorded it, which is precisely why they are marked and not deleted.")
    add("  * anything about pairs that were never evaluated. They are absent by")
    add("    construction; `partial` coverage counts them, and that is all.")
    add("")
    return "\n".join(lines) + "\n"


def _dist_line(label: str, dist: dict[str, Any]) -> str:
    if not dist["n"]:
        return f"{label:<38}{'(none)':>12}"
    return (
        f"{label:<38}"
        f"n={dist['n']} min={dist['min']} med={dist['median']} "
        f"p90={dist['p90']} max={dist['max']} mean={dist['mean']}"
    )
