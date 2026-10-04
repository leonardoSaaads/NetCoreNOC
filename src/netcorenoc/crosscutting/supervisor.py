"""Keeping the appliance's long-lived background tasks alive (§A.5, F10).

Moved out of `runner.py` in v0.29.0, at the 400-line module guard, when the host sampler learned to
record the trap rate and the batch latency (ADR #437). `runner` re-exports it by identity, so
`netcorenoc.runner.Supervisor` and `netcorenoc.main.Supervisor` are this class.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

__all__ = ["SUPERVISOR_BACKOFF_BASE_S", "SUPERVISOR_BACKOFF_MAX_S", "Supervisor"]

log = logging.getLogger("netcorenoc")

SUPERVISOR_BACKOFF_BASE_S = 1.0
SUPERVISOR_BACKOFF_MAX_S = 30.0


@dataclass
class Supervisor:
    """Keeps long-lived background tasks alive (§A.5, F10).

    A supervised task that raises is logged (through the redaction filter), counted, and — where
    a restart is safe — restarted with capped exponential backoff; the crash is surfaced through
    ``operator_warnings()`` so it is never silent. A *cancelled* task (graceful shutdown) is never
    restarted. This supervises the engine and the maintenance loop only; the trap datagram path
    lives in the receiver's UDP callback, which cannot raise into the event loop, and is untouched.
    """

    crashes: dict[str, int] = field(default_factory=dict)
    last_error: dict[str, str] = field(default_factory=dict)
    backoff_base: float = SUPERVISOR_BACKOFF_BASE_S
    backoff_max: float = SUPERVISOR_BACKOFF_MAX_S

    async def run(self, name: str, factory: Callable[[], Any], *, restart: bool = True) -> None:
        delay = self.backoff_base
        while True:
            try:
                await factory()
                return  # a task that returns cleanly is done (infinite loops never do)
            except asyncio.CancelledError:
                raise  # shutdown — propagate, never restart
            except Exception as exc:  # the supervisor's whole job is to catch and recover
                self.crashes[name] = self.crashes.get(name, 0) + 1
                self.last_error[name] = type(exc).__name__
                log.exception("supervised task %r crashed (restart=%s)", name, restart)
                if not restart:
                    return
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.backoff_max)

    def warnings(self) -> list[str]:
        return [
            f"Background task '{name}' crashed {n} time(s) (last: {self.last_error.get(name, '?')})"
            " and was restarted; ingestion/correlation may have paused. Check the logs."
            for name, n in sorted(self.crashes.items())
            if n > 0
        ]
