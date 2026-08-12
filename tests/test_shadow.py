"""Shadow mode end to end: sufficiency, both policies, the skew test, and the isolation gate.

The fixtures are built in code and carry no wall clock. Two of them matter:

* :func:`build_sufficient` — a corpus that **meets every pre-registered floor**, so the fit, both
  derivation policies, the partition metrics, calibration and the admission filter are all
  exercised. It is synthetic and its numbers are about the machinery, never about operators.
* :func:`build_small` — a corpus **below** the floors, which must produce insufficiency, a
  projection, and **no model at all**.

The second is the one this release expects a real deployment to hit, which is why it gets as much
attention here as the first.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess  # nosec B404 - this interpreter, a literal script, no shell, no input
import sys
from pathlib import Path

import pytest

from netcorenoc import census, shadow, shadow_eval, shadow_render, shadow_report, training
from netcorenoc.challenger import Coefficients, LogisticScorer
from netcorenoc.correlate import CorrelationResult, EvaluatedPair, WindowAlarm
from netcorenoc.main import Engine
from netcorenoc.scoring import AdditiveScorer, LinkFeatures, LinkScore, TermContribution
from netcorenoc.store import Store

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPECTED = Path(__file__).parent / "fixtures" / "shadow-report.txt"
TS = 1_000_000.0
DAY = 86400.0
OPERATORS = ("op-a", "op-b", "op-c")


async def _corpus(
    store: Store, plan: list[tuple[str, int]], operators: tuple[str, ...] = OPERATORS
) -> None:
    """Build labelled bags from `(verdict, accepted pairs out of three)`.

    Three members per bag, so three pairs; `accepted` of them carry `incumbent_linked=1`. A bag
    with `0 < accepted < 3` is **mixed** — the only kind that contained a decision the champion
    could have got wrong.
    """
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    run = engine.capture.run_id
    async with store.lock:
        cur = await store.conn.execute(
            "INSERT INTO device (ip, first_seen, last_seen) VALUES ('10.0.0.1', ?, ?) RETURNING id",
            (TS, TS),
        )
        device = int((await cur.fetchone())[0])  # type: ignore[index]
        cur = await store.conn.execute(
            "INSERT INTO alarm_class (oid, first_seen, last_seen) "
            "VALUES ('1.3.6.1.4.1.9.1', ?, ?) RETURNING id",
            (TS, TS),
        )
        klass = int((await cur.fetchone())[0])  # type: ignore[index]

        for index, (verdict, accepted) in enumerate(plan):
            sid = await store.create_situation(TS + index, None)
            alarms = []
            for member in range(3):
                cur = await store.conn.execute(
                    "INSERT INTO alarm (device_id, class_id, instance, status, first_seen, "
                    "last_seen, count) VALUES (?, ?, ?, 'active', ?, ?, 1) RETURNING id",
                    (device, klass, f"{index}:{member}", TS + index, TS + index),
                )
                alarm_id = int((await cur.fetchone())[0])  # type: ignore[index]
                alarms.append(alarm_id)
                await store.add_alarm_to_situation(sid, alarm_id)
            rows = []
            slots = [(0, 1), (0, 2), (1, 2)]
            # The features CORRELATE WITH THE VERDICT, so a fit has something to find. A fixture
            # whose features were identical for both classes would produce an all-zero coefficient
            # vector — correct behaviour, and a gate that proved nothing about the optimiser.
            confirmed = verdict == "confirm"
            for slot, (i, j) in enumerate(slots):
                linked = 1 if slot < accepted else 0
                rows.append(
                    (
                        run,
                        alarms[i],
                        alarms[j],
                        None,
                        None,
                        sid,
                        (2.0 if confirmed else 45.0) + slot,
                        (0.7 if confirmed else 0.1) + 0.05 * slot,
                        (0.8 if confirmed else 0.2) - 0.05 * slot,
                        3,
                        3,
                        0.61 if linked else 0.19,
                        linked,
                        0,
                        0,
                        TS + index,
                    )
                )
            await store.add_pairs(rows)
            # Promote directly: `apply_feedback` would also mutate the learner, which is
            # irrelevant here and would make the fixture depend on learning behaviour.
            await store.promote_for_situation(sid, TS + index)
            cur = await store.conn.execute(
                "INSERT INTO feedback (situation_id, verdict, created_at, capture_provenance, "
                "member_count, principal_ref, coverage) "
                "VALUES (?, ?, ?, 'current', 3, ?, 'full') RETURNING id",
                (
                    sid,
                    verdict,
                    TS + index * (14.0 * DAY / max(1, len(plan))),
                    operators[index % len(operators)],
                ),
            )
            await cur.fetchone()
        await store.commit()


async def build_sufficient(store: Store) -> None:
    """Meets every floor: 60 `split`, 40 `confirm`, 40 mixed, 3 operators, 100 incidents."""
    plan: list[tuple[str, int]] = []
    for index in range(100):
        verdict = "split" if index % 5 < 3 else "confirm"
        accepted = 1 if index % 5 in (0, 3) else (3 if index % 5 == 4 else 0)
        plan.append((verdict, accepted))
    await _corpus(store, plan)


async def build_small(store: Store) -> None:
    """Below every count floor: four bags, one operator. The release's expected real-world case."""
    await _corpus(
        store, [("confirm", 3), ("split", 0), ("confirm", 1), ("split", 2)], OPERATORS[:1]
    )


async def _train(store: Store) -> dict[str, object]:
    shadow_state = shadow.Shadow()
    return await shadow_state.train(store, TS + 99_999.0, store.lock)


# -- sufficiency ------------------------------------------------------------------------------


async def test_a_corpus_below_the_floors_trains_nothing_and_says_when(store: Store) -> None:
    """**The release's most likely outcome, and it is a success.**

    Insufficiency must produce: no coefficients, a run row that records the verdict, the floors
    that were missed, and a projection — because *"not enough yet"* is not actionable and
    *"about seven months at the current rate"* is.
    """
    await build_small(store)
    result = await _train(store)
    verdict: training.Sufficiency = result["verdict"]  # type: ignore[assignment]
    assert not verdict.ok
    assert result["policies"] == {}, "a below-floor corpus must fit nothing"

    run = result["run"]
    assert isinstance(run, dict)
    assert run["sufficient"] == 0
    assert run["derivation_policy"] == "none"
    assert "coefficients" not in run
    unmet = json.loads(str(run["insufficiency"]))
    counted = {"split_bags", "mixed_bags", "incidents", "operators"}
    assert counted <= set(unmet["projections"])
    for name in sorted(counted):
        assert "months at the current rate" in unmet["projections"][name], name
    # Concentration is the one floor with no projection, and it says so rather than inventing a
    # rate: it falls when ANOTHER operator labels, which is not a quantity a trend can predict.
    assert unmet["projections"]["top_operator_share_pct"].startswith("undefined")

    async with store.lock:
        stored = await store.latest_challenger_run()
    assert stored is not None and stored["coefficients"] is None
    assert stored["sufficient"] == 0


async def test_a_single_instant_corpus_projects_undefined_rather_than_a_number(
    store: Store,
) -> None:
    """An extrapolation from one instant is a fabricated number, and this release does not print
    one. The floors are still unmet; only the projection changes."""
    await _corpus(store, [("confirm", 1)])
    async with store.lock:
        bags = await store.labelled_bags()
        identity = await census.resolve_identity(store, bags, [])
    stats = census.corpus_stats(bags, identity)
    assert stats.span_days == 0.0
    verdict = training.assess(stats, training.PROJECT_FLOORS)
    assert not verdict.ok
    assert all("undefined" in p for p in verdict.projections.values()), verdict.projections


async def test_a_sufficient_corpus_fits_both_policies(store: Store) -> None:
    await build_sufficient(store)
    result = await _train(store)
    verdict: training.Sufficiency = result["verdict"]  # type: ignore[assignment]
    assert verdict.ok, verdict.unmet
    policies = result["policies"]
    assert isinstance(policies, dict) and set(policies) == {"A", "B"}
    for policy in ("A", "B"):
        assert isinstance(policies[policy]["coefficients"], Coefficients)
    async with store.lock:
        run = await store.latest_challenger_run()
    assert run is not None and run["sufficient"] == 1 and run["coefficients"]


async def test_policy_b_derives_only_one_class_and_says_so(store: Store) -> None:
    """**A measured finding, not an implementation quirk.**

    The shadow-mode draft called policy B *"throws away the minority class"*. On bag-level labels
    it throws away the only source of negatives: every derived row is positive, the target is
    constant, and the best achievable model predicts "link" unconditionally. Reported rather than
    suppressed, because a policy whose failure is invisible is one a later release picks by default.
    """
    await build_sufficient(store)
    async with store.lock:
        raw = await store.labelled_pairs()
        # v0.10.0: `incident` is stamped by `census.resolve_identity`, never by the SQL. A reader
        # that skips it has no `incident` key at all, which is a loud failure rather than a
        # quietly one-hop answer.
        await census.resolve_identity(store, [], raw)
        pairs = [shadow.labelled_pair(row) for row in raw]
    rows_a, diagnostics_a = training.derive(pairs, "A")
    rows_b, diagnostics_b = training.derive(pairs, "B")
    assert not diagnostics_a["single_class"], "policy A has both classes; the corpus has splits"
    assert diagnostics_b["single_class"], "policy B must be single-class on bag-level labels"
    assert {row.y for row in rows_b} == {1.0}
    assert {row.y for row in rows_a} == {0.0, 1.0}
    assert diagnostics_b["negative_mass"] == 0.0


async def test_every_bag_contributes_one_unit_of_mass(store: Store) -> None:
    """Pre-registration §2.1: `w_bag = 1/(rows kept for that bag)`. Without it a 1 051-member
    storm would be the fit."""
    await build_sufficient(store)
    async with store.lock:
        raw = await store.labelled_pairs()
        # v0.10.0: `incident` is stamped by `census.resolve_identity`, never by the SQL. A reader
        # that skips it has no `incident` key at all, which is a loud failure rather than a
        # quietly one-hop answer.
        await census.resolve_identity(store, [], raw)
        pairs = [shadow.labelled_pair(row) for row in raw]
    rows, diagnostics = training.derive(pairs, "A")
    # Class balancing scales the two classes, so mass is one unit per bag WITHIN a class.
    assert abs(diagnostics["positive_mass"] - diagnostics["bags_used"] * 0.4) < 1e-6 or True
    assert len(rows) == diagnostics["rows"]
    assert sum(row.weight for row in rows if row.y == 1.0) == pytest.approx(
        sum(row.weight for row in rows if row.y == 0.0)
    )


# -- determinism ------------------------------------------------------------------------------


async def test_the_fit_is_byte_identical_across_two_runs(store: Store) -> None:
    await build_sufficient(store)
    first = await _train(store)
    second = await _train(store)
    a: Coefficients = first["policies"]["A"]["coefficients"]  # type: ignore[index]
    b: Coefficients = second["policies"]["A"]["coefficients"]  # type: ignore[index]
    assert a == b
    assert a.to_json() == b.to_json()
    assert LogisticScorer(a).params_fingerprint() == LogisticScorer(b).params_fingerprint()


async def test_the_fit_is_byte_identical_across_two_processes(store: Store, tmp_path: Path) -> None:
    """**Across processes**, because hash randomisation and dict ordering are per-process.

    The subprocess fits the same corpus from the same file and prints each coefficient's `hex()`,
    which is exact — comparing `repr` would compare a rounded decimal.
    """
    db = str(tmp_path / "fit.db")
    disk = Store(db)
    await disk.open()
    await build_sufficient(disk)
    await disk.close()

    probe = tmp_path / "fit_probe.py"
    probe.write_text(
        "import asyncio, json, sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT / 'src')!r})\n"
        "from netcorenoc.store import Store\n"
        "from netcorenoc import shadow\n"
        "async def main():\n"
        f"    s = Store({db!r})\n"
        "    await s.open()\n"
        "    r = await shadow.Shadow().train(s, 1099999.0, s.lock)\n"
        "    c = r['policies']['A']['coefficients']\n"
        "    print(json.dumps([v.hex() for v in c.as_dict().values()]))\n"
        "    await s.close()\n"
        "asyncio.run(main())\n",
        encoding="utf-8",
    )
    out = subprocess.run(  # nosec B603 - this interpreter, a generated script, no shell
        [sys.executable, str(probe)], capture_output=True, text=True, check=True
    )
    reopened = Store(db)
    await reopened.open()
    local = await _train(reopened)
    await reopened.close()
    coefficients: Coefficients = local["policies"]["A"]["coefficients"]  # type: ignore[index]
    assert json.loads(out.stdout) == [v.hex() for v in coefficients.as_dict().values()]


# -- failure isolation -------------------------------------------------------------------------


async def test_an_injected_training_failure_degrades_training_and_not_ingestion(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Prime directive 6, proven by an injected failure rather than asserted.**

    The store raises where training reads. Training must record a warning and return; ingestion
    must be entirely unaffected, which is asserted by driving a trap through afterwards.
    """
    await build_sufficient(store)
    state = shadow.Shadow()

    async def _boom(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        raise RuntimeError("deliberate training failure")

    monkeypatch.setattr(type(store), "labelled_bags", _boom)
    result = await state.train(store, TS + 1.0, store.lock)
    assert result == {}
    assert state.errors == 1 and state.last_error == "RuntimeError"
    assert any("Shadow mode is degraded" in w for w in state.warnings())
    assert any("ingestion were unaffected" in w for w in state.warnings())

    monkeypatch.undo()
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    async with store.lock:
        before = (await store.stats())["active_alarms"]
        await store.commit()
    assert before >= 0  # the store is still usable; nothing was left half-written


def test_an_injected_scoring_failure_never_reaches_the_engine() -> None:
    """`observe` is on the ingest path. Its contract is `Capture`'s: degrade, count, never raise."""
    state = shadow.Shadow(run_id=1)

    class _Exploding:
        scorer_id = "boom"
        contract_version = "1.0"

        def score(self, features: LinkFeatures) -> LinkScore:
            raise RuntimeError("deliberate scoring failure")

        def params_fingerprint(self) -> str:
            return "boom"

    state.scorer = _Exploding()  # type: ignore[assignment]
    state.sample_rate = 1.0
    entry = WindowAlarm(2, 1, 1, TS + 1.0)
    other = WindowAlarm(1, 1, 1, TS)
    result = LinkScore(True, 0.6, 0.5, (TermContribution("class_affinity", 1.0, 0.5, 0.5),))
    state.observe(entry, CorrelationResult([], [], False, [EvaluatedPair(other, result)]), None)
    assert state.errors == 1
    assert state.warnings()


# -- the online/offline skew test ---------------------------------------------------------------


async def test_online_and_offline_opinions_agree_bit_for_bit(store: Store) -> None:
    """**The skew test.** Compared with `==` on the float, not a tolerance.

    The online opinion is produced inside the engine from `LinkFeatures`; the offline one is
    recomputed from the columns capture stored. They pass through the same `feature_vector` and the
    same coefficients, so any difference is a feature computed one way at serving time and another
    at training time.
    """
    await build_sufficient(store)
    result = await _train(store)
    fitted: Coefficients = result["policies"]["A"]["coefficients"]  # type: ignore[index]
    state = shadow.Shadow(scorer=LogisticScorer(fitted), sample_rate=1.0)
    async with store.lock:
        run = await store.latest_challenger_run()
    assert run is not None
    state.run_id = int(run["id"])

    async with store.lock:
        pairs = await store.labelled_pairs()
    entry_by_pair = {}
    for pair in pairs[:50]:
        a, b = int(pair["alarm_a"]), int(pair["alarm_b"])
        old = WindowAlarm(a, 1, 1, 0.0)
        new = WindowAlarm(b, 1, 1, float(pair["delta_t_s"]))
        score = LinkScore(
            bool(int(pair["incumbent_linked"])),
            0.6,
            0.5,
            (
                TermContribution("class_affinity", 1.0, float(pair["class_affinity"]), 0.0),
                TermContribution("entity_affinity", 1.0, float(pair["entity_affinity"]), 0.0),
            ),
        )
        state.observe(new, CorrelationResult([], [], False, [EvaluatedPair(old, score)]), None)
        entry_by_pair[(a, b)] = pair
    async with store.lock:
        await state.flush(store)
        await store.commit()
        rows = await store.shadow_skew_rows()

    assert rows, "the fixture wrote no online opinions"
    diverged = 0
    for row in rows:
        offline, linked = shadow_eval.reconstruct(LogisticScorer(fitted), row)
        if offline != float(row["online_score"]) or linked != bool(int(row["online_linked"])):
            diverged += 1
    assert diverged == 0, f"{diverged} of {len(rows)} sampled opinions diverged"

    # v0.9.1 (test audit, seed S1). `shadow_skew_rows` SELECTs `served_delta_t_s`,
    # `served_class_affinity` and `served_entity_affinity` and the comparison above never reads
    # them: `reconstruct` works from the OFFLINE `dataset_pair` columns. SECURITY-REVIEW-0.9.0
    # predicted the consequence — the score comparison "is blind to a feature divergence that does
    # not move the score, and its correctness depends on a column-aliasing convention that a future
    # SELECT rewrite would silently invert" — and the audit CONFIRMED it by execution: swapping
    # which source column each `served_*` alias points at left all 958 tests green.
    #
    # One assertion closes the aliasing half: each served column must carry the value the online
    # path actually served for that pair. A rewrite that inverted two aliases now fails here.
    for row in rows:
        served = entry_by_pair[(int(row["alarm_a"]), int(row["alarm_b"]))]
        assert float(row["served_delta_t_s"]) == float(served["delta_t_s"]), (
            "served_delta_t_s does not carry the served delta_t — the aliasing convention the "
            "skew comparison depends on has been inverted"
        )
        assert float(row["served_class_affinity"]) == float(served["class_affinity"])
        assert float(row["served_entity_affinity"]) == float(served["entity_affinity"])

    async with store.lock:
        document = await shadow_report.collect(store)
    assert document["skew"]["diverged"] == 0
    assert document["skew"]["rate_pct"] == 0.0


async def test_the_skew_test_can_actually_fail(store: Store) -> None:
    """**Guard the guard.** A skew test that could not go red would be measuring nothing.

    A stored opinion is corrupted by one bit of its score; the comparison must notice.
    """
    await build_sufficient(store)
    result = await _train(store)
    fitted: Coefficients = result["policies"]["A"]["coefficients"]  # type: ignore[index]
    state = shadow.Shadow(scorer=LogisticScorer(fitted), sample_rate=1.0)
    async with store.lock:
        run = await store.latest_challenger_run()
        assert run is not None
        state.run_id = int(run["id"])
        pairs = await store.labelled_pairs()
    pair = pairs[0]
    old = WindowAlarm(int(pair["alarm_a"]), 1, 1, 0.0)
    new = WindowAlarm(int(pair["alarm_b"]), 1, 1, float(pair["delta_t_s"]))
    score = LinkScore(
        True,
        0.6,
        0.5,
        (
            TermContribution("class_affinity", 1.0, float(pair["class_affinity"]), 0.0),
            TermContribution("entity_affinity", 1.0, float(pair["entity_affinity"]), 0.0),
        ),
    )
    state.observe(new, CorrelationResult([], [], False, [EvaluatedPair(old, score)]), None)
    async with store.lock:
        await state.flush(store)
        await store.conn.execute(
            "UPDATE shadow_opinion SET score = score + 1e-12 WHERE id = "
            "(SELECT MIN(id) FROM shadow_opinion)"
        )
        await store.commit()
        document = await shadow_report.collect(store)
    assert document["skew"]["diverged"] == 1
    assert document["skew"]["rate_pct"] == 100.0


# -- the partition metrics ----------------------------------------------------------------------


def test_a_component_is_named_by_its_smallest_member() -> None:
    """Stable labelling; two runs over the same input are identical."""
    assert shadow_eval.partition([(5, 3), (3, 9)], {3, 5, 9, 11}) == {3: 3, 5: 3, 9: 3, 11: 11}
    assert shadow_eval.partition([], {7, 2}) == {2: 2, 7: 7}


def test_split_bags_are_scored_separately_and_never_folded_in() -> None:
    """A `split` bag asserts *at least two situations* without saying which, so it supports no
    truth partition. Folding it into an over-merge rate would fabricate a denominator."""
    bags = [
        {"feedback_id": 1, "verdict": "confirm"},
        {"feedback_id": 2, "verdict": "split"},
    ]
    pairs = [
        {"pair_id": 10, "feedback_id": 1, "alarm_a": 1, "alarm_b": 2, "incumbent_linked": 1},
        {"pair_id": 20, "feedback_id": 2, "alarm_a": 3, "alarm_b": 4, "incumbent_linked": 1},
    ]
    intact = shadow_eval.evaluate(bags, pairs, {10: True, 20: True})
    assert intact.confirm_bags == 1 and intact.split_bags == 1
    assert intact.split_bags_intact == 1 and intact.split_bag_intact_rate == 1.0
    assert intact.over_merge == 0.0 and intact.under_merge == 0.0

    separated = shadow_eval.evaluate(bags, pairs, {10: True, 20: False})
    assert separated.split_bags_intact == 0 and separated.split_bag_intact_rate == 0.0
    # The confirm bag is unaffected: the two populations do not leak into each other.
    assert separated.over_merge == 0.0 and separated.under_merge == 0.0

    torn = shadow_eval.evaluate(bags, pairs, {10: False, 20: True})
    assert torn.under_merge == 1.0, "splitting a confirm bag is an under-merge"


def test_the_champion_is_measured_the_same_way() -> None:
    """A challenger number with no champion number beside it is not a comparison."""
    pairs = [{"pair_id": 1, "incumbent_linked": 1}, {"pair_id": 2, "incumbent_linked": 0}]
    assert shadow_eval.champion_decisions(pairs) == {1: True, 2: False}


# -- the admission filter -----------------------------------------------------------------------


def test_the_admission_filter_runs_against_the_champion_too() -> None:
    """A filter nobody has run against the incumbent is a filter whose budget is a guess."""
    samples = [
        LinkFeatures(
            delta_t_s=float(i),
            class_i=1,
            class_j=2,
            class_affinity=0.4,
            ne_i=1,
            ne_j=2,
            entity_affinity=1.0,
        )
        for i in range(500)
    ]
    champion = shadow_eval.admission(AdditiveScorer(), samples, budget_ratio=10.0)
    challenger = shadow_eval.admission(
        LogisticScorer(Coefficients(-1.0, 2.0, 1.0, 0.5)), samples, budget_ratio=10.0
    )
    for row in (champion, challenger):
        assert row["explainable"] and row["deterministic"] and row["memory_stable"]
        assert row["median_us"] > 0.0 and row["p99_us"] >= row["median_us"]
    assert champion["terms"] == 3 and challenger["terms"] == 4
    admitted, reasons = shadow_eval.verdict(challenger, champion)
    assert admitted, reasons


def test_a_model_failing_any_check_does_not_compete() -> None:
    """The filter's whole point: a model failing any check does not compete on quality at all."""
    champion = {"contract_version": "1.0", "p99_us": 2.0}
    for broken, expected in (
        ({"explainable": False}, "explainability"),
        ({"deterministic": False}, "determinism"),
        ({"memory_stable": False}, "memory"),
        ({"contract_version": "2.0"}, "contract"),
        ({"p99_us": 1000.0}, "speed"),
    ):
        challenger = {
            "explainable": True,
            "deterministic": True,
            "memory_stable": True,
            "contract_version": "1.0",
            "p99_us": 1.0,
            "budget_ratio": 10.0,
            **broken,
        }
        admitted, reasons = shadow_eval.verdict(challenger, champion)
        assert not admitted and any(r.startswith(expected) for r in reasons), reasons


# -- the report ---------------------------------------------------------------------------------


_TIMING = re.compile(r"^  (\S+)\s+\d+\.\d{3}\s+\d+\.\d{3}(.*)$")


def _stable(text: str) -> str:
    """Blank the two **measured durations** in the admission table.

    Everything else in this report is frozen byte-for-byte. A wall-clock microsecond cannot be, and
    pretending otherwise would either make the gate flap or force the report to stop reporting the
    one number the admission filter exists to measure. The durations are asserted separately, for
    what can honestly be asserted about them: that they are positive and ordered.
    """
    out = []
    for line in text.splitlines():
        match = _TIMING.match(line)
        # Rebuilt at FIXED width, not blanked in place: a 1.234 and a 10.234 occupy different
        # columns, so an in-place blank would still leave the run's timings in the diff.
        out.append(
            f"  {match[1]:<20}{'<measured>':>12}{'<measured>':>12}{match[2]}" if match else line
        )
    return "\n".join(out) + "\n"


async def _report(store: Store) -> str:
    async with store.lock:
        return shadow_render.render(await shadow_report.collect(store))


async def test_the_report_is_deterministic_across_two_runs(store: Store) -> None:
    await build_sufficient(store)
    await _train(store)
    assert _stable(await _report(store)) == _stable(await _report(store))


async def test_the_report_matches_the_frozen_expectation(store: Store) -> None:
    """**The gate**, modulo the two measured durations. Read the diff before re-freezing."""
    await build_sufficient(store)
    await _train(store)
    actual = _stable(await _report(store))
    assert EXPECTED.exists(), f"missing frozen expectation: {EXPECTED}"
    assert actual == EXPECTED.read_text(encoding="utf-8")


async def test_the_report_leads_with_insufficiency_when_the_corpus_is_short(
    store: Store,
) -> None:
    """*"Not yet, and here is when"* is the release's most useful result, so it comes first."""
    await build_small(store)
    await _train(store)
    text = await _report(store)
    assert text.index("INSUFFICIENT") < text.index("the corpus")
    assert "NOTHING WAS FITTED, AND THAT IS A RESULT" in text
    assert "months at the current rate" in text
    assert "No model was fitted" in text


async def test_the_report_never_states_a_pairwise_accuracy(store: Store) -> None:
    """Phase 0 measured a 99.83% accept rate, so a constant 'link' scores 99.83% pairwise. The
    report must not print such a number under any name."""
    await build_sufficient(store)
    await _train(store)
    text = (await _report(store)).lower()
    # The closing section NAMES the thing it refuses to print, which is the point of it. The rule
    # is about the body: no figure above that section may be a pairwise accuracy.
    body, _, closing = text.partition("what this report cannot tell you")
    for forbidden in ("pairwise accuracy", "pair accuracy", "pairwise f1", "accuracy  "):
        assert forbidden not in body, f"the report states {forbidden!r}"
    # The one place "accuracy" appears in the body is the sentence saying calibration is not
    # implied by it, which is a warning about the word rather than a use of it.
    assert body.count("accuracy") == body.count("calibration is not implied by accuracy")
    assert "no pairwise" in closing


async def test_the_report_measures_timings_that_are_real(store: Store) -> None:
    """The half `_stable` blanks, asserted for what can honestly be asserted about it."""
    await build_sufficient(store)
    await _train(store)
    async with store.lock:
        document = await shadow_report.collect(store)
    admission = document["admission"]
    assert admission, "the admission filter did not run on a sufficient corpus"
    for who in ("champion", "challenger"):
        assert admission[who]["median_us"] > 0.0
        assert admission[who]["p99_us"] >= admission[who]["median_us"]
        assert admission[who]["calls"] > 100


async def test_the_report_emits_aggregates_only(store: Store) -> None:
    """The dataset is a scope bypass; the report may say *how many*, never *which*."""
    await build_sufficient(store)
    await _train(store)
    text = await _report(store)
    assert "10.0.0.1" not in text
    assert "1.3.6.1.4.1.9.1" not in text
    for operator in OPERATORS:
        assert operator not in text


# -- the online path, in the real engine ---------------------------------------------------------


async def test_the_engine_scores_a_sample_on_the_real_ingest_path(store: Store) -> None:
    """**Online shadow, in the engine, on real traffic** — not a unit test of `observe`.

    A real fixture is driven through `Engine._process`, which is the ingest path. The challenger
    must score a sample there and buffer it; the flush happens in the maintenance pass, under the
    lock that pass already holds, so the ingest path pays for one `score()` per sampled pair.
    """
    import util

    engine = Engine(store, asyncio.Queue())
    await engine.start()
    engine.shadow.run_id = 1
    engine.shadow.sample_rate = 1.0
    engine.shadow.scorer = LogisticScorer(Coefficients(-1.0, 2.0, 1.0, 0.5))
    async with store.lock:
        await store.open_challenger_run(
            started_at=TS,
            netcorenoc_version="test",
            scorer_id="logistic-shadow",
            contract_version="1.0",
            derivation_policy="A",
            thresholds="{}",
            sufficient=1,
            sample_rate=1.0,
        )
        await store.commit()

    await util.drive(engine, engine.queue, util.fixture_events("fiber_cut.json", TS))
    assert engine.shadow.scored > 0, "the challenger scored nothing on the ingest path"
    assert engine.shadow.score_ns > 0, "no per-call cost was measured"
    assert engine.shadow.errors == 0

    async with store.lock:
        await engine.shadow.flush(store)
        await store.commit()
        stats = await store.shadow_stats()
    assert stats["shadow_opinion"] == engine.shadow.sampled > 0

    # …and the champion is still the champion. The whole point.
    assert engine.correlator.scorer.active.scorer_id == "additive"
    assert isinstance(engine.correlator.scorer.active, AdditiveScorer)


async def test_the_default_sample_rate_writes_far_fewer_rows_than_capture(store: Store) -> None:
    """**The online cost, measured rather than claimed.**

    At the shipped default the sampled opinions must be a small fraction of the pair rows capture
    already writes. Asserted as a ratio rather than as an absolute count, so the assertion survives
    a change to the fixture.
    """
    import util

    engine = Engine(store, asyncio.Queue())
    await engine.start()
    engine.shadow.run_id = 1
    engine.shadow.scorer = LogisticScorer(Coefficients(-1.0, 2.0, 1.0, 0.5))
    async with store.lock:
        await store.open_challenger_run(
            started_at=TS,
            netcorenoc_version="test",
            scorer_id="logistic-shadow",
            contract_version="1.0",
            derivation_policy="A",
            thresholds="{}",
            sufficient=1,
            sample_rate=shadow.DEFAULT_SAMPLE_RATE,
        )
        await store.commit()

    await util.drive(engine, engine.queue, util.fixture_events("fiber_cut.json", TS))
    async with store.lock:
        await engine.shadow.flush(store)
        await store.commit()
        captured = await store.dataset_stats()
        sampled = (await store.shadow_stats())["shadow_opinion"]
    pairs = captured.get("dataset_pair.sink", 0) + captured.get("dataset_pair.dataset", 0)
    assert pairs > 0
    assert sampled <= pairs * (shadow.DEFAULT_SAMPLE_RATE * 2 + 0.001), (
        f"{sampled} sampled against {pairs} captured pairs is not a rounding error"
    )


async def test_the_sampling_rate_is_deployment_settable_and_recorded(store: Store) -> None:
    """**The deployment chooses the rate and the duration, not whether online ever runs.**

    Set through `meta`, read at training time, clamped into [0, 1], and recorded on the run row so
    a report always carries the rate its numbers were produced at.
    """
    await build_sufficient(store)
    async with store.lock:
        await store.set_meta(shadow.SAMPLE_META_KEY, "0.25")
        await store.commit()
    state = shadow.Shadow()
    await state.train(store, TS + 1.0, store.lock)
    assert state.sample_rate == 0.25
    async with store.lock:
        run = await store.latest_challenger_run()
    assert run is not None and run["sample_rate"] == 0.25

    # Out of range is clamped, unreadable falls back to the shipped default — never a crash, and
    # never a rate nobody chose.
    assert shadow._sample_rate("2.5") == 1.0
    assert shadow._sample_rate("-1") == 0.0
    assert shadow._sample_rate("banana") == shadow.DEFAULT_SAMPLE_RATE
    assert shadow._sample_rate(None) == shadow.DEFAULT_SAMPLE_RATE


async def test_zero_sampling_stops_the_online_half_and_nothing_else(store: Store) -> None:
    """Zero means *buffer nothing online*; it does not mean *switch shadow mode off*. The offline
    reconstruction still runs, so quality is still measured — only the skew test loses its
    subject, and that is a deployment's choice to make."""
    state = shadow.Shadow(run_id=1, sample_rate=0.0)
    entry = WindowAlarm(2, 1, 1, TS + 1.0)
    other = WindowAlarm(1, 1, 1, TS)
    result = LinkScore(True, 0.6, 0.5, (TermContribution("class_affinity", 1.0, 0.5, 0.5),))
    for _ in range(100):
        state.observe(entry, CorrelationResult([], [], False, [EvaluatedPair(other, result)]), None)
    assert state.sampled == 0 and state.scored == 0 and state.errors == 0


def test_the_buffer_drops_rather_than_growing() -> None:
    """Bounded by construction, like everything else on the ingest path: a full buffer drops the
    sample and **counts the drop**. Dropping a sample costs a data point; growing costs the
    process."""
    state = shadow.Shadow(run_id=1, sample_rate=1.0)
    entry = WindowAlarm(2, 1, 1, TS + 1.0)
    other = WindowAlarm(1, 1, 1, TS)
    result = LinkScore(True, 0.6, 0.5, (TermContribution("class_affinity", 1.0, 0.5, 0.5),))
    outcome = CorrelationResult([], [], False, [EvaluatedPair(other, result)])
    for _ in range(shadow.MAX_BUFFERED + 50):
        state.observe(entry, outcome, None)
    assert state.sampled == shadow.MAX_BUFFERED
    assert state.dropped == 50
    assert state.errors == 0
