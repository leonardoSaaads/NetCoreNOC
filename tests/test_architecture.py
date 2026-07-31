"""Architecture guards (v0.7.2): module size with a shrink-only debt allowlist, and route order.

Both are pure-stdlib, dev-only, and assert the *shape* of the tree rather than its behaviour.
They are deliberately installed **before** the v0.7.2 `api.py` split, against the unmodified
v0.7.1 tree, so every step of that split is measured by a rule that predates it and cannot have
been shaped to fit the outcome (DECISIONS #81).

`docs/architecture/MODULE-ARCHITECTURE.md` is the document these enforce.
"""

from __future__ import annotations

import re
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
    # v0.7.2 pushed this over the guard by adding `ROUTE_SCOPE`, the declaration F34 showed was
    # missing. The table belongs here — `rbac.py` is the single source of authority and the build
    # scope says so — so the honest outcome is a named debt entry rather than a trimmed comment
    # block or a table hidden somewhere it does not belong. The split seam is already visible: the
    # route/capability *tables* on one side, the capability-policy parser and resolver on the
    # other. See MODULE-ARCHITECTURE.md §5.
    "rbac.py": (436, "v0.7.4 — split the declaration tables from the policy resolver"),
    "varbind_profile.py": (417, "v0.7.4 — one extraction (the accumulator), not a package"),
}

# The names permitted to appear in `DEBT_ALLOWLIST`. Enforced by
# `test_no_module_may_join_the_allowlist`, which is the half of "only shrinks" that did not exist
# before v0.7.3: the original pair of tests caught a *stale* entry but would have let a **new** one
# through green. This set shrinks when a module is fixed and never grows.
#
# v0.7.3 removed `store.py` (and the transitional `store/_all.py` it was `git mv`-d to) and
# `main.py`. The set may only ever get smaller from here.
ALLOWLIST_MEMBERSHIP_CEILING: frozenset[str] = frozenset({"rbac.py", "varbind_profile.py"})


# Modules that are over the guard by **deliberate, permanent design**, mapped to the invariant
# that forbids splitting them. This is NOT the debt allowlist and the difference is the whole
# point (v0.7.3, DECISIONS #91):
#
#   * `DEBT_ALLOWLIST` means *"too big, will be fixed by release N"*. It carries an owner and a
#     date because someone intends to do the work.
#   * `COHESION_EXEMPT` means *"large because an invariant requires it, and there is no release in
#     which that stops being true"*. It carries **no owner and no date**, because there is no fix —
#     and the absence is asserted below, so the two can never blur into each other.
#
# Conflating them would corrupt the one guard this project has against structural drift: a debt
# entry nobody intends to pay is a promise in CI that gets quietly re-dated the first time it is
# inconvenient, which is how a ratchet becomes a comment.
#
# Five constraints, each with its own test: the reason must cite an invariant **by name** from
# MODULE-ARCHITECTURE.md §1; a module may be in one list or the other, never both; entries carry
# no owner and no fix date; an exempt module may not grow past its recorded count; and at most
# MAX_COHESION_EXEMPT entries may exist, so the escape hatch cannot become the default.
MAX_COHESION_EXEMPT = 2

COHESION_EXEMPT: dict[str, str] = {
    # Note what is NOT here: an owner, a release, a date. `engine.py` is 542 lines because the
    # whole ingest path has to be readable in one place — a reviewer must be able to confirm,
    # without following imports, that nothing on it takes a lock, does I/O, or awaits where it must
    # not. There is no release in which that stops being true, so there is nothing to schedule.
    "engine.py": (
        "ingestion is sacred (MODULE-ARCHITECTURE.md §1): the batch lock and every decision that "
        "reasons about it stay in one file, because the invariant is only auditable if the ingest "
        "path can be read without following imports"
    ),
}

# The recorded line count of each cohesion-exempt module. Separate from the reason string so the
# reason stays a sentence about an invariant and never acquires a number that looks like a target.
COHESION_EXEMPT_CEILING: dict[str, int] = {"engine.py": 542}

# The invariant names a COHESION_EXEMPT reason may cite, taken from MODULE-ARCHITECTURE.md §1.
# A reason that cites nothing in this set is an assertion nobody has had to defend.
NAMED_INVARIANTS = frozenset({"ingestion is sacred"})


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
    """No module over MAX_MODULE_LINES except allowlisted debt or a cohesion exemption."""
    over = [
        f"{name} ({count} lines)"
        for name, count in _modules().items()
        if count > MAX_MODULE_LINES and name not in DEBT_ALLOWLIST and name not in COHESION_EXEMPT
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


def test_no_module_may_join_the_allowlist() -> None:
    """The other half of "only shrinks", which until v0.7.3 nothing actually asserted.

    `test_allowlist_only_shrinks` catches an entry that is no longer *needed*. It does not catch a
    **new** entry — and "the allowlist may only shrink" is a claim about membership in both
    directions. Without this, a contributor could add a module and every guard would stay green,
    which is precisely the ratchet-becomes-a-comment failure DECISIONS #91 describes.

    `ALLOWLIST_MEMBERSHIP_CEILING` is the set of names permitted to appear. It shrinks as modules
    are fixed and it never grows: adding a name here is the visible, arguable diff.
    """
    joined = sorted(set(DEBT_ALLOWLIST) - ALLOWLIST_MEMBERSHIP_CEILING)
    assert not joined, (
        f"module(s) newly added to DEBT_ALLOWLIST: {joined}. The allowlist may only ever shrink. "
        "Split the module instead — or, if an invariant genuinely forbids splitting it, argue for "
        "COHESION_EXEMPT, which carries no owner because it admits there is no fix."
    )


# --- the cohesion exemption, and its five constraints ------------------------------------


def test_cohesion_exempt_names_only_real_modules() -> None:
    """A dead entry would silently exempt nothing while looking like it exempted something."""
    modules = _modules()
    missing = [name for name in COHESION_EXEMPT if name not in modules]
    assert not missing, f"COHESION_EXEMPT names module(s) that do not exist: {missing}"
    orphan = sorted(set(COHESION_EXEMPT_CEILING) - set(COHESION_EXEMPT))
    assert not orphan, f"COHESION_EXEMPT_CEILING has entries with no exemption: {orphan}"


def test_every_cohesion_exemption_cites_a_named_invariant() -> None:
    """Constraint 1. The reason must cite an invariant **by name** from
    MODULE-ARCHITECTURE.md §1 — not "it's cohesive", which is what everyone says about their file.
    """
    unjustified = [
        f"{name}: {reason!r}"
        for name, reason in COHESION_EXEMPT.items()
        if not any(invariant in reason.lower() for invariant in NAMED_INVARIANTS)
    ]
    assert not unjustified, (
        "COHESION_EXEMPT entries that cite no named invariant:\n  "
        + "\n  ".join(unjustified)
        + f"\n\nThe reason must name one of {sorted(NAMED_INVARIANTS)} from "
        "MODULE-ARCHITECTURE.md §1. An exemption whose justification is a feeling is a waiver."
    )


def test_no_module_is_both_debt_and_cohesion_exempt() -> None:
    """Constraint 2. The two lists mean opposite things: "will be fixed" and "will never be".

    A module in both would be claiming an owner intends to split it *and* that an invariant
    forbids splitting it. One of those is false, and the guard could not tell you which.
    """
    both = sorted(set(DEBT_ALLOWLIST) & set(COHESION_EXEMPT))
    assert not both, (
        f"module(s) in both DEBT_ALLOWLIST and COHESION_EXEMPT: {both}. Debt is temporary and has "
        "an owner; a cohesion exemption is permanent and has none. Pick one."
    )


def test_cohesion_exemptions_carry_no_owner_and_no_fix_date() -> None:
    """Constraint 3. **This is the semantic difference from the debt allowlist**, asserted.

    A `DEBT_ALLOWLIST` value is `(lines, "v0.7.4 — do the thing")`. A `COHESION_EXEMPT` value is a
    bare reason with no release and no date, because there is no release in which the invariant
    stops being true. If a reason ever names one, the entry is debt in disguise and belongs in the
    other list, where the ratchet applies to it.
    """
    dated = [
        f"{name}: {reason!r}"
        for name, reason in COHESION_EXEMPT.items()
        if re.search(r"\bv\d+\.\d+(\.\d+)?\b|\b20\d\d\b", reason)
    ]
    assert not dated, (
        "COHESION_EXEMPT entries naming a release or a date:\n  "
        + "\n  ".join(dated)
        + "\n\nAn exemption with a fix date is debt. Move it to DEBT_ALLOWLIST, where the "
        "shrink-only ratchet will hold whoever wrote it to that date."
    )


def test_cohesion_exempt_modules_have_not_grown() -> None:
    """Constraint 4. Exempt from the guard is not exempt from a ceiling.

    "This file may be large because an invariant requires it" is not "this file may grow without
    limit". The recorded count is an upper bound, exactly as it is for allowlisted debt.
    """
    modules = _modules()
    grown = [
        f"{name}: {modules[name]} lines, exempted at {recorded}"
        for name, recorded in COHESION_EXEMPT_CEILING.items()
        if name in modules and modules[name] > recorded
    ]
    assert not grown, (
        "cohesion-exempt module(s) grew:\n  "
        + "\n  ".join(grown)
        + "\n\nThe invariant justifies the current size, not any future size. Take the addition "
        "somewhere else, or argue the new number on its own merits."
    )


def test_cohesion_exempt_is_capped() -> None:
    """Constraint 5. The escape hatch may not become the default.

    Two is enough for a project this size to express "large by design" honestly. A third entry
    means either the rule is wrong or someone is using this list to avoid an argument, and both
    of those deserve a conversation rather than a green build.
    """
    assert len(COHESION_EXEMPT) <= MAX_COHESION_EXEMPT, (
        f"COHESION_EXEMPT holds {len(COHESION_EXEMPT)} entries, over the cap of "
        f"{MAX_COHESION_EXEMPT}: {sorted(COHESION_EXEMPT)}"
    )


def test_cohesion_exempt_modules_actually_need_the_exemption() -> None:
    """Guard the guard: an entry for a module that now fits is stale and must be deleted.

    The same rule `test_allowlist_only_shrinks` applies to debt. A list that keeps entries it no
    longer needs stops describing the tree.
    """
    modules = _modules()
    stale = [
        f"{name} ({modules[name]} lines)"
        for name in COHESION_EXEMPT
        if name in modules and modules[name] <= MAX_MODULE_LINES
    ]
    assert not stale, (
        "COHESION_EXEMPT entries that are now within the guard — delete them:\n  "
        + "\n  ".join(stale)
    )


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
