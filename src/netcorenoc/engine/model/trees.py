"""Tree ensembles over the v2 feature vector — decision tree, random forest, boosted trees.

v0.27.0 (ADR #424). Three members of the model league share one document format, because all three
are the same arithmetic: a list of binary trees whose leaves hold **log-odds**, summed and scaled.

    logit(x) = base + scale · Σₜ leafₜ(x)

* ``decision_tree`` — one tree; each leaf holds the smoothed log-odds of its training rows;
* ``random_forest`` — many trees grown on row subsamples with a random feature subset per split;
  ``scale = 1/T``, so the forest's logit is the mean of its trees' leaf log-odds;
* ``boosted_trees`` — Newton-boosted regression trees; ``base`` is the prior log-odds and each leaf
  already carries its learning rate.

A node sends a pair **left when** ``x[feature] <= threshold``. The fitter bins with the same rule
(`trees_fit`), so a value that sat on an edge in training goes the same way when it is served.

## The document is data, never code (ADR #405, kept)

A JSON object with exactly the keys :data:`_KEYS`, validated here before a scorer exists: finite
bounded numbers, a feature list drawn only from :data:`FEATURE_NAMES`, bounded tree count, node
count and depth, children that point strictly forward (so the structure is a tree and cannot
loop), every node reached exactly once, and **reachability** — a document whose logit cannot cross
its threshold scores without error and groups nothing, and is refused.

## The explanation: exact Shapley values against one reference pair

A tree predicts a leaf, not a weighted sum, so its per-feature contribution must be computed. The
v0.14.0 kinds enumerated 2^F coalitions against a background set, which is exact and costs 2^F; at
fifteen features that door is shut (the reason the GAM was chosen in v0.26.0, ADR #407).

**Against a single reference pair ``z`` the interventional Shapley value of a tree is exact in
time linear in the tree** (Lundberg et al., *From local explanations to global understanding with
explainable AI for trees*, Nature Machine Intelligence 2, 2020 — the one-background-sample case of
interventional TreeSHAP). For the hybrid input that takes feature ``i`` from ``x`` when ``i ∈ S``
and from ``z`` otherwise, exactly one leaf is reached, and a leaf is reached iff every feature on
its path that only ``x`` satisfies is in ``S`` (set ``A``) and every feature that only ``z``
satisfies is not (set ``B``). Its game ``v·[A ⊆ S][B ∩ S = ∅]`` has the closed-form Shapley values

    φᵢ = v · (a-1)! b! / (a+b)!   for i ∈ A        φᵢ = -v · a! (b-1)! / (a+b)!   for i ∈ B

and Shapley values are linear in the game, so a tree's attribution is the sum over the leaves the
walk can reach, and an ensemble's is the scaled sum over its trees. The contributions add up to
``logit(x) - logit(z)``: ``base_value`` is ``logit(z)``, and the contract's check
``sum(contributions) + base_value == score`` holds. ``z`` is the training median of each feature,
stored in the document; two documents computing the same function with the same reference get the
same attribution, which is the property ADR #190 required.

The walk visits only branches that ``x`` or ``z`` can take, so it is cheap; and the correlator
builds an explanation only for the links it keeps (at most five per activation), never per
candidate.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

from netcorenoc.engine.correlate.features import FEATURE_NAMES
from netcorenoc.engine.correlate.scorer_contract import (
    BASIS_SHAPLEY,
    CONTRACT_VERSION,
    LinkFeatures,
    LinkScore,
    TermContribution,
)
from netcorenoc.engine.model import trees_shap
from netcorenoc.engine.model.trees_shap import LEAF, MAX_DEPTH

__all__ = [
    "FORMAT",
    "LEAF",
    "MAX_DEPTH",
    "MAX_NODES",
    "MAX_TREES",
    "METHODS",
    "TreesDocumentError",
    "TreesModel",
    "TreesScorer",
    "load",
    "validate",
]

FORMAT = "netcorenoc.trees/1"
METHODS = ("decision_tree", "random_forest", "boosted_trees")

MAX_TREES = 600
MAX_NODES = 2047  # per tree: a full binary tree of depth 10
MAX_TOTAL_NODES = 120_000
MAX_ABS_SCORE = 25.0
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024

_KEYS = frozenset(
    {"format", "method", "features", "reference", "base", "scale", "trees", "threshold", "grouping"}
)
_GROUPING_KEYS = frozenset({"join_bias", "merge_bias", "merge_min_pairs"})


class TreesDocumentError(ValueError):
    """A trees document that is malformed, oversized or degenerate. Never activated."""


@dataclass(frozen=True)
class Tree:
    """One tree, compiled to parallel tuples. ``feature`` holds **runtime vector** positions."""

    feature: tuple[int, ...]  # LEAF for a leaf
    local: tuple[int, ...]  # the same position in the model's own feature list (for attribution)
    threshold: tuple[float, ...]
    left: tuple[int, ...]
    right: tuple[int, ...]
    value: tuple[float, ...]

    def leaf_of(self, vector: tuple[float, ...] | list[float]) -> float:
        i = 0
        feature, threshold, left, right = self.feature, self.threshold, self.left, self.right
        f = feature[0]
        while f >= 0:
            i = left[i] if vector[f] <= threshold[i] else right[i]
            f = feature[i]
        return self.value[i]


@dataclass(frozen=True)
class TreesModel:
    """A validated document, ready to score."""

    method: str
    features: tuple[str, ...]
    reference: tuple[float, ...]
    base: float
    scale: float
    trees: tuple[Tree, ...]
    threshold: float
    grouping: dict[str, float]


def _num(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TreesDocumentError(f"{what} must be a number, got {type(value).__name__}")
    out = float(value)
    if not math.isfinite(out):
        raise TreesDocumentError(f"{what} must be finite, got {value!r}")
    return out


def _refuse_constant(name: str) -> float:
    raise TreesDocumentError(f"non-finite constant {name} in document")


def _tree(raw: Any, index: int, positions: tuple[int, ...]) -> Tree:
    """One tree: forward-pointing children, every node reached once, depth bounded."""
    what = f"tree {index}"
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_NODES:
        raise TreesDocumentError(f"{what} must hold between 1 and {MAX_NODES} nodes")
    n = len(raw)
    feature: list[int] = []
    local: list[int] = []
    threshold: list[float] = []
    left: list[int] = []
    right: list[int] = []
    value: list[float] = []
    parents = [0] * n
    for i, node in enumerate(raw):
        if not isinstance(node, list) or len(node) != 5:
            raise TreesDocumentError(
                f"{what} node {i} must be [feature, threshold, left, right, value]"
            )
        f = node[0]
        if isinstance(f, bool) or not isinstance(f, int):
            raise TreesDocumentError(f"{what} node {i}: the feature must be an integer")
        if f == LEAF:
            v = _num(node[4], f"{what} leaf {i}")
            if abs(v) > MAX_ABS_SCORE:
                raise TreesDocumentError(
                    f"{what} leaf {i} is beyond ±{MAX_ABS_SCORE}: a hard switch"
                )
            feature.append(LEAF)
            local.append(LEAF)
            threshold.append(0.0)
            left.append(0)
            right.append(0)
            value.append(v)
            continue
        if not 0 <= f < len(positions):
            raise TreesDocumentError(f"{what} node {i} names feature {f}, outside the model's list")
        lo, hi = node[2], node[3]
        if any(isinstance(c, bool) or not isinstance(c, int) for c in (lo, hi)):
            raise TreesDocumentError(f"{what} node {i}: children must be integers")
        if not (i < lo < n and i < hi < n and lo != hi):
            raise TreesDocumentError(
                f"{what} node {i}: children must point forward, inside the tree"
            )
        parents[lo] += 1
        parents[hi] += 1
        feature.append(positions[f])
        local.append(f)
        threshold.append(_num(node[1], f"{what} node {i} threshold"))
        left.append(lo)
        right.append(hi)
        value.append(0.0)
    if parents[0] != 0 or any(p != 1 for p in parents[1:]):
        raise TreesDocumentError(f"{what} is not a tree: every node but the root needs one parent")
    depth = [0] * n
    for i in range(n):
        if feature[i] != LEAF:
            for c in (left[i], right[i]):
                depth[c] = depth[i] + 1
    if max(depth) > MAX_DEPTH:
        raise TreesDocumentError(f"{what} is deeper than {MAX_DEPTH}")
    return Tree(
        tuple(feature), tuple(local), tuple(threshold), tuple(left), tuple(right), tuple(value)
    )


def validate(document: str) -> TreesModel:
    """Parse and validate. **The single validation point.** Raises `TreesDocumentError`."""
    if len(document.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise TreesDocumentError(f"document exceeds {MAX_DOCUMENT_BYTES} bytes")
    try:
        payload = json.loads(document, parse_constant=_refuse_constant)
    except (TypeError, ValueError) as exc:
        raise TreesDocumentError(f"document is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != _KEYS:
        got = sorted(payload) if isinstance(payload, dict) else type(payload).__name__
        raise TreesDocumentError(f"document must have exactly the keys {sorted(_KEYS)}, got {got}")
    if payload["format"] != FORMAT:
        raise TreesDocumentError(f"format must be {FORMAT!r}, got {payload['format']!r}")
    if payload["method"] not in METHODS:
        raise TreesDocumentError(f"method must be one of {METHODS}, got {payload['method']!r}")
    features = payload["features"]
    if not isinstance(features, list) or not features or len(set(features)) != len(features):
        raise TreesDocumentError("features must be a non-empty list of distinct names")
    if any(not isinstance(f, str) or f not in FEATURE_NAMES for f in features):
        raise TreesDocumentError(f"features must be drawn from {FEATURE_NAMES}")
    positions = tuple(FEATURE_NAMES.index(f) for f in features)
    reference = payload["reference"]
    if not isinstance(reference, list) or len(reference) != len(features):
        raise TreesDocumentError("reference must hold one value per feature")
    raw_trees = payload["trees"]
    if not isinstance(raw_trees, list) or not 1 <= len(raw_trees) <= MAX_TREES:
        raise TreesDocumentError(f"trees must be a list of 1 to {MAX_TREES} trees")
    if payload["method"] == "decision_tree" and len(raw_trees) != 1:
        raise TreesDocumentError("a decision tree is exactly one tree")
    trees = tuple(_tree(raw, i, positions) for i, raw in enumerate(raw_trees))
    if sum(len(t.feature) for t in trees) > MAX_TOTAL_NODES:
        raise TreesDocumentError(f"the model holds more than {MAX_TOTAL_NODES} nodes")
    grouping = payload["grouping"]
    if not isinstance(grouping, dict) or set(grouping) != _GROUPING_KEYS:
        raise TreesDocumentError(f"grouping must have exactly the keys {sorted(_GROUPING_KEYS)}")
    base = _num(payload["base"], "base")
    scale = _num(payload["scale"], "scale")
    if abs(base) > MAX_ABS_SCORE:
        raise TreesDocumentError("base is beyond the magnitude bound")
    if not 0.0 < scale <= 1.0:
        raise TreesDocumentError("scale must be in (0, 1]")
    model = TreesModel(
        str(payload["method"]),
        tuple(features),
        tuple(_num(v, "reference") for v in reference),
        base,
        scale,
        trees,
        _num(payload["threshold"], "threshold"),
        {k: _num(v, f"grouping.{k}") for k, v in sorted(grouping.items())},
    )
    if model.grouping["merge_min_pairs"] < 1:
        raise TreesDocumentError("grouping.merge_min_pairs must be at least 1")
    _reachable(model)
    return model


def _reachable(model: TreesModel) -> None:
    """Refuse a model that can only ever answer one way (the Gate 0 lesson, ADR #164)."""
    low = model.base + model.scale * sum(min(t.value[i] for i in _leaves(t)) for t in model.trees)
    high = model.base + model.scale * sum(max(t.value[i] for i in _leaves(t)) for t in model.trees)
    if not low < model.threshold < high:
        raise TreesDocumentError(
            f"the threshold {model.threshold} is outside the attainable logit range "
            f"[{low:.3f}, {high:.3f}]: the model cannot discriminate"
        )


def _leaves(tree: Tree) -> list[int]:
    return [i for i, f in enumerate(tree.feature) if f == LEAF]


def fingerprint(document: str) -> str:
    return hashlib.sha256(f"trees\n{CONTRACT_VERSION}\n{document}".encode()).hexdigest()


class TreesScorer:
    """A `LinkScorer` over the v2 feature vector. Pure and deterministic."""

    def __init__(self, model: TreesModel, document: str, scorer_id: str = "trees") -> None:
        self.model = model
        self.params_document = document
        self._fingerprint = fingerprint(document)
        self._scorer_id = scorer_id
        self._trees = model.trees
        self._base = model.base
        self._scale = model.scale
        self.threshold = model.threshold
        # The reference pair laid out as a runtime vector, so a tree reads it like any other.
        ref = [0.0] * len(FEATURE_NAMES)
        for name, v in zip(model.features, model.reference, strict=True):
            ref[FEATURE_NAMES.index(name)] = v
        self._reference = tuple(ref)
        self.base_value = self.logit(self._reference)

    @property
    def scorer_id(self) -> str:
        return self._scorer_id

    @property
    def contract_version(self) -> str:
        return CONTRACT_VERSION

    def params_fingerprint(self) -> str:
        return self._fingerprint

    def logit(self, vector: tuple[float, ...] | list[float]) -> float:
        """The hot path: one walk per tree, no explanation built."""
        total = 0.0
        for tree in self._trees:
            total += tree.leaf_of(vector)
        return self._base + self._scale * total

    def attribution(self, vector: tuple[float, ...]) -> list[float]:
        """Exact Shapley values against the reference pair, one per model feature."""
        phi = [0.0] * len(self.model.features)
        z = self._reference
        for tree in self._trees:
            trees_shap.walk(tree, vector, z, phi, self._scale)
        return phi

    def explain(self, vector: tuple[float, ...]) -> LinkScore:
        """The logit **and** its decomposition: one Shapley value per model feature."""
        phi = self.attribution(vector)
        terms = tuple(
            TermContribution(name, 0.0, vector[FEATURE_NAMES.index(name)], phi[k])
            for k, name in enumerate(self.model.features)
        )
        # The decision is the fast path's own number, so an explained link can never disagree with
        # the verdict the correlator acted on; the terms sum to it within float rounding.
        total = self.logit(vector)
        return LinkScore(
            linked=total > self.threshold,
            score=total,
            threshold=self.threshold,
            terms=terms,
            basis=BASIS_SHAPLEY,
            base_value=self.base_value,
        )

    def score(self, features: LinkFeatures) -> LinkScore:
        if features.vector is None:
            raise TreesDocumentError(
                "a trees scorer needs the v2 feature vector, and none was built"
            )
        return self.explain(features.vector)


def load(document: str, scorer_id: str = "trees") -> TreesScorer:
    return TreesScorer(validate(document), document, scorer_id)
