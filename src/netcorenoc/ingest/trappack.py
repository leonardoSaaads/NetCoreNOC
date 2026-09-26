"""The built-in trap pack: vendors' own trap names, and a default severity (v0.24.0, ADR #400).

**Plug and play.** An appliance that has just been pointed at a network should already know that
`1.3.6.1.4.1.2011.5.25.219.2.2.3` is Huawei's `hwBoardFail`, and that a board failure is serious,
without an operator typing either. This module ships that knowledge:

* **the names are the vendors'** — every notification of a network vendor or a standard module,
  and every object those modules declare (the varbinds their traps carry), as declared in the
  vendor's own MIB module, resolved to its numeric OID by `tools/trappack_build.py` and shipped as
  `trappack.tsv.gz` and `trapobjects.tsv.gz`, with the module each came from;
* **the severity is a published default, not the vendor's word.** A MIB module almost never states
  a severity, so each notification NAME is graded by `grade()` below — a category table an
  operator can read: failures of power, boards and signal are critical; links, sessions, fans,
  temperature and optics are major; utilisation thresholds are minor; configuration, login and
  topology notices are warnings; recoveries are `cleared`. A name no category matches is left
  ungraded rather than guessed.

**It is the lowest rung of the catalogue.** An operator's declaration and an imported file both
outrank it (`store/class_rules.SOURCES`), so any default here is one gesture from corrected.

**What it costs.** Nothing per trap. The notifications are read once into a dict keyed by OID
(~15 000 entries); the catalogue resolves a class with one lookup and memoises the answer per OID,
so a class is resolved once per catalogue lifetime whatever the traffic. The objects are read only
when a varbind name is first asked for. There is no table, no migration and no query.
"""

from __future__ import annotations

import bisect
import gzip
import re
from dataclasses import dataclass
from functools import cache
from importlib import resources

#: The categories, in the order they are tried — the first that matches a notification's name
#: wins. Each is `(severity, pattern)`, matched case-insensitively against the name with its
#: camel-case split into words, so `hwBoardFail` is read as `hw board fail`.
CATEGORIES: tuple[tuple[str, str], ...] = (
    # A recovery is a statement that a fault is over — X.733's `cleared`. Matched on the name's
    # LAST word, so `hwPowerFailResume` is a recovery and `upsBatteryLow` is not.
    (
        "cleared",
        r"\b(resume|resumed|recover|recovered|recovery|restore|restored|clear|cleared|"
        r"normal|ok|up|online|insert|inserted|back|resolved|return|returned|established|"
        r"present|good|end|ended|disappear|disappeared|deassert|deasserted|clr|falling|"
        r"cancel|cancelled|finish|finished|succeed|succeeded|success|recover(ed)?)$",
    ),
    # Notices whose words would otherwise read as a fault: a refused login is an event, not a
    # failure of the equipment.
    (
        "warning",
        r"\b(auth|authentication|authen|login|logon|logout|logoff|password|user|"
        r"config|configuration|cfg|save|saved|commit|committed|topology change|new root|"
        r"license|licence|expire|expired|expiry|cold ?start|warm ?start)\b",
    ),
    (
        "critical",
        r"\b(dying ?gasp|power (fail|failure|off|lost|loss|invalid|down|abnormal|outage)|"
        r"(psu|power supply|pwr|power module) (fail|failure|removed?|abnormal|invalid|down)|"
        r"(board|card|lpu|mpu|sru|chassis|slot|linecard|line card|supervisor|sup|fpc|pic) "
        r"(fail|failure|failed|invalid|down|crash|removed?|pull ?out|offline)|"
        r"los|lof|loss of (signal|frame|light)|signal (lost|loss)|fiber (cut|break)|"
        r"(node|device|system|ne) (down|unreachable|offline|fail|failure|crash|halt))\b",
    ),
    (
        "major",
        r"\b(link ?down|port ?down|if ?down|interface ?down|down|fail|failure|failed|fault|"
        r"backward ?trans(ition)?|(neighbou?r|nbr|adjacency|peer|session) (down|loss|lost|fail)|"
        r"unreachable|offline|fan|temperature|temp|overheat|over ?temp|voltage|"
        r"optical|rx ?power|tx ?power|laser|removed?|pull ?out|invalid|abnormal|lost|loss|"
        r"degrade|degraded|exhaust|exhausted|full|overflow|crash|reboot|restart|reload|"
        r"switchover|failover|standby|battery|loop|storm|"
        r"ais|rdi|lom|lop|ber|sd|sf|tim|uneq|plm|bdi|oci|lck|defect|degradation)\b",
    ),
    (
        "minor",
        r"\b(threshold|rising|exceed|exceeded|over|overload|high|utili[sz]ation|usage|cpu|"
        r"memory|mem|storage|disk|congestion|drop|drops|discard|flap|flapping|error|errors|"
        r"crc|collision|mismatch|conflict|duplicate|low)\b",
    ),
    (
        "warning",
        r"\b(change|changed|changes|state|status|notify|notification|event|alarm|warning|"
        r"master|backup|elect|elected)\b",
    ),
)

_COMPILED = tuple((sev, re.compile(rx)) for sev, rx in CATEGORIES)
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|[_\-.]+")


def words(name: str) -> str:
    """`hwBoardFailResume` -> `hw board fail resume`; `LOSAlarm` -> `los alarm`."""
    return " ".join(w for w in _CAMEL.split(name) if w).lower()


#: Words a vendor appends to every notification name, which say nothing about which one it is:
#: `hwOnuLosResumeTrap` is a recovery, and read with `trap` last it would not be.
_FILLER = frozenset(
    {
        "trap",
        "traps",
        "notification",
        "notifications",
        "notif",
        "notify",
        "event",
        "alarm",
        "alarms",
        "ind",
        "info",
        "msg",
    }
)


def grade(name: str) -> str | None:
    """The default severity for a notification NAME, or None where no category applies."""
    parts = words(name).split()
    while len(parts) > 1 and parts[-1] in _FILLER:
        parts.pop()
    text = " ".join(parts)
    for severity, pattern in _COMPILED:
        if pattern.search(text):
            return severity
    return None


@dataclass(frozen=True)
class Entry:
    """One notification the pack knows: its name, the MIB module that declares it, the vendor, and
    the default severity (None where no category applies)."""

    oid: str
    name: str
    module: str
    vendor: str
    severity: str | None


@dataclass(frozen=True)
class Pack:
    notifications: dict[str, Entry]


def _read(name: str) -> list[list[str]]:
    try:
        raw = resources.files("netcorenoc.ingest").joinpath(name).read_bytes()
    except (FileNotFoundError, OSError):
        return []
    return [line.split("\t") for line in gzip.decompress(raw).decode("utf-8").splitlines()]


@cache
def pack() -> Pack:
    """The shipped notifications, read once. An absent file is an empty pack, never an error."""
    notifications: dict[str, Entry] = {}
    for row in _read("trappack.tsv.gz"):
        oid, name, module, vendor, severity = (row + [""] * 5)[:5]
        notifications[oid] = Entry(oid, name, module, vendor, severity or None)
    return Pack(notifications)


@cache
def _objects() -> tuple[list[str], list[str]]:
    """Every object the pack names, as two parallel lists sorted by the OID's TEXT — read on the
    first varbind lookup and never on the ingest path. Two lists rather than a dict of ~116 000
    entries, and text keys rather than integer tuples: a varbind name is display text, looked up
    rarely, and an exact-match bisect needs only a consistent order, not a numeric one."""
    rows = sorted((row[0], row[1]) for row in _read("trapobjects.tsv.gz") if len(row) >= 2)
    return [r[0] for r in rows], [r[1] for r in rows]


def object_name(oid: str) -> str | None:
    """The name of a varbind OID: the object itself or, for a table cell, its column — found by
    dropping index arcs one at a time, never by a string prefix. `hwEntityCpuUsage.16842753` is
    `hwEntityCpuUsage`."""
    arcs = oid.strip(".").split(".")
    keys, names = _objects()
    for n in range(len(arcs), 6, -1):
        head = ".".join(arcs[:n])
        at = bisect.bisect_left(keys, head)
        if at < len(keys) and keys[at] == head:
            return names[at]
    return None
