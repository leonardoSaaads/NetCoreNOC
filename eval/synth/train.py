"""Training one GAM: the v0.26.0 pipeline, and the shared steps the league reuses.

    PYTHONPATH=eval python -m synth.train   # one GAM, gated by the bar (kept for comparison)
    make train                              # the league: `synth.league` (v0.27.0, ADR #424)

`synth.league` imports this module's rows, ablation, grouping tuning and quality bar, so every
member is trained exactly as the v0.26.0 GAM was; what changed is that the bar is a scorecard there,
not a gate. The steps of this module's own command, each deterministic from the pinned seed:

1. **Build** the recorded streams (`dataset.build`, cached by digest).
2. **Rows**: labelled pairs from ``train`` and the older 70 % of ``train_long``; validation rows
   from
   ``valid``. One unit of weight per activation (`dataset.training_rows`).
3. **Ablation**: fit with every candidate feature, then without each one in turn; a feature whose
   removal does not worsen validation log loss by at least :data:`KEEP_IF_WORSE_BY` is dropped —
   except the formula's three relations, :data:`CORE_FEATURES`, which are always kept (v0.29.0).
   Part III.2: *add them deliberately, measure each one's contribution, drop what does not pay.*
4. **Search** over the kept features (`netcorenoc.engine.model.search`): random search, then
   successive halving on validation log loss, every trial recorded.
5. **Final fit** with the best parameters, early-stopped on validation.
6. **Grouping**: the join and merge biases chosen on the *validation streams* with the model fixed:
   the fewest operator repair gestures among the settings that merge concurrent incidents no more
   often than the formula does.
7. **Evaluation** on every test split and on the independent yardsticks last, against the additive
   formula on the same streams, with cluster-bootstrap intervals over streams.
8. **The quality bar** (:data:`QUALITY_BAR`, fixed from validation before any test split is read):
   an artifact that misses any check is **not written**, and the command exits non-zero.
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
from synth.evaluate import FormulaDecider, ModelDecider, group, situation_metrics  # noqa: E402
from synth.record import Activation, StreamLog  # noqa: E402

REPO = HERE.parent.parent
SHIPPED = REPO / "src" / "netcorenoc" / "engine" / "model"
ARTIFACT = "linkmodel.json"
MANIFEST = "linkmodel.manifest.json"

# v0.29.0 (ADR #439): twice v0.27.0's 160 000 training rows and 50 000 validation rows — the new
# families and the bad-day regimes need the room, and a larger validation set makes the search's
# choices less noisy, which is itself a guard against fitting the validation streams.
MAX_TRAIN_ROWS = 320_000
MAX_VALID_ROWS = 100_000
ABLATION_ROWS = 60_000
BENCHMARK_ROWS = 2_000
KEEP_IF_WORSE_BY = 0.0005  # nats of validation log loss
#: v0.29.0 (ADR #439): the three relations the fail-safe formula reads are never dropped. A drop-one
#: ablation measures each feature against all the others, so features that carry one signal between
#: them each look dispensable and go together: on the v0.29.0 data `same_ne`, `entity_affinity` and
#: `ne_episodes` all fell under the threshold, every model lost which element an alarm came from,
#: and the corpus's fibre cut and both dual incidents were split. A model sees at least what the
#: formula sees.
CORE_FEATURES = ("dt", "same_ne", "same_class")

#: The quality bar (ADR #409). **Fixed from the validation streams alone, before any test split was
#: read**, from the paired stream-bootstrap of model - formula on validation (in brackets, 95 %):
#:
#:     pairwise F1 +0.005 [-0.001, +0.012]   over-merge +0.020 [+0.014, +0.028]
#:     under-merge -0.032 [-0.044, -0.022]   split-bag  -0.016 [-0.133, +0.137]
#:     negatives respected +0.069 [+0.020, +0.145]   repair gestures x0.725 [0.659, 0.809]
#:
#: Each check is ``(quantity, scope, kind, limit)``. ``scope`` is ``"splits"`` (every pooled test
#: split) or ``"held_out"`` (every held-out family, its own incidents). ``kind`` is ``min``/``max``
#: on the model's point estimate, ``diff_min``/``diff_max`` on model - formula on the same streams,
#: or ``ratio_max`` on model / formula. The relative checks carry the argument: the model replaces
#: the formula as the default, so it must not do more harm than the formula did — and on a family
#: it has never seen, it must not cost operators more repair work than the formula would have.
QUALITY_BAR: tuple[tuple[str, str, str, float], ...] = (
    # Sanity floors: whatever the formula does, the default decider may not be this poor.
    ("pairwise_f1", "splits", "min", 0.80),
    ("ari", "splits", "min", 0.80),
    ("over_merge_rate", "splits", "max", 0.15),
    ("under_merge_rate", "splits", "max", 0.20),
    # Against the formula on the same streams. Tolerances are about twice validation's interval.
    ("pairwise_f1", "splits", "diff_min", -0.02),
    ("ari", "splits", "diff_min", -0.02),
    ("over_merge_rate", "splits", "diff_max", 0.05),
    ("under_merge_rate", "splits", "diff_max", 0.0),
    ("split_bag_intact_rate", "splits", "diff_max", 0.10),
    ("asserted_negative_respected_rate", "splits", "diff_min", -0.02),
    ("repair_gestures", "splits", "ratio_max", 0.90),
    # Families the model never saw: no more repair work than the formula, and bounded harm.
    ("repair_gestures", "held_out", "ratio_max", 1.00),
    ("under_merge_rate", "held_out", "diff_max", 0.05),
    ("over_merge_rate", "held_out", "diff_max", 0.10),
)

#: The grouping grid (ADRs #409, #420): the biases are chosen on validation as the **fewest repair
#: gestures among the settings that pass every check of the quality bar on the validation
#: streams** — the ``splits`` checks on the pooled streams, the ``held_out`` checks on every family
#: in them. The first rule (pooled ``split_bag_intact_rate`` no higher than the formula's) chose a
#: setting that already failed eleven of those checks on validation, and it missed the bar on test
#: (#420): a selection rule weaker than the acceptance rule selects what acceptance refuses.
JOIN_GRID = (-0.5, 0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0)
MERGE_GRID = (0.5, 1.0, 2.0, 4.0)
PAIRS_GRID = (2, 3, 6)


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
    return gam_fit.Dataset.from_rows(
        features, [r.x for r in rows], [r.y for r in rows], [r.w for r in rows], picks
    )


def _defaults(rounds: int, seed: int) -> gam_fit.FitParams:
    return gam_fit.FitParams(
        rounds=rounds,
        learning_rate=0.1,
        max_bins=24,
        max_leaves=3,
        l2=1.0,
        min_hessian=1.0,
        subsample=0.6,
        interactions=0,
        seed=seed,
    )


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
        print(
            f"  ablation: without {name:16s} Δ valid log loss {deltas[name]:+.5f}", file=sys.stderr
        )
    kept = kept_features(candidates, deltas)
    return {
        "baseline_valid_log_loss": base,
        "delta_without": deltas,
        "kept": list(kept),
        "dropped": [f for f in candidates if f not in kept],
        "rows": [len(tr), len(va)],
    }


def kept_features(candidates: tuple[str, ...], deltas: dict[str, float]) -> tuple[str, ...]:
    """The ablation's rule: what pays its way, and the formula's three relations whatever they
    measured. Pure, so the rule is tested apart from the fits that feed it."""
    return tuple(f for f in candidates if f in CORE_FEATURES or deltas[f] >= KEEP_IF_WORSE_BY)


def _evidence_cache(logs: list[StreamLog], scorer: gam.GamScorer) -> dict[int, list[Scored]]:
    decider = ModelDecider.of(scorer)
    return {id(a): decider.evidence(a) for log in logs for a in log.activations()}


def tune_grouping(
    splits: dict[str, list[StreamLog]], scorer: gam.GamScorer, *, fallback: bool = False
) -> tuple[GroupingParams, list[dict[str, float]]]:
    """The fewest repair gestures among the settings that pass the quality bar on validation.

    Each validation split is checked on its own, as the bar checks each test split (#421): a
    regime pooled into a larger one is a regime the choice cannot see. The per-family checks read
    every validation stream; the objective is the pooled repair work over all of them.
    """
    from synth import report  # the bar's own check, so selection and acceptance cannot drift apart

    logs = [log for split in splits.values() for log in split]
    formula = {
        name: [group(log, FormulaDecider()) for log in split] for name, split in splits.items()
    }
    every = [o for outs in formula.values() for o in outs]
    families = sorted({f for o in every for f in o.family} & set(dataset.TRAIN_FAMILIES))
    formula_split = {name: _points(situation_metrics(outs)) for name, outs in formula.items()}
    formula_by = {f: _points(situation_metrics(every, f)) for f in families}
    cache = _evidence_cache(logs, scorer)
    rows: list[dict[str, float]] = []
    best: tuple[float, GroupingParams] | None = None
    for join in JOIN_GRID:
        for merge in MERGE_GRID:
            if merge < join:
                continue
            for pairs in PAIRS_GRID:
                params = GroupingParams(join, merge, pairs)
                decider = CachedDecider(cache, "cluster", params)
                outs = {
                    name: [group(log, decider) for log in split] for name, split in splits.items()
                }
                all_outs = [o for group_outs in outs.values() for o in group_outs]
                m = situation_metrics(all_outs)
                verdict = report.check_bar(
                    {
                        "splits": {
                            name: {
                                "model": _points(situation_metrics(split_outs)),
                                "formula": formula_split[name],
                            }
                            for name, split_outs in outs.items()
                        },
                        "held_out_families": {
                            f: {
                                "model": _points(situation_metrics(all_outs, f)),
                                "formula": formula_by[f],
                            }
                            for f in families
                        },
                    },
                    QUALITY_BAR,
                )
                rows.append(
                    {
                        "join_bias": join,
                        "merge_bias": merge,
                        "merge_min_pairs": float(pairs),
                        "admissible": float(verdict["passed"]),
                        "checks_missed": float(len(verdict["missed"])),
                        **{k: m[k] for k in (*_REPORTED, "repair_gestures")},
                    }
                )
                if verdict["passed"] and (best is None or m["repair_gestures"] < best[0] - 1e-12):
                    best = (m["repair_gestures"], params)
    if best is None and fallback:
        # v0.27.0 (ADR #426): the bar is a scorecard, not a gate. With no admissible setting the
        # choice is the fewest missed checks, then the fewest repair gestures — and the manifest
        # records that no setting was admissible, so the Judge screen can say so.
        pick = min(rows, key=lambda r: (r["checks_missed"], r["repair_gestures"]))
        return (
            GroupingParams(pick["join_bias"], pick["merge_bias"], int(pick["merge_min_pairs"])),
            rows,
        )
    if best is None:  # no setting passes the bar even on the streams it is tuned on: say so
        raise SystemExit(
            "no grouping setting passes the quality bar on validation; the model is not shipped"
        )
    return best[1], rows


def _points(metrics: dict[str, float]) -> dict[str, dict[str, float]]:
    """`situation_metrics` in the shape `report.check_bar` reads (a point, no interval)."""
    return {k: {"point": v} for k, v in metrics.items()}


_REPORTED = (
    "pairwise_f1",
    "ari",
    "over_merge_rate",
    "under_merge_rate",
    "split_bag_intact_rate",
    "asserted_negative_respected_rate",
)


def with_grouping(document: str, params: GroupingParams) -> str:
    doc = json.loads(document)
    doc["grouping"] = {
        "join_bias": params.join_bias,
        "merge_bias": params.merge_bias,
        "merge_min_pairs": float(params.merge_min_pairs),
    }
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
        "--stage",
        choices=("validate", "ship"),
        default="ship",
        help="validate: fit and report on the validation streams only, write nothing; "
        "ship: evaluate on the test splits once, enforce the quality bar, write the artifact",
    )
    args = parser.parse_args()
    from synth import report  # the evaluation half; imported here to keep this module's size

    t0 = time.time()
    root = dataset.build(args.scale, args.seed, args.workers)
    data_digest = root.name
    print(f"dataset {data_digest} ready in {time.time() - t0:.0f}s", file=sys.stderr)
    # Tuning only (#421): never a training row, never a search's validation loss.
    tuning = {
        "valid": list(dataset.load_split(root, "valid")),
        "valid_concurrency": list(dataset.load_split(root, "valid_concurrency")),
    }
    # The training streams are read one at a time (`load_split` is a generator); only rows stay.
    rows_train = dataset.training_rows(
        dataset.load_split(root, "train"), seed=args.seed
    ) + dataset.training_rows(
        dataset.load_split(root, "train_long"), seed=args.seed, time_window=(0.0, 0.7)
    )
    rows_valid = dataset.training_rows(tuning["valid"], seed=args.seed)
    # A recording that lost its feature vectors (a decider that reads none ran instead of the
    # probe) must stop here, loudly, rather than train on empty rows.
    short = [r for r in rows_train[:1000] + rows_valid[:1000] if len(r.x) != len(FEATURE_NAMES)]
    if short or not rows_train:
        raise SystemExit(f"the recorded vectors are not {len(FEATURE_NAMES)} long; re-record")
    rows_train = _cap(rows_train, MAX_TRAIN_ROWS, args.seed)
    rows_valid = _cap(rows_valid, MAX_VALID_ROWS, args.seed + 1)
    print(f"rows: train {len(rows_train)}, valid {len(rows_valid)}", file=sys.stderr)

    # The fit cache is keyed on the code that can change the fit — never on this file, whose
    # quality bar and reporting change nothing the fit produces.
    code = _digest(
        "src/netcorenoc/engine/model/gam.py",
        "src/netcorenoc/engine/model/gam_fit.py",
        "src/netcorenoc/engine/model/search.py",
        "src/netcorenoc/engine/correlate/grouping.py",
        "src/netcorenoc/engine/correlate/features.py",
        "eval/synth/dataset.py",
    )
    cache = root / f"fit-{args.seed}-{args.trials}-{int(args.skip_ablation)}-{code}.json"
    if cache.exists():
        saved = json.loads(cache.read_text())
        abl, trials, best = saved["ablation"], [search.Trial(**t) for t in saved["trials"]], None
        best = search.Trial(**saved["best"])
        features = tuple(abl["kept"])
        final_params = search._params(best.params, 600, args.seed)
        fit = gam_fit.FitResult(
            saved["fit_document"], saved["best_round"], saved["train_trace"], saved["valid_trace"]
        )
        grid = saved["grouping_grid"]
        document = saved["document"]
        print(f"reusing the cached fit {cache.name}", file=sys.stderr)
    else:
        # Each stage checkpoints, so a restarted run resumes rather than recomputes: the ablation
        # as one file, the search one trial per line (the search resumes from the trials it is
        # handed, and trial k's parameters are a pure function of the seed).
        fit_code = _digest(
            "src/netcorenoc/engine/model/gam_fit.py", "src/netcorenoc/engine/correlate/features.py"
        )
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
        trial_path = (
            root / f"trials-{args.seed}-{args.trials}-{fit_code}-"
            f"{_digest('src/netcorenoc/engine/model/search.py')}-{'_'.join(features)[:40]}.jsonl"
        )
        done = [
            search.Trial(**json.loads(line))
            for line in (trial_path.read_text().splitlines() if trial_path.exists() else [])
        ]

        def keep(t: search.Trial) -> None:
            with trial_path.open("a") as fh:
                fh.write(json.dumps(t.as_dict()) + "\n")
            print(
                f"  trial {t.index} rung {t.rung} rounds {t.rounds} "
                f"valid {t.valid_loss:.5f} ({t.seconds:.0f}s)",
                file=sys.stderr,
            )

        s = search.Search(
            train_ds,
            valid_ds,
            search.Budget(trials=args.trials),
            seed=args.seed,
            on_trial=keep,
            done=done,
        )
        trials = s.run()
        found = s.best()
        assert found is not None, "the search produced no finished trial"
        best = found
        final_params = search._params(best.params, 600, args.seed)
        fit = gam_fit.fit(train_ds, valid_ds, final_params, threshold=0.0)
        scorer = gam.load(fit.document)
        grouping, grid = tune_grouping(tuning, scorer)
        document = with_grouping(fit.document, grouping)
        cache.write_text(
            json.dumps(
                {
                    "ablation": abl,
                    "trials": [t.as_dict() for t in trials],
                    "best": best.as_dict(),
                    "fit_document": fit.document,
                    "best_round": fit.best_round,
                    "train_trace": fit.train_trace,
                    "valid_trace": fit.valid_trace,
                    "grouping_grid": grid,
                    "document": document,
                }
            )
        )
    scorer = gam.load(document)
    # Printed at both stages, so the ship run's own log shows the validation numbers the bar was
    # fixed against — and that a fresh run reproduces them.
    for name, split_logs in tuning.items():
        report.print_validation(split_logs, scorer, name)
    if args.stage == "validate":
        return 0

    evaluation = report.evaluate(root, scorer, args.seed)
    # The do-no-harm benchmark a site model is judged against (ADR #411): generated pairs from the
    # unseen i.i.d. test streams, whole activations, bounded, packaged with the model.
    bench = _cap(
        dataset.training_rows(dataset.load_split(root, "test_iid"), seed=args.seed),
        BENCHMARK_ROWS,
        args.seed + 2,
    )
    verdict = report.check_bar(evaluation, QUALITY_BAR)
    manifest = report.manifest(
        document=document,
        scorer=scorer,
        data_digest=data_digest,
        seed=args.seed,
        scale=args.scale,
        features=features,
        ablation=abl,
        trials=trials,
        best=best,
        fit=fit,
        final_params=final_params,
        grouping_grid=grid,
        evaluation=evaluation,
        bar=QUALITY_BAR,
        verdict=verdict,
        version=__version__,
        commit=_commit(),
        rows=(len(rows_train), len(rows_valid)),
        seconds=time.time() - t0,
        benchmark=[[r.y, round(r.w, 6), *(round(v, 6) for v in r.x)] for r in bench],
    )
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / (MANIFEST + ".candidate")).write_text(
        json.dumps(manifest, indent=1, sort_keys=True)
    )
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
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=REPO,
            check=False,
        ).stdout.strip()  # nosec B603 B607
    except OSError:
        return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
