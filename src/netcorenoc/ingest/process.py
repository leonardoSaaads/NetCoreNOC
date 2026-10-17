"""The main process's half of the ingest process: start it, hear from it, keep it running.

ADRs #449 and #450. :class:`IngestProcess` binds the trap socket **here**, so a port in use or a
missing privilege fails start-up exactly as it always did, then hands the socket and the spool's
directory to ``python -m netcorenoc.ingest.worker`` (`worker.py` says what runs there and why).
From then on:

* the process writes every trap it accepts to the spool, and a reader thread here takes its notices
  off the pipe — "written up to", the counters, the spool's state — **validating every field**: the
  boundary carries data, never code, and a malformed message is counted and ignored. The traps
  themselves are read from the spool by the engine's intake (`engine/operate/intake.py`);
* an admin's allowlist or SNMP-policy change is forwarded as the document the store already holds;
* if the process dies it is started again on the **same socket** and the same spool: datagrams the
  kernel buffered in between are read by the new one, what the old one wrote is still on disk, the
  counters carry over, and the restart is a warning.

To the runner it looks like the in-process receiver: ``stats``, ``reasons``, ``networks``,
``accepts``, ``close()``. ``NETCORENOC_INGEST_PROCESSES=0`` keeps the receiver in this process,
without a spool — for development and for hosts without POSIX descriptor inheritance only.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import socket
import subprocess  # nosec B404 - starts this package's own module with this interpreter
import sys
import threading
from collections.abc import Callable
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

from netcorenoc.ingest import snmpconf, worker
from netcorenoc.ingest.decoder import Decoder
from netcorenoc.ingest.receiver import ReceiverStats, parse_allowlist
from netcorenoc.ingest.records import string, whole

__all__ = ["RCVBUF_BYTES", "SPOOL_WARN_RATIO", "IngestProcess", "bind", "supported"]

log = logging.getLogger("netcorenoc")

#: The receive buffer asked of the kernel for the trap socket (it may grant less: `rmem_max`).
RCVBUF_BYTES = 32 * 1024 * 1024
#: How long start-up waits for the process to say it is ready.
READY_TIMEOUT_S = 15.0
#: The pause before a process that died is started again.
RESTART_DELAY_S = 1.0
#: The spool's share of its maximum at which the operator is warned.
SPOOL_WARN_RATIO = 0.8
#: What the ingest process counts (`worker.Counters`); the decoder counts the rest.
_COUNTERS = ("received", "denied", "spooled", "dropped")

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def supported() -> bool:
    """Inheriting descriptors by number is POSIX; elsewhere the receiver stays in-process."""
    return os.name == "posix"


async def bind(host: str, port: int) -> socket.socket:
    """The trap socket, bound here so a refusal is the parent's start-up error, as it always was."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_DGRAM, flags=socket.AI_PASSIVE)
    family, kind, proto, _canon, address = infos[0]
    sock = socket.socket(family, kind, proto)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RCVBUF_BYTES)
        if worker.kernel_stamps():
            sock.setsockopt(socket.SOL_SOCKET, worker.SO_TIMESTAMPNS, 1)
        sock.bind(address)
    except OSError:
        sock.close()
        raise
    granted = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
    if granted < RCVBUF_BYTES:
        log.warning(
            "the trap socket's receive buffer is %d bytes, not the %d asked for: a burst larger "
            "than it is dropped by the kernel before any process sees it. Raise the host's "
            "net.core.rmem_max (docs/configure.md)",
            granted,
            RCVBUF_BYTES,
        )
    return sock


class IngestProcess:
    """The ingest process, seen from the main process. See the module docstring."""

    def __init__(
        self,
        sock: socket.socket,
        allowlist: str,
        community_key: bytes | None,
        policy: snmpconf.SnmpPolicy,
        spool_dir: Path,
        spool_max: int,
        on_written: Callable[[int], None] | None = None,
    ) -> None:
        self._sock = sock
        self.spool_dir = spool_dir
        self.spool_max = spool_max
        self._on_written = on_written or (lambda _head: None)
        self.head = 0  # the last record the process made durable
        self.spool: dict[str, Any] = {}  # the process's latest report on the spool
        # ADR #450: decoding is behind the spool, in a process of its own; this owns it too, so
        # the runner still holds one receiver with one set of counters.
        self.decoder = Decoder(community_key, policy)
        self._networks = parse_allowlist(allowlist)
        self._base = dict.fromkeys(_COUNTERS, 0)  # what earlier incarnations counted
        self._child = dict.fromkeys(_COUNTERS, 0)  # what the running one has reported
        self.invalid = 0  # messages that failed validation (never expected; counted if seen)
        self.restarts = 0
        self._proc: subprocess.Popen[bytes] | None = None
        self._control: Connection | None = None
        self._reader: threading.Thread | None = None
        self._ready: asyncio.Future[int] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closing = False

    # -- what the runner reads and sets, as on the in-process receiver ----------------------

    @property
    def stats(self) -> ReceiverStats:
        """The receiver's five counters: three from the ingest process, two from the decoder."""
        total = {name: self._base[name] + self._child[name] for name in _COUNTERS}
        accepted, quarantined, _reasons = self.decoder.counts()
        return ReceiverStats(
            received=total["received"],
            accepted=accepted,
            denied=total["denied"],
            quarantined=quarantined,
            dropped=total["dropped"],
        )

    @property
    def spooled(self) -> int:
        return self._base["spooled"] + self._child["spooled"]

    @property
    def reasons(self) -> dict[str, int]:
        return self.decoder.counts()[2]

    @property
    def networks(self) -> list[Network] | None:
        return self._networks

    @networks.setter
    def networks(self, value: list[Network] | None) -> None:
        self._networks = value
        self._send(["allowlist", ",".join(str(n) for n in value or ())])

    @property
    def accepts(self) -> snmpconf.SnmpPolicy:
        return self.decoder.accepts

    @accepts.setter
    def accepts(self, value: snmpconf.SnmpPolicy) -> None:
        self.decoder.accepts = value  # the policy is the decoder's: this process never parses

    def warnings(self) -> list[str]:
        out = []
        if self.restarts:
            out.append(
                f"The ingest process stopped and was restarted {self.restarts} time(s). Traps it "
                "had written to the spool were kept; any it had read in the last few milliseconds "
                "and not yet written were not. Check the log for the reason."
            )
        if self.invalid:
            out.append(f"{self.invalid} malformed message(s) from the ingest process were ignored.")
        if self.decoder.restarts:
            out.append(
                f"The trap decoder stopped and was restarted {self.decoder.restarts} time(s); "
                f"{self.decoder.unreadable} datagram(s) it could not get through were quarantined "
                "by their metadata (Quarantine screen). Every other trap was decoded."
            )
        error, used = str(self.spool.get("error", "")), int(self.spool.get("bytes", 0))
        if error:
            out.append(
                f"The trap spool refused to write ({error}). Traps are held in memory until it "
                "accepts them, and counted as dropped once that is full: free disk space, or raise "
                "NETCORENOC_SPOOL_MAX_MB (docs/configure.md)."
            )
        elif self.spool_max and used >= self.spool_max * SPOOL_WARN_RATIO:
            out.append(
                f"The trap spool holds {used / 2**20:.0f} MB of its "
                f"{self.spool_max / 2**20:.0f} MB limit: classification is far behind the trap "
                "rate. Nothing is lost while it has room; check CPU and the pipeline on the "
                "Overview."
            )
        return out

    # -- life cycle ---------------------------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        await asyncio.to_thread(self.decoder.start)
        self._spawn()
        assert self._ready is not None
        try:
            pid = await asyncio.wait_for(self._ready, READY_TIMEOUT_S)
        except TimeoutError as exc:
            self.close()
            raise RuntimeError(
                f"the ingest process did not start within {READY_TIMEOUT_S:g} s (is the spool "
                f"directory {self.spool_dir} writable?); NETCORENOC_INGEST_PROCESSES=0 receives "
                "traps in the main process, without a spool"
            ) from exc
        log.info("traps are received and spooled raw in process %d", pid)

    def close(self) -> None:
        """Stop the process; what it holds is still delivered (await :meth:`wait_closed`)."""
        self._closing = True
        self._send(["stop"])

    async def wait_closed(self, timeout_s: float = 5.0) -> None:
        proc, reader = self._proc, self._reader
        if proc is not None:
            try:
                await asyncio.to_thread(proc.wait, timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
        if reader is not None:
            await asyncio.to_thread(reader.join, timeout_s)
        await asyncio.to_thread(self.decoder.close)
        await asyncio.sleep(0)  # notices the reader scheduled run before the caller drains
        with contextlib.suppress(OSError):
            if self._control is not None:
                self._control.close()
        self._sock.close()

    def _spawn(self) -> None:
        assert self._loop is not None
        events_r, events_w = os.pipe()
        control_r, control_w = os.pipe()
        boot = {
            "socket": self._sock.fileno(),
            "events": events_w,
            "control": control_r,
            "allowlist": ",".join(str(n) for n in self._networks or ()),
            "spool": str(self.spool_dir),
            "spool_max": self.spool_max,
        }
        proc = subprocess.Popen(  # nosec B603 - this interpreter, this package's module, no shell
            [sys.executable, "-m", "netcorenoc.ingest.worker"],
            stdin=subprocess.PIPE,
            pass_fds=(self._sock.fileno(), events_w, control_r),
            close_fds=True,
        )
        os.close(events_w)
        os.close(control_r)
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(boot).encode() + b"\n")
        proc.stdin.close()
        self._proc = proc
        self._control = Connection(control_w, readable=False)
        self._ready = self._loop.create_future()
        events = Connection(events_r, writable=False)
        self._reader = threading.Thread(
            target=self._read, args=(events, self._loop), name="ingest-reader", daemon=True
        )
        self._reader.start()

    def _send(self, message: list[str]) -> None:
        if self._control is None:
            return
        # A dead process cannot take it; its replacement starts from the current values.
        with contextlib.suppress(OSError):
            self._control.send_bytes(json.dumps(message).encode())

    # -- the reader thread, and what it schedules on the loop ---------------------------------

    def _read(self, events: Connection, loop: asyncio.AbstractEventLoop) -> None:
        try:
            while True:
                try:
                    raw = events.recv_bytes()
                except (EOFError, OSError):
                    break
                try:
                    message = json.loads(raw)
                    if not isinstance(message, list) or not message:
                        raise ValueError("not a message")
                    if message[0] == "w" and len(message) == 2:
                        loop.call_soon_threadsafe(self._written, whole(message[1]))
                    elif message[0] == "s" and len(message) == 3:
                        counters = {name: whole(message[1][name]) for name in _COUNTERS}
                        state = _spool_state(message[2])
                        loop.call_soon_threadsafe(self._counted, counters, state)
                    elif message[0] == "r" and len(message) == 3:
                        pid, head = whole(message[1]), whole(message[2])
                        loop.call_soon_threadsafe(self._written, head)
                        loop.call_soon_threadsafe(self._started, pid)
                    else:
                        raise ValueError("unknown message")
                except (ValueError, TypeError, KeyError, AttributeError):
                    loop.call_soon_threadsafe(self._malformed)
        finally:
            with contextlib.suppress(OSError):
                events.close()
            with contextlib.suppress(RuntimeError):  # the loop is closed: nothing to tell
                loop.call_soon_threadsafe(self._exited)

    def _written(self, head: int) -> None:
        self.head = max(self.head, head)
        self._on_written(head)

    def _counted(self, counters: dict[str, int], state: dict[str, Any]) -> None:
        self._child, self.spool = counters, state

    def _started(self, pid: int) -> None:
        if self._ready is not None and not self._ready.done():
            self._ready.set_result(pid)

    def _malformed(self) -> None:
        self.invalid += 1

    def _exited(self) -> None:
        if self._ready is not None and not self._ready.done():
            self._ready.set_exception(RuntimeError("the ingest process exited before it was ready"))
            return
        if self._closing or self._loop is None:
            return
        log.error("the ingest process exited; starting it again on the same socket")
        for name in _COUNTERS:
            self._base[name] += self._child[name]
        self._child = dict.fromkeys(_COUNTERS, 0)
        self.restarts += 1
        with contextlib.suppress(OSError):
            if self._control is not None:
                self._control.close()
        self._loop.call_later(RESTART_DELAY_S, self._respawn)

    def _respawn(self) -> None:
        if self._closing:
            return
        try:
            self._spawn()
        except OSError:
            log.exception("the ingest process could not be started again")
            assert self._loop is not None
            self._loop.call_later(RESTART_DELAY_S * 10, self._respawn)


_STATE_NUMBERS = ("head", "bytes", "max_bytes", "waiting", "refusals", "torn_bytes")


def _spool_state(value: object) -> dict[str, Any]:
    """The process's report on the spool, field by field: whole numbers and one string."""
    if not isinstance(value, dict):
        raise ValueError("the spool state is not an object")
    state: dict[str, Any] = {name: whole(value[name]) for name in _STATE_NUMBERS}
    state["error"] = string(value["error"])
    return state
