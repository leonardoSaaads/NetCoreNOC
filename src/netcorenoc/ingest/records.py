"""What the spool holds, and what the decoder hands back: bytes, never code (ADRs #449, #450).

**A raw record** is what the ingest process writes for each datagram it admits — before anything
parses it, so a trap is on disk at the speed the socket can be read (``R``, :func:`raw`)::

    b"R" | arrival (float64) | address length (4|16) | source port (uint16) | address | datagram

ADR #494 adds two envelopes around it, for a trap that belongs to a broker by how it arrived:

* ``b"P" | local port (uint16) | <R record>`` — it arrived on a broker's own UDP port;
* ``b"X" | broker id (uint32) | <R record>`` — a broker's remote proxy forwarded it, and the
  central server spooled it with the broker its token names.

An ``R`` record alone is a trap on the standard port, as it always was.

**An event** is a decoded trap or quarantined packet as a JSON array, which the decoder process
returns and the main process **validates field by field** (:func:`decode`) before it builds one:

* ``["t", device, trap_oid, instance, ts, community_tag, [[oid, kind, value], ...]]`` — a trap,
  with ``port, broker`` appended when either is set (ADR #494);
* ``["q", source, raw_b64, reason, ts, sha256, length, first8, sanitized]`` — a quarantined packet.

A spool record may also be an event (it starts with ``[``): what a test or a replay writes.

A number must be finite: ``json.loads`` accepts ``NaN`` and ``Infinity``, and a trap stamped with
either would have no deadline at all.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import socket
import struct
from typing import Any

from netcorenoc.ingest.events import QuarantinedPacket, TrapEvent, Varbind
from netcorenoc.ingest.receiver import QueueItem

__all__ = [
    "RAW",
    "as_list",
    "decode",
    "encode",
    "envelope",
    "for_broker",
    "is_raw",
    "load",
    "number",
    "on_port",
    "raw",
    "string",
    "unraw",
    "unreadable",
    "whole",
]

#: After the ``R``: arrival time, address length, source port.
RAW = struct.Struct("<dBH")
#: After the ``P``: the local port; after the ``X``: the broker.
_PORT = struct.Struct("<H")
_BROKER = struct.Struct("<I")
_FAMILY = {4: socket.AF_INET, 16: socket.AF_INET6}


def raw(source: str, port: int, ts: float, datagram: bytes) -> bytes:
    """A datagram as the ingest process spools it. ``source`` is an IPv4 or IPv6 address."""
    family = socket.AF_INET6 if ":" in source else socket.AF_INET
    address = socket.inet_pton(family, source)
    return b"R" + RAW.pack(ts, len(address), port) + address + datagram


def on_port(local_port: int, record: bytes) -> bytes:
    """A raw record that arrived on a broker's own port (ADR #494)."""
    return b"P" + _PORT.pack(local_port) + record


def for_broker(broker_id: int, record: bytes) -> bytes:
    """A raw record a broker's remote proxy forwarded (ADR #494)."""
    return b"X" + _BROKER.pack(broker_id) + record


def envelope(payload: bytes) -> tuple[int, int, bytes]:
    """``(local port, broker, R record)``: the envelope taken off, zeros where there is none."""
    if payload[:1] == b"P" and len(payload) > 1 + _PORT.size:
        return _PORT.unpack_from(payload, 1)[0], 0, payload[1 + _PORT.size :]
    if payload[:1] == b"X" and len(payload) > 1 + _BROKER.size:
        return 0, _BROKER.unpack_from(payload, 1)[0], payload[1 + _BROKER.size :]
    return 0, 0, payload


def is_raw(payload: bytes) -> bool:
    return payload[:1] in (b"R", b"P", b"X")


def unraw(payload: bytes) -> tuple[str, int, float, bytes]:
    """``(source, port, arrival, datagram)`` from a raw record, or ValueError. An envelope (ADR
    #494) is taken off first; :func:`envelope` says what it carried."""
    payload = envelope(payload)[2]
    if payload[:1] != b"R" or len(payload) < 1 + RAW.size:
        raise ValueError("not a raw record")
    ts, length, port = RAW.unpack_from(payload, 1)
    family = _FAMILY.get(length)
    start = 1 + RAW.size
    if family is None or len(payload) < start + length or not math.isfinite(ts):
        raise ValueError("a raw record with no valid address or time")
    source = socket.inet_ntop(family, payload[start : start + length])
    return source, port, ts, payload[start + length :]


def unreadable(payload: bytes, reason: str) -> QueueItem:
    """The quarantine for a raw record the decoder could not get through: **metadata only**.

    The main process never parses a datagram (F4, ADR #450) — the one that crashed the decoder is
    the last one to parse here — so nothing of its content is kept beyond what identifies it.
    """
    source, _port, ts, datagram = unraw(payload)
    return QuarantinedPacket.model_construct(
        source=source,
        raw=b"",
        reason=reason,
        ts=ts,
        sha256=hashlib.sha256(datagram).hexdigest(),
        length=len(datagram),
        first8=datagram[:8].hex(),
        sanitized=False,
    )


def as_list(item: QueueItem) -> list[Any]:
    """One event as the JSON array :func:`decode` reads."""
    if isinstance(item, TrapEvent):
        out: list[Any] = [
            "t",
            item.device,
            item.trap_oid,
            item.instance,
            item.ts,
            item.community_tag,
            [[vb.oid, vb.kind, vb.value] for vb in item.varbinds],
        ]
        if item.port or item.broker:  # ADR #494; a standard-port trap reads as it always did
            out.extend((item.port, item.broker))
        return out
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


def encode(item: QueueItem) -> bytes:
    """One event as the bytes a spool record carries."""
    return json.dumps(as_list(item), separators=(",", ":")).encode()


def string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("expected a string")
    return value


def number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("expected a number")
    if not math.isfinite(value):
        raise ValueError("expected a finite number")
    return float(value)


def whole(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("expected a whole number")
    return value


def _port(value: object) -> int:
    port = whole(value)
    if not 0 <= port <= 65535:
        raise ValueError("a port out of range")
    return port


def _broker(value: object) -> int:
    broker = whole(value)
    if not 0 <= broker <= 2**31 - 1:
        raise ValueError("a broker out of range")
    return broker


def decode(item: object) -> QueueItem:
    """One event from its JSON array, or ValueError. Every field is type-checked here: the models
    are then built without re-validation, from values already known to be what they say."""
    if not isinstance(item, list) or not item:
        raise ValueError("not an event")
    if item[0] == "t" and len(item) in (7, 9):
        varbinds = item[6]
        if not isinstance(varbinds, list):
            raise ValueError("varbinds are not a list")
        built = []
        for vb in varbinds:
            if not isinstance(vb, list) or len(vb) != 3:
                raise ValueError("a varbind is not [oid, kind, value]")
            built.append(
                Varbind.model_construct(oid=string(vb[0]), kind=string(vb[1]), value=string(vb[2]))
            )
        return TrapEvent.model_construct(
            device=string(item[1]),
            trap_oid=string(item[2]),
            instance=string(item[3]),
            ts=number(item[4]),
            community_tag=string(item[5]),
            varbinds=built,
            port=_port(item[7]) if len(item) == 9 else 0,
            broker=_broker(item[8]) if len(item) == 9 else 0,
        )
    if item[0] == "q" and len(item) == 9:
        if not isinstance(item[8], bool):
            raise ValueError("sanitized is not a boolean")
        return QuarantinedPacket.model_construct(
            source=string(item[1]),
            raw=base64.b64decode(string(item[2]), validate=True),
            reason=string(item[3]),
            ts=number(item[4]),
            sha256=string(item[5]),
            length=whole(item[6]),
            first8=string(item[7]),
            sanitized=item[8],
        )
    raise ValueError("unknown event kind")


def load(payload: bytes) -> QueueItem:
    """One event from a record's bytes, or ValueError (malformed JSON included)."""
    try:
        parsed = json.loads(payload)
    except (ValueError, RecursionError) as exc:  # bad UTF-8, bad JSON, or nested past the stack
        raise ValueError("not JSON") from exc
    return decode(parsed)
