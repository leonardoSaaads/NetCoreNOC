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
#: **Four lines moved and two are new.** `app/views/broker/scorer.js` and
#: `app/views/broker/promotion.js` changed;
#: `app/views/model.js` and `app/views/verdict.js` are new. Nothing else in the console was touched
#: by a release that added three scorer kinds — which is the property this table exists to make
#: visible at a glance rather than to be argued.
UI_HASHES: dict[str, str] = {
    "app.js": "8870c9345ea82cd79ea6770f42f8966a06f285a0d42e6cce6e2b022a7e1c3f90",
    "app/api.js": "db042593c344a37f646c3ab5df2c1a8b2abbae18d06bf0b2ffce789406d9ed1a",
    "app/avatar.js": "259c4a7bc6d13140d8e7629a053d55d796e2accf54acac141935c82204ddced6",
    "app/brands.js": "c3268b3b5529c9df9caecd9c2e915b3435f31f9c7efa6befd95c86b85261b073",
    "app/brokers.js": "2891961cc81d30ffab508b4cbe4aae04ce3843568bf9a8dc50bba8962b53a9b8",
    "app/chartdata.js": "b503668f63c2d46ae1017744d04d268ee2f3e365b95fdae793a0de82c5b45032",
    "app/charts.js": "f0043e04b0c4ba8b6a7e100f73bda78c67692ef00c8080497ffcb12cecee6418",
    "app/compare.js": "a3b5f7305d0725efb3657f5a5bd861dae46a6dde48209890fc5f80e5e081fb17",
    "app/destructive.js": "4a37ec64d13dcb5d83c0edb4dcbdca90a1bb99ab4551909a72abf40c74f633d4",
    "app/dom.js": "45f1971942517b3ea60c1346433e0b8ace6e1c2eeeedbf58bec81bf78ec1a582",
    "app/format.js": "270b0788ec61b5166ef4dc8589755b06caafe5ba87829bd419d115926521ac4c",
    "app/heading.js": "e4115d0da279413ab9c0855f549aeb1df6c94453e538afc05316b8aa8eaa89f5",
    "app/health.js": "0f1a629bac6126df079d88ededc8581d00cc43f2e9a6afecc293b51e3f73b090",
    "app/icons.js": "dbd5ef0a69acff66565dbfb6ee931d71985ae00ea40d43b1a328414aeec69a39",
    "app/info.js": "15e06186daadead308f180d23feb479eccf2506d70bfadbda477ccc6f7d3c11f",
    "app/layout.js": "447b775510c160d906ed6c54831954734c1f8bd70e90079a1fc280d6a9bad577",
    "app/login.js": "4316945989de1e1526389182db7e54dfdbeed4eab85d5d00821290ffc519dabf",
    "app/logo.js": "163352a0c9a8064f151396f17fa948fecb0334a41a7a4e068de24907845b52dd",
    "app/mailwizard.js": "7949b74b58aeec04522524c5821966357687e9df5487b1e99f496cdf26e3defb",
    "app/markdown.js": "489b4bfdac0ef83e06f2547d580afcfb62d3c0bec2d4435980f506a1c512960e",
    "app/modelcharts.js": "b5a2afc2b92bcb12d15fb0794cd21d0cd928472d7360616660df3c4805efff46",
    "app/mssignin.js": "239400e0d230589bcb93a57077286007fe9f34862e027ccf00dd22b761e546db",
    "app/netgraph.js": "30c308afc4fc650550ce0751d212a54145c293629552028ecf58c2061951251a",
    "app/notices.js": "a6d15665d53bff109c8d5e5be6e1116f4d1a014b1ba10e141b80cf9de7e62261",
    "app/parameters.js": "6c5cf807535cc2ef87d4b1d54f2ab2abb34f595f74c64cc272b26bb88113b1f4",
    "app/password.js": "24e6e0c3927e67542aacef55051cb289c5310c93cd9ab4844a581a5256a7d87c",
    "app/plane.js": "60590abc1b498e9f2474777f588f99a97516b7ae13b67ebd7e3bcc0640ef257f",
    "app/recover.js": "4ec71b672d6d647dc311fede65c51ff407b883f85d3bfdc8cec5e229f92fa5be",
    "app/registry.js": "189d12ca5f72366e1d1803c76195d0f27fd0fa2f4cb4afdd650542b3bb79cc4d",
    "app/roster.js": "9f6bf45a045e9d6d5ae1343d69fd330746b372f1d295344be9b93eac016c9d02",
    "app/router.js": "9d6635854bd66b18c51318ce219fdc54c8260444b95a1da6228afd03afdf313e",
    "app/session.js": "8779cdd7c8bea0dcb8427dc74d3ea6ce5e03c7dc6ea01b566eb4d85f5834c97e",
    "app/shell.js": "37852d7b372dae40007dea454d2096a593bfb1dabe49b39e8a85648052666c89",
    "app/sidebar.js": "817c8bb3751662ee64d8860d1b620f0dec244e07a3aca17cf0c352579f976255",
    "app/stack.js": "7fc57239d1f7cf2de84f39a726914bcc305cfd2b90de97df08870a6570ce5185",
    "app/store.js": "eb5e8965f788e5b8054d9aef7ea0ce060046203f3c17233f75bc24ed11951e0e",
    "app/theme.js": "6c51e4e51ec2a71786e8183b6e36fb1ab473a70151149123f89eb3944f33065d",
    "app/twofactor.js": "a8a55e2f44646be76a8acc511442d3f69c36005374be5888175828159cfdec6b",
    "app/views/admin/access.js": "0ade3e12b0216d74176fba115410c24e7494ba969f780dd9bd35ea1f196cef87",
    "app/views/admin/account.js": (
        "48238d2682e111c8534b078e0a9db9394f394f9d53d94eb7764c33b67bc04024"
    ),
    "app/views/admin/audit.js": "54009aba2ee0bcbc665abefd6b74d0ee16ab32676258452fee7d58a164b3f0a7",
    "app/views/admin/parts/apiref.js": (
        "79c46ab409d402f6389fc803c05594b6ffd9347f0160f309fd8dfac45ba1fc9e"
    ),
    "app/views/admin/parts/autonomy.js": (
        "5120c903eb8b60c3772bff97840835180d1304d615399762514dd0869a3028d9"
    ),
    "app/views/admin/parts/capgrid.js": (
        "4ee4f22219de9db4e88551177e17e93c24d075ef0061750b424a6f5aab7fe6a1"
    ),
    "app/views/admin/parts/custscope.js": (
        "f899e419e053f1876ca9c9070997cca217d88a3da1a1ce4a935c31805ce11d18"
    ),
    "app/views/admin/parts/email.js": (
        "d3e18c3f39fe8c55ddc53a8b6c5afdc6d0a304a62731a398e92978800c47ca57"
    ),
    "app/views/admin/parts/facts.js": (
        "a4ab82c1dec7eb3065be4f86895e92425485461854753a5b16ddb5cd056f2a03"
    ),
    "app/views/admin/parts/people.js": (
        "73cbb20ca32f896afc4d1e17bfcccc956c46d7b006062be1805b9c434ae0136a"
    ),
    "app/views/admin/parts/personsecurity.js": (
        "1324bd6bdbaae5ba69e3a8b253561e079d71ca3b1e326f1671aa21c1f3097af7"
    ),
    "app/views/admin/parts/retention.js": (
        "07503860a8448b2da7217780d7d5b292d9f5eb5c4e679a4499747f6d9c3cf8a6"
    ),
    "app/views/admin/parts/roles.js": (
        "23e27b7e5de2a527e04e17ec0d02aef4b9c04890a6b396b0ca60044965f68803"
    ),
    "app/views/admin/parts/scopeexamples.js": (
        "6ae09ee36621f287c3ca3caa9508890647030081c36f59a9762640374e7336ed"
    ),
    "app/views/admin/parts/snmp.js": (
        "a6b84308e9a0bdfeca69ecea4523f44e81800010c68c99cde5f7f0da344424cf"
    ),
    "app/views/admin/parts/snmpuser.js": (
        "d15719064bb784f146075c281dc202f816f5d2c494708047c9c2a031c5155bae"
    ),
    "app/views/admin/parts/tokenspanel.js": (
        "6813792adbdfcf832009fd7a273596e8d885e4ef2124606cbba659192383a781"
    ),
    "app/views/admin/parts/visibility.js": (
        "9c74b421d39f77e93a0eed90ace6281ea6b3a31d0a702798937488b6d566d8a0"
    ),
    "app/views/admin/settings.js": (
        "a80fe4509d33926ed32437b9bcffb713ce3b0abe52b25ac7cccac7b7766d8167"
    ),
    "app/views/broker/brokers.js": (
        "00f2647bc28869c04c7f8c6b5b500c06546431512fc587b78f7c81f52a842c48"
    ),
    "app/views/broker/classes.js": (
        "1d453b0912e9b5f0d4ab769a869b63d7b9651478537defb616820efe2cc6514b"
    ),
    "app/views/broker/entities.js": (
        "a126a18c660a1aa131dd6e62ba809da7393e622eb44ee6291ceb8fa0abd44fd2"
    ),
    "app/views/broker/graph.js": "5dd4ed7e4c2e0cb02dcf65c69b9df13beca9913f3c2fc9c5541b6d3cc9e51f5b",
    "app/views/broker/learning.js": (
        "0851d6e693333cd10c65fada0f292787b0f9859c95bde766a00fba8598580a39"
    ),
    "app/views/broker/maintenance.js": (
        "e67c1de4eb8f754895c385f4aba9f4501bdfaa902743f8297295c321a3423199"
    ),
    "app/views/broker/overview.js": (
        "e6cb6e2333bf318dda0575ae82dbbde7b1ababf5c329811cdc5fbe11ebd1bccb"
    ),
    "app/views/broker/parts/brokerhome.js": (
        "f860073da36af74f29329c36cd85ddf03b52595ed4d72299906732799bd023e7"
    ),
    "app/views/broker/parts/brokerlearn.js": (
        "04063ddf03e4fafdce90f01d57dd3b6312bc50a3173343cecce6f9007e6a963c"
    ),
    "app/views/broker/parts/brokerpick.js": (
        "f268896b7559db6b4c18082e3179c8c73eb03b4ba9b1b0fc0af7984fb14e78d4"
    ),
    "app/views/broker/parts/brokerwizard.js": (
        "1cb4e5a4478207e9ad38bb4f3b5e7dd4eea9ddea286e87b25c3f6196b00c85af"
    ),
    "app/views/broker/parts/bulkclear.js": (
        "706de71d2f33743ceb46b6ffb6cf7296bcd09f84fd09ff24b6890c613e3f4ae6"
    ),
    "app/views/broker/parts/card.js": (
        "fd576b83e08dc9adc424e5bba184c0dec73da875da87660c6178d6b009105b2b"
    ),
    "app/views/broker/parts/correlation.js": (
        "0b2cfa20e5b7efa8993d808c7a8ba04291e29c4e12ab3018f5a4298061984e69"
    ),
    "app/views/broker/parts/decide.js": (
        "ee6e85df33f36099bd6b2b5ebfa3e4c19e6c12e931554889deded06879c50d5a"
    ),
    "app/views/broker/parts/decider.js": (
        "f2d7042cafd8128c1c77b6ea738e4c71b2ede76aeec5d6dae0d90e424f938512"
    ),
    "app/views/broker/parts/declare.js": (
        "26990ab7cfe557d64e3fabcf536e9fe021fb7fccccfe06a52186674af28a19a6"
    ),
    "app/views/broker/parts/element.js": (
        "1b63d81329d8730aef3d9e94478952bbfb8a74cd14f4a06762e132bfc75738b6"
    ),
    "app/views/broker/parts/evidence.js": (
        "cf28ead3e7d3c474821de2efefa312df206a013d425d11d7d0c7cc48fa4dd705"
    ),
    "app/views/broker/parts/finder.js": (
        "06ce40a3c9de03e8baa7c9e8ee54f7cd0840f583242b2d87eeac8263581d5ea1"
    ),
    "app/views/broker/parts/firstrun.js": (
        "77fe9b99fb8e7d3033a606b27a98167b8e34303beec6a06f5367b64f1bd86892"
    ),
    "app/views/broker/parts/importbox.js": (
        "a609b85f0fbb5725fa1fbd5c32895eb60aa21e8b375314c3aa8c7002e471f889"
    ),
    "app/views/broker/parts/judge.js": (
        "3e6d9faae3c987a9cde27c6c524a32a4b59f8f602470b117beb43ff0e103927c"
    ),
    "app/views/broker/parts/keeping.js": (
        "9290839265fce3ed9fcc986a89aaeadeca06462ed395b66b458c6e885032fc08"
    ),
    "app/views/broker/parts/league.js": (
        "a3e5f3025be1f902b2b2bf135240ca7f08898748bb0f826057d0944461ff1be9"
    ),
    "app/views/broker/parts/leaguecharts.js": (
        "8a0d168a30296b1a856d7521d902772e4707efa591317e46fa6a9d4d374568da"
    ),
    "app/views/broker/parts/leaguecompare.js": (
        "f86cd2c0c6fe878cd0693f632e160b64abf9d30bbd8f9dcab5d56e9eedf0c280"
    ),
    "app/views/broker/parts/learnuml.js": (
        "6c7254a4a02642bbe46c22888b33d569e1002dd2928d3f619d939b8aa74e8ce5"
    ),
    "app/views/broker/parts/lifecycle.js": (
        "59e6d499855ed1bb49419891200ea49ebee173137106b3f2cc357fd6e071b0bd"
    ),
    "app/views/broker/parts/marks.js": (
        "4c8c97d20fd7f84ccaebff4ebf857d74f7d95ead97b2c19ec98de99925186a92"
    ),
    "app/views/broker/parts/members.js": (
        "dd7559318613c5085c2fbc5892e6a3e246c9d2b249692ad88ad8c67a72649040"
    ),
    "app/views/broker/parts/model.js": (
        "e27cf49f10d13a79ff818d1d25069bb9e86fd45f6011b6108575f0b2a55ff5eb"
    ),
    "app/views/broker/parts/models.js": (
        "8b1895fcf05e7fb7c568bc3376c0d3d3de22f6ee043907843780b5689028f61d"
    ),
    "app/views/broker/parts/mwboard.js": (
        "a2053295bb7a1aeb2266cf2ca8fe7b4a2f830a131d56eb37d4a9f1a1dfebc59a"
    ),
    "app/views/broker/parts/mwdetail.js": (
        "6f29ccadcf55a60e2e5dca8f54ecfad6d80e2c6e5e4c0855e8aec615cdf22a7a"
    ),
    "app/views/broker/parts/mwdraft.js": (
        "fb1f31ca7b40cf2520bee4d248f0bf2bf180eb1a23fb081a2b1a39e7f37d1295"
    ),
    "app/views/broker/parts/mwform.js": (
        "44bd2fdafc844af17f7f63c32c51a4a00e219c5310021d8f4fee70ff9605d179"
    ),
    "app/views/broker/parts/mwmarker.js": (
        "a01d0635bfc83b3fed5c31cbc998ac01826476b08e3d92d5e49c13a289c5a31d"
    ),
    "app/views/broker/parts/mwreview.js": (
        "118fed1710b0cebd49d5f18b571826c45fc10bca2716eb44a96ed15861d20382"
    ),
    "app/views/broker/parts/mwrules.js": (
        "0ab9924fa6d2ecdcf73008366eb7b41782e8c179adf68dd5d89354707d29a724"
    ),
    "app/views/broker/parts/mwsummary.js": (
        "1b7025f670382cc0e244e2aa74d027856180175d5b7fea71bf59e31e8688ceb2"
    ),
    "app/views/broker/parts/mwtime.js": (
        "f5f0cdbe8799e4f8a4eb8cd4bd75ec8c178ba5484969771b13769a69b5ba871c"
    ),
    "app/views/broker/parts/mwwhen.js": (
        "4c42053651cb8031d6d06b4c9e519fc0dd269ef312071281b3c2acda3c352b6c"
    ),
    "app/views/broker/parts/nedetail.js": (
        "d98f63b2f009cc6dbc5dae34f23a7ff23a30b215d230353abc8d6d8d3becb200"
    ),
    "app/views/broker/parts/oidtree.js": (
        "4221ae01b66ef99841b5ce5e8aaa23fe99bc7cd24bf43ed05a980f57c332558d"
    ),
    "app/views/broker/parts/pipeline.js": (
        "5b980018c5ea6f148f871d1a3a85fc6dd3224062db277de0017dbb6cf4b89268"
    ),
    "app/views/broker/parts/pulse.js": (
        "91ccdc9f05e72db41cf79c3e23400ade24682fe3fa0ee16a15152201e9587043"
    ),
    "app/views/broker/parts/restructure.js": (
        "efebbd2bcc84226887e27b35af94f5279b0fcc654768d1d71e94408f343f0a7f"
    ),
    "app/views/broker/parts/searchpanel.js": (
        "066193423df66775e38c33b9029ead09874dd64297b1e12f929e881f02fe20b4"
    ),
    "app/views/broker/parts/sequence.js": (
        "c6e955cac1493b7c5a68f1b2d41e7d36915d240089e7081591db08c197cd06ce"
    ),
    "app/views/broker/parts/severity.js": (
        "019054a943ae28b059a98ed741ef2ff9aacc4ed96e9d22385d43ac10e0cc120e"
    ),
    "app/views/broker/parts/sitejudge.js": (
        "ed5306f6db87417a4e2d86b1b868eb1e1ecee9e16e2fd499150ac34aa8be864c"
    ),
    "app/views/broker/parts/sitsummary.js": (
        "ba7f4252a2efc7f49f4ce36db8f74bdce9e850b63d0834de72f199005e72012c"
    ),
    "app/views/broker/parts/tlfilters.js": (
        "219e45a0dd868afe55395b67d25ce0a9713593d8d9acb516c719275784ab85d7"
    ),
    "app/views/broker/parts/topassets.js": (
        "f569e01151abc94ea43f7b041e467e6e5596b5c437c0439185eb6f2f19905747"
    ),
    "app/views/broker/parts/verdict.js": (
        "ba6357931675c9f8bab8a9e0e21ef4409424742a60f5ef4fa3a809607a9d18cf"
    ),
    "app/views/broker/parts/why.js": (
        "1389d301efc756f85a79a7e9f0943d9408b885dd58fa53b2a1e08f6a0d3c5970"
    ),
    "app/views/broker/promotion.js": (
        "45e441a86edfb39d6381eb7c0fd62b077aed5b13497c3b68ece0b5e672dea7d9"
    ),
    "app/views/broker/quarantine.js": (
        "cf6945b5949215eadc22fa4af95868b1dd5a314fdfa7604111d5e9302b71cf30"
    ),
    "app/views/broker/scorer.js": (
        "4ea60fe04227025a21538db709c9feab7233832b6a5ee33fc186ff2944228da8"
    ),
    "app/views/broker/situations.js": (
        "aa0e70fff3f6e60155fe64d1dfbf90c2875155450dd06daec73483de5d11613f"
    ),
    "app/views/broker/timeline.js": (
        "9974da67722d8cf8ac928d41175ecdc8d77ae6dc08b59358497859517603c427"
    ),
    "app/views/noc/agents.js": "d670fc06955dd40e718b0b68042dd2979ad944147754bf00771e153ab4a1d179",
    "app/views/noc/cases.js": "b09a5028c50e7c6f4cbe61f47b62238724a31d9b7b412cf5c77e3a72460f8ed7",
    "app/views/noc/customers.js": (
        "479d2db5e149fa41da56537290e9161af613d3ea046f9309bca7492e1478a48c"
    ),
    "app/views/noc/nockpis.js": "ad145e2f67715d8aa0ec58b9d8d494d24d8a68963af6444bf33d6552262f28ed",
    "app/views/noc/nocoverview.js": (
        "1ee0a10c1ffaf475a262595df707a8f70ffc7f0df64101724921ecb3ddcbd32b"
    ),
    "app/views/noc/org.js": "354575b3db7731ee73cce920365a5c676abe28cb3dced81da7fa6e1d27b7cb4d",
    "app/views/noc/parts/agentadmin.js": (
        "adcef1220a5ee504931c08f4edbd45140fd0c65cf96c0eb345d18159c85aad73"
    ),
    "app/views/noc/parts/agentdash.js": (
        "587dcf0823b65ea2d1bf3d16ea18bc29b4f6daee501928e80fa2ea80e3295643"
    ),
    "app/views/noc/parts/agentdetail.js": (
        "4b9539daac779b0977dbdc75ea7e3445b5758ac0766a23b3b06bb39a490127ff"
    ),
    "app/views/noc/parts/agentform.js": (
        "60009e4678220f78b3e360e7a34bda71e27dcd06bf33386e90031746b7a9b612"
    ),
    "app/views/noc/parts/agentkey.js": (
        "787004559acfc1ca5d27e04b55344f19155bb1fa2ded984eb0bd0323ee8512ba"
    ),
    "app/views/noc/parts/agentrun.js": (
        "76d5c0bf3997fe5ef19961a33dc09c2376e8256df6906cf4472dbe90c95dc259"
    ),
    "app/views/noc/parts/agentruns.js": (
        "6eec75d8cf677cb4a2f351cf303475ff35e9dc3adc0b91bf887acfc4591a1b88"
    ),
    "app/views/noc/parts/aiproviders.js": (
        "18af413e5aa1f26ae5b1ce8b2591086f361e1526eb1a6d886016d75bfc37e9b5"
    ),
    "app/views/noc/parts/caseacts.js": (
        "cb3e73c39031c1d03f0707f221d411b4c2e8a0317ed70f6ff8c1948c51cacb12"
    ),
    "app/views/noc/parts/caseassign.js": (
        "7023a85375a2365d593383306b27470376a1664f2963f686696a89cbeea3baed"
    ),
    "app/views/noc/parts/caseclock.js": (
        "5bd53f8b3878d74768a1ca078fcbc0a8f6f57e315493e2498a2ff91eb29244fc"
    ),
    "app/views/noc/parts/casedetail.js": (
        "1fae2f12d6542cb075790c946fb92cea26bcdc18bf1f4047967d1df7a3359eb2"
    ),
    "app/views/noc/parts/caseemail.js": (
        "40d7cad5776f4383860999107a9a7b2beb97233186fc6dfd444ce857b9389a33"
    ),
    "app/views/noc/parts/casefeed.js": (
        "ed8f102189babc7eefdb7141d6cbd41917bcf9533d13923bb111a262063e0cb8"
    ),
    "app/views/noc/parts/caseform.js": (
        "8e3759332d9cce96972ff72f0d847e389715773f9a338bac3c13c5c002661641"
    ),
    "app/views/noc/parts/casemilestones.js": (
        "829f6daa4c4c15a901a4af4e71e94d4df9cef74069081fb79d1852581cd1621b"
    ),
    "app/views/noc/parts/circuitedit.js": (
        "3910b7cfc6d9fc2a82be84b8893f25ff7656d4cbf6b0335cacca290bfbf4cf95"
    ),
    "app/views/noc/parts/circuits.js": (
        "96dce74786b427a0af232a96a8b03f5fee3299b77dd83b6bccdd2d3caea81de1"
    ),
    "app/views/noc/parts/credlist.js": (
        "4fa1b0a6059d4da35e3baf76322b04f327e3a083699985e646569fb20c8cb93b"
    ),
    "app/views/noc/parts/custaccess.js": (
        "7b34ac51e443ebff1e3dee849755825aa3c82e2c955f09d198b995036b4e52cc"
    ),
    "app/views/noc/parts/custbook.js": (
        "ded33c77cb92e5c71f40d30d0de38d4bc1033d3483d5204dd722b9a376f14705"
    ),
    "app/views/noc/parts/custcontacts.js": (
        "91f7b26f67fc91430ab3073382a68bcc2fa4f661d79739e6b697fefc502286ed"
    ),
    "app/views/noc/parts/custdocs.js": (
        "9b5c1fb247135775376ed8e06fb0ed39c2a4c83efd0dbc70eec2ef914e617683"
    ),
    "app/views/noc/parts/custhead.js": (
        "84daeab13a8697a00ea582c0ec9f21bfd8f64fd1b78efe9fa4d2003396247a75"
    ),
    "app/views/noc/parts/custmail.js": (
        "f014f7455558838bf00e1eea1c5ecaff5b1221da48d7cd1f54e1662c8781632b"
    ),
    "app/views/noc/parts/custroutines.js": (
        "a07030ac5efec1278c54b4053640362ce1a79958fc279e4a539bdda92111e8a5"
    ),
    "app/views/noc/parts/nocgrants.js": (
        "02cdbba050937aab071ceaa844db8cd8418385cbc8a3d6abf63ecc67df842ef4"
    ),
    "app/views/noc/parts/nocstart.js": (
        "90111af0703e8900244b6040ce8d80e9e9fc8c916abe3885c209aa1b00a22065"
    ),
    "app/views/noc/parts/nocui.js": (
        "06b7b3874f76a298fcae6b661e255f1ad11fc027f08b22902fbf6305bbff4346"
    ),
    "app/views/noc/parts/ollamamodels.js": (
        "d3df4a6e878bdcd397a3a23b09c28aaa92f81f82acd6bb91fa2849bd14764c1b"
    ),
    "app/views/noc/parts/orgmove.js": (
        "cb58997d38c5e99b1fcbb21a990504de97f2d7e182d050e222c092ec85a81a17"
    ),
    "app/views/noc/parts/routineform.js": (
        "3c7a79ab119bb797988465cdcdd593da498b0c7acdb515a2e9dbd6f332dd3665"
    ),
    "app/views/noc/parts/secretdetail.js": (
        "2b653da2d8ed4c7c4b64dd06b45b9e5c58639a07eb68d4c592fa0ed58be2e040"
    ),
    "app/views/noc/parts/secretform.js": (
        "d049ba2251869ec9ea3338db06ca5606408e84543eef060394cc352f2c6fb352"
    ),
    "app/views/noc/parts/stepup.js": (
        "a5cf528ee9f1f17dd444179df8762ab99cc545f2840eb754b6e5ed0c22a478dd"
    ),
    "app/views/noc/parts/vaultui.js": (
        "90e536a23797e0bfd9bef029151fb127d0ec3b02e107a0f2dcc524954cc67cdf"
    ),
    "app/views/noc/routines.js": "cfde981148a50091cb45895165a5b4e266700c2c4ecfb05fbe2250a5174c2d95",
    "app/views/noc/secrets.js": "b7870e6f144defb03f29668bf4bcd39574dc9472286d23f182df274f9e2378d9",
    "app/widgets.js": "8f5fde66bf02cdcd5e59933c6f946134a8084e3e1a2b90f267ead58ceac4e722",
    "favicon.svg": "67be772b54d99f4978143983b4d5313c8aab40e1498a1fa748680e00f7735bf2",
    "index.html": "4d35d7908118371e87e47c19047354ce0b2cec589032216a8a303114b70eb4c4",
    "login-bg.svg": "5f677f3371a2828c8d21668cd83381c105ccdc1780a66dc09fc6f6bc19d211d6",
    "styles/admin/audit.css": "12f67e7c62ce398fdcf5b0721fd8aa5eee999d5bfac740ccb3d8beb624f6f5a3",
    "styles/admin/people.css": "4fc298eb3967c41077ee998bf0a57cda7f5c6d561043b37d5a1f7e61bef9fb2d",
    "styles/admin/settings.css": "e09f093707cb5a4f65ceecf9d9eb172997f862aec587047f20e7d1fe8b71d895",
    "styles/broker/brokers.css": "71e21eaf4cfbeb11a45cea9572922706c86e515fc88c7ec8843bb7107e546516",
    "styles/broker/catalogue.css": (
        "a3dbc9550bb88aac32c4722224f078f04d357af18b11fda989d1d1239d1f3764"
    ),
    "styles/broker/entities.css": (
        "ffb705d87fa0e05cb9e2f78bc7b82649254848dfbb6c0ca579efe5cbd1b4e808"
    ),
    "styles/broker/first-run.css": (
        "6f387c9e9d4c956d7df211cdf5bc5025b4295759bde129d9b75693db8be1b2cc"
    ),
    "styles/broker/graph.css": "c72ab86b83d289c4477033d794f1eb08ab042b743390fa149b390d2138a5c571",
    "styles/broker/health.css": "a151ab99592d95c73cf294a32f0a75caecdf522461f75b77e10e565068b2603d",
    "styles/broker/learning-screens.css": (
        "07bb1a7f6e9f733c2362eb5d2bd4efff5f79e366bb45f4f0ac914f1b08c3a29e"
    ),
    "styles/broker/learning.css": (
        "0b6cf6730fe781e2bf4642b80cec28e2c1076205b5bb4517081e51e74ff329e3"
    ),
    "styles/broker/maintenance.css": (
        "105264c43930b3eb79e04ad5bb9fd3454b5747c8dc6777f425623cc318ddc343"
    ),
    "styles/broker/overview.css": (
        "690b2debc22cfaa60f184d27693e6a60d1fa75a6d8be1fe707382e5b5c55f8d9"
    ),
    "styles/broker/situations.css": (
        "bba641cbd50a2cb0b1990460b1f0ad107d24a24e67c9a46ed0f625e9a788035f"
    ),
    "styles/broker/timeline.css": (
        "1e7b081649ec2aa9ba3644abf98aa12576489d212cf52ea15b6062f5a430dda1"
    ),
    "styles/core/base.css": "7c3e716b1e8fbc6247d18d6f8052f392f6026b3a8f11dc3245360a0ca489ba10",
    "styles/core/charts.css": "90adc7633dae6fcb73615f11dee743dd61ff717b489c04f934063278e7772748",
    "styles/core/components.css": (
        "119a20c7684eae51b1c00f9ad0da37e00a532a969ee9b03dfc6967315ab59fdb"
    ),
    "styles/core/marks.css": "e4ddc1f69078a19d74928a7959159e1513042cc291ca67b498d744a97ac5a162",
    "styles/core/panels.css": "280adac5fae48e83e1abbc530b6ab0bc1ac0fb2e417379c6009c8a9e9169ace1",
    "styles/core/shell.css": "d28faf03d4d69feccc43e8fc0236c0e194c10cac5115c1e5443922fe55feb503",
    "styles/core/sign-in.css": "f6dbf93af39880d7572dc03f6f804e8421c7d79aa22049a6fd65a14d5b643f9a",
    "styles/core/tokens.css": "8a69f87ab999e0cdadcfcc5ff24a5d7bf1a172ee4e37864f311b46bbc3524b48",
    "styles/core/widths.css": "457e0b2b769295ecf4a5a7e733d2d0a02ad5f2b685c730c12e2719f71269187d",
    "styles/noc/agents.css": "94773616511f5532b0e4cad7c1170dca8c3e74c8e8f6c89b813741f67a88db6c",
    "styles/noc/cases.css": "14fd38b4a4f6a56e9ac69afec5ad90e69bb0de9f54363346926dccae73b36101",
    "styles/noc/customers.css": "63190db46d0df16f6b942be00bf356f984504f18daf841fcf004d36cfbd1b7e8",
    "styles/noc/ollama.css": "87585336e4e420fd486024762dfb99fda358582408c6745401d51d29d9b5154e",
    "styles/noc/overview.css": "6dfe622851bf4c9e98d7bebfd28261665b8dfbacc787ab653009523cb92a1550",
    "styles/noc/plane.css": "f0557e6d057e7ab08cbdb72b4c5c9026af892013b17e5599e39fe7e2758ea02c",
    "styles/noc/routines.css": "07461f834efb0609fc31736dd3937360b130f78c8ac66ceb9e6aa502aab9fab2",
    "styles/noc/secrets.css": "fa980457460a27afea63fac5ac29b6ff7c2101608b55ec1e0df7878df943d96c",
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
    "app.js": 5_594,
    "app/api.js": 5_405,
    "app/avatar.js": 7_309,
    "app/brands.js": 15_657,
    "app/brokers.js": 6_202,
    "app/chartdata.js": 8_533,
    "app/charts.js": 15_836,
    "app/compare.js": 3_524,
    "app/destructive.js": 4_811,
    "app/dom.js": 2_153,
    "app/format.js": 15_764,
    "app/heading.js": 1_603,
    "app/health.js": 7_617,
    "app/icons.js": 14_134,
    "app/info.js": 2_407,
    "app/layout.js": 9_799,
    "app/login.js": 8_780,
    "app/logo.js": 1_573,
    "app/mailwizard.js": 13_833,
    "app/markdown.js": 5_404,
    "app/modelcharts.js": 17_564,
    "app/mssignin.js": 4_521,
    "app/netgraph.js": 11_593,
    "app/notices.js": 14_771,
    "app/parameters.js": 10_388,
    "app/password.js": 6_064,
    "app/plane.js": 4_075,
    "app/recover.js": 4_934,
    "app/registry.js": 15_421,
    "app/roster.js": 4_070,
    "app/router.js": 5_261,
    "app/session.js": 4_224,
    "app/shell.js": 17_571,
    "app/sidebar.js": 13_188,
    "app/stack.js": 6_564,
    "app/store.js": 7_101,
    "app/theme.js": 8_141,
    "app/twofactor.js": 13_070,
    "app/views/admin/access.js": 4_952,
    "app/views/admin/account.js": 14_557,
    "app/views/admin/audit.js": 12_549,
    "app/views/admin/parts/apiref.js": 8_853,
    "app/views/admin/parts/autonomy.js": 10_616,
    "app/views/admin/parts/capgrid.js": 6_769,
    "app/views/admin/parts/custscope.js": 6_257,
    "app/views/admin/parts/email.js": 7_032,
    "app/views/admin/parts/facts.js": 6_394,
    "app/views/admin/parts/people.js": 16_981,
    "app/views/admin/parts/personsecurity.js": 4_865,
    "app/views/admin/parts/retention.js": 5_110,
    "app/views/admin/parts/roles.js": 4_988,
    "app/views/admin/parts/scopeexamples.js": 4_748,
    "app/views/admin/parts/snmp.js": 11_375,
    "app/views/admin/parts/snmpuser.js": 7_664,
    "app/views/admin/parts/tokenspanel.js": 7_693,
    "app/views/admin/parts/visibility.js": 4_462,
    "app/views/admin/settings.js": 16_383,
    "app/views/broker/brokers.js": 9_094,
    "app/views/broker/classes.js": 8_117,
    "app/views/broker/entities.js": 8_743,
    "app/views/broker/graph.js": 9_522,
    "app/views/broker/learning.js": 8_787,
    "app/views/broker/maintenance.js": 13_247,
    "app/views/broker/overview.js": 11_955,
    "app/views/broker/parts/brokerhome.js": 12_101,
    "app/views/broker/parts/brokerlearn.js": 2_767,
    "app/views/broker/parts/brokerpick.js": 1_964,
    "app/views/broker/parts/brokerwizard.js": 9_754,
    "app/views/broker/parts/bulkclear.js": 3_383,
    "app/views/broker/parts/card.js": 10_173,
    "app/views/broker/parts/correlation.js": 7_784,
    "app/views/broker/parts/decide.js": 5_383,
    "app/views/broker/parts/decider.js": 10_185,
    "app/views/broker/parts/declare.js": 13_101,
    "app/views/broker/parts/element.js": 6_180,
    "app/views/broker/parts/evidence.js": 12_687,
    "app/views/broker/parts/finder.js": 7_799,
    "app/views/broker/parts/firstrun.js": 7_102,
    "app/views/broker/parts/importbox.js": 12_576,
    "app/views/broker/parts/judge.js": 14_622,
    "app/views/broker/parts/keeping.js": 8_326,
    "app/views/broker/parts/league.js": 12_066,
    "app/views/broker/parts/leaguecharts.js": 10_705,
    "app/views/broker/parts/leaguecompare.js": 6_198,
    "app/views/broker/parts/learnuml.js": 5_620,
    "app/views/broker/parts/lifecycle.js": 9_487,
    "app/views/broker/parts/marks.js": 6_007,
    "app/views/broker/parts/members.js": 11_524,
    "app/views/broker/parts/model.js": 10_299,
    "app/views/broker/parts/models.js": 5_143,
    "app/views/broker/parts/mwboard.js": 8_689,
    "app/views/broker/parts/mwdetail.js": 8_501,
    "app/views/broker/parts/mwdraft.js": 9_888,
    "app/views/broker/parts/mwform.js": 15_019,
    "app/views/broker/parts/mwmarker.js": 3_580,
    "app/views/broker/parts/mwreview.js": 5_274,
    "app/views/broker/parts/mwrules.js": 7_642,
    "app/views/broker/parts/mwsummary.js": 9_573,
    "app/views/broker/parts/mwtime.js": 11_507,
    "app/views/broker/parts/mwwhen.js": 5_833,
    "app/views/broker/parts/nedetail.js": 11_543,
    "app/views/broker/parts/oidtree.js": 6_370,
    "app/views/broker/parts/pipeline.js": 9_981,
    "app/views/broker/parts/pulse.js": 5_740,
    "app/views/broker/parts/restructure.js": 13_735,
    "app/views/broker/parts/searchpanel.js": 6_111,
    "app/views/broker/parts/sequence.js": 5_788,
    "app/views/broker/parts/severity.js": 3_927,
    "app/views/broker/parts/sitejudge.js": 6_252,
    "app/views/broker/parts/sitsummary.js": 3_654,
    "app/views/broker/parts/tlfilters.js": 5_033,
    "app/views/broker/parts/topassets.js": 4_296,
    "app/views/broker/parts/verdict.js": 8_429,
    "app/views/broker/parts/why.js": 15_582,
    "app/views/broker/promotion.js": 13_890,
    "app/views/broker/quarantine.js": 2_259,
    "app/views/broker/scorer.js": 13_483,
    "app/views/broker/situations.js": 17_445,
    "app/views/broker/timeline.js": 7_538,
    "app/views/noc/agents.js": 10_398,
    "app/views/noc/cases.js": 11_517,
    "app/views/noc/customers.js": 13_728,
    "app/views/noc/nockpis.js": 4_785,
    "app/views/noc/nocoverview.js": 13_829,
    "app/views/noc/org.js": 12_147,
    "app/views/noc/parts/agentadmin.js": 8_066,
    "app/views/noc/parts/agentdash.js": 15_464,
    "app/views/noc/parts/agentdetail.js": 9_448,
    "app/views/noc/parts/agentform.js": 12_637,
    "app/views/noc/parts/agentkey.js": 4_342,
    "app/views/noc/parts/agentrun.js": 11_660,
    "app/views/noc/parts/agentruns.js": 4_149,
    "app/views/noc/parts/aiproviders.js": 16_316,
    "app/views/noc/parts/caseacts.js": 6_412,
    "app/views/noc/parts/caseassign.js": 5_945,
    "app/views/noc/parts/caseclock.js": 2_735,
    "app/views/noc/parts/casedetail.js": 12_203,
    "app/views/noc/parts/caseemail.js": 14_021,
    "app/views/noc/parts/casefeed.js": 9_717,
    "app/views/noc/parts/caseform.js": 10_126,
    "app/views/noc/parts/casemilestones.js": 5_162,
    "app/views/noc/parts/circuitedit.js": 8_828,
    "app/views/noc/parts/circuits.js": 14_221,
    "app/views/noc/parts/credlist.js": 2_738,
    "app/views/noc/parts/custaccess.js": 5_865,
    "app/views/noc/parts/custbook.js": 6_436,
    "app/views/noc/parts/custcontacts.js": 9_072,
    "app/views/noc/parts/custdocs.js": 8_205,
    "app/views/noc/parts/custhead.js": 5_178,
    "app/views/noc/parts/custmail.js": 7_170,
    "app/views/noc/parts/custroutines.js": 6_039,
    "app/views/noc/parts/nocgrants.js": 5_513,
    "app/views/noc/parts/nocstart.js": 7_403,
    "app/views/noc/parts/nocui.js": 3_874,
    "app/views/noc/parts/ollamamodels.js": 5_556,
    "app/views/noc/parts/orgmove.js": 4_157,
    "app/views/noc/parts/routineform.js": 17_311,
    "app/views/noc/parts/secretdetail.js": 14_296,
    "app/views/noc/parts/secretform.js": 12_493,
    "app/views/noc/parts/stepup.js": 4_864,
    "app/views/noc/parts/vaultui.js": 4_748,
    "app/views/noc/routines.js": 15_300,
    "app/views/noc/secrets.js": 5_095,
    "app/widgets.js": 14_354,
    "favicon.svg": 1_136,
    "index.html": 3_414,
    "login-bg.svg": 18_884,
    "styles/admin/audit.css": 5_339,
    "styles/admin/people.css": 15_995,
    "styles/admin/settings.css": 14_948,
    "styles/broker/brokers.css": 8_833,
    "styles/broker/catalogue.css": 6_296,
    "styles/broker/entities.css": 4_768,
    "styles/broker/first-run.css": 5_443,
    "styles/broker/graph.css": 10_277,
    "styles/broker/health.css": 7_349,
    "styles/broker/learning-screens.css": 5_922,
    "styles/broker/learning.css": 16_427,
    "styles/broker/maintenance.css": 18_915,
    "styles/broker/overview.css": 14_863,
    "styles/broker/situations.css": 18_897,
    "styles/broker/timeline.css": 6_616,
    "styles/core/base.css": 10_634,
    "styles/core/charts.css": 18_023,
    "styles/core/components.css": 5_940,
    "styles/core/marks.css": 417,
    "styles/core/panels.css": 6_401,
    "styles/core/shell.css": 14_257,
    "styles/core/sign-in.css": 11_836,
    "styles/core/tokens.css": 10_489,
    "styles/core/widths.css": 7_450,
    "styles/noc/agents.css": 24_542,
    "styles/noc/cases.css": 19_580,
    "styles/noc/customers.css": 18_107,
    "styles/noc/ollama.css": 2_231,
    "styles/noc/overview.css": 8_248,
    "styles/noc/plane.css": 6_123,
    "styles/noc/routines.css": 9_340,
    "styles/noc/secrets.css": 9_991,
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
