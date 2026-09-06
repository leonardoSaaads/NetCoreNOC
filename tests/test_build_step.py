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
    assert "/vendor/d3.v7.min.js" in named, (
        "no console file names the vendored d3 asset, so either it stopped being loaded or it is "
        "being loaded by something this guard cannot see"
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
    "app/api.js": "186f79e412a22550a061bc7fd0354a97398e41a153d60c1d19f8e9f03965e977",
    "app/destructive.js": "51994f0640e3e170061ec0f9bea068f7b3fff7348ca153ffb00d64cfb11838e0",
    "app/dom.js": "b0e279c902ae6f76a902dbc24bb8595d6936aff13f46354870120c4fe42b119b",
    "app/format.js": "441d130bf7f6257124f8a2ca5c0573915ca3918a0ede8960f9f8cc40bb770db4",
    "app/icons.js": "bca6a442d593b20549197ade2231b887b821f72d0355c4b69c4160d2d44e1c98",
    "app/login.js": "ee936acd5ec82c2299416c9c87e594ab954b42dad137db4e8662eb6eded104fc",
    "app/notices.js": "bdd481caeee3cc2516280200f3aa0bb04171ca9db07edefa95d4b42a1218636b",
    "app/parameters.js": "e661fcc6136f849f565a16c1326a786b0a149c9553d64115e9a591da9fc2545d",
    "app/password.js": "6c85d8111fcaa2da415910744064c84e41adf5db57fc953cad9f1f14543ec11d",
    "app/registry.js": "38114bfebf5692f13f234013599665f7df3a40aaf7081dc53f824e91c0eb4720",
    "app/router.js": "5abc5927a6e355c1271f4e9cf6295e4211441bdac5e3b38d287c32536e83bb4e",
    "app/session.js": "4d78e2ace8974ba3ce63aae9be06f3b1730f19784c25b7cb7ddc4c194401449f",
    "app/shell.js": "82f21540eb88591f60a4dd9abee605d4f7b63626b496fd57fe96852aff5fb24f",
    "app/sidebar.js": "8625e151e0ec4d15f4d64c1d16eace797e4341e5c8361694f9bad371d3536bfe",
    "app/store.js": "f6e89eccffe17ebf4a66b853b77a118a925924bee8c8b31b9fdcacfe042f7b40",
    "app/theme.js": "ace76aa649fbcfa88a9702b4f2febc15fce5eeea3cc510e593fe75ef1d4f378e",
    "app/vendor.js": "43293c5da446191dcd8563dea51635804c3d65bae5bc40892e9bb91a6b14a177",
    "app/views/account.js": "9459ec67e2288914ebd66b22e5f50c80abb2da53b7f0972474bed45f84ed453b",
    "app/views/audit.js": "5f6de01c2eb9ed02f920f9c0165be345ea127aafe186e6d67ad05f7e00b5ce71",
    "app/views/classes.js": "34a0ed86e2724ff763818fb10d22d0401d69e41f822d0a1401340952fa911a44",
    "app/views/corpus.js": "be622a6c979c0bc4ef0149422e5c08139099f83c3997f10e4181a1487c1ab826",
    "app/views/entities.js": "a9d54241a38d437695b8ffb20d951349e0d908f49f39542e4ee31c13539a2209",
    "app/views/governance.js": "66ec220c5e12b347cecab20a4319825cfdf8ec0a4e888e1070bcccbb7536f5ce",
    "app/views/graph.js": "0bf9dfdbfb72fff6b66c7bbce9bbf7e1c35b8755695ab33bea31d36e21c4a3e6",
    "app/views/labelling.js": "f4700fc88921415127605c524cd8d3d8442eea40e7239cffb9473ccf950a97a0",
    "app/views/overview.js": "d7059021aa35b6da39d7775570b500c7f100fbcbd7c16b31e7bbd58603989614",
    "app/views/parts/card.js": "ee4cc5d51a27b19f70259e3a5e61df623e53f6c6101f103cebe404d90748013b",
    "app/views/parts/declare.js": (
        "a672d580030e71b1cd9fc358129a0fde7d7f63647699af901fb35b386da932f0"
    ),
    "app/views/parts/facts.js": "2befcc9ed54b18d57de942f3f9583e00a0a39170bb2b45d68b29933881858f12",
    "app/views/parts/finder.js": "b0652368970a0631d24987c4d1a4dd92b40affe833d1d7959db2a3933fc03274",
    "app/views/parts/judge.js": "b31de76729dda8c3d2eb5978b2c8caab4c2483221bfbf95935a4277d50a2580b",
    "app/views/parts/lifecycle.js": (
        "a7c0a01b71795a048fad96956dc6280c0cd9e0a2b5db39893a029de4f9b23dbf"
    ),
    "app/views/parts/members.js": (
        "a3b39b4eb11b14858862c13d2c4c765bc6269c8ee2105ae673accc89d5aeaf24"
    ),
    "app/views/parts/model.js": "c30966786ff8e78e8c326b3b0206fe171298e86ef784b0841d68999ef068e81d",
    "app/views/parts/retention.js": (
        "d740766714ad72e1bae480f2e954f355aab286c84f6badf58dfa33d62d12bc85"
    ),
    "app/views/parts/verdict.js": (
        "bfedceae5645684afeaf50c636bc56f2626f6822a7b03e6f45af9f6f9bc2a9d3"
    ),
    "app/views/parts/why.js": "cb7d3cfe3190accb9f595ce4b064cf4e5cf91f004991df0b7bf41a36de097d87",
    "app/views/promotion.js": "f715fdeedf17a57011e874c590a3135ee5f822ae9c18f0e98c0f55a11692b628",
    "app/views/quarantine.js": "04ed768d8180e7d3182086e8dd12c69c8a497196bfb0086ea5023e34a5470b5b",
    "app/views/scorer.js": "7174739dca46ba17645c35ccd1663fe41e27280e54460b3ef8aa3c176029fad2",
    "app/views/settings.js": "87ecd031c5ab16f549223f5fdaf6711ae8ae97b12aa65f643b21f998a256584d",
    "app/views/situations.js": "afe374be8e8e920369da6fe767294cc29a7cec7fea9899a6deace262f1509241",
    "app/views/timeline.js": "d14d209f42054715fd801eac95ffcc06ea0c8f68fb7fb87a3202d297cbb20d0b",
    "app/views/tokens.js": "f1195d816ebc5e2a9a431d9d618c05299b4497eb60d8a26774be2edb99fbb676",
    "app/views/users.js": "c3fc89f35a09b0c4c748c609d3d45777842f877a59ef88dfdd70ef9a4ea31b19",
    "app/widgets.js": "abe9f6d9342f0335ead13cef4061b54ace36f31e28b0d92308918c96a3561e21",
    "favicon.svg": "c11ec68d389057cc4d4145b3cdf77f3ebfec40150e9f409ff35a7cf419f524b7",
    "index.html": "73f4206c6fa3dc1ae5ff0476f56c1c29e93e6bc38e2510c50311e96a1f833c1d",
    "style.css": "e448d9d4b9e53fd90eea44a4e479aca4a6a9d4fb49fb92c2559b4fa028cc59b3",
    "vendor/CHECKSUMS.txt": "0b492939937a27e94d1b27d4a304ce20d3ee8e1a5b139f748c1e979e6c28670a",
    "vendor/d3.LICENSE": "a823f856687522c6fdca3cc259f6f1e8f75c3349ac3d76398a0e5095600a35ca",
    "vendor/d3.v7.min.js": "f2094bbf6141b359722c4fe454eb6c4b0f0e42cc10cc7af921fc158fceb86539",
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
    "app/api.js": 3_424,
    "app/destructive.js": 4_152,
    "app/dom.js": 2_142,
    "app/format.js": 16_964,
    "app/icons.js": 7_222,
    "app/login.js": 7_137,
    "app/parameters.js": 10_264,
    "app/password.js": 5_442,
    "app/registry.js": 7_468,
    "app/router.js": 5_035,
    "app/session.js": 3_482,
    "app/shell.js": 13_239,
    "app/sidebar.js": 9_434,
    "app/store.js": 5_484,
    "app/theme.js": 6_575,
    "app/vendor.js": 2_385,
    "app/views/account.js": 4_614,
    "app/views/audit.js": 6_490,
    "app/views/classes.js": 7_106,
    "app/views/corpus.js": 4_359,
    "app/views/entities.js": 9_378,
    "app/views/governance.js": 10_218,
    "app/views/graph.js": 16_931,
    "app/views/labelling.js": 5_327,
    "app/views/overview.js": 14_432,
    "app/views/parts/card.js": 6_140,
    "app/views/parts/declare.js": 11_307,
    "app/views/parts/facts.js": 6_340,
    "app/views/parts/lifecycle.js": 13_509,
    "app/views/parts/members.js": 8_897,
    "app/views/parts/model.js": 10_290,
    "app/views/parts/retention.js": 5_003,
    "app/views/parts/verdict.js": 8_420,
    "app/views/parts/why.js": 10_884,
    "app/views/promotion.js": 10_901,
    "app/views/quarantine.js": 2_247,
    "app/views/scorer.js": 12_818,
    "app/views/settings.js": 11_331,
    "app/views/situations.js": 15_602,
    "app/views/timeline.js": 14_510,
    "app/views/tokens.js": 5_104,
    "app/views/users.js": 8_994,
    "app/widgets.js": 11_923,
    "favicon.svg": 608,
    "index.html": 2_159,
    "style.css": 59_529,
    "vendor/CHECKSUMS.txt": 2_039,
    "vendor/d3.LICENSE": 764,
    "vendor/d3.v7.min.js": 279_706,
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
