"""Principle 6 gets a test (v0.12.0, Workstream 2).

> *"One runtime identity. One process, one SQLite, **a static UI with no build step and no npm**,
> environment variables as the configuration surface."*

That is the constitution's most structural clause and until this release **nothing in the suite
failed if a `package.json`, a `node_modules/`, a lockfile or a bundler config appeared.** Phase 0
demonstrated it: a tree carrying a `package.json`, three lockfiles, a `vite.config.js` and a tracked
`node_modules/` passed all 1302 tests. `SECURITY-REVIEW-0.11.0.md` §3.4 named that class, and the
release that is about to touch the UI is the moment to close it — v0.13.0 is exactly when the
temptation arrives.

### The tool / product distinction, written here because v0.13.0 will read it while vendoring

**Node as a test dependency is permitted. Node as a build step for shipped assets is not.**

The DOM harness (`tests/support/domharness/`) runs under Node. It is stdlib-only, needs no `npm
install`, vendors nothing, and produces nothing that ships: `pyproject.toml`'s `package-data` names
`ui/*` and `migrations/*`, and no `.mjs` file is inside it. The appliance still runs on five runtime
dependencies and a static UI a browser loads directly.

What principle 6 forbids is a **transformation between the source and what a browser receives**: a
bundler, a transpiler, a minifier, a lockfile that has to be resolved before the UI exists. The
test below does not care whether Node is installed; it cares whether the *tracked tree* contains the
apparatus of such a transformation.

### Why the file list comes from git

v0.10.1's F51 was a guard scoped by a literal string: `_SKIP_DIRS` excluded `.venv` **by name**, so
a virtualenv called anything else stopped being skipped and the guard started reporting on files
nobody in this repository wrote. A directory walk here would repeat it in the mirror image — an
artefact under a directory the skip-list happened to name would stop being *found*.

`git ls-files` has no such failure mode: it answers "what does this repository contain", which is
precisely the question. It also draws the line in the right place for the harness. An **untracked**
`node_modules/` that some tool created locally is not in the tree the maintainer ships and is not
what this guard is about; a **tracked** one is. §3 asserts both directions, through the same
extractor, against a real repository.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

import pytest

import paths

REPO_ROOT = paths.REPO_ROOT
UI_DIR = REPO_ROOT / "src" / "netcorenoc" / "ui"

#: Exact filenames that only exist to drive a JavaScript package manager or bundler.
BUILD_STEP_FILES = frozenset(
    {
        "package.json",
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "bun.lockb",
        ".npmrc",
        ".yarnrc",
        ".yarnrc.yml",
        "webpack.config.js",
        "rollup.config.js",
        "rollup.config.mjs",
        "gulpfile.js",
        "Gruntfile.js",
        "babel.config.js",
        ".babelrc",
        "tsconfig.json",
        "jsconfig.json",
        "svelte.config.js",
        "next.config.js",
        "nuxt.config.js",
        "angular.json",
    }
)

#: Filename *stems* whose any-extension form is a bundler configuration.
BUILD_STEP_STEMS = frozenset({"vite.config", "esbuild.config", "snowpack.config", "parcel.config"})

#: Path components that may never appear in a tracked path.
BUILD_STEP_DIRS = frozenset({"node_modules", ".yarn", ".pnpm-store", "bower_components"})


def tracked_files(root: Path) -> list[str] | None:
    """Every path `git` tracks under `root`, or ``None`` when this is not a git repository.

    ``None`` rather than an empty list, and never a silent fallback to a directory walk: an
    extractor that answered "no files" outside a repository would report every tree clean, which
    is Appendix B's "measuring nothing and concluding CLOSED" with a guard's name on it.
    """
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - no git on PATH
        return None
    if proc.returncode != 0:
        return None
    return [entry for entry in proc.stdout.split("\0") if entry]


def _is_build_step(entry: str) -> bool:
    """Four independent reasons a tracked path is build-step apparatus.

    Kept as four named predicates rather than one boolean expression: each is a separate claim, and
    `test_the_guard_finds_a_package_json_that_is_actually_there` adds each class to a real
    repository **separately** so a partial extractor cannot pass by recognising only one of them.
    """
    parts = Path(entry).parts
    name = parts[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    inside_package_dir = any(part in BUILD_STEP_DIRS for part in parts)
    is_manifest_or_lockfile = name in BUILD_STEP_FILES
    is_bundler_config = stem in BUILD_STEP_STEMS
    is_generated_ui = "ui" in parts and "dist" in parts
    return inside_package_dir or is_manifest_or_lockfile or is_bundler_config or is_generated_ui


def build_step_artefacts(paths: list[str]) -> list[str]:
    """The build-step apparatus in `paths`. Pure, so it can be driven with any list of names."""
    return sorted(entry for entry in paths if _is_build_step(entry))


# --- §1 the guard itself -----------------------------------------------------------------------


def test_the_tracked_tree_contains_no_build_step_apparatus() -> None:
    """Principle 6, as a test. **The assertion that did not exist before v0.12.0.**

    If this fails, read the module docstring before deleting the offending file: a Node *test*
    dependency is permitted and would not trip this, so a hit here means something in the tracked
    tree wants to transform the UI before a browser sees it.
    """
    paths = tracked_files(REPO_ROOT)
    assert paths is not None, (
        "`git ls-files` did not answer, so this guard has no file list and is not guarding. "
        "It derives from the tracked set deliberately (see the module docstring); it must never "
        "fall back to a directory walk."
    )
    artefacts = build_step_artefacts(paths)
    assert not artefacts, (
        "build-step apparatus is tracked in this repository, which breaks principle 6 "
        "(a static UI with no build step and no npm):\n  " + "\n  ".join(artefacts)
    )


def test_the_extractor_is_looking_at_a_populated_tracked_set() -> None:
    """Guard the guard, part one: the file list must be non-empty and must be *this* repository.

    A `git ls-files` that answered with nothing would make the assertion above vacuous, and it is
    the single most likely way this guard silently stops guarding.
    """
    paths = tracked_files(REPO_ROOT)
    assert paths is not None and len(paths) > 200, f"suspiciously small tracked set: {paths}"
    for expected in ("pyproject.toml", "src/netcorenoc/ui/app.js", "Makefile"):
        assert expected in paths, f"{expected} is not in the tracked set; the extractor is wrong"


# --- §2 the vacuity check, through the same code path ------------------------------------------


@pytest.fixture
def scratch_repo(tmp_path: Path) -> Path:
    """A real, tiny git repository. Real because the extractor runs `git ls-files`, and a fixture
    that bypassed git would test a different function than the one the guard uses (the v0.9.2
    lesson: a first version of a guard test called the helper directly and stayed green when the
    caller was reverted)."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("scratch\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    return tmp_path


def test_the_guard_finds_a_package_json_that_is_actually_there(scratch_repo: Path) -> None:
    """**The vacuity check.** A broken extractor reports every tree clean.

    Driven end to end — a real repository, a real `git add`, the real extractor — because that is
    the path the guard takes. Each artefact class is added separately so a partial extractor
    cannot pass by finding one of them.
    """
    for relative in (
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "vite.config.js",
        "node_modules/left-pad/index.js",
        "src/netcorenoc/ui/dist/bundle.js",
    ):
        target = scratch_repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}\n", encoding="utf-8")
        subprocess.run(["git", "add", "-f", relative], cwd=scratch_repo, check=True)

        paths = tracked_files(scratch_repo)
        assert paths is not None
        assert relative in build_step_artefacts(paths), (
            f"the extractor did not flag {relative!r}; this guard would report a tree with it clean"
        )

        subprocess.run(["git", "rm", "-q", "--cached", relative], cwd=scratch_repo, check=True)
        target.unlink()


def test_a_clean_scratch_repo_is_reported_clean(scratch_repo: Path) -> None:
    """The other half of the vacuity check: an extractor that flagged everything would also pass
    the test above. A repository with only a README must come back empty."""
    paths = tracked_files(scratch_repo)
    assert paths == ["README.md"]
    assert build_step_artefacts(paths) == []


# --- §3 tracked versus present: where the line is, and that it is really there -------------------


def test_an_untracked_node_modules_is_out_of_scope_and_a_tracked_one_is_not(
    scratch_repo: Path,
) -> None:
    """The line this guard draws, asserted in **both** directions.

    The harness needs no `node_modules` and creates none. But the rule has to be stated for the
    day some tool makes one: an **untracked** directory is not part of what this repository
    contains and this guard does not look at it; a **tracked** one is a build step and fails.

    Saying this is not a loophole, it is the guard's scope. What an untracked `node_modules` would
    still be is *visible* — it is not in `.gitignore`, deliberately, so `git status` shows it. A
    guard that could not see it and an ignore rule that hid it would together be worse than the
    guard alone.
    """
    (scratch_repo / "node_modules" / "left-pad").mkdir(parents=True)
    (scratch_repo / "node_modules" / "left-pad" / "index.js").write_text("x\n", encoding="utf-8")

    paths = tracked_files(scratch_repo)
    assert paths is not None
    assert build_step_artefacts(paths) == [], "an UNTRACKED node_modules was flagged"

    subprocess.run(["git", "add", "-f", "node_modules"], cwd=scratch_repo, check=True)
    paths = tracked_files(scratch_repo)
    assert paths is not None
    assert build_step_artefacts(paths) == ["node_modules/left-pad/index.js"]


def test_node_modules_is_not_hidden_by_gitignore() -> None:
    """`.gitignore` must not conceal the thing the guard is about.

    Ignoring `node_modules/` would be the obvious tidy-up and it is the wrong one: it would take
    the only remaining signal — a dirty `git status` — away from a maintainer whose machine had
    grown one, while leaving the tracked-file guard unable to see it by construction.
    """
    ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert not any(line.strip().strip("/") == "node_modules" for line in ignore)


def test_the_harness_is_a_tool_and_is_not_flagged_as_a_product_build_step() -> None:
    """The tool/product distinction, asserted rather than only written down.

    The harness is tracked, runs under Node, and must come back clean — otherwise the guard would
    forbid the instrument that tests the thing it protects. What makes it a tool: no package
    manifest, no lockfile, nothing packaged, nothing generated into `ui/`.
    """
    paths = tracked_files(REPO_ROOT)
    assert paths is not None
    harness = [p for p in paths if p.startswith("tests/support/domharness/")]
    assert harness, "the harness is not tracked; this test is asserting nothing"
    assert build_step_artefacts(harness) == []
    config = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    package_data = config.split("[tool.setuptools.package-data]", 1)[1].split("[tool.", 1)[0]
    assert ".mjs" not in package_data and "domharness" not in package_data


def test_the_ui_is_still_loaded_directly_by_the_browser() -> None:
    """The property principle 6 is actually about: what a browser receives is what is in the tree.

    **`type="module"` is now permitted and `importmap` is still not**, and the distinction is the
    whole of this test. v0.12.0 forbade both together because the UI was one classic script and
    neither was needed. They are not the same thing:

    * a module script is still *one file, fetched by name, executed as written* — no
      transformation happens between the tree and the browser, which is what principle 6 is about;
    * an **import map** is an inline `<script type="importmap">`, which `script-src 'self'` forbids
      outright, and which exists precisely to let bare specifiers resolve — the first step towards
      a resolver, a lockfile and a bundler.

    So: two script tags, both same-origin, both naming a file that exists on disk byte-for-byte as
    served. No bundle, no hashed filename, no manifest, no import map.
    """
    index = (UI_DIR / "index.html").read_text(encoding="utf-8")
    sources = [line for line in index.splitlines() if "<script" in line]
    # **One tag since v0.15.2** (DECISIONS #228). d3 was the second, and it made every screen pay
    # 279 706 bytes for the two that draw with it; `app/vendor.js` now appends the same
    # same-origin element when one of those two mounts. This assertion got *stronger* rather than
    # weaker: it used to check the two literals in this file, and now it checks every script src
    # the console can ever load, wherever the literal is written.
    assert len(sources) == 1, sources
    assert (UI_DIR / "app.js").exists()
    assert 'type="module" src="/app.js"' in index
    assert "importmap" not in index

    # Every `src` any console file names: root-relative, same-origin, and a file on disk.
    named = set(re.findall(r'src\s*=\s*"([^"]+)"', index))
    for module in sorted(UI_DIR.rglob("*.js")):
        if "vendor" in module.parts:
            continue
        named |= set(re.findall(r'\.src\s*=\s*"([^"]+)"', module.read_text(encoding="utf-8")))
        named |= set(
            re.findall(r'loadVendorScript\(\s*"([^"]+)"', module.read_text(encoding="utf-8"))
        )
    # v0.22.0: d3 is gone, so `app.js` is the one script any console file names — and a second,
    # whether a vendored library brought back or a script appended at runtime, fails here.
    assert named == {"/app.js"}, (
        f"a console file names a script other than /app.js: {sorted(named - {'/app.js'})}"
    )
    for src in sorted(named):
        assert src.startswith("/"), f"{src!r} is not a root-relative same-origin path"
        assert "//" not in src, f"{src!r} names another origin"
        assert (UI_DIR / src.lstrip("/")).exists(), f"{src!r} is not a file in this tree"
    # Every relative import in every module resolves to a file that exists. A specifier that only
    # a resolver could satisfy is the thing an import map would be introduced for.
    for module in sorted(UI_DIR.rglob("*.js")):
        if "vendor" in module.parts:
            continue
        # Anchored to a statement at the start of a line. An unanchored `from "…"` also matches
        # prose inside a string literal — the word "from" followed by a quoted fragment — which is
        # how the first version of this reported `session.js` as importing ' +\n      '.
        source_text = module.read_text(encoding="utf-8")
        for specifier in re.findall(r'^import\s[^;]*?from\s+"([^"]+)"', source_text, re.MULTILINE):
            assert specifier.startswith("."), (
                f"{module.name} imports the bare specifier {specifier!r}, which needs an import "
                f"map — an inline script the CSP forbids"
            )
            assert (module.parent / specifier).resolve().exists(), (
                f"{module.name} imports {specifier!r}, which is not a file in this tree"
            )


# --- §4 the four UI files, pinned ---------------------------------------------------------------
#
# Here rather than in a file of its own because it asserts the same thing from the other side: not
# only is there no machinery to transform the UI, the UI itself did not move.

#: SHA-256 of every shipped UI file at **v0.14.0**. v0.12.0's table said: *"A later release will
#: change these files, and will update this table in the same commit. That is the point: the change
#: becomes a deliberate, reviewable line in a diff rather than something that happens while someone
#: is in the file for another reason."* v0.13.0 was that release for the rewrite; this is that
#: commit for the model family.
#:
#: **Four lines moved and two are new.** `app/views/scorer.js` and `app/views/promotion.js` changed;
#: `app/views/model.js` and `app/views/verdict.js` are new. Nothing else in the console was touched
#: by a release that added three scorer kinds — which is the property this table exists to make
#: visible at a glance rather than to be argued.
UI_HASHES: dict[str, str] = {
    "app.js": "c238694d8a5f87a67fa8c71d46aa5904a36bf2dd9d96792d31274ab7c9b7a310",
    "app/api.js": "db042593c344a37f646c3ab5df2c1a8b2abbae18d06bf0b2ffce789406d9ed1a",
    "app/avatar.js": "98da945d8a5ff6ffe7b48d22d89e9f7e003f3cf78422d70e72a51bb0b731844e",
    "app/chartdata.js": "b503668f63c2d46ae1017744d04d268ee2f3e365b95fdae793a0de82c5b45032",
    "app/charts.js": "f0043e04b0c4ba8b6a7e100f73bda78c67692ef00c8080497ffcb12cecee6418",
    "app/compare.js": "c13ceb94e46894d6c1cae3013ba9e600e8e8ff07db75b5528df49fdd96663758",
    "app/destructive.js": "4a37ec64d13dcb5d83c0edb4dcbdca90a1bb99ab4551909a72abf40c74f633d4",
    "app/dom.js": "45f1971942517b3ea60c1346433e0b8ace6e1c2eeeedbf58bec81bf78ec1a582",
    "app/format.js": "270b0788ec61b5166ef4dc8589755b06caafe5ba87829bd419d115926521ac4c",
    "app/health.js": "ff0aca2ec48ebbe23b7c42046b4178de85a2aeb1f11db3c079a8e89c42463db8",
    "app/icons.js": "dbe05fd894c4baa5eb4ca251fb9b440c78762885f8aa15b7c17646bdd11ec672",
    "app/info.js": "15e06186daadead308f180d23feb479eccf2506d70bfadbda477ccc6f7d3c11f",
    "app/layout.js": "447b775510c160d906ed6c54831954734c1f8bd70e90079a1fc280d6a9bad577",
    "app/login.js": "ee936acd5ec82c2299416c9c87e594ab954b42dad137db4e8662eb6eded104fc",
    "app/modelcharts.js": "92707eab7b1f3b1280bacaa30aa2b9cd75ec6b7ee450267be7bc685eb1d1a580",
    "app/netgraph.js": "30c308afc4fc650550ce0751d212a54145c293629552028ecf58c2061951251a",
    "app/notices.js": "65f1adc13ddbb4a1f5d4fd85e8c1d476079e3872ab64e74f4dd41df76dcab7c1",
    "app/parameters.js": "74431c96a3c6a9b12c29841e56eb13210df6ef78f5dfda054e05fa285eda1cae",
    "app/password.js": "6c85d8111fcaa2da415910744064c84e41adf5db57fc953cad9f1f14543ec11d",
    "app/registry.js": "0c27e3c227ad86527dda0476d3f80934adc12f8924ed71617c4e956c9aa74257",
    "app/router.js": "bfd465ac573a2420c862e6c804f8d151fba3d02c588e3c3ff0cf3aa160e244b9",
    "app/session.js": "273d9313c45637fdcade7aa4fb931f1b1175683aee95a2dd65c76b422c8871a3",
    "app/shell.js": "cfaeaf527f19fef5a0674dbbbbfaa0709d181506939e11e1726fac93d526681d",
    "app/sidebar.js": "8625e151e0ec4d15f4d64c1d16eace797e4341e5c8361694f9bad371d3536bfe",
    "app/stack.js": "e2f41526048800942fec89a25ffb6dc1fda5d650b3b96dc3ba692438241aaaba",
    "app/store.js": "30e1a8c8e02f60639589dfd143466554588a1b4fbcec3903fae10fc71dcd77e0",
    "app/theme.js": "0971f3067a744fd448517fcc2ce230fa468e6ca1e2bf813839b7378bad699419",
    "app/views/access.js": "f490040f823b46de143d858a11c92299e780e36f3d5a39aa9a8cb13bc770c893",
    "app/views/account.js": "7c81e38c53d3a61e52e47ca124db2a5c5deaa9d2599e7322061b259ff830d24b",
    "app/views/audit.js": "5f6de01c2eb9ed02f920f9c0165be345ea127aafe186e6d67ad05f7e00b5ce71",
    "app/views/classes.js": "3e99c1fd9d0f7a98a526d913107cdb2de24954656de525f213c4aea284358970",
    "app/views/corpus.js": "ccaae5cbf03bca4a13b9a8c96a420185eaf930459a5e72110d4f3ec05e29b1de",
    "app/views/entities.js": "36a2ae7b50e6c01270d5e37040658638f0ea206489aada3313c7950903d7da6d",
    "app/views/graph.js": "8792bb402c81f9125c275f9741b5f9e1c79791a39d1a32bf70092388f55c595a",
    "app/views/labelling.js": "7c38c9bc41f0605ef284e81f7dc54d727ad5267ee45b91966bf3c02292901659",
    "app/views/maintenance.js": "12db3a056e9e7e66e2f6fc3b7b2ca0d73bb09a2852cbae0212b575d148e87290",
    "app/views/overview.js": "490d62bce6c45031e944d873badfc3bb3e806bd976e2b93e72d55670500ac50c",
    "app/views/parts/autonomy.js": (
        "5c19fa6f52ec9740c64b0a9ae40d6a1b467ee10fde39dc86b376ed358431ddda"
    ),
    "app/views/parts/bulkclear.js": (
        "efdd15fe669b8a65b2a8c02745f07c0f1903fa619a60179f55b369025dc01298"
    ),
    "app/views/parts/capgrid.js": (
        "f9cd90605c2cee7ac3bdb4d1695c9f67383119d7add56a97d43d5efaddf7175e"
    ),
    "app/views/parts/card.js": "57fbcf2a2fb4ac0221bd9324e857cbc6094d04eb9beb5172c7a2dd4c7d53818b",
    "app/views/parts/correlation.js": (
        "a29a11b819187876b40d8e37d72c21f51c057d0e397a28f32a2d98dac0b171de"
    ),
    "app/views/parts/decide.js": "6474cd33ca0ffc92a0317d965d0c9dc78e7b7277d9160f34abfb6c1cd9cf4f11",
    "app/views/parts/decider.js": (
        "96e03e99982bd620e777bc771b773f1c3e0c71ccf7cb19080ee2c96b8d84930f"
    ),
    "app/views/parts/declare.js": (
        "852b5979244ae32cb1e7ba46a3f19c0412886f8910138f3a22ae1f0dcc82f3b7"
    ),
    "app/views/parts/element.js": (
        "7ad70f3bfaaef3f13f7dea4af2c6651beffeca8d666952574c6338456b2410ef"
    ),
    "app/views/parts/evidence.js": (
        "98cc15c6870dcb3a2b004353adaac5ce411e7937ab2cd6bdeb7f94b76c1098e6"
    ),
    "app/views/parts/facts.js": "2befcc9ed54b18d57de942f3f9583e00a0a39170bb2b45d68b29933881858f12",
    "app/views/parts/finder.js": "40a84469df45e93a451e7fd39d462ed51fbeff786a56571cfccb270ae1002b92",
    "app/views/parts/importbox.js": (
        "5e549b9dec2f8518d9c399d40e978cc461017cfd9845d0f4a1770acb3cd98319"
    ),
    "app/views/parts/judge.js": "4b23b348a4a42eb65c78ed5df6b50d9bd461dc951e9c99a449c8d1d99b09a398",
    "app/views/parts/keeping.js": (
        "10bf18e8e753121f3c51a130c8470aa3858e0d1b4cb7ce954b041228e97dfa56"
    ),
    "app/views/parts/league.js": "ae72a6a6ebdeb1da9ed1fff4c400678bc346dcfb3057e356b947912f11dc38fa",
    "app/views/parts/leaguecharts.js": (
        "4299f0a6e889d4f91655905abf997cd5e83632abec011270970161f6276e0db2"
    ),
    "app/views/parts/leaguecompare.js": (
        "e2f1f27b66716e861d3e1492eac4085c61634ebd706ce0b87c73b64f10697e65"
    ),
    "app/views/parts/lifecycle.js": (
        "b6adfa04f820b995c7f7fdd909625b9a1dad48821cfc0ba89a62e4c872ee6efb"
    ),
    "app/views/parts/marks.js": "28253d0b86ef3dd372ea7f26cf2608648ca5e66c16c682058d3287225780a148",
    "app/views/parts/members.js": (
        "1435449d80e78e62d3390d12818a235b13055e05b51e1063d813dc8721591f42"
    ),
    "app/views/parts/model.js": "c30966786ff8e78e8c326b3b0206fe171298e86ef784b0841d68999ef068e81d",
    "app/views/parts/models.js": "fc9ac57e955a70a8fe735284f9a9134f1bc77747c6bef6f57a4653569bb87e65",
    "app/views/parts/mwdetail.js": (
        "f66649c7d172a5e864891926ea4bd9c04a00fad02d173648688aff83899f775a"
    ),
    "app/views/parts/mwdraft.js": (
        "9773ea5ea47bc0f49cb1d2367be91c275b31a05f1ff36600d27abba8fff732de"
    ),
    "app/views/parts/mwform.js": "ed3a225a8d27ff58a68e59d4b1dd2b8725a118b4d8473d9f4316d0c85a490e93",
    "app/views/parts/mwmarker.js": (
        "c3e7ed23febacd82a4fd7142064b3d1d26fc7d8cade7e29bd7930454d2e3bd9a"
    ),
    "app/views/parts/mwreview.js": (
        "ecd1c0684921c5c0b9519bb6cebdf35406903c4f55d9666fe017a549fd6f5611"
    ),
    "app/views/parts/mwrules.js": (
        "c77b970377ec6eed2c90607c41b03399783ee9348f3fa357d01425f95a554327"
    ),
    "app/views/parts/mwsummary.js": (
        "ffa5d39073b25e99fd18919ecb7a9cb051f613e6aa2741ff5a61633d2b233e84"
    ),
    "app/views/parts/mwtime.js": "259d27952d3bf185c87b38e60f9eed9c2aea40ab52cfc03e31019d4a1df8088c",
    "app/views/parts/nedetail.js": (
        "9c287e1ffb18b079b417e42c9eaf61f8ace86c2065ecbdf868c4145ab5cdc0ff"
    ),
    "app/views/parts/oidtree.js": (
        "3458a69deca0eab3513684c67b37e2409f16b4db60d6713b64622f1cd8bb41e9"
    ),
    "app/views/parts/people.js": "a34afe2b0c52447377f51d6490c09ded9c1a3bed7479c844ed83b2022b54b1cf",
    "app/views/parts/pulse.js": "ac7e9bc59841185e5304d9d3fdc3fae8759ddf42376098efeb2b76e51b043e01",
    "app/views/parts/restructure.js": (
        "67be9cae2322b50ffa8646d8ccbb1c9b236e6c084c6cd88c5d76a0805b1c2f90"
    ),
    "app/views/parts/retention.js": (
        "324476d870b585473a7cce2575890ac35fb47bb339d519ed496086912b50aca8"
    ),
    "app/views/parts/roles.js": "d498e21afbf8d00c04f5a70e082e5868912d3fa0a96010777e1f30793b71511f",
    "app/views/parts/searchpanel.js": (
        "a5fbe3713c765c6afa82d926bda1bb5576da68161e83544bfc4f2540463e0f8b"
    ),
    "app/views/parts/sequence.js": (
        "9a70f2aa94a13c41c9d7e8a8fa4c59e5e6ce87814a9607305cbf524e9c7643f3"
    ),
    "app/views/parts/severity.js": (
        "799c125f48183a7de1a6597cfac400f4d0d0974de7daee4b1ddea7e485f53b08"
    ),
    "app/views/parts/sitejudge.js": (
        "ca99e39989f3c3949518fcda8cf0218fa78cf91faa0c1d8ff4a2eef6e65a239e"
    ),
    "app/views/parts/sitsummary.js": (
        "7e82edc2fa043820c08fe10e515c794284858d896898278771246675021a59a3"
    ),
    "app/views/parts/tlfilters.js": (
        "13260494504327f384bebebaced9f791e71b60243325dafa00b5bf23b04a0092"
    ),
    "app/views/parts/tokenspanel.js": (
        "fa00dbddace4f2c7fd33f3d284b070c784704e149da60e480028e05379927e84"
    ),
    "app/views/parts/topassets.js": (
        "c3c20d5c60fae1497ff7f449630971ab76656f56d9c7a010a3d4a8d2e3c13710"
    ),
    "app/views/parts/verdict.js": (
        "bfedceae5645684afeaf50c636bc56f2626f6822a7b03e6f45af9f6f9bc2a9d3"
    ),
    "app/views/parts/visibility.js": (
        "cfb004d75fa0c23592771e173ebbc62c579c49d95a203d4334bfcfd93dc2f724"
    ),
    "app/views/parts/why.js": "91807cd9bdb236e2980b987c36bbd9ff33f2450461890cf646dfdca2c3c69c88",
    "app/views/promotion.js": "221614ebee6f61aca720ad1822246a2ab7f8f55cda61c936deaa70f9a084cdae",
    "app/views/quarantine.js": "04ed768d8180e7d3182086e8dd12c69c8a497196bfb0086ea5023e34a5470b5b",
    "app/views/scorer.js": "0ed95cd927706a5ab1c9452e6d935363fab28b95f565ac0d232d41195f9de4c2",
    "app/views/settings.js": "3ec5098102f253de6dee9be442943990d37f09e7cd4649d4ba296991550edd4f",
    "app/views/situations.js": "afe374be8e8e920369da6fe767294cc29a7cec7fea9899a6deace262f1509241",
    "app/views/timeline.js": "f023cedba738a32c4961e4c0cb051f82bd4244be5548556c4c2633c6cfe9fe54",
    "app/widgets.js": "32ee7263e4f7b1905b5d8acd68532635a9351df2a5d811a88e814c268052c5c7",
    "favicon.svg": "c11ec68d389057cc4d4145b3cdf77f3ebfec40150e9f409ff35a7cf419f524b7",
    "index.html": "d057123e5cfc1ab497db465df016c598e38b049fbde26ecc9ee868eb4899fad7",
    "style.css": "fe434eece55f9d819801e31c2f9902996eb84027cad4e7496cd4547a684d575a",
    "vendor/CHECKSUMS.txt": "408939056400f45a215cb576dc324bfc64f1b6bfae2d1b45bfce061b508128e8",
    "vendor/htm-3.1.1.module.js": (
        "ab33dd3f38059b9be4d5f5350128eefb2356639c4e0bbe9d9e8b3ba75847e9e4"
    ),
    "vendor/htm.LICENSE": "740725f7252e750af735d0028cc534970772f513331e9f68150fede8fb3ce00f",
    "vendor/preact-10.29.8.module.js": (
        "c30e721ebfdc6e2ad4c18c14d2dfb82667829c8aec27de1207774e3fc16858a8"
    ),
    "vendor/preact.LICENSE": "1fe6958409c8c257a70c587a18b6f7f412b179b456630790d30b2ec9a8e4b7d4",
}

#: Byte sizes, recorded beside the hashes because a size is the figure a reader can check by eye.
#: `app.js` went from **52 738 bytes in one file** to an entry point plus 36 modules.
UI_SIZES: dict[str, int] = {
    "app.js": 5_545,
    "app/api.js": 5_405,
    "app/avatar.js": 6_707,
    "app/chartdata.js": 8_533,
    "app/charts.js": 15_836,
    "app/compare.js": 3_080,
    "app/destructive.js": 4_811,
    "app/dom.js": 2_153,
    "app/format.js": 15_764,
    "app/health.js": 7_609,
    "app/icons.js": 7_583,
    "app/info.js": 2_407,
    "app/layout.js": 9_799,
    "app/login.js": 7_137,
    "app/modelcharts.js": 16_437,
    "app/netgraph.js": 11_593,
    "app/notices.js": 14_370,
    "app/parameters.js": 10_270,
    "app/password.js": 5_442,
    "app/registry.js": 8_737,
    "app/router.js": 5_038,
    "app/session.js": 4_066,
    "app/shell.js": 15_821,
    "app/sidebar.js": 9_434,
    "app/stack.js": 6_357,
    "app/store.js": 5_488,
    "app/theme.js": 8_134,
    "app/views/access.js": 4_850,
    "app/views/account.js": 7_295,
    "app/views/audit.js": 6_490,
    "app/views/classes.js": 8_085,
    "app/views/corpus.js": 5_527,
    "app/views/entities.js": 8_496,
    "app/views/graph.js": 9_351,
    "app/views/labelling.js": 6_324,
    "app/views/maintenance.js": 8_093,
    "app/views/overview.js": 10_909,
    "app/views/parts/autonomy.js": 10_549,
    "app/views/parts/bulkclear.js": 3_374,
    "app/views/parts/capgrid.js": 6_751,
    "app/views/parts/card.js": 9_467,
    "app/views/parts/correlation.js": 7_762,
    "app/views/parts/decide.js": 6_460,
    "app/views/parts/decider.js": 9_827,
    "app/views/parts/declare.js": 13_092,
    "app/views/parts/element.js": 6_168,
    "app/views/parts/evidence.js": 12_652,
    "app/views/parts/facts.js": 6_340,
    "app/views/parts/finder.js": 6_650,
    "app/views/parts/importbox.js": 5_982,
    "app/views/parts/judge.js": 15_369,
    "app/views/parts/keeping.js": 7_186,
    "app/views/parts/league.js": 11_542,
    "app/views/parts/leaguecharts.js": 10_489,
    "app/views/parts/leaguecompare.js": 6_080,
    "app/views/parts/lifecycle.js": 8_044,
    "app/views/parts/marks.js": 5_998,
    "app/views/parts/members.js": 10_935,
    "app/views/parts/model.js": 10_290,
    "app/views/parts/models.js": 3_020,
    "app/views/parts/mwdetail.js": 7_956,
    "app/views/parts/mwdraft.js": 9_873,
    "app/views/parts/mwform.js": 14_757,
    "app/views/parts/mwmarker.js": 3_574,
    "app/views/parts/mwreview.js": 4_760,
    "app/views/parts/mwrules.js": 7_636,
    "app/views/parts/mwsummary.js": 6_327,
    "app/views/parts/mwtime.js": 9_554,
    "app/views/parts/nedetail.js": 7_889,
    "app/views/parts/oidtree.js": 6_358,
    "app/views/parts/people.js": 10_048,
    "app/views/parts/pulse.js": 5_546,
    "app/views/parts/restructure.js": 12_891,
    "app/views/parts/retention.js": 5_090,
    "app/views/parts/roles.js": 4_816,
    "app/views/parts/searchpanel.js": 5_893,
    "app/views/parts/sequence.js": 5_776,
    "app/views/parts/severity.js": 3_912,
    "app/views/parts/sitejudge.js": 6_236,
    "app/views/parts/sitsummary.js": 3_596,
    "app/views/parts/tlfilters.js": 5_030,
    "app/views/parts/tokenspanel.js": 7_287,
    "app/views/parts/topassets.js": 3_767,
    "app/views/parts/verdict.js": 8_420,
    "app/views/parts/visibility.js": 4_041,
    "app/views/parts/why.js": 15_573,
    "app/views/promotion.js": 13_197,
    "app/views/quarantine.js": 2_247,
    "app/views/scorer.js": 13_462,
    "app/views/settings.js": 13_446,
    "app/views/situations.js": 15_602,
    "app/views/timeline.js": 7_227,
    "app/widgets.js": 13_969,
    "favicon.svg": 608,
    "index.html": 1_549,
    "style.css": 167_581,
    "vendor/CHECKSUMS.txt": 2_016,
    "vendor/htm-3.1.1.module.js": 1_207,
    "vendor/htm.LICENSE": 11_341,
    "vendor/preact-10.29.8.module.js": 11_693,
    "vendor/preact.LICENSE": 1_087,
}

#: What the one file measured at v0.12.0, kept so the diff states the change
#: rather than implying it.
V0_12_0_APP_JS_BYTES = 52_738


def test_the_shipped_ui_is_exactly_what_this_release_pinned() -> None:
    """The pin, carried forward. Every shipped UI file, by hash, updated deliberately.

    The table's job does not change with the release: a UI file that moves without this line
    moving with it is a change nobody reviewed.
    """
    on_disk = {
        str(path.relative_to(UI_DIR))
        for path in UI_DIR.rglob("*")
        if path.is_file() and ".well-known" not in path.parts
    }
    assert on_disk == set(UI_HASHES), (
        f"the shipped UI file set moved.\n"
        f"  on disk, unpinned: {sorted(on_disk - set(UI_HASHES))}\n"
        f"  pinned, missing:   {sorted(set(UI_HASHES) - on_disk)}"
    )
    for relative, expected in UI_HASHES.items():
        digest = hashlib.sha256((UI_DIR / relative).read_bytes()).hexdigest()
        assert digest == expected, f"src/netcorenoc/ui/{relative} changed: {digest}"
    for relative, size in UI_SIZES.items():
        assert (UI_DIR / relative).stat().st_size == size, relative


def test_the_one_file_became_a_module_graph_and_no_module_is_the_old_file_renamed() -> None:
    """Part XI: *replace the shape, not just the file.*

    52 738 bytes in one file with 55 top-level functions was the problem. One file of the same size
    in a new syntax would be the problem, renamed — and it would pass every other test in this
    repository. So the shape is asserted: many modules, none of them anywhere near the old size,
    and the entry point smallest of all because it only boots.
    """
    modules = {
        str(path.relative_to(UI_DIR)): path.stat().st_size
        for path in UI_DIR.rglob("*.js")
        if "vendor" not in path.parts
    }
    assert len(modules) >= 20, f"the UI is {len(modules)} files; that is not a module graph"
    largest = max(modules.items(), key=lambda item: item[1])
    assert largest[1] < V0_12_0_APP_JS_BYTES // 3, (
        f"{largest[0]} is {largest[1]} bytes, more than a third of the 52 738-byte file this "
        f"release replaced. The shape has not changed, only the syntax."
    )
    assert modules["app.js"] < 6_000, (
        f"the entry point is {modules['app.js']} bytes; it should boot and nothing else"
    )


def test_the_two_ui_pins_cover_exactly_the_same_files() -> None:
    """`UI_SIZES` and `UI_HASHES` must describe the same file set, and this is what says so.

    **A pin whose membership can shrink in silence is not a pin.** v0.16.4 added three UI modules
    and the size pin never learned about them: the helper that regenerates it intersected the fresh
    list against the keys already present — a fix for a rebuild that had dropped entries — which
    also made it structurally incapable of gaining any. Nothing failed, because every test over
    `UI_SIZES` iterates `UI_SIZES`. Five files were outside it by the time anyone looked.

    The hash pin answers *"did this change?"* and the size pin answers *"by how much?"*, and the
    second is the one a reviewer scans to see whether a UI diff is the size its commit message
    claims. Neither can do its job over a subset nobody chose.
    """
    assert set(UI_SIZES) == set(UI_HASHES), (
        "the two UI pins have drifted apart.\n"
        f"  hashed but not sized: {sorted(set(UI_HASHES) - set(UI_SIZES))}\n"
        f"  sized but not hashed: {sorted(set(UI_SIZES) - set(UI_HASHES))}\n"
        "Regenerate both over the whole tree; never filter one against the other's keys."
    )
