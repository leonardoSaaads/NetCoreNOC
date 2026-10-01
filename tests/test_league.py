"""The model league (v0.27.0, ADRs #423-#427): what is promised about every member and the judge.

* **A member is data.** Each kind's document is refused for any structural fault before a scorer
  exists, its manifest's SHA-256 and declared kind are checked, and a refusal names no path.
* **Every explanation is exact.** A tree ensemble's Shapley values against its reference pair are
  checked against brute-force enumeration, and every kind's terms add up to its score.
* **The judge is the registered rule.** Offline order by mean pairwise F1 over five suites, ties by
  repair work, incomplete members last; the fast loop's latency budget; an admin's pin; and a
  switch on site labels **only** when the whole 95 % interval says so — with no count floor.
* **The packaged league passes all of the above**, and its manifests carry what the Judge screen
  charts.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import math
import random
import sys
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

from netcorenoc.engine.correlate.features import FEATURE_NAMES
from netcorenoc.engine.model import (
    gam,
    league,
    league_judge,
    linear,
    linear_fit,
    site,
    trees,
    trees_fit,
)
from netcorenoc.engine.model.gam_data import Dataset
from netcorenoc.engine.operate import league_loop
from netcorenoc.engine.operate.engine import Engine
from netcorenoc.store import Store

from modelutil import TEST_MODEL

PACKAGE = resources.files("netcorenoc.engine.model").joinpath(league.DIRECTORY)


def _manifest(document: str, kind: str, **extra: Any) -> str:
    sha = hashlib.sha256(document.encode()).hexdigest()
    return json.dumps({"artifact": {"sha256": sha, "kind": kind}, **extra})


def _data(n: int, seed: int) -> Dataset:
    rng = random.Random(seed)
    rows, ys = [], []
    for _ in range(n):
        x = [0.0] * len(FEATURE_NAMES)
        x[0] = rng.expovariate(1 / 30)  # dt
        x[1] = float(rng.random() < 0.5)  # same_ne
        x[2] = float(rng.random() < 0.3)  # same_class
        x[5] = rng.random()  # entity_affinity
        x[10] = float(rng.randint(1, 400))  # burst
        z = -1 + 2 * x[1] + 1.5 * x[2] - 0.05 * x[0] + 2 * x[5] * x[1] - 0.002 * x[10]
        ys.append(1 if rng.random() < 1 / (1 + math.exp(-z)) else 0)
        rows.append(x)
    return Dataset.from_rows(FEATURE_NAMES, rows, ys, [0.5] * n)


@pytest.fixture(scope="module")
def fitted() -> dict[str, str]:
    """One small document per kind, fitted by the real fitters (deterministic)."""
    train, valid = _data(3000, 1), _data(800, 2)
    out = {"gam": TEST_MODEL}
    for method, extra in (
        ("decision_tree", {"max_depth": 5, "min_leaf": 5.0}),
        ("random_forest", {"max_depth": 5, "min_leaf": 3.0, "trees": 8, "subsample": 0.5,
                           "colsample": 0.5}),
        ("boosted_trees", {"max_depth": 3, "min_leaf": 0.2, "trees": 40, "learning_rate": 0.2,
                           "subsample": 0.7}),
    ):
        out[method] = trees_fit.fit(train, valid, trees_fit.TreeParams(method, **extra)).document
    out["logistic_regression"] = linear_fit.fit(train, valid, linear_fit.LinearParams()).document
    return out


# -- a member is data ----------------------------------------------------------------------------


def test_every_kind_loads_and_its_terms_add_up_to_its_score(fitted: dict[str, str]) -> None:
    rng = random.Random(3)
    for kind, document in fitted.items():
        member = league.member_from(kind, document, _manifest(document, kind))
        assert member.ref.startswith(f"{kind}:") and member.name == league.KINDS[kind][0]
        for _ in range(40):
            v = tuple(rng.random() * (60 if i in (0, 10) else 1) for i in range(len(FEATURE_NAMES)))
            score = member.scorer.explain(v)
            total = score.base_value + sum(t.contribution for t in score.terms)
            assert abs(total - score.score) < 1e-9, kind
            assert abs(score.score - member.scorer.logit(v)) < 1e-9, kind
            assert score.linked == (score.score > score.threshold)


def test_a_tree_ensembles_attribution_is_the_exact_shapley_value(fitted: dict[str, str]) -> None:
    """Brute force over every coalition of the model's features, against the reference pair."""
    scorer = trees.load(fitted["random_forest"])
    used = sorted({t.local[i] for t in scorer.model.trees for i in range(len(t.feature))
                   if t.feature[i] != trees.LEAF})
    names = scorer.model.features
    ref = list(scorer._reference)
    rng = random.Random(5)
    for _ in range(10):
        x = tuple(rng.random() * (60 if i in (0, 10) else 1) for i in range(len(FEATURE_NAMES)))

        def hybrid(coalition: set[int]) -> float:
            v = list(ref)
            for k in coalition:
                v[FEATURE_NAMES.index(names[k])] = x[FEATURE_NAMES.index(names[k])]
            return scorer.logit(v)

        phi = scorer.attribution(x)
        n = len(used)
        for i in used:
            others = [j for j in used if j != i]
            exact = 0.0
            for size in range(n):
                weight = math.factorial(size) * math.factorial(n - size - 1) / math.factorial(n)
                for subset in itertools.combinations(others, size):
                    exact += weight * (hybrid(set(subset) | {i}) - hybrid(set(subset)))
            assert abs(exact - phi[i]) < 1e-9
        assert all(phi[k] == 0.0 for k in range(len(names)) if k not in used)


def _tree_doc(**changes: Any) -> str:
    doc: dict[str, Any] = {
        "format": trees.FORMAT,
        "method": "decision_tree",
        "features": ["dt", "same_ne"],
        "reference": [10.0, 0.0],
        "base": 0.0,
        "scale": 1.0,
        "trees": [[[1, 0.5, 1, 2, 0.0], [-1, 0.0, 0, 0, -2.0], [-1, 0.0, 0, 0, 2.0]]],
        "threshold": 0.0,
        "grouping": {"join_bias": 0.0, "merge_bias": 1.0, "merge_min_pairs": 3.0},
    }
    doc.update(changes)
    return json.dumps(doc)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"extra": 1}, "exactly the keys"),
        ({"features": ["dt", "device_ip"]}, "drawn from"),
        ({"trees": [[[1, 0.5, 0, 2, 0.0], [-1, 0.0, 0, 0, -2.0], [-1, 0, 0, 0, 2.0]]]}, "forward"),
        ({"trees": [[[1, 0.5, 1, 1, 0.0], [-1, 0.0, 0, 0, -2.0]]]}, "forward"),
        ({"trees": [[[1, 0.5, 2, 3, 0.0], [-1, 0, 0, 0, 1.0], [-1, 0, 0, 0, -1.0],
                     [-1, 0, 0, 0, 1.0]]]}, "one parent"),
        ({"trees": [[[5, 0.5, 1, 2, 0.0], [-1, 0, 0, 0, -2.0], [-1, 0, 0, 0, 2.0]]]}, "outside"),
        ({"trees": [[[1, 0.5, 1, 2, 0.0], [-1, 0, 0, 0, -99.0], [-1, 0, 0, 0, 2.0]]]}, "hard switch"),
        ({"threshold": 5.0}, "cannot discriminate"),
        ({"scale": 0.0}, "scale"),
        ({"method": "neural_net"}, "method"),
        ({"method": "random_forest", "trees": [[], []]}, "nodes"),
    ],
)
def test_a_malformed_trees_document_is_refused(change: dict[str, Any], reason: str) -> None:
    with pytest.raises(trees.TreesDocumentError, match=reason):
        trees.validate(_tree_doc(**change))
    assert trees.validate(_tree_doc()).method == "decision_tree"  # the control loads


def test_non_finite_numbers_and_oversize_documents_are_refused() -> None:
    with pytest.raises(trees.TreesDocumentError, match="non-finite"):
        trees.validate(_tree_doc().replace('"base": 0.0', '"base": NaN'))
    with pytest.raises(trees.TreesDocumentError, match="exceeds"):
        trees.validate(" " * (trees.MAX_DOCUMENT_BYTES + 1))
    lin = json.loads(linear_fit.fit(_data(500, 7), _data(200, 8), linear_fit.LinearParams()).document)
    lin["weights"][0] = 40.0
    with pytest.raises(linear.LinearDocumentError, match="hard switch"):
        linear.validate(json.dumps(lin))


def test_a_manifest_that_does_not_belong_to_its_document_is_refused(fitted: dict[str, str]) -> None:
    doc = fitted["decision_tree"]
    with pytest.raises(league.LeagueError, match="SHA-256"):
        league.member_from("decision_tree", doc, _manifest(doc + " ", "decision_tree"))
    with pytest.raises(league.LeagueError, match="describes"):
        league.member_from("decision_tree", doc, _manifest(doc, "random_forest"))
    with pytest.raises(league.LeagueError, match="not a random_forest"):
        league.member_from("random_forest", doc, _manifest(doc, "random_forest"))


def test_each_load_state_is_reported_alone_and_names_no_path(
    tmp_path: Path, fitted: dict[str, str]
) -> None:
    (tmp_path / "gam.json").write_text(fitted["gam"])
    (tmp_path / "gam.manifest.json").write_text(_manifest(fitted["gam"], "gam"))
    (tmp_path / "decision_tree.json").write_text(fitted["decision_tree"])  # no manifest
    bad = fitted["random_forest"].replace('"scale"', '"scale_"')
    (tmp_path / "random_forest.json").write_text(bad)
    (tmp_path / "random_forest.manifest.json").write_text(_manifest(bad, "random_forest"))
    found = league.load_dir(tmp_path)
    assert [m.kind for m in found.members] == ["gam"]
    refused = dict(found.refused)
    assert refused["decision_tree"] == "incomplete: the manifest is missing"
    assert "refused" in refused["random_forest"]
    assert all(str(tmp_path) not in reason for reason in refused.values())
    empty = tmp_path / "empty"
    empty.mkdir()
    assert league.load_dir(empty) == league.League(())


def test_the_benchmark_is_validated_as_data() -> None:
    width = 2 + len(FEATURE_NAMES)
    good = {"format": league.BENCHMARK_FORMAT, "rows": [[1, 0.5] + [0.0] * (width - 2)]}
    assert len(league.parse_benchmark(json.dumps(good))) == 1
    for broken in (
        {**good, "format": "x"},
        {**good, "rows": [[1, 0.5]]},
        {**good, "rows": [[1, 0.5] + ["os.system"] + [0.0] * (width - 3)]},
    ):
        with pytest.raises(league.LeagueError):
            league.parse_benchmark(json.dumps(broken))


# -- the judge ---------------------------------------------------------------------------------


def _member(kind: str, document: str, f1: dict[str, float], repair: float = 1.0) -> league.Member:
    splits = {
        s: {"model": {"pairwise_f1": {"point": v}, "repair_gestures": {"point": repair}}}
        for s, v in f1.items()
        if s != "corpus"
    }
    extra: dict[str, Any] = {"evaluation": {"splits": splits}}
    if "corpus" in f1:
        extra["corpus"] = {"aggregate": {"pairwise_f1": f1["corpus"]}}
    return league.member_from(kind, document, _manifest(document, kind, **extra))


def _suite(value: float) -> dict[str, float]:
    return {s: value for s in league_judge.SUITES}


def test_the_offline_order_is_the_registered_rule(fitted: dict[str, str]) -> None:
    a = _member("gam", fitted["gam"], _suite(0.90))
    b = _member("random_forest", fitted["random_forest"], _suite(0.95))
    c = _member("decision_tree", fitted["decision_tree"], _suite(0.952), repair=0.5)
    d = _member("boosted_trees", fitted["boosted_trees"], {"test_iid": 0.99})  # incomplete
    order = [r["kind"] for r in league_judge.offline_table(league.League((a, b, c, d)))]
    # b and c tie within TIE (0.005), and c needs fewer repairs; d is incomplete, so last.
    assert order == ["decision_tree", "random_forest", "gam", "boosted_trees"]
    choice = league_judge.choose(league.League((a, b, c, d)))
    assert choice is not None and choice.champion == c.ref
    assert "no site labels" in choice.reason


def test_a_member_over_the_latency_budget_cannot_be_champion(fitted: dict[str, str]) -> None:
    fast = _member("gam", fitted["gam"], _suite(0.80))
    slow = _member("random_forest", fitted["random_forest"], _suite(0.99))
    lat = {fast.ref: 5.0, slow.ref: league_judge.LATENCY_BUDGET_US + 1}
    choice = league_judge.choose(league.League((fast, slow)), latency_us=lat)
    assert choice is not None and choice.champion == fast.ref
    assert slow.ref in choice.ineligible and not choice.table[0]["eligible"]


def test_an_admin_pin_wins_and_the_table_still_ranks(fitted: dict[str, str]) -> None:
    a = _member("gam", fitted["gam"], _suite(0.80))
    b = _member("random_forest", fitted["random_forest"], _suite(0.99))
    choice = league_judge.choose(league.League((a, b)), pinned=a.ref)
    assert choice is not None and choice.champion == a.ref and choice.reason == "pinned by an admin"
    assert choice.table[0]["ref"] == b.ref


def _rows(n_incidents: int, better: league.Member, worse: league.Member) -> list[site.SiteRow]:
    """Labels on which ``better`` really is better: pairs it scores confidently right."""
    rng = random.Random(11)
    out = []
    for inc in range(n_incidents):
        for k in range(6):
            x = tuple(rng.random() * (60 if i in (0, 10) else 1) for i in range(len(FEATURE_NAMES)))
            y = 1 if better.scorer.logit(x) > 0 else 0
            out.append(site.SiteRow(x, y, 1.0, ("feedback", inc), inc, 1000.0 + inc * 100 + k))
    return out


def test_site_labels_switch_the_champion_only_when_the_whole_interval_says_so(
    fitted: dict[str, str],
) -> None:
    champion = _member("decision_tree", fitted["decision_tree"], _suite(0.99))
    challenger = _member("boosted_trees", fitted["boosted_trees"], _suite(0.80))
    both = league.League((champion, challenger))
    # Two incidents: a t-interval with one degree of freedom is 12.7 standard errors wide.
    few = _rows(2, challenger, champion)
    kept = league_judge.choose(both, rows=few, current=champion.ref)
    assert kept is not None and kept.champion == champion.ref
    assert kept.comparisons[0]["incidents"] == 2
    many = _rows(40, challenger, champion)
    moved = league_judge.choose(both, rows=many, current=champion.ref)
    assert moved is not None and moved.champion == challenger.ref, moved.comparisons
    c = moved.comparisons[0]
    assert c["verdict"] == "better" and c["high"] < 0
    assert "95 % interval" in moved.reason


def test_the_t_quantile_is_the_tables() -> None:
    assert league_judge.t_quantile(1) == 12.706
    assert league_judge.t_quantile(30) == 2.042
    assert abs(league_judge.t_quantile(1000) - 1.962) < 0.002
    assert league_judge.t_quantile(0) == math.inf


def test_a_site_member_is_compared_only_on_labels_newer_than_its_fit(
    fitted: dict[str, str],
) -> None:
    pre = _member("decision_tree", fitted["decision_tree"], _suite(0.9))
    adapted = league.site_member("gam", TEST_MODEL, version_id=7, created_at=2500.0)
    assert adapted.origin == "site" and adapted.ref.startswith("site-gam:")
    rows = _rows(40, adapted, pre)
    choice = league_judge.choose(league.League((pre, adapted)), rows=rows, current=pre.ref)
    assert choice is not None
    newer = [r for r in rows if r.label_at > 2500.0]
    assert choice.comparisons[0]["incidents"] == len({r.incident for r in newer}) < 40


# -- the two loops ------------------------------------------------------------------------------


async def test_the_slow_loop_records_the_champion_and_the_fast_loop_runs_it(
    store: Store, monkeypatch: pytest.MonkeyPatch, fitted: dict[str, str]
) -> None:
    a = _member("gam", fitted["gam"], _suite(0.80))
    b = _member("random_forest", fitted["random_forest"], _suite(0.95))
    monkeypatch.setattr(league, "load", lambda: league.League((a, b)))
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    assert engine.decider_ref == b.ref, "day 0: the first eligible member in the offline order"
    assert [m.ref for m in engine.challengers] == [a.ref]
    view = await league_loop.judge_step(engine, 1_800_000_000.0)
    assert view is not None and view["champion"] == b.ref
    decision = await store.latest_league_decision()
    assert decision is not None and decision["champion"] == b.ref and decision["actor"] == "judge"
    cur = await store.conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='league.decide'")
    assert (await cur.fetchone())[0] == 1
    # Unchanged: a second pass writes nothing.
    await league_loop.judge_step(engine, 1_800_000_300.0)
    assert len(await store.league_decisions(10)) == 1
    # An admin pins the other one; the fast loop runs it from the next reload point.
    async with store.lock:
        await store.set_decider_pin(a.ref, "admin", 1_800_000_400.0, "comparing by hand")
        await store.commit()
    await engine.load_scorer_config()
    assert engine.decider_ref == a.ref
    with pytest.raises(Exception, match="append-only"):
        async with store.lock:
            await store.conn.execute("DELETE FROM league_decision")


def test_the_live_shadow_is_bounded_and_never_raises() -> None:
    from netcorenoc.engine.correlate.pairs import CorrelationResult, EvaluatedPair
    from netcorenoc.engine.evaluation.league_shadow import SAMPLE_PAIRS, LeagueShadow

    class Broken:
        ref = "boom:000000000000"

        @property
        def scorer(self) -> Any:
            raise RuntimeError("a challenger that cannot score")

    vec = tuple(0.0 for _ in FEATURE_NAMES)
    pairs = [EvaluatedPair(object(), None, vec, 0.5, True) for _ in range(50)]  # type: ignore[arg-type]
    outcome = CorrelationResult(links=[], considered=[], storm=False, evaluated=pairs)
    good = league.member_from("gam", TEST_MODEL, _manifest(TEST_MODEL, "gam"))
    shadow = LeagueShadow()
    shadow.reset("champion")
    shadow.observe(1, outcome, [good, Broken()], burst=1)  # type: ignore[list-item]
    snap = shadow.snapshot()
    assert snap["challengers"][good.ref]["pairs"] == SAMPLE_PAIRS
    assert snap["errors"] == 1
    shadow.observe(3, outcome, [good], burst=10_000)  # a storm: one activation in eight
    assert snap["sampled"] == 1 and shadow.snapshot()["sampled"] == 1


# -- the packaged league -------------------------------------------------------------------------


def test_the_package_carries_a_full_league_and_every_member_is_whole() -> None:
    packaged = league.load_dir(PACKAGE)
    assert packaged.refused == ()
    assert sorted(m.kind for m in packaged.members) == sorted(league.KINDS)
    bench = league.parse_benchmark(PACKAGE.joinpath(league.BENCHMARK).read_text("utf-8"))
    bench_sha = hashlib.sha256(PACKAGE.joinpath(league.BENCHMARK).read_bytes()).hexdigest()
    from synth import dataset, train

    held_out = set(dataset.HOLDOUT_OPTICAL + dataset.HOLDOUT_PROTOCOL)
    for member in packaged.members:
        m = member.manifest
        assert m["benchmark_sha256"] == bench_sha
        assert set(m["artifact"]["features"]) <= set(FEATURE_NAMES)
        assert not [f for f in m["artifact"]["features"] if "incumbent" in f]
        prov = m["provenance"]
        assert prov["data"].startswith("generated") and not held_out & set(prov["training_families"])
        assert set(m["evaluation"]["held_out_families"]) == held_out
        assert [(c["quantity"], c["scope"], c["kind"], c["limit"]) for c in m["quality_bar"]] == list(
            train.QUALITY_BAR
        )
        from synth import report

        assert report.check_bar(m["evaluation"], train.QUALITY_BAR) == m["scorecard"]
        assert set(league_judge.suites(member)) == set(league_judge.SUITES), member.kind
        pairs = m["evaluation"]["pairs"]["test_iid"]
        for key in ("roc_curve", "pr_curve", "calibration", "confusion", "residuals"):
            assert pairs[key], (member.kind, key)
        assert m["search"]["trials"] and m["final_fit"]["valid_trace"]
    assert len(bench) > 100
    assert isinstance(gam.KIND, str)


async def test_a_search_is_refused_at_the_request_when_there_is_no_gam_to_adapt(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The site search adapts the league's GAM; without one the admin is told why at once, and no
    run is opened for the runner to refuse a tick later. With one, the same request opens a run."""
    import authutil

    monkeypatch.setattr(league, "load", lambda: league.League(()))
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        body = {"trials": 2, "max_rounds": 20, "minutes": 1}
        refused = await admin.post("/api/search", json=body)
        assert refused.status_code == 409, refused.text
        assert "no GAM to adapt" in refused.json()["detail"]
        assert await store.search_runs(1) == [], "a run was opened anyway"
        member = league.member_from("gam", TEST_MODEL, _manifest(TEST_MODEL, "gam"))
        monkeypatch.setattr(league, "load", lambda: league.League((member,)))
        assert (await admin.post("/api/search", json=body)).status_code == 200
    finally:
        await admin.aclose()


def test_differences_compares_exactly_and_treats_nan_as_nan() -> None:
    from synth.verify import differences

    assert differences({"a": [1.0, float("nan")]}, {"a": [1.0, float("nan")]}) == []
    assert differences({"a": 0.5}, {"a": 0.5000001}) == ["/a: published 0.5, measured 0.5000001"]
    assert differences({"a": 1}, {"b": 1}) == [
        "/a: present on one side only",
        "/b: present on one side only",
    ]
