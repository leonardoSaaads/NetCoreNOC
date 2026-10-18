"""`StoreBase` — the one declaration site for `Store`'s attributes.

This class **declares and does nothing else**. It holds the attribute annotations and the two
accessors, ``conn`` and ``reading``: no queries, no state, no behaviour beyond them.
`Store.__init__`, in this package's ``__init__.py``, is the only place any of these attributes is
ever assigned.

Why a declaring base rather than the two mechanisms `MODULE-ARCHITECTURE.md` §6 considered
(DECISIONS #88): a `Protocol` restating `Store`'s internals would be a second source of truth for
its shape, and free functions taking ``conn`` would rewrite every method body — which would make
the method-hash parity proof, the thing that makes a 1 512-line split *provable*, impossible to
state. Under this mechanism the enclosing class header is the only edit a moved method needs.

v0.31.0 (ADR #452) removed the nineteen schema probes this class declared. They answered "which
migration has this SQLite file reached?" for a store driven against a database frozen at an older
version; a PostgreSQL schema is migrated to the release's own version before the store runs, so
every probe would answer the same thing, and a branch that cannot be taken is code to delete.
"""

from __future__ import annotations

import asyncio
import contextlib

from netcorenoc.store.db import Connection, Database


class StoreBase:
    """The attributes every mixin may rely on. Declared here, assigned in `Store.__init__`."""

    _db: Database
    # One appliance per schema: `open()` takes a session advisory lock, so a second engine against
    # the same history fails at start instead of corrupting it (ADR #452).
    _exclusive: bool
    _create: bool  # False: a missing schema is refused, not created (the CLI's tools)
    _ne_ids: dict[str, int]
    _entity0_ids: dict[int, int]  # ne id -> its level-0 entity id
    _entity_ids: dict[tuple[int, str], int]  # (ne id, key) -> promoted entity id
    _class_ids: dict[str, int]
    _touched: dict[tuple[str, int], float]
    # Non-fatal findings from the startup checks (F11): surfaced through operator_warnings(), never
    # a crash — a NOC trap sink must keep ingesting.
    integrity_warnings: list[str]
    # One writer, many tasks: holders of this lock get a consistent view of the writer connection
    # and, critically, their statements are one transaction that no other task's commit adopts. The
    # engine takes it per batch; API writes take it per request (`Perimeter.write_txn`).
    # **No method in this package takes it** — it is a contract for callers. API reads do not take
    # it: they run on a reader (`reading()`), in their own snapshot.
    lock: asyncio.Lock

    @property
    def conn(self) -> Connection:
        """The connection this task's statements run on: its reader inside `reading()`, else the
        writer."""
        return self._db.current()

    def reading(self) -> contextlib.AbstractAsyncContextManager[None]:
        """Run the block's reads on a pooled read-only connection, without ``store.lock``.

        For a read that writes nothing and needs no uncommitted row: every GET the console polls.
        The block sees one consistent snapshot of what was committed when it began.
        """
        return self._db.reading()

    def side_write(self) -> contextlib.AbstractAsyncContextManager[None]:
        """A short write outside ``store.lock``, on its own connection, committed at the end.

        Only for rows nothing in the process keeps in memory: a session's or a token's last use.
        Everything the engine or the audit chain depends on stays on the writer, under the lock.
        """
        return self._db.side_write()

    def _label_join(self, alias: str, kind: str, target: str) -> str:
        """One `LEFT JOIN label` for the whole target (`qualifier = ''`) of one kind (v0.16.3).

        **A literal**, never caller data: `kind` is one of the label kinds and `alias`/`target` are
        SQL identifiers every call site passes as its own.
        """
        return (
            f"LEFT JOIN label {alias} ON {alias}.kind='{kind}' "
            f"AND {alias}.target_id={target} AND {alias}.qualifier='' "
        )
