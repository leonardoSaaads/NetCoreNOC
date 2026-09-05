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

# Infrastructure first, then the twelve route groups in the order `create_app` registers them.
#
# **v0.16.2: paths relative to the package, not bare basenames** (DECISIONS #278, F98). The route
# modules moved to `api/routes/`, and the walk below moved with them — see `modules()`.
MODULE_ORDER: tuple[str, ...] = (
    "__init__.py",
    "context.py",
    "models.py",
    "governance_cache.py",
    "perimeter.py",
    "declare.py",
    "app.py",
    "routes/__init__.py",
    "routes/static.py",
    "routes/auth.py",
    "routes/read.py",
    # v0.16.0: the five operator gestures, in the order `create_app` registers them — the three
    # that assert something about a grouping, then the two that assert nothing about one.
    "routes/lifecycle.py",
    "routes/annotate.py",
    "routes/operate.py",
    "routes/admin.py",
    "routes/scorer.py",
    "routes/promotion.py",
    "routes/governance.py",
    "routes/audit.py",
    "routes/events.py",
)

#: **The floor a subset must not be able to clear** (F98).
#:
#: It was 60 000, chosen when `api.py` was 79 179 bytes in one file — and by v0.16.1 the seven
#: NON-route modules were 74 398 characters on their own. So a walk that lost every route module
#: would still have cleared the floor by 24 %, and all four scanning guards would have gone on
#: passing over a corpus containing no routes at all. That is exactly the move v0.16.2 makes, and
#: it is why both halves are repaired here rather than only the walk: a floor a subset can clear
#: is not a floor.
#:
#: 150 000 is above the machinery (74 398) plus any one route module, and comfortably below the
#: real total, so it fails on a walk that lost the routes and does not fail on an ordinary edit.
MIN_SOURCE_CHARS = 150_000


def modules() -> list[Path]:
    """Every ``.py`` in the package **and its subpackages**, in `MODULE_ORDER`.

    **`rglob`, not `glob`** (F98). The non-recursive walk this used until v0.16.2 would have
    dropped all twelve route modules the moment they moved into `api/routes/` — silently, because
    the unplaced-module check below compares against the same walk, so a module out of the walk's
    reach is not *unplaced*, it is *invisible*.
    """
    present = {str(path.relative_to(PKG_DIR)) for path in PKG_DIR.rglob("*.py")}
    unplaced = sorted(present - set(MODULE_ORDER))
    if unplaced:
        raise AssertionError(
            f"module(s) under netcorenoc/api/ missing from apisource.MODULE_ORDER: {unplaced}. "
            "Add them where they belong in the registration order, so the source-scanning "
            "guards keep covering the whole package."
        )
    return [PKG_DIR / name for name in MODULE_ORDER if name in present]


# The scanning guards slice a handler body as "from this decorator to the next one". The last
# handler in the corpus has no next one, so the concatenation ends with a decorator-shaped
# terminator: the final slice then runs to the end of the real source, which is exactly the same
# tight body every other slice gets. Without it the guard raises ValueError on whichever module
# happens to sort last — a guard that breaks on a reordering it should not care about.
TERMINATOR = "\n    @route.__end_of_package__\n"


def api_source() -> str:
    """The concatenated source of the `netcorenoc.api` package, terminator included."""
    text = "\n".join(path.read_text(encoding="utf-8") for path in modules())
    assert len(text) >= MIN_SOURCE_CHARS, (
        f"the api package source is only {len(text)} characters — a scanning guard running "
        "against this would be vacuous"
    )
    return text + TERMINATOR
