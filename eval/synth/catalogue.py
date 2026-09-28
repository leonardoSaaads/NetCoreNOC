"""What each vendor's equipment says when a given thing goes wrong — resolved from the trap pack.

A generated incident is written in **fault roles** ("the optical input on this amplifier fell below
threshold", "this BGP session left Established") and each role is rendered by the element's vendor
into the notification that vendor actually sends. Every OID below is **resolved by name from
`netcorenoc.ingest.trappack`** — the vendors' own MIB modules as the appliance ships them (ADR
#400) — so a misspelt name fails at import rather than producing a plausible-looking OID nobody
declared. Where a vendor has no notification for a role, the role falls back to the IETF standard
(IF-MIB `linkDown`, SNMPv2-MIB `coldStart`, BGP4-MIB, OSPF-TRAP-MIB, UPS-MIB), which is what such
equipment really sends.

**One exception, stated.** The trap pack carries no GPON ONT notifications for any vendor (they
live in access-node MIBs the pack does not include). The GPON roles therefore use **illustrative
arcs under Huawei's and ZTE's enterprise numbers**, shaped like the access-node MIBs and marked
`attested=False`. Nothing downstream reads an OID as an identity — every model feature is a
*relation* between two OIDs — so an illustrative arc changes no number a model can learn, but it is
named here rather than passed off.

Pure data plus one resolver. No I/O beyond the pack read the appliance itself performs.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache

from netcorenoc.ingest import trappack

__all__ = [
    "ALARM_SEVERITY_OID",
    "IF_DESCR",
    "IF_INDEX",
    "VENDORS",
    "Notification",
    "Rendering",
    "Vendor",
    "render",
    "render_first",
    "roles",
]

# Standard objects used as varbinds. The instance heuristic picks `ifIndex` first when present and
# otherwise the first payload varbind, so the identity varbind always comes first.
IF_INDEX = "1.3.6.1.2.1.2.2.1.1"
IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
#: X.733 perceived severity at the IETF ALARM-MIB arc (RFC 3877), the same column the lab uses.
ALARM_SEVERITY_OID = "1.3.6.1.2.1.118.1.2.2.1.4"
BGP_PEER_REMOTE_ADDR = "1.3.6.1.2.1.15.3.1.7"
OSPF_NBR_IP = "1.3.6.1.2.1.14.10.1.1"
OSPF_NBR_STATE = "1.3.6.1.2.1.14.10.1.6"
UPS_ALARM_DESCR = "1.3.6.1.2.1.33.1.6.2.1.2"


@dataclass(frozen=True)
class Vendor:
    """A vendor as the generator uses one: its enterprise number and its identity-varbind arc."""

    key: str
    pen: int
    #: Where this vendor's own identity/description objects live (a column under its enterprise
    #: arc). Illustrative for every vendor: the generator needs *a* vendor-private varbind, and the
    #: appliance treats every varbind OID as an opaque token.
    object_arc: str
    #: Whether the vendor stamps an X.733 severity word on its alarms. Some do and some do not,
    #: which is what makes severity provenance a real feature rather than a constant.
    carries_severity: bool


VENDORS: dict[str, Vendor] = {
    "huawei": Vendor("huawei", 2011, "1.3.6.1.4.1.2011.5.25.31.1.1.1.1.7", True),
    "zte": Vendor("zte", 3902, "1.3.6.1.4.1.3902.1082.10.1.2.4.1.2", True),
    "nokia": Vendor("nokia", 6527, "1.3.6.1.4.1.6527.3.1.2.2.1.8.1.8", False),
    "juniper": Vendor("juniper", 2636, "1.3.6.1.4.1.2636.3.1.13.1.5", False),
    "cisco": Vendor("cisco", 9, "1.3.6.1.4.1.9.9.117.1.1.2.1.1", False),
    "ciena": Vendor("ciena", 1271, "1.3.6.1.4.1.1271.2.1.1.1.1.1.2", True),
    "adva": Vendor("adva", 2544, "1.3.6.1.4.1.2544.1.11.7.2.7.1.3", True),
}


@dataclass(frozen=True)
class Notification:
    """One raise and how it clears. ``clear`` is a second OID, ``None`` for a trap that never
    clears (a cold start, a dying gasp), or the raise OID itself for a state-style trap whose
    clear is the same notification carrying a different state value."""

    name: str
    oid: str
    clear: str | None
    attested: bool = True


def _oid(name: str) -> str:
    """The pack's OID for a notification name. Raises if the pack does not declare it."""
    for entry in _by_name().get(name, ()):
        return entry
    raise KeyError(f"the trap pack declares no notification named {name!r}")


@cache
def _by_name() -> dict[str, tuple[str, ...]]:
    out: dict[str, list[str]] = {}
    for oid, entry in sorted(trappack.pack().notifications.items()):
        out.setdefault(entry.name, []).append(oid)
    return {name: tuple(oids) for name, oids in out.items()}


def _pair(raise_name: str, clear_name: str | None) -> Notification:
    return Notification(
        raise_name, _oid(raise_name), None if clear_name is None else _oid(clear_name)
    )


def _state(name: str) -> Notification:
    oid = _oid(name)
    return Notification(name, oid, oid)


def _illustrative(name: str, oid: str, clear: str | None) -> Notification:
    return Notification(name, oid, clear, attested=False)


@cache
def _roles() -> dict[str, dict[str, Notification]]:
    std_link = _pair("linkDown", "linkUp")
    roles: dict[str, dict[str, Notification]] = {
        # -- physical / optical --------------------------------------------------------------
        "link_down": {"*": std_link},
        "signal_degrade": {
            "ciena": _pair(
                "cienaCesPortNotificationPortSignalDegradeSet",
                "cienaCesPortNotificationPortSignalDegradeClear",
            ),
            "huawei": _pair("hwOpticalPowerAbnormal", "hwOpticalPowerResume"),
            "adva": _state("alarmOptInputPwrReceivedTooLow"),
            "*": std_link,
        },
        "rx_power_low": {
            "ciena": _pair(
                "cienaCesPortXcvrRxPowerLowNotification",
                "cienaCesPortXcvrRxPowerNormalNotification",
            ),
            "huawei": _pair("hwOpticalPowerAbnormal", "hwOpticalPowerResume"),
            "adva": _state("alarmOptInputPwrReceivedTooLow"),
            "cisco": _state("entSensorThresholdNotification"),
        },
        "optical_los": {
            "adva": _state("alarmLossOfSignal"),
            "*": std_link,
        },
        "amp_output_low": {
            "adva": _state("alarmOptOutputPowerTransTooLow"),
            "ciena": _pair(
                "cienaCesPortXcvrRxPowerLowNotification",
                "cienaCesPortXcvrRxPowerNormalNotification",
            ),
        },
        "amp_abnormal": {"adva": _state("alarmAmplifierAbnormal")},
        "loss_of_frame": {"adva": _state("alarmLossOfFrame"), "*": std_link},
        "protection_switch": {
            "ciena": _pair("cienaCesModuleHASwitchOverNotification", None),
            "adva": _pair("transientManualWorkingSwitchedtoProtection", None),
            "*": _pair("entConfigChange", None),
        },
        # -- equipment ----------------------------------------------------------------------
        "board_fail": {
            "huawei": _pair("hwBoardFail", "hwBoardFailResume"),
            "nokia": _pair("tmnxEqCardFailure", "tmnxEqCardInserted"),
            "juniper": _pair("jnxFruFailed", "jnxFruOK"),
            "cisco": _state("cefcModuleStatusChange"),
            "zte": _pair("zxAnChassisSubcardDown", None),
            "*": _pair("entConfigChange", None),
        },
        "board_removed": {
            "huawei": _pair("hwBoardRemove", "hwBoardInsert"),
            "nokia": _pair("tmnxEqCardRemoved", "tmnxEqCardInserted"),
            "juniper": _pair("jnxFruRemoval", "jnxFruInsertion"),
            "cisco": _pair("cefcFRURemoved", "cefcFRUInserted"),
            "*": _pair("entConfigChange", None),
        },
        "psu_fail": {
            "huawei": _pair("hwPowerFail", "hwPowerFailResume"),
            "nokia": _pair("tmnxEqPowerSupplyFailure", "tmnxEqPowerSupplyInserted"),
            "juniper": _pair("jnxPowerSupplyFailure", "jnxPowerSupplyOK"),
            "cisco": _state("ciscoEnvMonRedundantSupplyNotification"),
            "zte": _pair("zxAnEnvPowerSupplyModuleDown", "zxAnEnvPowerSupplyModuleUp"),
            "ciena": _pair(
                "cienaCesChassisPowerSupplyFaultedNotification",
                "cienaCesChassisPowerSupplyOnlineNotification",
            ),
            "adva": _state("alarmPowerSupplyUnitFailure"),
        },
        "mains_fail": {
            "zte": _pair("zxAnEnvACPowerDownAlm", "zxAnEnvACPowerDownClr"),
            "*": _pair("upsTrapAlarmEntryAdded", "upsTrapAlarmEntryRemoved"),
        },
        "on_battery": {"*": _pair("upsTrapOnBattery", None)},
        "fan_fail": {
            "huawei": _pair("hwFanFail", "hwFanFailResume"),
            "juniper": _pair("jnxFanFailure", "jnxFanOK"),
            "cisco": _state("ciscoEnvMonFanNotification"),
            "zte": _pair("zxAnEnvFanLinkDown", "zxAnEnvFanLinkUp"),
            "nokia": _pair("tmnxEqFanFailure", None),
        },
        "temp_high": {
            "huawei": _pair("hwTempRisingAlarm", "hwTempRisingResume"),
            "juniper": _pair("jnxOverTemperature", "jnxTemperatureOK"),
            "cisco": _state("ciscoEnvMonTemperatureNotification"),
            "zte": _pair("zxAnEnvHighTempAlm", "zxAnEnvHighTempClr"),
            "nokia": _pair("tmnxEnvTempTooHigh", None),
            "adva": _state("alarmTemperatureTooHigh"),
            "ciena": _state("cienaCesModuleSensorHighTempNotification"),
        },
        "dying_gasp": {
            "huawei": _pair("hwEntityDyingGasp", None),
            "juniper": _pair("jnxDyingGasp", None),
            "nokia": _pair("tmnxSysDyingGasp", None),
            "ciena": _pair("cienaCesChassisDyingGaspNotification", None),
        },
        "cold_start": {"*": _pair("coldStart", None)},
        "warm_start": {"*": _pair("warmStart", None)},
        "config_change": {"*": _pair("entConfigChange", None)},
        "auth_failure": {"*": _pair("authenticationFailure", None)},
        "cpu_high": {
            "cisco": _pair("cpmCPURisingThreshold", "cpmCPUFallingThreshold"),
        },
        # -- routing -------------------------------------------------------------------------
        "bgp_down": {
            "cisco": _pair("cbgpPeer2BackwardTransNotification", "cbgpPeer2EstablishedNotification"),
            "juniper": _pair("jnxBgpM2BackwardTransition", "jnxBgpM2Established"),
            "nokia": _pair("tBgpNgBackwardTransition", "tBgpNgEstablished"),
            "*": _pair("bgpBackwardTransNotification", "bgpEstablishedNotification"),
        },
        "ospf_nbr": {
            "huawei": _state("hwOspfv3NbrStateChange"),
            "juniper": _state("jnxOspfv3NbrStateChange"),
            "*": _state("ospfNbrStateChange"),
        },
        # -- GPON access (ILLUSTRATIVE arcs; see the module docstring) ------------------------
        "pon_los": {
            "huawei": _illustrative(
                "hwGponOltPonLos", "1.3.6.1.4.1.2011.2.248.12.1.1", "1.3.6.1.4.1.2011.2.248.12.1.2"
            ),
            "zte": _illustrative(
                "zxGponOltPonLos", "1.3.6.1.4.1.3902.1082.500.20.2.2.1", "1.3.6.1.4.1.3902.1082.500.20.2.2.2"
            ),
        },
        "onu_los": {
            "huawei": _illustrative(
                "hwGponOntLos", "1.3.6.1.4.1.2011.2.248.12.2.1", "1.3.6.1.4.1.2011.2.248.12.2.2"
            ),
            "zte": _illustrative(
                "zxGponOnuLos", "1.3.6.1.4.1.3902.1082.500.20.3.1.1", "1.3.6.1.4.1.3902.1082.500.20.3.1.2"
            ),
        },
        "onu_dying_gasp": {
            "huawei": _illustrative(
                "hwGponOntDyingGasp", "1.3.6.1.4.1.2011.2.248.12.2.3", "1.3.6.1.4.1.2011.2.248.12.2.2"
            ),
            "zte": _illustrative(
                "zxGponOnuDyingGasp", "1.3.6.1.4.1.3902.1082.500.20.3.1.3", "1.3.6.1.4.1.3902.1082.500.20.3.1.2"
            ),
        },
    }
    return roles


@dataclass(frozen=True)
class Rendering:
    """A role rendered for one element: the notification and the vendor that sent it."""

    role: str
    notification: Notification
    vendor: Vendor


def render(role: str, vendor_key: str) -> Rendering | None:
    """The notification ``vendor_key`` sends for ``role``, the standard one, or ``None``.

    ``None`` rather than a stand-in: a Huawei device does not send a Cisco trap, and rendering
    another vendor's notification from it would teach a model a relation no estate produces.
    """
    table = _roles()[role]
    note = table.get(vendor_key) or table.get("*")
    return None if note is None else Rendering(role, note, VENDORS[vendor_key])


def render_first(chain: tuple[str, ...], vendor_key: str) -> Rendering:
    """The first role of ``chain`` this vendor can express. Every chain ends in a role with a
    standard fallback, so this always answers; a chain that does not is a generator bug."""
    for role in chain:
        found = render(role, vendor_key)
        if found is not None:
            return found
    raise KeyError(f"no role of {chain} is expressible by vendor {vendor_key!r}")


def roles() -> dict[str, dict[str, Notification]]:
    """Every role, resolved. Resolving is what proves each name exists in the pack."""
    return _roles()
