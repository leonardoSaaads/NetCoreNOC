"""The v0.26.0 console, driven: the Judge dashboard, Settings' tabs and the kill switch (ADR #414).

Two of Part VIII's injections live here, each with its control:

* *a chart mixing generated and site data* — `test_every_judge_chart_names_its_dataset_and_n`
  fails when a chart's caption stops naming the dataset of the block it sits in, or names the other
  one;
* *a headline metric without its sample size or baseline* — the same test (every caption carries a
  count) and `test_the_headline_tiles_and_the_pr_curve_carry_interval_and_baseline`.

The shipped model here is `modelutil.TEST_MODEL` with a manifest in the shape `eval/synth/report.py`
writes, so the screen is driven through the real `/api/judge` route whatever `make train` last
produced.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import pytest

import domdriver
import uifixtures
from netcorenoc.engine.model import shipped
from netcorenoc.store import Store
from test_dom_harness import dom_test

import authutil
from modelutil import TEST_MODEL

HEADLINE = (
    "pairwise_f1",
    "ari",
    "over_merge_rate",
    "under_merge_rate",
    "split_bag_intact_rate",
    "asserted_negative_respected_rate",
    "repair_gestures",
)


def _arm(base: float) -> dict[str, Any]:
    arm: dict[str, Any] = {
        k: {"point": base, "low": base - 0.01, "high": base + 0.01} for k in HEADLINE
    }
    arm.update(
        streams=32, activations=15000, incidents=2800, concurrent_pairs=40, negatives_compared=9000
    )
    return arm


def _manifest(document: str) -> dict[str, Any]:
    curve = [[r / 10, 1 - r / 20, 0.5] for r in range(11)]
    pairs = {
        "pairs": 50000,
        "streams": 32,
        "positive_rate_weighted": 0.21,
        "positive_rate_unweighted": 0.3,
        "average_precision": {"point": 0.8, "low": 0.78, "high": 0.82, "n": 32.0},
        "roc_auc": {"point": 0.9, "low": 0.89, "high": 0.91, "n": 32.0},
        "log_loss": {"point": 0.13, "low": 0.12, "high": 0.14, "n": 32.0},
        "brier": {"point": 0.04, "low": 0.03, "high": 0.05, "n": 32.0},
        "calibration": {
            "bins": [[0.1, 0.12, 100.0], [0.5, 0.48, 80.0], [0.9, 0.91, 60.0]],
            "brier": 0.04,
            "reliability": 0.001,
            "resolution": 0.1,
            "uncertainty": 0.14,
        },
        "confusion": {
            "tp": 10.0,
            "fp": 2.0,
            "tn": 80.0,
            "fn": 8.0,
            "precision": 0.83,
            "recall": 0.55,
            "f1": 0.66,
        },
        "threshold_probability": 0.5,
        "pr_curve": curve,
        "roc_curve": curve,
    }
    trials = [
        {
            "index": i,
            "rung": 0,
            "rounds": 40,
            "valid_loss": 0.15 - i * 0.002,
            "train_loss": 0.14,
            "seconds": 30.0 + i,
            "status": "done",
            "best_round": 30,
            "params": {"learning_rate": 0.05 + i * 0.01, "max_bins": 16 + i},
        }
        for i in range(6)
    ]
    return {
        "artifact": {"sha256": hashlib.sha256(document.encode()).hexdigest()},
        "provenance": {
            "trained_with_version": "0.26.0",
            "seed": 2026,
            "data": "generated",
            "validation_rows": 50000,
            "held_out_families": ["bgp_flap"],
        },
        "ablation": {
            "delta_without": {"dt": 0.05, "hour": -0.0002},
            "kept": ["dt"],
            "dropped": ["hour"],
        },
        "search": {
            "trials": trials,
            "importance": {"learning_rate": 0.6, "max_bins": 0.1},
            "space": {"learning_rate": ["log", 0.02, 0.3], "max_bins": ["int", 8, 48]},
        },
        "final_fit": {
            "best_round": 120,
            "train_trace": [0.2, 0.15, 0.12],
            "valid_trace": [0.21, 0.16, 0.14],
            "trace_every": 10,
        },
        "evaluation": {
            "splits": {"test_iid": {"model": _arm(0.9), "formula": _arm(0.88)}},
            "held_out_families": {
                "bgp_flap": {"split": "test_protocol", "model": _arm(0.8), "formula": _arm(0.7)}
            },
            "pairs": {"test_iid": pairs},
            "first_hour": {
                "model": {"decisions": 120, **_arm(0.85)},
                "formula": {"decisions": 90, **_arm(0.8)},
            },
        },
        "quality_bar": [],
        "verdict": {"passed": True, "checked": 60, "missed": []},
    }


@pytest.fixture
def judged(monkeypatch: pytest.MonkeyPatch) -> None:
    model = shipped.load_from(TEST_MODEL, json.dumps(_manifest(TEST_MODEL)))
    monkeypatch.setattr(shipped, "load", lambda: model)


async def _routes(store: Store, role: str = "admin") -> dict[str, Any]:
    _engine, app = await uifixtures.corpus(store)
    return await uifixtures.capture(app, role)


@dom_test
async def test_every_judge_chart_names_its_dataset_and_n(store: Store, judged: None) -> None:
    result = domdriver.run_scenario("judge", {"routes": await _routes(store)})
    blocks = {b["dataset"]: b for b in result["blocks"]}
    assert {"generated data", "site data", "live traffic"} <= set(blocks), list(blocks)
    for chip, word, other in (
        ("generated data", "generated", "site data"),
        ("site data", "site data", "generated data"),
    ):
        drawn = [c for c in blocks[chip]["charts"] if c["drawn"]]
        assert drawn, f"the {chip} block drew no chart"
        for chart in drawn:
            caption = chart["caption"] or ""
            assert word in caption, f"{chart['title']!r} in the {chip} block does not name it"
            assert other not in caption, f"{chart['title']!r} names the other dataset: {caption}"
            assert re.search(r"\d", caption), f"{chart['title']!r} states no sample size"


@dom_test
async def test_the_headline_tiles_and_the_pr_curve_carry_interval_and_baseline(
    store: Store, judged: None
) -> None:
    result = domdriver.run_scenario("judge", {"routes": await _routes(store)})
    generated = next(b for b in result["blocks"] if b["dataset"] == "generated data")
    headline = [t for t in generated["tiles"] if "formula" in t]
    assert len(headline) >= 5, generated["tiles"]
    for tile in headline:
        interval = re.search(r"\d\.\d{3} \u2013 \d\.\d{3}", tile)
        assert interval, f"a headline without its interval: {tile}"
    notes = " ".join(generated["notes"])
    assert "streams" in notes and "incidents" in notes, "the headline states no n"
    pr = next(c for c in generated["charts"] if c["title"] == "Precision\u2013recall")
    assert "baseline" in (pr["caption"] or ""), "a PR curve without its baseline rate"


@dom_test
async def test_settings_has_four_tabs_and_the_old_scorer_address_lands_on_correlation(
    store: Store, judged: None
) -> None:
    routes = await _routes(store)
    settings = domdriver.run_scenario("render", {"routes": routes, "navigate": "#/settings"})
    for tab in ("Correlation", "Autonomy", "Search", "System"):
        assert tab in settings["dump"], f"the {tab} tab is missing"
    for text in ("What decides links", "Shipped model", "Additive formula", "provenance"):
        assert text in settings["dump"], text
    old = domdriver.run_scenario("render", {"routes": routes, "navigate": "#/scorer"})
    assert "What decides links" in old["dump"] and "Configured parameters" in old["dump"]


async def _autonomy_routes(store: Store, role: str) -> dict[str, Any]:
    _engine, app = await uifixtures.corpus(store)
    admin = await authutil.client_as(app, "admin")
    try:
        on = {"grouping": True, "naming": True, "reason": "driving the kill switch"}
        assert (await admin.post("/api/autonomy", json=on)).status_code == 200
    finally:
        await admin.aclose()
    return await uifixtures.capture(app, role)


@dom_test
async def test_the_kill_switch_is_in_the_top_bar_while_autonomy_acts(
    store: Store, judged: None
) -> None:
    routes = await _autonomy_routes(store, "editor")
    editor = domdriver.run_scenario(
        "render",
        {
            "routes": routes,
            "navigate": "#/overview",
            "updates": [{"stats": routes["/api/stats"]["json"]}],
        },
    )
    # The dump prints each text node on its own line: the mark's label and its grades are two.
    assert '"autonomy:"' in editor["dump"] and "grouping, naming" in editor["dump"]
    assert "Stop autonomy" in editor["dump"], "an editor has no kill switch in the top bar"


@dom_test
async def test_no_kill_switch_while_autonomy_is_off(store: Store, judged: None) -> None:
    """The control: the usual case says nothing, so the mark cannot be decoration."""
    routes = await _routes(store, "editor")
    result = domdriver.run_scenario(
        "render",
        {
            "routes": routes,
            "navigate": "#/overview",
            "updates": [{"stats": routes["/api/stats"]["json"]}],
        },
    )
    assert "Stop autonomy" not in result["dump"] and "autonomy:" not in result["dump"]


@pytest.fixture
def no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def absent() -> shipped.Shipped:
        raise shipped.NoShippedModelError(shipped.NOT_SHIPPED)

    monkeypatch.setattr(shipped, "load", absent)


@dom_test
async def test_a_build_without_a_model_says_so_as_a_state_not_a_fault(
    store: Store, no_model: None
) -> None:
    """v0.26.0 ships no model (#422), so this is every appliance's screen: the reason in words, as a
    note rather than an error, the shipped card disabled with it, and no generated-data chart."""
    routes = await _routes(store)
    assert routes["/api/decider"]["json"]["shipped"] == {
        "available": False,
        "absent": True,
        "reason": shipped.NOT_SHIPPED,
    }
    settings = domdriver.run_scenario("render", {"routes": routes, "navigate": "#/settings"})
    assert shipped.NOT_SHIPPED in settings["dump"]
    assert "This build ships no model." in settings["dump"], "the shipped card does not say why"
    assert "could not be loaded" not in settings["dump"], "absence shown as a fault"
    judge = domdriver.run_scenario("judge", {"routes": routes})
    generated = [b for b in judge["blocks"] if b["dataset"] == "generated data"]
    assert not [c for b in generated for c in b["charts"] if c["drawn"]], "a chart with no model"
    page = domdriver.run_scenario("render", {"routes": routes, "navigate": "#/promotion"})
    note = re.compile(r"<p \.hint>\s*\"" + re.escape(shipped.NOT_SHIPPED))
    fault = re.compile(r"<p \.err>\s*\"" + re.escape(shipped.NOT_SHIPPED))
    for dump in (settings["dump"], page["dump"]):
        assert note.search(dump), "the reason is not shown as a note"
        assert not fault.search(dump), "a build without a model shown as an error"
