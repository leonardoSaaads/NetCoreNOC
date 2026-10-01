"""The shipped GAM: the league member a site adaptation boosts from.

v0.26.0 shipped one model, `linkmodel.json`, or none (ADR #422). v0.27.0 ships a **league** of five
(`engine/model/league.py`, ADR #424) and a judge chooses which one decides. This module keeps one
job from v0.26.0 and gives up the rest:

* **kept** — naming the pre-trained **GAM**, because the in-product search (ADR #413) adapts that
  family to a site by boosting from its logit on its own bins, and needs its document;
* **given up** — deciding. What decides is the league's champion (`league_judge.choose`), and the
  engine reads the league, not this module.

Loading is the league's: JSON validated by the kind's validator, the manifest's SHA-256 checked, no
code imported or evaluated, no path in any message.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources.abc import Traversable
from typing import Any

from netcorenoc.engine.model import gam, league

__all__ = [
    "NOT_SHIPPED",
    "NoShippedModelError",
    "Shipped",
    "ShippedModelError",
    "load",
    "load_dir",
    "load_from",
    "summary",
]


@dataclass(frozen=True)
class Shipped:
    document: str
    sha256: str
    manifest: dict[str, Any]
    scorer: gam.GamScorer

    @property
    def ref(self) -> str:
        return f"{gam.KIND}:{self.sha256[:12]}"


#: What an appliance says when its build carries no GAM to adapt. No path and no exception text:
#: every role can read the bell.
NOT_SHIPPED = "this build ships no GAM to adapt; the league's other members still compete"


class ShippedModelError(ValueError):
    """The shipped GAM's files are malformed, incomplete, or do not belong together."""


class NoShippedModelError(ShippedModelError):
    """No GAM is packaged: a state of the build, not a fault in it."""


def _of(member: league.Member | None, refused: dict[str, str]) -> Shipped:
    if member is None:
        if gam.KIND in refused:
            raise ShippedModelError(f"the shipped model could not be used: {refused[gam.KIND]}")
        raise NoShippedModelError(NOT_SHIPPED)
    assert isinstance(member.scorer, gam.GamScorer)
    return Shipped(member.document, member.sha256, member.manifest, member.scorer)


def load() -> Shipped:
    """The packaged league's GAM."""
    packaged = league.load()
    return _of(packaged.by_kind(gam.KIND), dict(packaged.refused))


def load_dir(root: Traversable) -> Shipped:
    found = league.load_dir(root)
    return _of(found.by_kind(gam.KIND), dict(found.refused))


def load_from(document: str, manifest_text: str) -> Shipped:
    """Validate a GAM document and its manifest (as `league.member_from`)."""
    manifest = json.loads(manifest_text) if manifest_text else {}
    if isinstance(manifest, dict) and isinstance(manifest.get("artifact"), dict):
        manifest["artifact"].setdefault("kind", gam.KIND)
        manifest_text = json.dumps(manifest)
    try:
        member = league.member_from(gam.KIND, document, manifest_text)
    except league.LeagueError as exc:
        raise ShippedModelError(str(exc)) from exc
    return _of(member, {})


def summary(shipped: Shipped) -> dict[str, Any]:
    m = shipped.manifest
    return {
        "ref": shipped.ref,
        "sha256": shipped.sha256,
        "features": list(shipped.scorer.model.features),
        "grouping": shipped.scorer.model.grouping,
        "provenance": m.get("provenance", {}),
        "scorecard": m.get("scorecard", {}),
    }
