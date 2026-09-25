"""Acknowledgements that change what is SHOWN, never what is known (v0.22.0, ADR #387, #388).

Two operator acts share this module because they share a rule: each quiets a signal on screen and
neither alters a fact, a grouping, or anything a model trains on.

* **A warning snooze** (`notice_snooze`, item 1) — per user, for an interval, keyed on the warning's
  text so a changed warning returns.
* **An acknowledgement of a fault that outlived a maintenance window** (`alarm.surfaced_ack_*`,
  item 8) — the alarm stays active and stays in its situation; only the marker that says *"this
  outlived planned work"* stops being drawn, because someone has seen it.

Both are audited by the routes that call them. Neither is in any `dataset_*` table.
"""

from __future__ import annotations

import hashlib
from typing import Any

from netcorenoc.store.base import StoreBase

#: The snooze intervals, in seconds; `change` has none — it lasts until the text changes.
SNOOZE_SECONDS: dict[str, float | None] = {
    "24h": 24 * 3600.0,
    "7d": 7 * 24 * 3600.0,
    "change": None,
}


def notice_digest(text: str) -> str:
    """The key a snooze is stored under: the warning's exact text, hashed."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


class AttentionMixin(StoreBase):
    async def snoozes_for(self, user_id: int, now: float) -> dict[str, dict[str, Any]]:
        """`{digest: snooze}` for this user's snoozes that have not expired."""
        if not self._has_notice_snooze:
            return {}
        cur = await self.conn.execute(
            "SELECT digest, text, security, mode, until, created_at FROM notice_snooze "
            "WHERE user_id=? AND (until IS NULL OR until > ?)",
            (user_id, now),
        )
        return {str(r["digest"]): dict(r) for r in await cur.fetchall()}

    async def snooze_notice(
        self, *, user_id: int, text: str, security: bool, mode: str, now: float
    ) -> dict[str, Any]:
        """Snooze one warning for one user. Replaces an earlier snooze of the same text."""
        seconds = SNOOZE_SECONDS[mode]
        until = None if seconds is None else now + seconds
        digest = notice_digest(text)
        await self.conn.execute(
            "INSERT INTO notice_snooze (user_id, digest, text, security, mode, until, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (user_id, digest) DO UPDATE SET "
            "mode=excluded.mode, until=excluded.until, created_at=excluded.created_at",
            (user_id, digest, text, int(security), mode, until, now),
        )
        return {"digest": digest, "mode": mode, "until": until}

    async def unsnooze_notice(self, user_id: int, digest: str) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM notice_snooze WHERE user_id=? AND digest=?", (user_id, digest)
        )
        return bool(cur.rowcount)

    async def security_snoozes(self, now: float) -> list[dict[str, Any]]:
        """Every live snooze of a security warning, by anyone: what an admin must be able to see."""
        if not self._has_notice_snooze:
            return []
        cur = await self.conn.execute(
            "SELECT u.username, s.text, s.mode, s.until FROM notice_snooze s "
            "JOIN user u ON u.id=s.user_id WHERE s.security=1 AND (s.until IS NULL OR s.until > ?) "
            "ORDER BY s.until",
            (now,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def acknowledge_outlived(self, alarm_id: int, actor: str | None, now: float) -> bool:
        """Stop drawing the "outlived a window" marker on one alarm. False if it had none."""
        if not self._has_surfaced_ack:
            return False
        cur = await self.conn.execute(
            "UPDATE alarm SET surfaced_ack_at=?, surfaced_ack_by=? WHERE id=? "
            "AND surfaced_from_window_id IS NOT NULL AND surfaced_ack_at IS NULL RETURNING id",
            (now, actor, alarm_id),
        )
        return await cur.fetchone() is not None
