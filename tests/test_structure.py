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

# **v0.15.0 replaced this taxonomy** (DECISIONS #198). It named six directories, four of which held
# the per-release record; those are deleted and their contents are at `3ecf237`. What remains is
# organised by what a reader is trying to do, so the guard now asserts the *reader-facing* files
# exist rather than that a set of producer-named directories does.
DOCS_TAXONOMY = ["adr", "analysis", "plans"]

# Every runtime submodule must resolve from the installed package under its unchanged name.
# v0.7.2: `api` became a package (DECISIONS #79). It keeps its name and its whole re-export
# surface, and every module inside it must resolve from the **installed** package too — the same
# F12 guarantee the src/ layout bought, extended one level down.
SUBMODULES = [
    "api",
    "api.app",
    "api.context",
    "api.declare",
    "api.governance_cache",
    "api.models",
    "api.perimeter",
    "api.routes_admin",
    "api.routes_audit",
    "api.routes_auth",
    "api.routes_events",
    "api.routes_governance",
    "api.routes_operate",
    "api.routes_read",
    "api.routes_promotion",
    "api.routes_scorer",
    "api.routes_static",
    "crosscutting",
    "crosscutting.audit",
    "crosscutting.auth",
    "engine.correlate",
    "engine.correlate.correlate",
    "engine.correlate.learn",
    "engine.correlate.preview",
    "engine.correlate.rootcause",
    "engine.correlate.scorer_contract",
    "engine.correlate.scoring",
    "engine.correlate.severity",
    "engine.correlate.varbind_accum",
    "engine.correlate.varbind_profile",
    "events",
    "known_oids",
    "engine",
    "engine.operate",
    "engine.operate.engine",
    "engine.operate.engine_base",
    "engine.operate.gaps",
    "engine.operate.maintenance",
    "engine.operate.scorer_lifecycle",
    "crosscutting.logsetup",
    "main",
    # v0.7.4: `rbac` became a package (DECISIONS #96). `tables.py` is the single source of
    # authority; `__init__.py` re-exports it by identity, never by copy.
    "crosscutting.rbac",
    "crosscutting.rbac.policy",
    "crosscutting.rbac.tables",
    "receiver",
    "runner",
    "crosscutting.runtime",
    "crosscutting.settings",
    # v0.7.4: `shaping` became a package (DECISIONS #95), on the same terms `api` and `store` did —
    # it keeps its name and its whole re-export surface, and every module inside it must resolve
    # from the **installed** package too.
    "crosscutting.shaping",
    "crosscutting.shaping.fields",
    "crosscutting.shaping.project",
    "crosscutting.shaping.scope",
    # v0.7.3: `store` became a package (DECISIONS #88), on the same terms `api` did in v0.7.2 — it
    # keeps its name and its whole re-export surface, and every module inside it must resolve from
    # the **installed** package too, which is the F12 guarantee extended one level down.
    "store",
    "store.alarms",
    "store.audit_log",
    "store.auth",
    "store.base",
    "store.dataset",
    "store.devices",
    "store.entities",
    "store.feedback",
    "store.governance",
    "store.ingest_gaps",
    "store.learned",
    "store.lifecycle",
    "store.promotion",
    "store.read_models",
    "store.retention",
    "store.scoring_config",
    # v0.9.0: shadow mode's SQL. A separate module from `store.dataset` because that file is at
    # 395 of its 400-line budget and the seam is real — `dataset` owns what capture wrote, this
    # owns what the challenger read and wrote back.
    "store.seal",
    "store.shadow",
    "store.situations",
    "store.state_clears",
    "store.types",
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
    assert (REPO_ROOT / "docs" / "adr" / "README.md").is_file()


def test_the_deleted_record_directories_have_not_returned() -> None:
    """v0.15.0 deleted the per-release record (DECISIONS #197). The convention it instituted is
    that a release writes none of it, so the directories coming back is the visible symptom of the
    convention lapsing — and `docs/record.md` is what a reader is sent to instead."""
    for gone in ("gates", "scope", "releases", "architecture"):
        assert not (REPO_ROOT / "docs" / gone).exists(), (
            f"docs/{gone}/ is back. A release writes no gate document, no scope document, no build "
            "report and no security review — DECISIONS #197. Forward specifications go in "
            "docs/plans/; findings go in docs/findings.md."
        )
    assert (REPO_ROOT / "docs" / "record.md").is_file()


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
# Directories that never hold this repository's own documentation. **Names, and names are the
# problem**: v0.9.2 found that `.venv` here is a literal, so a virtualenv created under any other
# name — `env`, `venv`, a CI cache — put every third-party `README.md` through the broken-link
# checker below. The list is kept for the non-virtualenv cases and is no longer the only defence.
_SKIP_DIRS = {".git", ".venv", "node_modules", "build", "dist", "__pycache__", ".hypothesis"}

# The other half, and the one that removes the whole class: a virtualenv is identified by the
# `pyvenv.cfg` every one of them has and no hand-written directory does. Name-independent, so the
# guard cannot silently stop guarding because somebody called their environment something else.
_VENV_MARKER = "pyvenv.cfg"


def _venv_roots(roots: list[Path]) -> list[Path]:
    """Every virtualenv under `roots`, found by its marker file rather than by its name."""
    return [cfg.parent for root in roots for cfg in root.rglob(_VENV_MARKER)]


def _is_inside_venv(path: Path, roots: list[Path]) -> bool:
    return any(path.is_relative_to(venv) for venv in _venv_roots(roots))


def _strip_code(text: str) -> str:
    """Remove fenced blocks and inline code spans — Markdown does not render links inside them,
    so a ``[text](target)`` written as documentation-about-links must not be treated as a link.
    A real link whose *text* is code (``[`SECURITY.md`](SECURITY.md)``) survives: stripping the
    inner code span leaves ``[](SECURITY.md)``, still a valid link with the real target."""
    return _INLINE_CODE.sub("", _FENCED.sub("", text))


def _markdown_files_under(root: Path) -> list[Path]:
    """Every Markdown file under `root` that this repository is responsible for.

    Parameterised on the root so the guard can be **driven over a fixture** rather than only over
    the repository it happens to live in. `tests/test_guard_scope.py` does exactly that: a first
    version of that test called `_is_inside_venv` directly and stayed green when this walk was
    reverted to the name list, because the helper still existed and nothing called it.
    """
    venvs = _venv_roots([root])
    return [
        p
        for p in root.rglob("*.md")
        if not any(part in _SKIP_DIRS or part.endswith(".egg-info") for part in p.parts)
        and not any(p.is_relative_to(venv) for venv in venvs)
    ]


def _markdown_files() -> list[Path]:
    return _markdown_files_under(REPO_ROOT)


# --- the one exemption, and why it cannot be widened by hand ------------------------------
#
# v0.15.0 deleted the per-release record and the drafts for shipped releases (DECISIONS #197,
# #198). Three of the four pre-registered analysis plans link into what went: `PREREGISTRATION-
# 0.9.0.md` names the gate that recorded its SHA-256, and the v0.10.0 and v0.11.0 plans name the
# specification drafts they were written against.
#
# **Those links cannot be repaired**, and the reason is the whole point of the files: editing one by
# a single byte changes its hash and turns `tests/test_preregistration.py` red. A plan is immutable
# by construction — that is what makes "the standard of evidence was fixed before the results" a
# checkable claim rather than a promise — so a link inside one is a reference to the tree as it was,
# not a broken link. `docs/record.md` is where a reader is sent to resolve it.
#
# The exemption is **derived from the guard that makes those files immutable**, never listed here.
# A name cannot be added to it by editing this module; a name can only join it by having its hash
# pinned in `test_preregistration.PLANS`, which is a deliberate and visible act. That is what stops
# this becoming the "skip list that quietly grew" every link checker eventually acquires. The
# targets are bounded too: only the directories this release removed, so an immutable document
# cannot dangle at an arbitrary path.
_HISTORICAL_PREFIXES = (
    "docs/gates/",
    "docs/scope/",
    "docs/releases/",
    "docs/architecture/",
    "docs/security/SECURITY-",
)


def _immutable_documents() -> frozenset[Path]:
    """The documents this repository may not edit, taken from the guard that pins them."""
    import test_preregistration

    return frozenset(plan.path.resolve() for plan in test_preregistration.PLANS)


def _is_historical_reference(source: Path, resolved: Path) -> bool:
    """A link from an immutable document into a directory v0.15.0 deleted."""
    if source.resolve() not in _immutable_documents():
        return False
    try:
        relative = resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return False
    return relative.startswith(_HISTORICAL_PREFIXES)


def test_markdown_files_discovered() -> None:
    assert _markdown_files(), "no Markdown files found — link check would be vacuous"


def test_the_immutable_exemption_is_derived_from_the_hash_guard() -> None:
    """The exemption's membership is not a list in this file, and must never become one.

    `test_preregistration.PLANS` is what makes these four documents uneditable. Keying the
    exemption on it means a document can only become exempt by having its SHA-256 pinned — so
    "this link may dangle" and "this file may not change" are the same fact, stated once.
    """
    import test_preregistration

    exempt = _immutable_documents()
    assert len(exempt) == 4, f"expected the four pinned plans, got {sorted(exempt)}"
    assert exempt == {plan.path.resolve() for plan in test_preregistration.PLANS}
    for path in exempt:
        assert path.is_relative_to(REPO_ROOT / "docs" / "analysis")


def test_the_immutable_exemption_covers_exactly_the_links_it_should() -> None:
    """An exemption nothing uses is dead code that widens silently; one that covers too much is a
    hole. Both are checked by counting what it actually forgives on this tree."""
    forgiven = [
        (md, target)
        for md in _markdown_files()
        for target in _LINK.findall(_strip_code(md.read_text(encoding="utf-8")))
        if not target.strip().startswith(("http://", "https://", "mailto:", "#"))
        and (path_part := target.strip().split("#", 1)[0].split("?", 1)[0])
        and not (md.parent / path_part).resolve().exists()
        and _is_historical_reference(md, (md.parent / path_part).resolve())
    ]
    assert len(forgiven) == 4, f"the exemption forgives {len(forgiven)} links, not 4: {forgiven}"
    assert {(md.name, target) for md, target in forgiven} == {
        ("PREREGISTRATION-0.9.0.md", "../gates/v0.9.0-phase-1.md"),
        ("PREREGISTRATION-0.10.0.md", "../architecture/HONEST-JUDGE-0.10-DRAFT.md"),
        ("PREREGISTRATION-0.10.0.md", "../architecture/EVIDENCE-BOUNDARY-0.9.2.md"),
        ("PREREGISTRATION-0.11.0.md", "../architecture/CHAMPION-CHALLENGER-0.11-DRAFT.md"),
    }
    # The v0.14.0 plan forgives nothing: it links to no removed document, so the exemption is not
    # something every plan simply receives.
    assert not any(md.name == "PREREGISTRATION-0.14.0.md" for md, _ in forgiven)


def test_a_dangling_link_in_a_mutable_document_is_still_broken() -> None:
    """**The control.** The exemption must not forgive the same link in a file that could be fixed.

    Same target, same shape, a document that is not hash-pinned — which must NOT be forgiven, or
    the exemption is keyed on the target rather than on the immutability that justifies it.
    """
    mutable = REPO_ROOT / "docs" / "record.md"
    immutable = next(iter(_immutable_documents()))
    target = (REPO_ROOT / "docs" / "gates" / "v0.9.0-phase-1.md").resolve()
    assert not target.exists(), "the fixture target must be a path that really is gone"
    assert _is_historical_reference(immutable, target)
    assert not _is_historical_reference(mutable, target)
    # …and an immutable document may not dangle at just anything, only at the deleted record.
    assert not _is_historical_reference(immutable, (REPO_ROOT / "docs" / "invented.md").resolve())


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
            if resolved.exists() or _is_historical_reference(md, resolved):
                continue
            broken.append(f"{md.relative_to(REPO_ROOT)} -> {target}")
    assert not broken, "broken relative Markdown links:\n  " + "\n  ".join(broken)


def test_the_api_package_holds_exactly_the_expected_modules() -> None:
    """The v0.7.2 package is a decided shape, not an accident (MODULE-ARCHITECTURE.md §3).

    A new module here must be added to `SUBMODULES` (so it is proved to resolve from the installed
    package) and to `apisource.MODULE_ORDER` (so the source-scanning guards keep covering it).
    Failing here is the reminder.
    """
    import netcorenoc.api

    pkg = Path(netcorenoc.api.__file__).resolve().parent
    found = sorted(p.stem for p in pkg.glob("*.py") if p.stem != "__init__")
    expected = sorted(m.split(".", 1)[1] for m in SUBMODULES if m.startswith("api."))
    assert found == expected, f"api package contents changed: {found}"


def test_the_store_package_holds_exactly_the_expected_modules() -> None:
    """The v0.7.3 package is a decided shape too (MODULE-ARCHITECTURE.md §6).

    The sibling of the `api` check above, and it exists for the same reason: a module added here
    must also be added to `SUBMODULES`, so it is proved to resolve from the **installed** package
    rather than only from the source tree. `_all.py`, the transitional holder the split moved
    through, must not survive — its presence would mean a section never left it.
    """
    import netcorenoc.store

    pkg = Path(netcorenoc.store.__file__).resolve().parent
    found = sorted(p.stem for p in pkg.glob("*.py") if p.stem != "__init__")
    expected = sorted(m.split(".", 1)[1] for m in SUBMODULES if m.startswith("store."))
    assert found == expected, f"store package contents changed: {found}"
    assert "_all" not in found, "the transitional store/_all.py must be deleted, not shipped"
