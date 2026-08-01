"""The layer-dependency guard (v0.7.3, §5.4 / DECISIONS #92).

`MODULE-ARCHITECTURE.md` §1 has stated the dependency rule since v0.7.2:

> **A layer may import downward, and may import cross-cutting. Never upward.**

Until this release **no test enforced it**. The existing guards assert module *size*
(`test_architecture.py`), nesting depth, route order, and import *resolution*
(`test_structure.py`) — never import *direction*. That gap is not theoretical: the one genuine
violation, `main.py` → `netcorenoc.api`, was recorded in v0.7.2's architecture document and is
only being resolved now, one release later. A rule with no test is a rule that gets noticed late.

The table below **mirrors** `MODULE-ARCHITECTURE.md` §1. It is duplicated here deliberately and
that duplication is itself asserted: `test_every_runtime_module_is_assigned_a_layer` fails on a
module nobody has placed, so a new module cannot quietly escape the rule by not appearing.

`EXEMPTIONS` ends this release **empty**, and a test says so. Because the guard is installed
against the *unmodified* tree — so it predates the code it measures — it necessarily starts holding
the one violation §1 already records, and Phase 4 deletes that entry when the split resolves it.
Until then a second test pins the list to that single known name, so it cannot grow while it is
briefly non-empty. Afterwards, an entry here is a visible, arguable diff — exactly what
`DEBT_ALLOWLIST` is for size. Adding one should be as uncomfortable as adding one there.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG = REPO_ROOT / "src" / "netcorenoc"

# --- the rule, mirroring MODULE-ARCHITECTURE.md §1 -----------------------------------------

# The stack, most-upward first. `http` may import `engine`, `data`, `ingest`; `engine` may import
# `data`, `ingest`; and so on. Cross-cutting is available to all of them and imports only itself.
STACK: list[str] = ["http", "engine", "data", "ingest"]
CROSS_CUTTING = "cross-cutting"

LAYER_OF: dict[str, str] = {
    # http — the delivery layer, plus the process entry point (see the note below)
    "api": "http",
    "main": "http",
    "runner": "http",
    "__main__": "http",
    # engine — the domain
    "engine": "engine",
    "engine_base": "engine",
    "maintenance": "engine",
    "gaps": "engine",
    "scorer_lifecycle": "engine",
    "settings": "cross-cutting",
    "correlate": "engine",
    "learn": "engine",
    "scoring": "engine",
    "rootcause": "engine",
    "severity": "engine",
    "varbind_profile": "engine",
    "varbind_accum": "engine",
    "preview": "engine",
    # v0.8.0. Engine-layer because it consumes a correlation decision and writes through the
    # data layer — a downward edge. It is NOT ingest: nothing here is reachable from
    # `receiver.datagram_received`, which is prime directive 1.
    "capture": "engine",
    # The verdict side of the same feature. Split from `capture.py` by *path*, not by size:
    # capture runs per activation on the ingest path, this runs per operator verdict.
    "labels": "engine",
    # data — one SQLite connection under one asyncio lock
    "store": "data",
    # ingest — the wire
    "receiver": "ingest",
    "events": "ingest",
    "known_oids": "ingest",
    # cross-cutting — every layer's concern, no layer's private concern
    "rbac": "cross-cutting",
    "shaping": "cross-cutting",
    "auth": "cross-cutting",
    "audit": "cross-cutting",
    "runtime": "cross-cutting",
    "logsetup": "cross-cutting",
    "__init__": "cross-cutting",
}

# Why `main.py`, `runner.py` and `__main__.py` are classified `http` rather than exempted:
# MODULE-ARCHITECTURE §1 records that the process entry point may legitimately reach up into
# `http` to build the server, while the `Engine` may not. An entry point that builds an HTTP
# server *is* a delivery concern, so it belongs at that layer; calling it an exemption would say
# the rule was bent, when in fact the module was misplaced.
#
# `main.py` sat at `engine` before v0.7.3 because it *was* the `Engine` as well as the entry point
# — which is exactly the confusion §1 recorded as the project's one genuine layer violation. Now
# that `engine.py` holds the domain and `main.py` holds only `main()` and the re-exports, `main.py`
# is an entry point and nothing else, and its layer says so. `engine.py` stays at `engine`, and the
# assertion that it does not import `netcorenoc.api` is stated separately below.

# Upward imports that are tolerated, each with the reason.
#
# This guard is installed in Phase 2 against the **unmodified** tree, so it necessarily starts
# holding the one violation `MODULE-ARCHITECTURE.md` §1 already records — otherwise it could only
# be written after the code it is supposed to measure, which is the failure mode DECISIONS #81
# exists to prevent. Build prompt §5.4 requires the list be **empty by the end of this release**,
# and Phase 4 empties it: once `runner.py` carries the process entry point, `engine.py` has no
# reason to import `netcorenoc.api` and this entry is deleted.
#
# After that, an entry here is a visible, arguable diff — exactly as DEBT_ALLOWLIST is for size.
EXEMPTIONS: dict[tuple[str, str], str] = {}

# Modules this release creates, which `LAYER_OF` names before they exist. Emptied in Phase 4, so
# the dead-entry check below goes back to being absolute.
PLANNED_THIS_RELEASE: set[str] = set()

# Type-only imports (`if TYPE_CHECKING:`) create no runtime edge and no import cycle.
# MODULE-ARCHITECTURE.md §1 records `audit.py`/`auth.py` -> `store.Store` as tolerated on exactly
# that ground, and `_imports` below flags them so the direction rule can skip them — which keeps
# the tolerance a decision rather than an oversight.


def _layer(module: str) -> str:
    return LAYER_OF[module]


def _rank(layer: str) -> int:
    """Position in the stack; cross-cutting sits outside it."""
    return STACK.index(layer)


def _module_files() -> list[tuple[str, Path]]:
    """(layer key, file) for every runtime `.py`, including each file inside a package."""
    out: list[tuple[str, Path]] = []
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG)
        out.append((rel.parts[0] if len(rel.parts) > 1 else rel.stem, path))
    return out


def _imports(path: Path) -> list[tuple[str, bool]]:
    """(imported `netcorenoc` submodule, is_type_only) for one file.

    `is_type_only` is True when the import sits under `if TYPE_CHECKING:` — no runtime edge.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    type_only_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = ast.unparse(node.test)
            if "TYPE_CHECKING" in test:
                for sub in ast.walk(node):
                    if hasattr(sub, "lineno"):
                        type_only_lines.add(sub.lineno)
    found: list[tuple[str, bool]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            type_only = node.lineno in type_only_lines
            if node.module == "netcorenoc":
                # `from netcorenoc import audit, auth` — each alias names a submodule.
                found.extend((alias.name, type_only) for alias in node.names)
            elif node.module.startswith("netcorenoc."):
                # `from netcorenoc.store.types import X` — the layer key is the first component.
                found.append((node.module.split(".")[1], type_only))
        elif isinstance(node, ast.Import):
            type_only = node.lineno in type_only_lines
            found.extend(
                (alias.name.split(".")[1], type_only)
                for alias in node.names
                if alias.name.startswith("netcorenoc.")
            )
    return found


# --- guarding the guard --------------------------------------------------------------------


def test_modules_discovered() -> None:
    """A glob that matched nothing would make every assertion below vacuous."""
    files = _module_files()
    assert len(files) >= 20, files
    assert any(key == "api" for key, _ in files)


def test_every_runtime_module_is_assigned_a_layer() -> None:
    """A module nobody placed would silently escape the rule. Failing here is the reminder."""
    unplaced = sorted({key for key, _ in _module_files() if key not in LAYER_OF})
    assert not unplaced, (
        f"module(s) under src/netcorenoc with no layer in LAYER_OF: {unplaced}. Place each one in "
        "MODULE-ARCHITECTURE.md §1 and mirror it here — a module outside the table is a module "
        "outside the dependency rule."
    )


def test_the_layer_table_names_only_real_modules() -> None:
    """A renamed or deleted module leaves a dead entry that would classify nothing.

    `PLANNED_THIS_RELEASE` is the one tolerated gap and it is emptied in Phase 4, at which point
    this check is absolute again.
    """
    live = {key for key, _ in _module_files()}
    dead = sorted(name for name in LAYER_OF if name not in live)
    unexpected = [name for name in dead if name not in PLANNED_THIS_RELEASE]
    assert not unexpected, f"LAYER_OF names module(s) that do not exist: {unexpected}"


def test_planned_modules_are_retired_once_they_exist() -> None:
    """`PLANNED_THIS_RELEASE` may not outlive the modules it stands in for.

    An entry that names a module which now exists is stale, and a stale entry would keep weakening
    the dead-entry check above for no reason. This is what forces Phase 4 to empty the set.
    """
    live = {key for key, _ in _module_files()}
    stale = sorted(name for name in PLANNED_THIS_RELEASE if name in live)
    assert not stale, (
        f"PLANNED_THIS_RELEASE still names module(s) that now exist: {stale}. Delete them; the "
        "set exists only to cover the window between writing the table and creating the modules."
    )


# --- the rule ------------------------------------------------------------------------------


def test_no_module_imports_upward() -> None:
    """**The guard.** A layer may import downward and may import cross-cutting. Never upward."""
    violations: list[str] = []
    for key, path in _module_files():
        src_layer = _layer(key)
        for imported, type_only in _imports(path):
            if imported not in LAYER_OF or imported == key or type_only:
                continue
            dst_layer = _layer(imported)
            if dst_layer == CROSS_CUTTING or src_layer == CROSS_CUTTING:
                # Cross-cutting is importable from anywhere; a cross-cutting module importing
                # downward is checked by its own test below.
                continue
            if _rank(dst_layer) < _rank(src_layer):
                if (key, imported) in EXEMPTIONS:
                    continue
                rel = path.relative_to(PKG)
                violations.append(
                    f"{rel} ({src_layer}) imports netcorenoc.{imported} ({dst_layer}) — upward"
                )
    assert not violations, (
        "upward import(s), against MODULE-ARCHITECTURE.md §1:\n  "
        + "\n  ".join(sorted(violations))
        + "\n\nAn upward import turns a stack into a knot: it makes the lower layer untestable "
        "without the higher one and makes 'where is this decided?' unanswerable."
    )


def test_the_exemption_list_is_empty() -> None:
    """The ratchet. v0.7.3 resolved the one recorded violation, so nothing needs waiving.

    This started the release holding `("main", "api")` — the guard was installed against the
    unmodified tree, so it had to — and Phase 4 deleted it when `runner.py` took over the process
    entry point. An exemption added later fails here until someone deletes this assertion, which
    is a visible, arguable diff, and the entire point.
    """
    assert EXEMPTIONS == {}, (
        f"the layer-rule exemption list is not empty: {sorted(EXEMPTIONS)}. It arrived empty at "
        "the end of v0.7.3, and every entry is a violation of MODULE-ARCHITECTURE.md §1 that "
        "someone chose to tolerate rather than fix."
    )


def test_cross_cutting_imports_only_cross_cutting() -> None:
    """MODULE-ARCHITECTURE.md §1: "Cross-cutting is importable from anywhere and imports only
    cross-cutting."

    One known, recorded exception: `runtime.py` -> `receiver.py`, named in §1's violation table and
    on the ROADMAP, deferred (not this release's scope). It is listed rather than hidden.
    """
    known = {("runtime", "receiver")}
    violations: list[str] = []
    for key, path in _module_files():
        if _layer(key) != CROSS_CUTTING:
            continue
        for imported, type_only in _imports(path):
            if imported not in LAYER_OF or imported == key or type_only:
                continue
            if _layer(imported) != CROSS_CUTTING and (key, imported) not in known:
                violations.append(f"{path.relative_to(PKG)} imports netcorenoc.{imported}")
    assert not violations, "cross-cutting module(s) importing a layer:\n  " + "\n  ".join(
        sorted(violations)
    )


def test_the_engine_does_not_import_the_http_layer() -> None:
    """**The violation this release resolves**, stated on its own so it cannot regress quietly.

    `MODULE-ARCHITECTURE.md` §1 recorded `main.py` -> `netcorenoc.api` as the one genuine upward
    import, because `main.py` was two things wearing one hat: the `Engine` (domain) *and* the
    process entry point that builds the HTTP server. v0.7.3 separates them. The entry point may
    reach up; the `Engine` may not — and after Phase 4 the `Engine` lives in `engine.py`.
    """
    modules = dict(_module_files())
    engine_side = [
        name for name in ("engine", "maintenance", "gaps", "scorer_lifecycle") if name in modules
    ]
    for name in engine_side:
        imported = {mod for mod, type_only in _imports(modules[name]) if not type_only}
        assert "api" not in imported, (
            f"{name}.py imports netcorenoc.api — the Engine must not reach into the http layer. "
            "The process runner may; that is what runner.py is for."
        )


@pytest.mark.parametrize("layer", [*STACK, CROSS_CUTTING])
def test_every_layer_has_at_least_one_module(layer: str) -> None:
    """A layer with no modules would make its share of the rule vacuous."""
    live = {key for key, _ in _module_files()}
    assert any(_layer(k) == layer for k in live), f"no module is classified {layer!r}"
