"""Hyperparameter search for the `gam` kind: random search, then successive halving.

**Random search** because it beats a grid whenever only a few of the parameters matter, which is
the usual case (Bergstra & Bengio, *Random Search for Hyper-Parameter Optimization*, JMLR 13,
2012). **Successive halving** because most configurations are recognisably poor after a fraction
of their training budget, so the budget is spent on the ones that are not (Jamieson & Talwalkar,
AISTATS 2016; the bracket Hyperband repeats, Li et al., JMLR 18, 2018).

It is the same module offline and in the appliance: the shipped model's search ran through it, and
an admin's search from the Settings screen runs through it too. So it is:

* **seeded** — trial ``k``'s parameters are a pure function of ``(seed, k)``, so a search is
  reproducible and a resumed search draws the same trials it would have drawn;
* **bounded** — a trial budget, a round budget, a wall-clock budget, and a row cap, each checked;
* **resumable** — a caller hands back the trials already recorded and they are not re-run;
* **stoppable** — ``stop()`` is polled between trials and between boosting rounds;
* **recorded per trial** — every trial produces a :class:`Trial`, which is what the Judge
  screen's optimisation-history, parameter and importance charts are drawn from.

No external library: the fit is `gam_fit`, and this is a loop around it.
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from netcorenoc.engine.model import gam, gam_fit

__all__ = ["SPACE", "Budget", "Search", "Trial", "draw", "importance"]

#: The search space: name -> (kind, low, high). ``log`` draws log-uniformly, ``int`` uniformly
#: over integers, ``float`` uniformly. Chosen to bracket the values EBM's defaults use (learning
#: rate ~0.01-0.1, leaves 2-3, bins 32-256) scaled to this problem's size.
SPACE: dict[str, tuple[str, float, float]] = {
    "learning_rate": ("log", 0.02, 0.3),
    "max_bins": ("int", 8, 48),
    "max_leaves": ("int", 2, 3),
    "l2": ("log", 0.1, 20.0),
    "min_hessian": ("log", 0.1, 20.0),
    "subsample": ("float", 0.3, 1.0),
    "interactions": ("int", 0, 4),
}


def draw(
    seed: int, index: int, space: dict[str, tuple[str, float, float]] = SPACE
) -> dict[str, float]:
    """Trial ``index``'s parameters: a pure function of ``(seed, index)``."""
    rng = random.Random(f"gam-search|{seed}|{index}")  # nosec B311 - seeded statistical sampling, never a secret
    out: dict[str, float] = {}
    for name in sorted(space):
        kind, lo, hi = space[name]
        if kind == "log":
            out[name] = math.exp(rng.uniform(math.log(lo), math.log(hi)))
        elif kind == "int":
            out[name] = float(rng.randint(int(lo), int(hi)))
        else:
            out[name] = rng.uniform(lo, hi)
    return out


@dataclass(frozen=True)
class Budget:
    trials: int = 12
    min_rounds: int = 40
    max_rounds: int = 320
    eta: int = 3  # keep the best 1/eta at each rung
    seconds: float = 3600.0


@dataclass
class Trial:
    """One fit: its parameters, its budget, and what it scored. The unit every chart is made of."""

    index: int
    rung: int
    params: dict[str, float]
    rounds: int
    best_round: int = 0
    train_loss: float = math.nan
    valid_loss: float = math.nan
    seconds: float = 0.0
    status: str = "done"  # done | stopped | failed
    trace: list[float] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _params(p: dict[str, float], rounds: int, seed: int) -> gam_fit.FitParams:
    return gam_fit.FitParams(
        rounds=rounds,
        learning_rate=p["learning_rate"],
        max_bins=int(p["max_bins"]),
        max_leaves=int(p["max_leaves"]),
        l2=p["l2"],
        min_hessian=p["min_hessian"],
        subsample=p["subsample"],
        interactions=int(p["interactions"]),
        seed=seed,
    )


@dataclass
class Search:
    train: gam_fit.Dataset
    valid: gam_fit.Dataset
    budget: Budget = field(default_factory=Budget)
    seed: int = 0
    stop: Callable[[], bool] | None = None
    on_trial: Callable[[Trial], None] | None = None
    done: list[Trial] = field(default_factory=list)
    #: A warm start (site adaptation): every trial boosts from this model's logit, on its bins.
    base: gam.Model | None = None

    def run(self) -> list[Trial]:
        """Run (or resume) the search. Returns every trial, including the ones handed in."""
        started = time.monotonic()
        rung_rounds = self.budget.min_rounds
        alive = list(range(self.budget.trials))
        rung = 0
        while alive:
            for index in alive:
                if self._recorded(index, rung) is not None:
                    continue
                if self._should_stop(started):
                    return self.done
                self._fit(index, rung, rung_rounds)
            scored = sorted(
                (
                    t
                    for t in self.done
                    if t.rung == rung and t.index in alive and t.status == "done"
                ),
                key=lambda t: (t.valid_loss, t.index),
            )
            keep = max(1, len(scored) // self.budget.eta)
            if rung_rounds >= self.budget.max_rounds or len(scored) <= 1:
                break
            alive = [t.index for t in scored[:keep]]
            rung += 1
            rung_rounds = min(self.budget.max_rounds, rung_rounds * self.budget.eta)
        return self.done

    def best(self) -> Trial | None:
        finished = [t for t in self.done if t.status == "done" and math.isfinite(t.valid_loss)]
        if not finished:
            return None
        top = max(t.rung for t in finished)
        return min((t for t in finished if t.rung == top), key=lambda t: (t.valid_loss, t.index))

    def _recorded(self, index: int, rung: int) -> Trial | None:
        return next((t for t in self.done if t.index == index and t.rung == rung), None)

    def _should_stop(self, started: float) -> bool:
        return (self.stop is not None and self.stop()) or (
            time.monotonic() - started > self.budget.seconds
        )

    def _fit(self, index: int, rung: int, rounds: int) -> None:
        params = draw(self.seed, index)
        trial = Trial(index, rung, params, rounds)
        t0 = time.monotonic()
        try:
            result = gam_fit.fit(
                self.train,
                self.valid,
                _params(params, rounds, self.seed + index),
                stop=self.stop,
                edges=None if self.base is None else {s.feature: s.edges for s in self.base.shapes},
                base=self.base,
            )
            trial.best_round = result.best_round
            trial.train_loss = result.train_trace[-1] if result.train_trace else math.nan
            trial.valid_loss = min(result.valid_trace) if result.valid_trace else math.nan
            trial.trace = result.valid_trace
            if self.stop is not None and self.stop():
                trial.status = "stopped"
        except Exception:  # a trial that cannot fit is a recorded failure, not a crash
            trial.status = "failed"
        trial.seconds = time.monotonic() - t0
        self.done.append(trial)
        if self.on_trial is not None:
            self.on_trial(trial)


def importance(trials: Sequence[Trial]) -> dict[str, float]:
    """How much each hyperparameter explains the validation loss across finished trials.

    A deliberately simple, stated estimate: the squared rank correlation (Spearman's rho²) between
    each parameter and the loss over the first rung, where every trial had the same budget. It is
    not fANOVA (Hutter et al., ICML 2014) — that needs a random-forest surrogate and more trials
    than an appliance search will run — and the Judge screen labels it for what it is.
    """
    first = [
        t for t in trials if t.rung == 0 and t.status == "done" and math.isfinite(t.valid_loss)
    ]
    if len(first) < 3:
        return {}
    losses = _ranks([t.valid_loss for t in first])
    out: dict[str, float] = {}
    for name in sorted(first[0].params):
        values = _ranks([t.params[name] for t in first])
        rho = _pearson(values, losses)
        out[name] = rho * rho
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
