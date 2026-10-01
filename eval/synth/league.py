"""Training the model league: one pinned command, five families, one dataset, one scorecard each.

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
from netcorenoc.engine.model import gam_fit, league, linear_fit, search, trees_fit  # noqa: E402

from synth import dataset, report, train, tuning  # noqa: E402

LEAGUE_DIR = train.SHIPPED / league.DIRECTORY
BENCHMARK = "benchmark.json"
BENCHMARK_FORMAT = "netcorenoc.benchmark/1"

#: The search space of each member. Bounded so a member fits the fast loop: a forest of at most
#: sixty trees of depth eight, a boosted model of at most 400 rounds of depth five (ADR #424).
SPACES: dict[str, tuning.Space] = {
    "gam": dict(search.SPACE),
    "boosted_trees": {
        "learning_rate": ("log", 0.03, 0.3),
        "max_depth": ("int", 2, 5),
        "min_leaf": ("log", 0.02, 3.0),
        "l2": ("log", 0.1, 20.0),
        "subsample": ("float", 0.3, 1.0),
        "max_bins": ("int", 16, 48),
    },
    "random_forest": {
        "max_depth": ("int", 4, 8),
        "min_leaf": ("log", 0.5, 30.0),
        "subsample": ("float", 0.2, 0.7),
        "colsample": ("float", 0.2, 0.8),
        "prior": ("log", 0.5, 8.0),
        "max_bins": ("int", 16, 48),
    },
    "decision_tree": {
        "max_depth": ("int", 3, 10),
        "min_leaf": ("log", 0.5, 60.0),
        "prior": ("log", 0.5, 8.0),
        "max_bins": ("int", 16, 64),
    },
    "logistic_regression": {"l2": ("log", 1e-6, 1e-1), "log_counts": ("int", 0, 1)},
}
#: Successive-halving rungs: boosting rounds, forest trees, or one rung for the kinds whose fit
#: has no budget to halve.
RUNGS: dict[str, tuple[int, ...]] = {
    "gam": (40, 120, 320),
    "boosted_trees": (40, 120, 360),
    "random_forest": (10, 30, 60),
    "decision_tree": (1,),
    "logistic_regression": (1,),
}
TRIALS = {
    "gam": 12,
    "boosted_trees": 12,
    "random_forest": 9,
    "decision_tree": 16,
    "logistic_regression": 8,
}
FINAL = {
    "gam": 600,
    "boosted_trees": 400,
    "random_forest": 60,
    "decision_tree": 1,
    "logistic_regression": 1,
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


def train_member(kind: str) -> dict[str, Any]:
    """Everything for one member, checkpointed under the dataset root. Runs in a child process."""
    root: Path = _DATA["root"]
    seed: int = _DATA["seed"]
    code = train._digest(
        "src/netcorenoc/engine/model/gam_fit.py",
        "src/netcorenoc/engine/model/trees_fit.py",
        "src/netcorenoc/engine/model/linear_fit.py",
        "src/netcorenoc/engine/correlate/grouping.py",
        "src/netcorenoc/engine/correlate/features.py",
        "eval/synth/tuning.py",
        "eval/synth/dataset.py",
    )
    tag = f"{kind}-{seed}-{code}-{'_'.join(_DATA['features'])[:40]}"
    done_path = root / f"league-{tag}.json"
    if done_path.exists():
        print(f"  [{kind}] reusing {done_path.name}", file=sys.stderr, flush=True)
        return dict(json.loads(done_path.read_text()))
    t0 = time.time()
    trials = _search(kind, seed, root / f"league-trials-{tag}.jsonl")
    best = tuning.best(trials)
    f0 = time.time()
    final = fit_member(kind, best.params, FINAL[kind], seed)
    fit_seconds = time.time() - f0
    scorer = league.KINDS[kind][1](final.document, kind)
    grouping, grid = train.tune_grouping(_DATA["tuning"], scorer, fallback=True)
    document = train.with_grouping(final.document, grouping)
    scorer = league.KINDS[kind][1](document, kind)
    for name, logs in _DATA["tuning"].items():
        report.print_validation(logs, scorer, f"{name} [{kind}]")
    out: dict[str, Any] = {
        "kind": kind,
        "document": document,
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
            "seconds": round(fit_seconds, 1),
        },
        "grouping": grouping.as_dict(),
        "grouping_grid": grid,
        "grouping_admissible": any(r["admissible"] for r in grid),
        "search_seconds": round(f0 - t0, 1),
    }
    if _DATA["stage"] == "ship":
        out["evaluation"] = report.evaluate(root, scorer, seed)
        out["corpus"] = corpus(kind, document)
        out["latency"] = latency(scorer, _DATA["bench"])
        done_path.write_text(json.dumps(out))
    return out


def corpus(kind: str, document: str) -> dict[str, Any]:
    """`eval/corpus` replayed through the real engine with this member as the only league member —
    so it decides every link, exactly as it would on an appliance (the independent yardstick)."""
    from netcorenoc.engine.model import shipped

    import harness  # eval/harness.py: BER-encodes, parses and ingests every corpus trap

    manifest = {"artifact": {"sha256": hashlib.sha256(document.encode()).hexdigest(), "kind": kind}}
    member = league.member_from(kind, document, json.dumps(manifest))
    saved_league, saved_shipped = league.load, shipped.load
    league.load = lambda: league.League((member,))  # type: ignore[assignment]
    shipped.load = lambda: shipped.Shipped(  # type: ignore[assignment]
        member.document,
        member.sha256,
        member.manifest,
        member.scorer,  # type: ignore[arg-type]
    )
    try:
        out = asyncio.run(harness.run_all())
    finally:
        league.load, shipped.load = saved_league, saved_shipped  # type: ignore[assignment]
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
    train_logs = list(dataset.load_split(root, "train"))
    long_logs = list(dataset.load_split(root, "train_long"))
    valid_logs = list(dataset.load_split(root, "valid"))
    rows_train = dataset.training_rows(train_logs, seed=args.seed) + dataset.training_rows(
        long_logs, seed=args.seed, time_window=(0.0, 0.7)
    )
    rows_valid = dataset.training_rows(valid_logs, seed=args.seed)
    if not rows_train or any(len(r.x) != len(FEATURE_NAMES) for r in rows_train[:1000]):
        raise SystemExit(f"the recorded vectors are not {len(FEATURE_NAMES)} long; re-record")
    rows_train = train._cap(rows_train, train.MAX_TRAIN_ROWS, args.seed)
    rows_valid = train._cap(rows_valid, train.MAX_VALID_ROWS, args.seed + 1)
    print(f"rows: train {len(rows_train)}, valid {len(rows_valid)}", file=sys.stderr, flush=True)
    abl_code = train._digest(
        "src/netcorenoc/engine/model/gam_fit.py", "src/netcorenoc/engine/correlate/features.py"
    )
    abl_path = root / f"ablation-{args.seed}-{abl_code}.json"
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
                dataset.training_rows(list(dataset.load_split(root, "test_iid")), seed=args.seed),
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
        stage=args.stage,
        features=features,
        train_ds=train.to_dataset(rows_train, features),
        valid_ds=train.to_dataset(rows_valid, features),
        tuning={
            "valid": valid_logs,
            "valid_concurrency": list(dataset.load_split(root, "valid_concurrency")),
        },
        bench=bench,
    )
    ctx = multiprocessing.get_context("fork")
    with ProcessPoolExecutor(
        max_workers=args.workers or min(4, len(kinds)), mp_context=ctx
    ) as pool:
        results = list(pool.map(train_member, kinds))
    if args.stage == "validate":
        return 0
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
            "training_rows": len(rows_train),
            "validation_rows": len(rows_valid),
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

    members = league.load_dir(out)  # type: ignore[arg-type]
    for kind, reason in members.refused:
        print(f"REFUSED {kind}: {reason}", file=sys.stderr)
    table = judge.offline_table(members)
    print("\nTHE LEAGUE (the judge's offline order)")
    for row in table:
        suites = "  ".join(f"{k} {v:.3f}" for k, v in row["suites"].items())
        print(f"  {row['rank']}. {row['name']:28s} score {row['score']:.4f}  {suites}")


if __name__ == "__main__":
    raise SystemExit(main())
