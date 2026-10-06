"""The model league: every pre-trained model the appliance ships, loaded as **data**.

v0.27.0 (ADRs #423, #424). v0.26.0 shipped one model family or none. This release ships a
**league** — five families trained on the same generated data, over the same feature vector, each
with its own manifest — and a judge (`engine/model/judge.py`) chooses which one decides. The league
is the unit a slow loop reasons about: the judge ranks its members, the fast loop runs one of them
and shadows the rest.

    league/<kind>.json            the model document — the only file that can change a decision
    league/<kind>.manifest.json   its provenance, its search, every measured number and chart

## The kinds

=====================  ================================  ==========================
kind                   what it is                        document
=====================  ================================  ==========================
``gam``                explainable boosting (GA²M)       ``netcorenoc.gam/1``
``boosted_trees``      Newton-boosted regression trees   ``netcorenoc.trees/1``
``random_forest``      bagged gini trees, mean log-odds  ``netcorenoc.trees/1``
``decision_tree``      one gini tree                     ``netcorenoc.trees/1``
``logistic_regression`` L2 logistic regression           ``netcorenoc.linear/1``
``xgboost``            the XGBoost algorithm (v0.29.0)   ``netcorenoc.trees/1``
``knn``                k-nearest neighbours (v0.29.0)    ``netcorenoc.knn/1``
=====================  ================================  ==========================

## Loading is validation and nothing else (ADR #405, kept)

A customer can replace any of these files and the appliance holds credentials, so each document is
parsed as JSON and put through its kind's validator — exact keys, finite bounded numbers, only
features this build serves, a structure that is a tree, reachability — before a scorer exists.
Nothing is imported, evaluated or unpickled; no field names code. The manifest's SHA-256 of the
document and its declared kind are checked too: **integrity, not security** — the console can never
describe one model while the engine runs another.

A member that fails is **refused alone**: the league is whatever loaded, and each refusal is
reported with its reason (no path). A league with no members is a build fault, and only then does
the engine fall back to the built-in formula (ADR #425).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from importlib import resources
from importlib.resources.abc import Traversable
from typing import Any, Protocol

from netcorenoc.engine.correlate.features import FEATURE_NAMES
from netcorenoc.engine.correlate.scorer_contract import LinkFeatures, LinkScore
from netcorenoc.engine.model import gam, knn, linear, trees

__all__ = [
    "DIRECTORY",
    "KINDS",
    "League",
    "LeagueError",
    "LeagueScorer",
    "Member",
    "benchmark_rows",
    "benchmark_vectors",
    "load",
    "load_dir",
    "member_from",
    "site_member",
]

DIRECTORY = "league"
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
BENCHMARK = "benchmark.json"
BENCHMARK_FORMAT = "netcorenoc.benchmark/1"
MAX_BENCHMARK_ROWS = 5000


class LeagueScorer(Protocol):
    """What every member's scorer offers: the fast path, the explanation, and its document."""

    threshold: float
    params_document: str

    @property
    def scorer_id(self) -> str: ...

    @property
    def contract_version(self) -> str: ...

    @property
    def model(self) -> Any: ...

    def params_fingerprint(self) -> str: ...

    def logit(self, vector: tuple[float, ...]) -> float: ...

    def explain(self, vector: tuple[float, ...]) -> LinkScore: ...

    def score(self, features: LinkFeatures) -> LinkScore: ...


def _trees(kind: str) -> Callable[[str, str], Any]:
    def load(document: str, scorer_id: str) -> Any:
        scorer = trees.load(document, scorer_id)
        if scorer.model.method != kind:
            raise trees.TreesDocumentError(f"the document is a {scorer.model.method}, not a {kind}")
        return scorer

    return load


#: kind -> (the name an operator reads, the loader, the errors that loader raises).
KINDS: dict[str, tuple[str, Callable[[str, str], Any]]] = {
    "gam": ("GAM (explainable boosting)", gam.load),
    "boosted_trees": ("Gradient-boosted trees", _trees("boosted_trees")),
    "random_forest": ("Random forest", _trees("random_forest")),
    "decision_tree": ("Decision tree", _trees("decision_tree")),
    "logistic_regression": ("Logistic regression", linear.load),
    # v0.29.0 (ADR #438): the XGBoost algorithm, served as trees; and the league's memory.
    "xgboost": ("XGBoost", _trees("xgboost")),
    "knn": ("k-nearest neighbours", knn.load),
}
_REFUSALS = (
    gam.GamDocumentError,
    trees.TreesDocumentError,
    linear.LinearDocumentError,
    knn.KnnDocumentError,
)


class LeagueError(ValueError):
    """One member's files are malformed, incomplete, or do not belong together."""


@dataclass(frozen=True)
class Member:
    kind: str
    document: str
    sha256: str
    manifest: dict[str, Any]
    scorer: LeagueScorer
    #: ``pretrained`` (packaged, generated data) or ``site`` (adapted here, on this site's labels —
    #: the in-product search's output, ADR #413). A site member competes under the same judge.
    origin: str = "pretrained"

    @property
    def name(self) -> str:
        base = KINDS[self.kind][0]
        return base if self.origin == "pretrained" else f"{base}, adapted to this site"

    @property
    def ref(self) -> str:
        """How a decision names this model: its kind and the first twelve hex digits of its hash."""
        prefix = self.kind if self.origin == "pretrained" else f"site-{self.kind}"
        return f"{prefix}:{self.sha256[:12]}"


@dataclass(frozen=True)
class League:
    members: tuple[Member, ...]
    #: ``(kind, reason)`` for every member whose files were present and refused.
    refused: tuple[tuple[str, str], ...] = ()

    def by_ref(self, ref: str) -> Member | None:
        return next((m for m in self.members if m.ref == ref), None)

    def by_kind(self, kind: str) -> Member | None:
        """The **pre-trained** member of ``kind``."""
        return next((m for m in self.members if m.kind == kind and m.origin == "pretrained"), None)

    def with_member(self, member: Member) -> League:
        return League((*self.members, member), self.refused)


def site_member(kind: str, document: str, *, version_id: int, created_at: float) -> Member:
    """A model this appliance fitted on its own labels, as a league member. Its document went
    through the same validator at registration; it is validated again here, because a row in a
    database is as replaceable as a file in a package."""
    if kind not in KINDS:
        raise LeagueError(f"{kind!r} is not a kind this build knows")
    try:
        scorer = KINDS[kind][1](document, f"site-{kind}")
    except _REFUSALS as exc:
        raise LeagueError(f"the site model was refused: {exc}") from exc
    digest = hashlib.sha256(document.encode("utf-8")).hexdigest()
    manifest = {
        "artifact": {"kind": kind, "sha256": digest},
        "provenance": {
            "data": "this site's labels",
            "model_version_id": version_id,
            "fitted_at": created_at,
        },
    }
    return Member(kind, document, digest, manifest, scorer, "site")


def member_from(kind: str, document: str, manifest_text: str) -> Member:
    """Validate one member. Raises `LeagueError` with the reason, never a path."""
    if kind not in KINDS:
        raise LeagueError(f"{kind!r} is not a kind this build knows")
    if len(manifest_text.encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise LeagueError("the manifest is larger than any this build would write")
    try:
        manifest = json.loads(manifest_text)
    except ValueError as exc:
        raise LeagueError(f"the manifest is not valid JSON: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("artifact"), dict):
        raise LeagueError("the manifest has no artifact section")
    digest = hashlib.sha256(document.encode("utf-8")).hexdigest()
    if manifest["artifact"].get("sha256") != digest:
        raise LeagueError(
            "the model document does not match its manifest's SHA-256: they are not one artifact"
        )
    if manifest["artifact"].get("kind") != kind:
        raise LeagueError(
            f"the manifest describes a {manifest['artifact'].get('kind')!r}, not a {kind}"
        )
    try:
        scorer = KINDS[kind][1](document, kind)
    except _REFUSALS as exc:
        raise LeagueError(f"the model document was refused: {exc}") from exc
    return Member(kind, document, digest, manifest, scorer)


def load_dir(root: Traversable) -> League:
    """Every member under ``root``. A kind with neither file is simply absent; one file without
    the other, or a file that fails validation, is a refusal with its reason."""
    members: list[Member] = []
    refused: list[tuple[str, str]] = []
    for kind in KINDS:
        doc_file = root.joinpath(f"{kind}.json")
        man_file = root.joinpath(f"{kind}.manifest.json")
        present = (doc_file.is_file(), man_file.is_file())
        if not any(present):
            continue
        if not all(present):
            missing = "the model document" if not present[0] else "the manifest"
            refused.append((kind, f"incomplete: {missing} is missing"))
            continue
        try:
            document = doc_file.read_text(encoding="utf-8")
            manifest = man_file.read_text(encoding="utf-8")
            members.append(member_from(kind, document, manifest))
        except (OSError, UnicodeDecodeError) as exc:
            refused.append((kind, f"could not be read ({type(exc).__name__})"))
        except LeagueError as exc:
            refused.append((kind, str(exc)))
    return League(tuple(members), tuple(refused))


@cache
def load() -> League:
    """The packaged league. Cached: the files are part of the installed package."""
    return load_dir(resources.files("netcorenoc.engine.model").joinpath(DIRECTORY))


def parse_benchmark(text: str) -> list[list[float]]:
    """The shared benchmark: ``[y, w, *vector]`` per generated test pair, validated as data."""
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise LeagueError(f"the benchmark is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("format") != BENCHMARK_FORMAT:
        raise LeagueError("the benchmark is not a netcorenoc benchmark")
    rows = payload.get("rows")
    width = 2 + len(FEATURE_NAMES)
    if not isinstance(rows, list) or len(rows) > MAX_BENCHMARK_ROWS:
        raise LeagueError("the benchmark rows are malformed or too many")
    out: list[list[float]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) != width:
            raise LeagueError("a benchmark row has the wrong width")
        values = [float(v) for v in row if isinstance(v, int | float) and not isinstance(v, bool)]
        if len(values) != width or any(
            v != v or v in (float("inf"), float("-inf")) for v in values
        ):
            raise LeagueError("a benchmark row holds a value that is not a finite number")
        out.append(values)
    return out


@cache
def benchmark_rows() -> tuple[tuple[float, ...], ...]:
    """The packaged benchmark, or nothing when it is absent or refused (a latency measurement and
    a do-no-harm check then have nothing to run on, and say so)."""
    root = resources.files("netcorenoc.engine.model").joinpath(DIRECTORY).joinpath(BENCHMARK)
    if not root.is_file():
        return ()
    try:
        return tuple(tuple(r) for r in parse_benchmark(root.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, LeagueError):
        return ()


def benchmark_vectors() -> list[tuple[float, ...]]:
    return [tuple(r[2:]) for r in benchmark_rows()]
