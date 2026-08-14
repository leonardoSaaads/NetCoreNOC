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

from netcorenoc import audit, model_version, promotion
from netcorenoc.api import create_app
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
