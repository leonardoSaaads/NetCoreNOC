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


def _covered_by(patterns: list[str], root: Path) -> set[str]:
    """Expand package-data patterns **the way setuptools expands them** — `glob`, not `fnmatch`.

    One function, so the test below and the guard-on-the-guard below that cannot disagree about
    which matcher is in use. Splitting them is how F85 stayed invisible: the check and the thing
    it spoke for answered different questions and nothing compared the two.
    """
    import glob as globmod

    covered: set[str] = set()
    for pattern in patterns:
        covered |= set(globmod.glob(pattern, root_dir=root, recursive=True))
    return covered


def test_all_ui_assets_are_covered_by_package_data_globs() -> None:
    """F12: every UI file must match a package-data glob so a wheel never again drops app.js /
    style.css / the vendored library (which left the shipped container UI broken).

    **This test asserted the wrong thing for three releases (F85).** It matched with `fnmatch`,
    whose `*` crosses `/`, so `fnmatch("ui/app/views/parts/why.js", "ui/*.js")` is **True** and
    every file was "covered" at any depth. Setuptools expands package-data with `glob`, whose `*`
    stops at a separator, and it shipped none of them. A guard that is more permissive than the
    thing it guards reports success for exactly the defect it exists to catch.

    It now expands the globs through `_covered_by`. The wheel guard further down is the stronger
    statement and does not depend on getting these semantics right at all.
    """
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    globs = pyproject["tool"]["setuptools"]["package-data"]["netcorenoc"]
    package = UI.parent

    covered = _covered_by(globs, package)
    on_disk = {p.relative_to(package).as_posix() for p in UI.rglob("*") if p.is_file()}
    uncovered = sorted(on_disk - covered)
    assert not uncovered, f"UI files not covered by package-data (won't ship): {uncovered}"


def test_the_glob_matcher_is_the_one_setuptools_uses_not_a_looser_one() -> None:
    """**Guard the guard**, and it is the assertion that would have caught F85 on its own.

    The failure above was not a missing glob; it was a *matcher* that answered a different
    question. So this drives `_covered_by` — the real expander, not a local copy of it — with the
    exact case F85 turned on: a nested path must NOT come back covered by a parent-level glob,
    because setuptools does not consider it covered either.

    Written against `_covered_by` rather than against `glob` directly so that reverting the matcher
    turns **this** red too. A guard-on-a-guard that only restates a property of the standard
    library guards nothing: `fnmatch` and `glob` would still differ while the check above quietly
    went back to asking the wrong one.
    """
    from fnmatch import fnmatch

    nested = "ui/app/views/parts/why.js"
    parent_glob = "ui/app/*.js"

    assert fnmatch(nested, parent_glob), (
        "fnmatch no longer crosses '/', so the premise of this guard has changed; re-derive it"
    )
    assert (UI / "app/views/parts/why.js").is_file(), "the case this guard is built on has moved"
    covered = _covered_by([parent_glob], UI.parent)
    assert covered, f"{parent_glob} matched nothing at all, so this asserts nothing"
    assert nested not in covered, (
        "the package-data expander now treats a nested path as covered by a parent-level glob. "
        "Either it is back on fnmatch (F85: the check above is decorative again) or setuptools' "
        "own matcher has changed — re-read DECISIONS #251 before relying on either."
    )


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


# --- v0.15.4: the guard that does not reason about globs at all (F85, DECISIONS #251) ------------


def _dockerfile_copied_paths() -> list[str]:
    """The repo-root paths the Dockerfile's **build stage** copies, read from the Dockerfile.

    Derived rather than duplicated. A context assembled from a hand-written list would keep
    agreeing with a Dockerfile that had changed underneath it, which is the F85 failure mode one
    level up: a check that measures something other than what ships.
    """

    paths: list[str] = []
    for raw in (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.upper().startswith("FROM ") and paths:
            break  # the build stage has ended; the runtime stage copies from it, not from here
        if line.upper().startswith("COPY "):
            words = line.split()[1:]
            if words[0].startswith("--from"):
                continue
            paths.extend(words[:-1])  # the last word is the destination
    return paths


def _docker_build_context(destination: Path) -> None:
    """Reproduce what the Dockerfile's build stage actually sees.

        COPY pyproject.toml README.md LICENSE ./
        COPY src ./src
        RUN pip install --no-cache-dir --prefix=/install .

    plus `.dockerignore`, which excludes `*.egg-info/` and the caches.

    **What is NOT in that list is the point** (F85). Two different files will each complete a wheel
    that `package-data` leaves incomplete, because setuptools runs `egg_info` during the build and
    `include_package_data` ships whatever `SOURCES.txt` ends up naming:

      * `MANIFEST.in` — its `graft src` names every file under `src/`. The Dockerfile does not
        `COPY` it, so it is absent from the image build and present in **every** build done here,
        including a clean clone with no editable install and CI's.
      * `src/netcorenoc.egg-info/SOURCES.txt` — left by `pip install -e`, excluded by
        `.dockerignore`.

    Measured as a 2x2 with v0.15.3's per-level globs restored: with either file present the wheel
    carries all 50 UI files; with **both** absent it carries 45 and the console loses the five
    modules under `ui/app/views/parts/`. Only the container had neither, which is why every wheel
    built in this repository was correct and the one the container ran was not. Adding either file
    to this context would not make it more realistic — it would silently disarm the guard, so the
    absence of both is asserted rather than merely arranged.
    """
    import shutil

    destination.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns("*.egg-info", "__pycache__", "*.pyc")
    for name in _dockerfile_copied_paths():
        source = REPO_ROOT / name
        assert source.exists(), f"the Dockerfile copies {name}, which is not in the repository"
        if source.is_dir():
            shutil.copytree(source, destination / name, ignore=ignore)
        else:
            shutil.copy2(source, destination / name)

    completers = [
        p for p in destination.rglob("*") if p.name == "MANIFEST.in" or p.suffix == ".egg-info"
    ]
    assert not completers, (
        f"{[str(p.relative_to(destination)) for p in completers]} would complete the wheel from "
        f"SOURCES.txt regardless of package-data, so this context no longer reproduces the image "
        f"build and the guard below would pass on a wheel the container cannot build (F85)."
    )


def test_a_wheel_built_the_way_docker_builds_one_carries_every_declared_asset(
    tmp_path: Path,
) -> None:
    """**F85.** Build the artefact and look inside it, rather than reasoning about the globs.

    This is the assertion that survives the next packaging mistake whatever shape it takes: a
    missing glob, a new directory, a dot-prefixed path `**` will not match, a `MANIFEST.in` edit,
    or a setuptools change. It compares the appliance's OWN `STATIC_ASSETS` allowlist — the list
    the server will try to open on the first page load — against the members of a real wheel.

    F85 was five `RuntimeError: File at path … does not exist` on a container's first page load,
    for the five modules under `ui/app/views/parts/`. `make qa` was green at 1637 tests, the wheel
    built in the repository was complete, and the wheel the container ran was not.
    """
    import subprocess
    import sys
    import zipfile

    from netcorenoc.api.routes_static import STATIC_ASSETS

    context = tmp_path / "ctx"
    _docker_build_context(context)
    # `--no-isolation` deliberately: build isolation downloads its own setuptools, and a guard
    # that needs the network is a guard that fails on an offline machine for a reason unrelated to
    # the code — the same argument that keeps `pip_audit` out of `make qa`. The dev extra already
    # pins setuptools, so the build backend is present.
    built = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(tmp_path / "dist"),
        ],
        cwd=context,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, f"the Docker-shaped build failed:\n{built.stderr[-2000:]}"

    wheels = list((tmp_path / "dist").glob("*.whl"))
    assert len(wheels) == 1, f"expected one wheel, got {wheels}"
    members = set(zipfile.ZipFile(wheels[0]).namelist())

    missing = sorted(a for a in STATIC_ASSETS if f"netcorenoc/ui/{a}" not in members)
    assert not missing, (
        f"{len(missing)} declared static asset(s) are NOT in a wheel built the way the Dockerfile "
        f"builds one, so the container serves a 500 for each on first load (F85): {missing}"
    )
    assert "netcorenoc/ui/index.html" in members, "the wheel carries no index.html"

    # THE CONTROL. Without it, an empty `STATIC_ASSETS` or a wheel of everything would pass.
    assert len(STATIC_ASSETS) > 40, (
        f"only {len(STATIC_ASSETS)} assets declared; this asserts little"
    )
    assert "netcorenoc/api/routes_static.py" in members, "the wheel has no code in it at all"
    # And the UI must arrive whole, not merely as far as the allowlist reaches: CHECKSUMS.txt and
    # the vendored licences are shipped and audited, and no route serves them.
    on_disk = {
        str(p.relative_to(UI))
        for p in UI.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    absent = sorted(f for f in on_disk if f"netcorenoc/ui/{f}" not in members)
    assert not absent, f"UI files on disk that the Docker-shaped wheel does not carry: {absent}"


def test_the_two_files_that_would_hide_a_packaging_hole_are_not_in_the_image_build() -> None:
    """**F85's invisibility, asserted rather than described.**

    `package-data` is not the only thing that decides a wheel's contents: setuptools runs
    `egg_info` during the build, and `include_package_data` ships whatever `SOURCES.txt` names.
    `MANIFEST.in` here says `graft src`, which names every UI file — so a wheel built in this
    repository is complete whether or not `package-data` is. The Dockerfile does not copy
    `MANIFEST.in`, and `.dockerignore` drops the other completer, `*.egg-info/`.

    This is why F85 shipped: nothing was wrong with any wheel anybody built, and the test suite,
    CI and the release check were all reading one. Keeping `graft src` is right — an sdist that
    could not rebuild the wheel would be a worse defect — so what has to hold is that the *guard*
    builds without it.
    """
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "graft src" in manifest, (
        "MANIFEST.in no longer grafts src/, so the sdist may not carry the UI. If this is "
        "deliberate, the docstring above and F85's second mask no longer describe this repository."
    )
    copied = _dockerfile_copied_paths()
    assert "MANIFEST.in" not in copied, (
        "the Dockerfile now copies MANIFEST.in, so `graft src` completes the image's wheel too and "
        "a package-data hole becomes invisible in production as well as here (F85)."
    )
    assert "src" in copied and "pyproject.toml" in copied, (
        f"the Dockerfile's build stage parsed as {copied}, which cannot be right; the context "
        f"builder above would then assemble something that is not what the image builds."
    )
    assert "*.egg-info/" in (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
