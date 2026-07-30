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

There are **no method declarations here at all**, which is a consequence of DECISIONS #90 rather
than luck: once `maintenance()` stays in `engine.py`, the cross-boundary call graph has zero
mixin→`Engine` and zero mixin→mixin edges, so nothing needs a signature it does not own.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from netcorenoc.correlate import Correlator
from netcorenoc.learn import Learner
from netcorenoc.rootcause import Precedence
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
    scorer_warnings: list[str]
    _loaded_key: tuple[int, str] | None
