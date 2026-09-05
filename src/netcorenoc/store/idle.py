"""The idle population: what nobody has touched, and which of it is still burning.

**Split out of `situations.py` in v0.16.2** (DECISIONS #274), and the reason is the ordinary one:
that module is the situation's *lifecycle writes* — create, promote, join, merge, close — and the
repair added four queries that are not writes and not about one situation. It went to 415 lines
against a 400-line guard, and the honest repair to a module over budget is not less prose in it, it
is noticing it had become two things.

One question, asked four ways:

    all_cleared(sid)              is this ONE situation quiet?
    idle_open_situations(cutoff)  quiet AND untouched -- what the sweep may resolve
    idle_active_situations(...)   NOT quiet AND untouched -- what it must NOT, and what the
                                  operator most needs to see
    with_idle_active(rows, ...)   the same answer, spread across a page of list rows

The two middle ones **partition** the live-and-untouched population, which
`tests/test_store.py::test_idle_open_situations` asserts on a fixture where both halves are
non-empty — a partition with an unreachable arm is a state that does not exist.

The `HAS_ACTIVE` fragment and `LIVE` stay in `situations.py`, imported here. `close_situation` uses
both and three other store modules already import `LIVE`; moving them would have made a 100-line
split touch four files to no end, and this direction is the one that does not cycle.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.store.base import StoreBase
from netcorenoc.store.situations import HAS_ACTIVE, LIVE


class IdleMixin(StoreBase):
    """Four queries and no state. Every attribute it touches is declared on `StoreBase`."""

    async def all_cleared(self, situation_id: int) -> bool:
        cur = await self.conn.execute(
            "SELECT COUNT(*) FROM situation_alarm sa JOIN alarm a ON a.id=sa.alarm_id "
            "WHERE sa.situation_id=? AND a.status='active'",
            (situation_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0]) == 0

    async def idle_open_situations(self, cutoff: float) -> list[int]:
        """**The situations the idle sweep may resolve**: live, untouched since `cutoff`, and
        holding no alarm that is still active.

        `new` counts as live. A situation the correlator opened and nobody triaged is exactly what
        the sweep is for, so both live states are here (DECISIONS #254).

        ## What this used to return, and why that was the release's critical defect

        Until v0.16.2 this asked two questions — *is it live?* and *has nobody touched it?* — and
        the caller resolved everything it returned. It never asked the third, which
        `all_cleared` answers **eight lines below** and four other call sites ask: *is one of its
        alarms still on?* A situation holding an **active** alarm that nobody had touched for
        `IDLE_CLOSE_S` was therefore resolved out of every live view while it was still burning,
        and because a repeating trap increments an existing alarm's `count` rather than raising a
        new one, nothing the network did afterwards put it back. **The symptom of that defect was
        the absence of a symptom.**

        **The name is one word short of the contract and stays that way**, because renaming it
        would edit `engine/operate/engine.py` — the file that carries *"ingestion is sacred"* and
        whose bytes are pinned by `TRAP_PATH_HASHES`. #259 took the same trade for the same reason:
        the store answers the question the caller actually has, and the caller does not change.

        The other half of this population — idle **and** still active — is
        :meth:`idle_active_situations`. Between them they partition what this method used to
        return, which `test_lifecycle.py::test_the_sweep_partitions_the_idle_population` asserts on
        a fixture where both halves are non-empty.
        """
        cur = await self.conn.execute(
            # nosec B608 - `LIVE` and `HAS_ACTIVE` are module literals; `cutoff` is bound
            f"SELECT id FROM situation WHERE {LIVE} AND updated_at < ? AND NOT {HAS_ACTIVE}",
            (cutoff,),
        )
        return [int(r[0]) for r in await cur.fetchall()]

    async def idle_active_situations(self, cutoff: float) -> list[int]:
        """**Live, untouched since `cutoff`, and one of its alarms is still on** (v0.16.2, #274).

        The fact the idle sweep must not act on and the operator most needs to see: nobody has
        looked at this for an hour and something is still broken. It is **derived**, never stored —
        staleness is a function of `now`, so a column holding it would be a cached clock reading and
        wrong between every pair of sweeps.

        Two readers, one expression: `maintenance_loop` counts these into the operator warning, and
        `GET /api/situations` marks the rows it is already returning. A second expression of the
        same question would be a second chance to answer it differently, which is F46's lesson
        applied to a predicate rather than to a threshold.

        **Unscoped, and safe to be.** It returns ids without consulting visibility, exactly as
        :meth:`idle_open_situations` does; the route uses it only to annotate rows the caller has
        already been shown, so an id the caller may not see is never rendered and never counted for
        them.
        """
        cur = await self.conn.execute(
            # nosec B608 - `LIVE` and `HAS_ACTIVE` are module literals; `cutoff` is bound
            f"SELECT id FROM situation WHERE {LIVE} AND updated_at < ? AND {HAS_ACTIVE}",
            (cutoff,),
        )
        return [int(r[0]) for r in await cur.fetchall()]

    async def with_idle_active(
        self, rows: list[dict[str, Any]], cutoff: float
    ) -> list[dict[str, Any]]:
        """The same list rows, each carrying `stale` (v0.16.2, DECISIONS #274).

        **Here rather than in each handler**, for `project_situation_row`'s reason one layer up:
        `GET /api/situations` and the SSE stream both render the situations list, and the console
        refreshes one from the other. Two copies of the annotation is one copy too many — a card
        that gained a stale badge from the poll and lost it on the next stream frame would be worse
        than no badge, because it would read as the situation having changed.

        One query for the whole page rather than one per row: the population is *live, idle and
        still burning*, which is small on a healthy appliance and is exactly the number the operator
        wants when it is not.
        """
        stale = set(await self.idle_active_situations(cutoff))
        return [{**row, "stale": int(row["id"]) in stale} for row in rows]
