"""An alarm's occurrences: a lost clear, an operator close, a reboot, a flaky port (v0.28.0).

Every case is a field scenario driven through the **real** wire encoding, the real parser and the
real engine, with the maintenance sweep run on the scenario's own clock — the shape
`tests/util.fixture_events` exists to keep honest. They are the reproductions of the defects
ADRs #431 to #433 repaired, and each fails on the v0.27.0 engine:

* a repeat after the operator closed the situation vanished into the resolved situation;
* a raise after a lost clear stayed in the old situation, untouched, for good;
* the first X.733 ``cleared`` of an ALARM-MIB-style alarm was ingested as a repeat;
* a severity word that came first became the alarm's instance, so raise and clear never met;
* a cold start kept its situation ``new`` for good;
* an intermittent port opened one situation per bounce.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from netcorenoc.crosscutting.settings import Settings, SettingsError
from netcorenoc.engine.operate.engine import CLEAR_HOLD_S, Engine
from netcorenoc.ingest.receiver import QueueItem, parse_trap
from netcorenoc.store import Store

import authutil
import trap_replay
import util

T0 = 1_700_000_000.0
OTM2 = "1.3.6.1.4.1.1271.2.1.1"
DESCR = "1.3.6.1.4.1.1271.2.1.2.1"
SEVERITY = "1.3.6.1.4.1.1271.2.1.2.2"
PORT = "1.3.6.1.4.1.1271.2.1.2.3"
LINK_DOWN, LINK_UP = "1.3.6.1.6.3.1.1.5.3", "1.3.6.1.6.3.1.1.5.4"
COLD_START = "1.3.6.1.6.3.1.1.5.1"
IF_INDEX = "1.3.6.1.2.1.2.2.1.1"


def trap(at: float, oid: str, varbinds: list[tuple[str, str, str]], source: str = "10.60.0.1"):
    vbs = [{"oid": o, "kind": k, "value": v} for o, k, v in varbinds]
    return parse_trap(source, trap_replay.encode_trap(oid, vbs, "public", 1), T0 + at)


def otm2(at: float, severity: str = "major", port: str = "1/5/1", source: str = "10.60.0.1"):
    """A Ciena-style alarm: one OID for raise and clear, the state in an X.733 severity word."""
    return trap(
        at,
        OTM2,
        [(SEVERITY, "str", severity), (PORT, "str", port), (DESCR, "str", "OTM2 service mismatch")],
        source,
    )


def link(at: float, ifindex: int, up: bool = False, source: str = "10.70.0.1"):
    oid = LINK_UP if up else LINK_DOWN
    return trap(at, oid, [(f"{IF_INDEX}.{ifindex}", "int", str(ifindex))], source)


async def feed(engine: Engine, queue: asyncio.Queue[QueueItem], *items: Any) -> None:
    await util.drive(engine, queue, list(items))


async def situations(store: Store) -> list[dict[str, Any]]:
    """Every situation, oldest first, with its members."""
    rows = sorted(await store.list_situations(None, 200), key=lambda r: int(r["id"]))
    for row in rows:
        row["members"] = await store.situation_member_ids(int(row["id"]))
    return rows


async def alarm_rows(store: Store) -> list[dict[str, Any]]:
    cur = await store.conn.execute("SELECT * FROM alarm ORDER BY id")
    return [dict(r) for r in await cur.fetchall()]


# --- ADR #431: a repeat that is a new occurrence ------------------------------------------------


async def test_a_raise_after_a_lost_clear_opens_a_new_situation(store: Store) -> None:
    """The reported case: OTM2 service mismatch raised, its clear never arrives, raised again three
    hours later. The old situation is concluded — `idle`, with an `idle_close` event naming the
    alarm — and the new occurrence is a new situation an operator will see."""
    engine, queue, _app = await authutil.make_env(store)
    await feed(engine, queue, otm2(0))
    await engine.maintenance(T0 + 3700, 365.0)  # the idle sweep must not resolve a burning one
    first = await situations(store)
    assert [s["status"] for s in first] == ["new"], first

    await feed(engine, queue, otm2(3 * 3600))
    rows = await situations(store)
    assert [(s["status"], s["resolution"]) for s in rows] == [("resolved", "idle"), ("new", None)]
    alarm_id = rows[0]["members"][0]
    assert rows[1]["members"] == [alarm_id], "the new occurrence is the same alarm, in a new place"
    events = await store.conn.execute(
        "SELECT kind, alarm_id, actor, produces_training_rows FROM situation_event "
        "WHERE situation_id=?",
        (rows[0]["id"],),
    )
    assert [tuple(e) for e in await events.fetchall()] == [("idle_close", alarm_id, None, 0)]
    assert engine.sit_of[alarm_id] == rows[1]["id"]


async def test_a_renotification_inside_the_window_stays_a_repeat(store: Store) -> None:
    """A standing alarm re-sent every five minutes for two hours is ONE situation: the silence
    between traps never reaches the window, which is what keeps a chatty device from flooding."""
    engine, queue, _app = await authutil.make_env(store)
    await feed(engine, queue, *[otm2(300 * i) for i in range(25)])
    await engine.maintenance(T0 + 7300, 365.0)
    rows = await situations(store)
    assert [s["status"] for s in rows] == ["new"]
    assert (await alarm_rows(store))[0]["count"] == 25


async def test_another_active_member_keeps_the_repeat_in_its_incident(store: Store) -> None:
    """A situation with a second member still on is a live incident, and a silent member raised
    again belongs to it: concluding it would resolve the other fault out of every live view, which
    is v0.16.2's critical defect (DECISIONS #274)."""
    engine, queue, _app = await authutil.make_env(store)
    await feed(engine, queue, otm2(0, port="1/5/1"), otm2(1, port="1/5/2"))
    rows = await situations(store)
    assert len(rows) == 1 and len(rows[0]["members"]) == 2, "the scenario needs one situation"
    await feed(engine, queue, otm2(3 * 3600, port="1/5/1"))
    assert [s["status"] for s in await situations(store)] == ["new"]


async def test_the_silence_rule_can_be_turned_off(store: Store) -> None:
    engine, queue, _app = await authutil.make_env(store)
    engine.rearm_s = 0.0
    await feed(engine, queue, otm2(0), otm2(3 * 3600))
    assert [s["status"] for s in await situations(store)] == ["new"]


async def test_a_repeat_after_an_operator_close_opens_a_new_situation(store: Store) -> None:
    """The operator closed the situation while the fault was on; the device kept reporting it.
    Until v0.28.0 every later trap was absorbed into the resolved situation and the alarm was in
    no live view. Now the next trap opens a situation — what the next trap after a hand-clear
    has always done — and the one after it is an ordinary repeat there."""
    engine, queue, app = await authutil.make_env(store)
    await feed(engine, queue, otm2(0))
    sid = int((await situations(store))[0]["id"])
    client = await authutil.client_as(app, "editor")
    try:
        assert (await client.post(f"/api/situations/{sid}/close")).status_code == 200
    finally:
        await client.aclose()
    await feed(engine, queue, otm2(600), otm2(660))
    rows = await situations(store)
    assert [(s["status"], s["resolution"]) for s in rows] == [
        ("resolved", "operator"),
        ("new", None),
    ]
    assert rows[1]["members"] == rows[0]["members"]


async def test_a_restart_does_not_orphan_a_live_alarm(store: Store) -> None:
    """The orphan test reads the engine's in-memory membership, so a restart that forgot it
    would turn every repeat into a new situation. `start` rebuilds it from the live situations."""
    engine, queue, _app = await authutil.make_env(store)
    await feed(engine, queue, otm2(0))
    restarted = Engine(store, queue)
    await restarted.start()
    await feed(restarted, queue, otm2(60))
    assert len(await situations(store)) == 1


async def test_a_flap_suppressed_alarm_is_not_rearmed(store: Store) -> None:
    """A fingerprint the flap detector demoted stays demoted: re-arming its repeats would make
    every one an activation and undo the demotion."""
    engine, queue, _app = await authutil.make_env(store)
    await feed(engine, queue, otm2(0))
    alarm_id = (await alarm_rows(store))[0]["id"]
    sid = engine.sit_of[alarm_id]
    engine.forget_situation(sid)  # orphaned, as an operator close leaves it
    engine.flapping.add(("10.60.0.1", OTM2, "1/5/1"))
    await feed(engine, queue, otm2(60))
    assert len(await situations(store)) == 1


# --- ADR #432: the standard's word for "over" ----------------------------------------------------


async def test_an_x733_cleared_ends_the_alarm_on_its_first_clear(store: Store) -> None:
    """ALARM-MIB-style equipment sends one OID for the raise and the clear and says which in the
    severity. The first `cleared` used to be a repeat — the alarm stayed active with the severity
    `cleared` — until two whole cycles had taught the state field."""
    engine, queue, _app = await authutil.make_env(store)
    await feed(engine, queue, otm2(0), otm2(120, severity="cleared"))
    [alarm] = await alarm_rows(store)
    assert (alarm["status"], alarm["severity"], alarm["count"]) == ("cleared", "major", 1)
    await engine.maintenance(T0 + 120 + CLEAR_HOLD_S + 1, 365.0)
    assert [(s["status"], s["resolution"]) for s in await situations(store)] == [
        ("resolved", "self_cleared")
    ]


async def test_a_cleared_with_nothing_to_clear_creates_no_alarm(store: Store) -> None:
    """A clear for a raise this appliance never saw (it started later, the raise was lost) used to
    become an active alarm of severity `cleared` that nothing would ever clear."""
    engine, queue, _app = await authutil.make_env(store)
    await feed(engine, queue, otm2(0, severity="cleared"))
    assert await alarm_rows(store) == []
    assert await situations(store) == []


def test_a_severity_word_is_never_the_instance() -> None:
    """With the severity first, raise and clear got the instances `major` and `cleared`: two rows
    for one fault, and a clear that could never find its raise."""
    raised, cleared = otm2(0), otm2(1, severity="cleared")
    assert raised.instance == cleared.instance == "1/5/1"
    lone = trap(0, OTM2, [(SEVERITY, "str", "critical")])
    assert lone.instance == "critical", "with nothing else to key on, the old answer stands"


# --- ADR #433: notifications with no clear, and intermittent faults ------------------------------


async def test_a_cold_start_situation_resolves_instead_of_staying_new(store: Store) -> None:
    """RFC 3418 defines no clear for `coldStart`. Its alarm stayed active for good and, by
    v0.16.2's rule, its situation stayed `new` for good — every reboot, on the board, forever."""
    engine, queue, _app = await authutil.make_env(store)
    await feed(engine, queue, trap(0, COLD_START, [], source="10.40.0.9"))
    await engine.maintenance(T0 + CLEAR_HOLD_S + 1, 365.0)  # quiet for the hold: it ends
    [alarm] = await alarm_rows(store)
    assert alarm["status"] == "cleared"
    assert [s["status"] for s in await situations(store)] == ["new"], "held for a bounce first"
    await engine.maintenance(T0 + 2 * CLEAR_HOLD_S + 2, 365.0)
    assert [(s["status"], s["resolution"]) for s in await situations(store)] == [
        ("resolved", "self_cleared")
    ]


async def test_a_state_alarm_without_a_clear_still_stays_live(store: Store) -> None:
    """The control for the test above: only the bundled occurrence classes end on silence. A
    vendor fault with no clear is a fault that may be burning silently, and stays visible."""
    engine, queue, _app = await authutil.make_env(store)
    await feed(engine, queue, otm2(0))
    await engine.maintenance(T0 + 2 * 3600, 365.0)
    assert [s["status"] for s in await situations(store)] == ["new"]


async def test_an_intermittent_port_stops_opening_a_situation_per_bounce(store: Store) -> None:
    """Down and up every ten minutes, six times. The five-minute hold (#410) had always expired
    before the next bounce, so each one opened a situation: five of them before the flap detector
    demoted the port. From its second bounce inside the hour, the port's situation is held while
    it keeps bouncing and the later bounces rejoin it: three situations, and the last one stays."""
    engine, queue, _app = await authutil.make_env(store)
    for i in range(6):
        await feed(engine, queue, link(600 * i, 5), link(600 * i + 30, 5, up=True))
        await engine.maintenance(T0 + 600 * i + 30 + CLEAR_HOLD_S + 1, 365.0)
    rows = await situations(store)
    assert len(rows) == 3 and len(rows[-1]["members"]) == 1
    assert rows[-1]["status"] == "new", "the intermittent port's situation is held, not resolved"
    await engine.maintenance(T0 + 3030 + 3600 + 1, 365.0)
    assert {s["status"] for s in await situations(store)} == {"resolved"}, (
        "an intermittent port that has gone quiet for the hour must not be held for good"
    )


# --- the setting --------------------------------------------------------------------------------


def test_the_rearm_window_is_read_and_refused_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings.from_env().rearm_s == 3600.0
    monkeypatch.setenv("NETCORENOC_REARM_S", "0")
    assert Settings.from_env().rearm_s == 0.0
    for bad in ("-1", "an hour", "nan"):
        monkeypatch.setenv("NETCORENOC_REARM_S", bad)
        with pytest.raises(SettingsError, match="NETCORENOC_REARM_S"):
            Settings.from_env()
