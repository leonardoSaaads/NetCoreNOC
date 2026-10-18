"""PostgreSQL persistence behind one small interface (ADR #452; SQLite until v0.30.0).

**One `Store`, one writer connection, one `store.lock`.** That is the load-bearing invariant of this
package and the reason it is a package of mixins over one object rather than several objects: F39
(`SECURITY-REVIEW-0.7.1.md` §4.2) exists precisely *because* the writer is shared by the engine
task and every API write, and v0.7.1's `write_txn` discipline is built on `store.lock` being the
one mutual exclusion for it.

**Reads are the exception PostgreSQL made possible.** A console read runs inside
:meth:`Store.reading` on a pooled, read-only connection in its own snapshot, and takes no lock: it
cannot see an uncommitted batch, so it cannot observe half of one, and it no longer waits for one.

`store.lock` is a contract for **callers** — `Engine._commit_batch`, `Engine.maintenance` and
`Perimeter.write_txn` take it; no method in this package does. See
`tests/store/test_store_concurrency.py`, which is the control.

Hand-written SQL, plain-SQL migrations applied at startup (`lifecycle.py`). The engine batches
writes and calls :meth:`Store.commit` per batch; interleaved API writes take the lock and commit
their own transaction.
"""

from __future__ import annotations

import asyncio

from netcorenoc.store.alarms import AlarmMixin
from netcorenoc.store.attention import AttentionMixin
from netcorenoc.store.audit_log import AuditLogMixin
from netcorenoc.store.auth import AuthMixin
from netcorenoc.store.autonomy import AutonomyMixin
from netcorenoc.store.base import StoreBase
from netcorenoc.store.batch_writes import BatchWriteMixin, LinkRow
from netcorenoc.store.catalogue_reads import CatalogueReadMixin
from netcorenoc.store.class_rules import ClassRuleMixin
from netcorenoc.store.dataset import (
    MAX_CLIENT_MEMBERS,
    DatasetMixin,
)
from netcorenoc.store.db import OPEN_ERRORS, STORAGE_ERRORS, Database
from netcorenoc.store.decider import DeciderMixin
from netcorenoc.store.devices import DeviceMixin
from netcorenoc.store.entities import EntityMixin
from netcorenoc.store.feedback import FeedbackMixin
from netcorenoc.store.governance import GovernanceMixin
from netcorenoc.store.host_samples import HostSampleMixin
from netcorenoc.store.idle import IdleMixin
from netcorenoc.store.ingest_gaps import IngestGapMixin
from netcorenoc.store.inventory import InventoryMixin
from netcorenoc.store.league import LeagueMixin
from netcorenoc.store.learned import LearnedMixin
from netcorenoc.store.lifecycle import LifecycleMixin, MissingSchemaError
from netcorenoc.store.maintenance_windows import MaintenanceWindowMixin
from netcorenoc.store.mw_compile import MaintenanceCompileMixin
from netcorenoc.store.mw_ledger import MaintenanceLedgerMixin
from netcorenoc.store.mw_reads import MaintenanceReadMixin
from netcorenoc.store.occurrences import OccurrenceMixin
from netcorenoc.store.organizations import OrganizationMixin
from netcorenoc.store.people import PeopleMixin
from netcorenoc.store.promotion import PromotionMixin
from netcorenoc.store.proposals import ProposalMixin
from netcorenoc.store.read_models import ReadModelsMixin
from netcorenoc.store.recovery import RecoveryMixin
from netcorenoc.store.restructure import RestructureMixin
from netcorenoc.store.retention import RetentionMixin
from netcorenoc.store.scoring_config import ScoringConfigMixin
from netcorenoc.store.seal import SealMixin
from netcorenoc.store.shadow import ShadowMixin
from netcorenoc.store.situation_reads import SituationReadsMixin
from netcorenoc.store.state_clears import StateClearMixin
from netcorenoc.store.timeline_models import TimelineMixin
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
    "OPEN_ERRORS",
    "STORAGE_ERRORS",
    "TOUCH_INTERVAL_S",
    "EdgeRow",
    "FeedbackResult",
    "IngestResult",
    "LinkRow",
    "MissingSchemaError",
    "Store",
    "StoreBase",
]


class Store(
    AutonomyMixin,
    # v0.27.0: the league's decisions and pin, and pending proposals (ADRs #425, #428). Plain
    # `StoreBase` mixins that override nothing.
    LeagueMixin,
    ProposalMixin,
    DeciderMixin,
    PromotionMixin,
    SealMixin,
    ShadowMixin,
    DatasetMixin,
    RetentionMixin,
    AuditLogMixin,
    # v0.25.0: a display name and a photo per account (ADR #401, #402). Plain `StoreBase`.
    PeopleMixin,
    # v0.30.0: a person's recovery address and the single-use reset links (ADR #445).
    RecoveryMixin,
    AuthMixin,
    ScoringConfigMixin,
    IngestGapMixin,
    StateClearMixin,
    EntityMixin,
    InventoryMixin,
    CatalogueReadMixin,
    ReadModelsMixin,
    ClassRuleMixin,
    # v0.20.0. The timeline's two reads and their shared scope builder, off
    # `ReadModelsMixin` at the 400-line guard. A plain `StoreBase` mixin: it overrides no
    # name and nothing overrides it, so its position here is only readability.
    TimelineMixin,
    HostSampleMixin,
    AttentionMixin,
    SituationReadsMixin,
    GovernanceMixin,
    FeedbackMixin,
    # v0.16.0. `RestructureMixin` inherits `SituationMixin`, which inherits `SituationEventMixin`
    # — the third sibling-inheritance edge in this package (DECISIONS #88). Listed most-derived
    # first so the MRO resolves each name to the real method rather than to a base that does not
    # have it.
    RestructureMixin,
    # v0.16.2. The idle predicate and the two queries that partition on it (DECISIONS #274). Below
    # `RestructureMixin` because nothing in it overrides a name; it is four methods and two SQL
    # fragments over `StoreBase`, and it is a sibling rather than a base of `SituationMixin` so the
    # lifecycle writes and the population reads stay separable.
    IdleMixin,
    # The engine batch's two per-activation writes without their per-call cost (ADR #448). Plain
    # `StoreBase`; it overrides nothing.
    BatchWriteMixin,
    # v0.28.0: an alarm's occurrences — when a repeat starts a new one, when one with no clear
    # ends (ADRs #431, #433). Plain `StoreBase`; it overrides nothing.
    OccurrenceMixin,
    # v0.21.0, the maintenance window. Four mixins on the seam this package already uses: the
    # writes and the state machine, the projections, the state ledger, and the organization an
    # element and a window belong to. `MaintenanceLedgerMixin` is listed above `AlarmMixin`
    # because it calls `entity_level0`, which `DeviceMixin` owns — the same sibling-resolution
    # rule the three existing inheritance edges follow, and the reason this list is ordered
    # most-derived first rather than alphabetically.
    MaintenanceWindowMixin,
    MaintenanceCompileMixin,
    MaintenanceReadMixin,
    MaintenanceLedgerMixin,
    OrganizationMixin,
    LearnedMixin,
    AlarmMixin,
    DeviceMixin,
    LifecycleMixin,
    StoreBase,
):
    def __init__(
        self,
        dsn: str,
        *,
        schema: str = "netcorenoc",
        readers: int = 4,
        statement_cache_size: int = 1024,
        exclusive: bool = True,
        create: bool = True,
    ) -> None:
        """A store for the PostgreSQL database at ``dsn``, in ``schema`` (ADR #452).

        ``readers`` is the size of the pool API reads run on (`reading()`); ``statement_cache_size``
        is asyncpg's per-connection prepared-statement cache, which must be ``0`` behind a pooler in
        transaction mode (Supabase's port 6543, PgBouncer); ``exclusive`` refuses a second appliance
        against the same schema; ``create=False`` refuses a schema that is not there instead of
        creating it (the CLI's tools, which must never read an empty history as the real one).
        """
        self._db = Database(
            dsn, schema=schema, readers=readers, statement_cache_size=statement_cache_size
        )
        self._exclusive = exclusive
        self._create = create
        self._ne_ids: dict[str, int] = {}
        self._entity0_ids: dict[int, int] = {}  # ne id -> its level-0 entity id
        self._entity_ids: dict[tuple[int, str], int] = {}  # (ne id, key) -> promoted entity id
        self._class_ids: dict[str, int] = {}
        self._touched: dict[tuple[str, int], float] = {}
        # Non-fatal findings from the startup checks (F11): surfaced through operator_warnings(),
        # never a crash — a NOC trap sink must keep ingesting.
        self.integrity_warnings: list[str] = []
        # One writer, many tasks: see `StoreBase.lock`. The engine takes it per batch; API writes
        # take it per request; API reads run on a reader without it.
        self.lock = asyncio.Lock()
