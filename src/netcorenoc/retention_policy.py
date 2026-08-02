"""The retention policy: three tiers, what each one *means*, and how it is persisted.

**Split out of `capture.py` in v0.8.1 because the size guard required it, and it turned out to be
the right seam anyway** (DECISIONS #113). `capture.py` is about turning one correlation decision
into rows; this is a configuration value with an invariant and a durable form. `capture.py`
re-exports every name here, so no import site anywhere else changed — the same arrangement that
already carries `MAX_CLIENT_MEMBERS` from `store/dataset.py` through `labels.py`.

**What the three tiers mean (DECISIONS #110), because two of them meant nothing in v0.8.0:**

* **sink** — pairs awaiting a verdict. **Deletes**, on the maintenance loop, under a dual bound.
* **training** — what a model may *read*. **Selects.** Nothing is deleted at this boundary: a
  training-retention delete destroys evidence in order to express a modelling preference, and
  selection is a `WHERE` clause. Keeping the rows keeps the choice revisable.
* **audit** — the outer bound of the data's life. **Deletes**, and it is the only background path
  in the product that may reach a human label.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

__all__ = ["RETENTION_META_KEY", "TIER_NAMES", "RetentionPolicy"]

# The `meta` key the policy is persisted under, and the field order the stored form uses. `meta` is
# how this product has always persisted operator configuration (`config.allowlist`,
# `config.retention_days`, `community_hmac_key`), so persisting retention needs no migration.
RETENTION_META_KEY = "config.dataset_retention"
TIER_NAMES = ("sink_days", "sink_rows", "training_days", "audit_days")


@dataclass(frozen=True)
class RetentionPolicy:
    """The three tiers, ordered. **The defaults are defaults, not policy.**

    The right value depends on the customer's network in ways no specification can anticipate: a
    stable regional ISP and a large operator with rolling equipment refreshes want different
    numbers. The product's posture has always been *"you can do this, but understand the risk"*
    rather than a number nobody may touch.

    The **21-day sink default is a conservative guess and this docstring says so.** v0.8.0 measures
    real label latency (`feedback.situation_opened_at`), so a later release can replace it with the
    observed distribution instead of defending a number nobody derived.
    """

    sink_days: float = 21.0
    sink_rows: int = 2_000_000
    training_days: float = 365.0
    audit_days: float = 730.0

    def as_key(self) -> tuple[Any, ...]:
        return (self.sink_days, self.sink_rows, self.training_days, self.audit_days)

    def as_json(self) -> str:
        """The stored form: one `meta` value, not four keys (DECISIONS #111)."""
        return json.dumps(dict(zip(TIER_NAMES, self.as_key(), strict=True)), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> RetentionPolicy | None:
        """Parse a stored policy, or ``None`` when it is unusable **for any reason**.

        Whole policy or nothing: reconstructing three good fields around one bad one would
        synthesise a policy no operator ever set, possibly one that deletes more than either the
        stored or the shipped value would. `validate()` runs here rather than being trusted from
        the write path — a `meta` row is durable state a restore or a hand-edit could have left
        inconsistent, and a stored ordering violation must not become a live one.
        """
        try:
            data = json.loads(raw)
            policy = cls(
                sink_days=float(data["sink_days"]),
                sink_rows=int(data["sink_rows"]),
                training_days=float(data["training_days"]),
                audit_days=float(data["audit_days"]),
            )
        except (TypeError, ValueError, KeyError, IndexError):
            return None
        return None if policy.validate() is not None else policy

    def validate(self) -> str | None:
        """The ordering invariant, **fail-closed, with a precise reason**.

        Returns an error string, or ``None`` when valid. A reason rather than a bool because the
        admin has to be able to fix it: "invalid" on a form with four numbers is not actionable.

        `sink < training <= audit` is not arbitrary. A sink outliving the training tier would keep
        unlabelled rows longer than labelled ones — the opposite of the point. A training tier
        outliving the audit archive would mean the corpus survived its own provenance.
        """
        if self.sink_days <= 0 or self.training_days <= 0 or self.audit_days <= 0:
            return "every retention tier must be greater than zero days"
        if self.sink_rows <= 0:
            return "the sink row cap must be greater than zero"
        if self.sink_days >= self.training_days:
            return (
                f"sink retention ({self.sink_days} d) must be strictly less than training "
                f"retention ({self.training_days} d): the sink holds rows whose destiny is "
                "unknown, so it cannot outlive the corpus they are promoted into"
            )
        if self.training_days > self.audit_days:
            return (
                f"training retention ({self.training_days} d) must not exceed audit retention "
                f"({self.audit_days} d): the corpus cannot outlive its own provenance"
            )
        return None
