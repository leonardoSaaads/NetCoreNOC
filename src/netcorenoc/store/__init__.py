"""SQLite (WAL) persistence behind one small interface.

**One `Store`, one connection, one `store.lock`.** That is the load-bearing invariant of this
package and the reason it is a package of mixins over one object rather than several objects: F39
(`SECURITY-REVIEW-0.7.1.md` §4.2) exists precisely *because* the single `aiosqlite` connection is
shared by the engine task and every API request, and v0.7.1's `write_txn` discipline is built on
`store.lock` being the one mutual exclusion. Several connections, or several locks, would be a
behaviour change whose failure mode is data corruption under concurrency.

`store.lock` is a contract for **callers** — `Engine._commit_batch`, `Engine.maintenance` and
`Perimeter.write_txn` take it; no method in this package does. See
`tests/test_store_concurrency.py`, which is the control.

Hand-written SQL, plain-SQL migrations applied at startup via ``PRAGMA user_version``. The engine
batches writes and calls :meth:`Store.commit` per batch; interleaved API writes simply join the
current transaction — a throughput choice, not an atomicity contract.
"""

from __future__ import annotations

import asyncio

import aiosqlite

from netcorenoc.store.alarms import AlarmMixin
from netcorenoc.store.audit_log import AuditLogMixin
from netcorenoc.store.auth import AuthMixin
from netcorenoc.store.base import StoreBase
from netcorenoc.store.dataset import (
    MAX_CLIENT_MEMBERS,
    DatasetMixin,
)
from netcorenoc.store.devices import DeviceMixin
from netcorenoc.store.entities import EntityMixin
from netcorenoc.store.feedback import FeedbackMixin
from netcorenoc.store.governance import GovernanceMixin
from netcorenoc.store.ingest_gaps import IngestGapMixin
from netcorenoc.store.learned import LearnedMixin
from netcorenoc.store.lifecycle import LifecycleMixin
from netcorenoc.store.promotion import PromotionMixin
from netcorenoc.store.read_models import ReadModelsMixin
from netcorenoc.store.restructure import RestructureMixin
from netcorenoc.store.retention import RetentionMixin
from netcorenoc.store.scoring_config import ScoringConfigMixin
from netcorenoc.store.seal import SealMixin
from netcorenoc.store.shadow import ShadowMixin
from netcorenoc.store.state_clears import StateClearMixin
from netcorenoc.store.types import (
    MAX_SCOPE_PARAMS,
    MIGRATIONS_DIR,
    TOUCH_INTERVAL_S,
    EdgeRow,
    FeedbackResult,
    IngestResult,
)

__all__ = [
    "MAX_CLIENT_MEMBERS",
    "MAX_SCOPE_PARAMS",
    "MIGRATIONS_DIR",
    "TOUCH_INTERVAL_S",
    "EdgeRow",
    "FeedbackResult",
    "IngestResult",
    "Store",
    "StoreBase",
]


class Store(
    PromotionMixin,
    SealMixin,
    ShadowMixin,
    DatasetMixin,
    RetentionMixin,
    AuditLogMixin,
    AuthMixin,
    ScoringConfigMixin,
    IngestGapMixin,
    StateClearMixin,
    EntityMixin,
    ReadModelsMixin,
    GovernanceMixin,
    FeedbackMixin,
    # v0.16.0. `RestructureMixin` inherits `SituationMixin`, which inherits `SituationEventMixin`
    # — the third sibling-inheritance edge in this package (DECISIONS #88). Listed most-derived
    # first so the MRO resolves each name to the real method rather than to a base that does not
    # have it.
    RestructureMixin,
    LearnedMixin,
    AlarmMixin,
    DeviceMixin,
    LifecycleMixin,
    StoreBase,
):
    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None
        self._device_ids: dict[str, int] = {}
        self._ne_ids: dict[str, int] = {}
        self._entity0_ids: dict[int, int] = {}  # ne id -> its level-0 entity id
        self._entity_ids: dict[tuple[int, str], int] = {}  # (ne id, key) -> promoted entity id
        self._class_ids: dict[str, int] = {}
        self._touched: dict[tuple[str, int], float] = {}
        # Non-fatal integrity findings from the startup PRAGMA checks (F11): surfaced through
        # operator_warnings(), never a crash — a NOC trap sink must keep ingesting even with a
        # partly-damaged history DB.
        self.integrity_warnings: list[str] = []
        # One connection, many tasks: holders of this lock get a consistent view and,
        # critically, commits can never interleave with another task's open cursor
        # (sqlite refuses to commit while statements are in progress). The engine takes
        # it per batch; API handlers take it per request.
        self.lock = asyncio.Lock()
        self._has_merged_into: bool | None = None
        # Answered by `open()` from `PRAGMA table_info(situation)`. `False` until then, which is
        # the fail-safe direction: a store nobody opened writes no lifecycle column.
        self._has_lifecycle: bool = False
