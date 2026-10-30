"""A case hears its alarms (ADR #474): what changed on the Broker since the case last looked.

Every half minute the case loop reads each live case's alarms beside what it heard last
(`store/noc_hearing.py`) and tells the case what changed, in one event on its feed:

* an alarm **joined** its situation — the fault grew, or the far end's side of it arrived;
* an alarm **raised again** after it had cleared — the fault came back — read from its count, so
  a port that went down and up again between two looks is heard too ("raised again, cleared
  since");
* an alarm **cleared** — the fault, or a part of it, is over.

A **pending** case whose fault came back or grew is back in the NOC queue (`alarm`): what it waited
on may no longer be the whole story. Found in the round's lab: a switch lost power while the case
for its trunk was pending on the field team, the trunk's alarm raised again, and the case waited
51 more minutes for its next update. A case being worked is told and stays with its owner.

The first look at a case is its **baseline** and says nothing, so opening a case, restarting the
appliance or installing this release never floods a feed with old news. A flapping alarm says so,
once per look, rather than each raise and clear the half minute caught.

`plan` is pure; `apply` runs under the store lock, inside the case loop's one transaction.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from netcorenoc.engine.noc import case_ops, cases

if TYPE_CHECKING:
    from netcorenoc.store import Store

__all__ = ["MAX_NAMED", "News", "apply", "plan", "sentence"]

#: Alarms named in one event, per kind; the rest are counted.
MAX_NAMED = 6


@dataclass(frozen=True)
class News:
    joined: tuple[Mapping[str, Any], ...] = ()
    raised: tuple[Mapping[str, Any], ...] = ()
    cleared: tuple[Mapping[str, Any], ...] = ()
    #: ``(alarm_id, active, raised)`` for every alarm whose heard state must be written down.
    moved: tuple[tuple[int, bool, int], ...] = field(default=(), compare=False)

    @property
    def worse(self) -> bool:
        """The fault grew or came back."""
        return bool(self.joined or self.raised)

    def __bool__(self) -> bool:
        return bool(self.joined or self.raised or self.cleared)


def _news(heard: Mapping[int, tuple[bool, int]], alarms: Sequence[Mapping[str, Any]]) -> News:
    now = {int(a["alarm_id"]): (bool(a["active"]), int(a["raised"])) for a in alarms}
    moved = tuple((i, *state) for i, state in now.items() if heard.get(i) != state)
    if not heard:
        return News(moved=moved)  # the baseline: written down, not said
    joined, raised, cleared = [], [], []
    for alarm in alarms:
        before = heard.get(int(alarm["alarm_id"]))
        active, count = now[int(alarm["alarm_id"])]
        if before is None:
            if active:
                joined.append(alarm)
        elif not before[0] and count > before[1]:
            # Raised again: still on, or on and off again since the last look (a flap).
            raised.append({**alarm, "since": not active})
        elif before[0] and not active:
            cleared.append(alarm)
    return News(tuple(joined), tuple(raised), tuple(cleared), moved)


def plan(
    states: Mapping[int, Sequence[Mapping[str, Any]]],
    heard: Mapping[int, Mapping[int, tuple[bool, int]]],
) -> dict[int, News]:
    """Each case with something to write down: its news (possibly none — a baseline)."""
    out = {}
    for sid, alarms in states.items():
        found = _news(heard.get(sid, {}), alarms)
        if found.moved:
            out[sid] = found
    return out


def _named(alarm: Mapping[str, Any]) -> str:
    where = f" ({alarm['instance']})" if alarm.get("instance") else ""
    flap = ", flapping" if alarm.get("flapping") else ""
    gone = ", cleared since" if alarm.get("since") else ""
    return f"{alarm['what']} on {alarm['element']}{where}{flap}{gone}"


def sentence(found: News) -> str:
    """One line for the feed: what joined, raised again and cleared, a few named each."""
    parts = []
    for word, rows in (
        ("joined", found.joined),
        ("raised again", found.raised),
        ("cleared", found.cleared),
    ):
        if not rows:
            continue
        names = ", ".join(_named(a) for a in rows[:MAX_NAMED])
        more = len(rows) - MAX_NAMED
        parts.append(f"{word}: {names}{f' and {more} more' if more > 0 else ''}")
    return "Its alarms on the Broker — " + "; ".join(parts) + "."


async def apply(store: Store, sid: int, found: News, now: float) -> str | None:
    """Tell one case its news and write down what moved: ``"requeued"`` when a pending case's
    fault came back or grew, ``"told"`` when it was only told, None when there was nothing to say
    (a baseline, or a case no longer listening)."""
    case = await store.noc_case(sid)
    if case is None or case["status"] in ("new", "auto", "resolved", "closed"):
        return None
    await store.noc_case_hear(sid, found.moved)
    if not found:
        return None
    await case_ops.event(store, sid, sentence(found), now)
    if found.worse and case["status"] in cases.PENDING:
        await case_ops.requeue(store, sid, "alarm", now)
        return "requeued"
    return "told"
