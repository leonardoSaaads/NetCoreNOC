"""**Is the corpus sufficient?** — the floors, the deployment policy, and the verdict.

The first of the three decisions `training.py` makes, moved here in v0.16.0 when that module
reached its 400-line budget. The seam is a real one rather than a size accident: `derive` and `fit`
are **arithmetic** over rows, while everything below is **policy** — a floor registered in a
pre-registration, a deployment's stored hardening of it, and a two-valued verdict about whether to
fit at all. `census.py` left `shadow.py` on the same kind of line one release earlier.

Every name here is re-exported by `training.py`, by identity, because every caller since v0.9.0 has
imported them from there and a moved module must not rewrite six call sites to prove it moved.

**If not sufficient, nothing is fitted** — and that is a successful outcome, reported with a
projection of how long until the floors would be met at the measured labelling rate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from netcorenoc.engine.dataset.census import CorpusStats

__all__ = [
    "PROJECT_FLOORS",
    "Floors",
    "Sufficiency",
    "assess",
    "resolve_floors",
]

DAY_S = 86400.0
MONTH_DAYS = 30.44


@dataclass(frozen=True)
class Floors:
    """The sufficiency bar. Pre-registered §5.2, and a deployment may only make it harder."""

    split_bags: int = 50
    mixed_bags: int = 20
    operators: int = 3
    top_operator_share_pct: float = 60.0
    incidents: int = 30

    def as_dict(self) -> dict[str, float]:
        return {
            "split_bags": self.split_bags,
            "mixed_bags": self.mixed_bags,
            "operators": self.operators,
            "top_operator_share_pct": self.top_operator_share_pct,
            "incidents": self.incidents,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))


# Ten `split` bags per free parameter. The challenger has four free parameters — three features and
# an intercept — so the events-per-variable convention gives forty. **Fifty is kept**, the number
# registered when the plan expected five parameters: `resolved = the more demanding of` runs
# monotone toward evidence, and lowering a floor because the model got simpler is the move
# DECISIONS #114 exists to forbid. `challenger.py`'s docstring records why the fourth feature could
# not be built.
PROJECT_FLOORS = Floors()


def resolve_floors(stored: str | None) -> tuple[Floors, str | None]:
    """`(resolved floors, warning)` — the project floor, hardened by a deployment policy.

    **A deployment may raise a requirement and can never lower one**, including by setting it to
    zero, to null, or by omitting it. "Harder" is resolved per threshold with its direction
    declared: every count is a minimum so the larger value wins, and the operator-concentration
    ceiling is a maximum so the *smaller* value wins.

    An unreadable value falls back to the **project floors as a whole** and returns a warning —
    never a partial reconstruction, the same discipline DECISIONS #111 applies to retention, and
    for the same reason: a policy that cannot be parsed must not become a policy that admits more
    than the shipped default would.
    """
    if stored is None:
        return PROJECT_FLOORS, None
    warning = (
        "The stored evidence-floor policy (config.evidence_floors) could not be read and was "
        "ignored; the shipped project floors are in effect. A floor may only ever be made harder."
    )
    try:
        payload = json.loads(stored)
        if not isinstance(payload, dict):
            raise ValueError("not an object")
        return (
            Floors(
                split_bags=max(PROJECT_FLOORS.split_bags, int(payload.get("split_bags", 0))),
                mixed_bags=max(PROJECT_FLOORS.mixed_bags, int(payload.get("mixed_bags", 0))),
                operators=max(PROJECT_FLOORS.operators, int(payload.get("operators", 0))),
                # A CEILING: harder means lower, so the minimum wins.
                top_operator_share_pct=min(
                    PROJECT_FLOORS.top_operator_share_pct,
                    float(payload.get("top_operator_share_pct", 100.0)),
                ),
                incidents=max(PROJECT_FLOORS.incidents, int(payload.get("incidents", 0))),
            ),
            None,
        )
    except (ValueError, TypeError):
        return PROJECT_FLOORS, warning


@dataclass(frozen=True)
class Sufficiency:
    """The verdict, the floors that were missed, and how long until they would be met."""

    ok: bool
    unmet: tuple[str, ...] = ()
    projections: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {"unmet": list(self.unmet), "projections": self.projections},
            sort_keys=True,
            separators=(",", ":"),
        )


def _projection(shortfall: int, observed: int, span_days: float) -> str:
    """`"about N.N months"`, or **`undefined`**.

    *"Not enough yet"* is not actionable; *"about seven months at the current rate"* is. But a rate
    needs a span, and a corpus whose labels all arrived at one instant has none — extrapolating
    from it would be a fabricated number, and this release does not print one.
    """
    if span_days <= 0.0 or observed <= 0:
        return "undefined (no measurable labelling rate yet)"
    per_day = observed / span_days
    return f"about {shortfall / per_day / MONTH_DAYS:.1f} months at the current rate"


def assess(stats: CorpusStats, floors: Floors) -> Sufficiency:
    """Evaluate the corpus against the resolved floors. **Ambiguity resolves to "insufficient".**"""
    unmet: list[str] = []
    projections: dict[str, str] = {}
    checks: tuple[tuple[str, int, int], ...] = (
        ("split_bags", stats.split_bags, floors.split_bags),
        ("mixed_bags", stats.mixed_bags, floors.mixed_bags),
        ("incidents", stats.incidents, floors.incidents),
        ("operators", stats.operators, floors.operators),
    )
    for name, observed, floor in checks:
        if observed < floor:
            unmet.append(f"{name}: {observed} < {floor}")
            projections[name] = _projection(floor - observed, observed, stats.span_days)
    if stats.operators and stats.top_operator_share_pct > floors.top_operator_share_pct:
        unmet.append(
            f"top_operator_share_pct: {stats.top_operator_share_pct:.1f} > "
            f"{floors.top_operator_share_pct:.1f}"
        )
        projections["top_operator_share_pct"] = (
            "undefined (concentration falls only when another operator labels)"
        )
    return Sufficiency(ok=not unmet, unmet=tuple(unmet), projections=projections)
