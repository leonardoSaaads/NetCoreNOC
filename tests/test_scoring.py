"""The v0.6.0 scoring seam: parity, the contract, bounds, provenance, fallback, and plurality.

Findings covered here: F20 (parameter poisoning), F23 (provenance integrity), F24 (no new
hot-path surface), F25 (fail-safe execution). The HTTP-side findings (F21 privilege boundary,
F22 preview as a DoS/exfiltration surface) live in `test_scorer_api.py`.

The two scorers defined at the top are the **plurality proof** required by SCOPE-0.6 §1: they
exist only here, prove the `LinkScorer` Protocol accepts an implementation that is not
`AdditiveScorer`, and drive the fail-safe fallback. No second scorer ships in `src/netcorenoc/`.
"""

from __future__ import annotations

import math
import sqlite3
import time

import pytest
from hypothesis import given
from hypothesis import strategies as st

from netcorenoc.engine.correlate import scoring
from netcorenoc.engine.correlate.correlate import Correlator, WindowAlarm
from netcorenoc.engine.correlate.learn import Learner
from netcorenoc.engine.correlate.scoring import (
    AdditiveScorer,
    ContractVersionError,
    LinkFeatures,
    LinkScore,
    LinkScorer,
    SafeScorer,
    ScorerParamsError,
    TermContribution,
)
from netcorenoc.main import Engine
from netcorenoc.store import MIGRATIONS_DIR, Store

import util

BASE = 1_700_000_000.0


# -- test-only alternate scorers (the Protocol plurality proof) -----------------------------


class ConstantScorer:
    """A second, trivially different implementation. Its only job is to prove the Protocol is
    satisfiable by something that is not the built-in scorer — structurally, with no base class
    to inherit and no registry to edit."""

    scorer_id = "test-constant"
    contract_version = scoring.CONTRACT_VERSION

    def __init__(self, value: float = 0.9) -> None:
        self.value = value

    def score(self, features: LinkFeatures) -> LinkScore:
        return LinkScore(
            linked=True,
            score=self.value,
            threshold=0.0,
            terms=(TermContribution("constant", 1.0, self.value, self.value),),
        )

    def params_fingerprint(self) -> str:
        return f"constant:{self.value}"


class RaisingScorer:
    """A scorer that always fails. The engine must degrade to the coded defaults, not die."""

    scorer_id = "test-raising"
    contract_version = scoring.CONTRACT_VERSION

    def score(self, features: LinkFeatures) -> LinkScore:
        raise RuntimeError("deliberate scorer failure")

    def params_fingerprint(self) -> str:
        return "raising"


class SlowScorer:
    """A scorer that overruns its budget. Correct but too slow is still a malfunction."""

    scorer_id = "test-slow"
    contract_version = scoring.CONTRACT_VERSION

    def __init__(self, delay_s: float) -> None:
        self.delay_s = delay_s
        self.calls = 0

    def score(self, features: LinkFeatures) -> LinkScore:
        self.calls += 1
        time.sleep(self.delay_s)
        return AdditiveScorer().score(features)

    def params_fingerprint(self) -> str:
        return "slow"


class EmptyTermsScorer:
    """A scorer that answers without explaining itself — a contract violation, by design."""

    scorer_id = "test-empty-terms"
    contract_version = scoring.CONTRACT_VERSION

    def score(self, features: LinkFeatures) -> LinkScore:
        return LinkScore(linked=True, score=1.0, threshold=0.5, terms=())

    def params_fingerprint(self) -> str:
        return "empty"


def features(dt: float = 5.0, a: float = 0.0, e: float = 1.0) -> LinkFeatures:
    return LinkFeatures(
        delta_t_s=dt, class_i=1, class_j=2, class_affinity=a, ne_i=10, ne_j=10, entity_affinity=e
    )


# -- the Protocol accepts a second implementation ---------------------------------------------


@pytest.mark.parametrize(
    "scorer",
    [AdditiveScorer(), ConstantScorer(), RaisingScorer(), SlowScorer(0.0), EmptyTermsScorer()],
)
def test_protocol_accepts_more_than_one_implementation(scorer: object) -> None:
    """Plurality proof: five structurally different classes all satisfy `LinkScorer`."""
    assert isinstance(scorer, LinkScorer)


def test_correlator_accepts_an_alternate_scorer() -> None:
    """The seam is real: swapping the scorer changes the decision, with no other change."""
    correlator, learner = Correlator(), Learner()
    correlator.process(WindowAlarm(1, 1, 10, ts=100.0), learner)
    # Far apart on the same device: the default refuses the link (0.3·e^-1 + 0.35 < 0.5).
    assert correlator.process(WindowAlarm(2, 2, 10, ts=130.0), learner).links == []

    correlator = Correlator()
    correlator.set_scorer(ConstantScorer())
    correlator.process(WindowAlarm(1, 1, 10, ts=100.0), learner)
    linked = correlator.process(WindowAlarm(2, 2, 10, ts=130.0), learner)
    assert len(linked.links) == 1
    assert linked.links[0].terms[0].name == "constant"


# -- parity: the extracted scorer is the v0.5.0 expression ------------------------------------


@given(
    dt=st.floats(min_value=0.0, max_value=120.0),
    affinity_a=st.floats(min_value=0.0, max_value=1.0),
    affinity_e=st.floats(min_value=0.0, max_value=1.0),
)
def test_default_scorer_reproduces_the_v050_expression(
    dt: float, affinity_a: float, affinity_e: float
) -> None:
    """Parity, at the bit level: the same three products summed in the same order, and the same
    strict comparison. Float addition is not associative, so `==` is the right assertion here —
    a tolerance would hide exactly the class of error this gate exists to catch."""
    w_t, w_a, w_e, tau, threshold = 0.3, 0.35, 0.35, 30.0, 0.5
    expected_t = w_t * math.exp(-abs(dt) / tau)
    expected_a = w_a * affinity_a
    expected_e = w_e * affinity_e
    expected = expected_t + expected_a + expected_e

    result = AdditiveScorer().score(features(dt=dt, a=affinity_a, e=affinity_e))
    assert result.score == expected
    assert result.linked == (expected > threshold)
    assert [t.contribution for t in result.terms] == [expected_t, expected_a, expected_e]


def test_default_scorer_emits_exactly_the_three_named_terms() -> None:
    result = AdditiveScorer().score(features())
    assert [t.name for t in result.terms] == ["temporal", "class_affinity", "entity_affinity"]
    for term in result.terms:
        assert term.contribution == term.weight * term.value
    assert result.score == sum(t.contribution for t in result.terms)


def test_coded_defaults_are_the_v050_constants() -> None:
    scorer = AdditiveScorer()
    assert scorer.params() == {
        "w_t": 0.3,
        "w_a": 0.35,
        "w_e": 0.35,
        "tau_s": 30.0,
        "threshold": 0.5,
    }
    assert scorer.scorer_id == "additive"
    assert scorer.contract_version == "1.0"


def test_reserved_feature_slots_are_none_and_ignored() -> None:
    """DECISIONS #49: the reserved slots exist so v0.7/v0.8 features are a minor bump. Setting
    one must not change today's answer — the default scorer never reads them."""
    plain = features()
    enriched = LinkFeatures(
        delta_t_s=plain.delta_t_s,
        class_i=plain.class_i,
        class_j=plain.class_j,
        class_affinity=plain.class_affinity,
        ne_i=plain.ne_i,
        ne_j=plain.ne_j,
        entity_affinity=plain.entity_affinity,
        severity_i=1,
        severity_j=5,
        topo_distance=3.0,
        probable_cause_i="lossOfSignal",
        event_type_i="communicationsAlarm",
    )
    assert plain.severity_i is None and plain.topo_distance is None
    assert AdditiveScorer().score(plain).score == AdditiveScorer().score(enriched).score


# -- fingerprint and the migration seed --------------------------------------------------------


def test_seed_params_hash_matches_the_coded_default() -> None:
    """The seed in 0005_scorer_config.sql must be the coded default's own fingerprint, so a
    future change to either the defaults or the canonicalisation cannot silently desynchronise
    them (Gate 2)."""
    sql = (MIGRATIONS_DIR / "0005_scorer_config.sql").read_text()
    assert AdditiveScorer().params_fingerprint() in sql


def test_fingerprint_is_stable_and_parameter_sensitive() -> None:
    assert AdditiveScorer().params_fingerprint() == AdditiveScorer().params_fingerprint()
    assert AdditiveScorer().params_fingerprint() != AdditiveScorer(w_t=0.31).params_fingerprint()


# -- contract versioning ------------------------------------------------------------------------


def test_matching_major_contract_version_is_accepted() -> None:
    scoring.check_contract_version("1.0")
    scoring.check_contract_version("1.7")  # a minor bump adds optional fields: still compatible


@pytest.mark.parametrize("version", ["2.0", "0.9", "10.1"])
def test_unsupported_major_contract_version_is_refused(version: str) -> None:
    with pytest.raises(ContractVersionError, match="not supported"):
        scoring.check_contract_version(version)


def test_malformed_contract_version_is_refused() -> None:
    with pytest.raises(ContractVersionError, match="malformed"):
        scoring.check_contract_version("not-a-version")


# -- F20: bounds and degeneracy ------------------------------------------------------------------


def test_f20_coded_defaults_validate() -> None:
    scoring.validate_params(0.3, 0.35, 0.35, 30.0, 0.5)


@pytest.mark.parametrize(
    ("params", "reason"),
    [
        ((-0.01, 0.35, 0.35, 30.0, 0.5), "between 0 and 1"),
        ((1.01, 0.35, 0.35, 30.0, 0.5), "between 0 and 1"),
        ((0.3, -0.1, 0.35, 30.0, 0.5), "between 0 and 1"),
        ((0.3, 0.35, 1.5, 30.0, 0.5), "between 0 and 1"),
        ((0.3, 0.35, 0.35, 30.0, 1.5), "between 0 and 1"),
        ((float("nan"), 0.35, 0.35, 30.0, 0.5), "finite"),
        ((0.3, 0.35, 0.35, float("inf"), 0.5), "finite"),
        ((0.3, 0.35, 0.35, 0.5, 0.5), "seconds"),  # tau below MIN_TAU_S
        ((0.3, 0.35, 0.35, 3600.1, 0.5), "seconds"),  # tau above MAX_TAU_S
        ((0.02, 0.02, 0.02, 30.0, 0.01), "below the minimum"),  # weight sum degenerate
        ((0.3, 0.35, 0.35, 30.0, 0.0), "at least"),  # threshold <= 0 merges everything
        ((0.3, 0.35, 0.35, 30.0, 1.0), "no headroom"),  # threshold >= max score links nothing
        ((0.2, 0.2, 0.2, 30.0, 0.6), "no headroom"),  # exactly the weight sum
        ((0.2, 0.2, 0.2, 30.0, 0.595), "no headroom"),  # inside the margin
    ],
)
def test_f20_out_of_bounds_and_degenerate_sets_are_rejected(
    params: tuple[float, float, float, float, float], reason: str
) -> None:
    """F20: range checks alone would admit the last five of these — each is a syntactically
    valid set that collapses or shatters every incident. The reason is precise, not generic."""
    with pytest.raises(ScorerParamsError, match=reason):
        scoring.validate_params(*params)


def test_f20_boundaries_are_inclusive_where_documented() -> None:
    """The exact boundary values are legal; one step beyond is not. Bounds are constants, so a
    silent widening of any of them fails here."""
    scoring.validate_params(0.3, 0.35, 0.35, scoring.MIN_TAU_S, 0.5)
    scoring.validate_params(0.3, 0.35, 0.35, scoring.MAX_TAU_S, 0.5)
    scoring.validate_params(0.3, 0.35, 0.35, 30.0, scoring.MIN_THRESHOLD)
    scoring.validate_params(0.34, 0.33, 0.33, 30.0, 1.0 - scoring.THRESHOLD_MARGIN)
    with pytest.raises(ScorerParamsError):
        scoring.validate_params(0.3, 0.35, 0.35, scoring.MIN_TAU_S - 0.001, 0.5)
    with pytest.raises(ScorerParamsError):
        scoring.validate_params(0.3, 0.35, 0.35, scoring.MAX_TAU_S + 0.001, 0.5)
    with pytest.raises(ScorerParamsError):
        scoring.validate_params(0.3, 0.35, 0.35, 30.0, scoring.MIN_THRESHOLD - 0.001)


def test_f20_degenerate_extremes_would_indeed_have_been_catastrophic() -> None:
    """Show the harm the bound prevents, so the bound is justified rather than asserted: at
    threshold 0 every cold-start cross-device pair links; at the weight sum, nothing ever does."""
    learner = Learner()
    merge_all = AdditiveScorer(threshold=0.0)
    shatter_all = AdditiveScorer(threshold=1.0)
    pair = Correlator.features(
        WindowAlarm(1, 1, 10, ts=100.0), WindowAlarm(2, 2, 11, ts=190.0), learner
    )
    assert merge_all.score(pair).linked is True  # unrelated NEs, 90 s apart: linked anyway
    assert shatter_all.score(features(dt=0.0, a=1.0, e=1.0)).linked is False  # perfect pair: not


# -- F25: fail-safe execution ----------------------------------------------------------------


def test_f25_raising_scorer_falls_back_to_the_defaults() -> None:
    safe = SafeScorer(RaisingScorer())
    result = safe.score(features())
    assert safe.degraded is True
    assert "RuntimeError" in safe.last_error
    # The answer is the *default* scorer's answer, not an error and not a refusal.
    assert result.score == AdditiveScorer().score(features()).score
    assert safe.active.scorer_id == "additive"
    assert safe.warnings() and "built-in defaults" in safe.warnings()[0]


def test_f25_scorer_that_returns_no_terms_is_a_contract_violation() -> None:
    safe = SafeScorer(EmptyTermsScorer())
    result = safe.score(features())
    assert safe.degraded is True
    assert "explainability is contractual" in safe.last_error
    assert [t.name for t in result.terms] == ["temporal", "class_affinity", "entity_affinity"]


def test_f25_over_budget_scorer_degrades_on_the_next_call() -> None:
    """A slow scorer cannot be interrupted mid-call in-process (documented limit), so the budget
    check degrades every subsequent call. The delegate is never consulted again."""
    slow = SlowScorer(delay_s=0.05)
    safe = SafeScorer(slow, budget_s=0.01)
    safe.score(features())  # first call completes, then trips the budget
    assert safe.degraded is True and slow.calls == 1
    safe.score(features())
    assert slow.calls == 1  # the delegate was not called again
    assert "over the" in safe.last_error


def test_f25_degradation_is_sticky_and_counted_once() -> None:
    safe = SafeScorer(RaisingScorer())
    for _ in range(5):
        safe.score(features())
    assert safe.failures == 1  # it degrades once, then stops trying


def test_f25_healthy_scorer_is_never_degraded() -> None:
    safe = SafeScorer(AdditiveScorer())
    for dt in (0.0, 1.0, 60.0):
        safe.score(features(dt=dt))
    assert safe.degraded is False and safe.failures == 0 and safe.warnings() == []


async def test_f25_engine_never_runs_scorerless_and_audits_the_fallback(store: Store) -> None:
    """The engine always has a usable scorer, and a degradation is recorded once as
    `scorer.fallback` (system actor) rather than passing silently."""
    engine = Engine(store, __import__("asyncio").Queue())
    await engine.start()
    assert engine.correlator.scorer.active is not None

    engine.correlator.set_scorer(RaisingScorer())
    await util.drive(engine, engine.queue, util.fixture_events("fiber_cut.json", BASE))
    assert engine.correlator.scorer.degraded is True

    await engine.maintenance(BASE + 10, retention_days=365.0)
    async with store.lock:
        rows = await store.audit_all()
    fallbacks = [r for r in rows if r["action"] == "scorer.fallback"]
    assert len(fallbacks) == 1, "exactly one row per degradation, not one per scored pair"
    assert fallbacks[0]["actor"] == "system" and fallbacks[0]["outcome"] == "error"
    assert "deliberate scorer failure" not in fallbacks[0]["details"]  # the type, not the message
    assert "RuntimeError" in fallbacks[0]["details"]

    await engine.maintenance(BASE + 20, retention_days=365.0)
    async with store.lock:
        rows = await store.audit_all()
    assert len([r for r in rows if r["action"] == "scorer.fallback"]) == 1  # still once

    # Correlation kept working on the defaults throughout.
    async with store.lock:
        assert (await store.stats())["open_situations"] > 0
    assert engine.scorer_warning_list()


# -- the engine reload point + F23 provenance ---------------------------------------------------


async def test_engine_loads_the_seeded_configuration(store: Store) -> None:
    engine = Engine(store, __import__("asyncio").Queue())
    await engine.start()
    assert engine.scorer_config_id == 1
    active = engine.correlator.scorer.active
    assert isinstance(active, AdditiveScorer)
    assert active.params() == AdditiveScorer().params()


async def test_f23_situation_provenance_recovers_exact_parameters(store: Store) -> None:
    """F23: from a situation, recover the parameters and contract version that formed it."""
    engine = Engine(store, __import__("asyncio").Queue())
    await engine.start()
    await util.drive(engine, engine.queue, util.fixture_events("fiber_cut.json", BASE))

    async with store.lock:
        cur = await store.conn.execute(
            "SELECT id, scorer_config_id FROM situation ORDER BY id LIMIT 1"
        )
        row = await cur.fetchone()
        assert row is not None and row["scorer_config_id"] is not None
        config = await store.get_scorer_config(int(row["scorer_config_id"]))
    assert config is not None
    assert (config["w_t"], config["w_a"], config["w_e"], config["tau_s"], config["threshold"]) == (
        0.3,
        0.35,
        0.35,
        30.0,
        0.5,
    )
    assert config["contract_version"] == "1.0"
    # The recovered parameters reproduce the fingerprint, so the row cannot have drifted.
    assert (
        config["params_hash"]
        == AdditiveScorer(
            w_t=float(config["w_t"]),
            w_a=float(config["w_a"]),
            w_e=float(config["w_e"]),
            tau_s=float(config["tau_s"]),
            threshold=float(config["threshold"]),
        ).params_fingerprint()
    )


async def test_f23_scorer_config_is_append_only(store: Store) -> None:
    """F23: the parameter history behind a historical grouping cannot be edited or deleted —
    the same storage-level guarantee `audit_log` has, and here with no sanctioned deleter."""
    async with store.lock:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            await store.conn.execute("UPDATE scorer_config SET w_t=0.9 WHERE id=1")
        await store.conn.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            await store.conn.execute("DELETE FROM scorer_config WHERE id=1")
        await store.conn.rollback()


async def test_f23_prune_does_not_touch_scorer_config(store: Store) -> None:
    """Provenance must outlive the alarms that formed it, so retention skips this table."""
    async with store.lock:
        await store.prune(now=BASE + 10_000_000, retention_s=1.0)
        cur = await store.conn.execute("SELECT COUNT(*) FROM scorer_config")
        row = await cur.fetchone()
    assert row is not None and row[0] == 1


async def test_active_pointer_moves_and_the_engine_reloads(store: Store) -> None:
    """Apply and rollback are the same operation, and both take effect at the reload point."""
    engine = Engine(store, __import__("asyncio").Queue())
    await engine.start()
    async with store.lock:
        new_id = await store.insert_scorer_config(
            "additive", "1.0", 0.4, 0.3, 0.3, 45.0, 0.55, "hash-2", "adm", BASE, "tuned"
        )
        assert await store.set_active_scorer_config(new_id, "adm", BASE)
        await store.commit()

    # Not yet: the change takes effect at the documented reload point, never mid-batch.
    active = engine.correlator.scorer.active
    assert isinstance(active, AdditiveScorer) and active.tau_s == 30.0

    await engine.maintenance(BASE + 1, retention_days=365.0)
    active = engine.correlator.scorer.active
    assert isinstance(active, AdditiveScorer) and active.tau_s == 45.0
    assert engine.scorer_config_id == new_id

    async with store.lock:
        assert await store.set_active_scorer_config(1, "adm", BASE + 2)
        await store.commit()
    await engine.maintenance(BASE + 3, retention_days=365.0)
    active = engine.correlator.scorer.active
    assert isinstance(active, AdditiveScorer) and active.tau_s == 30.0
    assert engine.scorer_config_id == 1


async def test_rollback_target_must_exist(store: Store) -> None:
    async with store.lock:
        assert await store.set_active_scorer_config(999_999, "adm", BASE) is False


# -- fail-safe on a bad stored row ---------------------------------------------------------------


async def test_stored_out_of_bounds_row_falls_back_to_the_coded_defaults(store: Store) -> None:
    """Belt and braces: validation runs at the write *and* at load, so a row that reached the
    table by any other means (a direct sqlite edit, a future bug) cannot reach the engine."""
    async with store.lock:
        bad = await store.insert_scorer_config(
            "additive", "1.0", 0.3, 0.35, 0.35, 30.0, 0.0, "bad", "adm", BASE, "degenerate"
        )
        await store.set_active_scorer_config(bad, "adm", BASE)
        await store.commit()
    engine = Engine(store, __import__("asyncio").Queue())
    await engine.start()
    active = engine.correlator.scorer.active
    assert isinstance(active, AdditiveScorer)
    assert active.params() == AdditiveScorer().params()  # the coded defaults
    assert engine.scorer_config_id is None
    assert any("rejected" in w for w in engine.scorer_warning_list())


async def test_stored_unsupported_contract_version_falls_back(store: Store) -> None:
    async with store.lock:
        bad = await store.insert_scorer_config(
            "future", "9.0", 0.3, 0.35, 0.35, 30.0, 0.5, "future", "adm", BASE, ""
        )
        await store.set_active_scorer_config(bad, "adm", BASE)
        await store.commit()
    engine = Engine(store, __import__("asyncio").Queue())
    await engine.start()
    assert engine.scorer_config_id is None
    assert any("not supported" in w for w in engine.scorer_warning_list())


async def test_unreadable_store_falls_back_without_stopping_the_engine(store: Store) -> None:
    """Fail-safe, never fail-stop: if the configuration cannot be read at all, correlation runs
    on the coded defaults rather than refusing to start."""
    engine = Engine(store, __import__("asyncio").Queue())

    async def boom() -> dict[str, object]:
        raise sqlite3.OperationalError("database is locked")

    engine.store.active_scorer_config = boom  # type: ignore[method-assign]
    await engine.load_scorer_config()
    assert engine.scorer_config_id is None
    assert isinstance(engine.correlator.scorer.active, AdditiveScorer)
    assert any("unreadable" in w for w in engine.scorer_warning_list())


# -- F24: the ingest path gained nothing ---------------------------------------------------------


def test_f24_datagram_received_is_unchanged_and_touches_no_scoring() -> None:
    """F24: prime directive 2. The datagram callback must not read a config, take a lock, or
    score anything — asserted against its source so a future edit has to face this test."""
    import inspect

    from netcorenoc import receiver

    source = inspect.getsource(receiver.TrapReceiver.datagram_received)
    for forbidden in ("scorer", "scoring", "score", "config", "await", "lock", "execute"):
        assert forbidden not in source, f"{forbidden!r} appeared on the datagram path"
    # It still does exactly the four things it did in v0.5.0.
    for expected in ("_allowed", "parse_trap", "quarantine_packet", "put_nowait"):
        assert expected in source


def test_f24_receiver_module_does_not_import_the_scoring_seam() -> None:
    """A structural guard: the ingest module cannot reach scoring even indirectly by import.

    Read through `ast`, not as a substring of the source. Until v0.15.1 this looked for the text
    `"netcorenoc.scoring"` — which stopped appearing anywhere the moment the module moved to
    `netcorenoc.engine.correlate.scoring`, so all three assertions would have passed forever on a
    receiver that imported every one of them. Its control is the next test.
    """
    reachable = util.imported_modules(util.module_path("receiver.py"))
    for forbidden in ("scoring", "preview", "correlate"):
        assert forbidden not in reachable, (
            f"receiver.py imports {forbidden!r}. The ingest module may not reach the scoring seam, "
            "even indirectly (F24)."
        )


def test_the_receiver_import_reader_would_notice_one() -> None:
    """**The control.** A reader that found nothing would clear any receiver at all.

    `correlate.py` imports both `learn` and `scoring`, so the same call applied there must come
    back positive — and `receiver.py` must be seen to import *something*, or the walk is broken
    rather than the module clean.
    """
    assert {"learn", "scoring"} <= util.imported_modules(util.module_path("correlate.py"))
    assert util.imported_modules(util.module_path("receiver.py")), "receiver imports nothing at all"


def test_f24_preview_module_never_imports_the_eval_harness() -> None:
    """DECISIONS #48: the corpus harness stays the dev/CI gate, never a runtime dependency.

    Asserted over the parsed import statements, not over the prose — the module *documents* that
    it does not import `eval/`, and a substring check would trip over its own explanation."""
    import ast

    source = util.module_path("preview.py").read_text(encoding="utf-8")
    imported: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert imported, "the parse found no imports at all — the assertion would be vacuous"
    for module in imported:
        root = module.split(".")[0]
        assert root not in {"harness", "metrics", "corpus_gen", "scenario_dsl"}, module
        assert root in {"__future__", "time", "dataclasses", "typing", "netcorenoc"}, module
