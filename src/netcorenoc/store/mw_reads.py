"""Reading maintenance windows: the list, one window, the preview, and the markers.

The projection half of :mod:`netcorenoc.store.maintenance_windows`, split on the seam this package
uses everywhere — what mutates, and what projects.

## The visibility rule, and where it is enforced

**A window's EXISTENCE is visible to every role; its DETAILS follow `visibility`.** That split is
prime directive 4 and it is not negotiable: a host that goes quiet with no marker reads as healthy,
which is exactly how maintenance windows hide outages.

It is enforced **in the query, never in the render** (prime directive 6). :meth:`window_markers`
returns what every role may see — that a window exists, when it ends, and how many targets it has
— and never the name, the description, the owner or the rules of a window whose visibility is
`editors`. A route that filtered a full row down in Python would be F35 again: v0.7.0 made a
display string an authorization key and it took two releases to find.

## No existence oracle

:meth:`preview_counts` answers *"if I create this, what does it cover?"*, which is precisely the
shape of a question that leaks inventory. It counts **only what the caller's scope already
permits**, so a window naming an element outside that scope contributes nothing to the count and
nothing to the error — the caller cannot tell an element they may not see from one that does not
exist. The same rule v0.16.5's bulk clear was designed around.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.store.base import StoreBase
from netcorenoc.store.types import MAX_SCOPE_PARAMS

#: The most windows one list call may return. D7 asks for 5/10/20; this is the ceiling a caller
#: cannot exceed, so a `limit` an agent invents cannot turn the list into an estate dump.
MAX_WINDOW_LIMIT = 200


class MaintenanceReadMixin(StoreBase):
    async def list_maintenance_windows(
        self,
        *,
        statuses: tuple[str, ...] = (),
        organization_id: int | None = None,
        ne_id: int | None = None,
        owner_ref: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """The upcoming list (D7) and every other filtered view of it, in one statement.

        Filters compose as an AND of the ones supplied, which is what an agent expects and what
        the console's chips do. `limit` is bounded here rather than trusted from the caller —
        F38's rule, one resource over: a `LIMIT` must bound the **filtered** set and must not be
        the caller's to widen.

        Ordered by `starts_at` so *"the next five"* is the first five rows, which is what D7 asks
        for and what makes the *"in 2 h 15 min"* column monotonic down the page.
        """
        where: list[str] = []
        args: list[Any] = []
        if statuses:
            where.append(f"w.status IN ({','.join('?' * len(statuses))})")
            args.extend(statuses)
        if organization_id is not None:
            where.append("w.organization_id = ?")
            args.append(organization_id)
        if owner_ref is not None:
            where.append("w.owner_ref = ?")
            args.append(owner_ref)
        if since is not None:
            where.append("(w.ends_at + w.patch_s) >= ?")
            args.append(since)
        if until is not None:
            where.append("(w.starts_at - w.patch_s) <= ?")
            args.append(until)
        if ne_id is not None:
            where.append(
                "EXISTS (SELECT 1 FROM maintenance_window_target t "
                "WHERE t.window_id = w.id AND t.ne_id = ?)"
            )
            args.append(ne_id)
        clause = f"WHERE {' AND '.join(where)} " if where else ""
        cur = await self.conn.execute(
            "SELECT w.*, (SELECT COUNT(*) FROM maintenance_window_target t "  # nosec B608
            "WHERE t.window_id = w.id) AS target_count, o.name AS organization_name "
            "FROM maintenance_window w JOIN organization o ON o.id = w.organization_id "
            f"{clause}ORDER BY w.starts_at, w.id LIMIT ? OFFSET ?",  # nosec B608 - placeholders
            (*args, max(1, min(limit, MAX_WINDOW_LIMIT)), max(0, offset)),
        )
        return [_row(row) for row in await cur.fetchall()]

    async def count_maintenance_windows(self, statuses: tuple[str, ...] = ()) -> int:
        """The total behind a page, so the console can say *"showing 5 of 23"* honestly."""
        if statuses:
            cur = await self.conn.execute(
                "SELECT COUNT(*) FROM maintenance_window "  # nosec B608 - placeholders only
                f"WHERE status IN ({','.join('?' * len(statuses))})",  # nosec B608
                statuses,
            )
        else:
            cur = await self.conn.execute("SELECT COUNT(*) FROM maintenance_window")
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def maintenance_window(self, window_id: int) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            "SELECT w.*, (SELECT COUNT(*) FROM maintenance_window_target t "
            "WHERE t.window_id = w.id) AS target_count, o.name AS organization_name "
            "FROM maintenance_window w JOIN organization o ON o.id = w.organization_id "
            "WHERE w.id = ?",
            (window_id,),
        )
        row = await cur.fetchone()
        return _row(row) if row is not None else None

    async def window_targets(self, window_id: int) -> list[dict[str, Any]]:
        """The window's elements, with the label an operator gave each one if there is one."""
        cur = await self.conn.execute(
            "SELECT t.ne_id, n.ip, "  # nosec B608 - one literal from `_label_join`
            "l.label AS label FROM maintenance_window_target t JOIN ne n ON n.id = t.ne_id "
            + self._label_join("l", "ne", "n.id")
            + "WHERE t.window_id = ? ORDER BY n.ip",
            (window_id,),
        )
        return [
            {
                "ne_id": int(row["ne_id"]),
                "address": str(row["ip"]),
                "label": row["label"],
            }
            for row in await cur.fetchall()
        ]

    async def window_rules(self, window_id: int) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT id, ne_id, kind, severity_rank, oid_root, match_on, slot_starts_at, "
            "slot_ends_at FROM maintenance_window_rule WHERE window_id = ? ORDER BY ne_id, id",
            (window_id,),
        )
        return [
            {
                "id": int(row["id"]),
                "ne_id": int(row["ne_id"]),
                "kind": str(row["kind"]),
                "severity_rank": row["severity_rank"],
                "oid_root": row["oid_root"],
                "match_on": row["match_on"],
                "slot_starts_at": row["slot_starts_at"],
                "slot_ends_at": row["slot_ends_at"],
            }
            for row in await cur.fetchall()
        ]

    async def window_markers(
        self, now: float, ne_ids: frozenset[int] | None = None
    ) -> dict[int, dict[str, Any]]:
        """`{ne_id: marker}` for every element under a window right now. **Every role sees this.**

        The marker carries no name, no description, no owner and no rules — only that a window is
        in force, its id, and when it is due to end. That is the whole of what prime directive 4
        requires every role to be told, and withholding the rest is what `visibility` means.

        Scoped in the WHERE clause when a scope is given, for F35/F38's reason: a marker on an
        element a viewer cannot see would answer *"does this exist?"*, which is the oracle the
        scope exists to close.
        """
        if not self._has_maintenance:
            # Pre-0020 schema: the feature's tables do not exist yet (`base.py::_has_maintenance`).
            return {}
        args: list[Any] = [now, now]
        clause = ""
        if ne_ids is not None:
            if not ne_ids:
                return {}
            if len(ne_ids) <= MAX_SCOPE_PARAMS:
                clause = f"AND t.ne_id IN ({','.join('?' * len(ne_ids))}) "
                args.extend(sorted(ne_ids))
        cur = await self.conn.execute(
            "SELECT t.ne_id, w.id, w.status, w.starts_at, w.ends_at, w.patch_s "  # nosec B608
            "FROM maintenance_window_target t JOIN maintenance_window w ON w.id = t.window_id "
            "WHERE w.status = 'active' AND (w.starts_at - w.patch_s) <= ? "
            f"AND (w.ends_at + w.patch_s) > ? {clause}"  # nosec B608 - placeholders only
            "ORDER BY w.ends_at",
            tuple(args),
        )
        out: dict[int, dict[str, Any]] = {}
        for row in await cur.fetchall():
            ne_id = int(row["ne_id"])
            # See MAX_SCOPE_PARAMS: an estate with more elements in one scope than SQLite will
            # bind is filtered here rather than having its id list truncated, which would answer a
            # different question quietly. The same choice `severity_census` makes.
            if ne_ids is not None and ne_id not in ne_ids:
                continue
            out.setdefault(
                ne_id,
                {
                    "window_id": int(row["id"]),
                    "status": str(row["status"]),
                    "starts_at": float(row["starts_at"]),
                    "ends_at": float(row["ends_at"]),
                    "ends_at_effective": float(row["ends_at"]) + float(row["patch_s"]),
                },
            )
        return out

    async def preview_counts(
        self, ne_ids: list[int], scope_ne_ids: frozenset[int] | None
    ) -> dict[str, int]:
        """*"How many devices, and how many active alarms would this affect?"* — **the dry run.**

        The most useful call an agent can make before creating a window, and the same code the
        form's live preview uses, so the number an operator reads and the number an agent reads
        cannot disagree.

        **No existence oracle** (Part III). `scope_ne_ids` is the caller's resolved visibility, and
        an element outside it is dropped *before* anything is counted — so a caller naming a host
        they may not see gets the same counts as one naming a host that does not exist. There is no
        error, no differing count and no timing difference to read the answer out of.
        """
        permitted = [
            ne_id for ne_id in sorted(set(ne_ids)) if scope_ne_ids is None or ne_id in scope_ne_ids
        ]
        if not permitted:
            return {"devices": 0, "active_alarms": 0}
        marks = ",".join("?" * len(permitted))
        cur = await self.conn.execute(
            "SELECT COUNT(DISTINCT a.ne_id) AS devices, COUNT(*) AS alarms "  # nosec B608
            f"FROM alarm a WHERE a.status='active' AND a.ne_id IN ({marks})",  # nosec B608
            tuple(permitted),
        )
        row = await cur.fetchone()
        return {
            "devices": len(permitted),
            "active_alarms": int(row["alarms"]) if row is not None else 0,
        }


def _row(row: Any) -> dict[str, Any]:
    """One stored window as the API's full shape. **The projection, not the redaction.**

    Every field a window has. What a caller is *allowed* to see is decided one layer up, in
    `api/routes/maintenance.py`, against `visibility` and the caller's role — and a caller who may
    not see the details is never handed this dict to have fields removed from it. They are served
    from `window_markers`, which is a different query returning different columns.
    """
    return {
        "id": int(row["id"]),
        "name": str(row["name"]),
        "description": str(row["description"]),
        "organization_id": int(row["organization_id"]),
        "organization_name": str(row["organization_name"]),
        "status": str(row["status"]),
        "tz": str(row["tz"]),
        "starts_at": float(row["starts_at"]),
        "ends_at": float(row["ends_at"]),
        "all_day": bool(row["all_day"]),
        "patch_s": float(row["patch_s"]),
        "ledger_enabled": bool(row["ledger_enabled"]),
        "visibility": str(row["visibility"]),
        "owner_ref": str(row["owner_ref"]),
        "owner_role": str(row["owner_role"]),
        "created_by_agent": bool(row["created_by_agent"]),
        "needs_confirmation": bool(row["needs_confirmation"]),
        "confirmed_at": row["confirmed_at"],
        "confirmed_by": row["confirmed_by"],
        "created_at": float(row["created_at"]),
        "updated_at": float(row["updated_at"]),
        "cancelled_at": row["cancelled_at"],
        "ended_at": row["ended_at"],
        "target_count": int(row["target_count"]),
    }
