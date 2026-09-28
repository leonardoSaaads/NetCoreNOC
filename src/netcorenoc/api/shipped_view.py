"""The shipped model as the console sees it: its manifest's charted keys, or why there is none.

Split out of `api/routes/decider.py` at the 400-line guard (v0.26.0): this is presentation of a
manifest — what to serve, what JSON cannot carry, how a missing model is described — and no route.
"""

from __future__ import annotations

import math
from typing import Any

from netcorenoc.engine.model import shipped

__all__ = ["finite", "search_blocked", "shipped_block", "unavailable"]

#: The manifest keys the console charts. The manifest holds more (every trial's trace, the whole
#: grouping grid); the screen needs these, and serving the rest would be a megabyte nobody reads.
_SHIPPED_CHART_KEYS = ("ablation", "final_fit", "quality_bar", "verdict", "provenance", "artifact")


def finite(value: Any) -> Any:
    """NaN and infinities as ``None``: a failed trial's loss is NaN, and JSON has no NaN."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite(v) for v in value]
    return value


def unavailable(exc: Exception) -> dict[str, Any]:
    """Why no shipped model runs. ``absent`` separates a build that carries none (#422) — a state,
    shown as a note — from one whose files were refused, which is a fault."""
    return {
        "available": False,
        "absent": isinstance(exc, shipped.NoShippedModelError),
        "reason": str(exc.args[0] if exc.args else exc),
    }


def shipped_block() -> dict[str, Any]:
    try:
        model = shipped.load()
    except Exception as exc:
        return unavailable(exc)
    m = model.manifest
    evaluation = m.get("evaluation", {})
    trials = m.get("search", {}).get("trials", [])
    block: dict[str, Any] = finite(
        {
            "available": True,
            "dataset": "generated",
            "ref": model.ref,
            "features": list(model.scorer.model.features),
            "grouping": model.scorer.model.grouping,
            **{k: m.get(k) for k in _SHIPPED_CHART_KEYS},
            "search": {
                "trials": [
                    {
                        k: t.get(k)
                        for k in (
                            "index",
                            "rung",
                            "rounds",
                            "valid_loss",
                            "train_loss",
                            "seconds",
                            "status",
                            "params",
                            "best_round",
                        )
                    }
                    for t in trials
                ],
                "importance": m.get("search", {}).get("importance", {}),
                "space": m.get("search", {}).get("space", {}),
            },
            "evaluation": evaluation,
            "shapes": [
                {"feature": s.feature, "edges": list(s.edges), "scores": list(s.scores)}
                for s in model.scorer.model.shapes
            ],
        }
    )
    return block


def search_blocked() -> str | None:
    """Why a search cannot start on this build, or ``None``: a search adapts the shipped model, so
    a build that carries none (#422) — or whose files were refused — has nothing to adapt."""
    try:
        shipped.load()
    except shipped.ShippedModelError as exc:
        return f"no shipped model to adapt: {exc}"
    return None
