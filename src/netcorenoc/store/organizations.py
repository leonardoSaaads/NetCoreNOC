"""Organizations: which provider an element or a window belongs to (v0.21.0, D1).

⚠ **Attribution, not isolation.** `migrations/0020_organization.sql` carries the banner in full and
`crosscutting/shaping/scope.py` has carried the same warning about visibility scoping since
v0.7.0. Restated here because this is the module somebody reads when they are about to use it for
something it does not do: **correlation still learns across every network element, and a situation
may still form across an organization boundary.** No method here is consulted by any authorization
decision, and none ever should be — that is what `crosscutting/shaping/scope.py` is for, and ADR
#367 records why the two stay separate mechanisms rather than one.

The appliance discovers network elements continuously, from the trap stream. An element that
arrives after an admin has created three organizations lands in the **default** one, because the
alternative is an element belonging to nothing — invisible on every filtered list, which is the
failure mode an attribution feature must not create.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.store.base import StoreBase

#: The longest an organization name may be. The API validator enforces it too; this is the
#: storage-side bound, and the two are asserted equal by `tests/test_maintenance_api.py`.
MAX_ORGANIZATION_NAME = 120


class OrganizationMixin(StoreBase):
    async def list_organizations(self) -> list[dict[str, Any]]:
        """Every organization, with how many elements it holds. Ordered default-first, then name.

        The element count is what makes the list actionable — an admin about to rename or retire
        an organization needs to know what is in it — and it is a correlated subquery over an
        indexed column rather than a join, so an appliance with one organization pays one scan of
        a one-row table.

        **The count coalesces NULL to the default, and that is not cosmetic.** An element
        discovered from the trap stream has `organization_id` NULL until the next maintenance
        sweep attributes it (see :meth:`attribute_unassigned_nes` for why discovery does not write
        it). Counting only the non-NULL rows made a freshly-installed appliance report *"Default
        organization — 0 elements"* while it was ingesting from forty of them, which is the
        attribution feature failing at the one moment an operator is looking at it. The coalesce
        is the same expression :meth:`ne_organizations` uses, so the list and the per-element
        lookup cannot disagree.
        """
        if not self._has_maintenance:
            # Pre-0020 schema: the feature's tables do not exist yet (`base.py::_has_maintenance`).
            return []
        cur = await self.conn.execute(
            "SELECT o.id, o.name, o.slug, o.is_default, o.created_at, "
            "(SELECT COUNT(*) FROM ne WHERE COALESCE(ne.organization_id, "
            "(SELECT id FROM organization WHERE is_default=1 ORDER BY id LIMIT 1)) = o.id) "
            "AS ne_count "
            "FROM organization o ORDER BY o.is_default DESC, o.name"
        )
        return [
            {
                "id": int(row["id"]),
                "name": str(row["name"]),
                "slug": str(row["slug"]),
                "is_default": bool(row["is_default"]),
                "created_at": float(row["created_at"]),
                "ne_count": int(row["ne_count"]),
            }
            for row in await cur.fetchall()
        ]

    async def default_organization_id(self) -> int:
        """The organization a newly-discovered element belongs to.

        `0020` creates exactly one row with `is_default = 1` and nothing deletes it, so the
        fallback below is unreachable on any migrated database. It is written anyway because the
        alternative is a `None` propagating into `create_maintenance_window`'s foreign key, and
        returning 1 — the id `0020` created — degrades to the right answer instead.

        **No `_has_maintenance` probe, unlike its neighbours, and that is the rule rather than an
        omission.** The probe guards the methods that run *unbidden* — the sweep's
        :meth:`attribute_unassigned_nes` and the reads the ingest path makes — because
        `tests/test_upgrade.py` drives this store against migration directories frozen below
        schema 20. This one is reached only from a route, and a route only exists on an appliance
        whose migrations have run. (An earlier draft of this docstring called it *"the
        element-discovery path"*, which it is not: discovery leaves `organization_id` NULL and the
        sweep attributes it, which is the whole point of the method below.)
        """
        cur = await self.conn.execute(
            "SELECT id FROM organization WHERE is_default=1 ORDER BY id LIMIT 1"
        )
        row = await cur.fetchone()
        return int(row[0]) if row is not None else 1

    async def organization_exists(self, organization_id: int) -> bool:
        cur = await self.conn.execute("SELECT 1 FROM organization WHERE id=?", (organization_id,))
        return await cur.fetchone() is not None

    async def create_organization(self, name: str, slug: str, now: float) -> int:
        """Add an organization. The caller owns the transaction, as every write path here does."""
        cur = await self.conn.execute(
            "INSERT INTO organization (name, slug, created_at, is_default) VALUES (?, ?, ?, 0) "
            "RETURNING id",
            (name[:MAX_ORGANIZATION_NAME], slug, now),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def set_ne_organization(self, ne_id: int, organization_id: int) -> None:
        """Move one element to an organization. **Attribution only** — see the module docstring."""
        await self.conn.execute(
            "UPDATE ne SET organization_id=? WHERE id=?", (organization_id, ne_id)
        )

    async def attribute_unassigned_nes(self) -> int:
        """Put every element with no organization into the default one; return how many moved.

        Called from the maintenance sweep rather than from the trap path. An element is created by
        `store.ne_id()` on the first trap from an address, which is inside the batch lock on the
        ingest path — and adding a second write there to look up a default would be exactly the
        I/O prime directive 2 keeps off it. So discovery leaves `organization_id` NULL and the
        sweep attributes it within five seconds, which is invisible to an operator and free to the
        hot path.

        Every read model coalesces NULL to the default anyway, so the window between discovery and
        attribution is not a window in which an element is missing from a filtered list.
        """
        if not self._has_maintenance:
            # Pre-0020 schema: the feature's tables do not exist yet (`base.py::_has_maintenance`).
            return 0
        cur = await self.conn.execute(
            "UPDATE ne SET organization_id=(SELECT id FROM organization WHERE is_default=1 "
            "ORDER BY id LIMIT 1) WHERE organization_id IS NULL"
        )
        return int(cur.rowcount or 0)

    async def ne_organizations(self, ne_ids: list[int]) -> dict[int, int]:
        """`{ne_id: organization_id}` for the elements named, coalescing NULL to the default.

        Bounded by the caller: every call site passes a target list an operator typed or a
        situation's own membership, both of which the API already bounds.
        """
        if not ne_ids:
            return {}
        marks = ",".join("?" * len(ne_ids))
        cur = await self.conn.execute(
            "SELECT n.id, COALESCE(n.organization_id, "  # nosec B608 - placeholders only
            "(SELECT id FROM organization WHERE is_default=1 ORDER BY id LIMIT 1)) AS org "
            f"FROM ne n WHERE n.id IN ({marks})",  # nosec B608 - placeholders only
            tuple(sorted(ne_ids)),
        )
        return {int(row["id"]): int(row["org"]) for row in await cur.fetchall()}
