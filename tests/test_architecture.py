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
DEBT_ALLOWLIST: dict[str, tuple[int, str]] = {}

# The names permitted to appear in `DEBT_ALLOWLIST`. Enforced by
# `test_no_module_may_join_the_allowlist`, which is the half of "only shrinks" that did not exist
# before v0.7.3: the original pair of tests caught a *stale* entry but would have let a **new** one
# through green. This set shrinks when a module is fixed and never grows.
#
# v0.7.3 removed `store.py` (and the transitional `store/_all.py` it was `git mv`-d to) and
# `main.py`. v0.7.4 removed the last three — `shaping.py`, `rbac.py` and `varbind_profile.py` — so
# the set is now **empty**, and any module added to DEBT_ALLOWLIST fails immediately.
#
# An empty allowlist that nothing defends is a coincidence, not a guarantee. Both direction tests
# are kept for exactly that reason: `test_allowlist_only_shrinks` catches a stale entry, and
# `test_no_module_may_join_the_allowlist` catches a new one. The second is the one that matters
# now, because the first has nothing left to check.
ALLOWLIST_MEMBERSHIP_CEILING: frozenset[str] = frozenset()


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
    "engine/operate/engine.py": (
        "ingestion is sacred (MODULE-ARCHITECTURE.md §1): the batch lock and every decision that "
        "reasons about it stay in one file, because the invariant is only auditable if the ingest "
        "path can be read without following imports"
    ),
}

# The recorded line count of each cohesion-exempt module. Separate from the reason string so the
# reason stays a sentence about an invariant and never acquires a number that looks like a target.
# v0.8.0: 542 -> 580. Raised deliberately, argued in DECISIONS #108, and paid for by
# `test_the_engine_holds_no_capture_logic` below — the ceiling is a number, and what the exemption
# actually means is that invariant. The 38 lines are call sites and two attribute assignments;
# every capture decision lives in `capture.py`. A raise without a compensating control is how a
# ratchet becomes a comment, which is the failure this whole section exists to prevent.
COHESION_EXEMPT_CEILING: dict[str, int] = {"engine/operate/engine.py": 580}

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
    assert "crosscutting/rbac/tables.py" in modules


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


#: The nesting budget, and what each level is allowed to say. Until v0.15.1 this was one level —
#: "where earned, never two" — and it was earned twice, by `api/` and by `store/`. #207 spends the
#: second level on the layer, so a path now reads `<layer>/<domain>/<module>.py` and there is no
#: third thing it may say (DECISIONS #210).
MAX_NESTING = 2


def test_the_package_is_at_most_two_levels_deep() -> None:
    """`architecture.md`: two levels of nesting, where earned. Never three.

    The budget is spent, deliberately and completely: level one is the layer, level two is the
    domain inside `engine/` (or the package inside `crosscutting/`). A third would be a directory
    nobody can name — which is how a tree stops being a description and becomes a filing habit.
    """
    deep = [
        str(path.relative_to(PKG))
        for path in PKG.rglob("*.py")
        if len(path.relative_to(PKG).parts) > MAX_NESTING + 1
    ]
    assert not deep, f"modules nested more than {MAX_NESTING} levels deep: {deep}"


def test_the_nesting_budget_is_spent_rather_than_merely_available() -> None:
    """The control on the guard above: a limit nothing reaches is a limit nobody has tested.

    Without this, `MAX_NESTING` could be raised to any number and every assertion would still pass.
    """
    depths = {len(path.relative_to(PKG).parts) for path in PKG.rglob("*.py")}
    assert max(depths) == MAX_NESTING + 1, (
        f"the deepest module sits at {max(depths) - 1} level(s) of nesting and the budget is "
        f"{MAX_NESTING}. A budget with headroom nothing uses is a number, not a rule."
    )


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
    ("GET", "/app/api.js"),
    ("GET", "/app/context.js"),
    ("GET", "/app/destructive.js"),
    ("GET", "/app/dom.js"),
    ("GET", "/app/format.js"),
    ("GET", "/app/login.js"),
    ("GET", "/app/parameters.js"),
    ("GET", "/app/registry.js"),
    ("GET", "/app/router.js"),
    ("GET", "/app/session.js"),
    ("GET", "/app/shell.js"),
    ("GET", "/app/sidebar.js"),
    ("GET", "/app/store.js"),
    ("GET", "/app/theme.js"),
    ("GET", "/app/views/account.js"),
    ("GET", "/app/views/audit.js"),
    ("GET", "/app/views/classes.js"),
    ("GET", "/app/views/corpus.js"),
    ("GET", "/app/views/entities.js"),
    ("GET", "/app/views/facts.js"),
    ("GET", "/app/views/governance.js"),
    ("GET", "/app/views/graph.js"),
    ("GET", "/app/views/labelling.js"),
    ("GET", "/app/views/model.js"),  # v0.14.0
    ("GET", "/app/views/overview.js"),
    ("GET", "/app/views/promotion.js"),
    ("GET", "/app/views/quarantine.js"),
    ("GET", "/app/views/retention.js"),
    ("GET", "/app/views/scorer.js"),
    ("GET", "/app/views/settings.js"),
    ("GET", "/app/views/situations.js"),
    ("GET", "/app/views/timeline.js"),
    ("GET", "/app/views/tokens.js"),
    ("GET", "/app/views/users.js"),
    ("GET", "/app/views/verdict.js"),  # v0.14.0
    ("GET", "/app/widgets.js"),
    ("GET", "/vendor/d3.v7.min.js"),
    ("GET", "/vendor/preact-10.29.8.module.js"),
    ("GET", "/vendor/htm-3.1.1.module.js"),
    ("GET", "/style.css"),
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
    ("GET", "/api/dataset/retention"),
    ("POST", "/api/dataset/retention"),
    ("GET", "/api/scorer"),
    ("POST", "/api/scorer/preview"),
    ("POST", "/api/scorer"),
    ("POST", "/api/scorer/rollback"),
    ("GET", "/api/promotion"),
    ("POST", "/api/promotion"),
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


#: The `/api` subsequence of the v0.7.1 baseline, unchanged through every release since.
#:
#: v0.13.0 appended 36 static module paths to the table and v0.14.0 inserted two more
#: (`app/views/model.js`, `app/views/verdict.js`), which moved the FULL list above without moving
#: anything that can shadow a handler — a static asset is an exact literal with no template and
#: cannot capture another route's requests. So the table is re-pinned **and** the property it
#: actually protects is asserted separately, rather than the whole comparison being loosened.
#:
#: This list is **derived** from the one above rather than transcribed, so a v0.14.0 edit that had
#: touched an `/api` route would have moved both and the second assertion would have caught it.
API_ORDER_BASELINE: list[tuple[str, str]] = [
    entry for entry in ROUTE_ORDER_BASELINE if entry[1].startswith("/api")
]


async def test_the_api_route_order_is_unchanged_by_the_ui_rewrite(store: Store) -> None:
    """The half of the ordering guard that decides which handler answers.

    `/api/situations` must stay above `/api/situations/{sid}` and `/api/scorer/preview` above the
    bare `POST /api/scorer`; a template that moves above its literal silently changes behaviour.
    v0.13.0 touched no `/api` route and neither does v0.14.0 — three scorer kinds, a repaired
    promotion gate and two new screens moved the served API surface by exactly nothing. This states
    that as a fact rather than as an intention.
    """
    _engine, _queue, app = await authutil.make_env(store)
    live = [entry for entry in route_order(app) if entry[1].startswith("/api")]
    assert live == API_ORDER_BASELINE
    assert len(live) == 44, f"the /api surface is {len(live)} pairs; v0.13.0 adds no route"


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


# --- v0.8.0: what the engine's exemption actually means -----------------------------------

# SQL fragments and dataset table names that must never appear in `engine.py`. The point is not the
# strings: it is that "engine.py may be large" has always meant "the ingest *reasoning* lives in one
# place", and a release that let dataset persistence accumulate there would satisfy the line count
# while destroying the property the line count is a proxy for.
# Deliberately SQL-shaped rather than bare table names: `engine.py` legitimately *calls*
# `_capture_run`, and a needle matching that would be testing the method's spelling instead of the
# property. What must never appear is a statement — a table reached, not a helper named.
_CAPTURE_LEAKS: tuple[str, ...] = (
    "INSERT INTO",
    "DELETE FROM",
    "UPDATE dataset",
    "FROM dataset_",
    "INTO dataset_",
    "FROM capture_run",
    "INTO capture_run",
    "feedback_member",
)


def test_the_engine_holds_no_capture_logic() -> None:
    """**The control that pays for v0.8.0's ceiling raise (DECISIONS #108).**

    `engine.py`'s ceiling went from 542 to 565 because capture needs call sites and a call site is,
    by definition, at the call. That is only acceptable if the *reason* for the exemption is
    preserved — so this asserts the thing the number is a proxy for: **no dataset SQL, no table
    name, no capture decision in `engine.py`.** It may call into `netcorenoc.capture` and nothing
    more.

    Without this, the raise would be exactly the pattern the `COHESION_EXEMPT` comment warns about:
    a bound relaxed the first time it was inconvenient. With it, the release ends with a *stronger*
    structural guarantee than it started with — the previous rule bounded size alone.
    """
    source = (PKG / "engine" / "operate" / "engine.py").read_text(encoding="utf-8")
    leaks = [needle for needle in _CAPTURE_LEAKS if needle in source]
    assert not leaks, (
        f"engine.py contains capture/persistence logic: {leaks}\n\n"
        "The COHESION_EXEMPT entry covers the ingest reasoning, not any code that lands nearby. "
        "Dataset persistence belongs in netcorenoc/capture.py (decisions) and "
        "netcorenoc/store/dataset.py (SQL); engine.py gets a call site."
    )


def test_the_capture_module_is_the_one_that_grew() -> None:
    """The other half: the extraction actually happened, rather than the code being deleted.

    A guard that only forbids SQL in `engine.py` is satisfied by capture not existing. This asserts
    the code is somewhere — and somewhere under the ordinary 400-line module guard, which
    `test_no_module_is_too_large` already enforces for both files.
    """
    modules = _modules()
    assert "capture.py" in modules, "netcorenoc/capture.py is missing"
    assert "store/dataset.py" in modules, "netcorenoc/store/dataset.py is missing"
    assert modules["capture.py"] > 100, "capture.py is too small to hold the capture logic"
    assert "capture.py" not in COHESION_EXEMPT, "capture.py must live under the ordinary guard"


# --- the JavaScript module-size guard (v0.13.0) -------------------------------------------------
#
# `MODULE-ARCHITECTURE.md` §2 — *a module owns one noun or one decision* — was written about Python
# and enforced only there. v0.12.0's Phase 0 measured what that omission cost: `ui/app.js` was
# 52 738 bytes with 55 top-level functions, and no guard in this repository had an opinion about it.
#
# The rule is the same rule and the number is the same number. Applying it to JavaScript is what
# stops v0.13.0 from replacing one 52 KB file with one 52 KB file in a new syntax — which would
# pass every other test here (Part XI: *replace the shape, not just the file*).
#
# **The vendored assets are exempt and the exemption is not a judgement call**: they are
# third-party bytes pinned by SHA-256, so holding them to this project's conventions would mean
# either editing them (breaking the pin, which is the point of the pin) or arguing about it once
# per asset. `test_the_javascript_exemption_is_only_vendor` asserts the exemption is exactly that
# set and nothing else — F51's lesson, which was a guard whose scope silently widened.

JS_ROOT = PKG / "ui"


def javascript_modules() -> dict[str, int]:
    """`{relative path: line count}` for every JavaScript module this project wrote."""
    return {
        str(path.relative_to(JS_ROOT)): len(path.read_text(encoding="utf-8").splitlines())
        for path in sorted(JS_ROOT.rglob("*.js"))
        if "vendor" not in path.parts
    }


def test_no_javascript_module_is_over_the_size_guard() -> None:
    """The Python guard's number, applied to the UI (Part VII.7).

    If this fails, split the module along a noun — do not raise the limit. There is deliberately no
    debt allowlist on this side: the Python one exists because it inherited debt, and this guard is
    installed against a tree that has none.
    """
    modules = javascript_modules()
    assert len(modules) >= 20, f"only {len(modules)} modules found; this guard is not guarding"
    oversize = {name: lines for name, lines in modules.items() if lines > MAX_MODULE_LINES}
    assert not oversize, (
        f"JavaScript modules over {MAX_MODULE_LINES} lines: {oversize}. "
        f"Split along a noun; do not raise the limit."
    )


def test_the_javascript_exemption_is_only_vendor() -> None:
    """Guard the guard: the skip is scoped to `ui/vendor/` and to nothing else.

    F51 was a guard scoped by a literal string — `_SKIP_DIRS` excluded `.venv` by name, so a
    virtualenv called anything else stopped being skipped. This is the mirror image: a skip that
    quietly widened would stop the guard *finding* things. So the exempt set is asserted to be
    exactly the vendored files, by counting both sides.
    """
    everything = set(JS_ROOT.rglob("*.js"))
    checked = {JS_ROOT / name for name in javascript_modules()}
    exempt = everything - checked
    assert exempt, "nothing is exempt, so this test is asserting nothing"
    assert all(path.parent.name == "vendor" for path in exempt), (
        f"the exemption reaches outside ui/vendor/: {sorted(str(p) for p in exempt)}"
    )
    assert len(exempt) == 3, f"expected exactly the three vendored assets, found {len(exempt)}"


def test_the_ui_entry_point_only_boots() -> None:
    """`app.js` is an entry point, and an entry point that grows is a `main()` becoming a program.

    Named separately from the size guard because the limit that matters here is far below 400: this
    file resolves a session, mounts one of two components, and opens the update stream.
    """
    lines = javascript_modules()["app.js"]
    assert lines < 160, f"the entry point is {lines} lines; it should boot and nothing else"


def test_every_javascript_module_opens_with_a_block_comment() -> None:
    """`MODULE-ARCHITECTURE.md` §2's other half: a module states the decision it owns.

    The Python side gets this from the docstring guard. The UI had no equivalent, and the file this
    release deleted opened with eleven lines of comment covering 52 KB of code.
    """
    for path in sorted(JS_ROOT.rglob("*.js")):
        if "vendor" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        assert source.lstrip().startswith("/*"), (
            f"{path.name} does not open with a block comment saying what it owns"
        )
        header = source.split("*/", 1)[0]
        assert len(header.split()) >= 20, (
            f"{path.name}'s header is {len(header.split())} words; it does not explain anything"
        )


# --- prime directive 1, as a test rather than as a command someone remembers to run --------------

#: SHA-256 of the five modules on the trap path, at **v0.13.0** — the release this one branched
#: from. v0.14.0's build prompt makes their byte-identity its first non-negotiable:
#:
#:   > `correlate.py`, `engine.py`, `receiver.py`, `capture.py`, `learn.py` byte-identical at the
#:   > end, verified by hash.
#:
#: Every gate document in this release quotes those hashes. **Quoting them is not a guard**: a hash
#: a human runs `sha256sum` for at the end of a phase catches a change only if the human remembers,
#: and the phase where they would most want to forget is the phase that found a defect in one of
#: these files. This release is that phase — F58 is a finding about `learn.py`, measured with the
#: evidence on screen, and left unfixed. This table is what made leaving it unfixed a property of
#: the tree rather than a promise in a document.
#:
#: Pinned by content, not by `git diff`, so it holds against a working tree with no history: a
#: reformat, an import reordering, a comment fix and a semantic change are all the same event here,
#: which is right, because "did anything at all move" is the question prime directive 1 asks.
#:
#: **When a later release legitimately changes one of these**, it updates this table in the same
#: commit — the reviewable-line-in-a-diff discipline `UI_HASHES` has used since v0.11.0.
TRAP_PATH_HASHES: dict[str, str] = {
    "capture.py": "8676482c1965a97d3720b642e62451ecba8ed5317fae9f779ed1b30be47dea1e",
    "correlate.py": "48767428a93ab511e09a07e0c6d40c9d3c0fc39fee33ec95625b49be722a4845",
    "engine/operate/engine.py": "cfd8bc06afd712fcaf34540fbd7637e53116cb1f829ae03188adfac5666318c2",
    "learn.py": "7545e7d9d33563b9fa832ca5e958f0ef24337afc540f6c5b9ad1a91c7fcddf63",
    "receiver.py": "139611c9f69bf54e87d8099cbfa3eb4820355b2f758106c1866dc4bbc8bdb441",
}


#: The same five modules, hashed with **every import statement removed** — and unlike the table
#: above, these do not change at all in v0.15.1.
#:
#: The brief for this release states that a move breaks the pin above "on path, not on content".
#: That is not so, and the difference matters: a moved module's own imports are rewritten, so its
#: bytes change too, and the pin above therefore has to be recomputed in each move commit. A pin
#: that is recomputed is a pin that absorbs whatever else came with the change.
#:
#: So the claim v0.15.1 can actually make is this one: strip the imports and **nothing moved**.
#: Same idea as `tools/evidence/move_census.py`, which makes it for all 56 moved files; here it is
#: a permanent test for the five that matter most, so a later release cannot change a trap-path
#: module's body while updating the raw hash in the same breath and call it a move.
TRAP_PATH_BODY_HASHES: dict[str, str] = {
    "capture.py": "103c97353c1ad55560c4819ada8bf7adb89590a2af42b33f9f6a12c7afdc37a8",
    "correlate.py": "e2bfcf768b0073ce70ce47166dde8b4fc022a0b733f252e85466d32eedeaebda",
    "engine/operate/engine.py": "d666eb915bb1b3ebc083d66d28f678ceaa86fc527a15206f18d86ad042a5bb5f",
    "learn.py": "8d07a7b12aa1afe09b64e9a1e78cdb3094be3752881514a1bf61db807c2fb4ba",
    "receiver.py": "f8290c1b99a2803e519c10e247a8041896a1fd0dd9e7c6f4192ec455eacfd5d6",
}


def _body(source: str) -> str:
    """Source with every `import` / `from … import` statement removed, by `ast` span.

    A second copy of `tools/evidence/move_census.py`'s `strip_imports`, deliberately: that one is
    a release gate somebody runs, this one runs on every `make qa`, and a test that reached into
    `tools/evidence/` to borrow eight lines would couple a permanent guard to a one-release script.
    """
    import ast

    tree = ast.parse(source)
    drop: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            drop.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return "\n".join(
        line for number, line in enumerate(source.splitlines(), start=1) if number not in drop
    )


def test_the_trap_path_bodies_are_unchanged_by_the_move() -> None:
    """**What v0.15.1 claims about the trap path**, and it is stronger than the raw pin.

    The five modules' imports were rewritten and their paths changed. Everything else — every
    decision on the ingest path, every line a reviewer would have to read to confirm the batch
    lock is respected — is byte-identical to the tree this release started from.
    """
    import hashlib

    moved = []
    for name, expected in sorted(TRAP_PATH_BODY_HASHES.items()):
        path = next(p for p in PKG.rglob(name))
        actual = hashlib.sha256(_body(path.read_text(encoding="utf-8")).encode("utf-8")).hexdigest()
        if actual != expected:
            moved.append(f"  {name}\n    pinned: {expected}\n    actual: {actual}")
    assert not moved, (
        "a trap-path module changed beyond its imports:\n"
        + "\n".join(moved)
        + "\n\nA move rewrites imports and nothing else. If a later release legitimately changes "
        "one of these bodies, it updates this table in the same commit — which is a reviewable "
        "line in a diff rather than something that happened while the raw hash was being bumped."
    )


def test_the_body_strip_is_load_bearing() -> None:
    """The control. A strip that removed everything, or nothing, would make the pin meaningless."""
    sample = (
        "import os\n"
        "from x import (\n    y,\n    z,\n)\n"  # a parenthesised import, which spans four lines
        "\n"
        "VALUE = 1\n"
        'PROSE = """this sentence contains the word import and is not one"""\n'
    )
    stripped = _body(sample).splitlines()
    assert not [line for line in stripped if line.startswith(("import ", "from "))], (
        f"the strip left an import statement behind: {stripped}"
    )
    assert "    y," not in stripped, "the strip stopped at the first line of a wrapped import"
    assert "VALUE = 1" in stripped, "the strip removed code, not just imports"
    # …and the half a line-based filter gets wrong: prose that merely says the word.
    assert any("word import and is not one" in line for line in stripped), (
        "the strip removed a string literal that mentions imports — which is what makes an `ast` "
        "span the right instrument and a pattern the wrong one"
    )
    assert _body("VALUE = 1\n") == "VALUE = 1", "a file with no imports must survive intact"


def test_the_trap_path_is_byte_identical_to_the_release_this_one_branched_from() -> None:
    """**Prime directive 1**, measured on every run rather than at the end of a phase.

    The five modules are named individually. A glob over "the ingest path" would be a claim about
    a boundary nobody drew, and the boundary is the point: these are the files a trap actually
    passes through, and a release about *models* has no business inside any of them.
    """
    import hashlib

    moved = []
    for name, expected in sorted(TRAP_PATH_HASHES.items()):
        actual = hashlib.sha256((PKG / name).read_bytes()).hexdigest()
        if actual != expected:
            moved.append(f"  {name}\n    pinned: {expected}\n    actual: {actual}")
    assert not moved, (
        "a module on the trap path moved:\n"
        + "\n".join(moved)
        + "\n\nPrime directive 1: correlate.py, engine.py, receiver.py, capture.py and learn.py "
        "are byte-identical for the whole of v0.14.0. If a later release changes one of these "
        "legitimately, update TRAP_PATH_HASHES in the same commit — which makes the change a "
        "reviewable line in a diff instead of something that happened while someone was in the "
        "file for another reason."
    )


def test_every_pinned_trap_path_module_exists_and_the_set_is_the_whole_path() -> None:
    """The other direction: the table names five files and they are the five that exist.

    Without this, deleting an entry would make the guard pass by having nothing left to check —
    the same hole `test_no_module_may_join_the_allowlist` closes for the size guard, and the same
    reason: a guard whose subject can be edited away is not a guard.
    """
    assert set(TRAP_PATH_HASHES) == {
        "capture.py",
        "correlate.py",
        "engine/operate/engine.py",
        "learn.py",
        "receiver.py",
    }, "the pinned set is no longer the five modules the build prompt names"
    for name in TRAP_PATH_HASHES:
        assert (PKG / name).is_file(), f"{name} is pinned and does not exist"


# --- "did any code move at all", as one reviewable line ----------------------------------------
#
# Installed in v0.15.0, whose central claim was that a release rewriting the documentation changes
# no code. v0.15.1 changes `src/` in every one of its move commits, so the pin is **recomputed in
# each of them** rather than once at the end — otherwise the strongest whole-tree guard this
# project has would be checking a tree that no longer exists for eleven commits, which is the
# `TRAP_PATH_HASHES` failure mode at the scale of the whole package (DECISIONS #214).

#: SHA-256 over `src/`, excluding `src/netcorenoc/__init__.py`, which carries the version string
#: and is the one file a release is always allowed to touch. Computed as
#:
#:     for each path in sorted order:  update(path); update(b"\0"); update(sha256(contents))
#:
#: — the path is hashed alongside the contents, so a **move** moves the digest even when every byte
#: of every file is unchanged, which is exactly what makes it the right guard for v0.15.1. A digest
#: over contents alone would let `learn.py` and `severity.py` swap names unnoticed.
#:
#: **When a release legitimately changes `src/`, it recomputes this in the same commit.** That is
#: the point rather than an inconvenience: it turns "did any code move" into one reviewable line of
#: a diff, the discipline `TRAP_PATH_HASHES` and `UI_HASHES` already use. The name carried
#: `_AT_V0_14_0` until v0.15.1, which is a claim this release stopped making.
SRC_TREE_DIGEST = "1239dc6e66bea3a84c3b912d31c3003aa17395cca7fa2bf99a133b211f023d86"
SRC_FILE_COUNT = 168
SRC_VERSION_FILE = "src/netcorenoc/__init__.py"


def _src_tree_digest() -> tuple[str, int]:
    import hashlib

    root = PKG.parent.parent  # …/src/netcorenoc -> …/src -> repo root
    # Sorted by the POSIX string, not by Path, because Path ordering compares parts and would put
    # `api/app.py` on the other side of `agreement.py`. The digest is order-sensitive by design,
    # so the ordering is part of the pin and is stated rather than inherited from a comparison
    # operator that could change between Python versions.
    paths = sorted(
        p.relative_to(root).as_posix()
        for p in (root / "src").rglob("*")
        if p.is_file() and _is_source(p)
    )
    digest = hashlib.sha256()
    for relative in paths:
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256((root / relative).read_bytes()).digest())
    return digest.hexdigest(), len(paths)


def _is_source(path: Path) -> bool:
    """Tracked source only. `__pycache__` and `*.egg-info` are build output that appears from
    merely importing the package, and a guard that a test run can turn red by existing is noise."""
    parts = path.parts
    if any(part == "__pycache__" or part.endswith(".egg-info") for part in parts):
        return False
    if path.suffix == ".pyc":
        return False
    return bool(path.as_posix().split("/src/", 1)[-1] != "netcorenoc/__init__.py")


def test_src_is_byte_identical_to_the_pin_except_the_version_string() -> None:
    """Every file under `src/`, by path and by content, against one recorded digest.

    The exclusion is exactly one file and it is named, not globbed: `__init__.py` carries
    `__version__` and nothing else a release changes. An exclusion pattern would be a hole.
    """
    actual, count = _src_tree_digest()
    assert count == SRC_FILE_COUNT, (
        f"{count} source files under src/ excluding the version file; the pin records "
        f"{SRC_FILE_COUNT}. A file was added or removed, which is a src/ change whatever its "
        "contents — and in a release of pure moves it is a defect."
    )
    assert actual == SRC_TREE_DIGEST, (
        f"src/ has moved.\n  pinned:  {SRC_TREE_DIGEST}\n"
        f"  actual:  {actual}\n\n"
        "A release that legitimately changes src/ recomputes SRC_TREE_DIGEST in the same commit, "
        "which makes the change one reviewable line of a diff instead of something that happened."
    )


def test_the_version_file_is_the_only_thing_the_digest_forgives() -> None:
    """The control. Without it the test above passes on a digest that excluded everything, or on
    one whose exclusion silently grew — and it asserts the version file is genuinely excluded, so
    the bump this release makes is not being smuggled past a guard that never looked."""
    from netcorenoc import __version__

    root = PKG.parent.parent
    assert not _is_source(root / SRC_VERSION_FILE), "the version file must be excluded"
    assert _is_source(PKG / "learn.py"), "an ordinary module must be included"
    assert not _is_source(PKG / "__pycache__" / "learn.cpython-312.pyc"), "build output is not src"
    assert __version__ == "0.15.0", "the one src/ change this release makes"
