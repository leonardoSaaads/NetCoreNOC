"""The main process's half of the ingest process: start it, carry its events, keep it running.

ADR #449. :class:`IngestProcess` binds the trap socket **here**, so a port in use or a missing
privilege fails start-up exactly as it always did, then hands the socket to
``python -m netcorenoc.ingest.worker`` (`worker.py` says what runs there and why). From then on:

* a reader thread takes each batch off the pipe, **validates every field** (:func:`decode`) — the
  boundary carries data, never code, and a malformed message is counted and dropped — and hands the
  events to the event loop, which puts them on the engine's queue in arrival order. A full queue
  counts the trap as `dropped`, the counter it has always been;
* an admin's allowlist or SNMP-policy change is forwarded as the document the store already holds;
* if the process dies it is started again on the **same socket**: datagrams the kernel buffered in
  between are read by the new one, the counters carry over, and the restart is a warning.

To the runner it looks like the in-process receiver: ``stats``, ``reasons``, ``networks``,
``accepts``, ``close()``. ``NETCORENOC_INGEST_PROCESSES=0`` keeps the receiver in this process.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import ipaddress
import json
import logging
import os
import socket
import subprocess  # nosec B404 - starts this package's own module with this interpreter
import sys
import threading
from collections.abc import Sequence
from dataclasses import fields
from multiprocessing.connection import Connection

from netcorenoc.ingest import snmpconf, worker
from netcorenoc.ingest.events import QuarantinedPacket, TrapEvent, Varbind
from netcorenoc.ingest.receiver import QueueItem, ReceiverStats, parse_allowlist

__all__ = ["RCVBUF_BYTES", "IngestProcess", "bind", "decode", "supported"]

log = logging.getLogger("netcorenoc")

#: The receive buffer asked of the kernel for the trap socket (it may grant less: `rmem_max`).
RCVBUF_BYTES = 32 * 1024 * 1024
#: How long start-up waits for the process to say it is ready.
READY_TIMEOUT_S = 15.0
#: The pause before a process that died is started again.
RESTART_DELAY_S = 1.0
_COUNTERS = tuple(f.name for f in fields(ReceiverStats))

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


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("expected a string")
    return value


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("expected a number")
    return float(value)


def _whole(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("expected a whole number")
    return value


def decode(item: object) -> QueueItem:
    """One event from the ingest process, or ValueError. Every field is type-checked here: the
    models are then built without re-validation, from values already known to be what they say."""
    if not isinstance(item, list) or not item:
        raise ValueError("not an event")
    if item[0] == "t" and len(item) == 7:
        varbinds = item[6]
        if not isinstance(varbinds, list):
            raise ValueError("varbinds are not a list")
        built = []
        for vb in varbinds:
            if not isinstance(vb, list) or len(vb) != 3:
                raise ValueError("a varbind is not [oid, kind, value]")
            built.append(
                Varbind.model_construct(
                    oid=_string(vb[0]), kind=_string(vb[1]), value=_string(vb[2])
                )
            )
        return TrapEvent.model_construct(
            device=_string(item[1]),
            trap_oid=_string(item[2]),
            instance=_string(item[3]),
            ts=_number(item[4]),
            community_tag=_string(item[5]),
            varbinds=built,
        )
    if item[0] == "q" and len(item) == 9:
        if not isinstance(item[8], bool):
            raise ValueError("sanitized is not a boolean")
        return QuarantinedPacket.model_construct(
            source=_string(item[1]),
            raw=base64.b64decode(_string(item[2]), validate=True),
            reason=_string(item[3]),
            ts=_number(item[4]),
            sha256=_string(item[5]),
            length=_whole(item[6]),
            first8=_string(item[7]),
            sanitized=item[8],
        )
    raise ValueError("unknown event kind")


class IngestProcess:
    """The ingest process, seen from the main process. See the module docstring."""

    def __init__(
        self,
        queue: asyncio.Queue[QueueItem],
        sock: socket.socket,
        allowlist: str,
        community_key: bytes | None,
        policy: snmpconf.SnmpPolicy,
    ) -> None:
        self.queue = queue
        self._sock = sock
        self._key = community_key
        self._networks = parse_allowlist(allowlist)
        self._accepts = policy
        self._base = dict.fromkeys(_COUNTERS, 0)  # what earlier incarnations counted
        self._child = dict.fromkeys(_COUNTERS, 0)  # what the running one has reported
        self._base_reasons: dict[str, int] = {}
        self._child_reasons: dict[str, int] = {}
        self.bridge_dropped = 0  # the engine's queue was full when a batch arrived
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
        total = {name: self._base[name] + self._child[name] for name in _COUNTERS}
        total["dropped"] += self.bridge_dropped
        return ReceiverStats(**total)

    @property
    def reasons(self) -> dict[str, int]:
        merged = dict(self._base_reasons)
        for reason, count in self._child_reasons.items():
            merged[reason] = merged.get(reason, 0) + count
        return merged

    @property
    def networks(self) -> list[Network] | None:
        return self._networks

    @networks.setter
    def networks(self, value: list[Network] | None) -> None:
        self._networks = value
        self._send(["allowlist", ",".join(str(n) for n in value or ())])

    @property
    def accepts(self) -> snmpconf.SnmpPolicy:
        return self._accepts

    @accepts.setter
    def accepts(self, value: snmpconf.SnmpPolicy) -> None:
        self._accepts = value
        self._send(["policy", snmpconf.to_document(value)])

    def warnings(self) -> list[str]:
        out = []
        if self.restarts:
            out.append(
                f"The ingest process stopped and was restarted {self.restarts} time(s); traps it "
                "held at that moment were lost. Check the log for the reason."
            )
        if self.invalid:
            out.append(f"{self.invalid} malformed message(s) from the ingest process were dropped.")
        return out

    # -- life cycle ---------------------------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._spawn()
        assert self._ready is not None
        try:
            pid = await asyncio.wait_for(self._ready, READY_TIMEOUT_S)
        except TimeoutError as exc:
            self.close()
            raise RuntimeError(
                f"the ingest process did not start within {READY_TIMEOUT_S:g} s; set "
                "NETCORENOC_INGEST_PROCESSES=0 to receive traps in the main process"
            ) from exc
        log.info("traps are received and decoded in process %d", pid)

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
        await asyncio.sleep(0)  # deliveries the reader scheduled run before the caller drains
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
            "key": self._key.hex() if self._key else "",
            "allowlist": ",".join(str(n) for n in self._networks or ()),
            "policy": snmpconf.to_document(self._accepts),
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
                    if message[0] == "b" and len(message) == 2 and isinstance(message[1], list):
                        items = [decode(item) for item in message[1]]
                        loop.call_soon_threadsafe(self._deliver, items)
                    elif message[0] == "s" and len(message) == 3:
                        counters = {name: _whole(message[1][name]) for name in _COUNTERS}
                        reasons = {_string(k): _whole(v) for k, v in message[2].items()}
                        loop.call_soon_threadsafe(self._counted, counters, reasons)
                    elif message[0] == "r" and len(message) == 2:
                        loop.call_soon_threadsafe(self._started, _whole(message[1]))
                    else:
                        raise ValueError("unknown message")
                except (ValueError, TypeError, KeyError, AttributeError):
                    loop.call_soon_threadsafe(self._malformed)
        finally:
            with contextlib.suppress(OSError):
                events.close()
            with contextlib.suppress(RuntimeError):  # the loop is closed: nothing to tell
                loop.call_soon_threadsafe(self._exited)

    def _deliver(self, items: Sequence[QueueItem]) -> None:
        for item in items:
            try:
                self.queue.put_nowait(item)
            except asyncio.QueueFull:
                self.bridge_dropped += 1

    def _counted(self, counters: dict[str, int], reasons: dict[str, int]) -> None:
        self._child, self._child_reasons = counters, reasons

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
        for reason, count in self._child_reasons.items():
            self._base_reasons[reason] = self._base_reasons.get(reason, 0) + count
        self._child, self._child_reasons = dict.fromkeys(_COUNTERS, 0), {}
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
