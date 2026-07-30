"""`StoreBase` — the one declaration site for `Store`'s attributes.

This class **declares and does nothing else**. It holds the ten attribute annotations and the
``conn`` accessor: no queries, no state, no behaviour beyond that accessor. `Store.__init__`, in
this package's ``__init__.py``, is the only place any of these attributes is ever assigned.

Why a declaring base rather than the two mechanisms `MODULE-ARCHITECTURE.md` §6 considered
(DECISIONS #88): a `Protocol` restating `Store`'s internals would be a second source of truth for
its shape, and free functions taking ``conn`` would rewrite all 109 method bodies — which would
make the method-hash parity proof, the thing that makes a 1 512-line split *provable*, impossible
to state. Under this mechanism the enclosing class header is the only edit a moved method needs.

The measurement behind it: exactly **six** methods are called across a mixin boundary in the whole
class. ``conn`` is one of them and lives here; the other five are handled by two sibling-inheritance
edges (`AlarmMixin(DeviceMixin)`, `ReadModelsMixin(GovernanceMixin)`) rather than by restating
signatures here, because a declaration needs a body and a stub that resolves instead of the real
method is a silent no-op write.
"""

from __future__ import annotations

import asyncio

import aiosqlite


class StoreBase:
    """The attributes every mixin may rely on. Declared here, assigned in `Store.__init__`."""

    _path: str
    _conn: aiosqlite.Connection | None
    _device_ids: dict[str, int]
    _ne_ids: dict[str, int]
    _entity0_ids: dict[int, int]  # ne id -> its level-0 entity id
    _entity_ids: dict[tuple[int, str], int]  # (ne id, key) -> promoted entity id
    _class_ids: dict[str, int]
    _touched: dict[tuple[str, int], float]
    # Non-fatal integrity findings from the startup PRAGMA checks (F11): surfaced through
    # operator_warnings(), never a crash — a NOC trap sink must keep ingesting even with a
    # partly-damaged history DB.
    integrity_warnings: list[str]
    # One connection, many tasks: holders of this lock get a consistent view and, critically,
    # commits can never interleave with another task's open cursor (sqlite refuses to commit while
    # statements are in progress). The engine takes it per batch; API handlers take it per request.
    # **No method in this package takes it** — it is a contract for callers.
    lock: asyncio.Lock

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Store.open() not called"
        return self._conn
