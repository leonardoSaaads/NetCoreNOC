"""What each live case last heard of its situation's alarms (ADR #474).

The case loop asks, every half minute and on a reader: which alarms each live case's situation holds
now, and which of them the case heard as active last time. `engine/noc/hearing.py` decides what
changed; under the lock the loop tells the case and writes down only what moved.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from netcorenoc.ingest import known_oids
from netcorenoc.store.base import StoreBase

#: The statuses a case listens in: past dispatch, before it is over.
_DEAF = ("new", "auto", "resolved", "closed")


class NocHearingMixin(StoreBase):
    async def noc_case_alarm_states(self, limit: int = 500) -> dict[int, list[dict[str, Any]]]:
        """``{situation_id: [alarm…]}`` for every live case past dispatch: each alarm's id, whether
        it is active or flapping, how many times it has raised (its count), and what names it in a
        sentence — its class, its element (the name an operator gave it, else its address) and its
        port (the trap's interface name, else its instance)."""
        cur = await self.conn.execute(
            # nosec B608 - `_label_join` returns a fixed literal; every value here is bound.
            "SELECT c.situation_id, a.id, a.status, a.count, a.is_flapping, a.instance, "  # nosec B608
            "a.class_id, a.varbinds, n.ip, l.label "
            "FROM (SELECT situation_id FROM noc_case WHERE status NOT IN (?, ?, ?, ?) "
            "ORDER BY situation_id LIMIT ?) c "
            "JOIN situation_alarm sa ON sa.situation_id = c.situation_id "
            "JOIN alarm a ON a.id = sa.alarm_id JOIN ne n ON n.id = a.ne_id "
            + self._label_join("l", "ne", "n.id")
            + "ORDER BY c.situation_id, a.id",
            (*_DEAF, limit),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        names = await self._class_names([int(r["class_id"]) for r in rows])  # type: ignore[attr-defined]
        out: dict[int, list[dict[str, Any]]] = {}
        for r in rows:
            out.setdefault(int(r["situation_id"]), []).append(
                {
                    "alarm_id": int(r["id"]),
                    "active": r["status"] == "active",
                    "raised": int(r["count"]),
                    "flapping": bool(r["is_flapping"]),
                    "what": names.get(int(r["class_id"]), {}).get("class") or "an alarm",
                    "element": r["label"] or r["ip"],
                    "instance": known_oids.port_name(r["instance"], r["varbinds"]) or r["instance"],
                }
            )
        return out

    async def noc_case_heard(
        self, situation_ids: Iterable[int]
    ) -> dict[int, dict[int, tuple[bool, int]]]:
        """``{situation_id: {alarm_id: (heard active, raised count)}}`` — what each case heard
        last time."""
        out: dict[int, dict[int, tuple[bool, int]]] = {int(i): {} for i in situation_ids}
        if not out:
            return out
        cur = await self.conn.execute(
            "SELECT situation_id, alarm_id, active, raised FROM noc_case_alarm "
            "WHERE situation_id = ANY(?)",
            (list(out),),
        )
        for row in await cur.fetchall():
            out[int(row[0])][int(row[1])] = (bool(row[2]), int(row[3]))
        return out

    async def noc_case_hear(
        self, situation_id: int, moved: Sequence[tuple[int, bool, int]]
    ) -> None:
        """Write down the alarms whose state or count moved since the case last heard them."""
        for alarm_id, active, raised in moved:
            await self.conn.execute(
                "INSERT INTO noc_case_alarm (situation_id, alarm_id, active, raised) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (situation_id, alarm_id) "
                "DO UPDATE SET active = EXCLUDED.active, raised = EXCLUDED.raised",
                (situation_id, int(alarm_id), bool(active), int(raised)),
            )
