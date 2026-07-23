"""Runtime configuration shared between the API, the receiver, and the maintenance loop.

Config precedence (DESIGN v0.2): environment variables are the defaults; an admin saving
a value from the UI writes a ``meta`` row that then takes precedence. This small holder is
the in-memory view both sides read, refreshed from ``meta`` at startup and on every audited
change so the running receiver (allowlist) and maintenance loop (retention) pick it up
without a restart.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from opticorr.receiver import Network, parse_allowlist


@dataclass
class RuntimeConfig:
    allowlist: str
    retention_days: float
    # Set by main to push a new allowlist into the live receiver; no-op in tests.
    on_allowlist_change: Callable[[list[Network] | None], None] = field(default=lambda _n: None)

    def networks(self) -> list[Network] | None:
        return parse_allowlist(self.allowlist)

    def apply_allowlist(self, allowlist: str) -> None:
        self.allowlist = allowlist
        self.on_allowlist_change(self.networks())
