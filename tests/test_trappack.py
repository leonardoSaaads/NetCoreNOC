"""The built-in trap pack: vendors' names and a default severity, with no operator input (ADR #400).

What is asserted:

* the pack ships, and knows the traps an operator names first — Huawei's `hwBoardFail`, the
  standard `linkDown` — with the vendor's own name and a severity;
* the objects those traps carry are named too, the maintainer's own examples included, at the
  column and at a table cell;
* the grading table reads names the way an operator would, and leaves a name it cannot read
  ungraded rather than guessing;
* the pack is the LOWEST rung: an operator's rule at any depth beats it, and the severity a trap
  carried beats its default;
* end to end, a trap that carries no severity is counted in its band, with provenance `builtin`;
* the build tool resolves a module the way the pack needs, IMPORTS and SMIv1 traps included.
"""

from __future__ import annotations

import gzip
import sys
from pathlib import Path

import pytest

from netcorenoc.ingest import known_oids, trappack
from netcorenoc.ingest.events import TrapEvent, Varbind
from netcorenoc.store import Store
from netcorenoc.store.class_rules import BUILTIN, Catalogue, Rule

import authutil
import util

HW_BOARD_FAIL = "1.3.6.1.4.1.2011.5.25.219.2.2.3"
LINK_DOWN = "1.3.6.1.6.3.1.1.5.3"
STD_SEV = "1.3.6.1.2.1.118.1.2.2.1.4"


def test_the_pack_ships_and_knows_the_traps_named_first() -> None:
    pack = trappack.pack()
    assert len(pack.notifications) > 10_000, "the shipped pack is empty or truncated"
    board = pack.notifications[HW_BOARD_FAIL]
    assert (board.name, board.module, board.vendor) == (
        "hwBoardFail",
        "HUAWEI-ENTITY-TRAP-MIB",
        "Huawei",
    )
    assert board.severity == "critical"
    assert pack.notifications[LINK_DOWN].name == "linkDown"
    assert pack.notifications[LINK_DOWN].severity == "major"
    vendors = {entry.vendor for entry in pack.notifications.values()}
    for vendor in ("Huawei", "Cisco Systems", "Juniper Networks", "Nokia (SR OS)", "ZTE", "H3C"):
        assert vendor in vendors, vendor


@pytest.mark.parametrize(
    ("oid", "name"),
    [
        # The maintainer's own examples (Huawei 9800).
        ("1.3.6.1.2.1.47.1.1.1.1.7", "entPhysicalName"),
        ("1.3.6.1.2.1.47.1.1.1.1.5", "entPhysicalClass"),
        ("1.3.6.1.4.1.2011.5.25.31.1.1.1.1.5", "hwEntityCpuUsage"),
        ("1.3.6.1.4.1.2011.5.25.31.1.1.1.1.3", "hwEntityStandbyStatus"),
        # A table cell names its column: the index arcs are dropped, one at a time.
        ("1.3.6.1.4.1.2011.5.25.31.1.1.1.1.5.16842753", "hwEntityCpuUsage"),
    ],
)
def test_the_varbinds_those_traps_carry_are_named(oid: str, name: str) -> None:
    assert known_oids.varbind_name(oid) == name


def test_an_arc_boundary_holds_for_object_names() -> None:
    """`…1.1.1.1.70` is not a cell of column `…1.1.1.1.7` — the lookup drops whole arcs."""
    assert trappack.object_name("1.3.6.1.2.1.47.1.1.1.1.70") != "entPhysicalName"


@pytest.mark.parametrize(
    ("name", "severity"),
    [
        ("hwBoardFail", "critical"),
        ("hwBoardFailResume", "cleared"),
        ("hwOnuLosTrap", "critical"),
        ("hwEponOltAlarmLosResumeTrap", "cleared"),
        ("jnxPowerSupplyFailure", "critical"),
        ("dyingGaspAlarm", "critical"),
        ("linkDown", "major"),
        ("linkUp", "cleared"),
        ("bgpBackwardTransNotification", "major"),
        ("jnxFanFailure", "major"),
        ("jnxOverTemperature", "major"),
        ("hwCPUUtilizationRisingAlarm", "minor"),
        ("risingAlarm", "minor"),
        ("fallingAlarm", "cleared"),
        ("authenticationFailure", "warning"),
        ("coldStart", "warning"),
        ("ciscoConfigManEvent", "warning"),
        # Nothing to read in the name: ungraded, never guessed.
        ("hwGtlInitial", None),
    ],
)
def test_the_grading_table_reads_names_as_an_operator_would(
    name: str, severity: str | None
) -> None:
    assert trappack.grade(name) == severity


def test_the_pack_is_the_lowest_rung_of_the_catalogue() -> None:
    pack = trappack.pack()
    bare = Catalogue([], pack).resolve(HW_BOARD_FAIL)
    assert (bare.name, bare.severity) == ("hwBoardFail", "critical")
    assert bare.name_rule is not None and bare.name_rule.source == BUILTIN
    # An operator's BRANCH rule, three arcs up, beats the pack's EXACT entry.
    branch = Rule(
        id=7,
        oid="1.3.6.1.4.1.2011.5.25.219",
        subtree=True,
        name=None,
        severity="minor",
        vendor=None,
        source="declared",
    )
    ruled = Catalogue([branch], pack).resolve(HW_BOARD_FAIL)
    assert ruled.severity == "minor" and ruled.severity_rule == branch
    assert ruled.name == "hwBoardFail", "a rule that only grades keeps the pack's name"
    # The trap's own word beats the pack's default; with no word, the default fills in.
    assert bare.rank_over(3) == 3
    assert bare.rank_over(None) == known_oids.severity_rank("critical")
    assert ruled.rank_over(3) == known_oids.severity_rank("minor")


async def test_a_trap_with_no_severity_is_graded_by_the_pack_end_to_end(store: Store) -> None:
    engine, queue, _app = await authutil.make_env(store)
    events = [
        TrapEvent(
            device="10.9.0.1",
            trap_oid=HW_BOARD_FAIL,
            instance="slot-3",
            ts=1_700_000_000.0,
            varbinds=[Varbind(oid="1.3.6.1.2.1.47.1.1.1.1.7.3", kind="str", value="slot-3")],
        ),
        # The trap's own word outranks the pack: this linkDown says `minor`, and is minor.
        TrapEvent(
            device="10.9.0.1",
            trap_oid=LINK_DOWN,
            instance="ge-0/0/1",
            ts=1_700_000_001.0,
            varbinds=[Varbind(oid=STD_SEV, kind="str", value="minor")],
        ),
    ]
    await util.drive(engine, queue, events)
    async with store.lock:
        census = await store.severity_census()
    assert census["placed"] == {"0": 1, "2": 1}, census
    assert census["provenance"]["builtin"] == 1 and census["provenance"]["standard"] == 1
    assert census["unplaced"] == 0


def test_the_build_tool_resolves_a_module_as_the_pack_needs(tmp_path: Path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import trappack_build as build

    (tmp_path / "ACME-MIB").write_text(
        """ACME-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, NOTIFICATION-TYPE, enterprises FROM SNMPv2-SMI;
acme MODULE-IDENTITY
    LAST-UPDATED "202601010000Z"
    DESCRIPTION "a module whose first assignment the IMPORTS list must not swallow ::= { x 1 }"
    ::= { enterprises 2011 99 }
acmeObjects OBJECT IDENTIFIER ::= { acme 1 }  -- a comment ::= { acme 9 }
acmePortName OBJECT-TYPE
    SYNTAX OCTET STRING
    MAX-ACCESS read-only
    STATUS current
    DESCRIPTION "the port"
    ::= { acmeObjects 1 }
acmeTraps OBJECT IDENTIFIER ::= { acme 0 }
acmePortFail NOTIFICATION-TYPE
    OBJECTS { acmePortName }
    STATUS current
    DESCRIPTION "a port failed"
    ::= { acmeTraps 1 }
acmeOldTrap TRAP-TYPE
    ENTERPRISE acme
    VARIABLES { acmePortName }
    DESCRIPTION "SMIv1"
    ::= 7
END
""",
        encoding="utf-8",
    )
    tree, found = build.scan([tmp_path])
    assert tree.oid("ACME-MIB", "acmePortFail") == "1.3.6.1.4.1.2011.99.0.1"
    assert tree.oid("ACME-MIB", "acmePortName") == "1.3.6.1.4.1.2011.99.1.1"
    kinds = {name: kind for _m, name, kind, _w, _o in found}
    assert kinds["acmePortFail"] == "v2" and kinds["acmeOldTrap"] == "v1:acme:7"
    out = tmp_path / "out"
    assert build.main(["build", str(out), str(tmp_path)]) == 0
    rows = gzip.decompress((out / "trappack.tsv.gz").read_bytes()).decode().splitlines()
    by_oid = {row.split("\t")[0]: row.split("\t") for row in rows}
    assert by_oid["1.3.6.1.4.1.2011.99.0.1"][1:] == [
        "acmePortFail",
        "ACME-MIB",
        "Huawei",
        "major",
    ]
    # RFC 3584: an SMIv1 trap's OID is enterprise.0.specific.
    assert "1.3.6.1.4.1.2011.99.0.7" in by_oid
    # Deterministic: a second build writes the same bytes.
    first = (out / "trappack.tsv.gz").read_bytes()
    build.main(["build", str(out), str(tmp_path)])
    assert (out / "trappack.tsv.gz").read_bytes() == first
