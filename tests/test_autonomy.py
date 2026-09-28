"""Autonomy (ADR #412): graded, attributed, explained, self-suspending — through the real engine.

Two of Part VIII's injections live here, each with its control:

* *autonomy staying on after the threshold is breached* —
  `test_autonomy_stops_itself_below_the_floor` against `test_agreement_above_the_floor_keeps_it_on`;
* *an autonomous decision without an explanation* — `test_every_act_is_attributed_and_explained`,
  and the schema's own refusal in `test_the_schema_refuses_an_unexplained_act`.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from netcorenoc.api.livestats import live_stats
from netcorenoc.crosscutting.shaping import UNRESTRICTED
from netcorenoc.engine.dataset import gestures
from netcorenoc.engine.operate import autonomy
from netcorenoc.engine.operate.engine import Engine
from netcorenoc.store import Store

from modelutil import T, ingest, trap

ON = {
    "grouping": 1,
    "naming": 1,
    "closing": 0,
    "severity": 1,
    "agreement_floor": 0.8,
    "window": 10,
    "min_judged": 5,
    "confidence_floor": 0.9,
}
LATER = T + autonomy.SETTLE_S + 10.0


async def _engine(store: Store) -> Engine:
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    return engine


async def _one_situation(engine: Engine, store: Store) -> int:
    oid = "1.3.6.1.4.1.1271.2.1.1"
    await ingest(
        engine, store, [trap("10.0.0.1", oid, "a", T), trap("10.0.0.1", oid, "b", T + 2.0)]
    )
    cur = await store.conn.execute("SELECT id FROM situation")
    rows = list(await cur.fetchall())
    assert len(rows) == 1
    return int(rows[0][0])


async def _switch(store: Store, **overrides: float) -> None:
    async with store.lock:
        await store.set_autonomy({**ON, **overrides}, "admin", T, "testing autonomy")
        await store.commit()


async def _sweep(engine: Engine, store: Store, now: float) -> None:
    async with store.lock:
        await autonomy.sweep(engine, now)
        await store.commit()


async def test_off_by_default_and_nothing_happens(store: Store, test_model: object) -> None:
    engine = await _engine(store)
    await _one_situation(engine, store)
    await _sweep(engine, store, LATER)
    assert await store.autonomy_decisions() == []
    status = await autonomy.status(engine)
    assert not status["active"] and not status["suspended"]


async def test_every_act_is_attributed_and_explained(store: Store, test_model: object) -> None:
    engine = await _engine(store)
    sid = await _one_situation(engine, store)
    await _switch(store)
    await _sweep(engine, store, LATER)
    rows = await store.autonomy_decisions(situation_id=sid)
    grades = {r["grade"] for r in rows}
    assert {"grouping", "naming"} <= grades, grades
    for row in rows:
        assert row["decider"] == engine.decider_ref and row["decider"].startswith("shipped:")
        explanation = row["explanation"]
        assert isinstance(explanation, dict) and explanation, f"{row['grade']} has no explanation"
    grouping = next(r for r in rows if r["grade"] == "grouping")
    assert grouping["confidence"] >= ON["confidence_floor"]
    weakest = grouping["explanation"]["weakest"]
    assert weakest["terms"], "the weakest link's largest terms are the explanation"
    assert {name for name, _ in weakest["terms"]} <= {"dt", "same_ne", "same_class", "oid_arcs"}
    cur = await store.conn.execute(
        "SELECT status, model_name, operator_name FROM situation WHERE id=?", (sid,)
    )
    found = await cur.fetchone()
    assert found is not None
    status, name, operator_name = found
    assert status == "open" and name, "accepted and named"
    assert operator_name is None, "the model wrote the operator's column"
    # A second pass acts no further: each grade acts once per situation.
    await _sweep(engine, store, LATER + 60.0)
    assert len(await store.autonomy_decisions(situation_id=sid)) == len(rows)


async def test_the_schema_refuses_an_unexplained_act(store: Store) -> None:
    """The CHECK itself, on a real situation, so no other constraint can be what refuses it."""
    insert = (
        "INSERT INTO autonomy_decision (situation_id, grade, action, decider, confidence, "
        "explanation, at) VALUES (?, 'naming', 'named', 'shipped:x', 0.9, ?, 1.0)"
    )
    async with store.lock:
        sid = await store.create_situation(T)
        await store.conn.execute(insert, (sid, '{"why":"control"}'))  # the control is accepted
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            await store.conn.execute(insert, (sid, "{}"))


async def test_the_formula_never_acts(store: Store, test_model: object) -> None:
    async with store.lock:
        await store.set_decider_mode("additive", "admin", T, "the formula decides")
        await store.commit()
    engine = await _engine(store)
    await _one_situation(engine, store)
    await _switch(store)
    await _sweep(engine, store, LATER)
    assert await store.autonomy_decisions() == [], (
        "the formula has no probability to be confident with"
    )


async def _judged(store: Store, sid: int, verdicts: list[str]) -> None:
    async with store.lock:
        for k, verdict in enumerate(verdicts):
            did = await store.add_autonomy_decision(
                situation_id=sid,
                grade="naming",
                action="named",
                decider="shipped:test",
                confidence=0.95,
                explanation={"test": k},
                at=T + k,
            )
            await store.judge_autonomy_decision(did, verdict, T + 100 + k, "ana", "test")
        await store.commit()


async def test_autonomy_stops_itself_below_the_floor(store: Store, test_model: object) -> None:
    """Part VIII: *autonomy staying on after the threshold is breached* must be red."""
    engine = await _engine(store)
    sid = await _one_situation(engine, store)
    await _switch(store)
    await _judged(store, sid, ["disagreed"] * 3 + ["agreed"] * 3)  # 50 % < 80 %, 6 >= 5 judged
    await _sweep(engine, store, LATER)
    status = await autonomy.status(engine)
    assert not status["active"], "autonomy stayed on below its agreement floor"
    assert status["suspended"] and status["set_by"] == "autonomy"
    assert "0.50" in status["reason"] or "50" in status["reason"], status["reason"]
    async with store.lock:
        stats = await live_stats(store, engine, UNRESTRICTED, lambda: [], None)
    assert stats["autonomy"] == {"active": False, "grades": [], "suspended": True}
    assert any(w.startswith("Autonomy stopped itself") for w in stats["warnings"])
    # Suspended, it acts no more.
    before = len(await store.autonomy_decisions())
    await _sweep(engine, store, LATER + 600.0)
    assert len(await store.autonomy_decisions()) == before


async def test_agreement_above_the_floor_keeps_it_on(store: Store, test_model: object) -> None:
    """The control: the same history at 5 of 6 agreed stays on."""
    engine = await _engine(store)
    sid = await _one_situation(engine, store)
    await _switch(store)
    await _judged(store, sid, ["disagreed"] + ["agreed"] * 5)
    await _sweep(engine, store, LATER)
    assert (await autonomy.status(engine))["active"]


async def test_fewer_judged_than_the_minimum_cannot_stop_it(
    store: Store, test_model: object
) -> None:
    engine = await _engine(store)
    sid = await _one_situation(engine, store)
    await _switch(store)
    await _judged(store, sid, ["disagreed"] * 4)  # 0 % but 4 < 5 judged
    await _sweep(engine, store, LATER)
    assert (await autonomy.status(engine))["active"]


async def test_an_operator_rename_is_a_disagreement(store: Store, test_model: object) -> None:
    engine = await _engine(store)
    sid = await _one_situation(engine, store)
    await _switch(store, grouping=0, severity=0)
    await _sweep(engine, store, LATER)
    async with store.lock:
        subject = await gestures.snapshot(store, sid)
        await gestures.record(
            store,
            gestures.Gesture(
                kind="rename", situation_id=sid, at=LATER + 5.0, actor="ana", role="editor"
            ),
            subject,
        )
        await store.commit()
    await _sweep(engine, store, LATER + 30.0)
    naming = next(
        r for r in await store.autonomy_decisions(situation_id=sid) if r["grade"] == "naming"
    )
    assert naming["verdict"] == "disagreed" and naming["verdict_by"] == "ana"
    assert json.dumps(naming["explanation"])  # still explained after judgement
