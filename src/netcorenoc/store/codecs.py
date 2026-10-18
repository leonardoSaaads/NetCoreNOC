"""The type codecs every store connection registers (ADR #452).

asyncpg decodes `timestamptz` to `datetime`, `inet` to `ipaddress`, `jsonb` to text it expects to
parse, and `numeric` to `Decimal`; the store computes in epoch seconds, strings and plain numbers.
These codecs make the mapping exact:

* an **instant** is microseconds on disk and a float in Python — a value with at most six decimals
  round-trips bit for bit, and ``inf``/``-inf`` are PostgreSQL's ``infinity``;
* an **address** (`inet`, `cidr`) and a **JSON document** (`json`, `jsonb`) are their text;
* a **`numeric`** (what ``SUM(bigint)``, ``AVG(integer)`` and ``extract()`` return) is an ``int``
  when it is whole and a ``float`` otherwise;
* a **`boolean`** is a ``bool`` that also accepts the 0/1 the code computed for SQLite.

The instant codec is the binary "tuple" form, so it also serves ``COPY`` (the importer's); the
others are text-format and are for statements only.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg

__all__ = ["decode_instant", "encode_instant", "setup"]

#: PostgreSQL counts microseconds from 2000-01-01 UTC; the code counts seconds from 1970.
_PG_EPOCH_US = 946_684_800_000_000
_INFINITY = 0x7FFF_FFFF_FFFF_FFFF


def encode_instant(value: Any) -> tuple[int]:
    if hasattr(value, "timestamp"):  # a datetime, for a caller that has one
        value = value.timestamp()
    seconds = float(value)
    if seconds == float("inf"):
        return (_INFINITY,)
    if seconds == float("-inf"):
        return (-_INFINITY - 1,)
    return (round(seconds * 1_000_000) - _PG_EPOCH_US,)


def decode_instant(value: tuple[int]) -> float:
    (microseconds,) = value
    if microseconds == _INFINITY:
        return float("inf")
    if microseconds == -_INFINITY - 1:
        return float("-inf")
    return (microseconds + _PG_EPOCH_US) / 1_000_000


def _decode_numeric(text: str) -> int | float:
    return int(text) if text.lstrip("-").isdigit() else float(text)


def _encode_bool(value: Any) -> str:
    return "t" if value else "f"


def _decode_bool(text: str) -> bool:
    return text == "t"


def _encode_json(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value)


async def setup(raw: asyncpg.Connection) -> None:
    """Register the codecs on one connection (the writer, and each reader as the pool opens it)."""
    await raw.set_type_codec(
        "timestamptz",
        schema="pg_catalog",
        encoder=encode_instant,
        decoder=decode_instant,
        format="tuple",
    )
    for name in ("inet", "cidr"):
        await raw.set_type_codec(name, schema="pg_catalog", encoder=str, decoder=str, format="text")
    for name in ("json", "jsonb"):
        await raw.set_type_codec(
            name, schema="pg_catalog", encoder=_encode_json, decoder=str, format="text"
        )
    await raw.set_type_codec(
        "numeric", schema="pg_catalog", encoder=str, decoder=_decode_numeric, format="text"
    )
    await raw.set_type_codec(
        "bool", schema="pg_catalog", encoder=_encode_bool, decoder=_decode_bool, format="text"
    )
