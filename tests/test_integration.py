"""Gate 1 evidence: traps from two distinct enterprise OID prefixes are received over
real UDP, parsed, vendor-labelled, deduplicated, and persisted with zero configuration."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from opticorr.main import Engine
from opticorr.receiver import QueueItem, start_receiver
from opticorr.store import Store

import trap_replay
import util

CIENA = "1.3.6.1.4.1.1271.2.1.1"
HUAWEI = "1.3.6.1.4.1.2011.5.104.1"


async def test_end_to_end_ingestion_zero_config(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "e2e.db"))
    await store.open()
    queue: asyncio.Queue[QueueItem] = asyncio.Queue()
    transport, receiver = await start_receiver(queue, "127.0.0.1", 0)
    port = transport.get_extra_info("sockname")[1]
    engine = Engine(store, queue)
    engine_task = asyncio.create_task(engine.run())
    sender = trap_replay.Sender(("127.0.0.1", port))
    varbinds = [{"oid": "1.3.6.1.4.1.1271.9.9", "kind": "str", "value": "port-1/1"}]
    try:
        sender.send("127.0.0.2", trap_replay.encode_trap(CIENA, varbinds, "public", 1))
        sender.send("127.0.0.2", trap_replay.encode_trap(CIENA, varbinds, "public", 2))
        sender.send("127.0.0.3", trap_replay.encode_trap(HUAWEI, [], "public", 3))
        sender.send("127.0.0.3", b"\xde\xad\xbe\xef")

        async def settled() -> bool:
            stats = await store.stats()
            return stats["active_alarms"] == 2 and stats["quarantined"] == 1

        await util.eventually(settled)
    finally:
        sender.close()
        transport.close()
        engine_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await engine_task

    cur = await store.conn.execute("SELECT ip FROM device ORDER BY ip")
    assert [r["ip"] for r in await cur.fetchall()] == ["127.0.0.2", "127.0.0.3"]
    cur = await store.conn.execute("SELECT oid, vendor FROM alarm_class ORDER BY oid")
    vendors = {r["oid"]: r["vendor"] for r in await cur.fetchall()}
    assert vendors == {CIENA: "Ciena", HUAWEI: "Huawei"}
    cur = await store.conn.execute(
        "SELECT count, instance FROM alarm a JOIN alarm_class c ON c.id=a.class_id WHERE c.oid=?",
        (CIENA,),
    )
    row = await cur.fetchone()
    assert row is not None and row["count"] == 2 and row["instance"] == "port-1/1"
    cur = await store.conn.execute("SELECT reason FROM quarantine")
    row = await cur.fetchone()
    assert row is not None and row["reason"] == "ber-decode-failed"
    assert receiver.stats.accepted == 3 and receiver.stats.quarantined == 1
    await store.close()
