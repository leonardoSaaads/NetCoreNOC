"""ADRs #449, #450 — traps are received and spooled raw in one process, decoded in another.

What must hold: every datagram the allowlist admits is written to the spool **raw, in the order it
arrived**, stamped with its arrival; the decoder turns each into exactly the event the in-process
receiver would have built, in order; an admin's allowlist reaches the receiving process and the SNMP
policy the decoding one; a process that dies is replaced (the receiver on the same socket and spool,
the decoder before the next batch) and says so; a datagram that stops the decoder is quarantined by
its metadata, never lost and never retried forever; a graceful stop spools what was received; a
spool that refuses writes is reported and counted; and nothing crosses a boundary that is not
validated data.
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
from pathlib import Path

import pytest

from netcorenoc.ingest import decoder as decoder_module
from netcorenoc.ingest import process, records, snmpconf, spool, worker
from netcorenoc.ingest.decoder import Decoder, DecoderError
from netcorenoc.ingest.events import QuarantinedPacket, TrapEvent
from netcorenoc.ingest.process import IngestProcess
from netcorenoc.ingest.receiver import parse_allowlist, parse_trap, quarantine_packet

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


def _spooled(directory: Path) -> list[bytes]:
    reader = spool.SpoolReader(directory, 0)
    try:
        out: list[bytes] = []
        while batch := reader.read(10_000):
            out.extend(payload for _seq, payload in batch)
        return out
    finally:
        reader.close()


async def _collect(directory: Path, n: int, timeout: float = 20.0) -> list[bytes]:
    deadline = time.monotonic() + timeout
    got = _spooled(directory)
    while len(got) < n and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        got = _spooled(directory)
    return got


async def _started(
    directory: Path, max_bytes: int = 1 << 30, allowlist: str = ""
) -> tuple[IngestProcess, socket.socket, int, list[int]]:
    sock = await process.bind("127.0.0.1", 0)
    port = sock.getsockname()[1]
    heads: list[int] = []
    ingest = IngestProcess(
        sock, allowlist, KEY, snmpconf.DEFAULT_POLICY, directory, max_bytes, on_written=heads.append
    )
    await ingest.start()
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    return ingest, sender, port, heads


# -- the records -------------------------------------------------------------------------


def test_events_cross_the_boundary_unchanged() -> None:
    event = parse_trap("10.0.0.1", _wire(3), ts=1234.5, community_key=KEY)
    packet = quarantine_packet("10.0.0.2", b"\x30\x03\x02\x01\x07", "ber-decode-failed", 99.0)
    for original in (event, packet):
        rebuilt = records.load(records.encode(original))
        assert rebuilt.model_dump() == original.model_dump()
        assert type(rebuilt) is type(original)


@pytest.mark.parametrize("source", ["10.1.2.3", "2001:db8::7"])
def test_a_raw_record_keeps_source_port_arrival_and_bytes(source: str) -> None:
    payload = records.raw(source, 50162, 1_800_000_000.25, b"\x30\x01\x00")
    assert records.is_raw(payload)
    assert records.unraw(payload) == (source, 50162, 1_800_000_000.25, b"\x30\x01\x00")


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"R",
        b"Rshort",
        b"R" + records.RAW.pack(1.0, 5, 162) + b"x" * 8,
        b"R" + records.RAW.pack(float("nan"), 4, 162) + b"\x7f\x00\x00\x01",
    ],
)
def test_a_raw_record_that_is_not_one_is_refused(payload: bytes) -> None:
    with pytest.raises(ValueError):
        records.unraw(payload)


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
        records.decode(bad)


# -- the decoder ---------------------------------------------------------------------------


async def test_the_decoder_builds_what_the_receiver_would_in_order() -> None:
    raws = [records.raw("10.0.0.9", 162, 100.0 + i, _wire(i)) for i in range(50)]
    raws.append(records.raw("10.0.0.9", 162, 200.0, b"not snmp"))
    decoder = Decoder(KEY, snmpconf.DEFAULT_POLICY)
    decoder.start()
    try:
        got = await decoder.decode(raws)
    finally:
        decoder.close()
    for i, item in enumerate(got[:50]):
        assert isinstance(item, TrapEvent)
        expected = parse_trap("10.0.0.9", _wire(i), 100.0 + i, community_key=KEY)
        assert item.model_dump() == expected.model_dump(), "same event, same order"
    assert isinstance(got[50], QuarantinedPacket) and got[50].ts == 200.0
    accepted, quarantined, reasons = decoder.counts()
    assert (accepted, quarantined) == (50, 1) and sum(reasons.values()) == 1


async def test_the_policy_reaches_the_decoder_and_a_bad_record_is_named() -> None:
    decoder = Decoder(KEY, snmpconf.DEFAULT_POLICY)
    decoder.start()
    try:
        decoder.accepts = replace(snmpconf.DEFAULT_POLICY, v2c=False)
        refused, broken = await decoder.decode(
            [records.raw("10.0.0.9", 162, 1.0, _wire(1)), b"R" + b"\x00" * 3]
        )
    finally:
        decoder.close()
    assert isinstance(refused, QuarantinedPacket) and refused.reason == "snmp-v2c-not-accepted"
    assert isinstance(broken, str) and "raw record" in broken, "named, so the feed sets it aside"


async def test_a_dead_decoder_is_restarted_and_the_batch_still_decoded() -> None:
    decoder = Decoder(KEY, snmpconf.DEFAULT_POLICY)
    decoder.start()
    try:
        assert decoder._proc is not None
        decoder._proc.kill()
        decoder._proc.wait()
        got = await decoder.decode([records.raw("10.0.0.9", 162, 1.0, _wire(i)) for i in range(3)])
        assert all(isinstance(item, TrapEvent) for item in got) and decoder.restarts == 1
    finally:
        decoder.close()


async def test_a_datagram_that_stops_the_decoder_is_quarantined_by_its_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The batch fails; it is replayed one record at a time after a restart; the one that still
    fails is quarantined with its hash and length — never parsed here — and the rest decoded."""
    poison = records.raw("10.0.0.66", 162, 7.0, b"\x30\x82\xff\xff crash")
    real = Decoder._roundtrip

    def roundtrip(self: Decoder, payloads: list[bytes]) -> list[decoder_module.Result]:
        if poison in payloads:
            raise DecoderError("the process died")
        return real(self, payloads)

    monkeypatch.setattr(Decoder, "_roundtrip", roundtrip)
    decoder = Decoder(KEY, snmpconf.DEFAULT_POLICY)
    decoder.start()
    try:
        batch = [records.raw("10.0.0.9", 162, 1.0, _wire(1)), poison]
        batch.append(records.raw("10.0.0.9", 162, 2.0, _wire(2)))
        first, held, last = await decoder.decode(batch)
    finally:
        decoder.close()
    assert isinstance(first, TrapEvent) and isinstance(last, TrapEvent)
    assert isinstance(held, QuarantinedPacket) and held.reason == "decoder-failed"
    assert held.raw == b"" and held.length == len(b"\x30\x82\xff\xff crash") and held.ts == 7.0
    assert decoder.restarts == 2 and decoder.unreadable == 1


# -- the receiving process ---------------------------------------------------------------


async def test_every_datagram_is_spooled_raw_in_order_and_decodes_as_the_receiver_would(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "spool"
    ingest, sender, port, heads = await _started(directory)
    try:
        wires = [_wire(i) for i in range(300)]
        for wire in wires:
            sender.sendto(wire, ("127.0.0.1", port))
        got = await _collect(directory, len(wires))
        assert [records.unraw(p)[3] for p in got] == wires, "the bytes, in arrival order"
        events = await ingest.decoder.decode(got)
        for wire, item in zip(wires, events, strict=True):
            assert isinstance(item, TrapEvent)
            expected = parse_trap(item.device, wire, item.ts, community_key=KEY)
            assert item.model_dump() == expected.model_dump()
        await asyncio.sleep(1.2)  # one stats report
        assert ingest.stats.received == ingest.spooled == ingest.stats.accepted == len(wires)
        assert max(heads) == ingest.head == 300, "the parent is told what is durable"
        assert ingest.spool["head"] == 300 and ingest.spool["bytes"] > 0
        assert ingest.spool["error"] == "" and ingest.warnings() == []
    finally:
        sender.close()
        ingest.close()
        await ingest.wait_closed()


async def test_the_allowlist_reaches_the_receiving_process(tmp_path: Path) -> None:
    directory = tmp_path / "spool"
    ingest, sender, port, _heads = await _started(directory)
    try:
        ingest.networks = parse_allowlist("192.0.2.0/24")  # 127.0.0.1 is no longer allowed
        await asyncio.sleep(0.3)
        sender.sendto(_wire(2), ("127.0.0.1", port))
        await asyncio.sleep(1.3)
        assert _spooled(directory) == [], "a refused source is never spooled"
        assert ingest.stats.denied == 1 and ingest.stats.received == 1
        ingest.accepts = replace(snmpconf.DEFAULT_POLICY, v2c=False)
        assert ingest.accepts.v2c is False, "the policy is the decoder's"
    finally:
        sender.close()
        ingest.close()
        await ingest.wait_closed()


async def test_a_dead_process_is_replaced_on_the_same_socket_and_spool(tmp_path: Path) -> None:
    directory = tmp_path / "spool"
    ingest, sender, port, _heads = await _started(directory)
    try:
        sender.sendto(_wire(1), ("127.0.0.1", port))
        assert len(await _collect(directory, 1)) == 1
        await asyncio.sleep(1.2)
        assert ingest._proc is not None
        ingest._proc.kill()
        for _ in range(100):
            if ingest.restarts == 1 and ingest._proc is not None and ingest._proc.poll() is None:
                break
            await asyncio.sleep(0.1)
        assert ingest.restarts == 1 and ingest.warnings()
        sender.sendto(_wire(2), ("127.0.0.1", port))  # read by the replacement
        assert len(await _collect(directory, 2)) == 2, "the first one's record is still there"
        await asyncio.sleep(1.2)
        assert ingest.stats.received == 2, "the counters carry over a restart"
        assert "were kept" in ingest.warnings()[0]
    finally:
        sender.close()
        ingest.close()
        await ingest.wait_closed()


async def test_a_graceful_stop_spools_what_was_received(tmp_path: Path) -> None:
    directory = tmp_path / "spool"
    ingest, sender, port, _heads = await _started(directory)
    for i in range(200):
        sender.sendto(_wire(i), ("127.0.0.1", port))
    sender.close()
    await asyncio.sleep(0.5)
    ingest.close()
    await ingest.wait_closed()
    assert len(_spooled(directory)) == 200


async def test_a_spool_that_refuses_writes_is_reported_and_its_drops_counted(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "spool"
    ingest, sender, port, _heads = await _started(directory, max_bytes=1)
    try:
        sender.sendto(_wire(0), ("127.0.0.1", port))  # the spool is empty: this one fits
        await _collect(directory, 1)
        for i in range(1, 6):
            sender.sendto(_wire(i), ("127.0.0.1", port))
        for _ in range(60):
            await asyncio.sleep(0.1)
            if ingest.spool.get("error"):
                break
        assert ingest.spool["error"] == "the spool is full" and ingest.spool["waiting"] == 5
        assert "refused to write" in " ".join(ingest.warnings())
        assert len(_spooled(directory)) == 1, "nothing written past the limit"
    finally:
        sender.close()
        ingest.close()
        await ingest.wait_closed()
    assert ingest.stats.dropped == 5, "at exit, what the spool never took is counted, not silent"


async def test_a_port_in_use_fails_in_the_parent_as_it_always_did() -> None:
    held = await process.bind("127.0.0.1", 0)
    try:
        with pytest.raises(OSError):
            await process.bind("127.0.0.1", held.getsockname()[1])
    finally:
        held.close()


# -- the receiving loop, in this process (what the child runs) -----------------------------


def test_the_receiving_loop_spools_reports_and_stops(tmp_path: Path) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    events_r, events_w = os.pipe()
    control_r, control_w = os.pipe()
    events_in = Connection(events_r, writable=False)
    control_out = Connection(control_w, readable=False)
    writer = spool.SpoolWriter(tmp_path / "spool", 1 << 30)
    loop = worker.Worker(
        sock, Connection(events_w, readable=False), Connection(control_r, writable=False), writer
    )
    thread = threading.Thread(target=loop.run)
    thread.start()
    try:
        assert json.loads(events_in.recv_bytes()) == ["r", os.getpid(), 0]
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for i in range(10):
            sender.sendto(_wire(i), sock.getsockname())
        sender.sendto(b"not snmp", sock.getsockname())  # spooled raw; the decoder quarantines it
        sender.close()
        time.sleep(0.3)
        control_out.send_bytes(json.dumps(["allowlist", "10.0.0.0/8"]).encode())
        control_out.send_bytes(json.dumps(["policy", "ignored here"]).encode())
        control_out.send_bytes(b"\xff")  # not JSON: ignored
        time.sleep(0.2)
        control_out.send_bytes(json.dumps(["stop"]).encode())
        thread.join(timeout=10)
        assert not thread.is_alive()
        heads, counters = [], {}
        while events_in.poll(0.5):
            message = json.loads(events_in.recv_bytes())
            if message[0] == "w":
                heads.append(message[1])
            elif message[0] == "s":
                counters = message[1]
        assert [records.unraw(p)[3] for p in _spooled(tmp_path / "spool")][-1] == b"not snmp"
        assert heads and heads[-1] == 11, "the last notice names the last record"
        assert counters == {"received": 11, "denied": 0, "spooled": 11, "dropped": 0}
        assert loop.networks is not None and str(loop.networks[0]) == "10.0.0.0/8"
    finally:
        writer.close()
        sock.close()
        events_in.close()
        control_out.close()


def test_the_outbox_overflow_is_a_counted_drop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "OUTBOX_MAX", 2)
    writer = spool.SpoolWriter(tmp_path / "spool", 1 << 30)
    try:
        loop = worker.Worker(None, None, None, writer, "10.0.0.0/8")  # type: ignore[arg-type]
        for i in range(5):
            loop.receive(_wire(i), ("10.0.0.1", 162), 1.0)
        loop.receive(_wire(9), ("192.0.2.1", 162), 1.0)
        assert len(loop.outbox) == 2
        assert loop.counters.as_dict() == {"received": 6, "denied": 1, "spooled": 0, "dropped": 3}
    finally:
        writer.close()


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


async def test_each_trap_is_stamped_at_its_arrival_not_at_its_decode(tmp_path: Path) -> None:
    """With kernel stamps a trap's clock starts when the datagram arrived, so time spent waiting
    in the socket buffer — or in the spool — counts against its KPI."""
    directory = tmp_path / "spool"
    ingest, sender, port, _heads = await _started(directory)
    try:
        sent_at = time.time()
        for i in range(50):
            sender.sendto(_wire(i), ("127.0.0.1", port))
        got = await _collect(directory, 50)
        assert len(got) == 50
        await asyncio.sleep(0.3)
        events = await ingest.decoder.decode(got)
        stamps = [records.unraw(p)[2] for p in got]
        assert [e.ts for e in events if isinstance(e, TrapEvent)] == stamps
        assert all(sent_at - 0.05 <= s <= time.time() for s in stamps)
        if worker.kernel_stamps():
            assert stamps == sorted(stamps), "kernel stamps follow arrival order"
    finally:
        sender.close()
        ingest.close()
        await ingest.wait_closed()


async def test_a_decoder_restart_is_a_warning(tmp_path: Path) -> None:
    ingest, sender, _port, _heads = await _started(tmp_path / "spool")
    try:
        assert ingest.warnings() == []
        ingest.decoder.restarts, ingest.decoder.unreadable = 2, 1
        (warning,) = ingest.warnings()
        assert "restarted 2 time(s)" in warning and "1 datagram(s)" in warning
    finally:
        sender.close()
        ingest.close()
        await ingest.wait_closed()
