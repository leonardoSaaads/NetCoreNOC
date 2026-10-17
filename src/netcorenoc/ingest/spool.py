"""The trap spool: every accepted trap is on disk before anything else is done with it (ADR #450).

Before this, a trap lived in memory from the socket to the commit that classified it — in the
ingest process's outbox, on a pipe, on the engine's queue — and a full queue, a restart or a crash
discarded what was there. A storm is exactly when classification falls behind, so the traps most
likely to be lost were the ones that arrived when the network was failing.

The design is the one Zabbix and Kafka use, for the same reason:

* **Zabbix** has `snmptrapd` append each trap to a file and the server's SNMP trapper read that
  file, keeping how far it got (`snmp_lastsize`) in its database, so a slow or restarted server
  resumes where it stopped and the receiver never waits for it.
* **Kafka** writes an append-only log in segments named by their first offset, and a consumer
  commits the offset it has processed; a segment wholly before every committed offset is deleted.

Here the ingest process (``worker.py``) is the only writer and the main process the only reader.
The reader's position — the **cursor**, the sequence number of the last record whose trap is
classified — is written in the same SQLite transaction as the batch that classified it, so the
database and the cursor cannot disagree: after any crash, reading resumes at the record after the
cursor and nothing is classified twice or skipped.

## On disk

A directory (mode 0700) of segment files named by the sequence number of their first record,
``00000000000000000001.seg`` and on. Each record is::

    length  uint32 LE   the payload's size in bytes
    crc     uint32 LE   CRC-32 over the 8 sequence bytes, then the payload
    seq     uint64 LE   one more than the record before it
    payload             the event as ``records.encode`` writes it

The writer holds an exclusive lock on ``spool.lock``, appends to the newest segment only, and makes
each group of records durable with one ``fdatasync`` before it tells the reader their sequence
number (group commit). It starts a new segment at :data:`SEGMENT_BYTES` and at every start, so a
segment that is not the newest is never written again; at start it cuts off a torn tail — a group a
crash interrupted — from the newest one. A failed write is cut back the same way at once, so a
record never follows a partial one.
"""

from __future__ import annotations

import base64
import fcntl
import itertools
import json
import os
import re
import struct
import time
import zlib
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO

__all__ = [
    "HEADER",
    "MAX_PAYLOAD",
    "SEGMENT_BYTES",
    "SpoolFullError",
    "SpoolReader",
    "SpoolWriter",
    "dead_letter",
    "frame",
    "parse",
    "prunable",
    "segments",
    "usage",
]

HEADER = struct.Struct("<IIQ")
#: A new segment is started once the newest reaches this size.
SEGMENT_BYTES = 64 * 1024 * 1024
#: A record's payload is never larger: a datagram is at most 64 KiB, and its base64 in a
#: quarantined record about 88 KiB. A header claiming more is corruption, not a record.
MAX_PAYLOAD = 256 * 1024
#: What the reader asks the file for at a time.
READ_CHUNK = 1024 * 1024
LOCK_NAME = "spool.lock"
DEAD_LETTER = "dead-letter.jsonl"
_SEGMENT = re.compile(r"^(\d{20})\.seg$")


class SpoolFullError(Exception):
    """The spool holds its configured maximum; nothing more is written until the reader catches
    up and old segments are deleted."""


def frame(seq: int, payload: bytes) -> bytes:
    """One record: the header, then the payload."""
    crc = zlib.crc32(payload, zlib.crc32(seq.to_bytes(8, "little")))
    return HEADER.pack(len(payload), crc, seq) + payload


def parse(buffer: bytes, offset: int) -> tuple[list[tuple[int, bytes]], int, bool]:
    """The complete, intact records in ``buffer`` from ``offset``: ``(records, end, corrupt)``.

    Stops at the first record that is incomplete (``corrupt`` False: more bytes may come) or that
    fails its length bound or its CRC (``corrupt`` True, ``end`` at that record).
    """
    out: list[tuple[int, bytes]] = []
    size = len(buffer)
    while size - offset >= HEADER.size:
        length, crc, seq = HEADER.unpack_from(buffer, offset)
        if length > MAX_PAYLOAD:
            return out, offset, True
        end = offset + HEADER.size + length
        if end > size:
            break
        payload = buffer[offset + HEADER.size : end]
        if zlib.crc32(payload, zlib.crc32(seq.to_bytes(8, "little"))) != crc:
            return out, offset, True
        out.append((seq, payload))
        offset = end
    return out, offset, False


def segments(directory: Path) -> list[tuple[int, Path]]:
    """``(first sequence number, path)`` of every segment, oldest first."""
    try:
        names = os.listdir(directory)
    except FileNotFoundError:
        return []
    found = [(int(m.group(1)), directory / name) for name in names if (m := _SEGMENT.match(name))]
    return sorted(found)


def usage(directory: Path) -> int:
    """Bytes the segments occupy."""
    total = 0
    for _first, path in segments(directory):
        try:
            total += path.stat().st_size
        except FileNotFoundError:
            continue  # deleted by the reader between the listing and the stat
    return total


def prunable(directory: Path, cursor: int) -> list[Path]:
    """Segments every record of which is at or before ``cursor``. Never the newest: the writer's."""
    found = segments(directory)
    return [path for (_first, path), (after, _) in itertools.pairwise(found) if after <= cursor + 1]


def dead_letter(directory: Path, seq: int, payload: bytes, reason: str) -> None:
    """Keep a record that could not be classified, for an administrator: one JSON line, the record
    itself in base64 — exactly the bytes, so it can be appended to a spool again once fixed."""
    line = json.dumps(
        {
            "seq": seq,
            "at": time.time(),
            "reason": reason,
            "record_b64": base64.b64encode(payload).decode("ascii"),
        }
    )
    fd = os.open(directory / DEAD_LETTER, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line.encode() + b"\n")
        os.fsync(fd)
    finally:
        os.close(fd)


def _sync_directory(directory: Path) -> None:
    """Make a new file's name durable, not only its bytes."""
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class SpoolWriter:
    """The one writer. Thread-confined: the ingest process's spool thread is its only caller."""

    def __init__(self, directory: Path, max_bytes: int) -> None:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory = directory
        self.max_bytes = max_bytes
        self._lock = os.open(directory / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self._lock)
            raise RuntimeError(f"another process is writing the spool in {directory}") from exc
        self.torn = 0  # bytes cut off a torn tail at start
        self.next_seq = self._recover()
        self._fd = -1
        self._size = 0
        self._open_segment()
        self.used = usage(directory)

    @property
    def head(self) -> int:
        """The sequence number of the last durable record (0 before the first)."""
        return self.next_seq - 1

    def _recover(self) -> int:
        found = segments(self.directory)
        if not found:
            return 1
        first, path = found[-1]
        data = path.read_bytes()
        records, valid, _corrupt = parse(data, 0)
        if valid < len(data):
            self.torn = len(data) - valid
            os.truncate(path, valid)
        if records:
            return records[-1][0] + 1
        path.unlink()  # an empty newest segment: started again under the same name
        return first

    def _open_segment(self) -> None:
        path = self.directory / f"{self.next_seq:020d}.seg"
        self._fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC, 0o600)
        self._size = os.fstat(self._fd).st_size
        _sync_directory(self.directory)

    def append(self, payloads: Sequence[bytes]) -> int:
        """Write ``payloads`` as consecutive records, durable on return; the last one's number.

        Raises :class:`SpoolFullError` before writing anything when the spool is at its maximum, and
        ``OSError`` when the disk refuses — after cutting the segment back to its last whole record,
        so the caller can keep the events and try again.
        """
        if self.used >= self.max_bytes:
            raise SpoolFullError
        buffer = bytearray()
        seq = self.next_seq
        for payload in payloads:
            buffer += frame(seq, payload)
            seq += 1
        try:
            view = memoryview(buffer)
            while view:
                view = view[os.write(self._fd, view) :]
            os.fdatasync(self._fd)
        except OSError:
            os.ftruncate(self._fd, self._size)
            raise
        self.next_seq = seq
        self._size += len(buffer)
        self.used += len(buffer)
        if self._size >= SEGMENT_BYTES:
            os.close(self._fd)
            self._open_segment()
        return self.head

    def refresh_usage(self) -> int:
        """Re-measure the spool: the reader deletes the segments it no longer needs."""
        self.used = usage(self.directory)
        return self.used

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1
        os.close(self._lock)  # releases the lock


class SpoolReader:
    """Reads the records after ``after``, in order, across segments. One caller at a time."""

    def __init__(self, directory: Path, after: int) -> None:
        self.directory = directory
        self.after = after  # the last sequence number returned
        self.torn = 0  # bytes left at the end of a segment the writer will not finish
        self.corrupt = 0  # bytes skipped to resynchronise after a record failed its CRC
        self.skipped = 0  # sequence numbers missing from the spool (segments deleted by hand)
        self._file: BinaryIO | None = None
        self._first = 0
        self._buffer = b""
        self._offset = 0

    def read(self, limit: int) -> list[tuple[int, bytes]]:
        """Up to about ``limit`` records after the last one returned; empty when there are none."""
        out: list[tuple[int, bytes]] = []
        while len(out) < limit:
            if self._file is None and not self._open(self.after + 1):
                break
            assert self._file is not None
            records, self._offset, corrupt = parse(self._buffer, self._offset)
            for seq, payload in records:
                if seq > self.after:
                    out.append((seq, payload))
                    self.after = seq
            if corrupt:
                self._resync()
                continue
            if records:
                continue
            chunk = self._file.read(READ_CHUNK)
            if chunk:
                self._buffer = self._buffer[self._offset :] + chunk
                self._offset = 0
                continue
            if not self._finished():
                break
        return out

    def _resync(self) -> None:
        """Past a record that failed its check: over it when its length still leads to an intact
        record (rot in a payload), else one byte at a time until one is found (a damaged header)."""
        length = HEADER.unpack_from(self._buffer, self._offset)[0]
        jump = self._offset + HEADER.size + length
        step = jump - self._offset if length <= MAX_PAYLOAD and parse(self._buffer, jump)[0] else 1
        self.corrupt += step
        self._offset += step

    def _finished(self) -> bool:
        """At the end of the current segment: move on if the writer has moved on, else False.

        The writer has moved on when a later segment exists, or when this one was **replaced**: a
        writer that restarts after a crash deletes a newest segment that holds no whole record and
        starts again under the same name, so the name is the same and the file is not.
        """
        later = [entry for entry in segments(self.directory) if entry[0] >= self._first]
        replaced = bool(later) and later[0][0] == self._first and not self._same(later[0][1])
        later = [entry for entry in later if entry[0] > self._first or replaced]
        if not later:
            return False
        assert self._file is not None
        rest = self._file.read()  # what was appended between our last read and the listing
        if rest:
            self._buffer = self._buffer[self._offset :] + rest
            self._offset = 0
            return True  # parse it first; the next pass comes back here
        self.torn += len(self._buffer) - self._offset
        self.close()
        first = later[0][0]
        self.skipped += max(0, first - (self.after + 1))
        return self._open(first)

    def _same(self, path: Path) -> bool:
        assert self._file is not None
        try:
            return path.stat().st_ino == os.fstat(self._file.fileno()).st_ino
        except FileNotFoundError:
            return True  # gone again; the next listing decides

    def _open(self, want: int) -> bool:
        found = segments(self.directory)
        if not found:
            return False
        holding = [entry for entry in found if entry[0] <= want]
        first, path = holding[-1] if holding else found[0]
        if not holding:
            self.skipped += first - want
        try:
            self._file = path.open("rb")
        except FileNotFoundError:
            return False  # deleted between the listing and the open; the next pass lists again
        self._first = first
        self._buffer = b""
        self._offset = 0
        return True

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
