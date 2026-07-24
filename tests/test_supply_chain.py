"""§A.6 supply-chain integrity: the vendored d3 checksum pin, and the F12 packaging regression.

The strict CSP forbids a CDN, so d3 is vendored. Its exact bytes are pinned in CHECKSUMS.txt and
asserted here (and by a CI job). F12: a built wheel shipped only index.html, so the container UI
served a missing app.js / style.css / d3 — every UI file must stay covered by a package-data glob.
"""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path

import netcorenoc

UI = Path(netcorenoc.__file__).parent / "ui"
VENDOR = UI / "vendor"
REPO_ROOT = Path(netcorenoc.__file__).parent.parent


def test_vendored_assets_match_pinned_checksums() -> None:
    verified = 0
    for raw in (VENDOR / "CHECKSUMS.txt").read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        expected, name = line.split()
        actual = hashlib.sha256((VENDOR / name).read_bytes()).hexdigest()
        assert actual == expected, f"{name} changed; if intentional, update CHECKSUMS.txt"
        verified += 1
    assert verified >= 1  # at least d3 is pinned


def test_d3_is_pinned() -> None:
    assert (VENDOR / "d3.v7.min.js").exists()
    assert "d3.v7.min.js" in (VENDOR / "CHECKSUMS.txt").read_text()


def test_all_ui_assets_are_covered_by_package_data_globs() -> None:
    """F12: every UI file must match a package-data glob so a wheel never again drops app.js /
    style.css / the vendored library (which left the shipped container UI broken)."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    globs = pyproject["tool"]["setuptools"]["package-data"]["netcorenoc"]
    from fnmatch import fnmatch

    uncovered: list[str] = []
    for path in UI.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(UI.parent).as_posix()  # e.g. "ui/app.js", "ui/vendor/d3.v7.min.js"
        if not any(fnmatch(rel, glob) for glob in globs):
            uncovered.append(rel)
    assert not uncovered, f"UI files not covered by package-data (won't ship): {uncovered}"
