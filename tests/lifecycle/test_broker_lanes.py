"""ADRs #494, #495 — two networks on one appliance, through the real engine.

The same fiber cut happens at the same instant in two customers' networks whose elements share
addresses: one network reaches us on its broker's own port, the other through its remote proxy.

* an element is (broker, address): each address exists once per broker that saw it;
* **no situation spans two brokers**, and each broker forms the same situations it would alone —
  its lane learned only from its own traps;
* each broker has its own decider: one switched to the formula runs it while the other keeps the
  league's champion, and the situations each forms record their own;
* labels are read per broker: the judge and the site search of one never see the other's.
"""

from __future__ import annotations

import time

from netcorenoc.engine.model import site_labels
from netcorenoc.store import Store

import authutil
import util

BASE = 1_700_000_000.0


async def _two_brokers(store: Store) -> tuple[int, int]:
    async with store.lock:
        north = await store.create_broker(
            name="Claro Nordeste",
            slug="claro-ne",
            organization_id=1,
            description="",
            intake="port",
            udp_port=1162,
            networks="",
            token_hash=None,
            token_hint=None,
            actor="test",
            now=BASE,
        )
        south = await store.create_broker(
            name="Vivo Sul",
            slug="vivo-sul",
            organization_id=1,
            description="",
            intake="proxy",
            udp_port=None,
            networks="",
            token_hash="0" * 64,
            token_hint="abcd",
            actor="test",
            now=BASE,
        )
        await store.commit()
    return north, south


async def test_the_same_cut_in_two_networks_stays_two_networks(store: Store) -> None:
    engine, queue, _app = await authutil.make_env(store)
    north, south = await _two_brokers(store)
    async with store.lock:
        await engine.load_scorer_config()  # the reload point: routing and a lane per broker
        await store.commit()
    assert {north, south} <= set(engine.lanes.by_id)
    events = util.fixture_events("fiber_cut.json", BASE)
    on_port = [e.model_copy(update={"port": 1162}) for e in events]
    by_proxy = [e.model_copy(update={"broker": south}) for e in events]
    await util.drive(
        engine, queue, [x for pair in zip(on_port, by_proxy, strict=True) for x in pair]
    )

    async with store.reading():
        cur = await store.conn.execute(
            "SELECT host(ip) AS ip, array_agg(broker_id ORDER BY broker_id) AS brokers "
            "FROM ne GROUP BY ip ORDER BY ip"
        )
        nes = [dict(r) for r in await cur.fetchall()]
        cur = await store.conn.execute(
            "SELECT s.id, s.broker_id, array_agg(DISTINCT n.broker_id) AS member_brokers "
            "FROM situation s JOIN situation_alarm sa ON sa.situation_id = s.id "
            "JOIN alarm a ON a.id = sa.alarm_id JOIN ne n ON n.id = a.ne_id GROUP BY s.id"
        )
        situations = [dict(r) for r in await cur.fetchall()]
    assert nes and all(row["brokers"] == [north, south] for row in nes), nes
    assert situations
    for row in situations:
        assert row["member_brokers"] == [row["broker_id"]], f"situation {row['id']} spans brokers"
    per_broker = {b: sum(1 for r in situations if r["broker_id"] == b) for b in (north, south)}
    assert per_broker[north] == per_broker[south] > 0, per_broker
    assert not any(r["broker_id"] == 1 for r in situations), "nothing fell to the Default broker"


async def test_each_broker_has_its_own_decider(store: Store, packaged_league: object) -> None:
    engine, queue, _app = await authutil.make_env(store)
    north, south = await _two_brokers(store)
    async with store.lock:
        await store.set_decider_mode("additive", "test", time.time(), "the formula", south)
        await engine.load_scorer_config()
        await store.commit()
    assert engine.lanes.of(south).decider_ref.startswith("additive")
    assert not engine.lanes.of(north).decider_ref.startswith("additive"), "the league still runs"
    assert engine.decider_ref == engine.lanes.default.decider_ref
    events = util.fixture_events("fiber_cut.json", BASE)
    await util.drive(
        engine,
        queue,
        [e.model_copy(update={"port": 1162}) for e in events]
        + [e.model_copy(update={"broker": south}) for e in events],
    )
    async with store.reading():
        cur = await store.conn.execute(
            "SELECT broker_id, array_agg(DISTINCT decider) AS deciders FROM situation "
            "GROUP BY broker_id ORDER BY broker_id"
        )
        deciders = {int(r[0]): list(r[1]) for r in await cur.fetchall()}
    assert all(str(d).startswith("additive") for d in deciders[south])
    assert not any(str(d).startswith("additive") for d in deciders[north])
    # Labels are a broker's own: the south's judge reads nothing the north produced.
    async with store.lock:
        assert await site_labels.label_pairs(store, south) == []
        assert await site_labels.label_pairs(store, north) == []


async def test_an_address_outside_every_range_is_the_default_brokers(store: Store) -> None:
    engine, queue, _app = await authutil.make_env(store)
    async with store.lock:
        ranged = await store.create_broker(
            name="Corp",
            slug="corp",
            organization_id=1,
            description="",
            intake="cidr",
            udp_port=None,
            networks="10.0.0.0/8",
            token_hash=None,
            token_hint=None,
            actor="test",
            now=BASE,
        )
        await engine.load_scorer_config()
        await store.commit()
    await util.drive(
        engine,
        queue,
        [util.event(device="10.1.2.3", ts=BASE), util.event(device="192.0.2.9", ts=BASE + 1)],
    )
    async with store.reading():
        cur = await store.conn.execute("SELECT host(ip), broker_id FROM ne ORDER BY ip")
        owners = {str(r[0]): int(r[1]) for r in await cur.fetchall()}
    assert owners == {"10.1.2.3": ranged, "192.0.2.9": 1}
