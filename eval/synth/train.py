"""Training the shipped model: one pinned command, from generated streams to a validated artifact.

    make train            # == python -m synth.train  (from eval/)

The steps, each deterministic from the pinned seed:

1. **Build** the recorded streams (`dataset.build`, cached by digest).
2. **Rows**: labelled pairs from ``train`` and the older 70 % of ``train_long``; validation rows from
   ``valid``. One unit of weight per activation (`dataset.training_rows`).
3. **Ablation**: fit with every candidate feature, then without each one in turn; a feature whose
   removal does not worsen validation log loss by at least :data:`KEEP_IF_WORSE_BY` is dropped.
   Part III.2: *add them deliberately, measure each one's contribution, drop what does not pay.*
4. **Search** over the kept features (`netcorenoc.engine.model.search`): random search, then
   successive halving on validation log loss, every trial recorded.
5. **Final fit** with the best parameters, early-stopped on validation.
6. **Grouping**: the join and merge biases chosen on the *validation streams'* situation-level
   quantities, with the model fixed.
7. **Evaluation** on every test split and on the independent yardsticks last, against the additive
   formula on the same streams, with cluster-bootstrap intervals over streams.
8. **The quality bar** (:data:`QUALITY_BAR`): an artifact that misses any threshold is **not
   written**, and the command exits non-zero.
9. **Write** the artifact (the model document) and its manifest (provenance and every number).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from netcorenoc import __version__  # noqa: E402
from netcorenoc.engine.correlate.features import FEATURE_NAMES  # noqa: E402
from netcorenoc.engine.correlate.grouping import GroupingParams, Scored  # noqa: E402
from netcorenoc.engine.model import gam, gam_fit, search  # noqa: E402

from synth import dataset  # noqa: E402
from synth.evaluate import ModelDecider, group, situation_metrics  # noqa: E402
from synth.record import Activation, StreamLog  # noqa: E402

REPO = HERE.parent.parent
SHIPPED = REPO / "src" / "netcorenoc" / "engine" / "model" / "shipped"
ARTIFACT = "linkmodel.json"
MANIFEST = "linkmodel.manifest.json"

MAX_TRAIN_ROWS = 160_000
MAX_VALID_ROWS = 50_000
ABLATION_ROWS = 60_000
KEEP_IF_WORSE_BY = 0.0005  # nats of validation log loss

#: The shipped artifact must meet every one of these on EVERY held-out test split (ADR #409).
#: Each is a direction and a number; the reasons are in the ADR.
QUALITY_BAR: dict[str, tuple[str, float]] = {
    "pairwise_f1": (">=", 0.90),
    "ari": (">=", 0.88),
    "over_merge_rate": ("<=", 0.10),
    "under_merge_rate": ("<=", 0.25),
    "split_bag_intact_rate": ("<=", 0.25),
    "asserted_negative_respected_rate": (">=", 0.90),
}


@dataclass
class CachedDecider:
    """A decider whose evidence was computed once per activation — for tuning grouping only."""

    evidence_by: dict[int, list[Scored]]
    mode: str
    params: GroupingParams

    def evidence(self, act: Activation) -> list[Scored]:
        return self.evidence_by[id(act)]


def _cap(rows: list[dataset.Row], limit: int, seed: int) -> list[dataset.Row]:
    """Keep whole activations, deterministically, until the row budget is met."""
    if len(rows) <= limit:
        return rows
    import random

    keys = sorted({(r.stream, r.ts, r.incident) for r in rows})
    random.Random(seed).shuffle(keys)
    budget = limit
    chosen: set[tuple[str, float, str]] = set()
    counts: dict[tuple[str, float, str], int] = {}
    for r in rows:
        k = (r.stream, r.ts, r.incident)
        counts[k] = counts.get(k, 0) + 1
    for k in keys:
        if counts[k] <= budget:
            chosen.add(k)
            budget -= counts[k]
    return [r for r in rows if (r.stream, r.ts, r.incident) in chosen]


def to_dataset(rows: list[dataset.Row], features: tuple[str, ...]) -> gam_fit.Dataset:
    picks = [FEATURE_NAMES.index(f) for f in features]
    return gam_fit.Dataset.from_rows(features, [r.x for r in rows], [r.y for r in rows], [r.w for r in rows], picks)


def _defaults(rounds: int, seed: int) -> gam_fit.FitParams:
    return gam_fit.FitParams(rounds=rounds, learning_rate=0.1, max_bins=24, max_leaves=3, l2=1.0,
                             min_hessian=1.0, subsample=0.6, interactions=0, seed=seed)


def ablation(train: list[dataset.Row], valid: list[dataset.Row], seed: int) -> dict[str, Any]:
    """Drop-one ablation on validation log loss. Returns the kept features and every delta."""
    candidates = tuple(FEATURE_NAMES)
    tr = _cap(train, ABLATION_ROWS, seed)
    va = _cap(valid, ABLATION_ROWS // 2, seed + 1)
    params = _defaults(150, seed)
    full = gam_fit.fit(to_dataset(tr, candidates), to_dataset(va, candidates), params)
    base = min(full.valid_trace)
    deltas: dict[str, float] = {}
    for name in candidates:
        rest = tuple(f for f in candidates if f != name)
        r = gam_fit.fit(to_dataset(tr, rest), to_dataset(va, rest), params)
        deltas[name] = min(r.valid_trace) - base
        print(f"  ablation: without {name:16s} Δ valid log loss {deltas[name]:+.5f}", file=sys.stderr)
    kept = tuple(f for f in candidates if deltas[f] >= KEEP_IF_WORSE_BY)
    return {"baseline_valid_log_loss": base, "delta_without": deltas, "kept": list(kept),
            "dropped": [f for f in candidates if f not in kept], "rows": [len(tr), len(va)]}


def _evidence_cache(logs: list[StreamLog], scorer: gam.GamScorer) -> dict[int, list[Scored]]:
    decider = ModelDecider.of(scorer)
    return {id(a): decider.evidence(a) for log in logs for a in log.activations()}


def grouping_error(m: dict[str, float]) -> float:
    """The scalar the grouping biases are chosen on: the mean of the four named rates, each
    oriented so lower is better. Stated, because a tuned parameter needs one number and this is it."""
    return (m["over_merge_rate"] + m["under_merge_rate"] + m["split_bag_intact_rate"]
            + (1.0 - m["asserted_negative_respected_rate"])) / 4.0


def tune_grouping(logs: list[StreamLog], scorer: gam.GamScorer) -> tuple[GroupingParams, list[dict[str, float]]]:
    cache = _evidence_cache(logs, scorer)
    rows: list[dict[str, float]] = []
    best: tuple[float, GroupingParams] | None = None
    for join in (-1.0, -0.5, 0.0, 0.5, 1.0):
        for merge in (0.0, 0.5, 1.0, 2.0, 3.0):
            if merge < join:
                continue
            for pairs in (2, 3, 6):
                params = GroupingParams(join, merge, pairs)
                decider = CachedDecider(cache, "cluster", params)
                m = situation_metrics([group(log, decider) for log in logs])
                err = grouping_error(m)
                rows.append({"join_bias": join, "merge_bias": merge, "merge_min_pairs": float(pairs),
                             "error": err, **{k: m[k] for k in ("over_merge_rate", "under_merge_rate",
                                                                "split_bag_intact_rate",
                                                                "asserted_negative_respected_rate")}})
                if best is None or err < best[0] - 1e-12:
                    best = (err, params)
    assert best is not None
    return best[1], rows


def with_grouping(document: str, params: GroupingParams) -> str:
    doc = json.loads(document)
    doc["grouping"] = {"join_bias": params.join_bias, "merge_bias": params.merge_bias,
                       "merge_min_pairs": float(params.merge_min_pairs)}
    return json.dumps(doc, sort_keys=True, separators=(",", ":"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Train the shipped link model.")
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=dataset.SEED)
    parser.add_argument("--trials", type=int, default=12)
    parser.add_argument("--out", type=Path, default=SHIPPED)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--skip-ablation", action="store_true")
    parser.add_argument(
        "--stage", choices=("validate", "ship"), default="ship",
        help="validate: fit and report on the validation streams only, write nothing; "
             "ship: evaluate on the test splits once, enforce the quality bar, write the artifact",
    )
    args = parser.parse_args()
    from synth import report  # the evaluation half; imported here to keep this module's size

    t0 = time.time()
    root = dataset.build(args.scale, args.seed, args.workers)
    data_digest = root.name
    print(f"dataset {data_digest} ready in {time.time() - t0:.0f}s", file=sys.stderr)
    train_logs = list(dataset.load_split(root, "train"))
    long_logs = list(dataset.load_split(root, "train_long"))
    valid_logs = list(dataset.load_split(root, "valid"))
    rows_train = dataset.training_rows(train_logs, seed=args.seed) + dataset.training_rows(
        long_logs, seed=args.seed, time_window=(0.0, 0.7))
    rows_valid = dataset.training_rows(valid_logs, seed=args.seed)
    rows_train = _cap(rows_train, MAX_TRAIN_ROWS, args.seed)
    rows_valid = _cap(rows_valid, MAX_VALID_ROWS, args.seed + 1)
    print(f"rows: train {len(rows_train)}, valid {len(rows_valid)}", file=sys.stderr)

    code = hashlib.sha256(b"".join(
        (REPO / "src/netcorenoc/engine/model" / f).read_bytes()
        for f in ("gam.py", "gam_fit.py", "search.py")
    ) + Path(__file__).read_bytes() + (REPO / "src/netcorenoc/engine/correlate/grouping.py").read_bytes()
    ).hexdigest()[:12]
    cache = root / f"fit-{args.seed}-{args.trials}-{int(args.skip_ablation)}-{code}.json"
    if cache.exists():
        saved = json.loads(cache.read_text())
        abl, trials, best = saved["ablation"], [search.Trial(**t) for t in saved["trials"]], None
        best = search.Trial(**saved["best"])
        features = tuple(abl["kept"])
        final_params = search._params(best.params, 600, args.seed)
        fit = gam_fit.FitResult(saved["fit_document"], saved["best_round"], saved["train_trace"],
                                saved["valid_trace"])
        grid = saved["grouping_grid"]
        document = saved["document"]
        print(f"reusing the cached fit {cache.name}", file=sys.stderr)
    else:
        # Each stage checkpoints, so a restarted run resumes rather than recomputes: the ablation
        # as one file, the search one trial per line (the search resumes from the trials it is
        # handed, and trial k's parameters are a pure function of the seed).
        fit_code = _digest("src/netcorenoc/engine/model/gam_fit.py",
                           "src/netcorenoc/engine/correlate/features.py")
        abl_path = root / f"ablation-{args.seed}-{fit_code}.json"
        if args.skip_ablation:
            abl = {"kept": list(FEATURE_NAMES), "dropped": [], "delta_without": {}}
        elif abl_path.exists():
            abl = json.loads(abl_path.read_text())
        else:
            abl = ablation(rows_train, rows_valid, args.seed)
            abl_path.write_text(json.dumps(abl))
        features = tuple(abl["kept"])
        train_ds, valid_ds = to_dataset(rows_train, features), to_dataset(rows_valid, features)
        trial_path = root / f"trials-{args.seed}-{args.trials}-{fit_code}-" \
            f"{_digest('src/netcorenoc/engine/model/search.py')}-{'_'.join(features)[:40]}.jsonl"
        done = [search.Trial(**json.loads(line)) for line in
                (trial_path.read_text().splitlines() if trial_path.exists() else [])]

        def keep(t: search.Trial) -> None:
            with trial_path.open("a") as fh:
                fh.write(json.dumps(t.as_dict()) + "\n")
            print(f"  trial {t.index} rung {t.rung} rounds {t.rounds} "
                  f"valid {t.valid_loss:.5f} ({t.seconds:.0f}s)", file=sys.stderr)

        s = search.Search(train_ds, valid_ds, search.Budget(trials=args.trials), seed=args.seed,
                          on_trial=keep, done=done)
        trials = s.run()
        found = s.best()
        assert found is not None, "the search produced no finished trial"
        best = found
        final_params = search._params(best.params, 600, args.seed)
        fit = gam_fit.fit(train_ds, valid_ds, final_params, threshold=0.0)
        scorer = gam.load(fit.document)
        grouping, grid = tune_grouping(valid_logs, scorer)
        document = with_grouping(fit.document, grouping)
        cache.write_text(json.dumps({
            "ablation": abl, "trials": [t.as_dict() for t in trials], "best": best.as_dict(),
            "fit_document": fit.document, "best_round": fit.best_round,
            "train_trace": fit.train_trace, "valid_trace": fit.valid_trace,
            "grouping_grid": grid, "document": document,
        }))
    scorer = gam.load(document)
    if args.stage == "validate":
        report.print_validation(valid_logs, scorer)
        return 0

    evaluation = report.evaluate(root, scorer, args.seed)
    verdict = report.check_bar(evaluation, QUALITY_BAR)
    manifest = report.manifest(
        document=document, scorer=scorer, data_digest=data_digest, seed=args.seed,
        scale=args.scale, features=features, ablation=abl, trials=trials, best=best,
        fit=fit, final_params=final_params, grouping_grid=grid, evaluation=evaluation,
        bar=QUALITY_BAR, verdict=verdict, version=__version__, commit=_commit(),
        rows=(len(rows_train), len(rows_valid)), seconds=time.time() - t0,
    )
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / (MANIFEST + ".candidate")).write_text(json.dumps(manifest, indent=1, sort_keys=True))
    if not verdict["passed"]:
        print("QUALITY BAR MISSED — the artifact is NOT written:", file=sys.stderr)
        for miss in verdict["missed"]:
            print(f"  {miss}", file=sys.stderr)
        return 1
    (args.out / ARTIFACT).write_text(document)
    (args.out / MANIFEST).write_text(json.dumps(manifest, indent=1, sort_keys=True))
    (args.out / (MANIFEST + ".candidate")).unlink()
    print(f"wrote {args.out / ARTIFACT} sha256 {hashlib.sha256(document.encode()).hexdigest()}")
    return 0


def _digest(*paths: str) -> str:
    return hashlib.sha256(b"".join((REPO / p).read_bytes() for p in paths)).hexdigest()[:12]


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,  # noqa: S607
                              cwd=REPO, check=False).stdout.strip()  # nosec B603 B607
    except OSError:
        return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
