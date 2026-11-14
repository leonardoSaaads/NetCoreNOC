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
`ui/*` and `migrations/*`, and no `.mjs` file is inside it. The appliance still runs on six runtime
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
    "app/avatar.js": "259c4a7bc6d13140d8e7629a053d55d796e2accf54acac141935c82204ddced6",
    "app/brands.js": "7c9622cc766564938731f5b51465b968833100e81f7f2fcc83d3e1c06065466a",
    "app/chartdata.js": "b503668f63c2d46ae1017744d04d268ee2f3e365b95fdae793a0de82c5b45032",
    "app/charts.js": "f0043e04b0c4ba8b6a7e100f73bda78c67692ef00c8080497ffcb12cecee6418",
    "app/compare.js": "a3b5f7305d0725efb3657f5a5bd861dae46a6dde48209890fc5f80e5e081fb17",
    "app/destructive.js": "4a37ec64d13dcb5d83c0edb4dcbdca90a1bb99ab4551909a72abf40c74f633d4",
    "app/dom.js": "45f1971942517b3ea60c1346433e0b8ace6e1c2eeeedbf58bec81bf78ec1a582",
    "app/format.js": "270b0788ec61b5166ef4dc8589755b06caafe5ba87829bd419d115926521ac4c",
    "app/health.js": "0f1a629bac6126df079d88ededc8581d00cc43f2e9a6afecc293b51e3f73b090",
    "app/icons.js": "efb9f86382133a9dfd498f4d9964c46af7c95085aca11f2edc4eaac78d5d98e4",
    "app/info.js": "15e06186daadead308f180d23feb479eccf2506d70bfadbda477ccc6f7d3c11f",
    "app/layout.js": "447b775510c160d906ed6c54831954734c1f8bd70e90079a1fc280d6a9bad577",
    "app/login.js": "4316945989de1e1526389182db7e54dfdbeed4eab85d5d00821290ffc519dabf",
    "app/logo.js": "163352a0c9a8064f151396f17fa948fecb0334a41a7a4e068de24907845b52dd",
    "app/modelcharts.js": "b5a2afc2b92bcb12d15fb0794cd21d0cd928472d7360616660df3c4805efff46",
    "app/netgraph.js": "30c308afc4fc650550ce0751d212a54145c293629552028ecf58c2061951251a",
    "app/notices.js": "a6d15665d53bff109c8d5e5be6e1116f4d1a014b1ba10e141b80cf9de7e62261",
    "app/parameters.js": "6c5cf807535cc2ef87d4b1d54f2ab2abb34f595f74c64cc272b26bb88113b1f4",
    "app/password.js": "24e6e0c3927e67542aacef55051cb289c5310c93cd9ab4844a581a5256a7d87c",
    "app/plane.js": "1f46083206d6a6a2c6e8fea242e3bfeef0a9465d7846f6b7844523af6f47b382",
    "app/recover.js": "4ec71b672d6d647dc311fede65c51ff407b883f85d3bfdc8cec5e229f92fa5be",
    "app/registry.js": "4c989a49eb45780fb5eda2c7f3d0d4baf5cf3472679678c727ef43fc66b3e79b",
    "app/roster.js": "3cb720924adb78f3ea8e4cdb94fd0b18c864df39e92218584c1a93cb6127d299",
    "app/router.js": "9d6635854bd66b18c51318ce219fdc54c8260444b95a1da6228afd03afdf313e",
    "app/session.js": "8779cdd7c8bea0dcb8427dc74d3ea6ce5e03c7dc6ea01b566eb4d85f5834c97e",
    "app/shell.js": "cbeca02e8ca9b9d302525c9eb25155299016b7b628b5aa33d17653b409826cc1",
    "app/sidebar.js": "a3add9090ae1968923b455da087efb41278a48c217740577eca77bca00b224a4",
    "app/stack.js": "7fc57239d1f7cf2de84f39a726914bcc305cfd2b90de97df08870a6570ce5185",
    "app/store.js": "4b83a23f2e22bb63717a9c6d5612fed3efeef4508501c80f3dfbdc7e358d696e",
    "app/theme.js": "0971f3067a744fd448517fcc2ce230fa468e6ca1e2bf813839b7378bad699419",
    "app/twofactor.js": "a8a55e2f44646be76a8acc511442d3f69c36005374be5888175828159cfdec6b",
    "app/views/access.js": "66ffc83757c7f630b603723c935cbea6648a0375a1c2930156f1a9955550271d",
    "app/views/account.js": "050c3e069d0a97933cce5677f23f6d5c1a423bdde395c0086fc8642c0c829338",
    "app/views/agents.js": "113f4224506f63ae80ad94c72c210337de0707a00a4009c87d3573965b7b9ff8",
    "app/views/audit.js": "f2273859c0c59cb22b68f949b4204f66e2a7c81bf51362fd570bb93dcb88d87d",
    "app/views/cases.js": "6356f789b0e5d223771b2b3a177e868d57937c3469485f118c4889e37faaca45",
    "app/views/classes.js": "3e99c1fd9d0f7a98a526d913107cdb2de24954656de525f213c4aea284358970",
    "app/views/customers.js": "24b37c383da39c330b32b8f4add637cf05f63dc037194175d7b3401b4e98d9f4",
    "app/views/entities.js": "8a246df70c67f09f352895b791c38880caad094daf0e85287d711c7fb048b2c9",
    "app/views/graph.js": "8792bb402c81f9125c275f9741b5f9e1c79791a39d1a32bf70092388f55c595a",
    "app/views/learning.js": "4da8215b316800f46ceb531f43d09f28cf9503b9a90b90462a735225304dbd3c",
    "app/views/maintenance.js": "6f7425b764581a59176d8fb1846a7eb1d1e728d001e551e97c6277b6a5985d6f",
    "app/views/nockpis.js": "4cc9d938d79b413cd776d75278e9dacccb6a1c72ceca1aac807d863235337dfe",
    "app/views/nocoverview.js": "cfee5248584478492570973d5d4e38ba50cf5bf1dba2801a6e9ea2695399fba0",
    "app/views/org.js": "2fa3ebd050ad0550894ce8691905065546d625343d9c69e36a5a2c9d4391bb1e",
    "app/views/overview.js": "3509b1501943712b7d81b9e3f6c19bdf493b1f218272f5f07c5322f668cc178e",
    "app/views/parts/agentadmin.js": (
        "950c9c5f2e062d56cf977c1391f15bcffb744e680c296c2c465546f0fbb32760"
    ),
    "app/views/parts/agentdash.js": (
        "7abecea3d252b98c88f4d5931833c29996090f83f33b45be523a18885d1999d9"
    ),
    "app/views/parts/agentdetail.js": (
        "7640b3184e1b72d4a5b00fa3059a7fd1648fbe9a78c2e699ce25e816ebaaadb0"
    ),
    "app/views/parts/agentform.js": (
        "9c5499da1c02359f330fde4eaa794e24ecd9ad3a2920c557bfc3e8dad0e1c7a8"
    ),
    "app/views/parts/agentkey.js": (
        "8d55dfb306c6c354892b35aa8e4f484f82ce0a4cd9b31f04a4b6c33979cefd7e"
    ),
    "app/views/parts/agentrun.js": (
        "619bcada9ce47cc9ce81aba1b9462a41d58e25c112b0aa79319e4e8ed890fc25"
    ),
    "app/views/parts/agentruns.js": (
        "35ad12a6983542b026e87560fee493b83479175c923e5a5afed0694e42039e71"
    ),
    "app/views/parts/aiproviders.js": (
        "8877be39f9025dbedb54e08da3336017feb5d2fdb5e6d62703dafc9be499a9f2"
    ),
    "app/views/parts/apiref.js": "a9e18e1d0b0afe816f38244a9ddaefadb4ca31488376bcd9d2e083f04cd075c5",
    "app/views/parts/autonomy.js": (
        "40161ea7912c27140d8d634821ee8033928a1cc4bdc4ad4b2e445c2e96cebffa"
    ),
    "app/views/parts/bulkclear.js": (
        "efdd15fe669b8a65b2a8c02745f07c0f1903fa619a60179f55b369025dc01298"
    ),
    "app/views/parts/capgrid.js": (
        "d169361f4ffa0d98c5e9a9cfacea71d1ef6c17089c19de048b57e74003f4baf3"
    ),
    "app/views/parts/card.js": "b61bd4e3c81c2c5e84d4a27b15d94b9f9c82c74e13e344628b034134a1a8a211",
    "app/views/parts/caseacts.js": (
        "bb7a6f7ed2c22c252830be556f4a0f3e229cdf3bce8b6285d0fabeb373bf986a"
    ),
    "app/views/parts/caseassign.js": (
        "add5c88ae5ad8b6ccdd2c8c443d4d905917605761d3c5363578b8770c69f76c5"
    ),
    "app/views/parts/caseclock.js": (
        "9bce26e4e9102cbcb80419deac7c44142b17a01e4725d2896e25c8b415b2fd6f"
    ),
    "app/views/parts/casedetail.js": (
        "e458f64402fa9306c58260045c5f168c6a47b3629ae99a014942a08c45ef08a7"
    ),
    "app/views/parts/casefeed.js": (
        "cac9f9d3503ee2f8cba10b31992f8fe51867a469ef7207d4e09a9bc73bd651bb"
    ),
    "app/views/parts/caseform.js": (
        "a5bbaead197388d3ca7c8bbd28cf1d58c7613dd3ced7bea202c40afdb48ab9a4"
    ),
    "app/views/parts/casemilestones.js": (
        "40dcd9b26243c8bfb3069b91fd89b55a2357f1168ce8002b22246c6dec3e3767"
    ),
    "app/views/parts/circuitedit.js": (
        "2acd46f434e7aeedd7f6ce97001fa33c740cdea4d08ffb3949e586c38f05e907"
    ),
    "app/views/parts/circuits.js": (
        "201edd0f7863c8f13782dc3db14f0227ed2d922e02b121e98d9838c9788a00f4"
    ),
    "app/views/parts/correlation.js": (
        "a29a11b819187876b40d8e37d72c21f51c057d0e397a28f32a2d98dac0b171de"
    ),
    "app/views/parts/credlist.js": (
        "159443f6b906a2c505ae971d44b32843649f673b3e72159c277ca82c8d025c23"
    ),
    "app/views/parts/custaccess.js": (
        "94666ccdadad155b88389030d1ef08826b434a411b6421a7009d9741688db186"
    ),
    "app/views/parts/custbook.js": (
        "a7659cecf35aa67e48fe5ca451ecea5234b502e88c2e2e9ad6e1df434dec3d18"
    ),
    "app/views/parts/custcontacts.js": (
        "6e4d3c378e5a308ad083ad18593ef6fea8dde5f15b6cc1a4a05e620a8c129635"
    ),
    "app/views/parts/custdocs.js": (
        "df0e7f19f834c13fc31289316898f814d87963a3f5b52b644c336b338ccccbfe"
    ),
    "app/views/parts/custmail.js": (
        "91303b22a06b5bda94937c748f38ee52daaf8c9c4a54f6db5dbcb8e48f969c64"
    ),
    "app/views/parts/decide.js": "8f251c644c3517df0561a9b9b9d4fb1800f8d774cafbf2bde3c16142a120207e",
    "app/views/parts/decider.js": (
        "c233e499e5ea3e75bbb0f07b24fb25377233482cabd4f70dcf1d00fdcaeab6e6"
    ),
    "app/views/parts/declare.js": (
        "852b5979244ae32cb1e7ba46a3f19c0412886f8910138f3a22ae1f0dcc82f3b7"
    ),
    "app/views/parts/element.js": (
        "7ad70f3bfaaef3f13f7dea4af2c6651beffeca8d666952574c6338456b2410ef"
    ),
    "app/views/parts/email.js": "a11418170fc5d360adcbafe326f2c975191f4124e008cb9077e7ba74b80053a6",
    "app/views/parts/evidence.js": (
        "98cc15c6870dcb3a2b004353adaac5ce411e7937ab2cd6bdeb7f94b76c1098e6"
    ),
    "app/views/parts/facts.js": "24e6ee60d2401cc91b60c83ce01e32754ba34a3e48fd4c8821b5af1114b93844",
    "app/views/parts/finder.js": "40a84469df45e93a451e7fd39d462ed51fbeff786a56571cfccb270ae1002b92",
    "app/views/parts/firstrun.js": (
        "bc31b99fb613ed0d60442c1ae818186b8e56b99a462324e4126d625c48ae4b31"
    ),
    "app/views/parts/importbox.js": (
        "5e549b9dec2f8518d9c399d40e978cc461017cfd9845d0f4a1770acb3cd98319"
    ),
    "app/views/parts/judge.js": "008ef0b0a25ca68b55952803bc483bff5e06ceb0f633dc0c5a263d6cdfd48fc6",
    "app/views/parts/keeping.js": (
        "e71bb50b1fd98f234128313d3b5c7be26aff99409dc597271ea007ca9c206fa8"
    ),
    "app/views/parts/league.js": "49fd6c930f3d176ec0f934b8f37ef541f776937d664724c842cfc79b29e16e77",
    "app/views/parts/leaguecharts.js": (
        "5a06aa69c7d2f8ab6ff256d6f10b6a6a910af7c4a57ce71c842e2735b75219eb"
    ),
    "app/views/parts/leaguecompare.js": (
        "0160a1ebb7c1bcdc2caa5781e7bb3de57a6d050bb1b189ac9c5c2b6308c11dc5"
    ),
    "app/views/parts/learnuml.js": (
        "ba8c89870a7767a61f34e2790f621067bf449e4f532d1ddf02ba6b63e75298f5"
    ),
    "app/views/parts/lifecycle.js": (
        "c2984a5305d4345bdfeba6c33bfd70795e04495b495fd1cbe90d1ba018265b8b"
    ),
    "app/views/parts/marks.js": "28253d0b86ef3dd372ea7f26cf2608648ca5e66c16c682058d3287225780a148",
    "app/views/parts/members.js": (
        "89c1102e8f612c517c5ec6a1d0d020ff57ded45be256b0cc11cc887ce4670af0"
    ),
    "app/views/parts/model.js": "c30966786ff8e78e8c326b3b0206fe171298e86ef784b0841d68999ef068e81d",
    "app/views/parts/models.js": "398a5ed7f8d13ef7730cfc088b7e531648d358865157ee37b6c2cb894e6b9df8",
    "app/views/parts/mwdetail.js": (
        "2c8bfe926642762387da311c2d211ed5eba9a38665ca5eab9da8c3e2b04f2867"
    ),
    "app/views/parts/mwdraft.js": (
        "fb1f31ca7b40cf2520bee4d248f0bf2bf180eb1a23fb081a2b1a39e7f37d1295"
    ),
    "app/views/parts/mwform.js": "30b74188015a4856a6c48a67bd147838d2893d32e7e4babdc60a74c8b2d4626e",
    "app/views/parts/mwmarker.js": (
        "c3e7ed23febacd82a4fd7142064b3d1d26fc7d8cade7e29bd7930454d2e3bd9a"
    ),
    "app/views/parts/mwreview.js": (
        "b771acc2408dc67e457ffa851bbd1fdf0300093d6d1e3e20fc89319997f949c8"
    ),
    "app/views/parts/mwrules.js": (
        "c77b970377ec6eed2c90607c41b03399783ee9348f3fa357d01425f95a554327"
    ),
    "app/views/parts/mwsummary.js": (
        "f48c334a30e2d7ed910142fca88b615f4596cf10d8baff4613a10207108fe361"
    ),
    "app/views/parts/mwtime.js": "f172afcff9003258a76392fb88c14d331c1397e8d84d3da66e3cbc6ef4ac1362",
    "app/views/parts/mwwhen.js": "310714e54a13af3a9555ce1ef62e36348af3d50be3ab4e032a2f7ef7a6d00a96",
    "app/views/parts/nedetail.js": (
        "b6595c8cb963857e934cac01833580f3162d5965efdbceabb2b1961dd959c702"
    ),
    "app/views/parts/nocgrants.js": (
        "1c425c46a5bb9d23882457c855727744f5a191cc848e06715924449ee69f69ba"
    ),
    "app/views/parts/nocui.js": "890acce879a6ce30350e9b72d91351f06be584ef69059916b8afc46db65cd397",
    "app/views/parts/oidtree.js": (
        "3458a69deca0eab3513684c67b37e2409f16b4db60d6713b64622f1cd8bb41e9"
    ),
    "app/views/parts/ollamamodels.js": (
        "46734e10edc1dca33eab3ee7264d0a8530839c0bb3c23a9d68528a4e48b30e10"
    ),
    "app/views/parts/orgmove.js": (
        "68fd9c8f811b622c32b33224ec0487e159fc792cb5b9294f6a0f27247ee0ac25"
    ),
    "app/views/parts/people.js": "47379d1807af214c01773cfa68dbb1a90c3aec272a610c3bdb483d9994771d5c",
    "app/views/parts/pipeline.js": (
        "7c5e43f7d1368717caa9186555cf9fda0be4266910e2cb75a3d6a7c5c1496a2c"
    ),
    "app/views/parts/pulse.js": "91658b65ace8f128a65367a16017015fc457cc84eea7f671d7e2d8372dc04d3f",
    "app/views/parts/restructure.js": (
        "d2f47178f96665ec0437ae36e969b097ac1254e5adc7f4740f09e639df29b54e"
    ),
    "app/views/parts/retention.js": (
        "62ca72d9e442b7159b1823e5dac84b3c97c0cbccda364a6ed9e2501682918168"
    ),
    "app/views/parts/roles.js": "e48e102554a1a3ce688c555c2bcd3f5f7826d466c892d99bf5c6889b058a9986",
    "app/views/parts/scopeexamples.js": (
        "82cd57e5a52dca80c9ab79a5f2e0b0d26c508de2ab9d48a3147eb8b188cb83cd"
    ),
    "app/views/parts/searchpanel.js": (
        "a5fbe3713c765c6afa82d926bda1bb5576da68161e83544bfc4f2540463e0f8b"
    ),
    "app/views/parts/secretdetail.js": (
        "8c9758d5e5907011e7b17fc9faeb9f6b03f2df5edd18ce6e858d75f733dacc18"
    ),
    "app/views/parts/secretform.js": (
        "8196b0aa3fde3acfb5b5a6d43c73591e460a9d1563932f053e05abb5cab0c28b"
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
        "b862e0f822ceba4397a8dc2eaccd1ab826b2d0261fd3c70c9cc218d1bd37d4f3"
    ),
    "app/views/parts/snmp.js": "3e8aea5120cbffd66904d098c3268b82696c4312c22fee5e05a51cb1e10497e3",
    "app/views/parts/snmpuser.js": (
        "25cc52733b86b7251cee014b2b0c776a9543d351c31431aff73b3fecb199e194"
    ),
    "app/views/parts/stepup.js": "20995854172330df26e667c0b99f8189cd3566e2bb759ddaff2cbe709f268d39",
    "app/views/parts/tlfilters.js": (
        "13260494504327f384bebebaced9f791e71b60243325dafa00b5bf23b04a0092"
    ),
    "app/views/parts/tokenspanel.js": (
        "73457aab8952e50f8e66030ef215bc2192bc3b6d619e25f1aa6d1dbcfda5eb64"
    ),
    "app/views/parts/topassets.js": (
        "2be5ee0ad7c8e7df1f26a701627c1fe550f75f941cd6227b4e1f72456b6f4d2f"
    ),
    "app/views/parts/vaultui.js": (
        "19dde199f518653bb3fc13df7037b19d76f40f608c5976d9b6f23b6eb6cf418d"
    ),
    "app/views/parts/verdict.js": (
        "bfedceae5645684afeaf50c636bc56f2626f6822a7b03e6f45af9f6f9bc2a9d3"
    ),
    "app/views/parts/visibility.js": (
        "3df175efe0e852f0e0d32c907fd6b591302efad65af0cb0ed3570ac8cfbf548c"
    ),
    "app/views/parts/why.js": "91807cd9bdb236e2980b987c36bbd9ff33f2450461890cf646dfdca2c3c69c88",
    "app/views/playbooks.js": "780464cbd576a6d81d2f000df974f8e59716aec77ca48f6be0f66dfc90764a5b",
    "app/views/promotion.js": "10308f82333556db515c514ec4cab45d9122d5b187a582e455efa80bbbf83ade",
    "app/views/quarantine.js": "04ed768d8180e7d3182086e8dd12c69c8a497196bfb0086ea5023e34a5470b5b",
    "app/views/scorer.js": "0ed95cd927706a5ab1c9452e6d935363fab28b95f565ac0d232d41195f9de4c2",
    "app/views/secrets.js": "9680fa88fc16fb89f7509139d5ce30c97030de3fb8bd5f03106a4f82f33a6481",
    "app/views/settings.js": "b80d3fb93f625b1251880c9a5a26affbf6b076406bb8edc3b5520f236e95118c",
    "app/views/situations.js": "afe374be8e8e920369da6fe767294cc29a7cec7fea9899a6deace262f1509241",
    "app/views/timeline.js": "f023cedba738a32c4961e4c0cb051f82bd4244be5548556c4c2633c6cfe9fe54",
    "app/widgets.js": "32d824bc65d63452ae2f5f077c5e4c75c70d2cfe17c6d6b39ca637673a90bdc1",
    "favicon.svg": "67be772b54d99f4978143983b4d5313c8aab40e1498a1fa748680e00f7735bf2",
    "index.html": "d057123e5cfc1ab497db465df016c598e38b049fbde26ecc9ee868eb4899fad7",
    "login-bg.svg": "5f677f3371a2828c8d21668cd83381c105ccdc1780a66dc09fc6f6bc19d211d6",
    "style.css": "541992e9a84620fca2e863039308c9af92637760bd29f881bf00dfc3e6cf7fb3",
    "vendor/CHECKSUMS.txt": "d0a7c4ec48719c3cb3c86f6da7e010b7be5e822cf62ed9f2aafdf39726405ad7",
    "vendor/htm-3.1.1.module.js": (
        "ab33dd3f38059b9be4d5f5350128eefb2356639c4e0bbe9d9e8b3ba75847e9e4"
    ),
    "vendor/htm.LICENSE": "740725f7252e750af735d0028cc534970772f513331e9f68150fede8fb3ce00f",
    "vendor/inter-5.3.0.latin.woff2": (
        "3100e775e8616cd2611beecfa23a4263d7037586789b43f035236a2e6fbd4c62"
    ),
    "vendor/inter.LICENSE": "3b0a5fca3d17942cde889069889dedbbbd075e9b599968c82a95f4d944e9b345",
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
    "app/avatar.js": 7_309,
    "app/brands.js": 10_533,
    "app/chartdata.js": 8_533,
    "app/charts.js": 15_836,
    "app/compare.js": 3_524,
    "app/destructive.js": 4_811,
    "app/dom.js": 2_153,
    "app/format.js": 15_764,
    "app/health.js": 7_617,
    "app/icons.js": 13_583,
    "app/info.js": 2_407,
    "app/layout.js": 9_799,
    "app/login.js": 8_780,
    "app/logo.js": 1_573,
    "app/modelcharts.js": 17_564,
    "app/netgraph.js": 11_593,
    "app/notices.js": 14_771,
    "app/parameters.js": 10_388,
    "app/password.js": 6_064,
    "app/plane.js": 3_305,
    "app/recover.js": 4_934,
    "app/registry.js": 13_857,
    "app/roster.js": 4_052,
    "app/router.js": 5_261,
    "app/session.js": 4_224,
    "app/shell.js": 17_412,
    "app/sidebar.js": 13_035,
    "app/stack.js": 6_564,
    "app/store.js": 5_978,
    "app/theme.js": 8_134,
    "app/twofactor.js": 13_070,
    "app/views/access.js": 4_940,
    "app/views/account.js": 14_533,
    "app/views/agents.js": 10_374,
    "app/views/audit.js": 12_531,
    "app/views/cases.js": 11_495,
    "app/views/classes.js": 8_085,
    "app/views/customers.js": 12_707,
    "app/views/entities.js": 8_634,
    "app/views/graph.js": 9_351,
    "app/views/learning.js": 8_531,
    "app/views/maintenance.js": 10_161,
    "app/views/nockpis.js": 4_770,
    "app/views/nocoverview.js": 14_864,
    "app/views/org.js": 12_125,
    "app/views/overview.js": 10_762,
    "app/views/parts/agentadmin.js": 8_051,
    "app/views/parts/agentdash.js": 10_083,
    "app/views/parts/agentdetail.js": 9_427,
    "app/views/parts/agentform.js": 12_631,
    "app/views/parts/agentkey.js": 4_339,
    "app/views/parts/agentrun.js": 11_049,
    "app/views/parts/agentruns.js": 4_073,
    "app/views/parts/aiproviders.js": 16_200,
    "app/views/parts/apiref.js": 8_844,
    "app/views/parts/autonomy.js": 10_601,
    "app/views/parts/bulkclear.js": 3_374,
    "app/views/parts/capgrid.js": 6_766,
    "app/views/parts/card.js": 9_710,
    "app/views/parts/caseacts.js": 6_406,
    "app/views/parts/caseassign.js": 5_930,
    "app/views/parts/caseclock.js": 2_732,
    "app/views/parts/casedetail.js": 12_180,
    "app/views/parts/casefeed.js": 6_893,
    "app/views/parts/caseform.js": 10_114,
    "app/views/parts/casemilestones.js": 5_153,
    "app/views/parts/circuitedit.js": 8_338,
    "app/views/parts/circuits.js": 13_650,
    "app/views/parts/correlation.js": 7_762,
    "app/views/parts/credlist.js": 2_725,
    "app/views/parts/custaccess.js": 3_833,
    "app/views/parts/custbook.js": 6_421,
    "app/views/parts/custcontacts.js": 9_060,
    "app/views/parts/custdocs.js": 8_190,
    "app/views/parts/custmail.js": 8_900,
    "app/views/parts/decide.js": 7_904,
    "app/views/parts/decider.js": 9_886,
    "app/views/parts/declare.js": 13_092,
    "app/views/parts/element.js": 6_168,
    "app/views/parts/email.js": 11_218,
    "app/views/parts/evidence.js": 12_652,
    "app/views/parts/facts.js": 6_382,
    "app/views/parts/finder.js": 6_650,
    "app/views/parts/firstrun.js": 7_093,
    "app/views/parts/importbox.js": 5_982,
    "app/views/parts/judge.js": 16_253,
    "app/views/parts/keeping.js": 8_308,
    "app/views/parts/league.js": 12_065,
    "app/views/parts/leaguecharts.js": 10_696,
    "app/views/parts/leaguecompare.js": 6_189,
    "app/views/parts/learnuml.js": 5_615,
    "app/views/parts/lifecycle.js": 9_471,
    "app/views/parts/marks.js": 5_998,
    "app/views/parts/members.js": 11_111,
    "app/views/parts/model.js": 10_290,
    "app/views/parts/models.js": 4_823,
    "app/views/parts/mwdetail.js": 8_486,
    "app/views/parts/mwdraft.js": 9_888,
    "app/views/parts/mwform.js": 13_426,
    "app/views/parts/mwmarker.js": 3_574,
    "app/views/parts/mwreview.js": 5_265,
    "app/views/parts/mwrules.js": 7_636,
    "app/views/parts/mwsummary.js": 6_654,
    "app/views/parts/mwtime.js": 11_498,
    "app/views/parts/mwwhen.js": 5_830,
    "app/views/parts/nedetail.js": 11_009,
    "app/views/parts/nocgrants.js": 5_501,
    "app/views/parts/nocui.js": 3_646,
    "app/views/parts/oidtree.js": 6_358,
    "app/views/parts/ollamamodels.js": 5_547,
    "app/views/parts/orgmove.js": 4_154,
    "app/views/parts/people.js": 16_204,
    "app/views/parts/pipeline.js": 9_526,
    "app/views/parts/pulse.js": 5_701,
    "app/views/parts/restructure.js": 13_682,
    "app/views/parts/retention.js": 5_092,
    "app/views/parts/roles.js": 4_973,
    "app/views/parts/scopeexamples.js": 3_471,
    "app/views/parts/searchpanel.js": 5_893,
    "app/views/parts/secretdetail.js": 14_281,
    "app/views/parts/secretform.js": 12_484,
    "app/views/parts/sequence.js": 5_776,
    "app/views/parts/severity.js": 3_912,
    "app/views/parts/sitejudge.js": 6_236,
    "app/views/parts/sitsummary.js": 3_645,
    "app/views/parts/snmp.js": 11_360,
    "app/views/parts/snmpuser.js": 7_658,
    "app/views/parts/stepup.js": 4_855,
    "app/views/parts/tlfilters.js": 5_030,
    "app/views/parts/tokenspanel.js": 7_675,
    "app/views/parts/topassets.js": 4_085,
    "app/views/parts/vaultui.js": 4_739,
    "app/views/parts/verdict.js": 8_420,
    "app/views/parts/visibility.js": 4_447,
    "app/views/parts/why.js": 15_573,
    "app/views/playbooks.js": 7_320,
    "app/views/promotion.js": 13_684,
    "app/views/quarantine.js": 2_247,
    "app/views/scorer.js": 13_462,
    "app/views/secrets.js": 5_076,
    "app/views/settings.js": 15_791,
    "app/views/situations.js": 15_602,
    "app/views/timeline.js": 7_227,
    "app/widgets.js": 14_347,
    "favicon.svg": 1_136,
    "index.html": 1_549,
    "login-bg.svg": 18_884,
    "style.css": 297_474,
    "vendor/CHECKSUMS.txt": 2_426,
    "vendor/htm-3.1.1.module.js": 1_207,
    "vendor/htm.LICENSE": 11_341,
    "vendor/inter-5.3.0.latin.woff2": 48_256,
    "vendor/inter.LICENSE": 4_477,
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
