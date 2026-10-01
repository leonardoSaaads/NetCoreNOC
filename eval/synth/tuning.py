"""The hyperparameter search every league member goes through: random search, successive halving.

v0.27.0 (ADR #424). One procedure for five families, so the Judge screen's search charts compare
like with like: each trial is drawn as a pure function of ``(kind, seed, index)`` from the kind's
space, fitted at the rung's **capacity** (boosting rounds, forest trees, or — for the kinds with no
natural budget — the whole fit at once), scored on validation log loss, and the best ``1/eta``
advance to the next rung (Jamieson & Talwalkar, *Non-stochastic Best Arm Identification and
Hyperparameter Optimization*, AISTATS 2016; Li et al., *Hyperband*, JMLR 18, 2018).

A trial is recorded whatever happens to it — a failure is a row with ``status='failed'``, never a
crash — because the optimisation-history chart must show every trial that ran, not the survivors.
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

__all__ = ["Space", "Trial", "draw", "halving", "importance"]

#: name -> (kind, low, high); ``log`` log-uniform, ``int`` uniform integers, ``float`` uniform.
Space = dict[str, tuple[str, float, float]]


@dataclass
class Trial:
    index: int
    rung: int
    capacity: int
    params: dict[str, float]
    best: int = 0
    train_loss: float = math.nan
    valid_loss: float = math.nan
    seconds: float = 0.0
    status: str = "done"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def draw(kind: str, seed: int, index: int, space: Space) -> dict[str, float]:
    """Trial ``index``'s parameters: a pure function of ``(kind, seed, index)``."""
    rng = random.Random(f"league-search|{kind}|{seed}|{index}")  # nosec B311
    out: dict[str, float] = {}
    for name in sorted(space):
        shape, lo, hi = space[name]
        if shape == "log":
            out[name] = math.exp(rng.uniform(math.log(lo), math.log(hi)))
        elif shape == "int":
            out[name] = float(rng.randint(int(lo), int(hi)))
        else:
            out[name] = rng.uniform(lo, hi)
    return out


@dataclass
class Fitted:
    """What a fit function returns to the search."""

    valid_loss: float
    train_loss: float
    best: int


def halving(
    kind: str,
    space: Space,
    fit: Callable[[dict[str, float], int], Fitted],
    *,
    trials: int,
    rungs: Sequence[int],
    seed: int,
    eta: int = 3,
    done: Sequence[Trial] = (),
    on_trial: Callable[[Trial], None] | None = None,
) -> list[Trial]:
    """Run (or resume from ``done``) the search. Returns every trial, the resumed ones included."""
    record = list(done)
    alive = list(range(trials))
    for rung, capacity in enumerate(rungs):
        for index in alive:
            if any(t.index == index and t.rung == rung for t in record):
                continue
            params = draw(kind, seed, index, space)
            trial = Trial(index, rung, capacity, params)
            started = time.monotonic()
            try:
                out = fit(params, capacity)
                trial.valid_loss, trial.train_loss, trial.best = (
                    out.valid_loss,
                    out.train_loss,
                    out.best,
                )
            except Exception:  # a trial that cannot fit is a recorded failure, not a crash
                trial.status = "failed"
            trial.seconds = round(time.monotonic() - started, 3)
            record.append(trial)
            if on_trial is not None:
                on_trial(trial)
        scored = sorted(
            (
                t
                for t in record
                if t.rung == rung
                and t.index in alive
                and t.status == "done"
                and math.isfinite(t.valid_loss)
            ),
            key=lambda t: (t.valid_loss, t.index),
        )
        if len(scored) <= 1 or rung == len(rungs) - 1:
            break
        alive = [t.index for t in scored[: max(1, len(scored) // eta)]]
    return record


def best(record: Sequence[Trial]) -> Trial:
    finished = [t for t in record if t.status == "done" and math.isfinite(t.valid_loss)]
    if not finished:
        raise SystemExit("the search produced no finished trial")
    top = max(t.rung for t in finished)
    return min((t for t in finished if t.rung == top), key=lambda t: (t.valid_loss, t.index))


def importance(record: Sequence[Trial]) -> dict[str, float]:
    """Spearman ρ² of each parameter against validation loss over the first rung — the stated,
    simple estimate the Judge screen labels as such (not fANOVA; ADR #409)."""
    first = [
        t for t in record if t.rung == 0 and t.status == "done" and math.isfinite(t.valid_loss)
    ]
    if len(first) < 3:
        return {}
    losses = _ranks([t.valid_loss for t in first])
    out: dict[str, float] = {}
    for name in sorted(first[0].params):
        rho = _pearson(_ranks([t.params[name] for t in first]), losses)
        out[name] = round(rho * rho, 6)
    return out


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2.0
        i = j + 1
    return ranks


def _pearson(a: Sequence[float], b: Sequence[float]) -> float:
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True))
    va = math.sqrt(sum((x - ma) ** 2 for x in a))
    vb = math.sqrt(sum((y - mb) ** 2 for y in b))
    return cov / (va * vb) if va > 0 and vb > 0 else 0.0
