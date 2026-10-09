"""The few BER primitives the receiver walks by hand: a length, a tag-length-value, an integer.

The receiver decodes messages with `pyasn1`, which returns values and forgets where they were. Two
things need the positions instead: blanking a community in a quarantined packet (F4) and checking
an SNMPv3 MAC, which is computed over the message with its own bytes zeroed. Both walk the BER
themselves, through these, and never raise anything but :class:`BerError`.
"""

from __future__ import annotations

from dataclasses import dataclass


class BerError(ValueError):
    """The bytes are not the BER the caller expected."""


@dataclass(frozen=True)
class Tlv:
    """One element: its tag, where it begins, and where its content begins and ends."""

    tag: int
    offset: int
    start: int
    end: int


def read_length(data: bytes, i: int) -> tuple[int, int] | None:
    """Read a BER length at offset i; return (length, content_offset) or None."""
    if i >= len(data):
        return None
    first = data[i]
    i += 1
    if first < 0x80:
        return first, i
    count = first & 0x7F
    if count == 0 or count > 4 or i + count > len(data):
        return None
    return int.from_bytes(data[i : i + count], "big"), i + count


def tlv(data: bytes, i: int, tag: int | None = None) -> Tlv:
    """The element at `i`, checked to fit inside `data` and, if given, to carry `tag`."""
    if i >= len(data):
        raise BerError("truncated")
    found = data[i]
    if tag is not None and found != tag:
        raise BerError(f"expected tag {tag:#04x}, found {found:#04x}")
    length = read_length(data, i + 1)
    if length is None or length[1] + length[0] > len(data):
        raise BerError("bad length")
    return Tlv(tag=found, offset=i, start=length[1], end=length[1] + length[0])


def integer(data: bytes, element: Tlv) -> int:
    """A BER INTEGER's value (two's complement, at most eight octets here)."""
    content = data[element.start : element.end]
    if not 1 <= len(content) <= 8:
        raise BerError("bad integer")
    return int.from_bytes(content, "big", signed=True)
