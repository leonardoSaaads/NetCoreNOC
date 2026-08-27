"""The promotion HTTP surface (v0.11.0, Phase 5, S1 to S5).

Four claims, and the refused ones come first because **on the real corpus they are the only paths an
operator will ever meet**:

* **S5** — a refused promotion **leaves a row**, with its reason.
* **S1** — a request asserting a verdict is ignored; the server's stands.
* **S4** — the applied path moves the pointer and writes `before`/`after`.
* the privilege boundary — admin-only to propose, viewer+ to read, no editor delegation.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from netcorenoc.api import create_app
from netcorenoc.crosscutting import audit
from netcorenoc.engine.dataset import seal
from netcorenoc.engine.evaluation import promotion
from netcorenoc.engine.evaluation.shadow_cv import Interval, power_at
from netcorenoc.engine.model import challenger, model_version
from netcorenoc.main import Engine
from netcorenoc.store import Store

import authutil

BASE = 1_700_000_000.0
PLAN = promotion.RATIFIED_PLAN_SHA256

FITTED = {
    "intercept": -1.5,
    "decay": 2.0,
    "class_affinity": 1.2,
    "entity_affinity": 0.8,
    "threshold": 0.0,
}


async def _env(store: Store) -> tuple[Engine, object]:
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    await authutil.make_users(store)
    app = create_app(engine, rate_capacity=100000.0, preview_rate_capacity=100000.0)
    return engine, app


async def _register(store: Store, *, with_run: bool = True) -> int:
    """Register a fitted logistic artefact and return its id."""
    document = model_version.canonical_document(FITTED)
    async with store.lock:
        run_id = None
        if with_run:
            run_id = await store.open_challenger_run(
                started_at=BASE,
                netcorenoc_version="0.11.0",
                scorer_id="logistic-shadow",
                contract_version="1.0",
                params_fingerprint="f" * 64,
                derivation_policy="A",
                thresholds="{}",
                sufficient=1,
            )
        model_version_id = await store.insert_model_version(
            kind=model_version.KIND_LOGISTIC,
            contract_version="1.0",
            params_document=document,
            params_hash=model_version.params_hash(model_version.KIND_LOGISTIC, "1.0", document),
            challenger_run_id=run_id,
            created_by="admin",
            created_at=BASE,
        )
        await store.commit()
    return model_version_id


async def _document_for(kind: str) -> str:
    """A **valid** parameter document for one kind, built the way the product builds one.

    The three tree kinds are fitted by their own `fit_document`, not hand-written: a hand-written
    tree would be a second definition of the format and the two would drift. `modelfixtures` is the
    same row generator `tests/test_tree_kinds.py` fits against, so the documents here are the ones
    that file already exercises in depth.
    """
    import modelfixtures
    from netcorenoc.engine.model import boosting, forest, tree

    if kind == model_version.KIND_ADDITIVE:
        return model_version.canonical_document(
            {"w_t": 0.30, "w_a": 0.35, "w_e": 0.35, "tau_s": 30.0, "threshold": 0.50}
        )
    if kind == model_version.KIND_LOGISTIC:
        return model_version.canonical_document(FITTED)
    rows = modelfixtures.training_rows()
    fitters = {
        model_version.KIND_TREE: lambda: tree.fit_document(
            rows, max_depth=3, min_samples_leaf=20, criterion="gini", threshold=0.5
        ),
        model_version.KIND_FOREST: lambda: forest.fit_document(
            rows, n_estimators=3, max_depth=3, min_samples_leaf=20, mtry=2, seed=7, threshold=0.5
        ),
        model_version.KIND_GRADIENT_BOOSTING: lambda: boosting.fit_document(
            rows, n_rounds=3, learning_rate=0.2, max_depth=2, min_samples_leaf=20, threshold=0.5
        ),
    }
    return model_version.canonical_object(await fitters[kind]())


# -- S5: THE REFUSED PATH, first ------------------------------------------------------------


async def test_a_refused_promotion_leaves_a_row_with_its_reason(store: Store) -> None:
    """**The row is the deliverable.** A table of successes answers *"what is deployed"*; this
    table answers *"what has this appliance been asked to deploy, and why was it refused"*, and the
    second is the audit question."""
    _engine, app = await _env(store)
    model_version_id = await _register(store)
    client = await authutil.client_as(app, "admin")

    response = await client.post("/api/promotion", json={"model_version_id": model_version_id})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "refused"
    assert body["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert "FLOOR_UNMET" in body["triggers"]

    rows = await store.list_promotions(10)
    assert len(rows) == 1, "the refusal wrote no row"
    row = rows[0]
    assert row["outcome"] == "refused"
    assert row["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert row["refusal_reason"], "the refusal recorded no reason"
    assert "INSUFFICIENT_EVIDENCE" in str(row["refusal_reason"])
    assert row["approved_by"] == "adm"
    assert row["plan_sha256"] == PLAN, "the ratified plan in force is not recorded"
    assert row["query_count"] == 0
    assert row["evaluation_run_id"], "the fold assignment reference is missing"
    assert json.loads(str(row["triggers"])), "the triggers were summarised away"
    assert json.loads(str(row["unavailable"])), "nothing was recorded as unavailable"
    await client.aclose()


async def test_the_refused_path_writes_an_audit_row(store: Store) -> None:
    """The refusal is in the hash chain too, not only in its own table."""
    _engine, app = await _env(store)
    model_version_id = await _register(store)
    client = await authutil.client_as(app, "admin")
    await client.post("/api/promotion", json={"model_version_id": model_version_id})
    await client.aclose()

    rows = [r for r in await store.audit_all() if str(r["action"]).startswith("promotion.")]
    assert len(rows) == 1, "the refused path wrote no audit row"
    assert rows[0]["action"] == "promotion.refused"
    details = json.loads(str(rows[0]["details"]))
    assert details["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert details["after"] is None, "a refusal recorded an `after` state"
    assert "FLOOR_UNMET" in details["triggers"]
    assert (await audit.verify_chain(store)).ok


async def test_a_refusal_does_not_move_the_active_pointer(store: Store) -> None:
    engine, app = await _env(store)
    model_version_id = await _register(store)
    client = await authutil.client_as(app, "admin")
    await client.post("/api/promotion", json={"model_version_id": model_version_id})
    await client.aclose()

    cur = await store.conn.execute("SELECT config_id, model_version_id FROM scorer_active")
    assert tuple(await cur.fetchone()) == (1, None)  # type: ignore[arg-type]
    await engine.load_scorer_config()
    assert engine.scorer_model_version_id is None


# -- S1: the verdict is the server's ---------------------------------------------------------


async def test_a_client_asserted_verdict_is_ignored_and_the_servers_stands(store: Store) -> None:
    """**The request may name a candidate; it may not assert a verdict.**

    The enforcement is that `PromotionIn` has no such field — so the assertion is dropped before any
    handler sees it. Asserted behaviourally here, and structurally by the test below.
    """
    _engine, app = await _env(store)
    model_version_id = await _register(store)
    client = await authutil.client_as(app, "admin")

    response = await client.post(
        "/api/promotion",
        json={
            "model_version_id": model_version_id,
            "verdict": "BETTER",
            "floors_met": True,
            "metrics": {"over_merge_rate": 0.0},
            "query_count": 99,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verdict"] == "INSUFFICIENT_EVIDENCE", "the client's verdict was believed"
    assert body["status"] == "refused"
    assert body["seal_query_count"] == 0, "the client's query count was believed"

    rows = await store.list_promotions(10)
    assert rows[0]["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert rows[0]["query_count"] == 0
    await client.aclose()


def test_the_request_model_has_no_field_that_could_assert_evidence() -> None:
    """Structural, because a behavioural test only proves the field is ignored *today*."""
    from netcorenoc.api.models import PromotionIn

    fields = set(PromotionIn.model_fields)
    assert fields == {"model_version_id", "note"}, fields
    for banned in ("verdict", "metrics", "floors_met", "query_count", "triggers", "outcome"):
        assert banned not in fields, f"PromotionIn grew a field that asserts evidence: {banned}"


# -- S4: the applied path, on a purpose-built sufficient fixture ------------------------------


# The APPLIED path is exercised end-to-end in `test_promotion_gate.py`
# (`test_a_sufficient_fixture_produces_a_real_better_verdict`) and its store effects in
# `test_promotion_applied.py`. It is deliberately NOT forced here by patching the route's derivation
# helpers: a fixture that reached `BETTER` by monkeypatching three module attributes would be
# testing the patches, and the one property this file exists to prove is that **nothing a client
# sends can reach that verdict**.


# -- the privilege boundary --------------------------------------------------------------------


@pytest.mark.parametrize("role", ["viewer", "editor"])
async def test_proposing_a_promotion_is_admin_only_with_no_editor_delegation(
    store: Store, role: str
) -> None:
    """Swapping the correlator is a system-wide logic change, exactly as retuning it is."""
    _engine, app = await _env(store)
    model_version_id = await _register(store)
    client = await authutil.client_as(app, role)
    response = await client.post("/api/promotion", json={"model_version_id": model_version_id})
    assert response.status_code == 403, f"{role} could propose a promotion"
    await client.aclose()
    assert await store.list_promotions(10) == [], "a denied request still wrote a row"


@pytest.mark.parametrize("role", ["viewer", "editor", "admin"])
async def test_reading_what_was_refused_is_viewer_plus(store: Store, role: str) -> None:
    """A refusal **explains** why the correlator is still what it is, and names no NE."""
    _engine, app = await _env(store)
    client = await authutil.client_as(app, role)
    response = await client.get("/api/promotion")
    assert response.status_code == 200
    body = response.json()
    assert body["seal_query_count"] == 0
    assert body["plan_sha256"] == PLAN
    await client.aclose()


async def test_an_unknown_model_version_is_404_and_writes_no_row(store: Store) -> None:
    _engine, app = await _env(store)
    client = await authutil.client_as(app, "admin")
    response = await client.post("/api/promotion", json={"model_version_id": 9999})
    assert response.status_code == 404
    await client.aclose()
    assert await store.list_promotions(10) == []


async def test_the_seal_is_not_read_by_a_refusal_on_the_real_floors(store: Store) -> None:
    """**The release's headline number**: the query count stays 0 because the floors fail first."""
    _engine, app = await _env(store)
    model_version_id = await _register(store)
    client = await authutil.client_as(app, "admin")
    await client.post("/api/promotion", json={"model_version_id": model_version_id})
    await client.aclose()
    assert await store.query_count() == 0


# -- S4: the applied path, end to end through the route ---------------------------------------


async def _sufficient_corpus(store: Store) -> None:
    """**Sixty asserting bags over sixty incidents — real rows, not patched floors.**

    The earlier version of this fixture lowered `ASSERTING_BAGS_FLOOR` to 0. That reached the
    applied path by removing the floor rather than by clearing it, and it left `smallest_side` at a
    derived 0 so `THIN_SPLIT` fired anyway — which is how the missing `smallest_side` derivation in
    the route was found. Seeding evidence that genuinely clears the registered floors exercises the
    same arithmetic an operator would.
    """
    async with store.lock:
        for _ in range(60):
            sid = await store.create_situation(BASE, None)
            await store.conn.execute(
                "INSERT INTO feedback (situation_id, verdict, created_at, principal_ref, "
                "coverage, member_count, excluded_reconciled, scope_redacted_members, "
                "capture_provenance) VALUES (?, 'split', ?, 'alice', 'full', 8, 2, 0, 'current')",
                (sid, BASE),
            )
        await store.commit()


async def test_the_applied_path_moves_the_pointer_and_records_before_and_after(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The one path this corpus can never reach, exercised where it can be.**

    Forced by lowering the **server's own floors** and widening the **server's own** power
    calculation — never by anything the request carries. That distinction is the point: the only way
    to reach `BETTER` is to change what the server measures, and a fixture that got here by sending
    a field would be evidence the boundary had been breached rather than that the path works.
    """
    await _sufficient_corpus(store)
    from netcorenoc.api import routes_promotion

    # Patched on the ROUTE's binding, deliberately: that is the one the handler calls, and the
    # substitute still goes through the real `power_at` — only its `n` and observed difference are
    # replaced, so the power arithmetic under test is the shipped arithmetic.
    monkeypatch.setattr(routes_promotion, "power_at", lambda _n, _d, p=0.70: power_at(400, 0.60, p))

    good = Interval(0.10, 0.08, 0.12, 40)
    bad = Interval(0.30, 0.28, 0.32, 40)
    monkeypatch.setattr(
        promotion.Metrics,
        "by_name",
        lambda self, name: promotion.Quantity(
            name,
            Interval(0.90, 0.88, 0.92, 40) if "respected" in name else good,
            Interval(0.50, 0.48, 0.52, 40) if "respected" in name else bad,
        ),
    )

    engine, app = await _env(store)
    async with store.lock:
        await store.construct_seal(
            release="0.11.0",
            plan_sha256=PLAN,
            created_at=BASE,
            digest="d" * 64,
            incident_ids=[1, 2, 3],
            corpus_incidents=9,
        )
        await seal.ratify(store, release="0.11.0", plan_sha256=PLAN, now=BASE)
        await store.commit()
    model_version_id = await _register(store)

    client = await authutil.client_as(app, "admin")
    response = await client.post("/api/promotion", json={"model_version_id": model_version_id})
    await client.aclose()
    body = response.json()
    assert body["status"] == "applied", body
    assert body["verdict"] == "BETTER"
    assert body["reason"] is None, "an applied promotion carried a refusal reason"

    # The pointer moved, and EXACTLY ONE side is set.
    cur = await store.conn.execute("SELECT config_id, model_version_id FROM scorer_active")
    assert tuple(await cur.fetchone()) == (None, model_version_id)  # type: ignore[arg-type]

    # The engine picks it up at its reload point and runs the promoted scorer.
    await engine.load_scorer_config()
    assert engine.scorer_model_version_id == model_version_id
    assert engine.scorer_config_id is None
    assert isinstance(engine.correlator.scorer.active, challenger.LogisticScorer)

    # The row, and the audit row with before/after.
    row = (await store.list_promotions(10))[0]
    assert row["outcome"] == "applied"
    assert row["refusal_reason"] is None
    assert row["query_count"] == 1, "the seal was not spent when the gate reached it"

    entry = next(r for r in await store.audit_all() if r["action"] == "promotion.applied")
    details = json.loads(str(entry["details"]))
    assert details["before"]["config_id"] == 1
    assert details["after"]["model_version_id"] == model_version_id
    assert (await audit.verify_chain(store)).ok


async def test_a_better_verdict_with_no_challenger_run_is_still_refused(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The third refusal, reached through the route: the evidence permits it and the artefact
    cannot be traced. **`registering is not promoting`, enforced.**"""
    await _sufficient_corpus(store)
    from netcorenoc.api import routes_promotion

    # Patched on the ROUTE's binding, deliberately: that is the one the handler calls, and the
    # substitute still goes through the real `power_at` — only its `n` and observed difference are
    # replaced, so the power arithmetic under test is the shipped arithmetic.
    monkeypatch.setattr(routes_promotion, "power_at", lambda _n, _d, p=0.70: power_at(400, 0.60, p))
    good = Interval(0.10, 0.08, 0.12, 40)
    bad = Interval(0.30, 0.28, 0.32, 40)
    monkeypatch.setattr(
        promotion.Metrics,
        "by_name",
        lambda self, name: promotion.Quantity(
            name,
            Interval(0.90, 0.88, 0.92, 40) if "respected" in name else good,
            Interval(0.50, 0.48, 0.52, 40) if "respected" in name else bad,
        ),
    )

    _engine, app = await _env(store)
    async with store.lock:
        await store.construct_seal(
            release="0.11.0",
            plan_sha256=PLAN,
            created_at=BASE,
            digest="d" * 64,
            incident_ids=[1, 2, 3],
            corpus_incidents=9,
        )
        await seal.ratify(store, release="0.11.0", plan_sha256=PLAN, now=BASE)
        await store.commit()
    model_version_id = await _register(store, with_run=False)

    client = await authutil.client_as(app, "admin")
    response = await client.post("/api/promotion", json={"model_version_id": model_version_id})
    await client.aclose()
    body = response.json()

    assert body["status"] == "refused"
    assert body["verdict"] == "BETTER", "the verdict itself was sufficient; the provenance was not"
    assert "no challenger run" in body["reason"]

    cur = await store.conn.execute("SELECT config_id, model_version_id FROM scorer_active")
    assert tuple(await cur.fetchone()) == (1, None)  # type: ignore[arg-type]
    row = (await store.list_promotions(10))[0]
    assert row["outcome"] == "refused"
    assert "retune with a better story" in str(row["refusal_reason"])


# -- F59: the arm measured is the model that would be activated (v0.14.0, Phase 7) ---------------


async def _labelled_pairs(store: Store, engine: Engine) -> None:
    """A bag with **promoted pairs under it**, so the gate has something to score.

    Without this the test below proves nothing: `promotion_metrics.measure` returns early on a
    corpus with no promoted pairs — recording the four quantities as absent rather than as zero,
    which is correct — and never touches either arm's scorer. A sentinel that is never called is a
    sentinel that always passes, so the assertion that the pairs were there is part of the test.
    """
    async with store.lock:
        sid = await store.create_situation(BASE, None)
        # Raw SQL, as `_sufficient_corpus` above uses, and for the same reason: `add_feedback`
        # leaves `capture_provenance` NULL, and `store.labelled_pairs` filters on `'current'` —
        # v0.7.5's repair, which makes a pre-repair label unusable rather than merely old.
        await store.conn.execute(
            "INSERT INTO feedback (situation_id, verdict, created_at, principal_ref, coverage, "
            "member_count, excluded_reconciled, scope_redacted_members, capture_provenance) "
            "VALUES (?, 'split', ?, 'alice', 'full', 6, 2, 0, 'current')",
            (sid, BASE),
        )
        for index, (delta, affinity, entity) in enumerate(
            ((1.0, 0.9, 0.9), (25.0, 0.1, 0.1), (5.0, 0.6, 0.2))
        ):
            await store.conn.execute(
                "INSERT INTO dataset_pair (capture_run_id, alarm_a, alarm_b, situation_id, "
                "delta_t_s, class_affinity, entity_affinity, a_epoch, e_epoch, score, "
                "incumbent_linked, storm, truncated, evaluated_at, lifecycle, promoted_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, 0.9, ?, 0, 0, ?, 'dataset', ?)",
                (
                    engine.capture.run_id,
                    1 + index * 2,
                    2 + index * 2,
                    sid,
                    delta,
                    affinity,
                    entity,
                    1 if index == 0 else 0,
                    BASE,
                    BASE,
                ),
            )
        await store.commit()


async def test_the_gate_measures_the_candidate_and_not_whatever_is_in_shadow(
    store: Store,
) -> None:
    """**F59.** v0.11.0 scored `engine.shadow.scorer` and activated `body.model_version_id`.

    Nothing bound them: no check that the candidate's parameters were the shadow scorer's, and
    `params_hash` reaches `promotion.evaluate` only as a fold-materialisation key. A promotion could
    therefore be applied on metrics measured against a *different model* — which re-opens by the
    back door the door `PromotionIn` closes at the front, and does it in the worse direction,
    because a verdict about a model nobody measured looks derived.

    Asserted by observation rather than by reading the call: the shadow scorer is replaced with one
    that would raise if it were ever asked to score, and the proposal is expected to succeed. A
    sentinel is used instead of a spy because a spy would pass on a call that merely *reached* the
    right object; only an exception proves the wrong one was never touched.
    """
    engine, app = await _env(store)
    await _labelled_pairs(store, engine)
    model_version_id = await _register(store)

    class Detonator:
        """A `LinkScorer` that fails the moment it is scored. Never the candidate."""

        scorer_id = "detonator"
        contract_version = "1.0"

        def score(self, features: object) -> object:  # pragma: no cover - must never run
            raise AssertionError("the gate scored the SHADOW arm, not the candidate (F59)")

        def params_fingerprint(self) -> str:
            return "detonator"

    engine.shadow.scorer = Detonator()  # type: ignore[assignment]
    client = await authutil.client_as(app, "admin")
    response = await client.post("/api/promotion", json={"model_version_id": model_version_id})
    assert response.status_code == 200, response.text
    assert response.json()["verdict"] == "INSUFFICIENT_EVIDENCE"
    body = response.json()
    assert not any("no promoted pairs" in note for note in body["unavailable"]), (
        "the corpus supplied no pairs, so nothing was scored and this test proves nothing"
    )
    await client.aclose()


async def test_a_candidate_whose_document_will_not_load_is_refused_not_substituted(
    store: Store,
) -> None:
    """A model that cannot be built cannot be promoted, and the gate is where that is discovered.

    The alternative — scoring *something else* and returning a verdict — is the defect above wearing
    an error-handling hat. 400 rather than 500: the request named a row this appliance cannot build,
    which is the client's problem to see and the server's to name.

    The row is inserted through the store directly, because `netcorenoc promotion register` runs
    `validate_document` and would refuse it. That is the point: the CLI is a gate, not the only
    writer, and a row that predates a contract change can reach this code path.
    """
    async with store.lock:
        broken = await store.insert_model_version(
            kind=model_version.KIND_TREE,
            contract_version="1.0",
            params_document='{"nodes": "not a list of nodes"}',
            params_hash="0" * 64,
            challenger_run_id=None,
            created_by="admin",
            created_at=BASE,
        )
        await store.commit()
    _engine, app = await _env(store)
    client = await authutil.client_as(app, "admin")
    response = await client.post("/api/promotion", json={"model_version_id": broken})
    assert response.status_code == 400, response.text
    assert "will not load" in response.json()["detail"]
    assert not await store.list_promotions(10), "a candidate that cannot be built wrote a row"
    await client.aclose()


async def test_every_supported_kind_can_be_scored_by_the_gate(store: Store) -> None:
    """The five kinds reach the gate through **one** dispatch, `model_version.scorer_for`.

    Part of why F59 survived three releases is that before v0.14.0 the gate could not have loaded a
    `tree` candidate at all — `scorer_for` covered two kinds — so the shadow scorer was the only
    thing there was to measure. This asserts the coverage the repair depends on, over
    `SUPPORTED_KINDS` rather than over a list written here, so a sixth kind added without a branch
    fails rather than silently falling through.
    """
    from netcorenoc.api.routes_promotion import _candidate_scorer

    assert len(model_version.SUPPORTED_KINDS) == 5
    for kind in sorted(model_version.SUPPORTED_KINDS):
        document = await _document_for(kind)
        scorer = _candidate_scorer(
            {"kind": kind, "contract_version": "1.0", "params_document": document}
        )
        assert scorer.params_fingerprint(), f"{kind} built a scorer with no fingerprint"
