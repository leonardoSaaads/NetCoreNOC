"""ADR #450 — the spool on disk: every accepted trap is kept until it is classified.

The format is a log of CRC-checked records in segments, written by one process and read by another.
What must hold: a record read back is the record written, in order, across segments; a crash's torn
tail is cut off and never misread; a failed write leaves no partial record behind; a corrupt record
is skipped without losing the ones after it; only segments the cursor has passed are deleted; and a
second writer is refused.
"""

from __future__ import annotations

import base64
import json
import os
import stat
from pathlib import Path

import pytest

from netcorenoc.ingest import records, spool
from netcorenoc.ingest.spool import (
    HEADER,
    SpoolFullError,
    SpoolReader,
    SpoolWriter,
    frame,
    parse,
    prunable,
    segments,
)

import util

BIG = 1 << 40


def _payloads(n: int, start: int = 0) -> list[bytes]:
    return [
        records.encode(util.event(device=f"10.0.{i // 250}.{i % 250}", ts=1.0 + i))
        for i in range(start, start + n)
    ]


def _read_all(directory: Path, after: int = 0) -> list[tuple[int, bytes]]:
    reader = SpoolReader(directory, after)
    try:
        out: list[tuple[int, bytes]] = []
        while batch := reader.read(1000):
            out.extend(batch)
        return out
    finally:
        reader.close()


# -- the record --------------------------------------------------------------------------------


def test_a_record_frames_and_parses_back() -> None:
    data = frame(7, b"hello") + frame(8, b"world")
    got, end, corrupt = parse(data, 0)
    assert got == [(7, b"hello"), (8, b"world")] and end == len(data) and not corrupt


def test_an_incomplete_record_waits_and_a_damaged_one_is_reported() -> None:
    whole = frame(1, b"payload")
    assert parse(whole[:-1], 0) == ([], 0, False), "a prefix is incomplete, not corrupt"
    assert parse(whole[: HEADER.size - 1], 0) == ([], 0, False)
    flipped = whole[:-1] + bytes([whole[-1] ^ 1])
    assert parse(flipped, 0) == ([], 0, True), "a CRC mismatch is corruption"
    huge = HEADER.pack(spool.MAX_PAYLOAD + 1, 0, 1)
    assert parse(huge, 0) == ([], 0, True), "a length past the bound is corruption"
    renumbered = HEADER.pack(len(b"payload"), HEADER.unpack_from(whole)[1], 2) + b"payload"
    assert parse(renumbered, 0)[2], "the sequence number is covered by the CRC"


# -- writing and reading -----------------------------------------------------------------------


def test_what_is_written_is_read_back_in_order(tmp_path: Path) -> None:
    writer = SpoolWriter(tmp_path / "spool", BIG)
    try:
        payloads = _payloads(300)
        assert writer.append(payloads[:100]) == 100
        assert writer.append(payloads[100:]) == 300 and writer.head == 300
    finally:
        writer.close()
    got = _read_all(tmp_path / "spool")
    assert [seq for seq, _ in got] == list(range(1, 301))
    assert [payload for _, payload in got] == payloads
    assert _read_all(tmp_path / "spool", after=250) == list(
        zip(range(251, 301), payloads[250:], strict=True)
    )


def test_records_cross_segments_and_only_passed_segments_are_prunable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spool, "SEGMENT_BYTES", 500)
    directory = tmp_path / "spool"
    writer = SpoolWriter(directory, BIG)
    try:
        for i in range(40):
            writer.append(_payloads(1, i))
    finally:
        writer.close()
    found = segments(directory)
    assert len(found) > 3, "rotated into several segments"
    assert [seq for seq, _ in _read_all(directory)] == list(range(1, 41))
    assert prunable(directory, 0) == []
    second_first = found[1][0]
    assert prunable(directory, second_first - 2) == [], "the cursor has not passed the first one"
    assert prunable(directory, second_first - 1) == [found[0][1]]
    assert found[-1][1] not in prunable(directory, 10**9), "never the writer's newest"


def test_the_spool_is_private_to_the_appliance(tmp_path: Path) -> None:
    directory = tmp_path / "spool"
    writer = SpoolWriter(directory, BIG)
    try:
        writer.append(_payloads(1))
    finally:
        writer.close()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    for _first, path in segments(directory):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_second_writer_is_refused(tmp_path: Path) -> None:
    first = SpoolWriter(tmp_path / "spool", BIG)
    try:
        with pytest.raises(RuntimeError, match="another process"):
            SpoolWriter(tmp_path / "spool", BIG)
    finally:
        first.close()
    SpoolWriter(tmp_path / "spool", BIG).close()  # released with the first


def test_a_full_spool_refuses_before_writing(tmp_path: Path) -> None:
    writer = SpoolWriter(tmp_path / "spool", 1)
    try:
        writer.append(_payloads(1))  # under the limit when asked
        with pytest.raises(SpoolFullError):
            writer.append(_payloads(1, 1))
        assert writer.head == 1
    finally:
        writer.close()


def test_a_failed_write_leaves_no_partial_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "spool"
    writer = SpoolWriter(directory, BIG)
    try:
        writer.append(_payloads(2))
        real = os.fdatasync

        def refuse(fd: int) -> None:
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(os, "fdatasync", refuse)
        with pytest.raises(OSError):
            writer.append(_payloads(3, 2))  # written, then refused: cut back
        monkeypatch.setattr(os, "fdatasync", real)
        assert writer.append(_payloads(1, 5)) == 3, "numbering continues from the last whole one"
    finally:
        writer.close()
    assert [seq for seq, _ in _read_all(directory)] == [1, 2, 3]


# -- after a crash -----------------------------------------------------------------------------


def test_a_torn_tail_is_cut_off_and_numbering_continues(tmp_path: Path) -> None:
    directory = tmp_path / "spool"
    writer = SpoolWriter(directory, BIG)
    writer.append(_payloads(5))
    writer.close()
    ((_first, path),) = segments(directory)
    with path.open("ab") as handle:
        handle.write(frame(6, b"x" * 50)[:30])  # a group a crash interrupted
    writer = SpoolWriter(directory, BIG)
    try:
        assert writer.torn == 30 and writer.head == 5
        writer.append(_payloads(2, 5))
    finally:
        writer.close()
    assert [seq for seq, _ in _read_all(directory)] == [1, 2, 3, 4, 5, 6, 7]


def test_a_reader_holding_a_replaced_segment_moves_to_its_replacement(tmp_path: Path) -> None:
    """A writer that crashed before a whole record reached a new segment deletes it on restart and
    starts again under the same name; a reader that had it open must follow, not wait forever."""
    directory = tmp_path / "spool"
    writer = SpoolWriter(directory, BIG)
    writer.append(_payloads(2))
    writer.close()
    writer = SpoolWriter(directory, BIG)  # a fresh segment named 3, still empty
    writer.close()
    newest = segments(directory)[-1][1]
    newest.write_bytes(frame(3, b"y" * 40)[:20])  # the crash: a torn first record
    reader = SpoolReader(directory, 0)
    try:
        assert [seq for seq, _ in reader.read(100)] == [1, 2]
        assert reader.read(100) == [], "waits on the torn record"
        writer = SpoolWriter(directory, BIG)  # restart: the empty newest is replaced
        try:
            writer.append(_payloads(1, 2))
        finally:
            writer.close()
        assert [seq for seq, _ in reader.read(100)] == [3]
        assert reader.torn == 20
    finally:
        reader.close()


def test_a_corrupt_record_is_skipped_and_the_rest_still_read(tmp_path: Path) -> None:
    directory = tmp_path / "spool"
    writer = SpoolWriter(directory, BIG)
    try:
        writer.append(_payloads(3))
    finally:
        writer.close()
    ((_first, path),) = segments(directory)
    data = bytearray(path.read_bytes())
    second = HEADER.size + HEADER.unpack_from(data, 0)[0]  # where record 2 starts
    data[second + HEADER.size + 3] ^= 0xFF  # one bit of rot inside its payload
    path.write_bytes(bytes(data))
    reader = SpoolReader(directory, 0)
    try:
        assert [seq for seq, _ in reader.read(100)] == [1, 3]
        assert reader.corrupt > 0
    finally:
        reader.close()


def test_a_dead_letter_is_one_private_json_line(tmp_path: Path) -> None:
    directory = tmp_path / "spool"
    directory.mkdir()
    spool.dead_letter(directory, 42, b"R\x00\xff", "the engine crashed on it (KeyError)")
    path = directory / spool.DEAD_LETTER
    line = json.loads(path.read_text().splitlines()[0])
    assert line["seq"] == 42 and "KeyError" in line["reason"]
    assert base64.b64decode(line["record_b64"]) == b"R\x00\xff", "the exact bytes, not a rendering"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# -- the record's payload ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b"\xff\xfe",
        b"[" * 100_000,
        b'["t","a","b","",NaN,"",[]]',
        b'["t","a","b","",Infinity,"",[]]',
    ],
)
def test_a_payload_that_is_not_a_finite_event_is_refused(payload: bytes) -> None:
    with pytest.raises(ValueError):
        records.load(payload)
