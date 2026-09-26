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

The DOM harness (`tests/domharness/`) runs under Node. It is stdlib-only, needs no `npm install`,
vendors nothing, and produces nothing that ships: `pyproject.toml`'s `package-data` names
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

REPO_ROOT = Path(__file__).resolve().parent.parent
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
    harness = [p for p in paths if p.startswith("tests/domharness/")]
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
    "app.js": "426f5fc2e948536a3b337a874c6dcc090f8e2714af74740c864db965a9a10bfd",
    "app/api.js": "db042593c344a37f646c3ab5df2c1a8b2abbae18d06bf0b2ffce789406d9ed1a",
    "app/chartdata.js": "20dd4efbb54f3d54632017069ef0d0c13800f70921c625b12469f1fa043ced82",
    "app/charts.js": "f0043e04b0c4ba8b6a7e100f73bda78c67692ef00c8080497ffcb12cecee6418",
    "app/compare.js": "c13ceb94e46894d6c1cae3013ba9e600e8e8ff07db75b5528df49fdd96663758",
    "app/destructive.js": "51994f0640e3e170061ec0f9bea068f7b3fff7348ca153ffb00d64cfb11838e0",
    "app/dom.js": "b0e279c902ae6f76a902dbc24bb8595d6936aff13f46354870120c4fe42b119b",
    "app/format.js": "b8d224157a757de657fa6841cade28a7b031a231d5961287f89627b33e401f39",
    "app/health.js": "8f397a8de15131917ff8567f849f468dce8e4b87bc54afd6e9bd502baf8cf921",
    "app/icons.js": "636ab2b8ee498925f675256acf0fc33de3d621a5ccd017e28c2788cab6a92ff6",
    "app/info.js": "15e06186daadead308f180d23feb479eccf2506d70bfadbda477ccc6f7d3c11f",
    "app/layout.js": "447b775510c160d906ed6c54831954734c1f8bd70e90079a1fc280d6a9bad577",
    "app/login.js": "ee936acd5ec82c2299416c9c87e594ab954b42dad137db4e8662eb6eded104fc",
    "app/netgraph.js": "30c308afc4fc650550ce0751d212a54145c293629552028ecf58c2061951251a",
    "app/notices.js": "65f1adc13ddbb4a1f5d4fd85e8c1d476079e3872ab64e74f4dd41df76dcab7c1",
    "app/parameters.js": "e661fcc6136f849f565a16c1326a786b0a149c9553d64115e9a591da9fc2545d",
    "app/password.js": "6c85d8111fcaa2da415910744064c84e41adf5db57fc953cad9f1f14543ec11d",
    "app/registry.js": "4686516e62b862ae7031ed40f618806c9bfdf38f422012317d89cecc042c1a45",
    "app/router.js": "5abc5927a6e355c1271f4e9cf6295e4211441bdac5e3b38d287c32536e83bb4e",
    "app/session.js": "4d78e2ace8974ba3ce63aae9be06f3b1730f19784c25b7cb7ddc4c194401449f",
    "app/shell.js": "ee698932858da28345c7592382532815ef1b2bea0d841556ff807848d091b59e",
    "app/sidebar.js": "8625e151e0ec4d15f4d64c1d16eace797e4341e5c8361694f9bad371d3536bfe",
    "app/stack.js": "e2f41526048800942fec89a25ffb6dc1fda5d650b3b96dc3ba692438241aaaba",
    "app/store.js": "266b38b349e0f33f3bf5643055aa75057abfcb629d609f6179a3abaa64244411",
    "app/theme.js": "69856ea8f1f66ac7a3f74c98151ad8854a41daeef315a908a630243c306ee031",
    "app/views/account.js": "9459ec67e2288914ebd66b22e5f50c80abb2da53b7f0972474bed45f84ed453b",
    "app/views/audit.js": "5f6de01c2eb9ed02f920f9c0165be345ea127aafe186e6d67ad05f7e00b5ce71",
    "app/views/classes.js": "3e99c1fd9d0f7a98a526d913107cdb2de24954656de525f213c4aea284358970",
    "app/views/corpus.js": "5493886eb9c3023f3a94bd555d20dc613aba3570f34a4519148d9191bfb6d131",
    "app/views/entities.js": "36a2ae7b50e6c01270d5e37040658638f0ea206489aada3313c7950903d7da6d",
    "app/views/governance.js": "66ec220c5e12b347cecab20a4319825cfdf8ec0a4e888e1070bcccbb7536f5ce",
    "app/views/graph.js": "8792bb402c81f9125c275f9741b5f9e1c79791a39d1a32bf70092388f55c595a",
    "app/views/labelling.js": "e163b0f1ca9d7af77404d50c45a2797c0ea8d038e4ce176d56eb7396b6d01aa1",
    "app/views/maintenance.js": "f9f6b0c68bbe9666d6e38e7a40af4902a39ed6459431ea18eea73a1314af1c3c",
    "app/views/overview.js": "20a98eb9de4770233757d90a0512303722f415979a34cf4bcc29c4fc7bd32ebc",
    "app/views/parts/bulkclear.js": (
        "efdd15fe669b8a65b2a8c02745f07c0f1903fa619a60179f55b369025dc01298"
    ),
    "app/views/parts/card.js": "57e73581a71081400a32d991eb191fee2b14a225aa22675b00a760582a42c2d2",
    "app/views/parts/correlation.js": (
        "a29a11b819187876b40d8e37d72c21f51c057d0e397a28f32a2d98dac0b171de"
    ),
    "app/views/parts/decide.js": "6474cd33ca0ffc92a0317d965d0c9dc78e7b7277d9160f34abfb6c1cd9cf4f11",
    "app/views/parts/declare.js": (
        "9d177b9c1a5e1c3207583b560eb9aeeeaae33b8fb50b5ab04fee3809f238912f"
    ),
    "app/views/parts/element.js": (
        "501899a6220ad2a4b464b2f83948e27558834e7ba108fda634620ace26b1670b"
    ),
    "app/views/parts/evidence.js": (
        "98cc15c6870dcb3a2b004353adaac5ce411e7937ab2cd6bdeb7f94b76c1098e6"
    ),
    "app/views/parts/facts.js": "2befcc9ed54b18d57de942f3f9583e00a0a39170bb2b45d68b29933881858f12",
    "app/views/parts/finder.js": "b0652368970a0631d24987c4d1a4dd92b40affe833d1d7959db2a3933fc03274",
    "app/views/parts/importbox.js": (
        "5e549b9dec2f8518d9c399d40e978cc461017cfd9845d0f4a1770acb3cd98319"
    ),
    "app/views/parts/judge.js": "782fcc72404267718802e91b1b194694656cf577a9b9fa19284c5b82028ffc3d",
    "app/views/parts/keeping.js": (
        "10bf18e8e753121f3c51a130c8470aa3858e0d1b4cb7ce954b041228e97dfa56"
    ),
    "app/views/parts/lifecycle.js": (
        "f6200404a808fc1cc31105991fa1fe65c0af133acb1dd3686c2b0c7a85ed551c"
    ),
    "app/views/parts/marks.js": "28253d0b86ef3dd372ea7f26cf2608648ca5e66c16c682058d3287225780a148",
    "app/views/parts/members.js": (
        "1435449d80e78e62d3390d12818a235b13055e05b51e1063d813dc8721591f42"
    ),
    "app/views/parts/model.js": "c30966786ff8e78e8c326b3b0206fe171298e86ef784b0841d68999ef068e81d",
    "app/views/parts/models.js": "6bec6d279b4d61395147d46bc4def7bda8ee219cae47ea953f669ab3b2d13dda",
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
        "459a85fd5647c2be815bf7dd208253a24603ba1a1289566d880701d1113f7f16"
    ),
    "app/views/parts/mwrules.js": (
        "c77b970377ec6eed2c90607c41b03399783ee9348f3fa357d01425f95a554327"
    ),
    "app/views/parts/mwsummary.js": (
        "a74238e7c30cd52e08b8ad7ad65da198115113792981727c0b77ce192ee802a9"
    ),
    "app/views/parts/mwtime.js": "a57748bfb2e409e3e2db5835fd7178f68161a8a3861b82b331a24ffd39c04252",
    "app/views/parts/nedetail.js": (
        "9c287e1ffb18b079b417e42c9eaf61f8ace86c2065ecbdf868c4145ab5cdc0ff"
    ),
    "app/views/parts/oidtree.js": (
        "3458a69deca0eab3513684c67b37e2409f16b4db60d6713b64622f1cd8bb41e9"
    ),
    "app/views/parts/pulse.js": "ac7e9bc59841185e5304d9d3fdc3fae8759ddf42376098efeb2b76e51b043e01",
    "app/views/parts/restructure.js": (
        "89cadfe5fb4cc6f25d51d72f97589632287cecd672b103f054650934eb17382c"
    ),
    "app/views/parts/retention.js": (
        "d740766714ad72e1bae480f2e954f355aab286c84f6badf58dfa33d62d12bc85"
    ),
    "app/views/parts/sequence.js": (
        "49bac7522443508bbf44d652dc1be4a4c26ebe656f5c3176df0a41292af38ad5"
    ),
    "app/views/parts/severity.js": (
        "799c125f48183a7de1a6597cfac400f4d0d0974de7daee4b1ddea7e485f53b08"
    ),
    "app/views/parts/sitsummary.js": (
        "57c702761ee27b6e9e5a74df95d33ed4aafefacb9d8e524a87d054a9263a0414"
    ),
    "app/views/parts/tlfilters.js": (
        "13260494504327f384bebebaced9f791e71b60243325dafa00b5bf23b04a0092"
    ),
    "app/views/parts/topassets.js": (
        "c3c20d5c60fae1497ff7f449630971ab76656f56d9c7a010a3d4a8d2e3c13710"
    ),
    "app/views/parts/verdict.js": (
        "bfedceae5645684afeaf50c636bc56f2626f6822a7b03e6f45af9f6f9bc2a9d3"
    ),
    "app/views/parts/why.js": "2a67688fa82815b28b640e6a8dfa9e6109cf4f1969b112b5a8f93311d5a7b948",
    "app/views/promotion.js": "0b6df8114a912042dd54b9736b8874145020a990aeede6a684705a90bbf542f4",
    "app/views/quarantine.js": "04ed768d8180e7d3182086e8dd12c69c8a497196bfb0086ea5023e34a5470b5b",
    "app/views/scorer.js": "bd3126b1928b72b840f80a7c5a7916fd4ac8c482aa92f8532cb40aeafcb5dce1",
    "app/views/settings.js": "87ecd031c5ab16f549223f5fdaf6711ae8ae97b12aa65f643b21f998a256584d",
    "app/views/situations.js": "afe374be8e8e920369da6fe767294cc29a7cec7fea9899a6deace262f1509241",
    "app/views/timeline.js": "482ff1f71115f4bd4791c019d9a537cbcbe2f41b7112115fb74baa0a6610ee0f",
    "app/views/tokens.js": "f1195d816ebc5e2a9a431d9d618c05299b4497eb60d8a26774be2edb99fbb676",
    "app/views/users.js": "c3fc89f35a09b0c4c748c609d3d45777842f877a59ef88dfdd70ef9a4ea31b19",
    "app/widgets.js": "660aa0a8451c8b8515a89d2e850eeabe58a370987305878ae7beb5cffa1a1078",
    "favicon.svg": "c11ec68d389057cc4d4145b3cdf77f3ebfec40150e9f409ff35a7cf419f524b7",
    "index.html": "d057123e5cfc1ab497db465df016c598e38b049fbde26ecc9ee868eb4899fad7",
    "style.css": "6772246d27ee247adc48c23c250a1a904954d833a2a1c7bfdf0d787fdf4f9665",
    "vendor/CHECKSUMS.txt": "989f0c2f0be99057149737c0d1bad41e59effb0c64357679f6c46257059ba2e3",
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
    "app.js": 5_540,
    "app/api.js": 5_405,
    "app/chartdata.js": 8_379,
    "app/charts.js": 15_836,
    "app/compare.js": 3_080,
    "app/destructive.js": 4_152,
    "app/dom.js": 2_142,
    "app/format.js": 14_291,
    "app/health.js": 7_606,
    "app/icons.js": 7_565,
    "app/info.js": 2_407,
    "app/layout.js": 9_799,
    "app/login.js": 7_137,
    "app/netgraph.js": 11_593,
    "app/notices.js": 14_370,
    "app/parameters.js": 10_264,
    "app/password.js": 5_442,
    "app/registry.js": 8_410,
    "app/router.js": 5_035,
    "app/session.js": 3_482,
    "app/shell.js": 14_096,
    "app/sidebar.js": 9_434,
    "app/stack.js": 6_357,
    "app/store.js": 5_485,
    "app/theme.js": 8_125,
    "app/views/account.js": 4_614,
    "app/views/audit.js": 6_490,
    "app/views/classes.js": 8_085,
    "app/views/corpus.js": 6_232,
    "app/views/entities.js": 8_496,
    "app/views/governance.js": 10_218,
    "app/views/graph.js": 9_351,
    "app/views/labelling.js": 6_105,
    "app/views/maintenance.js": 8_087,
    "app/views/overview.js": 10_906,
    "app/views/parts/bulkclear.js": 3_374,
    "app/views/parts/card.js": 6_961,
    "app/views/parts/correlation.js": 7_762,
    "app/views/parts/decide.js": 6_460,
    "app/views/parts/declare.js": 13_089,
    "app/views/parts/element.js": 6_152,
    "app/views/parts/evidence.js": 12_652,
    "app/views/parts/facts.js": 6_340,
    "app/views/parts/finder.js": 6_220,
    "app/views/parts/importbox.js": 5_982,
    "app/views/parts/judge.js": 15_306,
    "app/views/parts/keeping.js": 7_186,
    "app/views/parts/lifecycle.js": 7_033,
    "app/views/parts/marks.js": 5_998,
    "app/views/parts/members.js": 10_935,
    "app/views/parts/model.js": 10_290,
    "app/views/parts/models.js": 11_698,
    "app/views/parts/mwdetail.js": 7_956,
    "app/views/parts/mwdraft.js": 9_873,
    "app/views/parts/mwform.js": 14_757,
    "app/views/parts/mwmarker.js": 3_574,
    "app/views/parts/mwreview.js": 4_757,
    "app/views/parts/mwrules.js": 7_636,
    "app/views/parts/mwsummary.js": 6_324,
    "app/views/parts/mwtime.js": 9_551,
    "app/views/parts/nedetail.js": 7_889,
    "app/views/parts/oidtree.js": 6_358,
    "app/views/parts/pulse.js": 5_546,
    "app/views/parts/restructure.js": 12_859,
    "app/views/parts/retention.js": 5_003,
    "app/views/parts/sequence.js": 5_760,
    "app/views/parts/severity.js": 3_912,
    "app/views/parts/sitsummary.js": 3_580,
    "app/views/parts/tlfilters.js": 5_030,
    "app/views/parts/topassets.js": 3_767,
    "app/views/parts/verdict.js": 8_420,
    "app/views/parts/why.js": 13_210,
    "app/views/promotion.js": 11_652,
    "app/views/quarantine.js": 2_247,
    "app/views/scorer.js": 13_216,
    "app/views/settings.js": 11_331,
    "app/views/situations.js": 15_602,
    "app/views/timeline.js": 7_203,
    "app/views/tokens.js": 5_104,
    "app/views/users.js": 8_994,
    "app/widgets.js": 13_964,
    "favicon.svg": 608,
    "index.html": 1_549,
    "style.css": 143_107,
    "vendor/CHECKSUMS.txt": 1_996,
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
