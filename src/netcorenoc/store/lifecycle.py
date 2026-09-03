"""Connection lifecycle: open, migrate, integrity-check, commit, rollback, close.

The one module that owns ``self._conn`` itself rather than reading it through
:attr:`~netcorenoc.store.base.StoreBase.conn`. Migrations are plain SQL applied at startup via
``PRAGMA user_version``; the integrity checks are non-fatal by design (F11) — a NOC trap sink must
keep ingesting even with a partly-damaged history DB.
"""

from __future__ import annotations

import logging

import aiosqlite

from netcorenoc.store.base import StoreBase
from netcorenoc.store.types import MIGRATIONS_DIR

# `MIGRATIONS_DIR` is re-exported deliberately. It is *defined* in `types.py`, but the code that
# reads it — `_migrate` and `latest_schema_version` — lives here, so this module's namespace is the
# binding that matters: a caller substituting the directory (as `tests/test_upgrade.py` does, to
# replay a schema frozen at an older version) must patch it here. Naming it in `__all__` makes that
# a supported seam rather than an accident of import order.
log = logging.getLogger("netcorenoc")

__all__ = ["MIGRATIONS_DIR", "LifecycleMixin"]


class LifecycleMixin(StoreBase):
    async def open(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._migrate()
        await self._probe_schema()
        await self._check_integrity()

    async def _probe_schema(self) -> None:
        """Which optional columns this database actually has (v0.16.0).

        One `PRAGMA` after the migrations, so the answer is about the schema this process will
        run against rather than about the one it shipped with. `tests/test_upgrade.py` runs the
        **current** store against a migration directory frozen at an older version — which is what
        makes "the migration changes behaviour and the code does not" checkable — so every write
        path that names a v0.16.0 column has to know whether the column is there.

        Stated as a probe rather than learned from a caught `OperationalError`: a close, a merge
        and every situation this release creates all touch these columns, so the exception form
        would pay a caught error per process per path and would infer the schema from a failure
        instead of asking.
        """
        cur = await self.conn.execute("PRAGMA table_info(situation)")
        columns = {str(row[1]) for row in await cur.fetchall()}
        self._has_lifecycle = "resolution" in columns

    async def _migrate(self) -> None:
        """Apply the pending scripts, and **say which** (DECISIONS #227).

        `install.md` tells an operator that migrations are "forward-only, idempotent, and applied
        at startup — there is no separate migration step". Measured on a first boot against an
        empty database, the appliance applied all thirteen and logged nothing at all, so the one
        thing an upgrade wants to see — *did the schema move, and how far* — was unobservable.
        One line per applied script, and one summary line, both at `info`; a boot that applies
        nothing says so at `debug` rather than adding noise to every restart.
        """
        cur = await self.conn.execute("PRAGMA user_version")
        row = await cur.fetchone()
        current = int(row[0]) if row else 0
        applied: list[str] = []
        for script in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = int(script.name.split("_", 1)[0])
            if version > current:
                log.info("applying migration %s", script.name)
                await self.conn.executescript(script.read_text())
                await self.conn.execute(f"PRAGMA user_version={version}")
                applied.append(script.name)
        await self.conn.commit()
        if applied:
            log.info(
                "schema %d -> %d (%d migration(s) applied)",
                current,
                await self.schema_version(),
                len(applied),
            )
        else:
            log.debug("schema %d, no migrations pending", current)

    async def _check_integrity(self) -> None:
        """Startup integrity/FK check (F11). Records a warning on damage; never crashes."""
        cur = await self.conn.execute("PRAGMA integrity_check")
        row = await cur.fetchone()
        if row is not None and str(row[0]).lower() != "ok":
            self.integrity_warnings.append(
                "Database integrity_check reported damage. Restore from a backup and run "
                "`netcorenoc audit verify`; new traps are still being ingested."
            )
        cur = await self.conn.execute("PRAGMA foreign_key_check")
        orphans = list(await cur.fetchall())
        if orphans:
            self.integrity_warnings.append(
                f"Database foreign_key_check found {len(orphans)} orphaned row(s); "
                "history may be inconsistent. A backup/restore is recommended."
            )

    @staticmethod
    def latest_schema_version() -> int:
        """The highest migration number on disk (the schema version a healthy DB reaches)."""
        return max((int(p.name.split("_", 1)[0]) for p in MIGRATIONS_DIR.glob("*.sql")), default=0)

    async def schema_version(self) -> int:
        cur = await self.conn.execute("PRAGMA user_version")
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.commit()
            await self._conn.close()
            self._conn = None

    async def commit(self) -> None:
        await self.conn.commit()

    async def rollback(self) -> None:
        """Abandon the current transaction (F11). The audit chain only advances on commit, so a
        rolled-back batch never breaks it — the dropped traps are recorded as an ingest loss."""
        await self.conn.rollback()
