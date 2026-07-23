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

# Bundled, widely used severity tokens and their normalised rank (0 = most severe), public
# data in the spirit of the IANA table (§5.3). A varbind is only *treated* as severity after
# its ordinality is validated against observed alarm lifetimes — the vocabulary supplies the
# candidate ranking, never an assumed one.
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
    """Human name of a well-known varbind OID (exact or table-column match)."""
    for key, name in WELL_KNOWN_VARBINDS.items():
        if oid == key or (key.endswith(".") and oid.startswith(key)):
            return name
    return None
