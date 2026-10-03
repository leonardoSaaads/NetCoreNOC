"""An alarm's occurrences: when a repeated raise is a new one, and when one with no clear ends.

**v0.28.0 (ADRs #431, #433).** One alarm row is one fingerprint — ``(device, class, instance)`` —
and it is shared by every occurrence of that fault. Until this release an *active* alarm that was
raised again was always a **repeat**: its count went up and nothing else happened. Measured on
real-engine scenarios (`tests/lifecycle/test_occurrences.py`):

* **an operator closed the situation, the fault kept reporting** — every later trap was absorbed
  into the resolved situation. The alarm was active and in no live view: invisible;
* **the clear was lost** (UDP, a restart, a device that never sends one) and the fault came back
  hours later — it stayed in the old situation, untouched, and no new one was opened;
* **a reboot** (``coldStart``) has no clear in its standard, so its alarm stayed active for good
  and, by v0.16.2's rule, its situation stayed ``new`` for good;
* **an intermittent port** (down/up every ten minutes) opened one situation per bounce, because
  the five-minute hold (#410) had always expired before the next one.

The decisions are here; `engine.py` makes one call for each (`rearm` on the batch path, `settled`
in the sweep), so the ingest path stays readable in one place (DECISIONS #90) and this module is
where a reviewer reads what an occurrence is.

**No lock is taken here.** Both functions run inside a caller that holds ``store.lock`` (the batch
or the sweep); the store methods they call are SQL and take none.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from netcorenoc.engine.dataset import gestures
from netcorenoc.ingest.known_oids import OCCURRENCE_NOTIFICATIONS

if TYPE_CHECKING:  # pragma: no cover - type-only, no runtime edge (tests/repo/test_layers.py)
    from netcorenoc.engine.operate.engine import Engine
    from netcorenoc.ingest.events import TrapEvent
    from netcorenoc.store import IngestResult

__all__ = ["INTERMITTENT_ACTIVATIONS", "REARM_S", "rearm", "settled"]

#: The default **re-arm window**, in seconds (``NETCORENOC_REARM_S``; 0 turns the silence rule
#: off). The same hour the idle sweep uses (`IDLE_CLOSE_S`): an alarm whose situation has heard
#: nothing from it for that long, and nothing from any other member that is still on, is over.
REARM_S = 3600.0

#: Activations of one fingerprint within the flap detector's hour that make it **intermittent** —
#: its second bounce. Its situation is then held after the last clear for as long as that stays
#: true, rather than for `CLEAR_HOLD_S`, so the next bounce rejoins it (ADR #433). A single bounce
#: keeps #410's five-minute hold.
INTERMITTENT_ACTIVATIONS = 3


async def rearm(engine: Engine, result: IngestResult, event: TrapEvent, instance: str) -> bool:
    """Is this repeat of an **active** alarm a new occurrence? If so, end the previous one.

    Returns True when the trap must be correlated as an activation. Two cases, and only two:

    1. **Orphaned** — no live situation holds the alarm, because an operator closed it while the
       fault was still on. The next trap is the fault reporting again; it opens a situation,
       exactly as the next trap after a hand-clear always did.
    2. **Silent** — the alarm said nothing for longer than the re-arm window **and** it is the only
       member of its situation still active. The previous occurrence is over even though its clear
       never arrived: the situation resolves as ``idle`` with an ``idle_close`` event naming the
       alarm, and the trap starts a new situation. A situation with another member still on is a
       live incident, and the repeat stays in it.

    A fingerprint the flap detector is suppressing stays suppressed: re-arming it would turn every
    repeat into an activation and undo the demotion.
    """
    if (event.device, event.trap_oid, instance) in engine.flapping:
        return False
    sid = engine.sit_of.get(result.alarm_id)
    if sid is None:
        engine.correlator.remove(result.alarm_id)
        return True
    previous = result.previous_seen
    if engine.rearm_s <= 0 or previous is None or event.ts - previous <= engine.rearm_s:
        return False
    if not await engine.store.sole_active_member(sid, result.alarm_id):
        return False
    subject = await gestures.snapshot(engine.store, sid)
    if not await engine.store.resolve_situation(sid, "idle", event.ts):
        return False
    gesture = gestures.Gesture("idle_close", sid, event.ts, alarm_id=result.alarm_id)
    await gestures.record(engine.store, gesture, subject)
    engine.forget_situation(sid)
    engine.correlator.remove(result.alarm_id)
    return True


async def settled(engine: Engine, now: float, hold_s: float) -> list[int]:
    """The live situations the clear-hold sweep may resolve now.

    First, **occurrence-only notifications end** (ADR #433): an active alarm of a class
    `known_oids.OCCURRENCE_NOTIFICATIONS` names, silent for `hold_s`, is cleared — and leaves the
    correlator's window, as a received clear makes it. Its situation is then all-cleared, and the
    ordinary hold resolves it as ``self_cleared`` — a bounce inside that hold still rejoins it.

    Then the all-cleared situations past their hold are returned, **except** those holding an
    intermittent fingerprint — one the flap detector saw activate `INTERMITTENT_ACTIVATIONS` times
    in its hour. Those stay live, so the next bounce rejoins the same situation instead of opening
    another; they resolve once the fingerprint has been quiet for that hour. The flap memory is in
    process: after a restart the ordinary hold applies, which is the pre-v0.28.0 behaviour.
    """
    ended = await engine.store.end_occurrences(OCCURRENCE_NOTIFICATIONS, now - hold_s, now)
    for alarm_id in ended:
        engine.correlator.remove(alarm_id)
    candidates = await engine.store.cleared_open_situations(now - hold_s)
    fingerprints = await engine.store.member_fingerprints(candidates)
    return [
        sid
        for sid in candidates
        if not any(
            engine.flap.recent(fp, now) >= INTERMITTENT_ACTIVATIONS
            for fp in fingerprints.get(sid, ())
        )
    ]
