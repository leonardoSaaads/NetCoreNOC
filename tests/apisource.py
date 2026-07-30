"""The source text of the whole `netcorenoc.api` package, for the tests that scan it (v0.7.2).

Four v0.7.1 guards read `api.py`'s **text** rather than its behaviour: F28 (no role comparison
outside `rbac.py`), F34 (every mutating route below admin resolves scope), F39 (every mutating
handler reaches the transaction helper), and the scorer-panel caveat wording. When `api.py` became
the package `api/` in v0.7.2, `inspect.getsource(netcorenoc.api)` started returning only
`__init__.py` — so all four would have kept passing against almost no source at all, which is the
worst way for a guard to fail. This module gives them back their corpus (DECISIONS #84).

Two properties make it safe to rely on:

* **Order is fixed and meaningful.** Modules are concatenated infrastructure-first and then in
  *route registration order*, which is the order `create_app` calls the `register()` functions.
  The scanning tests slice a handler body as "from this decorator to the next one", so the
  concatenation order has to be the registration order for those slices to mean what they meant
  when everything was one file.
* **An unplaced module is an error, not a silent omission.** A new module under `api/` fails here
  until someone decides where it belongs in the order — the alternative is a guard that quietly
  stops covering the file somebody just added.
"""

from __future__ import annotations

from pathlib import Path

import netcorenoc.api

PKG_DIR = Path(netcorenoc.api.__file__).resolve().parent

# Infrastructure first, then the nine route groups in the order `create_app` registers them.
MODULE_ORDER: tuple[str, ...] = (
    "__init__.py",
    "context.py",
    "models.py",
    "perimeter.py",
    "app.py",
    "routes_static.py",
    "routes_auth.py",
    "routes_read.py",
    "routes_operate.py",
    "routes_admin.py",
    "routes_scorer.py",
    "routes_governance.py",
    "routes_audit.py",
    "routes_events.py",
)

# `api.py` was 79 179 bytes at v0.7.1. A concatenation that lost most of it would make every
# scanning assertion vacuous, so the floor is asserted rather than assumed.
MIN_SOURCE_CHARS = 60_000


def modules() -> list[Path]:
    """Every ``.py`` in the package, in `MODULE_ORDER`. Raises on one that is not placed."""
    present = {path.name for path in PKG_DIR.glob("*.py")}
    unplaced = sorted(present - set(MODULE_ORDER))
    if unplaced:
        raise AssertionError(
            f"module(s) under netcorenoc/api/ missing from apisource.MODULE_ORDER: {unplaced}. "
            "Add them where they belong in the registration order, so the source-scanning "
            "guards keep covering the whole package."
        )
    return [PKG_DIR / name for name in MODULE_ORDER if name in present]


def api_source() -> str:
    """The concatenated source of the `netcorenoc.api` package."""
    text = "\n".join(path.read_text(encoding="utf-8") for path in modules())
    assert len(text) >= MIN_SOURCE_CHARS, (
        f"the api package source is only {len(text)} characters — a scanning guard running "
        "against this would be vacuous"
    )
    return text
