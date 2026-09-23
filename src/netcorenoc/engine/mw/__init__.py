"""Maintenance windows: the rules, the compiled index, and the state ledger (v0.21.0).

Three modules, on the seam that matters for Part V's *"the check costs nothing"*:

* `rules.py`   — what still gets through, as pure predicates over data already in hand.
* `index.py`   — the immutable per-element snapshot the ingest path asks, and nothing else.
* `ledger.py`  — raise seen / clear seen for what a window suppressed, so a fault that outlives
                 the window surfaces when it closes.

**Nothing here touches the store.** The store modules compile rows into these types and the
maintenance loop swaps the result in; that direction is what keeps `decide()` free of I/O, and
`tests/test_maintenance_window.py` asserts it from the AST rather than from a convention.
"""
