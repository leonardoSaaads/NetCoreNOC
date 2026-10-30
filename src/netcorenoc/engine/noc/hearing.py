"""A case hears its alarms (ADR #474): what changed on the Broker since the case last looked.

Every half minute the case loop reads each live case's alarms beside what it heard last
(`store/noc_hearing.py`) and tells the case what changed, in one event on its feed:

* an alarm **joined** its situation — the fault grew, or the far end's side of it arrived;
* an alarm **raised again** after it had cleared — the fault came back — read from its count, so
  a port that went down and up again between two looks is heard too ("raised again, cleared
  since");
* an alarm **cleared** — the fault, or a part of it, is over.

An alarm that a newer live situation holds too is named with it ("now in situation #95"): a fault
that came back hours after its situation resolved is a new situation on the Broker, and the case
still validating the old one is the first place a NOC operator looks for it (ADR #476's lab).

A **pending** case whose fault came back or grew is back in the NOC queue (`alarm`): what it waited
on may no longer be the whole story. Found in the round's lab: a switch lost power while the case
for its trunk was pending on the field team, the trunk's alarm raised again, and the case waited
51 more minutes for its next update. A case being worked is told and stays with its owner.

The first look at a case is its **baseline** and says nothing, so opening a case, restarting the
appliance or installing this release never floods a feed with old news. An alarm the case was told
of less than `QUIET_S` ago waits: what it does meanwhile adds up and is said once the five minutes
are over, so a port flapping for an hour is a line every five minutes, not one every half minute
(the round's lab: six lines in two and a half minutes on one case). A new alarm joining is never
held, nor a fault back on a pending case: that case is back in the queue at once.

`plan` is pure; `apply` runs under the store lock, inside the case loop's one transaction.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from netcorenoc.engine.noc import case_ops, cases

if TYPE_CHECKING:
    from netcorenoc.store import Store

__all__ = ["MAX_NAMED", "QUIET_S", "News", "apply", "plan", "sentence"]

#: Alarms named in one event, per kind; the rest are counted.
MAX_NAMED = 6
#: How long an alarm the case was just told of waits before its next news.
QUIET_S = 300.0

#: What a case heard of one alarm: ``(active, raised count, told at)``.
Heard = tuple[bool, int, "float | None"]


@dataclass(frozen=True)
class News:
    joined: tuple[Mapping[str, Any], ...] = ()
    raised: tuple[Mapping[str, Any], ...] = ()
    cleared: tuple[Mapping[str, Any], ...] = ()
    #: ``(alarm_id, active, raised, told at)`` for every alarm whose heard state must be written.
    moved: tuple[tuple[int, bool, int, float | None], ...] = field(default=(), compare=False)

    @property
    def worse(self) -> bool:
        """The fault grew or came back."""
        return bool(self.joined or self.raised)

    def __bool__(self) -> bool:
        return bool(self.joined or self.raised or self.cleared)


def _news(heard: Mapping[int, Heard], alarms: Sequence[Mapping[str, Any]], at: float) -> News:
    if not heard:  # the baseline: written down, not said
        return News(
            moved=tuple(
                (int(a["alarm_id"]), bool(a["active"]), int(a["raised"]), None) for a in alarms
            )
        )
    joined, raised, cleared, moved = [], [], [], []
    pending = bool(alarms) and alarms[0].get("case_status") in cases.PENDING
    for alarm in alarms:
        aid, active, count = int(alarm["alarm_id"]), bool(alarm["active"]), int(alarm["raised"])
        before = heard.get(aid)
        if before is None:
            if active:
                joined.append(alarm)
            moved.append((aid, active, count, at if active else None))
            continue
        was, then, told = before
        if (was, then) == (active, count):
            continue
        back = not was and count > then
        if told is not None and at - told < QUIET_S and not (back and pending):
            continue  # just told: its changes add up until the quiet ends
        if back:
            # Raised again: still on, or on and off again since the last look (a flap).
            raised.append({**alarm, "since": not active})
        elif was and not active:
            cleared.append(alarm)
        else:
            moved.append((aid, active, count, told))  # a repeat while on: written, not said
            continue
        moved.append((aid, active, count, at))
    return News(tuple(joined), tuple(raised), tuple(cleared), tuple(moved))


def plan(
    states: Mapping[int, Sequence[Mapping[str, Any]]],
    heard: Mapping[int, Mapping[int, Heard]],
    at: float,
) -> dict[int, News]:
    """Each case with something to write down: its news (possibly none — a baseline)."""
    out = {}
    for sid, alarms in states.items():
        found = _news(heard.get(sid, {}), alarms, at)
        if found.moved:
            out[sid] = found
    return out


def _named(alarm: Mapping[str, Any]) -> str:
    """`linkDown on tim-rjo-barra-crs326 (ether5) [flapping, cleared since]` — the qualifiers in
    brackets, so a list of several alarms reads as a list (ADR #476's lab)."""
    where = f" ({alarm['instance']})" if alarm.get("instance") else ""
    said = [
        word
        for word, on in (("flapping", alarm.get("flapping")), ("cleared since", alarm.get("since")))
        if on
    ]
    if alarm.get("elsewhere"):
        said.append(f"now in situation #{alarm['elsewhere']}")
    return f"{alarm['what']} on {alarm['element']}{where}" + (
        f" [{', '.join(said)}]" if said else ""
    )


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
