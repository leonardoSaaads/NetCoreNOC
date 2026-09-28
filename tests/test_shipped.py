"""The shipped model, as packaged (ADRs #405, #409): what the maintainer is promised about it.

Each test reads the two files **as installed** — `shipped.load()`, the same call the engine makes —
so a document and a manifest that drifted apart, a bar that was loosened after the numbers came in,
or a held-out family that silently dropped out of the evaluation, turns this file red.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

from netcorenoc.engine.correlate.features import FEATURE_NAMES
from netcorenoc.engine.model import gam, shipped

from synth import dataset, report, train
from synth.verify import differences


@pytest.fixture(scope="module")
def model() -> shipped.Shipped:
    return shipped.load()


def test_the_packaged_model_loads_as_data_and_matches_its_manifest(model: shipped.Shipped) -> None:
    assert model.manifest["artifact"]["sha256"] == model.sha256
    assert model.manifest["artifact"]["kind"] == gam.KIND
    assert model.ref == f"shipped:{model.sha256[:12]}"
    # The document is JSON the validator accepted; nothing in it names code.
    assert json.loads(model.document)["format"] == gam.FORMAT


def test_a_tampered_document_is_refused(model: shipped.Shipped) -> None:
    """Control for the test above: the check is live, not a tautology."""
    doc = json.loads(model.document)
    doc["intercept"] = float(doc["intercept"]) + 1.0
    tampered = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    with pytest.raises(shipped.ShippedModelError, match="SHA-256"):
        shipped.load_from(tampered, json.dumps(model.manifest))


def test_the_model_reads_only_features_this_build_serves(model: shipped.Shipped) -> None:
    features = list(model.scorer.model.features)
    assert features and set(features) <= set(FEATURE_NAMES)
    assert not [f for f in features if "incumbent" in f]


def test_the_manifest_records_the_bar_in_force_and_a_passing_verdict(
    model: shipped.Shipped,
) -> None:
    """The bar in the manifest is the bar in the code, and re-checking the published numbers
    against it passes: a bar edited after the numbers were seen would show here."""
    published = [
        (c["quantity"], c["scope"], c["kind"], c["limit"]) for c in model.manifest["quality_bar"]
    ]
    assert published == list(train.QUALITY_BAR)
    verdict = report.check_bar(model.manifest["evaluation"], train.QUALITY_BAR)
    assert verdict["passed"], verdict["missed"]
    assert verdict == model.manifest["verdict"]


def test_every_held_out_family_was_evaluated_and_none_was_trained_on(
    model: shipped.Shipped,
) -> None:
    held_out = set(dataset.HOLDOUT_OPTICAL + dataset.HOLDOUT_PROTOCOL)
    evaluated = model.manifest["evaluation"]["held_out_families"]
    assert set(evaluated) == held_out
    for family, arms in evaluated.items():
        assert arms["model"]["incidents"] > 0, family
    assert not held_out & set(model.manifest["provenance"]["training_families"])


def test_provenance_names_generated_data_and_a_reproducible_run(model: shipped.Shipped) -> None:
    provenance = model.manifest["provenance"]
    assert provenance["data"].startswith("generated")
    assert provenance["command"] == "make train"
    assert provenance["seed"] == dataset.SEED
    assert len(provenance["commit"]) == 40


def test_the_benchmark_rows_fit_the_models_feature_vector(model: shipped.Shipped) -> None:
    rows = model.manifest["benchmark"]
    assert 0 < len(rows) <= train.BENCHMARK_ROWS
    assert all(len(r) == 2 + len(FEATURE_NAMES) for r in rows)


def test_differences_compares_exactly_and_treats_nan_as_nan() -> None:
    assert differences({"a": [1.0, float("nan")]}, {"a": [1.0, float("nan")]}) == []
    assert differences({"a": 0.5}, {"a": 0.5000001}) == ["/a: published 0.5, measured 0.5000001"]
    assert differences({"a": 1}, {"b": 1}) == [
        "/a: present on one side only",
        "/b: present on one side only",
    ]
