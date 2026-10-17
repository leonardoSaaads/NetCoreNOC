"""Decoding behind the spool, in a process of its own (ADR #450).

The ingest process spools each datagram **raw** (`records.raw`) — receiving, checking the allowlist
and appending is all it does, so it drains the socket at the speed the kernel delivers, and a storm
waits on disk rather than in a socket buffer that overflows. Decoding — BER, SNMPv3 security, the
SNMP policy, the quarantine — happens here, when the engine is ready for the next traps, at about
half a millisecond each: far faster than classification, and on a core that is neither the
receiver's nor the engine's.

**The receiving code is still the receiver's own.** A
:class:`~netcorenoc.ingest.receiver.TrapReceiver` runs in this process with a list for its queue
(and no allowlist: the ingest process applied it), so parsing, the SNMP policy, SNMPv3 and the
quarantine are one implementation wherever they run.

Between the two processes, data and never code:

* to the decoder: ``b"D"`` and the raw records, each length-prefixed; ``b"P"`` and an SNMP-policy
  document (`snmpconf.to_document`); ``b"S"`` to stop;
* back: ``["e", [event | null, ...], {counter: n}, {reason: n}]`` — one entry per record, in order,
  each event the JSON array `records.decode` validates field by field in the main process.

**A datagram that crashes or hangs the decoder is quarantined, never lost and never retried
forever.** :meth:`Decoder.decode` restarts the process and replays the batch one record at a time;
the record it still cannot get through becomes a metadata-only quarantine (`records.unreadable`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import struct
import subprocess  # nosec B404 - starts this package's own module with this interpreter
import sys
from collections.abc import Sequence
from multiprocessing.connection import Connection
from typing import Any

from netcorenoc.ingest import records, snmpconf
from netcorenoc.ingest.receiver import QueueItem, TrapReceiver

__all__ = ["DECODE_TIMEOUT_S", "Decoder", "DecoderError", "Result", "local", "main"]

log = logging.getLogger("netcorenoc")

#: A batch the decoder has not answered in this long is a hung decoder.
DECODE_TIMEOUT_S = 30.0
_LENGTH = struct.Struct("<I")

#: One record's outcome: an event to classify, ``None`` (nothing to classify), or why it is not a
#: record at all (a string: the feed sets it aside).
Result = QueueItem | None | str


class DecoderError(Exception):
    """The decoder process died or hung on a batch."""


def _frames(payloads: Sequence[bytes]) -> bytes:
    return b"D" + b"".join(_LENGTH.pack(len(p)) + p for p in payloads)


def _unframe(blob: bytes) -> list[bytes]:
    out, at = [], 1
    while at < len(blob):
        (length,) = _LENGTH.unpack_from(blob, at)
        at += _LENGTH.size
        out.append(blob[at : at + length])
        at += length
    return out


async def local(payloads: Sequence[bytes]) -> list[Result]:
    """Records that are already events (a test's, a replay's): validated here, nothing decoded."""
    out: list[Result] = []
    for payload in payloads:
        try:
            out.append(records.load(payload))
        except ValueError as exc:
            out.append(f"not a valid event: {exc}")
    return out


class _Sink(list[QueueItem]):
    def put_nowait(self, item: QueueItem) -> None:
        self.append(item)


def _decode_batch(receiver: TrapReceiver, sink: _Sink, payloads: list[bytes]) -> list[Any]:
    out: list[Any] = []
    for payload in payloads:
        try:
            source, port, ts, datagram = records.unraw(payload)
        except ValueError as exc:
            out.append(str(exc))
            continue
        receiver.datagram_received(datagram, (source, port), ts)
        out.append(records.as_list(sink.pop()) if sink else None)
    return out


def main() -> int:
    """The process entry: decode what the main process asks for, until told to stop."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # the main process decides when this stops
    boot = json.loads(sys.stdin.readline())
    requests = Connection(int(boot["requests"]), writable=False)
    replies = Connection(int(boot["replies"]), readable=False)
    sink = _Sink()
    key = bytes.fromhex(boot["key"]) if boot.get("key") else None
    receiver = TrapReceiver(sink, "", key, snmpconf.stored_policy(boot.get("policy")))  # type: ignore[arg-type]
    while True:
        try:
            message = requests.recv_bytes()
        except (EOFError, OSError):
            return 0  # the main process is gone
        kind = message[:1]
        if kind == b"S":
            return 0
        if kind == b"P":
            policy, problems = snmpconf.from_document(message[1:].decode("utf-8", "replace"))
            if not problems:
                receiver.accepts = policy
            continue
        if kind == b"D":
            decoded = _decode_batch(receiver, sink, _unframe(message))
            stats = receiver.stats
            counters = {"accepted": stats.accepted, "quarantined": stats.quarantined}
            replies.send_bytes(json.dumps(["e", decoded, counters, receiver.reasons]).encode())


class Decoder:
    """The decoder process, seen from the main process: one request at a time, in order."""

    def __init__(self, community_key: bytes | None, policy: snmpconf.SnmpPolicy) -> None:
        self._key = community_key
        self._policy = policy
        self._proc: subprocess.Popen[bytes] | None = None
        self._requests: Connection | None = None
        self._replies: Connection | None = None
        self._lock = asyncio.Lock()
        self.accepted = self.quarantined = 0  # since start, across restarts
        self.reasons: dict[str, int] = {}
        self._counted: tuple[int, int, dict[str, int]] = (0, 0, {})  # the running process's
        self.restarts = 0
        self.unreadable = 0  # records quarantined because the decoder could not get through them

    # -- life cycle ------------------------------------------------------------------------------

    def start(self) -> None:
        requests_r, requests_w = os.pipe()
        replies_r, replies_w = os.pipe()
        boot = {
            "requests": requests_r,
            "replies": replies_w,
            "key": self._key.hex() if self._key else "",
            "policy": snmpconf.to_document(self._policy),
        }
        proc = subprocess.Popen(  # nosec B603 - this interpreter, this package's module, no shell
            [sys.executable, "-m", "netcorenoc.ingest.decoder"],
            stdin=subprocess.PIPE,
            pass_fds=(requests_r, replies_w),
            close_fds=True,
        )
        os.close(requests_r)
        os.close(replies_w)
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(boot).encode() + b"\n")
        proc.stdin.close()
        self._proc = proc
        self._requests = Connection(requests_w, readable=False)
        self._replies = Connection(replies_r, writable=False)
        self._counted = (0, 0, {})

    def close(self) -> None:
        with contextlib.suppress(OSError):
            if self._requests is not None:
                self._requests.send_bytes(b"S")
        if self._proc is not None:
            try:
                self._proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        for end in (self._requests, self._replies):
            with contextlib.suppress(OSError):
                if end is not None:
                    end.close()
        self._proc = self._requests = self._replies = None

    def _restart(self) -> None:
        if self._proc is not None:
            self._proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self._proc.wait(timeout=5.0)
        accepted, quarantined, reasons = self._counted
        self.accepted += accepted
        self.quarantined += quarantined
        for reason, n in reasons.items():
            self.reasons[reason] = self.reasons.get(reason, 0) + n
        self.close()
        self.restarts += 1
        self.start()

    # -- what the runner sets and reads ---------------------------------------------------------

    @property
    def accepts(self) -> snmpconf.SnmpPolicy:
        return self._policy

    @accepts.setter
    def accepts(self, policy: snmpconf.SnmpPolicy) -> None:
        self._policy = policy  # a restarted process starts from it
        with contextlib.suppress(OSError):
            if self._requests is not None:
                self._requests.send_bytes(b"P" + snmpconf.to_document(policy).encode())

    def counts(self) -> tuple[int, int, dict[str, int]]:
        """(accepted, quarantined, refusals by reason) since start."""
        accepted, quarantined, reasons = self._counted
        merged = dict(self.reasons)
        for reason, n in reasons.items():
            merged[reason] = merged.get(reason, 0) + n
        return self.accepted + accepted, self.quarantined + quarantined, merged

    # -- decoding -------------------------------------------------------------------------------

    async def decode(self, payloads: Sequence[bytes]) -> list[Result]:
        """One result per payload, in order. Events are decoded by the process; a record that is
        already an event is validated here (`local`). A record the process cannot get through, on
        its own, after a restart, is quarantined by its metadata."""
        if not any(records.is_raw(p) for p in payloads):
            return await local(payloads)
        async with self._lock:
            try:
                return await asyncio.to_thread(self._roundtrip, list(payloads))
            except DecoderError:
                await asyncio.to_thread(self._restart)
                if len(payloads) == 1:
                    self.unreadable += 1
                    log.error("a trap datagram stopped the decoder; quarantined by its metadata")
                    return [records.unreadable(payloads[0], "decoder-failed")]
        out: list[Result] = []
        for payload in payloads:  # one at a time, to find the one it cannot get through
            out.extend(await self.decode([payload]))
        return out

    def _roundtrip(self, payloads: list[bytes]) -> list[Result]:
        if self._requests is None or self._replies is None:
            raise DecoderError("not started")
        try:
            self._requests.send_bytes(_frames(payloads))
            if not self._replies.poll(DECODE_TIMEOUT_S):
                raise DecoderError("no answer")
            message = json.loads(self._replies.recv_bytes())
        except (EOFError, OSError, ValueError) as exc:
            raise DecoderError(str(exc)) from exc
        if not (isinstance(message, list) and len(message) == 4 and message[0] == "e"):
            raise DecoderError("not an answer")
        decoded, counters, reasons = message[1], message[2], message[3]
        if not isinstance(decoded, list) or len(decoded) != len(payloads):
            raise DecoderError("an answer of the wrong length")
        try:
            accepted, quarantined = (
                records.whole(counters["accepted"]),
                records.whole(counters["quarantined"]),
            )
            self._counted = (
                accepted,
                quarantined,
                {records.string(k): records.whole(v) for k, v in reasons.items()},
            )
        except (TypeError, KeyError, AttributeError, ValueError) as exc:
            raise DecoderError("counters that are not counts") from exc
        out: list[Result] = []
        for entry in decoded:
            if entry is None or isinstance(entry, str):
                out.append(entry if entry is None else f"not a raw record: {entry}"[:200])
                continue
            try:
                out.append(records.decode(entry))
            except ValueError as exc:
                out.append(f"the decoder returned an invalid event: {exc}")
        return out


if __name__ == "__main__":  # pragma: no cover - the process entry, driven by Decoder
    sys.exit(main())
