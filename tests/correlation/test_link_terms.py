"""A model's link is explained on the wire, and its terms sum to its score (v0.27.0).

`store/link_terms.py` serves the `link.terms` column that `0026` added and no route read: before
it, a link a trained model decided reached the console as the formula's three columns, which did
not sum to its score — principle 2 broken on exactly the links the league now decides.
"""

from __future__ import annotations

import json
from typing import Any

from netcorenoc.store import Store, link_terms

import authutil
import domdriver
import uifixtures
import util
from domdriver import dom_test


def _stored(base: float, terms: list[tuple[str, float, float]], threshold: float = 0.0) -> str:
    return json.dumps(
        {
            "basis": "shapley",
            "base": base,
            "threshold": threshold,
            "terms": [list(t) for t in terms],
        }
    )


def test_names_travel_once_and_each_link_indexes_them() -> None:
    a: dict[str, Any] = {
        "score": 1.5,
        "terms": _stored(0.5, [("dt", 3.0, 0.75), ("same_ne", 1.0, 0.25)]),
    }
    b: dict[str, Any] = {
        "score": 0.5,
        "terms": _stored(0.5, [("dt", 9.0, -0.25), ("same_ne", 1.0, 0.25)]),
    }
    formula: dict[str, Any] = {"score": 0.7, "terms": None, "term_t": 0.3, "term_a": 0.2}
    table = link_terms.compact([a, b, formula])
    assert table == [["dt", "same_ne"]]
    assert a["names"] == b["names"] == 0 and "terms" not in a and "terms" not in formula
    for link in (a, b):
        assert abs(link["base"] + sum(link["phi"]) - link["score"]) < 1e-9
    assert "phi" not in formula, "the formula's link keeps its three columns"


def test_an_unreadable_explanation_is_dropped_never_guessed() -> None:
    bad = [{"score": 1.0, "terms": "{not json"}, {"score": 1.0, "terms": '{"basis": "x"}'}]
    assert link_terms.compact(bad) == []
    assert all("phi" not in link and "terms" not in link for link in bad)
    nan = {"score": 1.0, "terms": _stored(float("nan"), [("dt", 1.0, 1.0)])}
    assert link_terms.compact([nan]) == [] and "phi" not in nan


def test_the_threshold_is_the_deciders_and_mixed_is_not_reported() -> None:
    model = {"phi": [0.1], "threshold": 0.0}
    formula = {"term_t": 0.5}
    assert link_terms.threshold_of([model, dict(model)], 0.5) == 0.0
    assert link_terms.threshold_of([formula], 0.5) == 0.5
    assert link_terms.threshold_of([model, formula], 0.5) is None
    assert link_terms.scale_of([model]) == "logit"
    assert link_terms.scale_of([model, formula]) == "additive"
    assert link_terms.scale_of([]) == "additive"


async def test_a_model_decided_situation_explains_every_link_over_http(
    store: Store, test_model: object
) -> None:
    """End to end on the in-process app: the test GAM decides `fiber_cut`, and every link the
    detail route serves carries names and contributions that, with the base, are its score."""
    engine, queue, app = await authutil.make_env(store)
    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", 1_700_000_000.0))
    client = await authutil.client_as(app, "viewer")
    try:
        sits = (await client.get("/api/situations?limit=50")).json()
        checked = 0
        for sit in sits:
            detail: dict[str, Any] = (await client.get(f"/api/situations/{sit['id']}")).json()
            for link in detail["links"]:
                assert "phi" in link, "a model decided this link and the wire does not explain it"
                names = detail["link_terms"][link["names"]]
                assert len(names) == len(link["phi"])
                total = link["base"] + sum(link["phi"])
                assert abs(total - link["score"]) < 1e-5, (total, link)
                checked += 1
            if detail["links"]:
                assert detail["score_scale"] == "logit"
                assert detail["threshold"] == link["threshold"]
        assert checked, "no link was served, so nothing was checked"
    finally:
        await client.aclose()


@dom_test
async def test_the_console_names_a_models_terms_and_reads_its_margin_as_probability(
    store: Store, test_model: object
) -> None:
    """The "why grouped" section on a situation the test GAM decided: its terms are the model's
    features by name — not the formula's T/A/E reading zero — and the margin over the model's
    threshold is stated in probability, since a model's scores are log-odds."""
    _engine, app = await uifixtures.corpus(store)
    routes = await uifixtures.capture(app, "editor")
    sid, count = uifixtures.largest_situation(routes)
    assert count >= 2, "the corpus must offer a situation with a link"
    result = domdriver.run_scenario("whyGrouped", {"routes": routes, "sid": sid})
    opened = result["opened"]
    assert opened["rowCount"] >= 1
    assert any("how close in time" in m or "same network element" in m for m in opened["means"]), (
        opened["means"]
    )
    assert not any(m.startswith("T — ") for m in opened["means"]), (
        "a model's link is shown as the formula's three terms"
    )
    assert "in probability above the threshold" in opened["summaryText"], opened["summaryText"]
