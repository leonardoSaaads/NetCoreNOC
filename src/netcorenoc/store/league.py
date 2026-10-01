"""The league's decisions and an admin's pin (v0.27.0, migration `0027`, ADRs #423, #425).

SQL only; decides nothing. `league_decision` is append-only (two triggers enforce it): the newest
row is the champion the fast loop runs. **No method here takes ``store.lock``.**
"""

from __future__ import annotations

import json
from typing import Any

from netcorenoc.store.base import StoreBase

__all__ = ["LeagueMixin"]


class LeagueMixin(StoreBase):
    async def latest_league_decision(self) -> dict[str, Any] | None:
        if not self._has_league:
            return None
        cur = await self.conn.execute(
            "SELECT id, at, champion, previous, actor, reason, pinned FROM league_decision "
            "ORDER BY id DESC LIMIT 1"
        )
        row = await cur.fetchone()
        return None if row is None else dict(row)

    async def add_league_decision(
        self,
        *,
        at: float,
        champion: str,
        previous: str | None,
        actor: str,
        reason: str,
        pinned: bool,
        evidence: dict[str, Any],
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO league_decision (at, champion, previous, actor, reason, pinned, evidence) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (
                at,
                champion,
                previous,
                actor,
                reason,
                int(pinned),
                json.dumps(evidence, sort_keys=True, separators=(",", ":"), default=str),
            ),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def league_decisions(self, limit: int = 20) -> list[dict[str, Any]]:
        """The champion's history, newest first, without the (large) evidence column."""
        if not self._has_league:
            return []
        cur = await self.conn.execute(
            "SELECT id, at, champion, previous, actor, reason, pinned FROM league_decision "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def newest_site_model(self) -> dict[str, Any] | None:
        """The newest model this appliance fitted on its own labels (the in-product search's
        output, ADR #413): a `gam` row in `model_version`. The league adds it as a member."""
        cur = await self.conn.execute(
            "SELECT id, kind, params_document, created_at FROM model_version WHERE kind='gam' "
            "ORDER BY id DESC LIMIT 1"
        )
        row = await cur.fetchone()
        return None if row is None else dict(row)

    async def decider_pin(self) -> str | None:
        """The member an admin pinned, or ``None`` when the judge chooses."""
        if not self._has_league:
            return None
        cur = await self.conn.execute("SELECT pinned FROM decider_setting ORDER BY id DESC LIMIT 1")
        row = await cur.fetchone()
        return None if row is None or row[0] is None else str(row[0])

    async def set_decider_pin(self, pinned: str | None, set_by: str, ts: float, reason: str) -> int:
        """Append a setting row: ``pinned`` names a member, ``None`` hands the choice back."""
        cur = await self.conn.execute(
            "INSERT INTO decider_setting (mode, set_by, set_at, reason, pinned) "
            "VALUES ('shipped', ?, ?, ?, ?) RETURNING id",
            (set_by, ts, reason, pinned),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])
