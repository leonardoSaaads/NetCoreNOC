"""PostgreSQL access for the store: one writer connection, a pool of readers (ADR #452).

**The writer** is one connection, used by every method of `Store` unless a reader is bound, and
guarded by ``store.lock`` exactly as the SQLite connection was: the engine's batch, the maintenance
pass and every API write still serialise on it, because the engine's memory and the audit chain
assume one writer. Its transactions are **implicit**, as Python's ``sqlite3`` made them: the first
write after a commit opens one, a read outside one runs on its own, and ``commit()``/``rollback()``
end it. That is what lets 864 call sites keep their shape (``cur = await conn.execute(...)``).

**The readers** are a pool, bound per task by :meth:`Database.reading` into a context variable, so
an API read runs in its own ``REPEATABLE READ READ ONLY`` snapshot on its own connection and never
waits for ``store.lock``. Under SQLite every console request queued behind the engine's batch; this
is the change that measured 720 -> 19 ms at p50 under a storm (`docs/plans/postgresql.md`). A
write attempted inside a reader fails loudly — PostgreSQL refuses it in a read-only transaction.

**Types** are `codecs.py`'s: every connection here, the writer and each reader, registers them, so
the store reads an instant as epoch seconds and an address as its text.

**SQL** keeps SQLite's ``?`` and ``:name`` placeholders; `sql.py` translates them, once per
distinct statement.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import re
from collections.abc import AsyncIterator, Iterable, Sequence
from typing import Any

import asyncpg

from netcorenoc.store.codecs import setup as _setup
from netcorenoc.store.sql import arguments, rowcount, translate

log = logging.getLogger("netcorenoc")

__all__ = [
    "OPEN_ERRORS",
    "STORAGE_ERRORS",
    "Connection",
    "Cursor",
    "Database",
    "translate",
]

#: Errors a retry can outlive: the connection dropped, the server is restarting or failing over,
#: the disk or the connection slots are full, a deadlock or a serialisation conflict. Anything else
#: — a constraint violation, a syntax error — is a defect and is raised, never retried.
STORAGE_ERRORS: tuple[type[BaseException], ...] = (
    asyncpg.PostgresConnectionError,
    asyncpg.InterfaceError,
    asyncpg.exceptions.InsufficientResourcesError,
    asyncpg.exceptions.OperatorInterventionError,
    asyncpg.exceptions.TransactionRollbackError,
    asyncpg.exceptions.ReadOnlySQLTransactionError,
    ConnectionError,
    OSError,
    TimeoutError,
)

#: What opening a store can raise that an operator fixes by correcting the settings: everything a
#: retry can outlive, plus a role, a password, a database or a schema the server does not know.
OPEN_ERRORS: tuple[type[BaseException], ...] = (*STORAGE_ERRORS, asyncpg.PostgresError)


class Cursor:
    """What a statement returned, already fetched: the ``sqlite3`` cursor surface the store uses."""

    __slots__ = ("_next", "_rows", "rowcount")

    def __init__(self, rows: list[asyncpg.Record], rowcount: int) -> None:
        self._rows = rows
        self._next = 0
        self.rowcount = rowcount

    async def fetchone(self) -> asyncpg.Record | None:
        if self._next >= len(self._rows):
            return None
        self._next += 1
        return self._rows[self._next - 1]

    async def fetchall(self) -> list[asyncpg.Record]:
        rows, self._next = self._rows[self._next :], len(self._rows)
        return rows


class Connection:
    """One asyncpg connection behind the ``sqlite3`` surface: execute, executemany, commit.

    ``implicit`` is the writer's behaviour — a write opens a transaction that lasts until
    ``commit()`` or ``rollback()``. A reader is already inside its snapshot and opens nothing.
    """

    def __init__(self, raw: asyncpg.Connection, *, implicit: bool) -> None:
        self.raw = raw
        self._implicit = implicit
        self._open = False
        # A statement failed inside the implicit transaction, which PostgreSQL has therefore
        # aborted: nothing more can run in it, and a COMMIT would silently roll it back.
        self._failed = False
        # asyncpg refuses a second operation while one is in flight on the same connection, where
        # SQLite's driver queued it. Callers hold ``store.lock`` for anything that must be atomic;
        # this only keeps two tasks that read without it from colliding on the wire.
        self._busy = asyncio.Lock()

    @property
    def in_transaction(self) -> bool:
        return self._open

    async def _begin(self) -> None:
        if self._failed:
            # A block failed and did not roll back. Its work is already lost (PostgreSQL aborted the
            # transaction); carrying on would fail every later holder of `store.lock` instead. Say
            # so, undo it, and continue — the warning names a missing rollback to fix.
            log.warning("a failed transaction was left open on the writer; rolled back")
            self._failed = False
            self._open = False
            await self.raw.execute("ROLLBACK")
        if self._implicit and not self._open:
            await self.raw.execute("BEGIN")
            self._open = True

    async def _run(self, text: str, args: Sequence[Any], returns_rows: bool) -> Cursor:
        try:
            if returns_rows:
                rows = await self.raw.fetch(text, *args)
                return Cursor(rows, len(rows))
            status = await self.raw.execute(text, *args)
        except asyncpg.PostgresError:
            self._failed = self._open
            raise
        return Cursor([], rowcount(status))

    async def begin(self) -> None:
        """Open the implicit transaction now, if one is not open (before a ``SET LOCAL``)."""
        async with self._busy:
            await self._begin()

    async def execute(self, sql: str, params: Any = ()) -> Cursor:
        text, names, returns_rows, writes = translate(sql)
        args = arguments(names, params)
        async with self._busy:
            if writes or self._failed:
                await self._begin()
            return await self._run(text, args, returns_rows)

    async def executemany(self, sql: str, rows: Iterable[Any]) -> None:
        text, names, _, _ = translate(sql)
        batch = [arguments(names, row) for row in rows]
        if not batch:
            return
        async with self._busy:
            await self._begin()
            try:
                await self.raw.executemany(text, batch)
            except asyncpg.PostgresError:
                self._failed = True
                raise

    @contextlib.asynccontextmanager
    async def savepoint(self) -> AsyncIterator[None]:
        """A nested transaction: a failure inside undoes the block only and leaves the enclosing
        transaction usable — what SQLite did for any failed statement (`Store.savepoint`)."""
        await self.begin()
        await self.script("SAVEPOINT netcorenoc_block")
        try:
            yield
        except BaseException:
            async with self._busy:
                await self.raw.execute("ROLLBACK TO SAVEPOINT netcorenoc_block")
                self._failed = False
            raise
        await self.script("RELEASE SAVEPOINT netcorenoc_block")

    async def script(self, sql: str) -> None:
        """Run a fixed, parameterless, multi-statement script in one round trip."""
        async with self._busy:
            await self.raw.execute(sql)

    async def commit(self) -> None:
        async with self._busy:
            if self._failed:
                self._failed = self._open = False
                await self.raw.execute("ROLLBACK")
                raise asyncpg.exceptions.InFailedSQLTransactionError(
                    "the transaction failed earlier and was rolled back; nothing was committed"
                )
            if self._open:
                self._open = False
                await self.raw.execute("COMMIT")

    async def rollback(self) -> None:
        async with self._busy:
            if self._open:
                self._open = False
                self._failed = False
                if not self.raw.is_closed():
                    await self.raw.execute("ROLLBACK")


async def _release(raw: asyncpg.Connection) -> None:
    """What the pool does to a connection coming back: undo a transaction a cancelled block left
    open, and nothing else. asyncpg's default also sends ``RESET ALL``, ``UNLISTEN *``, ``CLOSE
    ALL`` and an advisory unlock — a round trip on every console request for state no block here
    ever sets (readers and side writes set no session settings, listen to nothing, lock nothing)."""
    if raw.is_in_transaction():
        await raw.reset()


#: The reader bound to the current task, with the database it belongs to.
_reader: contextvars.ContextVar[tuple[Database, Connection] | None] = contextvars.ContextVar(
    "netcorenoc_reader", default=None
)


class Database:
    """The writer and the reader pool for one database and one schema."""

    def __init__(
        self,
        dsn: str,
        *,
        schema: str = "netcorenoc",
        readers: int = 4,
        statement_cache_size: int = 1024,
        read_timeout_s: float = 30.0,
    ) -> None:
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema):
            raise ValueError(f"schema {schema!r}: lower-case letters, digits and _ only")
        self.dsn = dsn
        self.schema = schema
        self.readers = readers
        self.statement_cache_size = statement_cache_size
        self.read_timeout_s = read_timeout_s
        self.writer: Connection | None = None
        self.pool: asyncpg.Pool | None = None

    def _settings(self, *, reader: bool) -> dict[str, str]:
        settings = {
            "application_name": "netcorenoc-reader" if reader else "netcorenoc",
            "search_path": self.schema,
            "timezone": "UTC",
        }
        if reader:  # a console read that runs away is cancelled, never left holding a snapshot
            settings["statement_timeout"] = str(int(self.read_timeout_s * 1000))
            settings["idle_in_transaction_session_timeout"] = str(int(self.read_timeout_s * 2000))
            # The pool's only writes are a session's or a token's last use (`side_write`). A crash
            # that loses one shortens an idle session and never lengthens it, so those commits do
            # not wait for the WAL flush: one fsync less on every API request.
            settings["synchronous_commit"] = "off"
        return settings

    async def connect(self, dsn: str | None = None) -> asyncpg.Connection:
        """A new connection with the store's codecs and search path: the writer, or the schema
        owner's for the migrations (``dsn``, ADR #453)."""
        raw = await asyncpg.connect(
            dsn or self.dsn,
            statement_cache_size=self.statement_cache_size,
            server_settings=self._settings(reader=False),
        )
        await _setup(raw)
        return raw

    async def open(self) -> None:
        self.writer = Connection(await self.connect(), implicit=True)

    async def open_readers(self) -> None:
        """The reader pool. Opened after the migrations, so a reader never sees a half schema."""
        if self.pool is not None:
            return
        self.pool = await asyncpg.create_pool(
            self.dsn,
            min_size=1,
            max_size=max(1, self.readers),
            statement_cache_size=self.statement_cache_size,
            server_settings=self._settings(reader=True),
            init=_setup,
            reset=_release,
        )

    async def reconnect(self) -> None:
        """Replace a writer whose connection was lost. Its transaction went with it."""
        if self.writer is not None and not self.writer.raw.is_closed():
            with contextlib.suppress(Exception):
                await self.writer.raw.close(timeout=2)
        self.writer = Connection(await self.connect(), implicit=True)
        log.warning("database connection re-established")

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None
        if self.writer is not None:
            if not self.writer.raw.is_closed():
                try:
                    await self.writer.commit()
                except asyncpg.exceptions.InFailedSQLTransactionError:  # rolled back by commit()
                    log.warning("a failed transaction was open at close; rolled back")
                await self.writer.raw.close()
            self.writer = None

    def writer_raw(self) -> asyncpg.Connection:
        """The writer's asyncpg connection, for the lifecycle's own statements."""
        assert self.writer is not None, "Store.open() not called"
        return self.writer.raw

    def current(self) -> Connection:
        """The reader bound to this task, if it is one of ours, else the writer."""
        bound = _reader.get()
        if bound is not None and bound[0] is self:
            return bound[1]
        assert self.writer is not None, "Store.open() not called"
        return self.writer

    @contextlib.asynccontextmanager
    async def reading(self) -> AsyncIterator[None]:
        """Run the block's store reads on a pooled connection, in one read-only snapshot.

        **Never inside a write**: the snapshot cannot see the writer's uncommitted rows. Nested in
        another ``reading()`` (or ``side_write()``) the block joins the transaction it is in.
        """
        async with self._pooled(readonly=True):
            yield

    @contextlib.asynccontextmanager
    async def side_write(self) -> AsyncIterator[None]:
        """Run the block on a pooled connection in its **own** read-write transaction, committed
        when the block ends and rolled back if it raises — without ``store.lock``.

        Only for rows nothing in the process keeps in memory and nothing else writes in the same
        breath: a session's or a token's last use (`Perimeter.resolve_identity`). Everything the
        engine or the audit chain depends on stays on the writer.
        """
        async with self._pooled(readonly=False):
            yield

    @contextlib.asynccontextmanager
    async def _pooled(self, *, readonly: bool) -> AsyncIterator[None]:
        bound = _reader.get()
        if bound is not None and bound[0] is self:
            yield
            return
        assert self.pool is not None, "Store.open() not called"
        async with self.pool.acquire() as raw:
            if not readonly:
                # The writer's discipline on a pooled connection: the first write opens the
                # transaction, so a block that only reads — an identity whose last use was recorded
                # moments ago — sends no BEGIN and no COMMIT.
                side = Connection(raw, implicit=True)
                token = _reader.set((self, side))
                try:
                    yield
                except BaseException:
                    _reader.reset(token)
                    with contextlib.suppress(*STORAGE_ERRORS):
                        await side.rollback()
                    raise
                _reader.reset(token)
                await side.commit()
                return
            transaction = raw.transaction(isolation="repeatable_read", readonly=True)
            try:
                await transaction.start()
            except BaseException:
                raw.terminate()  # never back in the pool in a state `_release` does not undo
                raise
            token = _reader.set((self, Connection(raw, implicit=False)))
            try:
                yield
            finally:
                _reader.reset(token)
                with contextlib.suppress(*STORAGE_ERRORS):
                    await transaction.rollback()  # a snapshot has nothing to commit
