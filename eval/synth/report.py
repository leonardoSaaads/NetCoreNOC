"""The shipped model's evaluation and its manifest — every number with its dataset and its n.

Everything here compares **two deciders on the same recorded streams**: the model (every candidate,
correlation clustering) and the additive formula (window candidates, connected components). A number
without the formula's beside it on the same streams is not a comparison, and a number without its
sample size and interval is not a measurement (Part V).

Intervals are percentile intervals from a **cluster bootstrap over streams**: a stream is one
appliance's whole history, so its incidents and pairs are not independent of each other, and
resampling anything smaller would print an interval narrower than the truth.
"""

from __future__ import annotations

import hashlib
import math
import random
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netcorenoc.engine.evaluation import model_metrics as mm
from netcorenoc.engine.model import gam, gam_fit, search

from synth import dataset
from synth.evaluate import (
    FormulaDecider,
    ModelDecider,
    Outcome,
    first_hour,
    group,
    restrict,
    situation_metrics,
)

__all__ = ["check_bar", "evaluate", "manifest"]

REPLICATES = 200
HEADLINE = (
    "pairwise_f1",
    "ari",
    "over_merge_rate",
    "under_merge_rate",
    "split_bag_intact_rate",
    "asserted_negative_respected_rate",
    "repair_gestures",
)
TEST_SPLITS = ("test_iid", "test_optical", "test_protocol", "test_concurrency", "test_adverse")
HELD_OUT = {"test_optical": dataset.HOLDOUT_OPTICAL, "test_protocol": dataset.HOLDOUT_PROTOCOL}


def _sigmoid(z: float) -> float:
    z = max(-700.0, min(700.0, z))
    return 1.0 / (1.0 + math.exp(-z))


def _metrics_ci(
    outcomes: Sequence[Outcome], family: str | None = None, seed: int = 0
) -> dict[str, Any]:
    """Point, interval and n for every headline quantity, bootstrapped over streams."""
    point = situation_metrics(outcomes, family)
    rng = random.Random(seed)
    samples: dict[str, list[float]] = {k: [] for k in HEADLINE}
    if len(outcomes) >= 2:
        for _ in range(REPLICATES):
            pick = [
                replace(outcomes[rng.randrange(len(outcomes))], name=f"b{k}")
                for k in range(len(outcomes))
            ]
            m = situation_metrics(pick, family)
            for key in HEADLINE:
                samples[key].append(m[key])
    out: dict[str, Any] = {
        "streams": len(outcomes),
        "activations": int(point["activations"]),
        "incidents": int(point["incidents"]),
        "concurrent_pairs": int(point["concurrent_pairs"]),
        "negatives_compared": int(point["negatives_compared"]),
    }
    for key in HEADLINE:
        values = sorted(samples[key])
        lo = values[int(0.025 * (len(values) - 1))] if values else point[key]
        hi = values[int(0.975 * (len(values) - 1))] if values else point[key]
        out[key] = {"point": round(point[key], 4), "low": round(lo, 4), "high": round(hi, 4)}
    return out


def _pairs(logs: Sequence[Any], scorer: gam.GamScorer, seed: int) -> dict[str, Any]:
    """Pair-level metrics on sampled, weighted test pairs, bootstrapped over streams."""
    rows = dataset.training_rows(list(logs), seed=seed)
    if not rows:
        return {}
    y = [r.y for r in rows]
    w = [r.w for r in rows]
    p = [_sigmoid(scorer.logit(r.x)) for r in rows]
    by_stream: dict[str, list[int]] = {}
    for i, r in enumerate(rows):
        by_stream.setdefault(r.stream, []).append(i)
    clusters = list(by_stream.values())

    def stat(fn: Any) -> Any:
        return lambda idx: fn([y[i] for i in idx], [p[i] for i in idx], [w[i] for i in idx])

    baseline = sum(wi for yi, wi in zip(y, w, strict=True) if yi) / sum(w)
    raw_rate = sum(y) / len(y)
    thr = 1.0 / (1.0 + math.exp(-scorer.threshold))
    cal = mm.calibration(y, p, w)
    conf = mm.confusion(y, p, thr, w)
    return {
        "pairs": len(rows),
        "streams": len(clusters),
        "positive_rate_weighted": round(baseline, 4),
        "positive_rate_unweighted": round(raw_rate, 4),
        "average_precision": mm.cluster_bootstrap(
            clusters, stat(mm.average_precision), replicates=REPLICATES, seed=seed
        ).as_dict(),
        "roc_auc": mm.cluster_bootstrap(
            clusters, stat(mm.roc_auc), replicates=60, seed=seed
        ).as_dict(),
        "log_loss": mm.cluster_bootstrap(
            clusters, stat(mm.log_loss), replicates=REPLICATES, seed=seed
        ).as_dict(),
        "brier": mm.cluster_bootstrap(
            clusters, stat(mm.brier), replicates=REPLICATES, seed=seed
        ).as_dict(),
        "calibration": cal.as_dict(),
        "confusion": conf.as_dict(),
        "threshold_probability": thr,
        "pr_curve": [[round(a, 4), round(b, 4), round(c, 4)] for a, b, c in mm.pr_curve(y, p, w)],
        "roc_curve": [[round(a, 4), round(b, 4), round(c, 4)] for a, b, c in mm.roc_curve(y, p, w)],
        "residuals": residuals(y, p, w),
    }


def residuals(
    y: Sequence[int], p: Sequence[float], w: Sequence[float], bins: int = 20
) -> dict[str, Any]:
    """The distribution of ``y - p`` (v0.27.0, ADR #427): a classifier's residual is its predicted
    probability's distance from the outcome, in ``[-1, 1]``, and its histogram — split by the true
    class — shows *where* the model is wrong: mass near ±1 is confident error, mass near 0 is
    confident truth. Shares of the weighted pairs, so two models on one axis compare directly."""
    total = sum(w) or 1.0
    pos = [0.0] * bins
    neg = [0.0] * bins
    mean = mean_abs = 0.0
    for yi, pi, wi in zip(y, p, w, strict=True):
        r = yi - pi
        k = min(bins - 1, max(0, int((r + 1.0) / 2.0 * bins)))
        (pos if yi else neg)[k] += wi / total
        mean += wi * r / total
        mean_abs += wi * abs(r) / total
    return {
        "edges": [round(-1.0 + 2.0 * k / bins, 4) for k in range(bins + 1)],
        "positive": [round(v, 6) for v in pos],
        "negative": [round(v, 6) for v in neg],
        "mean": round(mean, 6),
        "mean_abs": round(mean_abs, 6),
    }


def evaluate(root: Path, scorer: gam.GamScorer, seed: int) -> dict[str, Any]:
    model = ModelDecider.of(scorer)
    formula = FormulaDecider()
    out: dict[str, Any] = {"splits": {}, "held_out_families": {}, "pairs": {}, "first_hour": {}}
    for split in TEST_SPLITS:
        logs = list(dataset.load_split(root, split))
        mo = [group(log, model) for log in logs]
        fo = [group(log, formula) for log in logs]
        out["splits"][split] = {
            "model": _metrics_ci(mo, seed=seed),
            "formula": _metrics_ci(fo, seed=seed),
        }
        out["pairs"][split] = _pairs(logs, scorer, seed)
        for fam in HELD_OUT.get(split, ()):
            out["held_out_families"][fam] = {
                "split": split,
                "model": _metrics_ci(mo, fam, seed),
                "formula": _metrics_ci(fo, fam, seed),
            }
        if split == "test_iid":
            out["first_hour"] = {"model": _first_hour(mo, seed), "formula": _first_hour(fo, seed)}
        # Released before the next split is read, so one split's streams are in memory at a time.
        del logs, mo, fo
    long_logs = list(dataset.load_split(root, "train_long"))
    tm: list[Outcome] = []
    tf: list[Outcome] = []
    for log in long_logs:
        for dec, bucket in ((model, tm), (formula, tf)):
            o = group(log, dec)
            if not o.ts:
                continue
            # Whole incidents, by their first activation: the complement of what trained.
            older = dataset.incident_side(log.activations(), (0.0, 0.7))
            bucket.append(restrict(o, [i for i, t in enumerate(o.truth) if not older[t]]))
    out["splits"]["test_time"] = {
        "model": _metrics_ci(tm, seed=seed),
        "formula": _metrics_ci(tf, seed=seed),
    }
    out["recurrence"] = _recurrence(long_logs, model, formula)
    return out


def _first_hour(outcomes: Sequence[Outcome], seed: int) -> dict[str, Any]:
    firsts = [first_hour(o) for o in outcomes]
    joins = 0
    for o in firsts:
        seen: set[int] = set()
        for p in o.pred:
            joins += p in seen
            seen.add(p)
    return {"decisions": joins, **_metrics_ci(firsts, seed=seed)}


def _recurrence(
    logs: Sequence[Any], model: ModelDecider, formula: FormulaDecider
) -> dict[str, Any]:
    """How recurrences are grouped: first occurrences against later ones, per decider."""
    out: dict[str, Any] = {}
    for name, dec in (("model", model), ("formula", formula)):
        first: list[Outcome] = []
        later: list[Outcome] = []
        for log in logs:
            o = group(log, dec)
            rec = set(log.recurs)
            later.append(restrict(o, [i for i, t in enumerate(o.truth) if t in rec]))
            first.append(
                restrict(o, [i for i, t in enumerate(o.truth) if t in set(log.recurs.values())])
            )
        out[name] = {
            "first_occurrence": situation_metrics(first),
            "recurrence": situation_metrics(later),
        }
    return out


def print_validation(logs: Sequence[Any], scorer: gam.GamScorer, split: str = "valid") -> None:
    """The validation streams only — what the bar may be set against. Never a test split."""
    model = ModelDecider.of(scorer)
    mo = [group(log, model) for log in logs]
    fo = [group(log, FormulaDecider()) for log in logs]
    print(f"VALIDATION {split} ({len(logs)} streams) — the only numbers the bar may be set against")
    for name, outs in (("model", mo), ("formula", fo)):
        m = situation_metrics(outs)
        print(
            f"  {name:8s} "
            + "  ".join(f"{k}={m[k]:.3f}" for k in HEADLINE)
            + f"  (activations {int(m['activations'])}, incidents {int(m['incidents'])}, "
            f"unrecognised clears {int(m['unrecognised_clears'])})"
        )
    fams = sorted({f for o in mo for f in o.family})
    for fam in fams:
        a, b = situation_metrics(mo, fam), situation_metrics(fo, fam)
        print(
            f"    {fam:22s} incidents {int(a['incidents']):4d}  "
            + "  ".join(
                f"{k[:12]} {a[k]:.2f}/{b[k]:.2f}"
                for k in ("over_merge_rate", "under_merge_rate", "pairwise_f1", "repair_gestures")
            )
        )


def check_bar(
    evaluation: dict[str, Any], bar: Sequence[tuple[str, str, str, float]]
) -> dict[str, Any]:
    """Every check of the bar, on every test split or held-out family its scope names.

    ``min``/``max`` read the model's point estimate; ``diff_min``/``diff_max`` read model - formula
    on the same streams; ``ratio_max`` reads model / formula. A ratio against a formula at zero is
    met only by a model at zero.
    """
    places = {
        "splits": [(f"split {k}", v) for k, v in evaluation["splits"].items()],
        "held_out": [
            (f"held-out family {k}", v) for k, v in evaluation["held_out_families"].items()
        ],
    }
    missed: list[str] = []
    results: list[dict[str, Any]] = []
    for quantity, scope, kind, limit in bar:
        for where, arms in places[scope]:
            model = arms["model"][quantity]["point"]
            formula = arms["formula"][quantity]["point"]
            if kind == "min":
                value, ok = model, model >= limit
            elif kind == "max":
                value, ok = model, model <= limit
            elif kind == "diff_min":
                value, ok = model - formula, model - formula >= limit
            elif kind == "diff_max":
                value, ok = model - formula, model - formula <= limit
            elif kind == "ratio_max":
                value = model / formula if formula else (0.0 if model == 0 else math.inf)
                ok = value <= limit
            else:
                raise ValueError(f"unknown check kind {kind!r}")
            results.append(
                {
                    "quantity": quantity,
                    "where": where,
                    "kind": kind,
                    "limit": limit,
                    "value": round(value, 4),
                    "passed": ok,
                }
            )
            if not ok:
                missed.append(f"{where}: {quantity} {kind} {value:.4f}, required {limit}")
    return {"passed": not missed, "missed": missed, "checked": len(results), "checks": results}


def manifest(**kw: Any) -> dict[str, Any]:
    document: str = kw["document"]
    trials: list[search.Trial] = kw["trials"]
    best: search.Trial = kw["best"]
    fit: gam_fit.FitResult = kw["fit"]
    return {
        "artifact": {
            "file": "linkmodel.json",
            "kind": gam.KIND,
            "format": gam.FORMAT,
            "sha256": hashlib.sha256(document.encode()).hexdigest(),
            "bytes": len(document.encode()),
            "features": list(kw["features"]),
        },
        "provenance": {
            "trained_with_version": kw["version"],
            "commit": kw["commit"],
            "seed": kw["seed"],
            "scale": kw["scale"],
            "dataset_digest": kw["data_digest"],
            "command": "make train",
            "training_rows": kw["rows"][0],
            "validation_rows": kw["rows"][1],
            "seconds": round(kw["seconds"], 1),
            "data": "generated (eval/synth), never site data",
            "held_out_families": list(dataset.HOLDOUT_OPTICAL + dataset.HOLDOUT_PROTOCOL),
            "training_families": list(dataset.TRAIN_FAMILIES),
        },
        "ablation": kw["ablation"],
        "search": {
            "space": {k: list(v) for k, v in search.SPACE.items()},
            "trials": [t.as_dict() for t in trials],
            "best": best.as_dict(),
            "importance": search.importance(trials),
        },
        "final_fit": {
            "params": kw["final_params"].as_dict(),
            "best_round": fit.best_round,
            "train_trace": [round(v, 6) for v in fit.train_trace],
            "valid_trace": [round(v, 6) for v in fit.valid_trace],
            "trace_every": fit.trace_every,
        },
        "grouping_grid": kw["grouping_grid"],
        "evaluation": kw["evaluation"],
        "quality_bar": [
            {"quantity": q, "scope": sc, "kind": k, "limit": lim} for q, sc, k, lim in kw["bar"]
        ],
        "verdict": kw["verdict"],
        # `[y, w, *vector]` per generated test pair: the site judge's do-no-harm benchmark.
        "benchmark": kw["benchmark"],
    }
