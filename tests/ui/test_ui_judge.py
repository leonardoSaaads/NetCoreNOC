"""The v0.27.0 console, driven: the league on the Judge screen, Settings, Labelling and Pending.

Two of the release's injections live here, each with its control:

* *a chart mixing generated and site data* — `test_every_judge_chart_names_its_dataset_and_n`
  fails when a chart's caption stops naming the dataset of the block it sits in, or names the other;
* *a model's ten charts incomplete* — `test_one_models_ten_charts_are_all_drawn` fails when any of
  the maintainer's ten is missing or drawn empty.

The league here is three members — the test GAM, a hand-written decision tree, a small logistic
regression — each with a manifest in the exact shape `eval/synth/league.py` writes, so the screens
are driven through the real `/api/judge` and `/api/decider` routes whatever `make train` produced.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import pytest

from netcorenoc.engine.model import league, linear_fit, shipped
from netcorenoc.store import Store

import authutil
import domdriver
import uifixtures
from domdriver import dom_test
from leaguefixtures import league_data, tree_doc
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
SUITES = ("test_iid", "test_concurrency", "test_optical", "test_protocol")


def _arm(base: float) -> dict[str, Any]:
    arm: dict[str, Any] = {
        k: {"point": base, "low": base - 0.01, "high": base + 0.01} for k in HEADLINE
    }
    arm.update(
        streams=32, activations=15000, incidents=2800, concurrent_pairs=40, negatives_compared=9000
    )
    return arm


def _manifest(document: str, kind: str, f1: float) -> str:
    curve = [[r / 10, 1 - r / 20, 0.5] for r in range(11)]
    pairs = {
        "pairs": 50000,
        "streams": 32,
        "positive_rate_weighted": 0.21,
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
        "residuals": {
            "edges": [-1.0, -0.5, 0.0, 0.5, 1.0],
            "positive": [0.0, 0.0, 0.05, 0.15],
            "negative": [0.1, 0.6, 0.1, 0.0],
            "mean": -0.02,
            "mean_abs": 0.18,
        },
    }
    trials = [
        {
            "index": i,
            "rung": 0 if i < 4 else 1,
            "capacity": 40,
            "params": {"alpha": 0.1 + i, "depth": 2.0 + i},
            "best": 30,
            "train_loss": 0.14,
            "valid_loss": 0.15 - i * 0.002,
            "seconds": 30.0 + i,
            "status": "done",
        }
        for i in range(6)
    ]
    return json.dumps(
        {
            "artifact": {"sha256": hashlib.sha256(document.encode()).hexdigest(), "kind": kind},
            "provenance": {"trained_with_version": "0.27.0", "seed": 2026, "data": "generated"},
            "search": {
                "trials": trials,
                "importance": {"alpha": 0.6, "depth": 0.1},
                "space": {"alpha": ["log", 0.01, 10.0], "depth": ["int", 2, 8]},
            },
            "final_fit": {
                "capacity": "rounds",
                "best": 30,
                "points": [10, 20, 30],
                "train_trace": [0.2, 0.15, 0.12],
                "valid_trace": [0.21, 0.16, 0.14],
                "seconds": 12.0,
            },
            "evaluation": {
                "splits": {s: {"model": _arm(f1), "formula": _arm(0.88)} for s in SUITES},
                "held_out_families": {
                    "bgp_flap": {"split": "test_protocol", "model": _arm(0.8), "formula": _arm(0.7)}
                },
                "pairs": {"test_iid": pairs},
            },
            "corpus": {
                "aggregate": {
                    "pairwise_f1": f1,
                    "ari": f1,
                    "over_merge_rate": 0.0,
                    "under_merge_rate": 0.1,
                },
                "scenarios": {
                    "fiber_cut": {"pairwise_f1": f1, "ari": f1},
                    "olt_storm": {"pairwise_f1": 1.0, "ari": 1.0},
                },
            },
            "latency": {"logit_us": 4.0, "explain_us": 30.0, "pairs": 2000},
            "scorecard": {"passed": False, "checked": 10, "missed": ["x"], "checks": []},
        }
    )


@pytest.fixture
def leagued(monkeypatch: pytest.MonkeyPatch) -> league.League:
    logistic = linear_fit.fit(
        league_data(400, 1), league_data(200, 2), linear_fit.LinearParams()
    ).document
    members = league.League(
        (
            league.member_from("gam", TEST_MODEL, _manifest(TEST_MODEL, "gam", 0.90)),
            league.member_from(
                "decision_tree", tree_doc(), _manifest(tree_doc(), "decision_tree", 0.95)
            ),
            league.member_from(
                "logistic_regression", logistic, _manifest(logistic, "logistic_regression", 0.70)
            ),
        )
    )
    monkeypatch.setattr(league, "load", lambda: members)
    gam_member = members.by_kind("gam")
    assert gam_member is not None
    model = shipped.load_from(gam_member.document, json.dumps(gam_member.manifest))
    monkeypatch.setattr(shipped, "load", lambda: model)
    return members


async def _routes(store: Store, role: str = "admin") -> dict[str, Any]:
    _engine, app = await uifixtures.corpus(store)
    return await uifixtures.capture(app, role)


@dom_test
async def test_every_judge_chart_names_its_dataset_and_n(
    store: Store, leagued: league.League
) -> None:
    result = domdriver.run_scenario("judge", {"routes": await _routes(store)})
    blocks = {b["dataset"]: b for b in result["blocks"] if b["dataset"]}
    assert {"generated data", "site data", "live traffic"} <= set(blocks), list(blocks)
    generated = [b for b in result["blocks"] if b["dataset"] == "generated data"]
    drawn = [c for b in generated for c in b["charts"] if c["drawn"]]
    assert len(drawn) >= 8, "the comparison view drew too few charts"
    for chart in drawn:
        caption = chart["caption"] or ""
        assert "generated" in caption or "corpus" in caption, (chart["title"], caption)
        assert "site data" not in caption, f"{chart['title']!r} names the other dataset"
    for chart in blocks["site data"]["charts"]:
        caption = chart["caption"] or ""
        assert "site data" in caption and "generated" not in caption, (chart["title"], caption)
        assert re.search(r"\d|answers|no run", caption), f"{chart['title']!r} states no sample"


@dom_test
async def test_one_models_ten_charts_are_all_drawn(store: Store, leagued: league.League) -> None:
    routes = await _routes(store)
    result = domdriver.run_scenario(
        "judge", {"routes": routes, "navigate": "#/promotion?model=decision_tree"}
    )
    charts = [c for b in result["blocks"] for c in b["charts"]]
    titles = [c["title"] or "" for c in charts]
    # v0.29.0: the maintainer's ten, by name — the numbers left the titles as on-screen noise.
    for name in (
        "Validation score ×",
        "Train × validation curve",
        "Pairwise F1 on every suite",  # 3, model performance: the suites drawn as bars
        "Hyperparameter importance",
        "Optimisation history",
        "Confusion matrix",
        "ROC",
        "Prediction vs actual",
        "Residual distribution",
        "Training time × performance",
    ):
        mine = [c for c in charts if (c["title"] or "").startswith(name)]
        assert mine, f"chart {name!r} is missing: {titles}"
        assert any(c["drawn"] for c in mine), f"chart {name!r} is drawn empty"
    assert any("Prediction vs actual" in t for t in titles)
    assert any("Residual distribution" in t for t in titles)
    # The two regression charts say what they are for a classifier, in their captions.
    captions = " ".join(re.sub(r"\s+", " ", c["caption"] or "") for c in charts)
    assert "a stated 0.8 comes true 80 % of the time" in captions
    assert "confident and wrong" in captions


@dom_test
async def test_the_league_says_who_decides_why_and_every_models_role(
    store: Store, leagued: league.League
) -> None:
    routes = await _routes(store)
    payload = routes["/api/judge"]["json"]
    roles = {m["kind"]: m["role"] for m in payload["members"]}
    assert roles == {
        "decision_tree": "champion",
        "gam": "challenger",
        "logistic_regression": "challenger",
    }, roles
    page = domdriver.run_scenario("render", {"routes": routes, "navigate": "#/promotion"})
    dump = page["dump"]
    for text in (
        "Fast loop",
        "Slow loop",
        "Decision tree",
        "deciding",
        "in shadow",
        "Compare all",
        "Logistic regression",
        "the reference",
    ):
        assert text in dump, text
    assert "What a model needs before it can decide" not in dump
    assert "floor" not in dump.lower().replace("floors", ""), "a labelling floor survived"


@dom_test
async def test_settings_explains_models_and_offers_automatic_or_pinned(
    store: Store, leagued: league.League
) -> None:
    routes = await _routes(store)
    settings = domdriver.run_scenario("render", {"routes": routes, "navigate": "#/settings"})
    for tab in ("Models", "Autonomy", "Site training", "System"):
        assert tab in settings["dump"], f"the {tab} tab is missing"
    for text in (
        "Models compete; the judge chooses.",
        "Automatic",
        "Pinned",
        "Fail-safe formula",
        "Decision tree",
        "chosen by the judge",
    ):
        assert text in settings["dump"], text
    assert "Additive formula" not in settings["dump"], "the formula offered as a choice"
    old = domdriver.run_scenario("render", {"routes": routes, "navigate": "#/scorer"})
    assert "Models compete; the judge chooses." in old["dump"]
    assert "1970" not in old["dump"]
    for tab in ("search", "autonomy", "system"):
        page = domdriver.run_scenario(
            "render", {"routes": routes, "navigate": f"#/settings?tab={tab}"}
        )
        assert "settings-intro" in page["dump"], f"the {tab} tab does not say what it is for"


@pytest.fixture
def no_league(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(league, "load", lambda: league.League(()))


@dom_test
async def test_with_no_model_the_fail_safe_is_a_fault_and_says_so(
    store: Store, no_league: None
) -> None:
    routes = await _routes(store)
    assert routes["/api/decider"]["json"]["fallback"] is True
    for where in ("#/settings", "#/promotion"):
        page = domdriver.run_scenario("render", {"routes": routes, "navigate": where})
        assert "No model could be loaded" in page["dump"], where
        assert re.search(r"<p \.err[^>]*>", page["dump"]), f"{where}: not shown as a fault"


@dom_test
async def test_labelling_shows_the_proposals_and_no_readiness_floor(
    store: Store, leagued: league.League
) -> None:
    routes = await _routes(store)
    page = domdriver.run_scenario("render", {"routes": routes, "navigate": "#/labelling"})
    dump = page["dump"]
    for text in (
        "Your judgements teach every model.",
        "Proposals waiting for an answer",
        "Groupings waiting for a verdict",
        "models compete",
    ):
        assert text in dump, text
    for gone in ("What a model needs before it can decide", "% ready", "make shadow-report"):
        assert gone not in dump, gone


async def _pending_routes(store: Store, *roles: str) -> dict[str, dict[str, Any]]:
    _engine, app = await uifixtures.corpus(store)
    async with store.lock:
        live = [r for r in await store.list_situations(None, 50) if r["status"] != "resolved"]
        target = int(live[0]["id"])
        await store.promote_situation(target, uifixtures.BASE + 20, "promote")
        proposal = await store.create_situation(uifixtures.BASE + 21, act="propose")
        await store.set_proposal(proposal, target, 0.93)
        members = await store.situation_members(target)
        await store.add_alarm_to_situation(proposal, int(members[0]["id"]))
        await store.commit()
    out = {}
    for role in roles:
        out[role] = await uifixtures.capture(app, role)
        out[role][f"POST /api/situations/{proposal}/proposal"] = {
            "status": 200,
            "json": {"status": "accepted", "into": target},
        }
    return out


@dom_test
async def test_a_pending_card_asks_and_only_an_editor_can_answer(
    store: Store, leagued: league.League
) -> None:
    routes = await _pending_routes(store, "editor", "viewer")
    # `charts` is the scenario that presses controls; the screen opens on the New tab.
    shown = {
        role: domdriver.run_scenario(
            "charts",
            {"routes": routes[role], "navigate": "#/situations", "click": ["#tab-pending"]},
        )["dump"]
        for role in routes
    }
    dump = shown["editor"]
    assert "Pending" in dump and "pending" in dump
    assert '"93%"' in dump and "Accept — add to" in dump and "Reject — keep separate" in dump
    assert "Accept — add to" not in shown["viewer"]
    assert "An editor can accept or reject it." in shown["viewer"]


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
    store: Store, leagued: league.League
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
async def test_no_kill_switch_while_autonomy_is_off(store: Store, leagued: league.League) -> None:
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
