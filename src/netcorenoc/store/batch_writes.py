"""The two writes the engine's batch makes per activation, in the shape a batch can afford.

ADR #448. `situations.py` is at the module guard, and these are not new decisions: they are
`add_alarm_to_situation` and `add_link` with the per-call cost taken out.

* :meth:`BatchWriteMixin.insert_member` is the membership insert **without** the derived-name
  refresh. The caller owns the refresh: the engine records the situation and refreshes each one
  once, before the batch commits (`engine/operate/upkeep.py`). Every other writer of membership
  keeps `add_alarm_to_situation`, which refreshes in the same statement group (DECISIONS #257).
* :meth:`BatchWriteMixin.add_links` is `add_link` for every kept link of one activation, as one
  `executemany` — one hand-off to the connection's thread instead of up to
  `correlate.MAX_LINKS_PER_ALARM`. Same rows, same order, so the same ids.

**No method here takes ``store.lock``** — the caller holds it
(`tests/store/test_store_concurrency.py`).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

from netcorenoc.store.base import StoreBase

__all__ = ["BatchWriteMixin", "LinkRow"]


class LinkRow(NamedTuple):
    """One accepted link, in `link`'s column order. ``terms`` is a trained model's explanation."""

    situation_id: int
    alarm_a: int
    alarm_b: int
    score: float
    term_t: float
    term_a: float
    term_e: float
    created_at: float
    terms: str | None


class BatchWriteMixin(StoreBase):
    async def insert_member(self, situation_id: int, alarm_id: int) -> bool:
        """Add one member; True if it was not one already. **The name is the caller's to refresh**
        before the transaction commits (see the module docstring)."""
        cur = await self.conn.execute(
            "INSERT OR IGNORE INTO situation_alarm (situation_id, alarm_id) VALUES (?, ?) "
            "RETURNING alarm_id",
            (situation_id, alarm_id),
        )
        return await cur.fetchone() is not None

    async def add_links(self, rows: Sequence[LinkRow]) -> None:
        """Every row, in order, as one statement. Nothing to do for an empty sequence."""
        if not rows:
            return
        if self._has_link_terms:
            await self.conn.executemany(
                "INSERT INTO link (situation_id, alarm_a, alarm_b, score, term_t, term_a, term_e, "
                "created_at, terms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            return
        await self.conn.executemany(
            "INSERT INTO link (situation_id, alarm_a, alarm_b, score, term_t, term_a, term_e, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [row[:8] for row in rows],
        )
