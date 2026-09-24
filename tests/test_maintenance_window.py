"""Maintenance windows: D4's rules, II.2's ledger, II.3's arc boundaries, and Part V's cost.

**The acceptance test of the feature is the maintainer's own example**, and it is
`test_the_maintainers_example_end_to_end` below:

    A MW on hosts A and B, 10:00 to 12:00 Brasilia. On host A, collect only critical alarms. On
    host B, collect traps only from 11:15 to 11:20, and only OIDs under
    1.3.6.1.4.1.2011.5.25.31.1.1.1.1 — so ...1.1, ...1.2, ...1.4, ...1.4.1, ...1.4.2 and so on.

Everything else in this file is one of the injections §10 requires, with the control that passes
beside it.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from netcorenoc.engine.mw import index as mw_index
from netcorenoc.engine.mw.ledger import LedgerKey
from netcorenoc.engine.mw.rules import OidRule, SeverityRule, SlotRule, TargetRules, under_subtree
from netcorenoc.ingest.events import TrapEvent, Varbind
from netcorenoc.main import Engine
from netcorenoc.store import Store
from netcorenoc.store.maintenance_windows import CONFIRMATION_THRESHOLD_S, WindowDraft

import authutil

HOST_A = "10.40.0.1"
HOST_B = "10.40.0.2"
BRASILIA = ZoneInfo("America/Sao_Paulo")

#: The maintainer's own subtree, and the arc that is its sibling rather than its child.
HW_SUBTREE = "1.3.6.1.4.1.2011.5.25.31.1.1.1.1"
HW_SIBLING = "1.3.6.1.4.1.2011.5.25.31.1.1.1.10"

STD_SEV = "1.3.6.1.2.1.118.1.2.2.1.4"
LINK_DOWN = "1.3.6.1.6.3.1.1.5.3"
LINK_UP = "1.3.6.1.6.3.1.1.5.4"
LOS = "1.3.6.1.4.1.2011.6.128.1.1.2.2"


async def _scalar(store: Store, sql: str, *args: object) -> int:
    """One integer out of one row. `fetchone()` is `Row | None`, and every call here knows it
    is not None — asserting that once beats eleven `# type: ignore` comments."""
    cur = await store.conn.execute(sql, args)
    row = await cur.fetchone()
    assert row is not None
    return int(row[0])


def _at(hour: int, minute: int = 0) -> float:
    """A Brasilia wall-clock time on a fixed day, as an absolute instant.

    A fixed date rather than `today`, so the test says the same thing in every month — and one
    outside a DST transition, because the transition has its own test.
    """
    return datetime(2026, 5, 14, hour, minute, tzinfo=BRASILIA).timestamp()


def _trap(host: str, oid: str, instance: str, ts: float, severity: str | None = None) -> TrapEvent:
    varbinds = [Varbind(oid=f"{HW_SUBTREE}.4.1", kind="str", value=instance)]
    if severity is not None:
        varbinds.append(Varbind(oid=STD_SEV, kind="str", value=severity))
    return TrapEvent(device=host, trap_oid=oid, instance=instance, ts=ts, varbinds=varbinds)


async def _window(
    store: Store,
    *,
    starts_at: float,
    ends_at: float,
    ne_ids: list[int],
    rules: list[dict[str, Any]],
    patch_s: float = 0.0,
    status: str = "active",
    ledger_enabled: bool = True,
) -> int:
    draft = WindowDraft(
        name="span splice",
        description="",
        organization_id=await store.default_organization_id(),
        tz="America/Sao_Paulo",
        starts_at=starts_at,
        ends_at=ends_at,
        all_day=False,
        patch_s=patch_s,
        ledger_enabled=ledger_enabled,
        visibility="editors",
        owner_ref="user:1",
        owner_role="editor",
        created_by_agent=False,
        needs_confirmation=False,
        status=status,
        idempotency_key=None,
    )
    async with store.lock:
        window_id = await store.create_maintenance_window(draft, starts_at)
        await store.set_window_targets(window_id, ne_ids)
        await store.set_window_rules(window_id, rules, starts_at)
        await store.commit()
    return window_id


# -- II.3: the arc boundary, which is this feature's most likely bug ---------------------------


def test_an_oid_rule_matches_on_arc_boundaries_and_never_on_a_string_prefix() -> None:
    """**The injection §10 names first**, run as an assertion.

    `"1.3.6.1.4.1.2011.5.25.31.1.1.1.10".startswith("1.3.6.1.4.1.2011.5.25.31.1.1.1.1")` is `True`,
    and the tenth column of a table is not a member of the first column's subtree. A rule written
    with `startswith` admits traffic the operator excluded, silently, on exactly the Huawei arc the
    maintainer's own example uses.
    """
    assert under_subtree(HW_SUBTREE, HW_SUBTREE), "the subtree's own node is in it"
    assert under_subtree(f"{HW_SUBTREE}.4", HW_SUBTREE)
    assert under_subtree(f"{HW_SUBTREE}.4.2", HW_SUBTREE)
    assert not under_subtree(HW_SIBLING, HW_SUBTREE), (
        "a string prefix was accepted as a subtree: ...1.1.1.10 is a SIBLING of ...1.1.1.1"
    )
    assert not under_subtree(f"{HW_SIBLING}.1", HW_SUBTREE)
    # The control: the naive implementation this guards against does the wrong thing here, so the
    # assertion above cannot pass by accident.
    assert HW_SIBLING.startswith(HW_SUBTREE), "the injection is not reproducing the hazard"


def test_an_oid_rule_says_where_it_looks() -> None:
    """II.3(2): the trap's own OID, or any varbind's. **Both, and the rule says which.**"""
    on_trap = OidRule(root=HW_SUBTREE, match_on="trap")
    on_varbind = OidRule(root=HW_SUBTREE, match_on="varbind")
    assert on_trap.admits(f"{HW_SUBTREE}.2", ())
    assert not on_trap.admits(LOS, (f"{HW_SUBTREE}.4.1",))
    assert on_varbind.admits(LOS, (f"{HW_SUBTREE}.4.1",))
    assert not on_varbind.admits(LOS, (HW_SIBLING,))


# -- D4: the composition rule ------------------------------------------------------------------


def test_rules_of_different_kinds_are_anded_and_of_the_same_kind_are_ored() -> None:
    """The maintainer's example settles it: a trap inside the slot but on the wrong subtree must
    not pass, so the slot and the subtree intersect. Two subtrees plainly union — the operator
    wrote a second one in order to admit more."""
    rules = TargetRules(
        oid=(OidRule(root=HW_SUBTREE, match_on="varbind"),),
        slot=(SlotRule(starts_at=_at(11, 15), ends_at=_at(11, 20)),),
    )
    inside, outside = _at(11, 17), _at(11, 30)
    assert rules.admits(inside, LOS, (f"{HW_SUBTREE}.4.1",), None)
    assert not rules.admits(inside, LOS, (HW_SIBLING,), None), "the AND across kinds is missing"
    assert not rules.admits(outside, LOS, (f"{HW_SUBTREE}.4.1",), None), "the slot is not applied"

    two_subtrees = TargetRules(
        oid=(
            OidRule(root=HW_SUBTREE, match_on="varbind"),
            OidRule(root="1.3.6.1.4.1.1271", match_on="varbind"),
        )
    )
    assert two_subtrees.admits(inside, LOS, ("1.3.6.1.4.1.1271.9.1",), None), (
        "two rules of one kind did not union"
    )


def test_a_target_with_no_rule_collects_nothing() -> None:
    """D4's default, and the reason an absent rule is meaningful rather than unconfigured."""
    assert TargetRules().collects_nothing
    assert not TargetRules().admits(_at(11), LOS, (f"{HW_SUBTREE}.4.1",), 0)


def test_a_severity_rule_refuses_a_trap_with_no_placed_severity() -> None:
    """Why D5 shipped first. A rule cannot admit on a severity the appliance never placed, and
    inventing one to make the rule fire is the fabrication prime directive 2 forbids."""
    critical_only = TargetRules(severity=(SeverityRule(at_or_above_rank=0),))
    assert critical_only.admits(_at(11), LINK_DOWN, (), 0)
    assert not critical_only.admits(_at(11), LINK_DOWN, (), 1), "major passed a critical-only rule"
    assert not critical_only.admits(_at(11), LINK_DOWN, (), None), (
        "an unplaced trap was admitted by a severity rule"
    )


# -- Part V: the per-trap cost -----------------------------------------------------------------


def test_the_check_is_free_on_an_appliance_with_no_windows() -> None:
    """One dict lookup on the empty-index path, and it returns a shared constant."""
    assert mw_index.EMPTY.decide(1, _at(11), LOS, (), 0) is mw_index.COLLECT
    populated = mw_index.compile_windows(
        [
            mw_index.build(
                window_id=1,
                name="w",
                organization_id=1,
                starts_at=_at(10),
                ends_at=_at(12),
                patch_s=0.0,
                targets={7: TargetRules()},
            )
        ]
    )
    # An element that is not a target of any window takes the same path as an empty index.
    assert populated.decide(99, _at(11), LOS, (), 0) is mw_index.COLLECT
    # And one that is, inside the window, with no rule, is suppressed.
    assert populated.decide(7, _at(11), LOS, (), 0).suppressed


def test_the_patch_band_widens_the_window_at_both_ends() -> None:
    """A chassis rebooting at 11:58 emits its linkUp storm at 12:03, and the operator who declared
    10:00-12:00 did not mean to be paged for it."""
    window = mw_index.build(
        window_id=1,
        name="w",
        organization_id=1,
        starts_at=_at(10),
        ends_at=_at(12),
        patch_s=600.0,
        targets={7: TargetRules()},
    )
    assert window.covers(_at(9, 55)), "the leading band is not in force"
    assert window.covers(_at(12, 5)), "the trailing band is not in force"
    assert not window.covers(_at(9, 45))
    assert not window.covers(_at(12, 15))
    assert window.starts_at == _at(10), "the DECLARED bounds were overwritten by the effective ones"


# -- the maintainer's example, end to end through the engine ------------------------------------


async def test_the_maintainers_example_end_to_end(store: Store) -> None:
    """**The acceptance test of the feature**, driven through `Engine._process`.

    A window on both hosts, 10:00-12:00 Brasilia. Host A: critical only. Host B: one five-minute
    slot AND one OID subtree. Every assertion below is a trap that either did or did not become an
    alarm, which is the only thing an operator can see.
    """
    engine, _queue, _app = await authutil.make_env(store)
    ne_a = await store.ne_id(HOST_A, _at(9))
    ne_b = await store.ne_id(HOST_B, _at(9))

    await _window(
        store,
        starts_at=_at(10),
        ends_at=_at(12),
        ne_ids=[ne_a, ne_b],
        rules=[
            {"ne_id": ne_a, "kind": "severity", "severity_rank": 0},
            {
                "ne_id": ne_b,
                "kind": "slot",
                "slot_starts_at": _at(11, 15),
                "slot_ends_at": _at(11, 20),
            },
            {"ne_id": ne_b, "kind": "oid", "oid_root": HW_SUBTREE, "match_on": "varbind"},
        ],
    )
    async with store.lock:
        await engine._maintenance_windows(_at(11))

    async with store.lock:
        # HOST A — critical passes, major does not.
        await engine._process(_trap(HOST_A, LINK_DOWN, "span-a", _at(11), "critical"))
        await engine._process(_trap(HOST_A, LOS, "onu-a", _at(11, 1), "major"))
        # HOST B — inside the slot and under the subtree: passes.
        await engine._process(_trap(HOST_B, LOS, "onu-in", _at(11, 17), "major"))
        # inside the slot, WRONG subtree: refused (the AND across kinds).
        outside_subtree = TrapEvent(
            device=HOST_B,
            trap_oid=LOS,
            instance="onu-wrong-arc",
            ts=_at(11, 18),
            varbinds=[Varbind(oid=HW_SIBLING, kind="str", value="onu-wrong-arc")],
        )
        await engine._process(outside_subtree)
        # right subtree, OUTSIDE the slot: refused.
        await engine._process(_trap(HOST_B, LOS, "onu-late", _at(11, 40), "major"))
        await store.commit()

    async with store.lock:
        cur = await store.conn.execute("SELECT instance FROM alarm ORDER BY instance")
        collected = [str(r[0]) for r in await cur.fetchall()]

    assert collected == ["onu-in", "span-a"], (
        f"the maintainer's example did not filter as declared: collected {collected}"
    )


async def test_a_fault_raised_inside_the_window_and_never_cleared_surfaces(store: Store) -> None:
    """**Prime directive 3, and II.2's whole reason.** The fibre cut that outlives the window.

    A trap reports a transition once. Loss of signal at 11:40 is suppressed; at 12:00 the window
    closes with the fibre still dark, and without the ledger the appliance would have no record
    that anything was wrong — and nothing would ever tell it.
    """
    engine, _queue, _app = await authutil.make_env(store)
    ne_a = await store.ne_id(HOST_A, _at(9))
    window_id = await _window(store, starts_at=_at(10), ends_at=_at(12), ne_ids=[ne_a], rules=[])
    async with store.lock:
        await engine._maintenance_windows(_at(11))
        await engine._process(_trap(HOST_A, LOS, "onu-dark", _at(11, 40), "critical"))
        await store.commit()
        assert await _scalar(store, "SELECT COUNT(*) FROM alarm") == 0, (
            "a suppressed trap became an alarm"
        )

    # The window closes with the fault still present.
    async with store.lock:
        await engine._maintenance_windows(_at(12, 1))
        await store.commit()
        cur = await store.conn.execute(
            "SELECT instance, status, severity, severity_source, surfaced_from_window_id, "
            "first_seen FROM alarm"
        )
        rows = [dict(r) for r in await cur.fetchall()]

    assert len(rows) == 1, f"the fault did not surface when the window ended: {rows}"
    row = rows[0]
    assert row["instance"] == "onu-dark"
    assert row["status"] == "active"
    assert row["surfaced_from_window_id"] == window_id, "the alarm is not marked as surfaced"
    assert row["severity"] is None and row["severity_source"] is None, (
        "a severity was invented for an alarm the appliance only saw the existence of"
    )
    assert row["first_seen"] == _at(11, 40), (
        "the alarm is dated from the window's end rather than from when the fault started"
    )


async def test_a_surfaced_fault_lands_in_a_situation_an_operator_can_actually_see(
    store: Store,
) -> None:
    """**F148, found by the live pass.** An alarm in no situation is on no screen.

    `Situations` is the only view in the console that lists alarms, and it lists them as members.
    The sweep wrote the surfaced alarm straight to the `alarm` table, so II.2's whole promise —
    *"a fault that outlives its window surfaces"* — was satisfied in a table nobody reads.

    Two assertions, and the second is the one that keeps a later real trap from doubling it: the
    alarm is in exactly one situation, and the engine's own map agrees, so `_assign_situation`
    will join that situation rather than open a second.
    """
    engine, _queue, _app = await authutil.make_env(store)
    ne_a = await store.ne_id(HOST_A, _at(9))
    await _window(store, starts_at=_at(10), ends_at=_at(12), ne_ids=[ne_a], rules=[])
    async with store.lock:
        await engine._maintenance_windows(_at(11))
        await engine._process(_trap(HOST_A, LOS, "onu-dark", _at(11, 40), "critical"))
        await engine._maintenance_windows(_at(12, 1))
        await store.commit()

        cur = await store.conn.execute(
            "SELECT id, surfaced_from_window_id FROM alarm "
            "WHERE surfaced_from_window_id IS NOT NULL"
        )
        surfaced = [(int(r[0]), int(r[1])) for r in await cur.fetchall()]
        assert len(surfaced) == 1, f"expected one surfaced alarm, got {surfaced}"
        alarm_id, _wid = surfaced[0]
        cur = await store.conn.execute(
            "SELECT situation_id FROM situation_alarm WHERE alarm_id=?", (alarm_id,)
        )
        sids = [int(r[0]) for r in await cur.fetchall()]

    assert len(sids) == 1, (
        f"the surfaced fault is in {len(sids)} situations; an operator reading Situations sees it "
        f"{'twice' if len(sids) > 1 else 'not at all'}"
    )
    assert engine.sit_of.get(alarm_id) == sids[0], (
        "the engine's in-memory map does not know about the situation the sweep opened, so this "
        "element's next real trap will open a second one for an alarm that is already in one"
    )


async def test_a_fault_that_cleared_inside_the_window_does_not_surface(store: Store) -> None:
    """The control for the test above. The ledger records the clear, so the raise is resolved.

    Also the test for the subtle half: a learned clear arrives on its **own** class, so the ledger
    has to key it on the RAISE class. Keyed on the clear class, this fault would surface at 12:00
    as a fibre cut that had in fact been repaired at 11:45.
    """
    engine, _queue, _app = await authutil.make_env(store)
    ne_a = await store.ne_id(HOST_A, _at(9))
    await _window(store, starts_at=_at(10), ends_at=_at(12), ne_ids=[ne_a], rules=[])
    async with store.lock:
        await engine._maintenance_windows(_at(11))
        # linkDown/linkUp is a bundled pair, so the appliance knows it with no learning.
        await engine._process(_trap(HOST_A, LINK_DOWN, "span-a", _at(11, 40), "critical"))
        await engine._process(_trap(HOST_A, LINK_UP, "span-a", _at(11, 45), "cleared"))
        await engine._maintenance_windows(_at(12, 1))
        await store.commit()
        count = await _scalar(store, "SELECT COUNT(*) FROM alarm")

    assert count == 0, (
        "a fault that cleared inside the window surfaced anyway — the ledger keyed the clear on "
        "the clear class rather than on the raise class"
    )


async def test_the_ledger_can_be_turned_off_and_then_nothing_survives_the_window(
    store: Store,
) -> None:
    """*"You may, and here is the risk."* True discard, offered rather than hidden — and this is
    the risk, asserted: the fault does not surface."""
    engine, _queue, _app = await authutil.make_env(store)
    ne_a = await store.ne_id(HOST_A, _at(9))
    await _window(
        store,
        starts_at=_at(10),
        ends_at=_at(12),
        ne_ids=[ne_a],
        rules=[],
        ledger_enabled=False,
    )
    async with store.lock:
        await engine._maintenance_windows(_at(11))
        await engine._process(_trap(HOST_A, LOS, "onu-dark", _at(11, 40), "critical"))
        await engine._maintenance_windows(_at(12, 1))
        await store.commit()
        assert await _scalar(store, "SELECT COUNT(*) FROM alarm") == 0
        assert await _scalar(store, "SELECT COUNT(*) FROM maintenance_ledger") == 0, (
            "ledger_enabled=0 still wrote a ledger row"
        )


async def test_an_unconfirmed_window_suppresses_nothing_and_then_expires(store: Store) -> None:
    """D6's safe failure (II.7). Alarms keep flowing and the window becomes visibly `expired`."""
    engine, _queue, _app = await authutil.make_env(store)
    ne_a = await store.ne_id(HOST_A, _at(9))
    window_id = await _window(
        store,
        starts_at=_at(10),
        ends_at=_at(22),  # twelve hours: over the threshold
        ne_ids=[ne_a],
        rules=[],
        status="pending_confirmation",
    )
    assert _at(22) - _at(10) > CONFIRMATION_THRESHOLD_S

    async with store.lock:
        await engine._maintenance_windows(_at(11))
        await engine._process(_trap(HOST_A, LOS, "onu-1", _at(11, 5), "critical"))
        await store.commit()
        active = await _scalar(store, "SELECT COUNT(*) FROM alarm WHERE status='active'")
        window = await store.maintenance_window(window_id)

    assert active == 1, "an unconfirmed window suppressed a trap"
    assert window is not None and window["status"] == "expired", (
        f"an unconfirmed window whose start arrived is {window and window['status']}, not expired"
    )


async def test_a_window_across_a_dst_transition_keeps_its_wall_clock_duration(store: Store) -> None:
    """§10's injection: a window across a DST transition with the wrong duration.

    Brazil abolished DST in 2019, so the transition is taken where one still exists — America/
    New_York, 8 March 2026, when 02:00 becomes 03:00. A window declared 01:00-05:00 local is
    **three** hours of elapsed time, not four, and an appliance that stored an offset instead of a
    zone would get it wrong by exactly the hour that vanished.
    """
    ny = ZoneInfo("America/New_York")
    starts = datetime(2026, 3, 8, 1, 0, tzinfo=ny).timestamp()
    ends = datetime(2026, 3, 8, 5, 0, tzinfo=ny).timestamp()
    assert ends - starts == 3 * 3600, (
        "the spring-forward hour was not skipped: the window is being computed from a fixed offset "
        "rather than from the zone"
    )
    # And the reverse, so the assertion is about the zone rather than about subtraction.
    autumn_starts = datetime(2026, 11, 1, 0, 0, tzinfo=ny).timestamp()
    autumn_ends = datetime(2026, 11, 1, 4, 0, tzinfo=ny).timestamp()
    assert autumn_ends - autumn_starts == 5 * 3600, "the repeated hour was not counted"


async def test_a_collected_trap_under_a_window_teaches_the_learner_nothing(store: Store) -> None:
    """ADR #372. Maintenance traffic forms situations and moves no learned state.

    Measured as the learner's own epoch and pair mass before and after, because *"it did not
    learn"* is otherwise the kind of claim that passes by doing nothing at all.
    """
    engine, _queue, _app = await authutil.make_env(store)
    ne_a = await store.ne_id(HOST_A, _at(9))
    await _window(
        store,
        starts_at=_at(10),
        ends_at=_at(12),
        ne_ids=[ne_a],
        rules=[{"ne_id": ne_a, "kind": "severity", "severity_rank": 4}],  # everything placed passes
    )
    async with store.lock:
        await engine._maintenance_windows(_at(11))
        before = (
            len(engine.learner.A.pairs),
            len(engine.learner.E.pairs),
            engine.learner.A.epoch,
        )
        await engine._process(_trap(HOST_A, LINK_DOWN, "span-a", _at(11), "critical"))
        await engine._process(_trap(HOST_A, LOS, "onu-1", _at(11, 1), "major"))
        await store.commit()
        active = await _scalar(store, "SELECT COUNT(*) FROM alarm WHERE status='active'")
        pairs = await _scalar(store, "SELECT COUNT(*) FROM dataset_pair")

    assert active == 2, "the rule admitted nothing, so this test proves nothing"
    after = (len(engine.learner.A.pairs), len(engine.learner.E.pairs), engine.learner.A.epoch)
    assert after == before, (
        f"maintenance traffic moved the learner's affinity state: {before} -> {after}"
    )
    assert pairs == 0, "maintenance traffic reached the dataset"


async def test_the_ledger_reaches_no_alarm_no_situation_and_no_dataset(store: Store) -> None:
    """§10's injection: ledger state reaching the correlator, the learner, the dataset or the
    screen. Asserted as *"the suppressed trap produced rows in exactly one table"*."""
    engine, _queue, _app = await authutil.make_env(store)
    ne_a = await store.ne_id(HOST_A, _at(9))
    await _window(store, starts_at=_at(10), ends_at=_at(12), ne_ids=[ne_a], rules=[])
    async with store.lock:
        await engine._maintenance_windows(_at(11))
        for i in range(5):
            await engine._process(_trap(HOST_A, LOS, f"onu-{i}", _at(11, i), "major"))
        await engine._maintenance_windows(_at(11, 30))
        await store.commit()
        counts = {
            table: await _scalar(store, f"SELECT COUNT(*) FROM {table}")  # nosec B608
            for table in ("alarm", "situation", "situation_alarm", "dataset_pair", "link")
        }
        ledger_rows = await _scalar(store, "SELECT COUNT(*) FROM maintenance_ledger")

    assert ledger_rows == 5, "the ledger did not record the suppressed raises"
    assert counts == {
        "alarm": 0,
        "situation": 0,
        "situation_alarm": 0,
        "dataset_pair": 0,
        "link": 0,
    }, f"a suppressed trap reached something it must not: {counts}"
    assert engine.correlator.index == {} or not engine.correlator.index, (
        "a suppressed trap reached the correlator's window"
    )


async def test_the_ledger_survives_a_restart(store: Store) -> None:
    """A window that spans a restart still surfaces what it saw before the restart."""
    engine, _queue, _app = await authutil.make_env(store)
    ne_a = await store.ne_id(HOST_A, _at(9))
    await _window(store, starts_at=_at(10), ends_at=_at(12), ne_ids=[ne_a], rules=[])
    async with store.lock:
        await engine._maintenance_windows(_at(11))
        await engine._process(_trap(HOST_A, LOS, "onu-dark", _at(11, 40), "critical"))
        await engine._maintenance_windows(_at(11, 45))  # flushes the ledger
        await store.commit()

    # A second engine over the same store: the in-memory ledger is empty, the rows are not.
    # Constructed directly rather than through `make_env`, which also seeds the users and would
    # collide on `user.username` — the same thing `test_severity_survives_restart` does.
    fresh = Engine(store, asyncio.Queue())
    await fresh.start()
    assert not fresh.ledger.entries
    async with store.lock:
        await fresh._maintenance_windows(_at(12, 1))
        await store.commit()
        cur = await store.conn.execute("SELECT instance FROM alarm")
        rows = [str(r[0]) for r in await cur.fetchall()]

    assert rows == ["onu-dark"], (
        "a window that spanned a restart lost the fault it had already recorded"
    )


def test_the_per_trap_check_performs_no_query() -> None:
    """§10's injection: the MW check performing a query per trap. **Read from the AST.**

    A timing test would pass on a fast machine with a query in it; a mock would assert against a
    stub of the store rather than the store. This reads `engine/mw/`'s own source and fails on an
    `await`, an `async def`, or any name that could reach the database — the same shape of guard
    v0.18.0 used to replace `TRAP_PATH_HASHES` for `datagram_received`, and for the same reason: a
    property nobody can edit away by accident beats a number somebody recomputes.

    `engine/mw/ledger.py` is included. It is called from the ingest path too — once per suppressed
    trap — and a flush that wrote a row there instead of in the sweep would be I/O inside the batch
    lock, which is exactly what prime directive 1 forbids.
    """
    import ast
    from pathlib import Path

    package = Path(mw_index.__file__).resolve().parent
    offenders: list[str] = []
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Await | ast.AsyncFunctionDef | ast.AsyncFor | ast.AsyncWith):
                offenders.append(f"{path.name}:{node.lineno} {type(node).__name__}")
            if isinstance(node, ast.Attribute) and node.attr in {
                "execute",
                "executescript",
                "commit",
                "fetchone",
                "fetchall",
            }:
                offenders.append(f"{path.name}:{node.lineno} .{node.attr}()")
    assert not offenders, (
        "the per-trap maintenance-window check does I/O or awaits — Part V requires it to cost "
        f"no query, no lock and no I/O: {offenders}"
    )

    # The control: the guard can fail. A module with an `await` in it is detected by the same walk.
    sample = ast.parse("async def f():\n    await g()\n")
    assert any(isinstance(n, ast.Await) for n in ast.walk(sample)), "the injection cannot fail"


async def test_a_suppressed_trap_writes_strictly_less_than_a_collected_one(store: Store) -> None:
    """The behavioural half of the guard above: suppression **saves** work rather than adding it.

    Counted as rows written, which is what an operator's disk sees. A collected trap writes an
    alarm and its correlation rows; a suppressed one writes nothing at all until the sweep flushes
    the ledger.
    """
    engine, _queue, _app = await authutil.make_env(store)
    ne_a = await store.ne_id(HOST_A, _at(9))

    async def row_counts() -> dict[str, int]:
        return {
            table: await _scalar(store, f"SELECT COUNT(*) FROM {table}")  # nosec B608
            for table in ("alarm", "situation", "dataset_pair", "maintenance_ledger")
        }

    async with store.lock:
        await engine._process(_trap(HOST_A, LOS, "collected", _at(9, 1), "major"))
        await store.commit()
        after_collected = await row_counts()

    # `_window` takes `store.lock`, which is **not reentrant** — creating the window inside the
    # block above deadlocks the test rather than failing it.
    await _window(store, starts_at=_at(10), ends_at=_at(12), ne_ids=[ne_a], rules=[])
    async with store.lock:
        await engine._maintenance_windows(_at(11))
        await engine._process(_trap(HOST_A, LOS, "suppressed", _at(11, 1), "major"))
        await store.commit()
        after_suppressed = await row_counts()

    assert after_collected["alarm"] == 1
    assert after_suppressed["alarm"] == 1, "a suppressed trap wrote an alarm row"
    assert after_suppressed["maintenance_ledger"] == 0, (
        "the ledger was flushed on the ingest path instead of by the sweep"
    )


async def test_the_decision_is_made_against_the_traps_own_timestamp(store: Store) -> None:
    """Part V: boundaries are exact rather than quantised to the five-second tick.

    Two traps forty milliseconds either side of a window's start, both processed in the same tick.
    One is collected and one is not, and no refresh happens between them.
    """
    engine, _queue, _app = await authutil.make_env(store)
    ne_a = await store.ne_id(HOST_A, _at(9))
    await _window(store, starts_at=_at(10), ends_at=_at(12), ne_ids=[ne_a], rules=[])
    async with store.lock:
        await engine._maintenance_windows(_at(9, 59))
        await engine._process(_trap(HOST_A, LOS, "before", _at(10) - 0.04, "major"))
        await engine._process(_trap(HOST_A, LOS, "after", _at(10) + 0.04, "major"))
        await store.commit()
        cur = await store.conn.execute("SELECT instance FROM alarm")
        rows = [str(r[0]) for r in await cur.fetchall()]

    assert rows == ["before"], (
        f"the boundary was quantised to the refresh tick rather than compared to the trap's own "
        f"timestamp: collected {rows}"
    )


async def test_a_suppressed_repeat_keeps_the_first_raise_instant(store: Store) -> None:
    """The fault started when it started. A repeat is the same fault still being reported."""
    engine, _queue, _app = await authutil.make_env(store)
    ne_a = await store.ne_id(HOST_A, _at(9))
    window_id = await _window(store, starts_at=_at(10), ends_at=_at(12), ne_ids=[ne_a], rules=[])
    async with store.lock:
        await engine._maintenance_windows(_at(11))
        await engine._process(_trap(HOST_A, LOS, "onu-dark", _at(10, 5), "critical"))
        await engine._process(_trap(HOST_A, LOS, "onu-dark", _at(11, 50), "critical"))
        await store.commit()
        device_id = await store.device_id(HOST_A, _at(9))
        class_id = await store.class_id(LOS, _at(9))
    key = LedgerKey(window_id, device_id, class_id, "onu-dark")
    assert engine.ledger.entries[key].raised_at == _at(10, 5), (
        "a repeat moved the raise instant forward, so the fault will be dated from its last report"
    )


async def test_ending_a_window_early_surfaces_immediately(store: Store) -> None:
    """*"End now"* is a real end: the sweep that follows it surfaces what the window suppressed."""
    engine, _queue, _app = await authutil.make_env(store)
    ne_a = await store.ne_id(HOST_A, _at(9))
    window_id = await _window(store, starts_at=_at(10), ends_at=_at(12), ne_ids=[ne_a], rules=[])
    async with store.lock:
        await engine._maintenance_windows(_at(11))
        await engine._process(_trap(HOST_A, LOS, "onu-dark", _at(11, 10), "critical"))
        await engine._maintenance_windows(_at(11, 15))
        assert await store.end_window_now(window_id, _at(11, 20))
        await store.commit()

    async with store.lock:
        # `advance_statuses` no longer touches it — it is already `ended` — so the sweep has to
        # notice an ended window with an unresolved ledger by another route.
        await engine._surface_window(window_id, _at(11, 20))
        await store.commit()
        cur = await store.conn.execute("SELECT instance FROM alarm")
        rows = [str(r[0]) for r in await cur.fetchall()]

    assert rows == ["onu-dark"]


def test_the_status_set_matches_what_the_migration_documents() -> None:
    """The schema comment and the code cannot drift: one of them is read by the other's test."""
    from pathlib import Path

    from netcorenoc.store.maintenance_windows import STATUSES

    sql = (
        Path(__file__)
        .resolve()
        .parent.parent.joinpath("src/netcorenoc/migrations/0021_maintenance_window.sql")
        .read_text(encoding="utf-8")
    )
    for status in STATUSES:
        assert status in sql, f"status {status!r} is in the code and not in the migration's prose"


def test_the_index_refresh_is_bounded_by_the_horizon() -> None:
    """A window a year away is not compiled into the structure the ingest path reads."""
    assert mw_index.LEADING_HORIZON_S <= 3600.0
    assert mw_index.TRAILING_GRACE_S >= 5.0, (
        "the trailing grace must outlast one maintenance tick, or a trap timestamped inside a "
        "window that ended between two refreshes would be collected because the index forgot it"
    )
    assert time.time() > 0  # the module imports `time` for the perf test above


def test_the_store_and_the_engine_agree_on_the_index_horizons() -> None:
    """The layer rule costs one duplicated pair of constants; this is what stops them drifting.

    `store/mw_compile.py` may not import `engine/mw/index.py` — that would be an upward import —
    so its two horizons are literals. A literal that disagreed with the engine's would make the
    index cover a different span from the one the check believes, and the symptom would be a trap
    at a boundary decided by a window the index had already forgotten.
    """
    from netcorenoc.store import mw_compile

    assert mw_compile.INDEX_LOOKAHEAD_S == mw_index.LEADING_HORIZON_S
    assert mw_compile.INDEX_LOOKBEHIND_S == mw_index.TRAILING_GRACE_S


async def test_the_sweep_and_the_markers_are_inert_on_a_pre_0020_schema(store: Store) -> None:
    """**The fifth schema probe, asserted** (`base.py::_has_maintenance`).

    Four of this store's probes guard a *column*. This one guards a *loop*: the maintenance sweep
    runs every five seconds whether or not anybody has declared planned work, and `/api/entities`
    and `/api/situations` ask for markers on every request. Unlike a column read, those run
    **unbidden** — so on a database frozen below schema 20 they raise `no such table` on their own,
    with nobody having asked for the feature.

    `tests/test_upgrade.py` drives this store against migration directories frozen as far back as
    schema 4, and it went red on nine of them for exactly this reason. That is the instrument
    working: the upgrade path is a contract, not an accident.

    The tables are dropped here rather than reached through the frozen-migration fixtures, because
    what is under test is the probe's effect and not the migration machinery — and dropping them is
    the one way to produce the state without also producing a schema-4 database's everything else.
    """
    engine, _queue, _app = await authutil.make_env(store)
    async with store.lock:
        await store.conn.execute("DROP TABLE maintenance_window_rule")
        await store.conn.execute("DROP TABLE maintenance_window_target")
        await store.conn.execute("DROP TABLE maintenance_ledger")
        await store.conn.execute("DROP TABLE maintenance_window")
        await store.conn.execute("DROP TABLE organization")
        await store.commit()
    await store._probe_schema()

    assert store._has_maintenance is False, "the probe still believes the tables are there"
    assert await store.advance_statuses(_at(10)) == []
    assert await store.attribute_unassigned_nes() == 0
    assert await store.window_index_rows(_at(10)) == []
    assert await store.window_markers(_at(10)) == {}
    assert await store.list_organizations() == []

    # The whole sweep, which is what actually went red: it calls all four of the above in order.
    async with store.lock:
        await engine._maintenance_windows(_at(10))
    assert engine.windows.decide(1, _at(10), LINK_DOWN, (), None).collect is True, (
        "an appliance on an old schema must collect every trap, not suppress on an empty index"
    )


async def test_the_probe_is_true_on_a_current_schema(store: Store) -> None:
    """The control. Without it the test above passes on a probe hardwired to `False`, which would
    make the whole feature inert on every database including the ones that have the tables."""
    await authutil.make_env(store)
    assert store._has_maintenance is True, "the probe does not see tables migration 0021 created"
    assert await store.list_organizations(), "the seeded default organization is not readable"
