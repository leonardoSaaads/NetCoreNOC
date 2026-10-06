"""Training the model league: one pinned command, seven families, one dataset, one scorecard each.

    make train            # == python -m synth.league  (from eval/)

v0.27.0 (ADRs #424, #426). The steps, each deterministic from the pinned seed:

1. **Build** the recorded streams (`dataset.build`, cached by digest) and the training rows —
   ``train`` plus the older 70 % of ``train_long`` — and validation rows from ``valid``.
2. **Ablation** (the GAM's drop-one test, v0.26.0): the kept features are **shared by every
   member**, so the league compares estimators, not inputs.
3. Per member, in parallel processes: **search** (`tuning.halving`, every trial recorded), **final
   fit** at full capacity with validation choosing the rounds, trees or depth, **grouping** chosen
   on the validation streams under the quality bar (`train.tune_grouping`), **evaluation** on every
   test split and held-out family against the formula, the **hand-labelled corpus** replayed
   through the real engine with this member deciding, and **latency** on the benchmark pairs.
4. **Write** every member — document and manifest — whatever its scorecard says. The bar is
   recorded per member and read by the judge; it no longer decides what ships (ADR #426).
5. **Print** the league table in the judge's own order (`netcorenoc.engine.model.judge`), so the
   build log shows which member an appliance will start with.

Nothing here reads a test split before step 3's evaluation, and no test number feeds back into a
choice: search, final fit and grouping read only ``train`` and the validation streams.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import hashlib
import json
import multiprocessing
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from netcorenoc import __version__  # noqa: E402
from netcorenoc.engine.correlate.features import FEATURE_NAMES  # noqa: E402
from netcorenoc.engine.model import (  # noqa: E402
    gam_fit,
    knn_fit,
    league,
    linear_fit,
    search,
    trees_fit,
    xgb_fit,
)

from synth import dataset, report, train, tuning  # noqa: E402

LEAGUE_DIR = train.SHIPPED / league.DIRECTORY
BENCHMARK = "benchmark.json"
BENCHMARK_FORMAT = "netcorenoc.benchmark/1"

#: The search space of each member, reviewed against overfitting in v0.29.0 (ADR #438). Every
#: weight here is in training-weight units — one activation is one unit, so a leaf's Hessian floor
#: is a number of activations' worth of evidence, not a count of rows. The changes, and why:
#:
#: * floors raised where a leaf could rest on a sliver of one activation (boosted ``min_leaf``
#:   0.02 -> 0.2, forest 0.5 -> 1, tree 0.5 -> 2), and the L2 floors with them;
#: * row subsampling no lower than half the rows for the boosters, and column sampling added to
#:   ``boosted_trees`` — decorrelated rounds overfit later;
#: * the single tree capped at depth 8 (validation still cuts it shorter);
#: * the GAM's learning rate and subsample narrowed to where its earlier searches settled.
#:
#: Bounded so a member fits the fast loop: forests of at most sixty trees of depth eight, boosted
#: models of at most 600 rounds of depth six, a k-NN of at most 512 prototypes (ADR #424, #438).
SPACES: dict[str, tuning.Space] = {
    "gam": {
        **dict(search.SPACE),
        "learning_rate": ("log", 0.02, 0.2),
        "min_hessian": ("log", 0.3, 20.0),
        "subsample": ("float", 0.5, 1.0),
    },
    "boosted_trees": {
        "learning_rate": ("log", 0.02, 0.2),
        "max_depth": ("int", 2, 5),
        "min_leaf": ("log", 0.2, 10.0),
        "l2": ("log", 0.5, 50.0),
        "subsample": ("float", 0.5, 1.0),
        "colsample": ("float", 0.5, 1.0),
        "max_bins": ("int", 16, 48),
    },
    "random_forest": {
        "max_depth": ("int", 4, 8),
        "min_leaf": ("log", 1.0, 50.0),
        "subsample": ("float", 0.2, 0.7),
        "colsample": ("float", 0.2, 0.8),
        "prior": ("log", 0.5, 8.0),
        "max_bins": ("int", 16, 48),
    },
    "decision_tree": {
        "max_depth": ("int", 3, 8),
        "min_leaf": ("log", 2.0, 100.0),
        "prior": ("log", 0.5, 8.0),
        "max_bins": ("int", 16, 64),
    },
    "logistic_regression": {"l2": ("log", 1e-6, 1e-1), "log_counts": ("int", 0, 1)},
    "xgboost": {
        # eta from 0.05 and at most 300 rounds: a tree costs ~0.3 µs per pair on the reference
        # host, so 300 stay inside the 150 µs budget with room for a slower appliance.
        "eta": ("log", 0.05, 0.3),
        "max_depth": ("int", 2, 6),
        "min_child_weight": ("log", 0.1, 10.0),
        "gamma": ("log", 1e-3, 2.0),
        "reg_lambda": ("log", 0.5, 50.0),
        "reg_alpha": ("log", 1e-3, 1.0),
        "max_delta_step": ("float", 0.5, 5.0),
        "subsample": ("float", 0.5, 1.0),
        "colsample_bytree": ("float", 0.5, 1.0),
        "colsample_bynode": ("float", 0.5, 1.0),
        "max_bin": ("int", 16, 48),
    },
    "knn": {
        # Capped at 384: the scorer costs ~0.18 µs per prototype per pair on the reference host,
        # and a member over the 150 µs budget is ineligible to decide (`league_judge`).
        "prototypes": ("log", 64.0, 384.0),
        "weighting": ("int", 0, 1),
        "metric_power": ("float", 0.0, 2.0),
        "prior": ("log", 0.5, 32.0),
    },
}
#: Successive-halving rungs: boosting rounds, forest trees, or one rung for the kinds whose fit
#: has no budget to halve.
RUNGS: dict[str, tuple[int, ...]] = {
    "gam": (40, 120, 320),
    "boosted_trees": (40, 120, 400),
    "random_forest": (10, 30, 60),
    "decision_tree": (1,),
    "logistic_regression": (1,),
    "xgboost": (40, 120, 300),
    "knn": (1,),
}
#: v0.29.0: about twice v0.27.0's trials — a wider search, on more validation data (ADR #438).
#: The focused round (ADR #442) is a shorter one, on the same reviewed spaces: about half of them.
TRIALS = {
    "gam": 10,
    "boosted_trees": 12,
    "random_forest": 8,
    "decision_tree": 12,
    "logistic_regression": 6,
    "xgboost": 14,
    "knn": 10,
}
FINAL = {
    "gam": 600,
    "boosted_trees": 600,
    "random_forest": 60,
    "decision_tree": 1,
    "logistic_regression": 1,
    "xgboost": 300,
    "knn": 1,
}

# Filled in the parent before the pool forks, so each child reads the same rows without a copy.
_DATA: dict[str, Any] = {}


@dataclass
class Fit:
    document: str
    capacity: str
    best: int
    points: list[int]
    train_trace: list[float]
    valid_trace: list[float]


def fit_member(kind: str, p: dict[str, float], capacity: int, seed: int) -> Fit:
    """One fit of ``kind`` at ``capacity``. Threshold 0 (probability ½); grouping comes later."""
    tr, va = _DATA["train_ds"], _DATA["valid_ds"]
    if kind == "gam":
        r = gam_fit.fit(tr, va, search._params(p, capacity, seed), threshold=0.0)
        points = [r.trace_every * (i + 1) for i in range(len(r.valid_trace))]
        return Fit(r.document, "rounds", r.best_round, points, r.train_trace, r.valid_trace)
    if kind == "logistic_regression":
        lr = linear_fit.fit(
            tr, va, linear_fit.LinearParams(l2=p["l2"], log_counts=bool(round(p["log_counts"])))
        )
        return Fit(lr.document, lr.capacity, lr.best, lr.points, lr.train_trace, lr.valid_trace)
    if kind == "xgboost":
        xp = xgb_fit.XGBParams(
            eta=p["eta"],
            max_depth=int(p["max_depth"]),
            min_child_weight=p["min_child_weight"],
            gamma=p["gamma"],
            reg_lambda=p["reg_lambda"],
            reg_alpha=p["reg_alpha"],
            max_delta_step=p["max_delta_step"],
            subsample=p["subsample"],
            colsample_bytree=p["colsample_bytree"],
            colsample_bynode=p["colsample_bynode"],
            max_bin=int(p["max_bin"]),
            rounds=capacity,
            seed=seed,
        )
        x = xgb_fit.fit(tr, va, xp, threshold=0.0)
        return Fit(x.document, x.capacity, x.best, x.points, x.train_trace, x.valid_trace)
    if kind == "knn":
        kp = knn_fit.KnnParams(
            prototypes=round(p["prototypes"]),
            weighting="distance" if round(p["weighting"]) else "uniform",
            metric_power=p["metric_power"],
            prior=p["prior"],
            seed=seed,
        )
        k = knn_fit.fit(tr, va, kp, threshold=0.0)
        return Fit(k.document, k.capacity, k.best, k.points, k.train_trace, k.valid_trace)
    params = trees_fit.TreeParams(
        method=kind,
        max_depth=int(p["max_depth"]),
        min_leaf=p["min_leaf"],
        max_bins=int(p["max_bins"]),
        trees=capacity,
        learning_rate=p.get("learning_rate", 0.1),
        l2=p.get("l2", 1.0),
        subsample=p.get("subsample", 1.0),
        colsample=p.get("colsample", 1.0),
        prior=p.get("prior", 2.0),
        seed=seed,
    )
    t = trees_fit.fit(tr, va, params, threshold=0.0)
    return Fit(t.document, t.capacity, t.best, t.points, t.train_trace, t.valid_trace)


def _search(kind: str, seed: int, cache: Path) -> list[tuning.Trial]:
    """The member's search, resumed from its checkpoint (one trial per line)."""
    done = [
        tuning.Trial(**json.loads(line))
        for line in (cache.read_text().splitlines() if cache.exists() else [])
    ]

    def fit(p: dict[str, float], capacity: int) -> tuning.Fitted:
        r = fit_member(kind, p, capacity, seed)
        best = min(r.valid_trace)
        return tuning.Fitted(best, r.train_trace[r.valid_trace.index(best)], r.best)

    def keep(t: tuning.Trial) -> None:
        with cache.open("a") as fh:
            fh.write(json.dumps(t.as_dict()) + "\n")
        print(
            f"  [{kind}] trial {t.index} rung {t.rung} capacity {t.capacity} "
            f"valid {t.valid_loss:.5f} ({t.seconds:.0f}s) {t.status}",
            file=sys.stderr,
            flush=True,
        )

    return tuning.halving(
        kind,
        SPACES[kind],
        fit,
        trials=TRIALS[kind],
        rungs=RUNGS[kind],
        seed=seed,
        done=done,
        on_trial=keep,
    )


def _tag(kind: str) -> str:
    code = train._digest(
        "src/netcorenoc/engine/model/gam_fit.py",
        "src/netcorenoc/engine/model/trees_fit.py",
        "src/netcorenoc/engine/model/trees_grow.py",
        "src/netcorenoc/engine/model/linear_fit.py",
        "src/netcorenoc/engine/model/xgb_fit.py",
        "src/netcorenoc/engine/model/knn_fit.py",
        "src/netcorenoc/engine/model/knn.py",
        "src/netcorenoc/engine/correlate/grouping.py",
        "src/netcorenoc/engine/correlate/features.py",
        "eval/synth/tuning.py",
        "eval/synth/dataset.py",
        # The training weights and the grouping grid live here (ADR #442): a change to either is
        # a new fit, not a cached one.
        "eval/synth/train.py",
    )
    weights = _DATA.get("weights", "activation")
    return f"{kind}-{_DATA['seed']}-{weights[0]}{code}-{'_'.join(_DATA['features'])[:40]}"


def fit_phase(kind: str) -> dict[str, Any]:
    """Phase 1, in a child process: the search and the final fit. Light on memory — it reads only
    the column-major training and validation rows — so every kind can run at once."""
    root: Path = _DATA["root"]
    seed: int = _DATA["seed"]
    done_path = root / f"league-fit-{_tag(kind)}.json"
    if done_path.exists():
        print(f"  [{kind}] reusing {done_path.name}", file=sys.stderr, flush=True)
        return dict(json.loads(done_path.read_text()))
    t0 = time.time()
    trials = _search(kind, seed, root / f"league-trials-{_tag(kind)}.jsonl")
    best = tuning.best(trials)
    f0 = time.time()
    final = fit_member(kind, best.params, FINAL[kind], seed)
    out: dict[str, Any] = {
        "kind": kind,
        "fit_document": final.document,
        "trials": [t.as_dict() for t in trials],
        "best": best.as_dict(),
        "importance": tuning.importance(trials),
        "final_fit": {
            "params": best.params,
            "capacity": final.capacity,
            "best": final.best,
            "points": final.points,
            "train_trace": [round(v, 6) for v in final.train_trace],
            "valid_trace": [round(v, 6) for v in final.valid_trace],
            "seconds": round(time.time() - f0, 1),
        },
        "search_seconds": round(f0 - t0, 1),
    }
    done_path.write_text(json.dumps(out))
    return out


def grouping_phase(fitted: dict[str, Any], tuning_logs: dict[str, Any]) -> dict[str, Any]:
    """Phase 2a, in the parent, one kind at a time: the grouping biases on the validation streams
    under the quality bar, and the validation numbers printed before any test split is read."""
    kind = fitted["kind"]
    path = _DATA["root"] / f"league-grouping-{_tag(kind)}.json"
    if path.exists():
        return {**fitted, **json.loads(path.read_text())}
    scorer = league.KINDS[kind][1](fitted["fit_document"], kind)
    grouping, grid = train.tune_grouping(tuning_logs, scorer, fallback=True)
    document = train.with_grouping(fitted["fit_document"], grouping)
    scorer = league.KINDS[kind][1](document, kind)
    for name, logs in tuning_logs.items():
        report.print_validation(logs, scorer, f"{name} [{kind}]")
    extra = {
        "document": document,
        "grouping": grouping.as_dict(),
        "grouping_grid": grid,
        "grouping_admissible": any(r["admissible"] for r in grid),
    }
    path.write_text(json.dumps(extra))
    return {**fitted, **extra}


def evaluation_phase(member: dict[str, Any]) -> dict[str, Any]:
    """Phase 2b, in the parent, one kind at a time: the test splits, the corpus, the latency."""
    kind = member["kind"]
    path = _DATA["root"] / f"league-eval-{_tag(kind)}.json"
    if path.exists():
        return {**member, **json.loads(path.read_text())}
    scorer = league.KINDS[kind][1](member["document"], kind)
    print(f"  [{kind}] evaluating on the test splits", file=sys.stderr, flush=True)
    extra = {
        "evaluation": report.evaluate(_DATA["root"], scorer, _DATA["seed"]),
        "corpus": corpus(kind, member["document"]),
        "latency": latency(scorer, _DATA["bench"]),
    }
    path.write_text(json.dumps(extra))
    gc.collect()
    return {**member, **extra}


def corpus(kind: str, document: str) -> dict[str, Any]:
    """`eval/corpus` replayed through the real engine with this member as the only league member —
    so it decides every link, exactly as it would on an appliance (the independent yardstick)."""
    from netcorenoc.engine.model import shipped

    import harness  # eval/harness.py: BER-encodes, parses and ingests every corpus trap

    manifest = {"artifact": {"sha256": hashlib.sha256(document.encode()).hexdigest(), "kind": kind}}
    member = league.member_from(kind, document, json.dumps(manifest))
    saved_league, saved_shipped = league.load, shipped.load
    league.load = lambda: league.League((member,))  # type: ignore[assignment]
    shipped.load = lambda: shipped.Shipped(
        member.document,
        member.sha256,
        member.manifest,
        member.scorer,  # type: ignore[arg-type]
    )
    try:
        out = asyncio.run(harness.run_all())
    finally:
        league.load, shipped.load = saved_league, saved_shipped
    keys = ("pairwise_f1", "ari", "over_merge_rate", "under_merge_rate")
    return {
        "dataset": "eval/corpus (hand-labelled, generated by a different program)",
        "aggregate": {k: round(float(out["aggregate"][k]), 4) for k in keys},
        "scenarios": {
            name: {k: round(float(m[k]), 4) for k in keys}
            for name, m in sorted(out["scenarios"].items())
        },
    }


def latency(scorer: Any, bench: list[list[float]]) -> dict[str, Any]:
    """Per-pair scoring and explanation time on the benchmark pairs — on the training machine,
    and labelled as such; the appliance measures its own at load (ADR #423)."""
    vectors = [tuple(r[2:]) for r in bench]
    fast: list[float] = []
    for _ in range(3):
        t0 = time.perf_counter()
        for v in vectors:
            scorer.logit(v)
        fast.append((time.perf_counter() - t0) / len(vectors) * 1e6)
    t0 = time.perf_counter()
    for v in vectors[:200]:
        scorer.explain(v)
    explain = (time.perf_counter() - t0) / min(200, len(vectors)) * 1e6
    return {
        "logit_us": round(statistics.median(fast), 2),
        "explain_us": round(explain, 1),
        "pairs": len(vectors),
        "measured_on": "the training machine",
    }


def manifest_of(r: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    kind, document = r["kind"], r["document"]
    verdict = report.check_bar(r["evaluation"], train.QUALITY_BAR)
    return {
        "artifact": {
            "file": f"{kind}.json",
            "kind": kind,
            "name": league.KINDS[kind][0],
            "sha256": hashlib.sha256(document.encode()).hexdigest(),
            "bytes": len(document.encode()),
            "features": list(common["features"]),
        },
        "provenance": {**common["provenance"], "search_seconds": r["search_seconds"]},
        "ablation": common["ablation"],
        "search": {
            "space": {k: list(v) for k, v in SPACES[kind].items()},
            "rungs": list(RUNGS[kind]),
            "trials": r["trials"],
            "best": r["best"],
            "importance": r["importance"],
            "importance_method": "Spearman rho squared, first rung",
        },
        "final_fit": r["final_fit"],
        "grouping": r["grouping"],
        "grouping_grid": r["grouping_grid"],
        "grouping_admissible": r["grouping_admissible"],
        "evaluation": r["evaluation"],
        "corpus": r["corpus"],
        "latency": r["latency"],
        "benchmark_sha256": common["benchmark_sha256"],
        "quality_bar": [
            {"quantity": q, "scope": sc, "kind": k, "limit": lim}
            for q, sc, k, lim in train.QUALITY_BAR
        ],
        # A scorecard, not a gate (ADR #426): the judge reads it; nothing here withholds a member.
        "scorecard": verdict,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Train the model league.")
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=dataset.SEED)
    parser.add_argument("--out", type=Path, default=LEAGUE_DIR)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--kinds", default=",".join(league.KINDS))
    parser.add_argument("--stage", choices=("validate", "ship"), default="ship")
    parser.add_argument(
        "--weights",
        choices=("activation", "balanced"),
        default="activation",
        help="training weight: one unit per activation (shipped), or that balanced over time-gap "
        "bands (`train.balance_gaps`; measured and not adopted, ADR #442)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="a recorded dataset directory to train on instead of building one (development "
        "only: its digest is the recording's, and the manifest says so)",
    )
    args = parser.parse_args()
    kinds = [k for k in args.kinds.split(",") if k]
    unknown = [k for k in kinds if k not in league.KINDS]
    if unknown:
        raise SystemExit(f"unknown kinds: {unknown}")
    t0 = time.time()
    root = args.root.resolve() if args.root else dataset.build(args.scale, args.seed, args.workers)
    print(f"dataset {root.name} ready in {time.time() - t0:.0f}s", file=sys.stderr, flush=True)
    # Streams are read one at a time (`load_split` is a generator): with the proxied storms a
    # split's logs together are several gigabytes, and only the sampled rows need to stay.
    rows_train = train.training_rows(root, args.seed)
    rows_valid = train.validation_rows(root, args.seed)
    if not rows_train or any(len(r.x) != len(FEATURE_NAMES) for r in rows_train[:1000]):
        raise SystemExit(f"the recorded vectors are not {len(FEATURE_NAMES)} long; re-record")
    # ADR #442: `--weights balanced` balances the weight over time-gap bands, in training and in the
    # validation loss the search and the early stop read; measured against the default and not
    # adopted.
    rows_train = train._cap(rows_train, train.MAX_TRAIN_ROWS, args.seed)
    rows_valid = train._cap(rows_valid, train.MAX_VALID_ROWS, args.seed + 1)
    if args.weights == "balanced":
        rows_train, rows_valid = train.balance_gaps(rows_train), train.balance_gaps(rows_valid)
    print(f"rows: train {len(rows_train)}, valid {len(rows_valid)}", file=sys.stderr, flush=True)
    # The rule that keeps a feature is code too (v0.29.0, `train.kept_features`): a change to it
    # is a new ablation, not a cached one.
    abl_code = train._digest(
        "src/netcorenoc/engine/model/gam_fit.py",
        "src/netcorenoc/engine/correlate/features.py",
        "eval/synth/train.py",
    )
    abl_path = root / f"ablation-{args.seed}-{args.weights[0]}{abl_code}.json"
    if abl_path.exists():
        abl = json.loads(abl_path.read_text())
    else:
        abl = train.ablation(rows_train, rows_valid, args.seed)
        abl_path.write_text(json.dumps(abl))
    features = tuple(abl["kept"])
    bench = (
        [
            [r.y, round(r.w, 6), *(round(v, 6) for v in r.x)]
            for r in train._cap(
                dataset.training_rows(dataset.load_split(root, "test_iid"), seed=args.seed),
                train.BENCHMARK_ROWS,
                args.seed + 2,
            )
        ]
        if args.stage == "ship"
        else []
    )
    _DATA.update(
        root=root,
        seed=args.seed,
        weights=args.weights,
        stage=args.stage,
        features=features,
        train_ds=train.to_dataset(rows_train, features),
        valid_ds=train.to_dataset(rows_valid, features),
        bench=bench,
    )
    # The recorded streams are large (the proxied storms put hundreds of candidates on every
    # activation): free everything row-shaped before forking, so each child holds only the
    # column-major arrays, and load the streams once, here, after the children are done.
    n_train, n_valid = len(rows_train), len(rows_valid)
    del rows_train, rows_valid
    gc.collect()
    ctx = multiprocessing.get_context("fork")
    with ProcessPoolExecutor(
        max_workers=args.workers or min(4, len(kinds)), mp_context=ctx
    ) as pool:
        fitted = list(pool.map(fit_phase, kinds))
    tuning_logs = {
        "valid": list(dataset.load_split(root, "valid")),
        "valid_concurrency": list(dataset.load_split(root, "valid_concurrency")),
        "valid_adverse": list(dataset.load_split(root, "valid_adverse")),
        "valid_spread": list(dataset.load_split(root, "valid_spread")),
    }
    grouped = [grouping_phase(f, tuning_logs) for f in fitted]
    del tuning_logs
    gc.collect()
    if args.stage == "validate":
        return 0
    results = [evaluation_phase(g) for g in grouped]
    bench_doc = json.dumps(
        {
            "format": BENCHMARK_FORMAT,
            "features": list(FEATURE_NAMES),
            "source": "test_iid pairs (generated, never trained or tuned on), whole activations",
            "rows": bench,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    common = {
        "features": features,
        "ablation": abl,
        "benchmark_sha256": hashlib.sha256(bench_doc.encode()).hexdigest(),
        "provenance": {
            "trained_with_version": __version__,
            "commit": train._commit(),
            "seed": args.seed,
            "scale": args.scale,
            "dataset_digest": root.name,
            "command": "make train",
            "training_rows": n_train,
            "training_weights": args.weights,
            "validation_rows": n_valid,
            "seconds": round(time.time() - t0, 1),
            "data": "generated (eval/synth), never site data",
            "held_out_families": list(dataset.HOLDOUT_OPTICAL + dataset.HOLDOUT_PROTOCOL),
            "training_families": list(dataset.TRAIN_FAMILIES),
        },
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / BENCHMARK).write_text(bench_doc)
    for r in results:
        (args.out / f"{r['kind']}.json").write_text(r["document"])
        (args.out / f"{r['kind']}.manifest.json").write_text(
            json.dumps(manifest_of(r, common), indent=1, sort_keys=True)
        )
    _print_table(args.out)
    return 0


def _print_table(out: Path) -> None:
    from netcorenoc.engine.model import league_judge as judge

    members = league.load_dir(out)
    for kind, reason in members.refused:
        print(f"REFUSED {kind}: {reason}", file=sys.stderr)
    table = judge.offline_table(members)
    print("\nTHE LEAGUE (the judge's offline order)")
    for row in table:
        suites = "  ".join(f"{k} {v:.3f}" for k, v in row["suites"].items())
        print(f"  {row['rank']}. {row['name']:28s} score {row['score']:.4f}  {suites}")


if __name__ == "__main__":
    raise SystemExit(main())
