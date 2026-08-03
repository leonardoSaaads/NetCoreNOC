"""Rendering the champion-agreement report: the presentation half, and the prose that bounds it.

Split from `agreement.py` at the seam this project already uses twice — `eval/metrics.py` computes
and the harness renders, `bias.py` computes and `bias_report.py` renders. Not a new abstraction; the
existing one, applied a third time (DECISIONS #115).

**The prose is not decoration.** The number this report prints is the one that bounds the value of
v0.10.0 through v0.13.0, and it is the easiest number in the repository to misread: a confirm rate
is *agreement*, not *correctness*, and on a uniform bag it is agreement about a decision that was
never made. Every heading below carries the sentence that stops the misreading.

Output is **byte-stable** for a given set of measurements — fixed field widths, fixed float
precision, no clock — because `tests/test_agreement.py` compares it against a frozen expectation.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.agreement import MIN_INCIDENTS_FOR_INTERVAL, Rate

__all__ = ["render"]

_WIDTH = 62


def _rate_line(label: str, rate: Rate) -> str:
    """`label   n=…  confirmed=…  rate  [interval]`, in fixed columns."""
    if rate.n == 0:
        return f"  {label:<24}{'(none)':>10}"
    pct = rate.pct
    assert pct is not None
    interval = (
        f"  [{rate.lo:5.1f}, {rate.hi:5.1f}]"
        if rate.lo is not None and rate.hi is not None
        else "  [interval n/a]"
    )
    return f"  {label:<24}n={rate.n:>5}  confirmed={rate.confirms:>5}  {pct:>6.1f}%{interval}"


def _section(add: Any, title: str, rows: list[tuple[str, Rate]]) -> None:
    add(f"-- {title} " + "-" * max(0, _WIDTH - len(title) - 4))
    if not rows:
        add("  (no bags)")
    for label, rate in rows:
        add(_rate_line(label, rate))
    add("")


def render(m: dict[str, Any]) -> str:
    """The report, as fixed-width text. Byte-stable for a given dataset."""
    lines: list[str] = []
    add = lines.append

    add("NetCoreNOC champion-agreement report")
    add("=" * _WIDTH)
    add("")
    add("How well the BUILT-IN SCORER already agrees with the operators, at bag")
    add("level, conditioned. This report trains nothing and involves no model.")
    add("")
    add("  AGREEMENT IS NOT CORRECTNESS. There is no ground truth here — only what")
    add("  an operator said about a grouping they were shown. A high rate means the")
    add("  champion and the operators concur; it does not mean either was right.")
    add("")

    add("-- population " + "-" * (_WIDTH - 14))
    add(f"  {'labelled bags, all provenance':<36}{m['bags_all']:>10}")
    add(f"  {'labelled bags, provenance=current':<36}{m['bags']:>10}")
    add(f"  {'merge-aware incidents among them':<36}{m['incidents']:>10}")
    add(f"  {'distinct operators':<36}{m['operators']:>10}")
    add(f"  {'bags with no promoted pair':<36}{m['bags_without_pairs']:>10}")
    add(f"  {'promoted pairs behind them':<36}{m['pairs_total']:>10}")
    for verdict in sorted(m["verdicts"]):
        add(f"  {'verdict: ' + verdict:<36}{m['verdicts'][verdict]:>10}")
    add("")
    add("  EVERY FIGURE BELOW EXCEPT THE PROVENANCE SECTION IS OVER `current` ROWS")
    add("  ALONE. `legacy_capture` labels were acquired before v0.7.5 repaired the")
    add("  click path: they are of UNKNOWN quality, which is weaker than bad, and")
    add("  blending them into a headline would make it uninterpretable in a way no")
    add("  reader could detect afterwards.")
    add("")

    add("-- THE HEADLINE " + "-" * (_WIDTH - 16))
    add(_rate_line("champion agreement", m["headline"]))
    add("")
    add("  The interval is a CLUSTER BOOTSTRAP OVER INCIDENTS, not over bags and")
    add("  never over pairs: two labels on situations that were merged into one are")
    add(f"  ONE incident. Below {MIN_INCIDENTS_FOR_INTERVAL} incidents it is REFUSED outright and")
    add("  prints `n/a` — resampling a handful of clusters gives an interval that")
    add("  looks like a measurement and is an artefact of a handful of numbers.")
    add("")
    add("  READ THE DECOMPOSITION, NOT THIS LINE. An aggregate of 94% that is 99% on")
    add("  uniform storm bags and 58% on mixed bags is the difference between an ML")
    add("  programme with no headroom and one with a clear target.")
    add("")

    _section(add, "by MIXED vs UNIFORM bag (the strongest cut)", m["by_mixedness"])
    add("  A UNIFORM BAG CONTAINED NO DECISION. Every pair in it fell on the same")
    add("  side of the threshold, so the operator confirmed an outcome the champion")
    add("  could not have reached differently: the confirm says nothing whatever")
    add("  about the scorer's judgement. A MIXED bag spans the threshold — there the")
    add("  champion made a call, and there alone the agreement rate is about it.")
    add("  Measured over the eval corpus, 7 of 9 scenarios are uniform.")
    add("")

    _section(add, "by bag size", m["by_size"])
    add("  CONFIRMATION STRENGTH DECAYS WITH SIZE. An operator confirming a")
    add("  five-hundred-member storm did not verify one hundred and twenty-four")
    add("  thousand pairs; one confirming two members verified the only pair there")
    add("  was. `unrecorded` is a pre-v0.8.0 label, distinguishable from a zero.")
    add("")

    _section(add, "by storm", m["by_storm"])
    add("  IN A STORM THE CHAMPION IS RIGHT ABOUT SOMETHING IT COULD NOT HAVE GOT")
    add("  WRONG. Accept rates measured at 0% on quiet corpus traffic and 100% in")
    add("  every storm scenario, so a storm confirm is close to tautological.")
    add("  `partly` is a bag whose pairs straddle the storm boundary; it is given")
    add("  its own row rather than assigned by a majority rule, because the")
    add("  threshold such a rule would need is a number nobody measured.")
    add("")

    _section(add, "by visibility scope", m["by_scope"])
    add("  A SCOPED EDITOR JUDGED A PARTIAL VIEW and cannot say which part — the")
    add("  redaction deliberately carries no NE id, address or entity key. The")
    add("  resulting noise is SYSTEMATIC, not random: it correlates with the scope")
    add("  policy, so it does not average out with more data.")
    add("")

    _section(add, "by operator", m["by_operator"])
    add("  A HEADLINE OVER THREE PEOPLE IS THREE PEOPLE'S OPINION. Spread here is")
    add("  not noise to be averaged away — it is the quantity that says whether")
    add("  `confirm` means the same thing to two operators.")
    add("  OPERATORS ARE ANONYMISED and the aliases are ordered by bag count, not by")
    add("  identity. What this cut is for is the spread; a name would turn a bias")
    add("  measurement into a per-employee performance report nobody consented to.")
    add("")

    _section(add, "by capture provenance (NEVER averaged)", m["by_provenance"])
    add("  The one section computed over ALL rows, because separating the two")
    add("  populations is its entire purpose. If these two rates differ materially,")
    add("  the pre-v0.7.5 click path is visible in the data and the `current` rate")
    add("  above is the only one worth quoting.")
    add("")

    _section(add, "by promotion coverage", m["by_coverage"])
    add("  `partial` is NOT an edge case: the sliding window and MAX_CANDIDATES mean")
    add("  pairs within a labelled situation may never have been evaluated. A bag")
    add("  with coverage `none` or `empty` has no promoted pair, so it appears in")
    add("  the `no-pairs` row of the cuts above — counted, never silently dropped.")
    add("")

    add("-- what this report CANNOT tell you " + "-" * (_WIDTH - 36))
    add("  * whether the champion was RIGHT. Only whether an operator agreed.")
    add("  * whether a disagreement was the scorer's fault. A `split` may mean the")
    add("    grouping was wrong, or that the operator wanted a different grouping")
    add("    for an operational reason this system never sees.")
    add("  * anything about situations nobody labelled. Operators label what they")
    add("    open, and this measures what they labelled.")
    add("  * anything about pairs that were never evaluated. They are absent by")
    add("    construction; the coverage section counts them, and that is all.")
    add("  * how much headroom a model has. It bounds it from above on the labelled")
    add("    population and says nothing about the rest of the network.")
    add("")
    return "\n".join(lines) + "\n"
