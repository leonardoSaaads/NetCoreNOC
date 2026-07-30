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

from netcorenoc.store._all import Store
from netcorenoc.store.base import StoreBase
from netcorenoc.store.types import (
    MAX_SCOPE_PARAMS,
    MIGRATIONS_DIR,
    TOUCH_INTERVAL_S,
    EdgeRow,
    FeedbackResult,
    IngestResult,
)

__all__ = [
    "MAX_SCOPE_PARAMS",
    "MIGRATIONS_DIR",
    "TOUCH_INTERVAL_S",
    "EdgeRow",
    "FeedbackResult",
    "IngestResult",
    "Store",
    "StoreBase",
]
