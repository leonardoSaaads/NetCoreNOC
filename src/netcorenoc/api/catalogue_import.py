"""Reading an operator's trap list: a CSV or text file of **class, trap OID, vendor, severity**
(v0.22.0, item 17, ADR #385).

The plug-and-play the maintainer keeps asking for: a customer with a MIB list should not have to
wait for traps to arrive before the console can name them. The engineering is where this goes
wrong, so it is stated in the code:

* **No MIB compiler and no dependency.** Rows of text, read with the standard `csv` module.
* **Every row is validated and every failure is reported** — line, field, rule — and **a file with
  any failure imports nothing.** All-or-nothing was chosen over "up to the first error": a partial
  import leaves the catalogue in a state that matches no file anyone has, and re-importing a fixed
  file is idempotent (one row per OID), so all-or-nothing costs the operator one retry and never a
  cleanup. The report says which it was.
* **Bounded** — :data:`MAX_BYTES`, :data:`MAX_ROWS` and :data:`DEADLINE_S` — and never executed:
  the text is split into cells and each cell is matched against a pattern. Nothing in it is
  interpreted.

A parsed row is an **imported** rule: cosmetic plus severity, not evidence (`store/class_rules.py`).
"""

from __future__ import annotations

import csv
import io
import re
import time
from dataclasses import dataclass, field

from netcorenoc.ingest import known_oids

#: The largest file accepted, in bytes. A vendor's full notification list is a few hundred rows.
MAX_BYTES = 256 * 1024
#: The most data rows accepted.
MAX_ROWS = 5000
#: Parsing stops, and the file is refused, if it takes longer than this.
DEADLINE_S = 2.0
#: How many errors a report lists. The count is always exact; the list is for reading.
MAX_LISTED = 200

MAX_NAME = 80
MAX_VENDOR = 64
MAX_ARCS = 128

#: The column names a header may use, and the field each means. Case and spacing are ignored.
COLUMNS = {
    "class": "name",
    "name": "name",
    "class name": "name",
    "trap_oid": "oid",
    "trap oid": "oid",
    "oid": "oid",
    "vendor": "vendor",
    "severity": "severity",
}

#: The severities a row may declare: X.733's five. `cleared` is a state, not a grade.
SEVERITIES = tuple(t for t in known_oids.SEVERITY_VOCAB if t != "cleared")

_OID = re.compile(r"^[0-9]+(\.[0-9]+)+$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class Problem:
    line: int
    field: str
    rule: str


@dataclass(frozen=True)
class Row:
    line: int
    oid: str
    name: str | None
    severity: str | None
    vendor: str | None


@dataclass
class Parsed:
    rows: list[Row] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    #: A problem with the file as a whole — too big, no header, too slow. Nothing is imported.
    refused: str | None = None

    @property
    def ok(self) -> bool:
        return self.refused is None and not self.problems


def normalise_oid(raw: str) -> str:
    """`.1.3.6.1…` (as MIB tools print it) and surrounding space are accepted; nothing else."""
    return raw.strip().lstrip(".")


def valid_oid(oid: str) -> str | None:
    """None when `oid` is a dotted numeric OID this appliance can store, else the rule it broke."""
    if not _OID.match(oid):
        return "must be dotted numbers, like 1.3.6.1.4.1.2011.2.15"
    arcs = oid.split(".")
    if len(arcs) > MAX_ARCS:
        return f"has more than {MAX_ARCS} arcs"
    if any(int(arc) > 4294967295 for arc in arcs):
        return "has an arc above 4294967295"
    return None


def _text(value: str, limit: int) -> tuple[str | None, str | None]:
    """A cleaned optional text cell, or the rule it broke."""
    cleaned = " ".join(value.split())
    if not cleaned:
        return None, None
    if _CONTROL.search(value):
        return None, "contains a control character"
    if len(cleaned) > limit:
        return None, f"is longer than {limit} characters"
    return cleaned, None


def parse(text: str, *, now: float | None = None) -> Parsed:
    """Validate every row of `text`. Pure: no I/O, no clock beyond the deadline."""
    started = time.monotonic() if now is None else now
    out = Parsed()
    if len(text.encode("utf-8")) > MAX_BYTES:
        out.refused = f"the file is larger than {MAX_BYTES // 1024} KiB"
        return out
    lines = text.splitlines()
    head_index = next((i for i, line in enumerate(lines) if line.strip()), None)
    if head_index is None:
        out.refused = "the file is empty"
        return out
    header = lines[head_index]
    delimiter = max((",", ";", "\t"), key=header.count)
    reader = csv.reader(io.StringIO("\n".join(lines[head_index:])), delimiter=delimiter)
    columns = [COLUMNS.get(" ".join(cell.strip().lower().split())) for cell in next(reader)]
    if "oid" not in columns:
        out.refused = (
            "the first line must name the columns, including trap_oid "
            "(class, trap_oid, vendor, severity)"
        )
        return out
    seen: dict[str, int] = {}
    data_rows = 0
    for offset, cells in enumerate(reader):
        line = head_index + 2 + offset
        if not any(cell.strip() for cell in cells):
            continue
        data_rows += 1
        if data_rows > MAX_ROWS:
            out.refused = f"the file has more than {MAX_ROWS} rows"
            return out
        if time.monotonic() - started > DEADLINE_S:
            out.refused = f"the file took longer than {DEADLINE_S:g} s to read"
            return out
        values = {name: cells[i] for i, name in enumerate(columns) if name and i < len(cells)}
        before = len(out.problems)
        oid = normalise_oid(values.get("oid", ""))
        rule = "is required" if not oid else valid_oid(oid)
        if rule:
            out.problems.append(Problem(line, "trap_oid", rule))
        elif oid in seen:
            out.problems.append(Problem(line, "trap_oid", f"repeats line {seen[oid]}"))
        name, why = _text(values.get("name", ""), MAX_NAME)
        if why:
            out.problems.append(Problem(line, "class", why))
        vendor, why = _text(values.get("vendor", ""), MAX_VENDOR)
        if why:
            out.problems.append(Problem(line, "vendor", why))
        severity = " ".join(values.get("severity", "").split()).lower() or None
        if severity is not None and severity not in SEVERITIES:
            out.problems.append(
                Problem(line, "severity", f"must be one of {', '.join(SEVERITIES)}")
            )
        if name is None and severity is None and len(out.problems) == before:
            out.problems.append(Problem(line, "class", "a row must give a class or a severity"))
        if len(out.problems) == before:
            seen[oid] = line
            out.rows.append(Row(line, oid, name, severity, vendor))
    return out
