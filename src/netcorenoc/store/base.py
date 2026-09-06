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
    # v0.8.0: does `situation` carry `merged_into` (migration 0008)? `None` until the first merge
    # answers it. Present so the identical engine code can run against a pre-0008 schema, which is
    # what `tests/test_upgrade.py` relies on to prove the migration changes behaviour and the code
    # does not — the same reason `create_situation` tolerates a schema without `scorer_config_id`.
    _has_merged_into: bool | None
    # v0.16.0: does `situation` carry `resolution` (migration 0014)? Resolved **once, at
    # `open()`**, from `PRAGMA table_info` — not lazily from a caught `OperationalError` like
    # `_has_merged_into` above. The difference is deliberate: `merged_into` learns from a failure
    # on a rare path (four merges across the whole eval corpus), while every close, every merge and
    # every situation this release creates would have to learn it, and a probe that costs one query
    # at startup is cheaper and states the question instead of inferring it from an exception.
    # `False` is what lets the identical store code run against the schema-13 database
    # `tests/test_upgrade.py` builds.
    _has_lifecycle: bool
    # v0.16.1: does `feedback` carry `bag_key` (migration 0015)? Resolved by the same probe, for
    # the same reason — `add_feedback` runs on every verdict and every gesture, so inferring the
    # schema from an exception would pay a caught error on the busiest write path there is.
    _has_bag_key: bool
    # v0.16.3: does `label` carry `qualifier` (migration 0016)? One probe answers for the whole of
    # that migration's change to the table, because `0016` renamed the equipment kind from
    # `device` to `ne` in the same step that widened the primary key — so a schema that has the
    # column has the kind, and one that has neither has neither.
    _has_label_qualifier: bool

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Store.open() not called"
        return self._conn

    def _label_join(self, alias: str, kind: str, target: str, legacy_target: str = "") -> str:
        """One `LEFT JOIN label`, in the shape the schema on disk actually has (v0.16.3).

        **A literal chosen by the schema probe**, never by a caller: both branches are fixed
        strings and only `alias`/`target`, which every call site passes as its own SQL identifier,
        vary. The same discipline `_lifecycle_columns` uses, and for the same reason —
        `tests/test_upgrade.py` drives the current store against a migration directory frozen at
        an older version, and a join naming `qualifier` unconditionally would raise there.

        `legacy_target` is the pre-`0016` target expression for the equipment kind, where the row
        was keyed on `device.id` rather than on `ne.id`. It is given rather than derived, because
        deriving it would mean assuming those two ids agree — which they do on every database
        anyone has, and which is exactly the coincidence `0016` exists to stop depending on
        (DECISIONS #281).
        """
        if self._has_label_qualifier:
            return (
                f"LEFT JOIN label {alias} ON {alias}.kind='{kind}' "
                f"AND {alias}.target_id={target} AND {alias}.qualifier='' "
            )
        legacy_kind = "device" if kind == "ne" else kind
        on = legacy_target or target
        return (
            f"LEFT JOIN label {alias} ON {alias}.kind='{legacy_kind}' AND {alias}.target_id={on} "
        )
