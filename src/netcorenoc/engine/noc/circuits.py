"""A customer's circuits: what each one is, and the path it takes end to end (ADR #473).

A circuit is its **designation** (``SILLA-TO-QATAR-11-ETH 10G-P011-011``), its two ends, its
bandwidth and service, its status, owner and provider, and its **path**: the hops a signal takes
from one client port to the other — client port, line port, the ODF it leaves through, the next
ODF, a cross-connect, a patch cord, the far client port. Each hop is where it is (``site``), what it
is (``segment``) and its coordinates (``fields``: shelf, slot, port, mode, an ODF's Tx/Rx pair…).

**Reading a customer's sheet** (:func:`parse_table`). Circuit lists are kept as wide spreadsheets
whose header is several rows deep: a row of sites (``SILA PoP``, ``QATAR``…), under it a row of what
each group of columns is (``Client Port``, ``Line side Port OCLD``, ``MEETS ODF``…), and under that
the row naming each column (``Circuit Designation``, ``Terminal A``… then ``Shelf``, ``Slot``,
``Port``, ``Mode``). The row naming the circuit's designation is the header; the up to two rows
above it that name more than one thing are its bands — sites, then segments — each carried
rightwards over the columns its merged cell spanned. A column under a segment is part of the
path, grouped with its neighbours under the same site and segment into one hop (a segment that
names a field twice, a line card's two ports, is two hops); a column the circuit's own fields
recognise (:data:`ALIASES`) is that field; any other column is kept as an attribute. ``NA`` and
``-`` are nothing. An Excel date that arrives as its serial number is written as a date.

Pure.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "ALIASES",
    "FIELDS",
    "MAX_CIRCUITS",
    "CircuitError",
    "clean",
    "hop_text",
    "parse_table",
    "read_text_table",
    "search_text",
]

#: The circuit's own fields, in the order a list shows them, with their labels and length caps.
FIELDS: dict[str, tuple[str, int]] = {
    "designation": ("Circuit", 200),
    "a_end": ("A end", 120),
    "z_end": ("Z end", 120),
    "bandwidth": ("Bandwidth", 40),
    "service": ("Service", 80),
    "status": ("Status", 40),
    "owner": ("Owner", 120),
    "provider": ("Provider", 120),
    "rfs_date": ("Ready for service", 40),
    "cease_date": ("Ceased", 40),
}
#: Header words each field is recognised by, most specific first (lower case, spaces collapsed).
ALIASES: dict[str, tuple[str, ...]] = {
    "designation": ("circuit designation", "circuit name", "circuit id", "circuit", "service id"),
    "a_end": ("terminal a", "a end", "a-end", "site a", "a side", "end a"),
    "z_end": ("terminal b", "z end", "z-end", "b end", "b-end", "site b", "z side", "end b"),
    "bandwidth": ("bw", "bandwidth", "speed"),
    "service": ("capacity type", "service type", "service", "interface type"),
    "status": ("status",),
    "owner": ("circuit owner/operational owner", "circuit owner", "operational owner", "owner"),
    "provider": ("front ending party", "provider", "carrier", "vendor"),
    "rfs_date": ("rfs date", "rfs", "ready for service"),
    "cease_date": ("cease date", "cease"),
}
#: Columns that only number the rows.
_SERIAL = {"sr", "sr.", "s.no", "s.no.", "no", "no.", "#", "item"}
_EMPTY = {"", "na", "n/a", "-", "--", "none", "null"}
MAX_CIRCUITS = 5000
MAX_HOPS = 60
MAX_TABLE = 4 * 1024 * 1024


class CircuitError(ValueError):
    """A circuit, or a sheet of them, this appliance will not keep; the message says what."""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _value(text: Any) -> str:
    value = re.sub(r"[ \t]+", " ", str(text or "")).strip()
    return "" if value.lower() in _EMPTY else value


def _date(value: str) -> str:
    """An Excel serial (``43723``) as the date it is; anything else as it was written."""
    if re.fullmatch(r"\d{5}(\.0+)?", value) and 20000 < float(value) < 80000:
        return (dt.date(1899, 12, 30) + dt.timedelta(days=int(float(value)))).isoformat()
    return value


def read_text_table(text: str) -> list[list[str]]:
    """Rows from pasted or exported text: tab-separated when it has tabs, else comma-separated.
    Quoted cells may span lines, as a spreadsheet writes a cell with a line break."""
    if len(text) > MAX_TABLE:
        raise CircuitError(f"the table is larger than {MAX_TABLE // (1024 * 1024)} MiB")
    delimiter = "\t" if "\t" in text else ","
    return [list(row) for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def _header_row(rows: Sequence[Sequence[str]]) -> int:
    for index, row in enumerate(rows[:50]):
        cells = {_norm(c) for c in row}
        if cells & {"circuit designation", "circuit name", "circuit id", "circuit"}:
            return index
    raise CircuitError(
        "no header row: one row must name the circuit (Circuit Designation, Circuit ID or Circuit)"
    )


def _bands(rows: Sequence[Sequence[str]], header: int, width: int) -> list[list[str]]:
    """The rows of groups above the header, outermost first, each carried right over its span."""
    out: list[list[str]] = []
    index = header - 1
    while index >= 0 and len(out) < 2:
        row = [" ".join(str(c or "").split()) for c in rows[index]]
        named = [c for c in row if c]
        index -= 1
        if len(named) <= 1:
            continue  # empty, or a title over the whole sheet
        filled: list[str] = []
        current = ""
        for col in range(width):
            cell = row[col] if col < len(row) else ""
            current = cell or current
            filled.append(current)
        out.insert(0, filled)
    return out


def _columns(header: Sequence[str]) -> dict[str, int]:
    names = [_norm(h) for h in header]
    taken: dict[str, int] = {}
    for field, aliases in ALIASES.items():
        for alias in aliases:
            col = next(
                (i for i, n in enumerate(names) if n == alias and i not in taken.values()), None
            )
            if col is not None:
                taken[field] = col
                break
    return taken


def parse_table(rows: Sequence[Sequence[str]]) -> list[dict[str, Any]]:
    """Every circuit in a sheet, with its fields, attributes and path (module docstring)."""
    header_at = _header_row(rows)
    header = [" ".join(str(c or "").split()) for c in rows[header_at]]
    width = max([len(header)] + [len(r) for r in rows[header_at + 1 :]])
    header += [""] * (width - len(header))
    bands = _bands(rows, header_at, width)
    fields = _columns(header)
    if "designation" not in fields:
        raise CircuitError("no column names the circuit")
    core = set(fields.values())
    # The band right above the header says what each group of columns is (its segment); the one
    # above that, where (its site). A column under no segment is an attribute, whatever site a
    # merged cell above it was carried over.
    segments = bands[-1] if bands else [""] * width
    sites = bands[0] if len(bands) > 1 else [""] * width
    groups: list[tuple[str, str, list[int]]] = []
    attributes: list[int] = []
    for col in range(width):
        name = header[col]
        if col in core or not name or _norm(name) in _SERIAL:
            continue
        if not segments[col]:
            attributes.append(col)
            continue
        site, segment = sites[col], segments[col]
        last = groups[-1] if groups else None
        # A segment that names a field twice (a line card's two ports) is two hops.
        if last and last[:2] == (site, segment) and name not in {header[c] for c in last[2]}:
            last[2].append(col)
        else:
            groups.append((site, segment, [col]))
    circuits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows[header_at + 1 :]:

        def cell(col: int, row: Sequence[str] = row) -> str:
            return _value(row[col]) if col < len(row) else ""

        designation = cell(fields["designation"])
        if not designation or designation in seen:
            continue
        seen.add(designation)
        circuit: dict[str, Any] = {
            name: (_date(cell(col)) if name.endswith("_date") else cell(col))
            for name, col in fields.items()
        }
        circuit["attributes"] = {}
        for col in attributes:
            if cell(col):
                key, n = header[col], 2
                while key in circuit["attributes"]:
                    key, n = f"{header[col]} {n}", n + 1
                circuit["attributes"][key] = cell(col)
        path = []
        for site, segment, cols in groups:
            values: dict[str, str] = {}
            for col in cols:
                value = cell(col)
                if not value:
                    continue
                key, n = header[col], 2
                while key in values:
                    key, n = f"{header[col]} {n}", n + 1
                values[key] = value
            if values:
                path.append({"site": site, "segment": segment, "fields": values})
        circuit["path"] = path
        circuits.append(circuit)
        if len(circuits) > MAX_CIRCUITS:
            raise CircuitError(f"more than {MAX_CIRCUITS} circuits in one sheet")
    if not circuits:
        raise CircuitError("the sheet names no circuit below its header")
    return clean_many(circuits)


def clean_many(circuits: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [clean(c) for c in circuits]


def _short(text: Any, cap: int) -> str:
    return str(text or "").strip()[:cap]


def clean(circuit: Mapping[str, Any]) -> dict[str, Any]:
    """A circuit as it is stored: every field bounded, its path and attributes well formed."""
    out: dict[str, Any] = {
        name: _short(circuit.get(name), cap) for name, (_l, cap) in FIELDS.items()
    }
    if not out["designation"]:
        raise CircuitError("designation: required")
    attributes = circuit.get("attributes") or {}
    if not isinstance(attributes, Mapping):
        raise CircuitError("attributes: an object")
    out["attributes"] = {
        _short(k, 80): _short(v, 500) for k, v in list(attributes.items())[:60] if _short(k, 80)
    }
    path = circuit.get("path") or []
    if not isinstance(path, Sequence) or isinstance(path, str) or len(path) > MAX_HOPS:
        raise CircuitError(f"path: a list of at most {MAX_HOPS} hops")
    hops = []
    for hop in path:
        if not isinstance(hop, Mapping):
            raise CircuitError("path: each hop an object")
        values = hop.get("fields") or {}
        if not isinstance(values, Mapping):
            raise CircuitError("path: a hop's fields are an object")
        cleaned = {
            _short(k, 60): _short(v, 300) for k, v in list(values.items())[:30] if _short(v, 300)
        }
        site, segment = _short(hop.get("site"), 120), _short(hop.get("segment"), 120)
        if cleaned or site or segment:
            hops.append({"site": site, "segment": segment, "fields": cleaned})
    out["path"] = hops
    out["notes"] = _short(circuit.get("notes"), 8000)
    if len(json.dumps(out)) > 96_000:
        raise CircuitError("the circuit is larger than this appliance keeps")
    return out


def hop_text(hop: Mapping[str, Any]) -> str:
    """One hop in a line: where, what, and its coordinates."""
    where = " · ".join(p for p in (hop.get("site"), hop.get("segment")) if p)
    coordinates = ", ".join(f"{k} {v}" for k, v in (hop.get("fields") or {}).items())
    return f"{where}: {coordinates}" if where and coordinates else where or coordinates


def search_text(circuit: Mapping[str, Any]) -> str:
    """What a circuit is found by: its fields, attributes, every hop and its notes, lower case."""
    parts = [str(circuit.get(name) or "") for name in FIELDS]
    parts += [f"{k} {v}" for k, v in (circuit.get("attributes") or {}).items()]
    parts += [hop_text(h) for h in circuit.get("path") or []]
    parts.append(str(circuit.get("notes") or ""))
    return re.sub(r"\s+", " ", " ".join(parts)).lower()[:20000]
