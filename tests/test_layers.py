"""The layer-dependency guard (v0.7.3, DECISIONS #92) — **a directory check since v0.15.1**.

`docs/architecture.md` has stated the dependency rule since v0.7.2:

> **A layer may import downward, and may import cross-cutting. Never upward.**

Between v0.7.3 and v0.15.1 the rule was enforced against a **dictionary of 62 module names** kept
in this file, mirroring the architecture document. That worked, and it had one weakness the brief
for v0.15.1 named exactly: a module's layer was a *declaration*, so placing a new module correctly
depended on someone remembering to come here and write a line.

v0.15.1 put the layers on disk (#207). A module's layer is now **where it was saved**, the table
below has five directory rows instead of 62 module rows, and a module cannot escape the rule by not
appearing in it — because there is nowhere else to put a file. `test_the_package_root_is_closed`
is the half that makes that true: the root holds four modules, they are named, and a fifth fails.

Two rows are still declarations and it is worth being honest about which: `api` is the http layer
and `store` is the data layer, and neither directory is named for its layer. #209 records why —
renaming them would either break `Path(__file__).parent.parent / "ui"` and `/ "migrations"`, which
is a content change inside a move, or drag 47 UI files and 13 migrations along for no layer at all.

`EXEMPTIONS` is **empty** and a test says so. An entry here is a visible, arguable diff — exactly
what `DEBT_ALLOWLIST` is for size. Adding one should be as uncomfortable as adding one there.
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

#: **The whole table.** Every top-level directory under `src/netcorenoc`, and its layer.
#:
#: `migrations/` and `ui/` are absent because they hold no Python — SQL and a static console —
#: and `test_every_top_level_directory_has_a_layer` asserts that is still true rather than
#: assuming it.
DIRECTORY_LAYER: dict[str, str] = {
    "api": "http",  # the delivery layer; it kept its name (#209)
    "engine": "engine",  # the domain, in six subpackages (#208)
    "store": "data",  # one SQLite connection under one asyncio lock; it kept its name (#209)
    "ingest": "ingest",  # the wire
    "crosscutting": "cross-cutting",  # every layer's concern, no layer's private concern
}

#: The package root, which is closed. `__init__.py` is the package's identity; the other three are
#: the process entry surface, and `python -m netcorenoc.main` is a public interface printed by the
#: `Dockerfile`, the systemd unit, `flake.nix`, `docker-compose.yml` and the README — which is why
#: they did not move (#209).
#:
#: Why the three entry modules are classified `http` rather than exempted: the architecture
#: document records that a process entry point may legitimately reach up into `http` to build the
#: server, while the `Engine` may not. An entry point that builds an HTTP server *is* a delivery
#: concern, so it belongs at that layer; calling it an exemption would say the rule was bent, when
#: in fact the module was misplaced.
ROOT_LAYER: dict[str, str] = {
    "__init__": "cross-cutting",
    "__main__": "http",
    "main": "http",
    "runner": "http",
}

EXEMPTIONS: dict[tuple[str, str], str] = {}

# Type-only imports (`if TYPE_CHECKING:`) create no runtime edge and no import cycle.
# MODULE-ARCHITECTURE.md §1 records `audit.py`/`auth.py` -> `store.Store` as tolerated on exactly
# that ground, and `_imports` below flags them so the direction rule can skip them — which keeps
# the tolerance a decision rather than an oversight.


def _layer(key: str) -> str:
    """A directory name, or a root module's stem, to its layer."""
    return DIRECTORY_LAYER[key] if key in DIRECTORY_LAYER else ROOT_LAYER[key]


def _placed(key: str) -> bool:
    return key in DIRECTORY_LAYER or key in ROOT_LAYER


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
    """A module nobody placed would silently escape the rule. Failing here is the reminder.

    Since v0.15.1 this can only fail two ways: a new top-level directory, or a fifth module at the
    package root. Both are deliberate acts that need a decision, which is the point — before the
    move a module escaped the rule by the author simply not editing this file.
    """
    unplaced = sorted({key for key, _ in _module_files() if not _placed(key)})
    assert not unplaced, (
        f"under src/netcorenoc with no layer: {unplaced}. Every module lives in a layer directory "
        "(DIRECTORY_LAYER) or is one of the four entry modules at the root (ROOT_LAYER). A third "
        "possibility is a decision, not an oversight — record it in docs/adr/DECISIONS.md."
    )


def test_the_layer_table_names_only_real_directories() -> None:
    """A renamed or deleted directory leaves a dead row that would classify nothing."""
    live = {key for key, _ in _module_files()}
    dead = sorted(name for name in (*DIRECTORY_LAYER, *ROOT_LAYER) if name not in live)
    assert not dead, f"the layer table names {dead}, which do not exist"


def test_the_package_root_is_closed() -> None:
    """**The half that makes "the layer is the directory" true** (DECISIONS #209).

    If a module could be saved at the package root it would have no directory and therefore no
    layer, and the table would be back to being a thing someone has to remember. Four modules live
    here, they are named, and a fifth is a red test rather than an unclassified file.
    """
    root = sorted(path.stem for path in PKG.glob("*.py"))
    assert root == sorted(ROOT_LAYER), (
        f"the package root holds {root}; it may hold only {sorted(ROOT_LAYER)} — the package's "
        "identity and its process entry surface. Everything else goes in a layer directory."
    )


def test_every_top_level_directory_has_a_layer() -> None:
    """The other direction: a directory holding Python must appear in the table.

    `migrations/` and `ui/` are absent from it because they hold no `.py`, and that is asserted
    here rather than assumed — a `.py` appearing in either would be a module with no layer.
    """
    with_python = {
        path.relative_to(PKG).parts[0] for path in PKG.rglob("*.py") if path.parent != PKG
    }
    assert with_python == set(DIRECTORY_LAYER), (
        f"directories holding Python: {sorted(with_python)}; the table names "
        f"{sorted(DIRECTORY_LAYER)}"
    )


def test_a_module_in_the_wrong_directory_is_classified_by_the_directory() -> None:
    """**The demonstration**, and the reason this guard is stronger than the dictionary it replaced.

    A module's layer is read off its path, so a file saved in the wrong place is *classified* wrong
    — and then caught by the direction rule, because its imports no longer match its new layer.
    The control is the same module read at its real path, which classifies correctly.
    """
    correlate = PKG / "engine" / "correlate" / "correlate.py"
    assert _layer(correlate.relative_to(PKG).parts[0]) == "engine", "the control must classify"

    misplaced = Path("ingest") / "correlate.py"  # the same module, saved one layer down
    assert _layer(misplaced.parts[0]) == "ingest", "the directory is what classifies"
    imported = {name for name, type_only in _imports(correlate) if not type_only and _placed(name)}
    upward = sorted(
        name
        for name in imported
        if _layer(name) != CROSS_CUTTING and _rank(_layer(name)) < _rank("ingest")
    )
    assert upward, (
        "correlate.py imports nothing that would be upward from `ingest/`, so moving it there "
        "would not be caught — and this demonstration would be proving nothing"
    )


# --- the rule ------------------------------------------------------------------------------


def test_no_module_imports_upward() -> None:
    """**The guard.** A layer may import downward and may import cross-cutting. Never upward."""
    violations: list[str] = []
    for key, path in _module_files():
        src_layer = _layer(key)
        for imported, type_only in _imports(path):
            if not _placed(imported) or imported == key or type_only:
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
    known = {("crosscutting", "ingest")}
    violations: list[str] = []
    for key, path in _module_files():
        if _layer(key) != CROSS_CUTTING:
            continue
        for imported, type_only in _imports(path):
            if not _placed(imported) or imported == key or type_only:
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
    engine_side = [(key, path) for key, path in _module_files() if key == "engine"]
    assert len(engine_side) >= 5, (
        f"only {len(engine_side)} file(s) under engine/; before v0.15.1 this test named four "
        "modules by hand and it now covers the whole layer, so a small number means the walk "
        "broke rather than that the layer shrank"
    )
    for _key, path in engine_side:
        imported = {mod for mod, type_only in _imports(path) if not type_only}
        assert "api" not in imported, (
            f"{path.relative_to(PKG)} imports netcorenoc.api — the Engine must not reach into the "
            "http layer. The process runner may; that is what runner.py is for."
        )


@pytest.mark.parametrize("layer", [*STACK, CROSS_CUTTING])
def test_every_layer_has_at_least_one_module(layer: str) -> None:
    """A layer with no modules would make its share of the rule vacuous."""
    live = {key for key, _ in _module_files()}
    assert any(_layer(k) == layer for k in live), f"no module is classified {layer!r}"
