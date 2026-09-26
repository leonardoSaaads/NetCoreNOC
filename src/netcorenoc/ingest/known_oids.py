"""Bundled public OID knowledge.

Two static tables, both public data and both deliberately small:

- A curated subset of the IANA Private Enterprise Numbers registry, enough to label the
  vendors that actually ship SNMP-speaking network equipment. Unknown prefixes render as
  ``enterprise-<n>`` — honest, and still a stable class token.
- The standard SNMPv2/MIB-II trap and varbind OIDs that every device implements. These
  are the only OIDs with pre-loaded semantics; everything else is learned.

Neither table is user configuration.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.ingest import trappack

ENTERPRISE_PREFIX = "1.3.6.1.4.1."
SNMP_TRAP_OID = "1.3.6.1.6.3.1.1.4.1.0"
SYS_UPTIME_OID = "1.3.6.1.2.1.1.3.0"
IF_INDEX_PREFIX = "1.3.6.1.2.1.2.2.1.1."

# Curated IANA Private Enterprise Numbers (https://www.iana.org/assignments/enterprise-numbers).
IANA_ENTERPRISES: dict[int, str] = {
    2: "IBM",
    9: "Cisco Systems",
    11: "Hewlett Packard",
    23: "Novell",
    42: "Sun Microsystems (Oracle)",
    43: "3Com",
    52: "Enterasys Networks",
    63: "Apple",
    94: "Nokia",
    111: "Oracle",
    116: "Hitachi",
    119: "NEC",
    161: "Motorola",
    164: "RAD Data Communications",
    171: "D-Link",
    193: "Ericsson",
    207: "Allied Telesis",
    211: "Fujitsu",
    232: "Compaq (HPE)",
    236: "Samsung Electronics",
    244: "Lantronix",
    248: "Hirschmann (Belden)",
    253: "Xerox",
    259: "Accton (Edgecore)",
    311: "Microsoft",
    318: "APC (Schneider Electric)",
    332: "Digi International",
    343: "Intel",
    368: "Axis Communications",
    388: "Symbol Technologies",
    429: "U.S. Robotics",
    476: "Vertiv (Liebert)",
    534: "Eaton",
    637: "Alcatel-Lucent",
    664: "ADTRAN",
    674: "Dell",
    789: "NetApp",
    890: "Zyxel",
    1139: "Dell EMC",
    1271: "Ciena",
    1286: "ECI Telecom (Ribbon)",
    1397: "Tellabs",
    1588: "Brocade (Broadcom)",
    1602: "Canon",
    1916: "Extreme Networks",
    1991: "Foundry Networks (Extreme)",
    2011: "Huawei",
    2021: "UCD (net-snmp)",
    2272: "Nortel Networks",
    2281: "Ceragon Networks",
    2352: "Ericsson (Redback)",
    2544: "Adtran Networks (ADVA)",
    2604: "Sophos",
    2620: "Check Point",
    2636: "Juniper Networks",
    2682: "DPS Telecom",
    2879: "Ribbon (Sonus)",
    3097: "WatchGuard",
    3224: "Juniper (NetScreen)",
    3320: "BDCOM",
    3375: "F5 Networks",
    3607: "Cisco ONS (Cerent)",
    3709: "Datacom",
    3808: "CyberPower Systems",
    3902: "ZTE",
    4115: "CommScope (Arris)",
    4526: "Netgear",
    4874: "Juniper (Unisphere)",
    4881: "Ruijie Networks",
    5003: "AudioCodes",
    5875: "FiberHome",
    5951: "Citrix (NetScaler)",
    6027: "Dell (Force10)",
    6141: "Ciena",
    6296: "DASAN Networks",
    6321: "Calix",
    6486: "Alcatel-Lucent Enterprise",
    6527: "Nokia (SR OS)",
    6574: "Synology",
    6876: "VMware",
    7483: "Nokia 1830 PSS (Alcatel-Lucent Tropic)",
    8072: "net-snmp",
    8691: "Moxa",
    8741: "SonicWall",
    8886: "Raisecom",
    10876: "Supermicro",
    11863: "TP-Link",
    12356: "Fortinet",
    13742: "Raritan (Legrand)",
    14179: "Cisco (Airespace)",
    14823: "Aruba (HPE)",
    14988: "MikroTik",
    17163: "Riverbed",
    17713: "Cambium Networks",
    19046: "Lenovo",
    21296: "Infinera",
    24681: "QNAP",
    25053: "Ruckus (CommScope)",
    25461: "Palo Alto Networks",
    25506: "H3C",
    29671: "Cisco Meraki",
    30065: "Arista Networks",
    33049: "Mellanox (NVIDIA)",
    41112: "Ubiquiti",
    41263: "Nutanix",
    42229: "Coriant (Infinera)",
}

# Standard SNMPv2 notification OIDs (RFC 3418) plus the two RMON alarm traps.
STANDARD_TRAPS: dict[str, str] = {
    "1.3.6.1.6.3.1.1.5.1": "coldStart",
    "1.3.6.1.6.3.1.1.5.2": "warmStart",
    "1.3.6.1.6.3.1.1.5.3": "linkDown",
    "1.3.6.1.6.3.1.1.5.4": "linkUp",
    "1.3.6.1.6.3.1.1.5.5": "authenticationFailure",
    "1.3.6.1.6.3.1.1.5.6": "egpNeighborLoss",
    "1.3.6.1.2.1.16.0.1": "risingAlarm",
    "1.3.6.1.2.1.16.0.2": "fallingAlarm",
}

# Well-known varbind OIDs (exact, or table-column prefixes ending in a dot).
WELL_KNOWN_VARBINDS: dict[str, str] = {
    SYS_UPTIME_OID: "sysUpTime",
    SNMP_TRAP_OID: "snmpTrapOID",
    "1.3.6.1.6.3.1.1.4.3.0": "snmpTrapEnterprise",
    "1.3.6.1.2.1.1.5.0": "sysName",
    "1.3.6.1.2.1.2.2.1.1.": "ifIndex",
    "1.3.6.1.2.1.2.2.1.2.": "ifDescr",
    "1.3.6.1.2.1.2.2.1.7.": "ifAdminStatus",
    "1.3.6.1.2.1.2.2.1.8.": "ifOperStatus",
    "1.3.6.1.2.1.31.1.1.1.1.": "ifName",
    "1.3.6.1.2.1.31.1.1.1.18.": "ifAlias",
}

# Universally valid raise → clear pairs; everything else is learned by alternation.
CLEAR_PAIR_SEEDS: dict[str, str] = {
    "1.3.6.1.6.3.1.1.5.3": "1.3.6.1.6.3.1.1.5.4",  # linkDown → linkUp
    "1.3.6.1.2.1.16.0.1": "1.3.6.1.2.1.16.0.2",  # risingAlarm → fallingAlarm
}

# The **X.733 perceived-severity vocabulary**, and its normalised rank (0 = most severe).
#
# **Source, cited in v0.17.1 and uncited for the eight releases before it**: ITU-T Recommendation
# X.733 (ISO/IEC 10164-4), *Systems Management: Alarm reporting function*, `perceivedSeverity`. Its
# six values are exactly the six below — `critical`, `major`, `minor`, `warning`, `indeterminate`
# and `cleared` — and the IETF carries the same six into SNMP through RFC 3877's ALARM-MIB. This
# table has held them since v0.8.0 with no attribution at all, which is the small dishonesty
# DECISIONS #337 closes: the appliance was already shipping standard knowledge and calling it a
# bundled convenience.
#
# **The ranking is X.733's own ordering**, not a judgement made here. `cleared` and `indeterminate`
# share rank 4 because neither asserts a degree of fault: one says the fault is over and the other
# says the reporter does not know.
SEVERITY_VOCAB: dict[str, int] = {
    "critical": 0,
    "major": 1,
    "minor": 2,
    "warning": 3,
    "indeterminate": 4,
    "cleared": 4,
}


def severity_rank(value: str) -> int | None:
    """Normalised 0-4 rank for a severity token (case-insensitive), or None if unrecognised."""
    return SEVERITY_VOCAB.get(value.strip().lower())


def standard_severity(varbinds: list[dict[str, Any]]) -> tuple[str, int, str] | None:
    """`(token, rank, varbind_oid)` for the severity a trap **carried**, or None (v0.17.1, #337).

    **This asserts nothing about any vendor.** The device transmitted the word `critical`; X.733 is
    the standard that defines that word as a perceived severity; this reads it. It is neither a
    learned inference nor a bundled claim about a product — it is the trap's own statement about its
    own alarm, in the vocabulary the ITU standardised, and that is why its provenance is `standard`.

    **Why the value and not the OID.** The intended design keyed on the registered column — RFC
    3877's ALARM-MIB and the IANA-ITU-ALARM-TC registry. Both sources are unreachable from the
    environment this was built in (`www.iana.org` and `www.rfc-editor.org` answer 403 to the egress
    proxy), so every OID-keyed row would carry a citation nobody here could open. Keying on the
    value needs only the vocabulary above, which is citable and already shipped — and it has the
    happier property of working at whatever OID a vendor chose, which an OID table would miss.

    **The most severe wins**, not the first seen. A trap carrying both `major` and `cleared` is
    reporting something about both, and under-reporting a fault is the worse error; ties break on
    varbind order so the result is deterministic. The rank is X.733's, so "most severe" is its
    ordering and not one invented here.

    **The false positive is real and bounded.** A varbind whose value is `minor` for an unrelated
    reason — a version qualifier — is read as a severity. The provenance says `standard`, the screen
    says so, and an operator's declaration outranks it (#338), so the recourse is one gesture. The
    alternative is what v0.17.0 shipped: nothing placed, ever.
    """
    best: tuple[str, int, str] | None = None
    for vb in varbinds:
        raw = vb.get("value")
        if not isinstance(raw, str):
            continue
        rank = severity_rank(raw)
        if rank is None:
            continue
        if best is None or rank < best[1]:
            best = (raw.strip().lower(), rank, str(vb.get("oid", "")))
    return best


def under_subtree(oid: str, root: str) -> bool:
    """Is `oid` the node `root` or a descendant of it? **Arc boundaries, never string prefixes.**

    An OID is a sequence of arcs written with dots, so membership of a subtree is a statement
    about arcs, with `root = "1.3.6.1.4.1.2011.5.25.31.1.1.1.1"`:

        under_subtree("1.3.6.1.4.1.2011.5.25.31.1.1.1.1",     root)  -> True   (the node itself)
        under_subtree("1.3.6.1.4.1.2011.5.25.31.1.1.1.1.4.2", root)  -> True   (a descendant)
        under_subtree("1.3.6.1.4.1.2011.5.25.31.1.1.1.10",    root)  -> False  (a SIBLING)

    The third line is the one that matters: `startswith(root)` returns `True` for it, and
    `1.1.1.10` is the tenth column of a table whose first column the operator named. Appending the
    separator before comparing is what makes the comparison about arcs. Moved here from
    `engine/mw/rules.py` in v0.22.0 so the alarm-class catalogue uses the same predicate the
    maintenance-window rules do (ADR #384).
    """
    return oid == root or oid.startswith(root + ".")


def ancestors(oid: str) -> list[str]:
    """`oid` and every node above it, **most specific first**, split on arcs.

    `ancestors("1.3.6.1.4.1.2011.1.12")` is `["1.3.6.1.4.1.2011.1.12", "1.3.6.1.4.1.2011.1", …,
    "1"]` and can never contain `"1.3.6.1.4.1.2011.1.1"`: a node is reached by dropping whole arcs,
    so the string-prefix mistake has no way in. The catalogue resolves a class by looking each of
    these up — O(arcs) dictionary reads rather than a scan of every rule.
    """
    arcs = oid.split(".")
    return [".".join(arcs[:n]) for n in range(len(arcs), 0, -1)]


def vendor_of(oid: str) -> str | None:
    """Vendor for an enterprise OID; ``enterprise-<n>`` if unknown; None if not enterprise."""
    if not oid.startswith(ENTERPRISE_PREFIX):
        return None
    head = oid.removeprefix(ENTERPRISE_PREFIX).split(".", 1)[0]
    if not head.isdigit():
        return None
    number = int(head)
    return IANA_ENTERPRISES.get(number, f"enterprise-{number}")


def trap_name(oid: str) -> str | None:
    """Human name of a standard trap OID, or None for vendor traps."""
    return STANDARD_TRAPS.get(oid)


def varbind_name(oid: str) -> str | None:
    """Human name of a varbind OID: a well-known one (exact or table-column match), else the name
    the vendor's MIB gives it in the built-in trap pack (v0.24.0, ADR #400)."""
    for key, name in WELL_KNOWN_VARBINDS.items():
        if oid == key or (key.endswith(".") and oid.startswith(key)):
            return name
    return trappack.object_name(oid)


#: **Where every bundled table above came from**, keyed by the table's own name (v0.17.1, #344).
#:
#: One mapping rather than five `<NAME>_SOURCE` constants, because five constants is a list that
#: grows — add a sixth table in v0.18.0 and somebody has to remember a sixth line in three places.
#: `tests/test_known_oids.py` derives the set of bundled tables from this module and asserts each
#: has an entry here, so a table added without a citation fails the guard by name and no list has
#: to be maintained to make that happen.
#:
#: **A citation is a constant and not a comment** because the appliance has to be able to *quote*
#: it. An operator looking at a severity the console says came from `standard` is entitled to ask
#: which standard, and a comment cannot answer them.
BUNDLED_SOURCES: dict[str, str] = {
    # **Not re-verified in v0.17.1**: `www.iana.org` answers 403 to this build environment's egress
    # proxy, so the rows are as curated in the releases that added them. The citation names where a
    # reader checks them; it does not claim that anyone here did.
    "IANA_ENTERPRISES": (
        "IANA Private Enterprise Numbers registry — "
        "https://www.iana.org/assignments/enterprise-numbers (rows not re-verified in v0.17.1: the "
        "registry is unreachable from the build environment)"
    ),
    "STANDARD_TRAPS": (
        "RFC 3418 (SNMPv2-MIB) snmpTraps 1.3.6.1.6.3.1.1.5 — coldStart, warmStart, "
        "authenticationFailure; RFC 2863 (IF-MIB) — linkDown, linkUp, registered under that same "
        "subtree; RFC 1213 (MIB-II) EGP group — egpNeighborLoss; RFC 2819 (RMON-MIB) "
        "1.3.6.1.2.1.16.0 — risingAlarm, fallingAlarm"
    ),
    "WELL_KNOWN_VARBINDS": (
        "RFC 3418 (SNMPv2-MIB) — sysUpTime and sysName in the system group 1.3.6.1.2.1.1, and "
        "snmpTrapOID.0 / snmpTrapEnterprise.0 in 1.3.6.1.6.3.1.1.4; RFC 2863 (IF-MIB) ifTable "
        "1.3.6.1.2.1.2.2.1 — ifIndex, ifDescr, ifAdminStatus, ifOperStatus; RFC 2863 ifXTable "
        "1.3.6.1.2.1.31.1.1.1 — ifName, ifAlias"
    ),
    "CLEAR_PAIR_SEEDS": (
        "RFC 2863 (IF-MIB) — linkUp is defined as the notification sent when ifOperStatus "
        "leaves the down state that raised linkDown; RFC 2819 (RMON-MIB) — fallingAlarm is "
        "the complement of "
        "risingAlarm for the same alarmIndex, one per threshold crossing in each direction"
    ),
    "SEVERITY_VOCAB": (
        "ITU-T Rec. X.733 (ISO/IEC 10164-4), Systems Management: Alarm reporting function — "
        "perceivedSeverity; carried into SNMP by RFC 3877 (ALARM-MIB)"
    ),
}
