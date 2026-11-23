"""`EngineBase` — the one declaration site for `Engine`'s attributes.

The exact counterpart of `store/base.py`, for the same reason and on the same terms: it
**declares and does nothing else**. `Engine.__init__` is the only place any of these is assigned,
and no mixin duplicates an annotation.

It is a separate module rather than living in `engine.py` because the import graph forbids the
alternative: `engine.py` imports the mixins in order to assemble `Engine`, so a mixin importing
`EngineBase` from `engine.py` would be a cycle. `store/base.py` sits apart from
`store/__init__.py` for exactly this reason.

Only the attributes the **may-leave** mixins touch are declared here — fifteen of them, measured
rather than listed by hand. The rest of `Engine`'s state is private to `engine.py` and stays
there, because nothing outside it needs to see it.

Between v0.7.3 and v0.8.1 there were **no method declarations here at all**, which was a
consequence of DECISIONS #90 rather than luck: with `maintenance()` staying in `engine.py`, the
cross-boundary call graph had zero mixin→`Engine` and zero mixin→mixin edges.

**v0.9.0 adds exactly one (DECISIONS #121)**, and its shape is chosen so the hazard DECISIONS #88
named cannot occur. `maintenance_loop` moved here from `engine.py` and calls `self.maintenance`,
which `Engine` still owns — one mixin→`Engine` edge. The declaration is under `if TYPE_CHECKING:`,
so **at runtime this class has no such attribute at all**: `mypy --strict` gets the signature it
needs, and a build that somehow lost `Engine.maintenance` would raise `AttributeError` rather than
resolve to a stub that silently did nothing. #88's objection was to *stubs that resolve*; a
declaration that does not exist at runtime cannot.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from functools import cached_property
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from netcorenoc.engine.correlate.correlate import Correlator
from netcorenoc.engine.correlate.learn import Learner
from netcorenoc.engine.correlate.rootcause import Member, Precedence
from netcorenoc.engine.correlate.varbind_profile import VarbindProfiler
from netcorenoc.engine.dataset.capture import Capture, RetentionPolicy
from netcorenoc.engine.evaluation.league_shadow import LeagueShadow
from netcorenoc.engine.evaluation.shadow import Shadow
from netcorenoc.engine.model import league as league_model
from netcorenoc.engine.mw import index as mw_index
from netcorenoc.engine.mw.ledger import StateLedger
from netcorenoc.engine.operate.deadline import DeadlineTracker
from netcorenoc.engine.operate.intake import Feed, NullFeed
from netcorenoc.engine.operate.lanes import Lanes
from netcorenoc.engine.operate.occurrence import REARM_S
from netcorenoc.engine.operate.upkeep import SituationUpkeep
from netcorenoc.store import Store

if TYPE_CHECKING:
    from netcorenoc.engine.operate.gaps import GapTracker


class EngineBase:
    """The attributes the may-leave mixins rely on. Declared here, assigned in `Engine.__init__`."""

    store: Store
    #: ADR #495: one lane per broker — its correlator, learned state and decider. The attributes
    #: below that used to be the engine's own are now the Default broker's lane's, kept as
    #: properties so every reader written before brokers still reads what it read.
    lanes: Lanes
    profiler: VarbindProfiler
    gap: GapTracker
    # Set by the runner to the receiver's cumulative queue-full drop count (§5.6). The default in
    # `Engine.__init__` keeps the engine self-contained in tests; the trap path stays untouched.
    dropped_provider: Callable[[], int]
    _dropped_baseline: int
    # Entity promotion state (S5), read by the maintenance sweep.
    _active_nes: set[int]
    _cap_audited: set[int]
    _entity_cap_hit: set[int]
    _ne_entity_keys: dict[int, set[str]]
    ne_discriminator: dict[int, list[tuple[str, float]]]
    ne_severity: dict[int, str]
    # Scoring configuration (v0.6.0). `_loaded_key` is (config id, params hash) of the
    # configuration currently instantiated; a reload that finds the same key is a no-op, which is
    # what keeps a fail-safe degradation sticky.
    rejected_proposals: set[tuple[int, int]]
    # Feedback-dataset capture (v0.8.0), read by the maintenance mixin's `_capture_run`. Every
    # decision about what to write lives in `netcorenoc.capture`; these two are state the engine
    # owns and the mixin marshals.
    capture: Capture
    retention: RetentionPolicy
    # Shadow mode (v0.9.0), read by `maintenance_loop`. The `Shadow` object owns the challenger and
    # every decision about it; the engine only holds it and calls it.
    shadow: Shadow
    # v0.16.2. How many live situations the last sweep refused to resolve because one of their
    # alarms is still active (DECISIONS #275). Written by `MaintenanceMixin._observe_idle_active`
    # and read by `stale_situation_warnings`, both in `maintenance.py`.
    #
    # **A class-level default rather than an `__init__` assignment**, for the reason
    # `scorer_model_version_id` gives one entry above: `Engine.__init__` is in `engine.py`, whose
    # bytes are pinned by `TRAP_PATH_HASHES`, so a release that may not touch that file cannot
    # assign there. The default is only ever read by an `Engine` whose maintenance loop has not run
    # a pass, and zero is the right answer for it — it has observed nothing, and reports nothing.
    _idle_active_count: int = 0
    # v0.21.0. The maintenance-window snapshot the ingest path reads, and the state ledger for
    # what a window suppressed. Declared here because `MaintenanceMixin._maintenance_windows`
    # rebuilds the first and flushes the second, and both are assigned in `Engine.__init__`.
    windows: mw_index.WindowIndex
    ledger: StateLedger
    # The open-situation maps, assigned in `Engine.__init__` and declared here for the same reason
    # the two above are: `WindowSweepMixin` writes to them when it surfaces a fault, so that a
    # later real trap for the same fingerprint joins the situation the sweep opened rather than
    # opening a second one beside it.
    sit_of: dict[int, int]
    members: dict[int, list[Member]]
    # v0.26.0: when the correlator's memories were last bounded (`MaintenanceMixin._memory_sweep`).
    # A class-level default for the reason `_idle_active_count` gives: nothing reads it before a
    # sweep has run, and zero means "prune on the first one".
    _memory_pruned_at: float = 0.0
    # v0.28.0 (ADR #431): the re-arm window `engine/operate/occurrence.py` reads. A class-level
    # default for the reason `_idle_active_count` gives; `runner.py` sets it from the settings.
    rearm_s: float = REARM_S
    # ADR #450: where the batch transaction stages the spool's cursor. A class-level default for
    # the reason `_idle_active_count` gives; `runner.py` sets the spool's own feed.
    feed: Feed = NullFeed()
    # ADR #494: how a remote proxy's records reach the spool — the ingest process's `append`,
    # set by `runner.py`; None where there is no spool (the in-process receiver, tests).
    proxy_spool: Callable[[list[bytes]], Awaitable[bool]] | None = None
    #: ADR #494: a broker port the ingest process could not open, and why (`listening.loop`).
    broker_port_errors: Mapping[int, str] = MappingProxyType({})

    # ADRs #447 and #448: each trap's own classification clock, and what a batch owes the
    # situations it touched. Per-instance objects created on first use, for the reason
    # `_idle_active_count` gives: `engine.py` is at its cohesion ceiling, so an `__init__` line
    # there costs the one module that has no room. `runner.py` sets the KPI from the settings.
    @cached_property
    def deadlines(self) -> DeadlineTracker:
        return DeadlineTracker()

    @cached_property
    def upkeep(self) -> SituationUpkeep:
        return SituationUpkeep()

    # -- the Default broker's lane, under the names the engine always had (ADR #495) ------------

    @property
    def correlator(self) -> Correlator:
        return self.lanes.default.correlator

    @correlator.setter
    def correlator(self, value: Correlator) -> None:
        self.lanes.default.correlator = value

    @property
    def learner(self) -> Learner:
        return self.lanes.default.learner

    @learner.setter
    def learner(self, value: Learner) -> None:
        self.lanes.default.learner = value

    @property
    def precedence(self) -> Precedence:
        return self.lanes.default.precedence

    @precedence.setter
    def precedence(self, value: Precedence) -> None:
        self.lanes.default.precedence = value

    @property
    def league_shadow(self) -> LeagueShadow:
        return self.lanes.default.league_shadow

    @league_shadow.setter
    def league_shadow(self, value: LeagueShadow) -> None:
        self.lanes.default.league_shadow = value

    @property
    def decider_ref(self) -> str:
        return self.lanes.default.decider_ref

    @decider_ref.setter
    def decider_ref(self, value: str) -> None:
        self.lanes.default.decider_ref = value

    @property
    def champion(self) -> league_model.Member | None:
        return self.lanes.default.champion

    @champion.setter
    def champion(self, value: league_model.Member | None) -> None:
        self.lanes.default.champion = value

    @property
    def challengers(self) -> tuple[league_model.Member, ...]:
        return self.lanes.default.challengers

    @challengers.setter
    def challengers(self, value: tuple[league_model.Member, ...]) -> None:
        self.lanes.default.challengers = value

    @property
    def league_members(self) -> league_model.League | None:
        return self.lanes.default.league_members

    @league_members.setter
    def league_members(self, value: league_model.League | None) -> None:
        self.lanes.default.league_members = value

    @property
    def league_view(self) -> dict[str, Any] | None:
        return self.lanes.default.league_view

    @league_view.setter
    def league_view(self, value: dict[str, Any] | None) -> None:
        self.lanes.default.league_view = value

    @property
    def scorer_config_id(self) -> int | None:
        return self.lanes.default.scorer_config_id

    @scorer_config_id.setter
    def scorer_config_id(self, value: int | None) -> None:
        self.lanes.default.scorer_config_id = value

    @property
    def scorer_model_version_id(self) -> int | None:
        return self.lanes.default.scorer_model_version_id

    @scorer_model_version_id.setter
    def scorer_model_version_id(self, value: int | None) -> None:
        self.lanes.default.scorer_model_version_id = value

    @property
    def scorer_warnings(self) -> list[str]:
        return self.lanes.default.scorer_warnings

    @scorer_warnings.setter
    def scorer_warnings(self, value: list[str]) -> None:
        self.lanes.default.scorer_warnings = value

    @property
    def _loaded_key(self) -> tuple[object, ...] | None:
        return self.lanes.default._loaded_key

    @_loaded_key.setter
    def _loaded_key(self, value: tuple[object, ...] | None) -> None:
        self.lanes.default._loaded_key = value

    @property
    def latency_us(self) -> dict[str, float]:
        return self.lanes.latency_us

    @latency_us.setter
    def latency_us(self, value: dict[str, float]) -> None:
        self.lanes.latency_us.clear()
        self.lanes.latency_us.update(value)

    if TYPE_CHECKING:  # pragma: no cover - declaration only; no runtime attribute exists

        async def maintenance(
            self, now: float, retention_days: float, tick: int = 0
        ) -> None:  # pragma: no cover
            """Declared, never defined. `Engine` owns the body; see this module's docstring."""
