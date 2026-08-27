"""The v0.15.1 content census: prove every moved file changed **only its import statements**.

v0.15.1 moves 61 files and rewrites every import that names one. The claim the release rests on is
that a move changed nothing else — no renamed function, no fixed docstring, no reformatting, no
*"while I'm here"*. That claim is checkable, and this is what checks it, mechanically:

    for each moved file:
        old = git show <BASELINE>:<old path>
        new = the file where it is now
        strip every `import` / `from … import` statement from both, by `ast` span
        the SHA-256 of what is left must be equal

Stripping by `ast` span rather than by pattern is the whole design. A line-based filter would miss
a parenthesised import spanning four lines and would mistake a docstring line beginning with the
word *import* for one — and this project has been caught by exactly that class of mistake before
(a grep once accused `engine.py` of importing `netcorenoc.api`; it was the docstring saying it
never must). Stripping the statements also makes the census blind to import **order**, which is
right: `ruff` sorts the block after a rewrite, and a sorted block is still only imports.

**Every exception is listed with its reason and the exit code is non-zero.** Expected: zero.

    python tools/evidence/move_census.py            # against the recorded baseline
    python tools/evidence/move_census.py <ref>      # against another commit
"""

from __future__ import annotations

import ast
import hashlib

# B404: subprocess is how this reads the baseline out of git. Every call passes a fixed argv list
# with `shell=False`, and the executable is resolved here rather than by the shell at call time.
import subprocess  # nosec B404
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GIT = "/usr/bin/git"

#: The tree every moved file is compared against: v0.15.0 as this release found it, before the
#: first `git mv`. Recorded as a SHA rather than a tag because the delivered clone may carry no
#: tags, and a census whose baseline cannot be resolved is a census that quietly passes.
BASELINE = "0089fc0"

#: The directories v0.15.1 created. A moved file's path at the baseline is its path today with one
#: of these stripped — `crosscutting/rbac/tables.py` was `rbac/tables.py`, and
#: `engine/report/bias.py` was `bias.py`. Declared rather than inferred: a rule that guessed the
#: old path from the file name alone would look up `tables.py` at the package root, find nothing,
#: and report a file it never actually compared.
DESTINATIONS = (
    "engine/correlate",
    "engine/dataset",
    "engine/evaluation",
    "engine/model",
    "engine/operate",
    "engine/report",
    "crosscutting",
    "ingest",
)


def _load_moves() -> dict[str, str]:
    """`path at the baseline -> path today`, derived from the tree so it cannot drift from it."""
    out: dict[str, str] = {}
    pkg = ROOT / "src" / "netcorenoc"
    for path in sorted(pkg.rglob("*.py")):
        relative = path.relative_to(pkg).as_posix()
        for destination in DESTINATIONS:
            if not relative.startswith(destination + "/"):
                continue
            stripped = relative.removeprefix(destination + "/")
            # A bare `__init__.py` directly under a destination is the marker this release created
            # for that directory. Stripping the prefix would point it at the package root's own
            # `__init__.py`, which resolves — and would be a comparison between two unrelated files
            # reported as a defect. `crosscutting/rbac/__init__.py` is not this case: it strips to
            # `rbac/__init__.py`, which is where it came from.
            out[relative if stripped == "__init__.py" else stripped] = relative
            break
    return out


def _baseline(ref: str, relative: str) -> str | None:
    result = subprocess.run(  # nosec B603 - fixed argv, shell=False, absolute executable
        [GIT, "show", f"{ref}:{relative}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def strip_imports(source: str) -> str:
    """Everything that is not an import statement, by `ast` span."""
    tree = ast.parse(source)
    drop: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            drop.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    # …and the blank lines the import block is separated by. Sorting an import into a different
    # position moves a blank line with it — `varbind_profile.py` lost one when `known_oids` sorted
    # past a comment — and a blank line BETWEEN imports is the import block's layout, not the
    # module's substance. Only blanks touching a removed line go; one elsewhere in the file stays,
    # which is what the control below asserts.
    lines = source.splitlines()
    changed = True
    while changed:
        changed = False
        for number, line in enumerate(lines, start=1):
            if line.strip() or number in drop:
                continue
            if (number - 1) in drop or (number + 1) in drop:
                drop.add(number)
                changed = True
    return "\n".join(line for number, line in enumerate(lines, start=1) if number not in drop)


def digest(source: str) -> str:
    return hashlib.sha256(strip_imports(source).encode("utf-8")).hexdigest()


def main(argv: list[str]) -> int:
    ref = argv[0] if argv else BASELINE
    moves = _load_moves()
    if not moves:
        sys.stdout.write("no moved modules found — the census would be vacuous\n")
        return 2

    exceptions: list[str] = []
    unresolved: list[str] = []
    created: list[str] = []
    checked = 0
    for old_relative, new_relative in sorted(moves.items()):
        old = _baseline(ref, f"src/netcorenoc/{old_relative}")
        if old is None:
            # A package marker this release creates has no baseline and is not an exception; a
            # module without one is a file that appeared during a release of pure moves, which is.
            (created if new_relative.endswith("__init__.py") else unresolved).append(new_relative)
            continue
        new = (ROOT / "src" / "netcorenoc" / new_relative).read_text(encoding="utf-8")
        before, after = digest(old), digest(new)
        checked += 1
        if before != after:
            exceptions.append(
                f"  {old_relative} -> {new_relative}\n    was {before}\n    now {after}"
            )

    sys.stdout.write(
        f"content census against {ref}: {checked} moved file(s) compared, "
        f"{len(created)} package marker(s) created\n"
    )
    if unresolved:
        sys.stdout.write(
            f"UNRESOLVED at the baseline ({len(unresolved)}): {', '.join(unresolved)}\n"
            "  A moved file that did not exist at the root of the baseline tree. Either the move\n"
            "  map is wrong or the file is new — and a new file in a move release is a defect.\n"
        )
    if exceptions:
        sys.stdout.write(
            f"EXCEPTIONS ({len(exceptions)}) — these changed beyond their imports:\n"
            + "\n".join(exceptions)
            + "\n"
        )
    else:
        sys.stdout.write("zero exceptions: every moved file changed only its import statements\n")
    return 1 if (exceptions or unresolved) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
