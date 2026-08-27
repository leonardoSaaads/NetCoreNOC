"""Correlation-quality metrics — pure Python, no external dependencies.

Every metric here is a counter, a ratio, or a set operation over an aligned pair of
label lists (one entry per predicted alarm): the predicted label and the ground-truth
label. This mirrors the product's own discipline — no numpy, no clustering libraries —
and keeps the evaluation as auditable as the correlator it measures.

The pair-counting metrics (``pairwise_f1``, ``adjusted_rand_index``) are derived from a
single contingency table in O(N) rather than by enumerating O(N^2) pairs, so a 2 000-alarm
storm scores in milliseconds.

**``over_merge_rate`` and ``under_merge_rate`` moved into the package in v0.9.0** and are imported
back here (DECISIONS #122). They became a *runtime* metric — the shadow report scores the
challenger's partition with them — and ``src/netcorenoc/`` never imports ``eval/``, so the
implementation had to live on the other side. Re-exported rather than reimplemented, because two
copies of one metric agree until one is tuned and then the harness and the report disagree with
nothing to notice. The functions were moved without an edit; ``make eval`` is byte-identical.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Hashable, Sequence

from netcorenoc.engine.evaluation.shadow_eval import over_merge_rate, under_merge_rate

__all__ = [
    "adjusted_rand_index",
    "dedup_ratio",
    "entity_accuracy",
    "over_merge_rate",
    "pairwise_f1",
    "percentile",
    "root_top1",
    "under_merge_rate",
]

Label = Hashable


def _comb2(n: int) -> int:
    """n choose 2."""
    return n * (n - 1) // 2


def _contingency(pred: Sequence[Label], truth: Sequence[Label]) -> dict[tuple[Label, Label], int]:
    table: Counter[tuple[Label, Label]] = Counter()
    for p, t in zip(pred, truth, strict=True):
        table[(p, t)] += 1
    return dict(table)


def pairwise_f1(pred: Sequence[Label], truth: Sequence[Label]) -> float:
    """F1 over all alarm pairs on the question "same situation?".

    A pair is a true positive when predicted-together and truth-together. Derived from
    the contingency table: same_both / same_pred is precision, same_both / same_truth is
    recall.
    """
    if len(pred) < 2:
        return 1.0
    table = _contingency(pred, truth)
    pred_sizes: Counter[Label] = Counter()
    truth_sizes: Counter[Label] = Counter()
    for (p, t), n in table.items():
        pred_sizes[p] += n
        truth_sizes[t] += n
    same_both = sum(_comb2(n) for n in table.values())
    same_pred = sum(_comb2(n) for n in pred_sizes.values())
    same_truth = sum(_comb2(n) for n in truth_sizes.values())
    if same_pred == 0 and same_truth == 0:
        return 1.0  # every alarm is its own singleton in both partitions
    precision = same_both / same_pred if same_pred else 0.0
    recall = same_both / same_truth if same_truth else 0.0
    if precision + recall == 0.0:
        return 0.0
    return 2.0 * precision * recall / (precision + recall)


def adjusted_rand_index(pred: Sequence[Label], truth: Sequence[Label]) -> float:
    """Chance-corrected agreement between the predicted and true partitions."""
    n = len(pred)
    if n < 2:
        return 1.0
    table = _contingency(pred, truth)
    pred_sizes: Counter[Label] = Counter()
    truth_sizes: Counter[Label] = Counter()
    for (p, t), cnt in table.items():
        pred_sizes[p] += cnt
        truth_sizes[t] += cnt
    index = sum(_comb2(cnt) for cnt in table.values())
    sum_a = sum(_comb2(cnt) for cnt in pred_sizes.values())
    sum_b = sum(_comb2(cnt) for cnt in truth_sizes.values())
    total = _comb2(n)
    expected = (sum_a * sum_b) / total if total else 0.0
    max_index = 0.5 * (sum_a + sum_b)
    if max_index == expected:
        return 1.0  # both partitions are degenerate (all-together or all-singletons)
    return (index - expected) / (max_index - expected)


def entity_accuracy(pred_entity: Sequence[Label], truth_entity: Sequence[Label]) -> float:
    """Purity of the predicted entity attribution against the true entity of each alarm.

    Each predicted entity is assigned its majority true entity; the score is the fraction
    of alarms whose true entity equals the majority true entity of their predicted entity.
    With no entity model (v0.2.0) the predicted entity is the reporting NE, so proxied
    storms — thousands of ONUs behind one OLT — score near zero, which is the failure this
    version targets.
    """
    if not pred_entity:
        return 1.0
    groups: dict[Label, Counter[Label]] = {}
    for p, t in zip(pred_entity, truth_entity, strict=True):
        groups.setdefault(p, Counter())[t] += 1
    correct = sum(counts.most_common(1)[0][1] for counts in groups.values())
    return correct / len(pred_entity)


def root_top1(considered: int, correct: int) -> float:
    """Fraction of situations whose flagged root matches the labelled true root."""
    return correct / considered if considered else 0.0


def dedup_ratio(distinct_alarms: int, traps_ingested: int) -> float:
    """alarms created / traps ingested. Near 1.0 signals the instance heuristic failing
    (every trap became its own alarm — no deduplication, no learning)."""
    return distinct_alarms / traps_ingested if traps_ingested else 0.0


def percentile(values: Sequence[float], q: float) -> float:
    """Deterministic nearest-rank percentile (q in [0, 1])."""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(q * len(ordered)))
    return ordered[idx]
