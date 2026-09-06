"""The v0.7.0 governance policies, and the scope projections that read alongside them.

Same discipline as ``scorer_config``: an append-only history plus a pointer, so a change is
auditable and reversible and history is tamper-evident. One row holds the WHOLE policy for its kind
as canonical JSON, because a policy is read and applied as a unit on every request.

Read HTTP-side, per request, and nowhere else. **The trap path never touches these tables.**
"""

from __future__ import annotations

from typing import Any

from netcorenoc.store.base import StoreBase
from netcorenoc.store.situations import LIVE


class GovernanceMixin(StoreBase):
    async def active_governance_ids(self) -> dict[str, int]:
        """``{kind: policy_id}`` for the kinds that have an active policy (at most two rows).

        The per-request read. It is deliberately id-only: the API re-parses a document only when
        its id differs from the one it is holding, so a change lands on the very next request while
        the parse cost is paid once per policy version. An empty result means "no governance
        configured", which resolves to the compiled ceiling and full visibility — v0.6.0 exactly.
        """
        cur = await self.conn.execute("SELECT kind, policy_id FROM governance_active")
        return {str(r["kind"]): int(r["policy_id"]) for r in await cur.fetchall()}

    async def get_governance_policy(self, policy_id: int) -> dict[str, Any] | None:
        cur = await self.conn.execute("SELECT * FROM governance_policy WHERE id=?", (policy_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list_governance_policies(self, kind: str, limit: int) -> list[dict[str, Any]]:
        """The immutable history for one kind, newest first, with the active one flagged."""
        cur = await self.conn.execute(
            "SELECT p.*, (a.policy_id IS NOT NULL) AS active FROM governance_policy p "
            "LEFT JOIN governance_active a ON a.policy_id = p.id AND a.kind = p.kind "
            "WHERE p.kind=? ORDER BY p.id DESC LIMIT ?",
            (kind, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def insert_governance_policy(
        self,
        kind: str,
        document: str,
        doc_hash: str,
        created_by: str | None,
        created_at: float,
        note: str,
    ) -> int:
        """Append one immutable policy row. Never UPDATE, never DELETE (triggers abort)."""
        cur = await self.conn.execute(
            "INSERT INTO governance_policy (kind, document, doc_hash, created_by, created_at, "
            "note) VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (kind, document, doc_hash, created_by, created_at, note),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def set_active_governance_policy(
        self, kind: str, policy_id: int, activated_by: str | None, ts: float
    ) -> bool:
        """Point `kind`'s active row at a policy. Apply and rollback are this one call — history is
        immutable, so reverting is moving the pointer, never editing or deleting."""
        cur = await self.conn.execute(
            "SELECT 1 FROM governance_policy WHERE id=? AND kind=?", (policy_id, kind)
        )
        if await cur.fetchone() is None:
            return False
        await self.conn.execute(
            "INSERT INTO governance_active (kind, policy_id, activated_by, activated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (kind) DO UPDATE SET policy_id=excluded.policy_id, "
            "activated_by=excluded.activated_by, activated_at=excluded.activated_at",
            (kind, policy_id, activated_by, ts),
        )
        return True

    async def clear_active_governance_policy(self, kind: str) -> bool:
        """Remove `kind`'s pointer: back to the shipped baseline (ceiling / full visibility).

        This is the recovery path, and it is why the pointer table is not append-only. The history
        rows survive, so what was once active remains answerable.
        """
        cur = await self.conn.execute("DELETE FROM governance_active WHERE kind=?", (kind,))
        return bool(cur.rowcount)

    async def scoped_stats(self, ne_ids: frozenset[int], ips: frozenset[str]) -> dict[str, int]:
        """:meth:`stats` computed over one principal's visible NEs only.

        Every counter here is an enumeration of something an out-of-scope NE could contribute to,
        so leaving any of them global would hand a scoped viewer a volume oracle: a rising
        `active_alarms` with nothing visible to explain it says "a storm is happening somewhere you
        cannot see", and a rising `classes` says "a device you cannot see just emitted a trap type
        nobody has emitted before". `quarantined` is filtered by source address for the same
        reason. Situations count when they have at least one visible member, matching the listing
        rule exactly (DECISIONS #59).
        """
        if not ne_ids:
            return {
                "devices": 0,
                "classes": 0,
                "active_alarms": 0,
                "open_situations": 0,
                "new_situations": 0,
                "working_situations": 0,
                "quarantined": 0,
            }
        ne_marks = ",".join("?" * len(ne_ids))
        ne_args = tuple(sorted(ne_ids))
        out: dict[str, int] = {"devices": len(ne_ids)}
        for name, sql in (
            (
                "classes",
                f"SELECT COUNT(DISTINCT class_id) FROM alarm WHERE ne_id IN ({ne_marks})",  # nosec B608 - placeholders only
            ),
            (
                "active_alarms",
                f"SELECT COUNT(*) FROM alarm WHERE status='active' AND ne_id IN ({ne_marks})",  # nosec B608 - placeholders only
            ),
            (
                "open_situations",
                "SELECT COUNT(DISTINCT s.id) FROM situation s "
                "JOIN situation_alarm sa ON sa.situation_id=s.id "
                "JOIN alarm a ON a.id=sa.alarm_id "
                # v0.16.0: `new` is live too, so a scoped operator's count means what it always
                # meant — situations that have not left — rather than only the triaged ones.
                f"WHERE s.{LIVE} AND a.ne_id IN ({ne_marks})",  # nosec B608 - placeholders only
            ),
            # v0.16.4: the same population split by status, and scoped by the same rule — a
            # situation counts when it has at least one visible member. Leaving these global while
            # `open_situations` beside them is scoped would be the volume oracle this docstring
            # describes, reintroduced one key along.
            (
                "new_situations",
                "SELECT COUNT(DISTINCT s.id) FROM situation s "
                "JOIN situation_alarm sa ON sa.situation_id=s.id "
                "JOIN alarm a ON a.id=sa.alarm_id "
                f"WHERE s.status='new' AND a.ne_id IN ({ne_marks})",  # nosec B608 - placeholders only
            ),
            (
                "working_situations",
                "SELECT COUNT(DISTINCT s.id) FROM situation s "
                "JOIN situation_alarm sa ON sa.situation_id=s.id "
                "JOIN alarm a ON a.id=sa.alarm_id "
                f"WHERE s.status='open' AND a.ne_id IN ({ne_marks})",  # nosec B608 - placeholders only
            ),
        ):
            cur = await self.conn.execute(sql, ne_args)
            row = await cur.fetchone()
            assert row is not None
            out[name] = int(row[0])
        if ips:
            ip_marks = ",".join("?" * len(ips))
            cur = await self.conn.execute(
                f"SELECT COUNT(*) FROM quarantine WHERE source IN ({ip_marks})",  # nosec B608 - placeholders only
                tuple(sorted(ips)),
            )
            row = await cur.fetchone()
            assert row is not None
            out["quarantined"] = int(row[0])
        else:
            out["quarantined"] = 0
        return out

    async def situation_member_nes(self, situation_ids: list[int]) -> dict[int, list[int | None]]:
        """``{situation_id: [ne_id per member alarm]}`` — the scope filter's input for a listing.

        One query for the whole page rather than one per situation. A **list**, not a set, because
        the caller needs both "is any member visible?" (whether to list it at all) and "how many
        members are visible versus redacted?" (the honest counts), and a set would lose the second.
        """
        if not situation_ids:
            return {}
        marks = ",".join("?" * len(situation_ids))
        cur = await self.conn.execute(
            "SELECT sa.situation_id, a.ne_id FROM situation_alarm sa "
            f"JOIN alarm a ON a.id=sa.alarm_id WHERE sa.situation_id IN ({marks})",  # nosec B608 - placeholders only
            tuple(situation_ids),
        )
        out: dict[int, list[int | None]] = {}
        for row in await cur.fetchall():
            ne_id = int(row["ne_id"]) if row["ne_id"] is not None else None
            out.setdefault(int(row["situation_id"]), []).append(ne_id)
        return out

    async def situation_member_ne(self, situation_id: int) -> dict[int, int | None]:
        """``{alarm_id: ne_id}`` for one situation's members — which members a reader may see."""
        cur = await self.conn.execute(
            "SELECT sa.alarm_id, a.ne_id FROM situation_alarm sa "
            "JOIN alarm a ON a.id=sa.alarm_id WHERE sa.situation_id=?",
            (situation_id,),
        )
        return {
            int(r["alarm_id"]): (int(r["ne_id"]) if r["ne_id"] is not None else None)
            for r in await cur.fetchall()
        }

    async def list_ne_for_scope(self) -> list[dict[str, Any]]:
        """Every NE as ``(id, ip)`` — the input to resolving scope selectors to NE ids.

        **v0.7.1 (F35): the operator label is deliberately NOT selected here.** v0.7.0 joined it in
        so a glob selector could match a labelled estate, which made `POST /api/labels` — an
        `editor` route — a write path into the authorization decision that constrains editors. The
        column is gone rather than guarded, because a guard protects only the write paths it sits
        on and this one must survive the next one (DECISIONS #66). Everything this query returns is
        engine-written: `ne.id` and `ne.ip` are created from the trap stream and are not writable
        through any API route.

        Called only when a scope policy is active and the caller is not an admin, so an
        un-configured appliance runs the v0.6.0 read paths untouched.
        """
        cur = await self.conn.execute("SELECT n.id, n.ip FROM ne n ORDER BY n.id")
        return [dict(r) for r in await cur.fetchall()]
