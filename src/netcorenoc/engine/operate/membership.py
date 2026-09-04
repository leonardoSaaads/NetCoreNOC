"""Keeping the engine's in-memory membership honest when an **operator** moves rows.

`Engine` holds two maps that mirror `situation_alarm` — `sit_of` (alarm -> its open situation) and
`members` (situation -> its `Member` list) — and they exist so the correlator's hot path never has
to read them back. Every writer of those maps has been the ingest path itself, so they could not
disagree with the database: the same function wrote both.

This release adds four writers that are **not** the ingest path, and this module is the whole of
their in-memory half. It is deliberately small, deliberately dumb, and it decides nothing.

**Why here rather than in `engine.py`.** `engine/operate/engine.py` is byte-identical through this
release (DECISIONS #259) — `TRAP_PATH_HASHES` and `TRAP_PATH_BODY_HASHES` are unchanged, and that
is the strongest available evidence that a release which adds a state machine, an event log and five
operator operations did not touch the trap path. Adding a mixin would have changed its class header
and forfeited exactly that. These are free functions taking the engine, the way
`engine/dataset/labels.py` takes the store and the capture.

**What happens if a map is wrong.** Nothing catastrophic and nothing silent: `sit_of` decides which
situation a *newly linked* alarm joins, so a stale entry would merge into a situation the alarm has
left. The functions below are called inside the same `write_txn` as the row movement, so the two
land together or not at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type-only, no runtime edge (tests/test_layers.py)
    from netcorenoc.engine.operate.engine import Engine

__all__ = ["cleared", "merged", "moved", "split"]


def moved(engine: Engine, alarm_id: int, from_situation: int, to_situation: int) -> None:
    """One alarm changes situation. The `Member` record travels with it, unchanged.

    The record itself is not rebuilt from the database: it carries the alarm's class, device and
    first-seen time, none of which a move changes, and rebuilding it would be a second read of facts
    the map already holds correctly.

    A destination the engine is not tracking — a `resolved` situation the operator moved into, which
    the route refuses, or one that resolved between the read and the write — simply gains no
    in-memory entry. `sit_of` then does not name it, which is the correct state for a situation the
    correlator is no longer growing.
    """
    member = next(
        (m for m in engine.members.get(from_situation, []) if m.alarm_id == alarm_id), None
    )
    if member is not None:
        engine.members[from_situation].remove(member)
    if to_situation in engine.members and member is not None:
        engine.members[to_situation].append(member)
        engine.sit_of[alarm_id] = to_situation
    else:
        engine.sit_of.pop(alarm_id, None)


def merged(engine: Engine, dst: int, src: int) -> None:
    """`src`'s members become `dst`'s. **The same bookkeeping `_assign_situation` does on a merge**,
    which is why it is written the same way: pop the source, re-point each member, append."""
    for member in engine.members.pop(src, []):
        engine.sit_of[member.alarm_id] = dst
        engine.members.setdefault(dst, []).append(member)


def split(engine: Engine, situation_id: int, new_situation: int, departing: set[int]) -> None:
    """`departing` leaves `situation_id` for `new_situation`, which the engine starts tracking.

    The new situation is live, so it gets a `members` entry: the correlator may legitimately grow
    it, and a situation the engine does not track would silently stop accepting new members.
    """
    staying = [m for m in engine.members.get(situation_id, []) if m.alarm_id not in departing]
    leaving = [m for m in engine.members.get(situation_id, []) if m.alarm_id in departing]
    if situation_id in engine.members:
        engine.members[situation_id] = staying
    engine.members[new_situation] = leaving
    for member in leaving:
        engine.sit_of[member.alarm_id] = new_situation


def cleared(engine: Engine, alarm_id: int) -> None:
    """A hand-cleared alarm leaves the correlator's window.

    **The same call `_handle_clear` makes** when a device sends the clear itself:
    `correlator.remove` is O(1) and leaves a tombstone, so an alarm an operator cleared and one the
    network cleared are in the same state afterwards. That is what stops a zombie clear from
    being a second kind of cleared alarm nothing else knows about.

    `sit_of` and `members` are deliberately **not** touched: the alarm is still a member of its
    situation, and a cleared member is an ordinary thing for a situation to hold — `all_cleared` is
    the query that asks whether every one of them is.
    """
    engine.correlator.remove(alarm_id)
