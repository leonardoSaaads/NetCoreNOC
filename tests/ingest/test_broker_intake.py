"""ADR #494 — how a trap reaches its broker, through the real ingest and decoder processes.

* a trap on a broker's **own port** is spooled in a ``P`` envelope naming that port, and decodes to
  an event carrying it; one on the standard port is spooled as it always was (``R``);
* the set of ports changes without losing a datagram: the sockets stay open in the main process
  while the ingest process is started again with all of them, and the kernel holds what arrives;
* a port already in use is reported, not fatal;
* a remote proxy's records reach the spool through the ingest process — the spool's one writer — and
  are acknowledged only once durable; a batch that is not a broker's is refused whole;
* routing answers which broker each belongs to: port, proxy, longest prefix, else the Default.
"""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import pytest
import test_ingest_process as harness

from netcorenoc.engine.operate import routing
from netcorenoc.ingest import process, records
from netcorenoc.ingest.events import TrapEvent

pytestmark = pytest.mark.skipif(not process.supported(), reason="POSIX descriptor inheritance")


def _free_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return port


async def test_a_brokers_own_port_is_spooled_with_its_port(tmp_path: Path) -> None:
    ingest, sender, standard, _heads = await harness._started(tmp_path)
    own = _free_port()
    try:
        assert await ingest.listen([own]) == {}
        assert ingest.ports == (own,)
        await asyncio.sleep(0.6)  # the ingest process is started again with the new socket
        sender.sendto(harness._wire(1), ("127.0.0.1", standard))
        sender.sendto(harness._wire(2), ("127.0.0.1", own))
        got = await harness._collect(tmp_path, 2)
        assert len(got) == 2
        kinds = sorted(p[:1] for p in got)
        assert kinds == [b"P", b"R"]
        on_port = next(p for p in got if p[:1] == b"P")
        assert records.envelope(on_port)[:2] == (own, 0)
        decoded = await ingest.decoder.decode(got)
        events = [e for e in decoded if isinstance(e, TrapEvent)]
        assert sorted(e.port for e in events) == [0, own]
        assert ingest.restarts == 0, "taking a port is not a failure"
    finally:
        sender.close()
        ingest.close()
        await ingest.wait_closed()


async def test_changing_ports_loses_nothing(tmp_path: Path) -> None:
    ingest, sender, standard, _heads = await harness._started(tmp_path)
    first, second = _free_port(), _free_port()
    try:
        await ingest.listen([first])
        await asyncio.sleep(0.6)
        for i in range(200):  # in flight while the process is replaced with the second port
            sender.sendto(harness._wire(i), ("127.0.0.1", standard if i % 2 else first))
            if i == 100:
                await ingest.listen([first, second])
        await asyncio.sleep(0.6)
        sender.sendto(harness._wire(999), ("127.0.0.1", second))
        got = await harness._collect(tmp_path, 201)
        assert len(got) == 201, "every datagram, including those sent during the swap"
    finally:
        sender.close()
        ingest.close()
        await ingest.wait_closed()


async def test_a_port_in_use_is_reported_not_fatal(tmp_path: Path) -> None:
    ingest, sender, _standard, _heads = await harness._started(tmp_path)
    taken = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    taken.bind(("127.0.0.1", 0))
    busy = int(taken.getsockname()[1])
    try:
        errors = await ingest.listen([busy])
        assert busy in errors and ingest.ports == ()
        assert any(str(busy) in w for w in ingest.warnings())
    finally:
        taken.close()
        sender.close()
        ingest.close()
        await ingest.wait_closed()


async def test_a_proxys_records_are_acknowledged_once_durable(tmp_path: Path) -> None:
    ingest, sender, _standard, _heads = await harness._started(tmp_path)
    try:
        batch = [
            records.for_broker(7, records.raw("10.0.0.1", 40000 + i, 100.0 + i, harness._wire(i)))
            for i in range(50)
        ]
        assert await ingest.append(batch) is True
        got = harness._spooled(tmp_path)  # durable before the acknowledgement: no waiting
        assert len(got) == 50 and all(records.envelope(p)[1] == 7 for p in got)
        decoded = await ingest.decoder.decode(got)
        assert {e.broker for e in decoded if isinstance(e, TrapEvent)} == {7}
        # Anything that is not a broker's record refuses the whole batch.
        mixed = [*batch[:2], records.raw("10.0.0.2", 1, 1.0, b"x")]
        assert await ingest.append(mixed) is False
        assert len(harness._spooled(tmp_path)) == 50
    finally:
        sender.close()
        ingest.close()
        await ingest.wait_closed()


def test_routing_follows_port_proxy_and_longest_prefix() -> None:
    snapshot = routing.build(
        [
            {"id": 1, "name": "Default", "intake": "cidr", "networks": ""},
            {
                "id": 2,
                "name": "North-East",
                "intake": "port",
                "udp_port": 1162,
                "networks": "10.0.0.0/8",
            },
            {"id": 3, "name": "South", "intake": "cidr", "networks": "10.20.0.0/16"},
            {"id": 4, "name": "Proxy", "intake": "proxy", "networks": ""},
            {
                "id": 5,
                "name": "Off",
                "intake": "port",
                "udp_port": 1170,
                "enabled": False,
                "networks": "192.0.2.0/24",
            },
        ]
    )
    assert snapshot.resolve("10.20.3.4") == 3, "the longest prefix wins"
    assert snapshot.resolve("10.1.1.1") == 2
    assert snapshot.resolve("10.20.3.4", port=1162) == 2, "a broker's own port wins over ranges"
    assert snapshot.resolve("203.0.113.1") == routing.DEFAULT_BROKER
    assert snapshot.resolve("203.0.113.1", broker=4) == 4
    assert snapshot.resolve("203.0.113.1", broker=2) == routing.DEFAULT_BROKER, "not a proxy"
    assert snapshot.resolve("192.0.2.9") == routing.DEFAULT_BROKER, "a broker switched off"
    assert snapshot.ports() == (1162,)
    with pytest.raises(routing.RoutingError):
        routing.canonical_networks("10.0.0.1/8")
    assert routing.overlaps(("10.0.0.0/8", "10.9.0.0/16"), {3: ("10.0.0.0/8",)}) == [
        ("10.0.0.0/8", 3)
    ]
