"""Connection lifecycle: open, migrate, check, commit, rollback, close (ADR #452).

The one module that opens and closes the database itself rather than reading it through
:attr:`~netcorenoc.store.base.StoreBase.conn`. Migrations are plain SQL files applied at startup,
each in **its own transaction** — PostgreSQL's DDL is transactional, so a script that fails leaves
the schema exactly as it was — under an advisory lock, so two appliances starting against one
database migrate it once. ``schema_migrations`` records each script's version, name and checksum.

The checks are non-fatal by design (F11): a NOC trap sink must keep ingesting, so a database newer
than this release or a migration edited after it was applied is a warning on the console, never a
crash. A second appliance against the same schema is the one refusal: two engines writing one
history would corrupt it, so ``open()`` takes a session advisory lock and fails without it.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
from collections.abc import AsyncIterator

from netcorenoc.store.base import StoreBase
from netcorenoc.store.types import MIGRATIONS_DIR

# `MIGRATIONS_DIR` is re-exported deliberately. It is *defined* in `types.py`, but the code that
# reads it — `_migrate` and `latest_schema_version` — lives here, so this module's namespace is the
# binding a caller substituting the directory must patch. Naming it in `__all__` makes that a
# supported seam rather than an accident of import order.
log = logging.getLogger("netcorenoc")

__all__ = ["MIGRATIONS_DIR", "MINIMUM_SERVER", "LifecycleMixin", "MissingSchemaError"]

#: PostgreSQL 15: `UNIQUE NULLS NOT DISTINCT`, and the SQL-standard function bodies of `0031`.
MINIMUM_SERVER = 150000


class MissingSchemaError(RuntimeError):
    """The schema is not there, and the store was opened not to create it (the CLI's tools)."""


def _lock_key(schema: str, purpose: str) -> int:
    """A stable signed 64-bit advisory-lock key for one schema and one purpose."""
    digest = hashlib.sha256(f"netcorenoc:{purpose}:{schema}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


class LifecycleMixin(StoreBase):
    async def open(self) -> None:
        await self._db.open()
        raw = self._db.writer_raw()
        server = int(await raw.fetchval("SHOW server_version_num"))
        if server < MINIMUM_SERVER:
            await self._db.close()
            raise RuntimeError(
                f"PostgreSQL {server // 10000} is too old: NetCoreNOC needs 15 or newer"
            )
        if self._exclusive and not await raw.fetchval(
            "SELECT pg_try_advisory_lock($1)", _lock_key(self._db.schema, "instance")
        ):
            await self._db.close()
            raise RuntimeError(
                f"another NetCoreNOC is already using schema {self._db.schema!r} of this "
                "database: one appliance per schema"
            )
        if not self._create and not await raw.fetchval(
            "SELECT 1 FROM pg_namespace n JOIN pg_class c ON c.relnamespace = n.oid "
            "WHERE n.nspname = $1 AND c.relname = 'schema_migrations'",
            self._db.schema,
        ):
            await self._db.close()
            raise MissingSchemaError(f"schema {self._db.schema!r} has no NetCoreNOC tables")
        await self._migrate()
        await self._db.open_readers()

    async def _migrate(self) -> None:
        """Apply the pending scripts, each in one transaction, and **say which** (DECISIONS #227).

        One line per applied script and one summary line, both at `info`; a boot that applies
        nothing says so at `debug` rather than adding noise to every restart.
        """
        raw = self._db.writer_raw()
        key = _lock_key(self._db.schema, "migrate")
        async with raw.transaction():
            await raw.execute("SELECT pg_advisory_xact_lock($1)", key)
            await raw.execute(f'CREATE SCHEMA IF NOT EXISTS "{self._db.schema}"')
            await raw.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version integer PRIMARY KEY, name text NOT NULL, checksum text NOT NULL, "
                "applied_at timestamptz NOT NULL DEFAULT now())"
            )
            done = {
                int(r["version"]): (str(r["name"]), str(r["checksum"]))
                for r in await raw.fetch("SELECT version, name, checksum FROM schema_migrations")
            }
        current = max(done, default=0)
        applied: list[str] = []
        for script in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = int(script.name.split("_", 1)[0])
            text = script.read_text()
            checksum = hashlib.sha256(text.encode()).hexdigest()
            if version in done:
                if done[version][1] != checksum:
                    self.integrity_warnings.append(
                        f"Migration {script.name} changed after it was applied to this database; "
                        "the schema may not be what this release expects."
                    )
                continue
            async with raw.transaction():
                await raw.execute("SELECT pg_advisory_xact_lock($1)", key)
                if await raw.fetchval(
                    "SELECT 1 FROM schema_migrations WHERE version = $1", version
                ):
                    continue  # another appliance applied it while this one waited for the lock
                log.info("applying migration %s", script.name)
                await raw.execute(text)
                await raw.execute(
                    "INSERT INTO schema_migrations (version, name, checksum) VALUES ($1, $2, $3)",
                    version,
                    script.name,
                    checksum,
                )
            applied.append(script.name)
        newest = await self.schema_version()
        if newest > self.latest_schema_version():
            self.integrity_warnings.append(
                f"The database is at schema {newest}, newer than this release's "
                f"{self.latest_schema_version()}. Run the release that migrated it."
            )
        if applied:
            log.info("schema %d -> %d (%d migration(s) applied)", current, newest, len(applied))
        else:
            log.debug("schema %d, no migrations pending", current)

    @staticmethod
    def latest_schema_version() -> int:
        """The highest migration number on disk (the schema version a healthy DB reaches)."""
        return max((int(p.name.split("_", 1)[0]) for p in MIGRATIONS_DIR.glob("*.sql")), default=0)

    async def schema_version(self) -> int:
        cur = await self.conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def close(self) -> None:
        await self._db.close()

    async def commit(self) -> None:
        await self.conn.commit()

    async def rollback(self) -> None:
        """Abandon the current transaction (F11). The audit chain only advances on commit, so a
        rolled-back batch never breaks it; the engine retries the batch (ADR #450).

        **A lost connection is replaced here**: its transaction is gone already, and the retry
        needs a connection to retry on. The instance lock went with it and is taken again.

        **The id caches go with it.** An element, entity or class first seen inside the
        transaction was cached with the id its INSERT returned, and the rollback removed that row.
        Kept, the cache named a row that did not exist — the next trap from that source failed its
        foreign key — or, once the id was reused, a **different** row, so alarms were filed under
        another element. Found by replaying a rolled-back batch (ADR #450); the caches are only a
        shortcut for an `INSERT ... ON CONFLICT ... RETURNING` that rebuilds them.
        """
        writer = self._db.writer
        if writer is None or writer.raw.is_closed():
            await self._db.reconnect()
            if self._exclusive:
                await self._db.writer_raw().fetchval(
                    "SELECT pg_try_advisory_lock($1)", _lock_key(self._db.schema, "instance")
                )
        else:
            await writer.rollback()
        for cache in (
            self._ne_ids,
            self._entity0_ids,
            self._entity_ids,
            self._class_ids,
            self._touched,
        ):
            cache.clear()

    @contextlib.asynccontextmanager
    async def savepoint(self) -> AsyncIterator[None]:
        """A block whose failure rolls back **only the block** (ADR #452).

        For the capture paths that degrade instead of failing ("a failure here loses the
        annotation, never the trap"). Under SQLite an error left the rest of the transaction
        usable; in PostgreSQL it aborts the whole transaction, so the engine's batch — the traps
        themselves — would go with the annotation. A savepoint keeps the old meaning. The caller
        still catches the exception; this only undoes what the block wrote.
        """
        async with self.conn.savepoint():
            yield

    async def relax_durability(self) -> None:
        """Commit the current transaction **without waiting for its WAL to reach disk**.

        For the engine's batch only (ADR #452). The raw trap stays in the spool until
        :meth:`checkpoint` says the batch is durable, so a power cut that loses the commit loses
        nothing the spool cannot replay — the guarantee SQLite's ``synchronous=NORMAL`` gave, and
        a commit that costs no fsync of its own.
        """
        await self.conn.begin()
        await self.conn.execute("SET LOCAL synchronous_commit = off")

    async def checkpoint(self) -> bool:
        """Make every transaction committed so far durable; True when it is.

        A transaction that takes an id and commits with ``synchronous_commit = on`` returns only
        once the WAL is flushed up to its own commit record — and the WAL is one sequence, so every
        commit before it, relaxed or not, is on disk too. This is what the spool waits for before
        it deletes a segment its cursor has passed (ADR #450). Never inside an open transaction.
        """
        if self.conn.in_transaction:
            return False
        await self.conn.script(
            "BEGIN; SET LOCAL synchronous_commit = on; SELECT pg_current_xact_id(); COMMIT"
        )
        return True

    async def server_status(self) -> dict[str, str]:
        """What `netcorenoc db status` prints: the server, the schema, its version and size."""
        cur = await self.conn.execute(
            "SELECT version() AS server, current_database() AS database, current_user AS role, "
            "current_schema() AS schema"
        )
        row = await cur.fetchone()
        assert row is not None
        status = {key: str(row[key]) for key in ("server", "database", "role", "schema")}
        status["version"] = f"{await self.schema_version()} (this release: "
        status["version"] += f"{self.latest_schema_version()})"
        status["size"] = f"{await self.database_bytes() / 2**20:.1f} MiB"
        return status

    async def database_bytes(self) -> int:
        """Bytes this schema's tables, indexes and TOAST occupy on the server."""
        cur = await self.conn.execute(
            "SELECT COALESCE(SUM(pg_total_relation_size(c.oid)), 0) FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'm', 'p')"
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0
