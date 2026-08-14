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

from collections.abc import Callable
from typing import TYPE_CHECKING

from netcorenoc.capture import Capture, RetentionPolicy
from netcorenoc.correlate import Correlator
from netcorenoc.learn import Learner
from netcorenoc.rootcause import Precedence
from netcorenoc.shadow import Shadow
from netcorenoc.store import Store
from netcorenoc.varbind_profile import VarbindProfiler

if TYPE_CHECKING:
    from netcorenoc.gaps import GapTracker


class EngineBase:
    """The attributes the may-leave mixins rely on. Declared here, assigned in `Engine.__init__`."""

    store: Store
    correlator: Correlator
    learner: Learner
    precedence: Precedence
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
    scorer_config_id: int | None
    # v0.11.0. The OTHER thing the active pointer may name. Exactly one of the two is ever set,
    # which the database enforces (`0013`'s CHECK) and `scorer_lifecycle` maintains — two live
    # values here would be two answers to "what is running", which principle 6 forbids.
    #
    # **A class-level default rather than an `__init__` assignment**, and the reason is a project
    # rule rather than a style preference: `engine.py` is `COHESION_EXEMPT` at a ceiling equal to
    # its exact size, so every line added there costs the one module that has no room. Every path
    # through `load_scorer_config` assigns this field, so the default is only ever read by an
    # `Engine` that has not started — which is the same state `scorer_config_id` is in there.
    scorer_model_version_id: int | None = None
    scorer_warnings: list[str]
    _loaded_key: tuple[int, str] | None
    # Feedback-dataset capture (v0.8.0), read by the maintenance mixin's `_capture_run`. Every
    # decision about what to write lives in `netcorenoc.capture`; these two are state the engine
    # owns and the mixin marshals.
    capture: Capture
    retention: RetentionPolicy
    # Shadow mode (v0.9.0), read by `maintenance_loop`. The `Shadow` object owns the challenger and
    # every decision about it; the engine only holds it and calls it.
    shadow: Shadow

    if TYPE_CHECKING:  # pragma: no cover - declaration only; no runtime attribute exists

        async def maintenance(
            self, now: float, retention_days: float, tick: int = 0
        ) -> None:  # pragma: no cover
            """Declared, never defined. `Engine` owns the body; see this module's docstring."""
