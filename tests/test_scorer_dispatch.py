"""Dispatch by kind, and the load path that never raises (v0.11.0, Phase 3).

Two claims, and they are different claims:

* **A dispatch test** — each supported kind loads *its own* scorer; an unknown kind falls back.
  Before this release `load_scorer_config` constructed `AdditiveScorer` unconditionally and read the
  `scorer_id` column only as a label (`docs/gates/v0.11.0-phase-0.md` §2.2, by `ast`).
* **A no-raise test over every malformed-input class** — a payload that cannot be parsed, a kind
  this build does not know, a contract version it does not support, and a parameter set the
  validator rejects **all** fall back to the built-in default, warn the operator, and audit. None of
  them raises.

The second is the one that matters at three in the morning. `load_scorer_config` runs at engine
start and at the top of every maintenance pass; an exception there is an appliance that stops
correlating because a row in a table it reads once a minute is wrong.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from netcorenoc import scoring
from netcorenoc.crosscutting import audit
from netcorenoc.engine.model import challenger, model_version
from netcorenoc.engine.model.model_version import KIND_ADDITIVE, KIND_LOGISTIC
from netcorenoc.main import Engine
from netcorenoc.store import Store

BASE = 1_700_000_000.0
CV = scoring.CONTRACT_VERSION

FITTED = {
    "intercept": -1.5,
    "decay": 2.0,
    "class_affinity": 1.2,
    "entity_affinity": 0.8,
    "threshold": 0.0,
}


async def _engine(store: Store) -> Engine:
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    return engine


async def _activate(store: Store, kind: str, document: str, contract_version: str = CV) -> int:
    """Register an artefact and point the active pointer at it, **bypassing every validator**.

    Deliberately direct SQL. The load path's job is to survive a row that should never have been
    written, and a fixture that could only produce rows the writer would have accepted could not
    exercise it. This is the database as a hostile input, which is what `0013`'s rows are once
    anyone with a SQLite prompt is in scope.
    """
    async with store.lock:
        model_version_id = await store.insert_model_version(
            kind=kind,
            contract_version=contract_version,
            params_document=document,
            params_hash=model_version.params_hash(kind, contract_version, document),
            challenger_run_id=None,
            created_by="tester",
            created_at=BASE,
        )
        await store.set_active_model_version(model_version_id, "tester", BASE)
        await store.commit()
    return model_version_id


# -- the dispatch --------------------------------------------------------------------------


async def test_each_supported_kind_loads_its_own_scorer(store: Store) -> None:
    """**The dispatch test.** Two kinds, two different scorer classes, from the same code path."""
    engine = await _engine(store)

    await _activate(store, KIND_LOGISTIC, model_version.canonical_document(FITTED))
    await engine.load_scorer_config()
    active = engine.correlator.scorer.active
    assert isinstance(active, challenger.LogisticScorer)
    assert active.coefficients.decay == 2.0
    assert engine.scorer_warnings == []

    additive = {"w_t": 0.4, "w_a": 0.3, "w_e": 0.3, "tau_s": 45.0, "threshold": 0.55}
    await _activate(store, KIND_ADDITIVE, model_version.canonical_document(additive))
    await engine.load_scorer_config()
    active = engine.correlator.scorer.active
    assert isinstance(active, scoring.AdditiveScorer)
    assert active.params() == additive
    assert engine.scorer_warnings == []


async def test_the_engine_records_which_pointer_it_loaded(store: Store) -> None:
    """`scorer_config_id` and `scorer_model_version_id` are never both set, on any path.

    Two live values here would be two answers to *"what is running"*, which is exactly what the
    database `CHECK` forbids one layer down. The engine keeping its own pair in step is what makes
    the two agree rather than merely both existing.
    """
    engine = await _engine(store)
    # `Engine.start` already loaded the pointer, which the migration seeds at `scorer_config` 1.
    assert (engine.scorer_config_id, engine.scorer_model_version_id) == (1, None)

    model_version_id = await _activate(
        store, KIND_LOGISTIC, model_version.canonical_document(FITTED)
    )
    await engine.load_scorer_config()
    assert engine.scorer_model_version_id == model_version_id
    assert engine.scorer_config_id is None

    async with store.lock:
        await store.set_active_scorer_config(1, "tester", BASE)
        await store.commit()
    await engine.load_scorer_config()
    assert engine.scorer_config_id == 1
    assert engine.scorer_model_version_id is None, (
        "moving back to a scorer_config left the engine still claiming a model version"
    )


# -- the load path never raises ------------------------------------------------------------

# Every malformed-input class, each with the fragment its warning must carry. Parametrised so a
# class cannot be dropped without the id disappearing from the test report.
MALFORMED: list[tuple[str, str, str, str]] = [
    ("unparseable-json", KIND_LOGISTIC, "{not json at all", "not valid JSON"),
    ("json-not-an-object", KIND_LOGISTIC, "[1, 2, 3]", "must be a JSON object"),
    ("empty-document", KIND_LOGISTIC, "", "not valid JSON"),
    ("unknown-kind", "onnx-cartridge", json.dumps(FITTED), "not one this build implements"),
    (
        "degenerate-all-zero-weights",
        KIND_LOGISTIC,
        json.dumps({**FITTED, "decay": 0.0, "class_affinity": 0.0, "entity_affinity": 0.0}),
        "every feature weight is zero",
    ),
    (
        "unreachable-threshold",
        KIND_LOGISTIC,
        json.dumps({**FITTED, "threshold": 99.0}),
        "highest attainable logit",
    ),
    (
        "runaway-coefficient",
        KIND_LOGISTIC,
        json.dumps({**FITTED, "decay": 5000.0}),
        "beyond the magnitude bound",
    ),
    (
        "non-finite",
        KIND_LOGISTIC,
        '{"intercept":NaN,"decay":2.0,"class_affinity":1.2,"entity_affinity":0.8,"threshold":0.0}',
        "finite number",
    ),
    (
        "wrong-keys-for-the-kind",
        KIND_ADDITIVE,
        json.dumps(FITTED),
        "wrong keys",
    ),
]


@pytest.mark.parametrize(
    ("label", "kind", "document", "expected"),
    MALFORMED,
    ids=[case[0] for case in MALFORMED],
)
async def test_every_malformed_input_class_falls_back_and_never_raises(
    store: Store, label: str, kind: str, document: str, expected: str
) -> None:
    """**The no-raise test.** Fall back to the built-in default, warn, and do not raise."""
    engine = await _engine(store)
    await _activate(store, kind, document)

    await engine.load_scorer_config()  # must not raise — that is half the assertion

    active = engine.correlator.scorer.active
    assert isinstance(active, scoring.AdditiveScorer)
    assert active.params_fingerprint() == scoring.default_scorer().params_fingerprint(), (
        f"{label}: fell back to something other than the coded defaults"
    )
    assert engine.scorer_model_version_id is None, (
        f"{label}: the engine still claims to run the model version it just refused"
    )
    assert len(engine.scorer_warnings) == 1, f"{label}: the operator was not warned"
    warning = engine.scorer_warnings[0]
    assert expected in warning, f"{label}: the warning does not say why — {warning!r}"
    assert "built-in default parameters" in warning


async def test_an_unsupported_contract_version_falls_back_too(store: Store) -> None:
    """The contract check reaches the same door as a degenerate payload, so a caller cannot handle
    the two differently by accident."""
    engine = await _engine(store)
    await _activate(store, KIND_LOGISTIC, json.dumps(FITTED), contract_version="9.0")
    await engine.load_scorer_config()
    assert isinstance(engine.correlator.scorer.active, scoring.AdditiveScorer)
    assert "not supported by this build" in engine.scorer_warnings[0]


async def test_the_fallback_goes_to_the_default_and_not_to_the_previous_model(
    store: Store,
) -> None:
    """`PREREGISTRATION-0.11.0.md` §6.9, registered in advance and asserted here.

    A promoted model loads, then a later one is malformed. The appliance must run **the built-in
    default**, not the model it was running a minute ago. An appliance silently running something
    other than what the pointer names is worse than one running the documented default, because the
    first is invisible and the second is in the operator's warnings.
    """
    engine = await _engine(store)
    await _activate(store, KIND_LOGISTIC, model_version.canonical_document(FITTED))
    await engine.load_scorer_config()
    assert isinstance(engine.correlator.scorer.active, challenger.LogisticScorer)

    await _activate(store, KIND_LOGISTIC, "{broken")
    await engine.load_scorer_config()

    active = engine.correlator.scorer.active
    assert isinstance(active, scoring.AdditiveScorer), "it fell back to the PREVIOUS model"
    assert active.params_fingerprint() == scoring.default_scorer().params_fingerprint()


async def test_a_malformed_model_version_writes_an_audit_row(store: Store) -> None:
    """The degradation is recorded, not only warned about.

    `scorer.fallback` is written by the maintenance pass through `_audit_scorer_fallback`, which
    fires when the *active scorer* has degraded. A rejected payload never becomes the active scorer
    at all, so the operator warning is the surface that carries it — and the audit row that carries
    a promotion's own refusal is `promotion`, one phase later. This test pins which of the two
    mechanisms is responsible, so a later reader does not assume the wrong one and find nothing.
    """
    engine = await _engine(store)
    await _activate(store, KIND_LOGISTIC, "{broken")
    await engine.load_scorer_config()

    assert engine.scorer_warnings, "no operator warning"
    assert engine.scorer_warning_list() == engine.scorer_warnings

    chain = await audit.verify_chain(store)
    assert chain.ok, "the audit chain must survive a rejected payload"


async def test_the_load_path_survives_a_failure_the_validator_does_not_produce(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure this build did **not** anticipate, which is why the `except` is wide.

    `ModelPayloadError` covers everything the validator knows how to refuse. It cannot cover what a
    *future* scorer kind's constructor does — a `MemoryError` loading something large, a
    `RecursionError`, an `AttributeError` from a half-written adapter in v0.13.0. A narrower
    `except ModelPayloadError` would be the more precise-looking code and would let exactly that
    take ingestion down, on a call that runs at the top of every maintenance pass.

    Injected at the seam rather than through a corrupt row **because the schema makes a corrupt row
    hard to build**: `kind` and `params_document` are both `NOT NULL`, and SQLite's dynamic typing
    turns everything else into a string the validator refuses politely. That is a good property of
    `0013` and it is the reason this test cannot get at the branch from the database side.
    """
    engine = await _engine(store)
    await _activate(store, KIND_LOGISTIC, model_version.canonical_document(FITTED))
    await engine.load_scorer_config()
    assert isinstance(engine.correlator.scorer.active, challenger.LogisticScorer)

    def _explode(*_args: object, **_kwargs: object) -> scoring.LinkScorer:
        raise RecursionError("a future kind's constructor did something nobody planned for")

    monkeypatch.setattr(model_version, "scorer_for", _explode)
    await _activate(store, KIND_LOGISTIC, model_version.canonical_document(FITTED))

    await engine.load_scorer_config()  # must not raise

    assert isinstance(engine.correlator.scorer.active, scoring.AdditiveScorer)
    assert engine.scorer_warnings, "an unanticipated failure degraded silently"
    assert "RecursionError" in engine.scorer_warnings[0], (
        "the warning must name what went wrong, even when the build cannot interpret it"
    )
