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
is the change that measured 715 ms -> a few ms at p50 under a storm (`docs/plans/postgresql.md`). A
write attempted inside a reader fails loudly — PostgreSQL refuses it in a read-only transaction.

**Types** are `codecs.py`'s: every connection here, the writer and each reader, registers them, so
the store reads an instant as epoch seconds and an address as its text.

**SQL.** The store's statements keep SQLite's ``?`` and ``:name`` placeholders; :func:`translate`
rewrites them to ``$n`` once per distinct statement, skipping quoted text and comments, and decides
whether the statement returns rows and whether it writes. PostgreSQL's ``?`` jsonb operators are
therefore unusable in store SQL; ``jsonb_exists()`` and ``@>`` say the same thing.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import functools
import logging
import re
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from typing import Any

import asyncpg

from netcorenoc.store.codecs import setup as _setup

log = logging.getLogger("netcorenoc")

__all__ = [
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

# --- the statements ------------------------------------------------------------------------------

_ROWS = re.compile(r"^\s*(SELECT|WITH|VALUES|SHOW|EXPLAIN|TABLE)\b|\bRETURNING\b", re.IGNORECASE)
_WRITE_HEAD = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|MERGE|CREATE|DROP|ALTER|TRUNCATE|SET|LOCK|GRANT|REVOKE|COMMENT)\b",
    re.IGNORECASE,
)
_WRITE_ANYWHERE = re.compile(
    r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|FOR\s+(NO\s+KEY\s+)?UPDATE|FOR\s+SHARE|"
    r"pg_advisory_xact_lock)\b",
    re.IGNORECASE,
)


@functools.lru_cache(maxsize=4096)
def translate(sql: str) -> tuple[str, tuple[str, ...] | None, bool, bool]:
    """``(postgres_sql, names, returns_rows, writes)`` for one store statement.

    ``names`` is the order of ``:name`` parameters, or ``None`` for positional ``?`` ones.
    Quoted literals, quoted identifiers and ``--`` comments are copied untouched, and ``::`` (a
    cast) is never read as a parameter.
    """
    out: list[str] = []
    names: list[str] = []
    positional = 0
    i, length = 0, len(sql)
    while i < length:
        c = sql[i]
        if c in "'\"":
            j = i + 1
            while j < length:
                if sql[j] == c:
                    if j + 1 < length and sql[j + 1] == c:
                        j += 2
                        continue
                    break
                j += 1
            out.append(sql[i : j + 1])
            i = j + 1
        elif c == "-" and sql.startswith("--", i):
            j = sql.find("\n", i)
            j = length if j < 0 else j
            out.append(sql[i:j])
            i = j
        elif c == "?":
            positional += 1
            out.append(f"${positional}")
            i += 1
        elif (
            c == ":"
            and i + 1 < length
            and (sql[i + 1].isalpha() or sql[i + 1] == "_")
            and (i == 0 or sql[i - 1] != ":")
        ):
            j = i + 1
            while j < length and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            name = sql[i + 1 : j]
            if name not in names:
                names.append(name)
            out.append(f"${names.index(name) + 1}")
            i = j
        else:
            out.append(c)
            i += 1
    text = "".join(out)
    if positional and names:
        raise ValueError("a statement mixes ? and :name parameters")
    returns_rows = bool(_ROWS.search(text))
    writes = bool(_WRITE_HEAD.match(text) or _WRITE_ANYWHERE.search(text))
    return text, (tuple(names) if names else None), returns_rows, writes


def _arguments(names: tuple[str, ...] | None, params: Any) -> Sequence[Any]:
    if names is None:
        return tuple(params) if params else ()
    assert isinstance(params, Mapping), "a :name statement takes a mapping"
    return tuple(params[name] for name in names)


def _rowcount(status: str) -> int:
    """`UPDATE 3`, `DELETE 0`, `INSERT 0 1` -> the number of rows; -1 when the status has none."""
    tail = status.rsplit(" ", 1)[-1] if status else ""
    return int(tail) if tail.isdigit() else -1


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
        # asyncpg refuses a second operation while one is in flight on the same connection, where
        # SQLite's driver queued it. Callers hold ``store.lock`` for anything that must be atomic;
        # this only keeps two tasks that read without it from colliding on the wire.
        self._busy = asyncio.Lock()

    @property
    def in_transaction(self) -> bool:
        return self._open

    async def _begin(self) -> None:
        if self._implicit and not self._open:
            await self.raw.execute("BEGIN")
            self._open = True

    async def begin(self) -> None:
        """Open the implicit transaction now, if one is not open (before a ``SET LOCAL``)."""
        async with self._busy:
            await self._begin()

    async def execute(self, sql: str, params: Any = ()) -> Cursor:
        text, names, returns_rows, writes = translate(sql)
        args = _arguments(names, params)
        async with self._busy:
            if writes:
                await self._begin()
            if returns_rows:
                rows = await self.raw.fetch(text, *args)
                return Cursor(rows, len(rows))
            status = await self.raw.execute(text, *args)
        return Cursor([], _rowcount(status))

    async def executemany(self, sql: str, rows: Iterable[Any]) -> None:
        text, names, _, _ = translate(sql)
        batch = [_arguments(names, row) for row in rows]
        if not batch:
            return
        async with self._busy:
            await self._begin()
            await self.raw.executemany(text, batch)

    async def copy(self, table: str, columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
        """Bulk-insert ``rows`` with ``COPY`` — one round trip for any number of rows."""
        async with self._busy:
            await self._begin()
            await self.raw.copy_records_to_table(table, columns=list(columns), records=rows)

    async def script(self, sql: str) -> None:
        """Run a fixed, parameterless, multi-statement script in one round trip."""
        async with self._busy:
            await self.raw.execute(sql)

    async def commit(self) -> None:
        async with self._busy:
            if self._open:
                self._open = False
                await self.raw.execute("COMMIT")

    async def rollback(self) -> None:
        async with self._busy:
            if self._open:
                self._open = False
                if not self.raw.is_closed():
                    await self.raw.execute("ROLLBACK")


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
        return settings

    async def connect(self) -> asyncpg.Connection:
        """A new connection with the store's codecs and search path (the writer, a migration)."""
        raw = await asyncpg.connect(
            self.dsn,
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
                await self.writer.commit()
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
        another ``reading()`` the block joins the snapshot it is already in.
        """
        bound = _reader.get()
        if bound is not None and bound[0] is self:
            yield
            return
        assert self.pool is not None, "Store.open() not called"
        async with self.pool.acquire() as raw:
            transaction = raw.transaction(isolation="repeatable_read", readonly=True)
            await transaction.start()
            token = _reader.set((self, Connection(raw, implicit=False)))
            try:
                yield
            finally:
                _reader.reset(token)
                with contextlib.suppress(*STORAGE_ERRORS):
                    await transaction.rollback()
