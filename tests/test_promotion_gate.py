"""The promotion gate and its two refusals (v0.11.0, Phase 5).

**The most likely way this release fails is a promotion path that works and a refusal path nobody
exercised.** So this file is written refusal-first, and the two claims it exists to carry are:

1. **Each refusal is reachable**, with a message that says what the plan's §4 requires it to say.
2. **Neither message is producible by the other's condition** — asserted structurally, not by
   comparing strings.

Plus a trigger test per `INSUFFICIENT_EVIDENCE` condition, **each firing its own and no other**.
"""

from __future__ import annotations

import pytest

from netcorenoc import promotion, seal
from netcorenoc.judge import Judgement, Trigger, Verdict
from netcorenoc.promotion import Metrics, PromotionPathError, Quantity
from netcorenoc.shadow_cv import Interval, Power, power_at
from netcorenoc.store import Store

BASE = 1_700_000_000.0
PLAN = "e011ee6ad2367d44f2ede14cad7b072df598298f91ecc1a405744358b589d449"
HASH = "c" * 64


def interval(rate: float, half_width: float = 0.02, clusters: int = 40) -> Interval:
    return Interval(rate, rate - half_width, rate + half_width, clusters)


def metrics(
    *,
    over: tuple[float, float] = (0.10, 0.30),
    under: tuple[float, float] = (0.10, 0.30),
    intact: tuple[float, float] = (0.10, 0.30),
    respected: tuple[float, float] = (0.90, 0.50),
    half_width: float = 0.02,
) -> Metrics:
    """Four quantities as (challenger, champion). The defaults describe a clear challenger win."""
    return Metrics(
        quantities=(
            Quantity("over_merge_rate", interval(over[0], half_width), interval(over[1])),
            Quantity("under_merge_rate", interval(under[0], half_width), interval(under[1])),
            Quantity("split_bag_intact_rate", interval(intact[0], half_width), interval(intact[1])),
            Quantity(
                "asserted_negative_respected_rate",
                interval(respected[0], half_width),
                interval(respected[1]),
            ),
        )
    )


def sufficient_power() -> Power:
    """An `n` and a difference the corpus can actually resolve."""
    return power_at(400, 0.60)


async def _ratify(store: Store) -> None:
    """Register the plan and cut a seal, so the seal CAN be spent when the gate reaches it."""
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


async def decide(store: Store, **kwargs: object) -> promotion.Decision:
    defaults: dict[str, object] = {
        "floors_met": True,
        "power": sufficient_power(),
        "metrics": metrics(),
        "incident_ids": list(range(1, 41)),
        "params_hash": HASH,
        "plan_sha256": PLAN,
        "release": "0.11.0",
        "now": BASE,
        "smallest_side": 20,
        "asserting_incidents": 40,
    }
    defaults.update(kwargs)
    async with store.lock:
        decision = await promotion.evaluate(store, **defaults)  # type: ignore[arg-type]
        await store.commit()
    return decision


# -- the two refusals are reachable ---------------------------------------------------------


async def test_the_insufficient_evidence_refusal_is_reachable_and_says_what_it_must(
    store: Store,
) -> None:
    """§4: every trigger that fired; the detection threshold at the available `n`; what would have
    to change, `undefined` where the arithmetic does not allow a number."""
    decision = await decide(store, floors_met=False)
    assert decision.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert not decision.may_apply

    message = promotion.refusal_for(decision)
    assert "INSUFFICIENT_EVIDENCE" in message
    assert "FLOOR_UNMET" in message, "the trigger that fired is not named"
    assert "Detection threshold at n =" in message, "the threshold at the available n is missing"
    assert "What would have to change" in message
    assert "undefined" in message, (
        "the projection must print `undefined`: this repository has measured that it has no "
        "labelling-rate data, and printing the harness's rate would be manufacturing one"
    )
    assert "not a finding that the challenger is worse" in message


async def test_the_not_better_refusal_is_reachable_and_says_what_it_must(store: Store) -> None:
    """§4: which of the four quantities produced the verdict, its clustered interval, and the
    champion's number beside it."""
    await _ratify(store)
    decision = await decide(store, metrics=metrics(over=(0.30, 0.10)))
    assert decision.verdict is Verdict.NOT_BETTER, decision.judgement.triggers

    message = promotion.refusal_for(decision)
    assert "NOT_BETTER" in message
    assert "over_merge_rate" in message, "the deciding quantity is not named"
    assert "challenger 0.3000" in message and "champion 0.1000" in message, (
        "the champion's number must stand beside the challenger's"
    )
    assert "[0.2800, 0.3200]" in message, "the clustered interval is missing"
    assert "No composite is computed" in message
    assert "decided against the challenger" in message


# -- THE DISTINCTNESS PROPERTY ---------------------------------------------------------------


def test_neither_refusal_message_is_producible_by_the_others_condition() -> None:
    """**The property this release would fail quietly without.**

    A single `if not decisive or verdict is not BETTER: refuse()` would satisfy a naive reachability
    test and destroy the distinction. Asserted here by handing each refusal the *other's* verdict
    and requiring it to raise — so the guard is a property of the code, not of a string comparison
    that a reworded message would silently break.
    """
    insufficient = Judgement(verdict=Verdict.INSUFFICIENT_EVIDENCE, triggers=(Trigger.FLOOR_UNMET,))
    not_better = Judgement(verdict=Verdict.NOT_BETTER)
    better = Judgement(verdict=Verdict.BETTER)

    # Each path produces its own message.
    assert "INSUFFICIENT_EVIDENCE" in promotion.insufficient_evidence_refusal(insufficient)
    assert "NOT_BETTER" in promotion.not_better_refusal(not_better, metrics())

    # And neither can be reached from the other's condition.
    with pytest.raises(PromotionPathError, match="opposite claims"):
        promotion.insufficient_evidence_refusal(not_better)
    with pytest.raises(PromotionPathError, match="opposite claims"):
        promotion.not_better_refusal(insufficient, metrics())

    # BETTER is not a refusal at all, on either path.
    with pytest.raises(PromotionPathError):
        promotion.insufficient_evidence_refusal(better)
    with pytest.raises(PromotionPathError):
        promotion.not_better_refusal(better, metrics())


def test_the_dispatcher_routes_each_verdict_to_exactly_one_path() -> None:
    """`refusal_for` is a `match` over the three-valued type: no branch is shared by two verdicts,
    and `BETTER` raises rather than borrowing a refusal written for something else."""
    base = {
        "metrics": metrics(),
        "evaluation_run_id": "r",
        "plan_sha256": PLAN,
    }
    insufficient = promotion.Decision(
        judgement=Judgement(verdict=Verdict.INSUFFICIENT_EVIDENCE, triggers=(Trigger.POWER,)),
        **base,  # type: ignore[arg-type]
    )
    not_better = promotion.Decision(
        judgement=Judgement(verdict=Verdict.NOT_BETTER),
        **base,  # type: ignore[arg-type]
    )
    better = promotion.Decision(
        judgement=Judgement(verdict=Verdict.BETTER),
        **base,  # type: ignore[arg-type]
    )

    assert promotion.refusal_for(insufficient).startswith("PROMOTION REFUSED — INSUFFICIENT")
    assert promotion.refusal_for(not_better).startswith("PROMOTION REFUSED — NOT_BETTER")
    with pytest.raises(PromotionPathError, match="not a refusal"):
        promotion.refusal_for(better)

    assert better.may_apply
    assert not insufficient.may_apply
    assert not not_better.may_apply


# -- a trigger test per condition, each firing its own and no other --------------------------

# Each entry is (trigger, the ONE argument that fires it, the EXACT set that must fire). Everything
# else stays at values that fire nothing, so a corpus firing two triggers cannot make a test pass
# for the wrong reason.
#
# TWO TRIGGERS CANNOT BE ISOLATED, AND THAT IS A CONSEQUENCE OF THE PLAN RATHER THAN A DEFECT.
# §3 registers the evaluation order — floors first, power second, seal last, and the seal is spent
# WHEN AND ONLY WHEN the first two pass. So a corpus that fails a floor, or fails the power
# condition, necessarily also leaves the holdout unspent, and `HOLDOUT_UNSPENT` fires with it.
#
# The expected set is therefore written out **exactly** for every case rather than relaxed to "at
# least this trigger". A loose assertion would hide the day an unrelated trigger started co-firing,
# which is the whole thing this test exists to detect.
TRIGGER_CASES: list[tuple[Trigger, dict[str, object], set[Trigger]]] = [
    (Trigger.FLOOR_UNMET, {"floors_met": False}, {Trigger.FLOOR_UNMET, Trigger.HOLDOUT_UNSPENT}),
    (Trigger.THIN_SPLIT, {"smallest_side": 9}, {Trigger.THIN_SPLIT}),
    (Trigger.POWER, {"power": power_at(37, 0.01)}, {Trigger.POWER, Trigger.HOLDOUT_UNSPENT}),
    (Trigger.COVERAGE_EXCLUSIONS, {"coverage_excluded": 1}, {Trigger.COVERAGE_EXCLUSIONS}),
    (Trigger.UNKNOWN_POPULATION, {"unknown_rows": 1}, {Trigger.UNKNOWN_POPULATION}),
    (
        Trigger.TRUNCATED_LOAD_BEARING,
        {"truncated_load_bearing": True},
        {Trigger.TRUNCATED_LOAD_BEARING},
    ),
    (Trigger.UNSOUND_CHAINS, {"unsound_chains": 1}, {Trigger.UNSOUND_CHAINS}),
    (
        Trigger.PRE_V080_MERGES,
        {"pre_v080_merges": 5, "asserting_incidents": 10},
        {Trigger.PRE_V080_MERGES},
    ),
]


@pytest.mark.parametrize(
    ("trigger", "override", "expected"),
    TRIGGER_CASES,
    ids=[case[0].name for case in TRIGGER_CASES],
)
async def test_each_trigger_fires_on_its_own_condition_and_no_other(
    store: Store, trigger: Trigger, override: dict[str, object], expected: set[Trigger]
) -> None:
    """One corpus per trigger, constructed to fire **it and nothing the plan does not require**."""
    await _ratify(store)
    decision = await decide(store, **override)
    assert decision.verdict is Verdict.INSUFFICIENT_EVIDENCE
    fired = set(decision.judgement.triggers)
    assert trigger in fired, f"{trigger.name} did not fire on its own condition"
    assert fired == expected, (
        f"{trigger.name}'s fixture fired {sorted(t.name for t in fired)}, expected "
        f"{sorted(t.name for t in expected)}"
    )
    if fired != {trigger}:
        assert fired - {trigger} == {Trigger.HOLDOUT_UNSPENT}, (
            "the only trigger permitted to accompany another is HOLDOUT_UNSPENT, and only because "
            "the plan's §3 order makes it a CONSEQUENCE of the floors or the power failing"
        )
    assert trigger.name in promotion.refusal_for(decision)


async def test_holdout_unspent_fires_when_the_seal_cannot_be_read(store: Store) -> None:
    """`HOLDOUT_UNSPENT` is the one trigger that cannot be isolated by an argument, because the
    plan's §3 makes it a **consequence** of the evaluation order rather than an input.

    With no seal constructed, the floors and the power condition pass, the gate reaches the seal,
    and the read is refused — so exactly this trigger fires.
    """
    decision = await decide(store)  # no _ratify: there is no seal to read
    assert decision.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert set(decision.judgement.triggers) == {Trigger.HOLDOUT_UNSPENT}
    assert any("no seal has been constructed" in reason for reason in decision.unavailable)


def test_every_registered_trigger_has_a_test() -> None:
    """Guard the guard. §6.2 registers a **closed** list, and a release that could add a reason by
    writing a new string could also drop one by not writing it."""
    covered = {case[0] for case in TRIGGER_CASES} | {Trigger.HOLDOUT_UNSPENT}
    missing = sorted(t.name for t in Trigger if t not in covered)
    assert not missing, f"triggers with no test that fires them individually: {missing}"


# -- the seal policy: floors, then power, then the seal ---------------------------------------


async def test_the_seal_is_not_read_when_the_floors_fail(store: Store) -> None:
    """**The plan's §3, and the release's headline discipline.**

    Floors first, power second, seal last. The seal is spent when and only when the first two pass.
    """
    await _ratify(store)
    before = await store.query_count()
    decision = await decide(store, floors_met=False)
    after = await store.query_count()

    assert decision.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert after == before == 0, "the seal was read on a corpus that had already failed the floors"
    assert any("not read" in reason for reason in decision.unavailable)


async def test_the_seal_is_not_read_when_the_power_condition_fails(store: Store) -> None:
    """The second half of the order: floors met, power fails, seal still unread."""
    await _ratify(store)
    decision = await decide(store, power=power_at(37, 0.01))
    assert Trigger.POWER in decision.judgement.triggers
    assert await store.query_count() == 0


async def test_the_seal_is_spent_exactly_once_when_both_pass(store: Store) -> None:
    """And when both pass it **is** spent — otherwise `HOLDOUT_UNSPENT` would be decorative, which
    is the outcome the plan's §3 refuses."""
    await _ratify(store)
    assert await store.query_count() == 0
    decision = await decide(store)
    assert await store.query_count() == 1, "the seal was not spent when the gate reached it"
    assert decision.judgement.query_count == 1, (
        "the recorded count is the one AT decision time — after this decision's own read, because "
        "§6.3 requires the count to read 1 thereafter and be printed beside every holdout number "
        "the project publishes. Recording the pre-read 0 would let a promotion cite a count that "
        "was already stale by the time the row was written."
    )
    assert Trigger.HOLDOUT_UNSPENT not in decision.judgement.triggers


# -- BETTER, on a purpose-built sufficient fixture --------------------------------------------


async def test_a_sufficient_fixture_produces_a_real_better_verdict(store: Store) -> None:
    """**The machinery proved where the corpus cannot exercise it.**

    On the real corpus this gate always refuses, so without this test the applied path would ship
    unexercised — which is the second half of the failure the build prompt names, and the one a
    build is tempted to skip.
    """
    await _ratify(store)
    decision = await decide(store)
    assert decision.verdict is Verdict.BETTER, decision.judgement.triggers
    assert decision.may_apply
    assert decision.judgement.triggers == ()
    with pytest.raises(PromotionPathError, match="not a refusal"):
        promotion.refusal_for(decision)


# -- §6.5, §6.6, §6.7: the three ways a good-looking challenger is still NOT_BETTER ------------


async def test_better_on_one_headline_rate_and_worse_on_the_other_is_not_better(
    store: Store,
) -> None:
    """§6.5. **No composite is computed** — the two are not traded off against each other."""
    await _ratify(store)
    decision = await decide(store, metrics=metrics(under=(0.40, 0.10)))
    assert decision.verdict is Verdict.NOT_BETTER
    assert "under_merge_rate" in promotion.refusal_for(decision)


async def test_a_model_that_buries_splits_is_not_better_regardless_of_the_other_numbers(
    store: Store,
) -> None:
    """§6.6. v0.9.0's measured policy-B failure repeating; it produces the verdict on its own."""
    await _ratify(store)
    decision = await decide(store, metrics=metrics(intact=(0.90, 0.10)))
    assert decision.verdict is Verdict.NOT_BETTER
    message = promotion.refusal_for(decision)
    assert "split_bag_intact_rate" in message
    assert "§7.6" in message, "the adverse note must travel with the refusal"


async def test_a_model_that_separates_everything_respects_assertions_for_free(store: Store) -> None:
    """§6.7. A near-1.0 respected rate beside a high under-merge rate is not a virtue — the two
    numbers must be read together, and the refusal says so."""
    await _ratify(store)
    decision = await decide(store, metrics=metrics(respected=(0.99, 0.50), under=(0.40, 0.10)))
    assert decision.verdict is Verdict.NOT_BETTER
    assert "§7.7" in promotion.refusal_for(decision)


def test_the_metrics_object_offers_no_composite() -> None:
    """Four named quantities, never composed. Asserted structurally, because a composite added
    later would be a one-line property and would reverse the plan's §1 under a different name."""
    banned = {"score", "quality", "composite", "index", "total", "overall", "combined"}
    present = {name for name in dir(Metrics) if not name.startswith("_")}
    assert not (present & banned), f"Metrics grew a composite: {sorted(present & banned)}"
    assert set(promotion.QUANTITY_NAMES) == {q.name for q in metrics().quantities}


# -- the third refusal: the evidence permits it, and the artefact cannot be traced -------------


def test_a_promotion_with_no_challenger_run_is_refused_even_when_the_verdict_permits_it() -> None:
    """**The schema cannot enforce this and the verdict does not cover it.**

    `model_version.challenger_run_id` is legitimately `NULL` for an additive artefact registered
    from hand-chosen parameters — registering is not promoting. So the rule that a *promotion* must
    name the run that produced its coefficients lives in code, and it is a refusal **with a reason**
    rather than a silent skip.

    Kept distinct from both of the plan's §4 refusals on purpose: this is neither *"the corpus
    cannot decide"* nor *"the corpus decided against"*. The evidence is sufficient and the
    challenger won; the **provenance** is missing.
    """
    better = promotion.Decision(
        judgement=Judgement(verdict=Verdict.BETTER),
        metrics=metrics(),
        evaluation_run_id="r",
        plan_sha256=PLAN,
    )
    message = promotion.missing_run_refusal(better, 7)
    assert "no challenger run" in message
    assert "model version 7" in message
    assert "retune with a better story" in message
    # It must not read as either of the other two.
    assert "INSUFFICIENT_EVIDENCE" not in message
    assert "NOT_BETTER" not in message


def test_the_missing_run_refusal_cannot_describe_a_verdict_that_never_permitted_a_promotion() -> (
    None
):
    """The same guard its two siblings carry: it cannot become a way of reaching a refusal message
    the verdict did not earn."""
    for verdict in (Verdict.INSUFFICIENT_EVIDENCE, Verdict.NOT_BETTER):
        decision = promotion.Decision(
            judgement=Judgement(verdict=verdict),
            metrics=metrics(),
            evaluation_run_id="r",
            plan_sha256=PLAN,
        )
        with pytest.raises(PromotionPathError, match="applies only where"):
            promotion.missing_run_refusal(decision, 7)


# -- F52: the asserting-bag predicate itself ---------------------------------------------------


async def test_f52_a_bag_with_no_reconciled_mark_is_not_an_asserting_bag(store: Store) -> None:
    """**F52.** `asserting_bag_rows` counts bags with `excluded_reconciled >= 1`, and until this
    test nothing guarded the `1`.

    Measured during Phase 6: widening the predicate to `>= 0` left **all 1 296 tests green**. The
    consequence is not cosmetic — `asserting_bags` is `PREREGISTRATION-0.10.0.md` §2.2's PRIMARY
    floor, and a predicate that counted bags asserting **nothing** would let that floor be cleared
    by evidence that does not exist. It is F46 in a new place: *a threshold counting a quantity
    other than the one it names.*

    On the current corpus the verdict would not change (13 < 50 either way), which is exactly why
    no existing test noticed — the release's headline outcome is insensitive to it, and a larger
    corpus would not be.

    **The control comes first**: a bag that DOES carry a reconciled mark must be counted, or this
    test would pass against a predicate that counted nothing at all.
    """
    async with store.lock:
        # One situation per bag: `feedback` is UNIQUE on (situation_id, verdict), which is itself
        # the "a repeat is a no-op" rule from DECISIONS #68 and not something to work around.
        for marks, verdict in ((2, "split"), (0, "split"), (0, "confirm")):
            sid = await store.create_situation(BASE, None)
            await store.conn.execute(
                "INSERT INTO feedback (situation_id, verdict, created_at, principal_ref, "
                "coverage, member_count, excluded_reconciled, scope_redacted_members, "
                "capture_provenance) VALUES (?, ?, ?, 'alice', 'full', 5, ?, 0, 'current')",
                (sid, verdict, BASE, marks),
            )
        await store.commit()

    rows = await store.asserting_bag_rows()
    # THE CONTROL: the one bag that asserts something is counted.
    assert len(rows) == 1, f"expected exactly the marked split bag, got {len(rows)}"
    assert int(rows[0]["excluded_reconciled"]) == 2
    # THE GUARD: neither unmarked bag is, and the `split` one is the case that matters — a `split`
    # with no marks is a real and common label that asserts no PAIR.
    assert all(int(row["excluded_reconciled"]) >= 1 for row in rows), (
        "a bag with no reconciled mark was counted as an asserting bag: `asserting_bags` is the "
        "PRIMARY floor, and this predicate is what decides what counts toward it"
    )
