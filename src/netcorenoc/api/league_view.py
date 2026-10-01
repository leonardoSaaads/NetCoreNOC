"""The league as the console sees it: every member, its role, and what each chart is drawn from.

v0.27.0 (ADRs #423, #427). Presentation of manifests and of the engine's two loops — no route here,
and nothing is computed that the manifests or the engine do not already hold. Each block names its
**dataset**, so the console can never put generated-data numbers and this site's labels on one
unlabelled axis (the rule the v0.26.0 Judge screen was built on, ADR #414):

* ``generated`` — a member's manifest: its search, its fit, its evaluation on held-out generated
  streams and on the hand-labelled corpus. Measured once, when the build was trained;
* ``site`` — this appliance's labels and the slow loop's paired comparisons on them;
* ``live`` — the fast loop's counters and the challengers' shadow, since this process started.
"""

from __future__ import annotations

import math
from typing import Any

from netcorenoc.engine.model import league as league_model
from netcorenoc.engine.model import league_judge

__all__ = ["decider_payload", "finite", "member_block", "members_table"]

#: The pair-level quantities one test split carries in a manifest, served for the charts.
_PAIR_KEYS = (
    "pairs",
    "streams",
    "positive_rate_weighted",
    "average_precision",
    "roc_auc",
    "log_loss",
    "brier",
    "calibration",
    "confusion",
    "threshold_probability",
    "pr_curve",
    "roc_curve",
    "residuals",
)
_TRIAL_KEYS = (
    "index",
    "rung",
    "capacity",
    "params",
    "best",
    "train_loss",
    "valid_loss",
    "seconds",
    "status",
)


def finite(value: Any) -> Any:
    """NaN and infinities as ``None``: a failed trial's loss is NaN, and JSON has no NaN."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: finite(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [finite(v) for v in value]
    return value


def member_block(member: league_model.Member, appliance_us: float | None) -> dict[str, Any]:
    """Everything the ten charts need about one member, from its manifest (generated data)."""
    m = member.manifest
    evaluation = m.get("evaluation") or {}
    splits = evaluation.get("splits") or {}
    pairs = evaluation.get("pairs") or {}
    search = m.get("search") or {}
    return finite(
        {
            "ref": member.ref,
            "kind": member.kind,
            "name": member.name,
            "origin": member.origin,
            "sha256": member.sha256,
            "dataset": "generated" if member.origin == "pretrained" else "site",
            "provenance": m.get("provenance") or {},
            "features": list(getattr(member.scorer.model, "features", ())),
            "grouping": dict(getattr(member.scorer.model, "grouping", {})),
            "search": {
                "space": search.get("space") or {},
                "rungs": search.get("rungs") or [],
                "trials": [{k: t.get(k) for k in _TRIAL_KEYS} for t in search.get("trials") or []],
                "best": search.get("best"),
                "importance": search.get("importance") or {},
                "importance_method": search.get("importance_method"),
            },
            "final_fit": m.get("final_fit") or {},
            "splits": {
                name: {arm: arms.get(arm) for arm in ("model", "formula")}
                for name, arms in splits.items()
            },
            "held_out_families": evaluation.get("held_out_families") or {},
            "first_hour": evaluation.get("first_hour") or {},
            "pairs": {
                split: {k: values.get(k) for k in _PAIR_KEYS} for split, values in pairs.items()
            },
            "corpus": m.get("corpus") or {},
            "latency": {"training": m.get("latency") or {}, "appliance_us": appliance_us},
            "scorecard": m.get("scorecard") or {},
            "grouping_admissible": m.get("grouping_admissible"),
        }
    )


def members_table(
    members: league_model.League, champion: str | None, latency_us: dict[str, float]
) -> list[dict[str, Any]]:
    """The judge's offline order, each row with its role in the two loops."""
    rows = league_judge.offline_table(members)
    for row in rows:
        us = latency_us.get(row["ref"])
        row["latency_us"] = None if us is None else round(us, 1)
        row["eligible"] = us is None or us <= league_judge.LATENCY_BUDGET_US
        member = members.by_ref(row["ref"])
        row["origin"] = member.origin if member is not None else "pretrained"
        row["role"] = (
            "champion"
            if row["ref"] == champion
            else "challenger"
            if row["eligible"]
            else "ineligible"
        )
    return finite(rows)  # type: ignore[no-any-return]


def decider_payload(engine: Any, pinned: str | None) -> dict[str, Any]:
    """What `GET /api/decider` serves: who decides, the league's order, and the pin."""
    members = engine.league_members
    champion = engine.champion
    return {
        "mode": "league",
        "running": engine.decider_ref,
        # The formula runs only as the fail-safe (ADR #425): no member could load.
        "fallback": champion is None or engine.decider_ref != champion.ref,
        "champion": None
        if champion is None
        else {"ref": champion.ref, "name": champion.name, "kind": champion.kind},
        "pinned": pinned,
        "members": []
        if members is None
        else members_table(members, engine.decider_ref, engine.latency_us),
        "refused": []
        if members is None
        else [{"kind": k, "reason": r} for k, r in members.refused],
        "warnings": engine.scorer_warning_list(),
        "policy": {
            "suites": list(league_judge.SUITES),
            "tie": league_judge.TIE,
            "latency_budget_us": league_judge.LATENCY_BUDGET_US,
        },
    }
