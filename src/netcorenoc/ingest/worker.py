"""The ingest process: receive and decode traps on a core of their own (ADR #449).

``python -m netcorenoc.ingest.worker`` is started by
:class:`netcorenoc.ingest.process.IngestProcess` and by nothing else. Decoding a trap costs about
half a millisecond of CPU (measured: 538 µs per v2c trap through `TrapReceiver.datagram_received`),
and in the single-process appliance that half millisecond was taken from the same core that
correlates — so a storm slowed classification twice, once to decode and once to correlate, and the
kernel's socket buffer overflowed silently while the one event loop did both. Here the socket has a
reader of its own.

**The receiving code is the receiver's own.** A :class:`~netcorenoc.ingest.receiver.TrapReceiver`
runs here with an :class:`_Outbox` for its queue, so the allowlist, the SNMP policy, SNMPv3, the
parse and the quarantine are one implementation whichever process runs them, and
`datagram_received` — the method prime directive 1 is asserted on — is unchanged.

**What crosses to the parent is data, never code.** JSON arrays on an inherited pipe, which the
parent validates field by field before it builds an event (`process.decode`):

* ``["r", pid]`` — ready;
* ``["b", [item, ...]]`` — a batch, in arrival order: ``["t", device, trap_oid, instance, ts,
  community_tag, [[oid, kind, value], ...]]`` for a trap, ``["q", source, raw_b64, reason, ts,
  sha256, length, first8, sanitized]`` for a quarantined packet;
* ``["s", {counter: n}, {reason: n}]`` — the receiver's counters, every second and on exit.

From the parent, on a second pipe: ``["allowlist", "cidr,cidr"]``, ``["policy", document]`` (the
stored form, `snmpconf.to_document`), ``["stop"]``. The bootstrap — which descriptor is which, the
community HMAC key, the starting allowlist and policy — is one JSON line on stdin, so no secret is
ever in the argument list or the environment.

One process, one socket, one sender thread: the order traps leave in is the order they arrived in.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import platform
import selectors
import signal
import socket
import struct
import sys
import threading
import time
from collections import deque
from multiprocessing.connection import Connection
from typing import Any

from netcorenoc.ingest import snmpconf
from netcorenoc.ingest.events import TrapEvent
from netcorenoc.ingest.receiver import QueueItem, TrapReceiver, parse_allowlist

__all__ = [
    "BATCH_MAX",
    "FLUSH_S",
    "OUTBOX_MAX",
    "SO_TIMESTAMPNS",
    "Worker",
    "arrival",
    "encode",
    "kernel_stamps",
    "main",
]

#: Items per message to the parent.
BATCH_MAX = 256
#: A partial batch waits at most this long before it is sent.
FLUSH_S = 0.005
#: Items held here while the parent is not reading. Small on purpose: the parent's queue is the
#: buffer; this only absorbs a parent that is briefly starved of CPU.
OUTBOX_MAX = 20_000
#: The counters are reported this often.
STATS_EVERY_S = 1.0
#: Datagrams read per wake-up before the control pipe is looked at again.
RECV_BURST = 1024
#: Linux's ``SO_TIMESTAMPNS`` (the asm-generic value; the standard library does not name it). With
#: it, each datagram carries the instant the **kernel** received it, so a trap's deadline runs from
#: its real arrival even when it waited in the socket buffer during a burst. Asked for only where
#: 35 is the kernel's value, and every stamp is sanity-checked (:func:`arrival`).
SO_TIMESTAMPNS = 35
_STAMPED_MACHINES = frozenset({"x86_64", "aarch64", "armv7l", "armv8l", "riscv64", "s390x"})
_ANCILLARY = socket.CMSG_SPACE(16) if hasattr(socket, "CMSG_SPACE") else 0


def kernel_stamps() -> bool:
    """Whether this kernel and architecture give datagrams a receive time we can read."""
    return sys.platform.startswith("linux") and platform.machine() in _STAMPED_MACHINES


def arrival(ancillary: list[tuple[int, int, bytes]], now: float) -> float | None:
    """The kernel's receive time from ``recvmsg``'s ancillary data, or None (stamp the read).

    Refused when it is in the future or more than an hour old: a stamp that is not plausibly this
    datagram's arrival would move its deadline, so the read time is used instead.
    """
    for level, kind, payload in ancillary:
        if level == socket.SOL_SOCKET and kind == SO_TIMESTAMPNS and len(payload) >= 16:
            seconds, nanoseconds = struct.unpack("qq", payload[:16])
            stamp = float(seconds) + float(nanoseconds) / 1e9
            if now - 3600.0 <= stamp <= now + 1.0:
                return stamp
    return None


def encode(item: QueueItem) -> list[Any]:
    """One event as the JSON array the parent decodes."""
    if isinstance(item, TrapEvent):
        return [
            "t",
            item.device,
            item.trap_oid,
            item.instance,
            item.ts,
            item.community_tag,
            [[vb.oid, vb.kind, vb.value] for vb in item.varbinds],
        ]
    return [
        "q",
        item.source,
        base64.b64encode(item.raw).decode("ascii"),
        item.reason,
        item.ts,
        item.sha256,
        item.length,
        item.first8,
        item.sanitized,
    ]


class _Outbox:
    """The receiver's queue in this process: a bounded deque the sender thread empties.

    `put_nowait` raises `asyncio.QueueFull` when full, which is exactly what the receiver catches
    and counts as `dropped` — the same counter, the same meaning, as the in-process queue.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.items: deque[QueueItem | tuple[str, Any, Any]] = deque()

    def put_nowait(self, item: QueueItem) -> None:
        if len(self.items) >= self.limit:
            raise asyncio.QueueFull
        self.items.append(item)

    def report(self, receiver: TrapReceiver) -> None:
        """Queue the counters; outside the bound, so a full outbox still reports why it is full."""
        stats = receiver.stats
        counters = {
            "received": stats.received,
            "accepted": stats.accepted,
            "denied": stats.denied,
            "quarantined": stats.quarantined,
            "dropped": stats.dropped,
        }
        self.items.append(("s", counters, dict(receiver.reasons)))


class Worker:
    def __init__(
        self,
        sock: socket.socket,
        events: Connection,
        control: Connection,
        receiver: TrapReceiver,
        outbox: _Outbox,
    ) -> None:
        self.sock = sock
        self.events = events
        self.control = control
        self.receiver = receiver
        self.outbox = outbox
        self.stopping = threading.Event()  # set by "stop", by the parent's exit, or a dead pipe
        self.closed = threading.Event()  # set once the last item has been queued
        self.sent_all = threading.Event()  # set by the sender when it has nothing left to send

    def run(self) -> int:
        self.events.send_bytes(json.dumps(["r", os.getpid()]).encode())
        sender = threading.Thread(target=self._send_loop, name="ingest-sender", daemon=True)
        sender.start()
        self.sock.setblocking(False)
        selector = selectors.DefaultSelector()
        selector.register(self.sock, selectors.EVENT_READ, "udp")
        selector.register(self.control.fileno(), selectors.EVENT_READ, "control")
        reported = time.monotonic()
        while not self.stopping.is_set():
            for key, _ in selector.select(timeout=STATS_EVERY_S):
                if key.data == "udp":
                    self._drain()
                else:
                    self._control()
            if time.monotonic() - reported >= STATS_EVERY_S:
                self.outbox.report(self.receiver)
                reported = time.monotonic()
        self._drain()  # what the kernel already holds is the parent's, not lost
        self.outbox.report(self.receiver)
        self.closed.set()
        self.sent_all.wait(timeout=5.0)
        selector.close()
        return 0

    def _drain(self) -> None:
        for _ in range(RECV_BURST):
            try:
                data, ancillary, _flags, addr = self.sock.recvmsg(65535, _ANCILLARY)
            except OSError:  # nothing left (BlockingIOError) or the socket is gone
                return
            self.receiver.datagram_received(data, addr, arrival(ancillary, time.time()))

    def _control(self) -> None:
        try:
            message = json.loads(self.control.recv_bytes())
        except (EOFError, OSError):  # the parent is gone: so is the reason to receive
            self.stopping.set()
            return
        except ValueError:
            return  # not JSON: the parent never sends that; nothing to apply
        if not isinstance(message, list) or not message or not isinstance(message[0], str):
            return
        kind = message[0]
        if kind == "stop":
            self.stopping.set()
        elif kind == "allowlist" and len(message) == 2 and isinstance(message[1], str):
            try:
                self.receiver.networks = parse_allowlist(message[1])
            except ValueError:
                return  # the parent validates before it sends; an invalid list changes nothing
        elif kind == "policy" and len(message) == 2 and isinstance(message[1], str):
            policy, problems = snmpconf.from_document(message[1])
            if not problems:
                self.receiver.accepts = policy

    def _send_loop(self) -> None:
        items = self.outbox.items
        try:
            while True:
                if not items:
                    if self.closed.is_set():
                        break
                    time.sleep(FLUSH_S)
                    continue
                head = items[0]
                if isinstance(head, tuple):
                    items.popleft()
                    self.events.send_bytes(json.dumps(list(head)).encode())
                    continue
                batch: list[list[Any]] = []
                while items and len(batch) < BATCH_MAX and not isinstance(items[0], tuple):
                    event = items.popleft()
                    assert not isinstance(event, tuple)
                    batch.append(encode(event))
                self.events.send_bytes(json.dumps(["b", batch]).encode())
        except (BrokenPipeError, OSError):
            self.stopping.set()  # the parent stopped reading; nothing left to deliver to
        finally:
            self.sent_all.set()


def main() -> int:
    """The process entry: read the bootstrap from stdin, then receive until told to stop."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # the parent decides when ingest stops
    boot = json.loads(sys.stdin.readline())
    sock = socket.socket(fileno=int(boot["socket"]))
    events = Connection(int(boot["events"]), readable=False)
    control = Connection(int(boot["control"]), writable=False)
    key = bytes.fromhex(boot["key"]) if boot.get("key") else None
    policy = snmpconf.stored_policy(boot.get("policy"))
    outbox = _Outbox(OUTBOX_MAX)
    receiver = TrapReceiver(outbox, str(boot.get("allowlist", "")), key, policy)  # type: ignore[arg-type]
    return Worker(sock, events, control, receiver, outbox).run()


if __name__ == "__main__":  # pragma: no cover - the process entry, driven by IngestProcess
    sys.exit(main())
