"""The shipped model, as packaged (ADRs #405, #409, #422): what the maintainer is promised about it.

**The package carries both files and they passed the bar, or it carries neither and says so.**
v0.26.0 carries neither (#422): the model trained for it did not pass its own quality bar. The
invariant below holds for either state, so the build that finally ships a model is held to every
check here without a line of this file changing — a document without its manifest, a manifest whose
verdict failed, or a bar loosened after the numbers came in turns it red.

Each load state is also exercised on a directory of its own, so none of them depends on what this
build happens to package.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

from netcorenoc.engine.correlate.features import FEATURE_NAMES
from netcorenoc.engine.model import gam, shipped
from netcorenoc.engine.operate.engine import Engine
from netcorenoc.store import Store

from modelutil import TEST_MODEL
from synth import dataset, report, train
from synth.verify import differences

PACKAGE = resources.files("netcorenoc.engine.model")


def _passing_manifest(document: str) -> dict[str, Any]:
    """A manifest `make train` could have written for ``document``: every check passed."""
    return {
        "artifact": {"sha256": hashlib.sha256(document.encode()).hexdigest()},
        "quality_bar": [
            {"quantity": q, "scope": s, "kind": k, "limit": v} for q, s, k, v in train.QUALITY_BAR
        ],
        "verdict": {"passed": True, "missed": [], "checked": 0, "checks": []},
    }


def _write(root: Path, document: str | None, manifest: dict[str, Any] | None) -> Path:
    if document is not None:
        (root / shipped.ARTIFACT).write_text(document)
    if manifest is not None:
        (root / shipped.MANIFEST).write_text(json.dumps(manifest))
    return root


def check_packaged(model: shipped.Shipped) -> None:
    """Every promise a packaged model makes. Run on the real package when it carries one."""
    manifest = model.manifest
    assert manifest["artifact"]["sha256"] == model.sha256
    assert manifest["artifact"]["kind"] == gam.KIND
    features = list(model.scorer.model.features)
    assert features and set(features) <= set(FEATURE_NAMES)
    assert not [f for f in features if "incumbent" in f]
    check_verdict(manifest)
    held_out = set(dataset.HOLDOUT_OPTICAL + dataset.HOLDOUT_PROTOCOL)
    evaluated = manifest["evaluation"]["held_out_families"]
    assert set(evaluated) == held_out
    assert all(arms["model"]["incidents"] > 0 for arms in evaluated.values())
    provenance = manifest["provenance"]
    assert not held_out & set(provenance["training_families"])
    assert provenance["data"].startswith("generated")
    assert provenance["seed"] == dataset.SEED and len(provenance["commit"]) == 40
    rows = manifest["benchmark"]
    assert 0 < len(rows) <= train.BENCHMARK_ROWS
    assert all(len(r) == 2 + len(FEATURE_NAMES) for r in rows)


def check_verdict(manifest: dict[str, Any]) -> None:
    """The bar in the manifest is the bar in the code, and the published numbers pass it: a bar
    edited after the numbers were seen, or a verdict that disagrees with its numbers, shows here."""
    published = [
        (c["quantity"], c["scope"], c["kind"], c["limit"]) for c in manifest["quality_bar"]
    ]
    assert published == list(train.QUALITY_BAR)
    verdict = report.check_bar(manifest["evaluation"], train.QUALITY_BAR)
    assert verdict["passed"], verdict["missed"]
    assert verdict == manifest["verdict"]


def test_the_package_carries_a_passing_model_or_none_and_says_so() -> None:
    present = [PACKAGE.joinpath(name).is_file() for name in (shipped.ARTIFACT, shipped.MANIFEST)]
    assert all(present) or not any(present), "one of the two model files is packaged alone"
    if not any(present):  # v0.26.0 (#422)
        with pytest.raises(shipped.NoShippedModelError) as refused:
            shipped.load_dir(PACKAGE)
        assert str(refused.value) == shipped.NOT_SHIPPED
        assert not PACKAGE.joinpath(shipped.MANIFEST + ".candidate").is_file()
        return
    check_packaged(shipped.load_dir(PACKAGE))


def test_each_load_state_says_what_it_is_and_names_no_path(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(shipped.NoShippedModelError, match="ships no model"):
        shipped.load_dir(empty)
    (tmp_path / "alone").mkdir()
    alone = _write(tmp_path / "alone", TEST_MODEL, None)
    incomplete = r"incomplete: linkmodel\.manifest\.json is missing"
    with pytest.raises(shipped.ShippedModelError, match=incomplete) as e:
        shipped.load_dir(alone)
    assert not isinstance(e.value, shipped.NoShippedModelError), "a broken build is not a choice"
    assert str(tmp_path) not in str(e.value)
    (tmp_path / "whole").mkdir()
    whole = _write(tmp_path / "whole", TEST_MODEL, _passing_manifest(TEST_MODEL))
    assert shipped.load_dir(whole).sha256 == hashlib.sha256(TEST_MODEL.encode()).hexdigest()


def test_a_tampered_document_is_refused(tmp_path: Path) -> None:
    """The SHA-256 check is live: a document edited after its manifest was written is refused."""
    doc = json.loads(TEST_MODEL)
    doc["intercept"] = float(doc["intercept"]) + 1.0
    tampered = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    root = _write(tmp_path, tampered, _passing_manifest(TEST_MODEL))
    with pytest.raises(shipped.ShippedModelError, match="SHA-256"):
        shipped.load_dir(root)


def test_a_verdict_is_rechecked_against_its_numbers_and_the_bar_in_force() -> None:
    """Control for `check_verdict`: numbers that pass the bar are accepted; numbers that miss it
    are refused whatever the manifest's own verdict claims; a loosened bar is refused too."""
    from test_synth import _arms

    evaluation: dict[str, Any] = {
        "splits": {"test_iid": _arms(0.30, 0.40)},
        "held_out_families": {"bgp_flap": _arms(2.0, 2.5)},
    }
    bar = [{"quantity": q, "scope": s, "kind": k, "limit": v} for q, s, k, v in train.QUALITY_BAR]
    passing: dict[str, Any] = {
        "quality_bar": bar,
        "evaluation": evaluation,
        "verdict": report.check_bar(evaluation, train.QUALITY_BAR),
    }
    check_verdict(passing)
    missed = {**passing, "evaluation": {**evaluation, "splits": {"test_iid": _arms(0.40, 0.40)}}}
    with pytest.raises(AssertionError):
        check_verdict(missed)  # repair x1.00 where the bar asks x0.90; its verdict says passed
    loosened = {**passing, "quality_bar": [{**c, "limit": 1.0} for c in bar]}
    with pytest.raises(AssertionError):
        check_verdict(loosened)


async def test_without_a_model_the_formula_decides_and_the_bell_says_why(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    def absent() -> shipped.Shipped:
        raise shipped.NoShippedModelError(shipped.NOT_SHIPPED)

    monkeypatch.setattr(shipped, "load", absent)
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    assert engine.decider_ref.startswith("additive:"), "the formula as configured decides"
    warnings = engine.scorer_warning_list()
    assert any(w.startswith("This build ships no model") for w in warnings), warnings
    assert not any("could not be used" in w for w in warnings), "absence is not a fault"
    assert not any("/" in w for w in warnings), "a path in the bell"


def test_differences_compares_exactly_and_treats_nan_as_nan() -> None:
    assert differences({"a": [1.0, float("nan")]}, {"a": [1.0, float("nan")]}) == []
    assert differences({"a": 0.5}, {"a": 0.5000001}) == ["/a: published 0.5, measured 0.5000001"]
    assert differences({"a": 1}, {"b": 1}) == [
        "/a: present on one side only",
        "/b: present on one side only",
    ]
