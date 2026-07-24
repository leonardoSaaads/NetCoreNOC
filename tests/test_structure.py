"""Structure-guard and documentation link-check for the v0.5.0 reorg.

These tests assert the *shape* of the repository, not its behaviour: the ``src/`` layout and
import resolution (so the F12 class of bug — tests passing against a source tree that a wheel
would not reproduce — stays impossible), and that every relative Markdown link resolves (so a
``git mv`` that relocates a doc can never silently leave a dangling cross-reference).

Both are pure-stdlib and dev-only: no runtime dependency, no network. They run under ``make
test`` (hence ``make qa``).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import netcorenoc

REPO_ROOT = Path(__file__).resolve().parent.parent

# --- structure guard --------------------------------------------------------------------

TOP_LEVEL_REQUIRED = [
    "src/netcorenoc/__init__.py",
    "pyproject.toml",
    "Makefile",
    "Dockerfile",
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "SECURITY.md",
    "MIGRATION.md",
    "tests",
    "eval",
    "tools",
    "docs",
]

DOCS_TAXONOMY = ["architecture", "adr", "security", "scope", "releases", "gates"]

# Every runtime submodule must resolve from the installed package under its unchanged name.
SUBMODULES = [
    "api",
    "audit",
    "auth",
    "correlate",
    "events",
    "known_oids",
    "learn",
    "logsetup",
    "main",
    "rbac",
    "receiver",
    "rootcause",
    "runtime",
    "severity",
    "shaping",
    "store",
    "varbind_profile",
]


def test_adopts_src_layout() -> None:
    """The import package lives at ``src/netcorenoc`` and resolves to exactly there."""
    pkg_dir = Path(netcorenoc.__file__).resolve().parent
    assert pkg_dir.name == "netcorenoc"
    assert pkg_dir.parent.name == "src", f"expected src/ layout, package is at {pkg_dir}"


def test_expected_top_level_tree() -> None:
    missing = [p for p in TOP_LEVEL_REQUIRED if not (REPO_ROOT / p).exists()]
    assert not missing, f"missing expected top-level paths: {missing}"


def test_docs_taxonomy_present() -> None:
    missing = [d for d in DOCS_TAXONOMY if not (REPO_ROOT / "docs" / d).is_dir()]
    assert not missing, f"missing docs taxonomy dirs: {missing}"
    # The documentation index and the newcomer map are the entry points a stranger uses.
    assert (REPO_ROOT / "docs" / "README.md").is_file()
    assert (REPO_ROOT / "docs" / "architecture" / "repo-map.md").is_file()
    assert (REPO_ROOT / "docs" / "adr" / "README.md").is_file()


def test_import_path_unchanged() -> None:
    """The public import path stays ``netcorenoc`` — the src/ move is not a public change."""
    assert netcorenoc.__name__ == "netcorenoc"
    assert isinstance(netcorenoc.__version__, str)


@pytest.mark.parametrize("mod", SUBMODULES)
def test_every_submodule_resolves(mod: str) -> None:
    __import__(f"netcorenoc.{mod}")


def test_no_stale_flat_package() -> None:
    """The pre-move flat ``netcorenoc/`` and the retired ``opticorr/`` must not reappear."""
    assert not (REPO_ROOT / "netcorenoc").exists(), "flat netcorenoc/ should be under src/ now"
    assert not (REPO_ROOT / "opticorr").exists(), "the retired opticorr/ package must stay gone"


# --- documentation link check -----------------------------------------------------------

_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_FENCED = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`]*`")
_SKIP_DIRS = {".git", ".venv", "node_modules", "build", "dist", "__pycache__", ".hypothesis"}


def _strip_code(text: str) -> str:
    """Remove fenced blocks and inline code spans — Markdown does not render links inside them,
    so a ``[text](target)`` written as documentation-about-links must not be treated as a link.
    A real link whose *text* is code (``[`SECURITY.md`](SECURITY.md)``) survives: stripping the
    inner code span leaves ``[](SECURITY.md)``, still a valid link with the real target."""
    return _INLINE_CODE.sub("", _FENCED.sub("", text))


def _markdown_files() -> list[Path]:
    return [
        p
        for p in REPO_ROOT.rglob("*.md")
        if not any(part in _SKIP_DIRS or part.endswith(".egg-info") for part in p.parts)
    ]


def test_markdown_files_discovered() -> None:
    assert _markdown_files(), "no Markdown files found — link check would be vacuous"


def test_no_broken_relative_markdown_links() -> None:
    """Every relative ``[text](target)`` link resolves to a real file (anchors stripped).

    External links (``http(s):``/``mailto:``) and pure ``#anchor`` links are out of scope — this
    guards internal cross-references, which a doc move is what breaks.
    """
    broken: list[str] = []
    for md in _markdown_files():
        for target in _LINK.findall(_strip_code(md.read_text(encoding="utf-8"))):
            target = target.strip()
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path_part = target.split("#", 1)[0].split("?", 1)[0]
            if not path_part:
                continue  # a pure in-page anchor
            resolved = (md.parent / path_part).resolve()
            if not resolved.exists():
                broken.append(f"{md.relative_to(REPO_ROOT)} -> {target}")
    assert not broken, "broken relative Markdown links:\n  " + "\n  ".join(broken)
