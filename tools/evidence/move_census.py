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

#: Where each module went. `old name -> new path under src/netcorenoc/`, and this table is the
#: release's map: it is what `git mv` was driven from and what the census reads back.
MOVES: dict[str, str] = {}


def _load_moves() -> dict[str, str]:
    """Derive the map from the tree itself, so it cannot drift from what actually moved.

    Every `.py` under a layer directory whose name is not `__init__` is a file that came from the
    package root — which is true for this release and asserted by the baseline lookup: a file that
    was NOT at the root at `BASELINE` fails to resolve and is reported rather than skipped.
    """
    out: dict[str, str] = {}
    pkg = ROOT / "src" / "netcorenoc"
    for path in sorted(pkg.rglob("*.py")):
        rel = path.relative_to(pkg)
        if len(rel.parts) < 2 or rel.stem == "__init__":
            continue
        if rel.parts[0] in ("api", "store", "migrations", "ui"):
            continue  # never moved; they were already packages at the root
        out[rel.stem] = rel.as_posix()
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
    return "\n".join(
        line for number, line in enumerate(source.splitlines(), start=1) if number not in drop
    )


def digest(source: str) -> str:
    return hashlib.sha256(strip_imports(source).encode("utf-8")).hexdigest()


def main(argv: list[str]) -> int:
    ref = argv[0] if argv else BASELINE
    moves = MOVES or _load_moves()
    if not moves:
        sys.stdout.write("no moved modules found — the census would be vacuous\n")
        return 2

    exceptions: list[str] = []
    unresolved: list[str] = []
    checked = 0
    for name, new_relative in sorted(moves.items()):
        old = _baseline(ref, f"src/netcorenoc/{name}.py")
        if old is None:
            unresolved.append(name)
            continue
        new = (ROOT / "src" / "netcorenoc" / new_relative).read_text(encoding="utf-8")
        before, after = digest(old), digest(new)
        checked += 1
        if before != after:
            exceptions.append(f"  {name}.py -> {new_relative}\n    was {before}\n    now {after}")

    sys.stdout.write(f"content census against {ref}: {checked} moved file(s) compared\n")
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
