"""The ingest process: receive traps and **spool them raw**, on a core of its own (ADRs #449, #450).

``python -m netcorenoc.ingest.worker`` is started by
:class:`netcorenoc.ingest.process.IngestProcess` and by nothing else. It does four things per
datagram and nothing more: read it, stamp its arrival (the kernel's time, :func:`arrival`), check
the source against the allowlist, and append it to the spool (`spool.py`) as a raw record
(`records.raw`). Decoding — about half a millisecond a trap — happens behind the spool, in the
decoder process (`decoder.py`), when the engine is ready for the next traps.

**That order is the point.** Measured on this branch: an ingest process that decoded each trap
before spooling it drained the socket at about 1 850 traps/s, so a 5 000/s burst overflowed an
8 MiB kernel buffer and the kernel dropped a fifth of a 20 000-trap storm — invisibly to every
process. Receiving and appending costs microseconds, so the socket is read as fast as it fills
and a storm waits on disk, where it is durable, instead of in a buffer that overflows.

Two threads: this one reads the socket; the **spool thread** takes the admitted datagrams off the
outbox in groups of up to :data:`BATCH_MAX`, appends each group with one ``fdatasync`` (group
commit: one sync for many records) and only then tells the parent the group's last sequence number.
Both spend their time in system calls, which release the interpreter lock to the other.

**What crosses to the parent is data, never code.** JSON arrays on an inherited pipe:

* ``["r", pid, head]`` — ready, with the last record already in the spool;
* ``["w", head]`` — every record up to ``head`` is durable;
* ``["s", {counter: n}, {spool state}]`` — every second and on exit.

From the parent, on a second pipe: ``["allowlist", "cidr,cidr"]``, ``["stop"]``. The bootstrap —
which descriptor is which, the allowlist, the spool's directory and size — is one JSON line on
stdin. No key and no SNMP secret reaches this process: it never parses a datagram.

**The one place a trap can still be refused for lack of room** is the outbox, and only when the
spool cannot take it: the disk refuses, or the spool holds its configured maximum. Then the outbox
fills, each further trap is counted as ``dropped``, and the parent raises it as a warning. A full
disk is a fault the operator must fix; nothing here hides it by deleting what waits to be
classified.
"""

from __future__ import annotations

import ipaddress
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
from pathlib import Path
from typing import Any

from netcorenoc.ingest import records
from netcorenoc.ingest.receiver import Network, parse_allowlist
from netcorenoc.ingest.spool import SpoolFullError, SpoolWriter

__all__ = [
    "BATCH_MAX",
    "FLUSH_S",
    "OUTBOX_MAX",
    "SO_TIMESTAMPNS",
    "Counters",
    "Worker",
    "allowed",
    "arrival",
    "kernel_stamps",
    "main",
]

#: Records per group commit.
BATCH_MAX = 1024
#: A partial group waits at most this long before it is written.
FLUSH_S = 0.005
#: Raw datagrams held in memory while the spool cannot take them (the disk refused, or the spool
#: is at its maximum): about 25 MB of small traps. The spool, not this, is the buffer for a slow
#: engine; this only rides out a disk that stalls.
OUTBOX_MAX = 100_000
#: The counters are reported this often.
STATS_EVERY_S = 1.0
#: Datagrams read per wake-up before the control pipe is looked at again.
RECV_BURST = 1024
#: The longest pause before a refused write is tried again.
RETRY_MAX_S = 1.0
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


def allowed(networks: list[Network] | None, source: str) -> bool:
    """The allowlist, as the in-process receiver applies it: none set admits every source."""
    if networks is None:
        return True
    try:
        address = ipaddress.ip_address(source)
    except ValueError:
        return False
    return any(address in net for net in networks)


class Counters:
    """What this process counts: datagrams read, refused by the allowlist, spooled, dropped."""

    __slots__ = ("denied", "dropped", "received", "spooled")

    def __init__(self) -> None:
        self.received = self.denied = self.spooled = self.dropped = 0

    def as_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in self.__slots__}


class Worker:
    def __init__(
        self,
        sock: socket.socket,
        events: Connection,
        control: Connection,
        writer: SpoolWriter,
        allowlist: str = "",
    ) -> None:
        self.sock = sock
        self.events = events
        self.control = control
        self.writer = writer
        self.networks = parse_allowlist(allowlist)
        self.counters = Counters()
        self.outbox: deque[bytes] = deque()
        self.error = ""  # why the spool refused the last write, until one succeeds
        self.refusals = 0  # writes the spool refused
        self.stopping = threading.Event()  # set by "stop", by the parent's exit, or a dead pipe
        self.closed = threading.Event()  # set once the last datagram has been read
        self.spooled = threading.Event()  # set by the spool thread when it has written it all
        self._send_lock = threading.Lock()  # both threads write to the events pipe

    def run(self) -> int:
        self._send(["r", os.getpid(), self.writer.head])
        spooler = threading.Thread(target=self._spool_loop, name="ingest-spool", daemon=True)
        spooler.start()
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
                self._report()
                reported = time.monotonic()
        self._drain()  # what the kernel already holds goes to the spool, not lost
        self.closed.set()
        self.spooled.wait(timeout=10.0)
        # Only when the spool refused to the end: what it could not take is counted, never silent.
        self.counters.dropped += len(self.outbox)
        self._report()
        selector.close()
        return 0

    def receive(self, data: bytes, addr: tuple[str, int], ts: float | None) -> None:
        """One datagram: counted, checked against the allowlist, and queued for the spool raw."""
        counters, source = self.counters, str(addr[0])
        counters.received += 1
        if not allowed(self.networks, source):
            counters.denied += 1
            return
        if len(self.outbox) >= OUTBOX_MAX:
            counters.dropped += 1
            return
        self.outbox.append(
            records.raw(source, int(addr[1]), time.time() if ts is None else ts, data)
        )

    def _drain(self) -> None:
        for _ in range(RECV_BURST):
            try:
                data, ancillary, _flags, addr = self.sock.recvmsg(65535, _ANCILLARY)
            except OSError:  # nothing left (BlockingIOError) or the socket is gone
                return
            self.receive(data, addr, arrival(ancillary, time.time()))

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
                self.networks = parse_allowlist(message[1])
            except ValueError:
                return  # the parent validates before it sends; an invalid list changes nothing

    def _report(self) -> None:
        state = {
            "head": self.writer.head,
            "bytes": self.writer.refresh_usage(),
            "max_bytes": self.writer.max_bytes,
            "waiting": len(self.outbox),
            "error": self.error,
            "refusals": self.refusals,
            "torn_bytes": self.writer.torn,
        }
        self._send(["s", self.counters.as_dict(), state])

    def _send(self, message: list[Any]) -> None:
        try:
            with self._send_lock:
                self.events.send_bytes(json.dumps(message).encode())
        except OSError:
            self.stopping.set()  # the parent stopped reading; nothing left to deliver to

    def _spool_loop(self) -> None:
        items = self.outbox
        pause = FLUSH_S
        try:
            while True:
                if not items:
                    if self.closed.is_set():
                        break
                    time.sleep(FLUSH_S)
                    continue
                group = [items[i] for i in range(min(BATCH_MAX, len(items)))]
                try:
                    head = self.writer.append(group)
                except (SpoolFullError, OSError) as exc:
                    # Kept in the outbox and tried again; what does not fit is counted as dropped.
                    self.refusals += 1
                    self.error = (
                        "the spool is full" if isinstance(exc, SpoolFullError) else str(exc)
                    )
                    if self.closed.is_set() and self.stopping.is_set() and pause >= RETRY_MAX_S:
                        break  # stopping and the disk still refuses: what is held is reported
                    time.sleep(pause)
                    pause = min(RETRY_MAX_S, pause * 2)
                    self.writer.refresh_usage()
                    continue
                for _ in group:
                    items.popleft()
                self.counters.spooled += len(group)
                self.error, pause = "", FLUSH_S
                self._send(["w", head])
        finally:
            self.spooled.set()


def main() -> int:
    """The process entry: read the bootstrap from stdin, then receive until told to stop."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # the parent decides when ingest stops
    boot = json.loads(sys.stdin.readline())
    sock = socket.socket(fileno=int(boot["socket"]))
    events = Connection(int(boot["events"]), readable=False)
    control = Connection(int(boot["control"]), writable=False)
    writer = SpoolWriter(Path(str(boot["spool"])), int(boot["spool_max"]))
    try:
        return Worker(sock, events, control, writer, str(boot.get("allowlist", ""))).run()
    finally:
        writer.close()


if __name__ == "__main__":  # pragma: no cover - the process entry, driven by IngestProcess
    sys.exit(main())
