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
# src/ layout: netcorenoc/__init__.py -> src/netcorenoc -> src -> repo root.
REPO_ROOT = Path(netcorenoc.__file__).parent.parent.parent


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


def test_vendored_license_shipped_beside_asset() -> None:
    """Third-party licence compliance: the upstream d3 licence ships next to the vendored asset
    (and is covered by the ``ui/vendor/*`` package-data glob, so a wheel carries it too)."""
    lic = VENDOR / "d3.LICENSE"
    assert lic.exists(), "d3.LICENSE must ship beside src/netcorenoc/ui/vendor/d3.v7.min.js"
    text = lic.read_text()
    assert "Mike Bostock" in text and "d3" in text


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


# -- v0.13.0: the gaps the checksum loop above could not see --------------------------------------


def test_every_vendored_asset_is_pinned_by_name() -> None:
    """**The gap `test_vendored_assets_match_pinned_checksums` cannot close.**

    That loop iterates `CHECKSUMS.txt` and verifies what it finds, so it can only ever check what
    the file already names. An asset dropped into `vendor/` with no pin is invisible to it — the
    loop passes, the asset ships, and nothing has verified a byte of it.

    `UI-0.13-DRAFT.md` §10.1(4) said this had to be *"verified rather than assumed"* before a
    second asset was vendored. It was assumed. This is the verification, and it goes in the
    opposite direction: every file in the directory must be **named** in the pin file.
    """
    pinned = {
        line.split()[1]
        for line in (VENDOR / "CHECKSUMS.txt").read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    present = {
        path.name
        for path in VENDOR.iterdir()
        if path.is_file() and path.name != "CHECKSUMS.txt" and not path.name.endswith("LICENSE")
    }
    assert present == pinned, (
        f"the vendored set and the pinned set disagree.\n"
        f"  present, unpinned: {sorted(present - pinned)}\n"
        f"  pinned, missing:   {sorted(pinned - present)}"
    )
    assert len(pinned) >= 3, f"only {len(pinned)} assets pinned; d3, preact and htm are expected"


def test_every_vendored_asset_ships_its_licence() -> None:
    """§10.1(3): the upstream licence travels with the bytes, per asset.

    Derived from the pinned set rather than listed, so vendoring a fourth asset without its licence
    fails here instead of being noticed at a compliance review.
    """
    pinned = {
        line.split()[1]
        for line in (VENDOR / "CHECKSUMS.txt").read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    # `d3.v7.min.js` -> `d3`; `preact-10.29.8.module.js` -> `preact`;
    # `htm-3.1.1.module.js` -> `htm`.
    for asset in pinned:
        package = asset.split(".")[0].split("-")[0]
        licence = VENDOR / f"{package}.LICENSE"
        assert licence.exists(), f"{asset} ships without {licence.name}"
        assert len(licence.read_text().strip()) > 200, f"{licence.name} is not a licence text"


def test_every_vendored_asset_is_attributed_in_notice() -> None:
    """Apache-2.0 §4(c) and plain honesty: NOTICE names what this product bundles."""
    notice = (REPO_ROOT / "NOTICE").read_text(encoding="utf-8")
    for asset in ("d3.v7.min.js", "preact-10.29.8.module.js", "htm-3.1.1.module.js"):
        assert asset in notice, f"{asset} is bundled but is not attributed in NOTICE"
    assert "No other third-party code is bundled." in notice


def test_the_framework_carries_its_version_in_its_filename() -> None:
    """§10.1(1). A vendored asset whose filename does not state its version is an asset nobody can
    audit against an advisory without opening it."""
    for asset, marker in (
        ("preact-10.29.8.module.js", "10.29.8"),
        ("htm-3.1.1.module.js", "3.1.1"),
    ):
        assert (VENDOR / asset).exists(), f"{asset} is not vendored"
        assert marker in asset


def test_the_vendored_modules_carry_no_bare_specifier() -> None:
    """Load-bearing, not cosmetic (ADR #174).

    A bare specifier in a vendored module would need an import map to resolve, and an import map is
    an inline `<script type="importmap">` that `script-src 'self'` forbids. It is the reason this
    release vendors Preact's *core* and no hooks module — `hooks.module.js` imports `"preact"`.
    """
    import re

    for asset in ("preact-10.29.8.module.js", "htm-3.1.1.module.js"):
        source = (VENDOR / asset).read_text(encoding="utf-8")
        specifiers = re.findall(r'\bfrom\s*"([^"]+)"', source)
        assert not specifiers, f"{asset} imports {specifiers}, which would need an import map"


def test_the_served_module_set_equals_the_module_set_on_disk() -> None:
    """ADR #175: the appliance serves an **enumerated** set, and enumeration only helps if it is
    complete. Both directions: a module on disk that is not served is a console that half-loads;
    a module served that is not on disk is a 404 waiting for a browser to find it."""
    from netcorenoc.api.routes_static import STATIC_ASSETS

    on_disk = {str(p.relative_to(UI)) for p in UI.rglob("*.js")}
    served = {name for name in STATIC_ASSETS if name.endswith(".js")}
    assert on_disk == served, (
        f"on disk, not served: {sorted(on_disk - served)}\n"
        f"served, not on disk: {sorted(served - on_disk)}"
    )
