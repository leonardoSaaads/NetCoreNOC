"""The champion-agreement gate (v0.9.0, Workstream 1).

**A gate, not a unit test**, in the mould of `tests/test_bias.py`: the rendered report is compared
**byte for byte** against a frozen expectation built from a deterministic fixture, so it goes red
the day the labelled corpus changes shape.

The fixture is deliberately richer than the bias report's, because this report has to survive cuts
the bias report does not make. It carries **twelve incidents** — one more than the bootstrap's
refusal threshold, so the clustered interval is exercised rather than skipped — bags in every size
bucket, bags that are mixed / uniform-accept / uniform-reject / pairless, storm and quiet and
partly-storm bags, a scoped label, three named operators and one unattributed, and a
`legacy_capture` row that must never be averaged into the headline.

Everything derives from `TS`. No wall clock anywhere.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

from netcorenoc import agreement, agreement_report
from netcorenoc.labels import LabelScope
from netcorenoc.main import Engine
from netcorenoc.rootcause import Member
from netcorenoc.store import Store

EXPECTED = Path(__file__).parent / "fixtures" / "agreement-report.txt"
TS = 1_000_000.0

# (members, verdict, operator, storm pairs, accepted pairs, restricted)
# Twelve situations, chosen so every cut has at least two populated cells.
PLAN: list[tuple[int, str, str | None, str, str, bool]] = [
    (2, "confirm", "alice", "quiet", "all", False),
    (2, "split", "alice", "quiet", "none", False),
    (3, "confirm", "alice", "quiet", "mixed", False),
    (4, "split", "bob", "storm", "all", False),
    (5, "confirm", "bob", "storm", "all", False),
    (3, "confirm", "bob", "partly", "mixed", True),
    (7, "confirm", "carol", "storm", "all", False),
    (12, "confirm", "carol", "storm", "all", False),
    (3, "split", "carol", "quiet", "mixed", True),
    (1, "confirm", None, "quiet", "none", False),
    (60, "confirm", "alice", "storm", "all", False),
    (2, "split", "bob", "quiet", "none", False),
]


async def build_fixture(store: Store) -> None:
    """Twelve labelled incidents with a deliberately awkward spread."""
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
            "INSERT INTO alarm_class (oid, first_seen, last_seen) VALUES "
            "('1.3.6.1.4.1.9.1', ?, ?) RETURNING id",
            (TS, TS),
        )
        klass = int((await cur.fetchone())[0])  # type: ignore[index]

        situations: list[tuple[int, list[int]]] = []
        for index, (members, _v, _op, storm, accepted, _r) in enumerate(PLAN):
            sid = await store.create_situation(TS + index, None)
            alarms: list[int] = []
            for m in range(members):
                cur = await store.conn.execute(
                    "INSERT INTO alarm (device_id, class_id, instance, status, first_seen, "
                    "last_seen, count) VALUES (?, ?, ?, 'active', ?, ?, 1) RETURNING id",
                    (device, klass, f"{index}:{m}", TS + index, TS + index),
                )
                alarm_id = int((await cur.fetchone())[0])  # type: ignore[index]
                alarms.append(alarm_id)
                await store.add_alarm_to_situation(sid, alarm_id)
            situations.append((sid, alarms))

            rows = []
            pair_index = 0
            for i in range(len(alarms)):
                for j in range(i + 1, len(alarms)):
                    linked = {"all": 1, "none": 0}.get(accepted, pair_index % 2)
                    in_storm = {"quiet": 0, "storm": 1}.get(storm, pair_index % 2)
                    rows.append(
                        (
                            run,
                            alarms[i],
                            alarms[j],
                            None,
                            None,
                            sid,
                            1.5,
                            0.25,
                            0.5,
                            3,
                            3,
                            0.61 if linked else 0.19,
                            linked,
                            in_storm,
                            0,
                            TS + index,
                        )
                    )
                    pair_index += 1
            await store.add_pairs(rows)
        await store.commit()

    for (sid, alarms), (_m, _v, _op, _s, _a, _r) in zip(situations, PLAN, strict=True):
        engine.members[sid] = [Member(a, klass, device, TS) for a in alarms]

    async with store.lock:
        for index, ((sid, _alarms), plan) in enumerate(zip(situations, PLAN, strict=True)):
            _members, verdict, operator, _storm, _accepted, restricted = plan
            await engine.apply_feedback(
                sid,
                verdict,
                TS + 1000.0 + index,
                principal_ref=operator,
                role="editor" if operator else None,
                scope=LabelScope(policy_id=7, restricted=True, redacted_members=2)
                if restricted
                else None,
            )
        # A pre-v0.7.5 row, marked as migration 0008 would have marked it. It must appear in the
        # provenance section and NOWHERE else — the headline is `current` only.
        await store.conn.execute(
            "INSERT INTO feedback (situation_id, verdict, created_at, capture_provenance, "
            "member_count, principal_ref) VALUES (?, 'split', ?, 'legacy_capture', 4, 'alice')",
            (situations[0][0], TS + 5.0),
        )
        await store.commit()


async def _report(store: Store) -> str:
    async with store.lock:
        return agreement_report.render(await agreement.collect(store))


async def test_the_report_is_deterministic_across_two_runs(store: Store) -> None:
    """A non-deterministic gate is worthless. This also exercises the bootstrap, which is the
    only part of the report with an RNG in it — and the reason that RNG is written out in
    `agreement.py` rather than taken from `random`."""
    await build_fixture(store)
    assert await _report(store) == await _report(store)


async def test_the_report_matches_the_frozen_expectation(store: Store) -> None:
    """**The gate.** Byte-for-byte against the checked-in expectation.

    If this fails, the labelled corpus changed shape. Read the diff and decide whether its
    *meaning* changed before re-freezing — the same discipline `eval/baselines/v0.2.0.json` has.
    """
    await build_fixture(store)
    actual = await _report(store)
    assert EXPECTED.exists(), f"missing frozen expectation: {EXPECTED}"
    assert actual == EXPECTED.read_text(encoding="utf-8"), (
        "the champion-agreement report changed. Read the diff and decide whether the labelled "
        "corpus's meaning changed before re-freezing tests/fixtures/agreement-report.txt."
    )


async def test_the_gate_goes_red_when_the_corpus_changes(store: Store) -> None:
    """**Guard the guard.** One more label must move the report; a gate that survives that is
    measuring nothing."""
    await build_fixture(store)
    before = await _report(store)
    async with store.lock:
        # A new situation and a new label on it — `feedback` is UNIQUE on
        # (situation_id, verdict), so a genuinely new bag is the honest injection.
        sid = await store.create_situation(TS + 9000.0, None)
        await store.conn.execute(
            "INSERT INTO feedback (situation_id, verdict, created_at, capture_provenance, "
            "member_count, principal_ref) VALUES (?, 'split', ?, 'current', 2, 'dave')",
            (sid, TS + 9999.0),
        )
        await store.commit()
    assert await _report(store) != before


async def test_the_report_never_prints_an_operator_identity(store: Store) -> None:
    """**DECISIONS #120.** The per-operator cut is the spread, not a personnel file.

    Asserted against the rendered text *and* against the measurement document, because the
    guarantee is that the identity never reaches the renderer — a renderer that merely chose not
    to print it would be a convention, not a guarantee.
    """
    await build_fixture(store)
    async with store.lock:
        measurements = await agreement.collect(store)
    text = agreement_report.render(measurements)
    for name in ("alice", "bob", "carol", "dave"):
        assert name not in text, f"the report leaked an operator identity: {name}"
        assert name not in repr(measurements), f"the measurement document carries {name}"
    assert "operator 1" in text and "operator 3" in text
    assert "(unattributed)" in text


async def test_the_report_emits_aggregates_only(store: Store) -> None:
    """The dataset is a scope bypass; the report may say *how many*, never *which*."""
    await build_fixture(store)
    text = await _report(store)
    assert "10.0.0.1" not in text
    assert "1.3.6.1.4.1.9.1" not in text


async def test_legacy_rows_are_never_averaged_into_the_headline(store: Store) -> None:
    """The `legacy_capture` label in the fixture is a `split`. If it reached the headline the
    confirm rate would move, and the provenance section would be the only honest thing left."""
    await build_fixture(store)
    async with store.lock:
        measurements = await agreement.collect(store)
    headline = measurements["headline"]
    assert measurements["bags_all"] == measurements["bags"] + 1
    assert headline.n == measurements["bags"] == len(PLAN)
    provenance = dict(measurements["by_provenance"])
    assert provenance["legacy_capture"].n == 1
    assert provenance["legacy_capture"].confirms == 0
    assert provenance["current"].n == len(PLAN)


async def test_uniform_bags_are_separated_from_mixed_ones(store: Store) -> None:
    """**The strongest cut**, asserted rather than assumed: a uniform bag contained no decision,
    so its confirm says nothing about the scorer's judgement."""
    await build_fixture(store)
    async with store.lock:
        measurements = await agreement.collect(store)
    cut = dict(measurements["by_mixedness"])
    assert set(cut) <= {"mixed", "uniform-accept", "uniform-reject", "no-pairs"}
    assert cut["mixed"].n == 3, "the fixture carries three mixed bags"
    assert cut["uniform-accept"].n == 6
    assert cut["uniform-reject"].n == 2
    # The one-member bag has no pairs at all: it is counted, never dropped.
    assert cut["no-pairs"].n == 1
    assert sum(rate.n for rate in cut.values()) == len(PLAN)


async def test_the_interval_is_refused_below_the_incident_threshold(store: Store) -> None:
    """A bootstrap over a handful of clusters produces an interval that looks like a measurement
    and is an artefact of a handful of numbers. It is refused, not narrowed."""
    bags = [
        agreement.Bag(
            feedback_id=i,
            verdict="confirm" if i % 2 else "split",
            size=2,
            operator="x",
            scope_restricted=False,
            coverage="full",
            provenance="current",
            incident=i,
            pairs=1,
            accepted=1,
            storm_pairs=0,
        )
        for i in range(agreement.MIN_INCIDENTS_FOR_INTERVAL - 1)
    ]
    refused = agreement.rate_of(bags)
    assert refused.lo is None and refused.hi is None
    enough = agreement.rate_of([*bags, bags[0].__class__(**{**vars(bags[0]), "incident": 999})])
    assert enough.lo is not None and enough.hi is not None
    assert enough.lo <= enough.hi


def test_the_report_needs_no_model() -> None:
    """**Workstream 1 is delivered first and independently, and stays that way.**

    Checked against the modules' *imports*, parsed, rather than against their prose — the prose
    says "no training" on purpose, and a substring check over English would forbid the sentence
    that states the property. If either module ever imports the challenger or the training path,
    the deliverable has stopped being the one that needs no model and this goes red.
    """
    forbidden = {"challenger", "training", "shadow"}
    for module in (agreement, agreement_report):
        tree = ast.parse(Path(module.__file__ or "").read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                imported.update(f"{node.module}.{a.name}" for a in node.names)
        leaked = {name for name in imported if any(bad in name for bad in forbidden)}
        assert not leaked, f"{module.__name__} imports {sorted(leaked)}; W1 needs no model"
