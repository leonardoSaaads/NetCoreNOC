"""Access to a device wakes the cases that waited for it (ADR #478).

An agent that cannot reach a device — no credential it may lease, or none that answers — says so
on the feed and sets the case to `pending_connectivity` (or `pending_device_access`): nothing it
can do restores the service until somebody gives it access, and the restore clock pauses while it
waits (`migrations/0042_noc_case_pause.sql`). The moment a custodian lets the agents use a
credential for one of the case's devices, the wait is over: the case goes back to the NOC queue
(`access`) and to its agent, woken (`case_ops.requeue`), instead of sleeping until its next update.

Matched by address: a credential's host against the address of each element the case's alarms
came from. The caller holds the write transaction and commits, as for `case_ops`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from netcorenoc.engine.noc import case_ops

if TYPE_CHECKING:
    from netcorenoc.store import Store

__all__ = ["WAITING", "granted"]

#: The pending statuses that wait on the NOC's own access to a device.
WAITING = ("pending_connectivity", "pending_device_access")


async def granted(store: Store, host: str, now: float) -> list[int]:
    """The agents may now use a credential for ``host``: every case waiting for access to it goes
    back to the queue and its agent. Returns the cases woken."""
    woken = await store.noc_cases_awaiting(host, WAITING)
    for sid in woken:
        note = f"A credential for {host} is now usable by the agents."
        await case_ops.requeue(store, sid, "access", now, note=note)
    return woken
