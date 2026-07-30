"""Architecture guards (v0.7.2): module size with a shrink-only debt allowlist, and route order.

Both are pure-stdlib, dev-only, and assert the *shape* of the tree rather than its behaviour.
They are deliberately installed **before** the v0.7.2 `api.py` split, against the unmodified
v0.7.1 tree, so every step of that split is measured by a rule that predates it and cannot have
been shaped to fit the outcome (DECISIONS #81).

`docs/architecture/MODULE-ARCHITECTURE.md` is the document these enforce.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from netcorenoc.store import Store

import authutil

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG = REPO_ROOT / "src" / "netcorenoc"

# --- the module-size guard ---------------------------------------------------------------

# `MODULE-ARCHITECTURE.md` §2: a module owns one noun or one decision. Over ~250 lines is a
# smell; over this many it is debt with a named owner.
MAX_MODULE_LINES = 400

# Modules over the limit today, each mapped to its measured line count and the release that owns
# the fix. Two rules make this a ratchet rather than a comment, and both are asserted below:
#
#   * the allowlist may only ever SHRINK — a module that drops to or below MAX_MODULE_LINES must
#     be removed from it, so stale exemptions cannot accumulate;
#   * an allowlisted module may not GROW — its recorded count is an upper bound, so debt is
#     allowed to exist but is not allowed to compound.
#
# Adding an entry here is therefore a visible, arguable diff, which is the entire point.
DEBT_ALLOWLIST: dict[str, tuple[int, str]] = {
    "api.py": (1752, "v0.7.2 — the subject of this release; becomes the api/ package"),
    "store.py": (1512, "v0.7.3 — split by domain along its own section comments"),
    "main.py": (1079, "v0.7.3 — the maintenance loop, gap tracker and process runner leave Engine"),
    "shaping.py": (476, "v0.7.4 — two axes in one file: field shaping and NE scoping"),
    "varbind_profile.py": (417, "v0.7.4 — one extraction (the accumulator), not a package"),
}


def _modules() -> dict[str, int]:
    """Every ``.py`` under ``src/netcorenoc``, keyed by its package-relative path, with its
    line count."""
    return {
        str(path.relative_to(PKG)): len(path.read_text(encoding="utf-8").splitlines())
        for path in sorted(PKG.rglob("*.py"))
    }


def test_modules_discovered() -> None:
    """Guard the guard: a glob that matched nothing would make every assertion below vacuous."""
    modules = _modules()
    assert len(modules) >= 20, modules
    assert "rbac.py" in modules


def test_no_module_exceeds_the_size_guard() -> None:
    """No module over MAX_MODULE_LINES except the explicitly allowlisted debt."""
    over = [
        f"{name} ({count} lines)"
        for name, count in _modules().items()
        if count > MAX_MODULE_LINES and name not in DEBT_ALLOWLIST
    ]
    assert not over, (
        f"module(s) over the {MAX_MODULE_LINES}-line guard and not on the debt allowlist:\n  "
        + "\n  ".join(over)
        + "\n\nSplit the module (MODULE-ARCHITECTURE.md §2), or add it to DEBT_ALLOWLIST with the "
        "release that will fix it — which is a deliberate, reviewable decision, not a formality."
    )


def test_allowlisted_modules_have_not_grown() -> None:
    """Debt may exist; it may not compound. Each entry's recorded count is an upper bound."""
    modules = _modules()
    grown = [
        f"{name}: {modules[name]} lines, allowlisted at {recorded}"
        for name, (recorded, _owner) in DEBT_ALLOWLIST.items()
        if name in modules and modules[name] > recorded
    ]
    assert not grown, (
        "allowlisted module(s) grew:\n  "
        + "\n  ".join(grown)
        + "\n\nThe allowlist is a ceiling, not a licence. Take the addition somewhere else."
    )


def test_allowlist_only_shrinks() -> None:
    """An entry that no longer needs an exemption must be removed, so the list cannot go stale."""
    modules = _modules()
    stale = [
        f"{name} ({modules[name]} lines)"
        for name in DEBT_ALLOWLIST
        if name in modules and modules[name] <= MAX_MODULE_LINES
    ]
    assert not stale, (
        "DEBT_ALLOWLIST entries that are now within the guard — delete them:\n  "
        + "\n  ".join(stale)
        + "\n\nThe allowlist may only ever shrink."
    )


def test_allowlist_names_only_real_modules() -> None:
    """A renamed or deleted module leaves a dead entry that would silently exempt nothing."""
    modules = _modules()
    missing = [name for name in DEBT_ALLOWLIST if name not in modules]
    assert not missing, f"DEBT_ALLOWLIST names module(s) that do not exist: {missing}"


def test_the_package_is_at_most_one_level_deep() -> None:
    """MODULE-ARCHITECTURE.md §9: one level of nesting, where earned. Never two."""
    deep = [
        str(path.relative_to(PKG))
        for path in PKG.rglob("*.py")
        if len(path.relative_to(PKG).parts) > 2
    ]
    assert not deep, f"modules nested more than one level deep: {deep}"


# --- the route-order parity baseline -----------------------------------------------------

# The ordered (method, path) list on the app built at v0.7.1, recorded in
# docs/gates/v0.7.2-phase-0.md §5.1. FastAPI matches paths in **registration order**, so this
# sequence is behaviour: `/api/situations` before `/api/situations/{sid}`, and
# `/api/scorer/preview` before `/api/scorer/rollback` before `/api/scorer`'s own POST, all decide
# which handler a request reaches. The v0.7.2 split calls its nine `register()` functions in this
# order for exactly that reason.
ROUTE_ORDER_BASELINE: list[tuple[str, str]] = [
    ("GET", "/openapi.json"),
    ("GET", "/healthz"),
    ("GET", "/readyz"),
    ("GET", "/"),
    ("GET", "/app.js"),
    ("GET", "/style.css"),
    ("GET", "/vendor/d3.v7.min.js"),
    ("GET", "/.well-known/security.txt"),
    ("POST", "/api/login"),
    ("POST", "/api/logout"),
    ("GET", "/api/me"),
    ("POST", "/api/password"),
    ("GET", "/api/stats"),
    ("GET", "/api/graph"),
    ("GET", "/api/classes"),
    ("GET", "/api/situations"),
    ("GET", "/api/situations/{sid}"),
    ("GET", "/api/timeline"),
    ("GET", "/api/entities"),
    ("GET", "/api/entities/{ne_id}"),
    ("GET", "/api/state-clears"),
    ("POST", "/api/entities/{ne_id}/reset"),
    ("POST", "/api/profiles/{ne_id}/reset"),
    ("POST", "/api/situations/{sid}/feedback"),
    ("POST", "/api/labels"),
    ("POST", "/api/situations/{sid}/close"),
    ("GET", "/api/users"),
    ("POST", "/api/users"),
    ("POST", "/api/users/{uid}/role"),
    ("DELETE", "/api/users/{uid}"),
    ("GET", "/api/tokens"),
    ("POST", "/api/tokens"),
    ("DELETE", "/api/tokens/{tid}"),
    ("GET", "/api/config"),
    ("POST", "/api/config"),
    ("GET", "/api/scorer"),
    ("POST", "/api/scorer/preview"),
    ("POST", "/api/scorer"),
    ("POST", "/api/scorer/rollback"),
    ("GET", "/api/rbac"),
    ("POST", "/api/rbac"),
    ("GET", "/api/scope"),
    ("POST", "/api/scope"),
    ("GET", "/api/quarantine"),
    ("GET", "/api/audit"),
    ("GET", "/api/audit/export"),
    ("POST", "/api/audit/prune"),
    ("GET", "/api/events"),
]


def route_order(app: object) -> list[tuple[str, str]]:
    """The ordered (method, path) list on a built app, HEAD/OPTIONS excluded."""
    out: list[tuple[str, str]] = []
    for route in app.routes:  # type: ignore[attr-defined]
        path = getattr(route, "path", None)
        if path is None:
            continue
        methods = sorted(
            m for m in (getattr(route, "methods", set()) or set()) if m not in ("HEAD", "OPTIONS")
        )
        out.extend((method, path) for method in methods)
    return out


async def test_route_table_order_is_unchanged(store: Store) -> None:
    """The route table is identical, in order, to the v0.7.1 baseline.

    A reordering is not cosmetic: FastAPI resolves the first matching route, so moving
    `/api/situations/{sid}` above `/api/situations` would silently change which handler answers.
    This is the test that makes "we only moved code between files" mean something.
    """
    _engine, _queue, app = await authutil.make_env(store)
    assert route_order(app) == ROUTE_ORDER_BASELINE


async def test_route_order_baseline_has_no_duplicates(store: Store) -> None:
    """A duplicated (method, path) would make the comparison above pass while shadowing a
    handler — the second registration is dead code the router never reaches."""
    _engine, _queue, app = await authutil.make_env(store)
    live = route_order(app)
    duplicates = sorted({entry for entry in live if live.count(entry) > 1})
    assert not duplicates, f"duplicate (method, path) registrations: {duplicates}"


@pytest.mark.parametrize("entry", ROUTE_ORDER_BASELINE, ids=lambda e: f"{e[0]} {e[1]}")
def test_every_baseline_route_is_uniquely_named(entry: tuple[str, str]) -> None:
    """The baseline itself is well-formed: 48 distinct entries, no accidental repetition."""
    assert ROUTE_ORDER_BASELINE.count(entry) == 1
