"""Build the built-in trap pack from vendors' MIB files (v0.24.0, ADR #400).

    python tools/trappack_build.py <out-dir> <mib-root> [<mib-root> ...]

Reads SMIv1/SMIv2 MIB modules as text and writes two gzipped TSVs into `<out-dir>`:

* `trappack.tsv.gz` — every NOTIFICATION-TYPE and TRAP-TYPE of a network vendor or a standard
  module, resolved to its numeric OID: `oid  name  module  vendor  severity`. `severity` is
  `netcorenoc.ingest.trappack.grade(name)` — a published category table, not the vendor's word —
  and empty where no category applies.
* `trapobjects.tsv.gz` — every leaf OBJECT-TYPE of a module that declares notifications, and every
  object a notification lists in OBJECTS/VARIABLES: `oid  name  module`, sorted by OID — the
  varbinds those traps carry, named by the module that DEFINES each.

The MIB files are the vendors' own modules; the ones this release was built from are the LibreNMS
collection (https://github.com/librenms/librenms, `mibs/`). **A development tool**: the appliance
never parses a MIB at runtime; it reads the two files this writes.

The parser is deliberately small. It does not validate a module, check SYNTAX or follow IMPORTS
to a file; it needs exactly one thing from each module, the `::= { parent n }` assignments, and
resolves names against the module first and every module second. A name it cannot resolve to a
numeric root is dropped rather than guessed, and the count dropped is printed to stderr.
"""

from __future__ import annotations

import gzip
import re
import sys
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from netcorenoc.ingest import known_oids, trappack

ROOTS = {
    "ccitt": "0",
    "zeroDotZero": "0.0",
    "iso": "1",
    "joint-iso-ccitt": "2",
    "org": "1.3",
    "dod": "1.3.6",
    "internet": "1.3.6.1",
    "directory": "1.3.6.1.1",
    "mgmt": "1.3.6.1.2",
    "mib-2": "1.3.6.1.2.1",
    "transmission": "1.3.6.1.2.1.10",
    "experimental": "1.3.6.1.3",
    "private": "1.3.6.1.4",
    "enterprises": "1.3.6.1.4.1",
    "security": "1.3.6.1.5",
    "snmpV2": "1.3.6.1.6",
    "snmpDomains": "1.3.6.1.6.1",
    "snmpProxys": "1.3.6.1.6.2",
    "snmpModules": "1.3.6.1.6.3",
}

#: The enterprises the pack carries: network equipment a NOC receives traps from. Anything else
#: under `enterprises` — HVAC, RAID controllers, mail appliances that happen to sit in the same MIB
#: collection — is left out, and every non-enterprise (standard, IETF) notification is kept.
NETWORK_VENDORS = frozenset(
    {
        9,
        11,
        193,
        637,
        1271,
        1588,
        1916,
        2011,
        2352,
        2544,
        2636,
        3607,
        3709,
        3902,
        4874,
        4881,
        6141,
        6486,
        6527,
        7483,
        8886,
        12356,
        14179,
        14988,
        25461,
        25506,
        30065,
        42229,
    }
)

MACROS = (
    "OBJECT IDENTIFIER|MODULE-IDENTITY|OBJECT-IDENTITY|OBJECT-TYPE|NOTIFICATION-TYPE|"
    "OBJECT-GROUP|NOTIFICATION-GROUP|MODULE-COMPLIANCE|AGENT-CAPABILITIES"
)
ASSIGN = re.compile(
    rf"(?<![\w-])([a-zA-Z][\w-]*)\s+({MACROS})\b(.*?)::=\s*\{{([^}}]*)\}}", re.DOTALL
)
TRAP_V1 = re.compile(r"(?<![\w-])([a-zA-Z][\w-]*)\s+TRAP-TYPE\b(.*?)::=\s*(\d+)", re.DOTALL)
MODULE = re.compile(r"(?<![\w-])([A-Z][\w-]*)\s+DEFINITIONS\s*::=\s*BEGIN")
STRING = re.compile(r'"[^"]*"', re.DOTALL)
IMPORTS = re.compile(r"\bIMPORTS\b.*?;", re.DOTALL)
EXPORTS = re.compile(r"\bEXPORTS\b.*?;", re.DOTALL)
COMMENT = re.compile(r"--.*?(--|$)", re.MULTILINE)
LIST = re.compile(r"\b(?:OBJECTS|VARIABLES)\s*\{([^}]*)\}")
ENTERPRISE = re.compile(r"\bENTERPRISE\s+([a-zA-Z][\w-]*)")
STATUS = re.compile(r"\bSTATUS\s+(\w+)")
ARC = re.compile(r"^(?:([a-zA-Z][\w-]*)\((\d+)\)|(\d+)|([a-zA-Z][\w-]*))$")


def clean(text: str) -> str:
    """The module with its strings blanked and its comments removed — `::=` inside a
    DESCRIPTION is prose, and a `--` inside a string is not a comment."""
    return COMMENT.sub("", STRING.sub('""', text))


def modules(text: str) -> Iterator[tuple[str, str]]:
    """`(module name, body)` for each module in a file — some files hold several."""
    marks = list(MODULE.finditer(text))
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        # The IMPORTS list names macros ("MODULE-IDENTITY, OBJECT-TYPE FROM …"), which would read
        # as the start of an assignment and swallow the module's first real one.
        yield mark.group(1), EXPORTS.sub(" ", IMPORTS.sub(" ", text[mark.end() : end]))


LEAF = re.compile(r"\bSYNTAX\s+(SEQUENCE\b|[A-Z][\w-]*Entry\b)")


class Tree:
    """Every `name ::= { parent arcs }` seen, resolvable to a numeric OID."""

    def __init__(self) -> None:
        self.local: dict[tuple[str, str], tuple[str, list[str]]] = {}
        self.anywhere: dict[str, tuple[str, str, list[str]]] = {}
        self.memo: dict[tuple[str, str], str | None] = {}
        self.leaves: set[tuple[str, str]] = set()
        self.trap_modules: set[str] = set()

    def add(self, module: str, name: str, value: str) -> None:
        parts = value.split()
        if not parts:
            return
        head, arcs = parts[0], []
        first = ARC.match(head)
        if first and first.group(2):
            head, arcs = first.group(1), [first.group(2)]
        elif first and first.group(3):
            head, arcs = "", [first.group(3)]
        for part in parts[1:]:
            m = ARC.match(part)
            if not m or m.group(4):
                return
            arcs.append(m.group(2) or m.group(3))
        self.local[(module, name)] = (head, arcs)
        self.anywhere.setdefault(name, (module, head, arcs))

    def home(self, module: str, name: str) -> str:
        """The module that DEFINES `name` as seen from `module`."""
        if (module, name) in self.local:
            return module
        hit = self.anywhere.get(name)
        return hit[0] if hit else module

    def oid(self, module: str, name: str, depth: int = 0) -> str | None:
        if name in ROOTS:
            return ROOTS[name]
        key = (module, name)
        if key in self.memo:
            return self.memo[key]
        if depth > 64:
            return None
        found = self.local.get(key)
        where = module
        if found is None:
            hit = self.anywhere.get(name)
            if hit is None:
                return None
            where, found = hit[0], (hit[1], hit[2])
        head, arcs = found
        base = "" if head == "" else self.oid(where, head, depth + 1)
        out = None if base is None else ".".join(x for x in (base, *arcs) if x)
        self.memo[key] = out
        return out


def scan(roots: list[Path]) -> tuple[Tree, list[tuple[str, str, str, str, list[str]]]]:
    """Parse every file under `roots`. Returns the tree and the notifications found, as
    `(module, name, kind, where, objects)` — `where` is the parent value or the v1 enterprise."""
    tree = Tree()
    found: list[tuple[str, str, str, str, list[str]]] = []
    for root in roots:
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            try:
                text = clean(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            for module, body in modules(text):
                for m in ASSIGN.finditer(body):
                    name, macro, inner, value = m.groups()
                    tree.add(module, name, value)
                    if macro == "OBJECT-TYPE" and not LEAF.search(inner):
                        tree.leaves.add((module, name))
                    if macro == "NOTIFICATION-TYPE":
                        tree.trap_modules.add(module)
                        status = STATUS.search(inner)
                        if status and status.group(1) == "obsolete":
                            continue
                        objs = LIST.search(inner)
                        names = [o.strip() for o in objs.group(1).split(",")] if objs else []
                        found.append((module, name, "v2", "", [o for o in names if o]))
                for m in TRAP_V1.finditer(body):
                    name, inner, number = m.groups()
                    ent = ENTERPRISE.search(inner)
                    if not ent:
                        continue
                    tree.trap_modules.add(module)
                    objs = LIST.search(inner)
                    names = [o.strip() for o in objs.group(1).split(",")] if objs else []
                    found.append((module, name, f"v1:{ent.group(1)}:{number}", "", names))
    return tree, found


def carried(oid: str) -> bool:
    """A standard OID, or one under a network vendor's enterprise."""
    if not oid.startswith(known_oids.ENTERPRISE_PREFIX):
        return True
    number = oid.removeprefix(known_oids.ENTERPRISE_PREFIX).split(".", 1)[0]
    return number.isdigit() and int(number) in NETWORK_VENDORS


def by_oid(row: tuple[str, ...]) -> list[int]:
    return [int(a) for a in row[0].split(".")]


def write(path: Path, rows: list[tuple[str, ...]]) -> None:
    """Deterministic gzip (no name, no mtime), so an unchanged pack rebuilds to the same bytes."""
    body = "".join("\t".join(r) + "\n" for r in sorted(rows, key=by_oid)).encode("utf-8")
    with path.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as gz:
        gz.write(body)


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    out, roots = Path(argv[1]), [Path(a) for a in argv[2:]]
    tree, found = scan(roots)
    notes: dict[str, tuple[str, ...]] = {}
    objects: dict[str, tuple[str, ...]] = {}
    dropped = 0
    for module, name, kind, _where, listed in found:
        if kind == "v2":
            oid = tree.oid(module, name)
        else:
            _v1, ent, number = kind.split(":")
            base = tree.oid(module, ent)
            # RFC 3584 §3.1: a v1 trap's v2 OID is enterprise.0.specific-trap.
            oid = None if base is None else f"{base}.0.{number}"
        if oid is None or not oid.startswith("1."):
            dropped += 1
            continue
        if not carried(oid):
            continue
        vendor = known_oids.vendor_of(oid) or "standard"
        notes[oid] = (oid, name, module, vendor, trappack.grade(name) or "")
        for obj in listed:
            at = tree.oid(module, obj)
            if at is not None and carried(at):
                objects.setdefault(at, (at, obj, tree.home(module, obj)))
    for module, name in sorted(tree.leaves):
        if module in tree.trap_modules:
            at = tree.oid(module, name)
            if at is not None and carried(at):
                objects.setdefault(at, (at, name, module))
    out.mkdir(parents=True, exist_ok=True)
    write(out / "trappack.tsv.gz", list(notes.values()))
    write(out / "trapobjects.tsv.gz", list(objects.values()))
    print(
        f"{len(notes)} notifications, {len(objects)} objects, {dropped} unresolved", file=sys.stderr
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
