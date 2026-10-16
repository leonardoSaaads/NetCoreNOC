"""ADR #449 — traps are received and decoded in a process of their own.

What must hold, whichever process decodes: every trap arrives, **in the order it was sent**, as the
same event the in-process receiver would have built; an admin's allowlist and SNMP policy take
effect; a process that dies is replaced on the same socket and says so; a graceful stop delivers
what was already received; and nothing crosses the boundary that is not validated data.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import struct
import threading
import time
from dataclasses import replace
from multiprocessing.connection import Connection

import pytest

from netcorenoc.ingest import process, snmpconf, worker
from netcorenoc.ingest.events import QuarantinedPacket, TrapEvent
from netcorenoc.ingest.process import IngestProcess, decode
from netcorenoc.ingest.receiver import (
    QueueItem,
    TrapReceiver,
    parse_allowlist,
    parse_trap,
    quarantine_packet,
)

import trap_replay

pytestmark = pytest.mark.skipif(not process.supported(), reason="POSIX descriptor inheritance")

KEY = b"k" * 32


def _wire(i: int) -> bytes:
    return trap_replay.encode_trap(
        f"1.3.6.1.4.1.2011.5.25.{i % 7}",
        [{"oid": f"1.3.6.1.2.1.2.2.1.1.{i % 5}", "kind": "int", "value": str(i % 5)}],
        "public",
        i,
    )


async def _collect(
    queue: asyncio.Queue[QueueItem], n: int, timeout: float = 20.0
) -> list[QueueItem]:
    out: list[QueueItem] = []
    deadline = time.monotonic() + timeout
    while len(out) < n and time.monotonic() < deadline:
        try:
            out.append(await asyncio.wait_for(queue.get(), 0.5))
        except TimeoutError:
            continue
    return out


async def _started(queue: asyncio.Queue[QueueItem]) -> tuple[IngestProcess, socket.socket, int]:
    sock = await process.bind("127.0.0.1", 0)
    port = sock.getsockname()[1]
    ingest = IngestProcess(queue, sock, "", KEY, snmpconf.DEFAULT_POLICY)
    await ingest.start()
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    return ingest, sender, port


# -- the wire format ----------------------------------------------------------------------


def test_events_cross_the_boundary_unchanged() -> None:
    event = parse_trap("10.0.0.1", _wire(3), ts=1234.5, community_key=KEY)
    packet = quarantine_packet("10.0.0.2", b"\x30\x03\x02\x01\x07", "ber-decode-failed", 99.0)
    for original in (event, packet):
        rebuilt = decode(json.loads(json.dumps(worker.encode(original))))
        assert rebuilt.model_dump() == original.model_dump()
        assert type(rebuilt) is type(original)


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        ["x"],
        ["t", "10.0.0.1", "1.3", "", "not-a-time", "", []],
        ["t", "10.0.0.1", "1.3", "", True, "", []],
        ["t", "10.0.0.1", "1.3", "", 1.0, "", [["1.3", "Integer"]]],
        ["t", 7, "1.3", "", 1.0, "", []],
        ["q", "10.0.0.1", "%%%", "r", 1.0, "", 1, "", True],
        ["q", "10.0.0.1", "", "r", 1.0, "", 1.5, "", True],
        ["q", "10.0.0.1", "", "r", 1.0, "", 1, "", "yes"],
    ],
)
def test_a_malformed_event_is_refused(bad: object) -> None:
    with pytest.raises(ValueError):
        decode(bad)


# -- the real process ---------------------------------------------------------------------


async def test_every_trap_arrives_in_order_as_the_receiver_would_build_it() -> None:
    queue: asyncio.Queue[QueueItem] = asyncio.Queue()
    ingest, sender, port = await _started(queue)
    try:
        wires = [_wire(i) for i in range(300)]
        for wire in wires:
            sender.sendto(wire, ("127.0.0.1", port))
        got = await _collect(queue, len(wires))
        assert len(got) == len(wires)
        for wire, item in zip(wires, got, strict=True):
            assert isinstance(item, TrapEvent)
            expected = parse_trap(item.device, wire, item.ts, community_key=KEY)
            assert item.model_dump() == expected.model_dump(), "same event, same order"
        await asyncio.sleep(1.2)  # one stats report
        assert ingest.stats.received == ingest.stats.accepted == len(wires)
    finally:
        sender.close()
        ingest.close()
        await ingest.wait_closed()


async def test_allowlist_and_policy_changes_reach_the_process() -> None:
    queue: asyncio.Queue[QueueItem] = asyncio.Queue()
    ingest, sender, port = await _started(queue)
    try:
        ingest.accepts = replace(snmpconf.DEFAULT_POLICY, v2c=False)
        await asyncio.sleep(0.3)
        sender.sendto(_wire(1), ("127.0.0.1", port))
        (refused,) = await _collect(queue, 1)
        assert isinstance(refused, QuarantinedPacket) and refused.reason == "snmp-v2c-not-accepted"
        ingest.accepts = snmpconf.DEFAULT_POLICY
        ingest.networks = parse_allowlist("192.0.2.0/24")  # 127.0.0.1 is no longer allowed
        await asyncio.sleep(0.3)
        sender.sendto(_wire(2), ("127.0.0.1", port))
        await asyncio.sleep(1.3)
        assert queue.empty() and ingest.stats.denied == 1
        assert ingest.reasons == {"snmp-v2c-not-accepted": 1}
    finally:
        sender.close()
        ingest.close()
        await ingest.wait_closed()


async def test_a_dead_process_is_replaced_on_the_same_socket() -> None:
    queue: asyncio.Queue[QueueItem] = asyncio.Queue()
    ingest, sender, port = await _started(queue)
    try:
        sender.sendto(_wire(1), ("127.0.0.1", port))
        assert len(await _collect(queue, 1)) == 1
        await asyncio.sleep(1.2)
        assert ingest._proc is not None
        ingest._proc.kill()
        for _ in range(100):
            if ingest.restarts == 1 and ingest._proc is not None and ingest._proc.poll() is None:
                break
            await asyncio.sleep(0.1)
        assert ingest.restarts == 1 and ingest.warnings()
        sender.sendto(_wire(2), ("127.0.0.1", port))  # read by the replacement
        assert len(await _collect(queue, 1)) == 1
        await asyncio.sleep(1.2)
        assert ingest.stats.received == 2, "the counters carry over a restart"
    finally:
        sender.close()
        ingest.close()
        await ingest.wait_closed()


async def test_a_graceful_stop_delivers_what_was_received() -> None:
    queue: asyncio.Queue[QueueItem] = asyncio.Queue()
    ingest, sender, port = await _started(queue)
    for i in range(200):
        sender.sendto(_wire(i), ("127.0.0.1", port))
    sender.close()
    await asyncio.sleep(0.5)
    ingest.close()
    await ingest.wait_closed()
    assert queue.qsize() == 200


async def test_a_full_engine_queue_counts_the_drop() -> None:
    queue: asyncio.Queue[QueueItem] = asyncio.Queue(maxsize=5)
    ingest, sender, port = await _started(queue)
    try:
        for i in range(20):
            sender.sendto(_wire(i), ("127.0.0.1", port))
        await asyncio.sleep(1.5)
        assert queue.qsize() == 5 and ingest.stats.dropped == 15
    finally:
        sender.close()
        ingest.close()
        await ingest.wait_closed()


async def test_a_port_in_use_fails_in_the_parent_as_it_always_did() -> None:
    held = await process.bind("127.0.0.1", 0)
    try:
        with pytest.raises(OSError):
            await process.bind("127.0.0.1", held.getsockname()[1])
    finally:
        held.close()


# -- the worker loop, in this process (what the child runs) --------------------------------


def test_the_worker_loop_receives_reports_and_stops() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    events_r, events_w = os.pipe()
    control_r, control_w = os.pipe()
    events_in = Connection(events_r, writable=False)
    control_out = Connection(control_w, readable=False)
    outbox = worker._Outbox(worker.OUTBOX_MAX)
    receiver = TrapReceiver(outbox, "", KEY)  # type: ignore[arg-type]
    loop = worker.Worker(
        sock,
        Connection(events_w, readable=False),
        Connection(control_r, writable=False),
        receiver,
        outbox,
    )
    thread = threading.Thread(target=loop.run)
    thread.start()
    try:
        assert json.loads(events_in.recv_bytes())[0] == "r"
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for i in range(10):
            sender.sendto(_wire(i), sock.getsockname())
        sender.sendto(b"not snmp", sock.getsockname())
        sender.close()
        control_out.send_bytes(json.dumps(["allowlist", "10.0.0.0/8"]).encode())
        control_out.send_bytes(json.dumps(["policy", "not json"]).encode())  # ignored
        control_out.send_bytes(b"\xff")  # not JSON: ignored
        time.sleep(0.2)
        control_out.send_bytes(json.dumps(["stop"]).encode())
        thread.join(timeout=10)
        assert not thread.is_alive()
        items: list[QueueItem] = []
        received = -1
        while events_in.poll(0.5):
            message = json.loads(events_in.recv_bytes())
            if message[0] == "b":
                items.extend(decode(item) for item in message[1])
            elif message[0] == "s":
                received = int(message[1]["received"])
        assert sum(isinstance(i, TrapEvent) for i in items) == 10
        assert sum(isinstance(i, QuarantinedPacket) for i in items) == 1
        assert received == 11, "the last report counts every datagram"
        assert receiver.networks is not None and str(receiver.networks[0]) == "10.0.0.0/8"
    finally:
        sock.close()
        events_in.close()
        control_out.close()


def test_the_outbox_overflow_is_the_receivers_drop() -> None:
    outbox = worker._Outbox(2)
    receiver = TrapReceiver(outbox, "", KEY)  # type: ignore[arg-type]
    for i in range(5):
        receiver.datagram_received(_wire(i), ("10.0.0.1", 162))
    assert len(outbox.items) == 2 and receiver.stats.dropped == 3


# -- the arrival stamp ---------------------------------------------------------------------


def test_the_kernel_stamp_is_read_and_sanity_checked() -> None:
    now = 1_800_000_000.0

    def cmsg(stamp: float) -> list[tuple[int, int, bytes]]:
        seconds = int(stamp)
        payload = struct.pack("qq", seconds, round((stamp - seconds) * 1e9))
        return [(socket.SOL_SOCKET, worker.SO_TIMESTAMPNS, payload)]

    assert worker.arrival(cmsg(now - 2.5), now) == pytest.approx(now - 2.5)
    assert worker.arrival(cmsg(now + 30.0), now) is None, "a stamp from the future is refused"
    assert worker.arrival(cmsg(now - 7200.0), now) is None, "so is one hours old"
    assert worker.arrival([], now) is None
    assert worker.arrival([(socket.SOL_SOCKET, 99, b"\0" * 16)], now) is None


async def test_each_trap_is_stamped_at_its_arrival_not_at_its_decode() -> None:
    """With kernel stamps a trap's clock starts when the datagram arrived, so time spent waiting
    in the socket buffer during a burst counts against its deadline."""
    queue: asyncio.Queue[QueueItem] = asyncio.Queue()
    ingest, sender, port = await _started(queue)
    try:
        sent_at = time.time()
        for i in range(50):
            sender.sendto(_wire(i), ("127.0.0.1", port))
        got = await _collect(queue, 50)
        assert len(got) == 50
        assert all(sent_at - 0.05 <= item.ts <= time.time() for item in got)
        if worker.kernel_stamps():
            stamps = [item.ts for item in got]
            assert stamps == sorted(stamps), "kernel stamps follow arrival order"
    finally:
        sender.close()
        ingest.close()
        await ingest.wait_closed()
