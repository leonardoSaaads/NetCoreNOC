"""The v0.26.0 request surface: what decides links, autonomy, a situation's severity, the search.

Split out of `models.py` mechanically, at the 400-line module guard, exactly as
`models_maintenance.py` was: `models.py` re-exports every name here **by identity**, so *"what can
a caller send this appliance"* is still one import list in one file (ADR #374). Every field is
bounded and every enum is a `Literal`, so a request outside the product's range is a 422 naming the
field rather than a value a handler has to defend against.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

__all__ = ["AutonomyIn", "DeciderIn", "SearchIn", "SituationSeverityIn"]


class DeciderIn(BaseModel):
    """Choose what decides links (ADR #405). ``reason`` is required: an unexplained switch of the
    thing that groups every alarm is exactly the change an audit two months later cannot read."""

    mode: Literal["shipped", "site", "additive"]
    reason: str = Field(min_length=3, max_length=500)


class AutonomyIn(BaseModel):
    """Autonomy's four grades and its self-suspension trigger (ADR #412). Admin only."""

    grouping: bool = False
    naming: bool = False
    closing: bool = False
    severity: bool = False
    agreement_floor: float = Field(default=0.8, gt=0.0, le=1.0)
    window: int = Field(default=30, ge=5, le=1000)
    min_judged: int = Field(default=8, ge=3, le=1000)
    confidence_floor: float = Field(default=0.9, ge=0.5, lt=1.0)
    reason: str = Field(min_length=3, max_length=500)


class SituationSeverityIn(BaseModel):
    """An operator's severity for a whole situation. It outranks autonomy's, which never overwrites
    it again, and it is the gesture that tells autonomy it was wrong."""

    severity: Literal["critical", "major", "minor", "warning", "indeterminate"]


class SearchIn(BaseModel):
    """Start a site-adaptation search (ADR #413). Every field is bounded; the defaults are the
    budget the Settings screen offers."""

    trials: int = Field(default=6, ge=2, le=40)
    max_rounds: int = Field(default=120, ge=20, le=600)
    minutes: int = Field(default=30, ge=1, le=240)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)
