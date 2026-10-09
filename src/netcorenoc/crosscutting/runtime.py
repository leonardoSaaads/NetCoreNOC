"""Runtime configuration shared between the API, the receiver, and the maintenance loop.

Config precedence (DESIGN v0.2): environment variables are the defaults; an admin saving
a value from the UI writes a ``meta`` row that then takes precedence. This small holder is
the in-memory view both sides read, refreshed from ``meta`` at startup and on every audited
change so the running receiver (allowlist, SNMP policy) and maintenance loop (retention) pick
it up without a restart.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from netcorenoc.ingest.receiver import Network, parse_allowlist

if TYPE_CHECKING:
    from netcorenoc.ingest.snmpconf import SnmpPolicy


def _ignore(_value: Any) -> None:
    """The default for a callback a test does not wire: nothing to push the value into."""


@dataclass
class RuntimeConfig:
    allowlist: str
    retention_days: float
    # Set by main to push a new allowlist into the live receiver; no-op in tests.
    on_allowlist_change: Callable[[list[Network] | None], None] = field(default=_ignore)
    # v0.30.0: the receiver's SNMP policy and the hook that swaps it in the live receiver.
    snmp: SnmpPolicy | None = None
    on_snmp_change: Callable[[SnmpPolicy], None] = field(default=_ignore)
    # v0.30.0: what the receiver refused, by reason, since the process started. Read-only here.
    refusals: Callable[[], dict[str, int]] = field(default=dict)

    def networks(self) -> list[Network] | None:
        return parse_allowlist(self.allowlist)

    def apply_allowlist(self, allowlist: str) -> None:
        self.allowlist = allowlist
        self.on_allowlist_change(self.networks())

    def apply_snmp(self, policy: SnmpPolicy) -> None:
        self.snmp = policy
        self.on_snmp_change(policy)
