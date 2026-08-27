"""The documentation-consistency guard (v0.7.4, §6.5 / DECISIONS #94).

Until this release the repository stated **both** that v0.8.0 was customer-supplied models and that
v0.8.0 was the operator-feedback dataset — four lines apart in `docs/ROADMAP.md`, and again across
`SCORER-PLUGINS-0.8-DRAFT.md` and `MODULE-ARCHITECTURE.md` §10.4. Nothing caught it, because nothing
was looking: the existing documentation guards check that links resolve
(`test_structure.py::test_no_broken_relative_markdown_links`) and that one specific claim is present
(`test_governance.py::test_f32_scoping_is_not_tenant_isolation_is_documented`). Neither can notice
two documents answering the same question differently.

This module is the third of that family — same shape, same place. It asserts that **the repository
states exactly one answer to "what is release X"**.

Three mechanisms, and the difference between them matters:

1. **One machine-readable source of truth.** `docs/plans/releases.md`'s release table. Everything
   else is checked *against* it; it is never inferred from the documents.
2. **An explicit claim form**, so a claim is detectable without reading English. A document that
   asserts what a release *is* carries `<!-- release-claim: vX.Y.Z = key -->`; a document that tags
   a spec *element* for a release keeps the existing `vX.Y.Z: planned` convention. The two are
   different jobs — `plans/cartridge-onnx.md` tags twenty elements and makes one claim — and
   conflating them is what would make the guard either noisy or vacuous. Both are documented in
   `docs/README.md`.
3. **A narrow, enumerated check for the specific contradiction that happened.** See
   `test_the_recorded_contradiction_is_gone` — and read its comment before extending it, because it
   is deliberately *not* the general guarantee.

## Live documents and historical ones

`DECISIONS.md` records what was decided when it was decided; an entry saying what v0.6.0 believed is
not a claim about what is true today, and rewriting it to agree with today would be falsifying a
record. So the guard reads **live, forward-looking documents** and excludes it.

That exclusion is the guard's biggest risk — widened far enough it would check nothing — so
`test_the_historical_exclusion_is_exactly_the_record_taxonomy` pins it.

**v0.15.0 (DECISIONS #199)**: the exclusion used to name four directories and a filename prefix,
because `scope/`, `gates/`, `releases/` and the per-release security reviews were also records. They
are deleted — the record is at `3ecf237`, and `docs/record.md` says so — and the exclusion is one
name. The release table moved with them, from `architecture/ROADMAP-0.8-TO-0.13.md` to
`plans/releases.md` (DECISIONS #203).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "docs"
# v0.15.0 renamed this (DECISIONS #203). It was `architecture/ROADMAP-0.8-TO-0.13.md`, a
# filename that had misstated its own range since DECISIONS #170 moved the chain past v0.13.0
# — a ROADMAP line for three releases. `docs/plans/releases.md` cannot go stale that way.
RELEASE_TABLE_DOC = DOCS / "plans" / "releases.md"

# The roadmap table governs v0.8.0 onward. A release below this is either shipped or is a v0.7.x
# patch specified elsewhere (v0.7.5), and the table says nothing about it.
GOVERNED_FROM = (0, 8, 0)

# --- what counts as a live document ------------------------------------------------------

# Directories that hold the **record** of what was decided at a point in time. They are never
# rewritten to agree with a later decision, so they are not scanned for claims.
#
# **v0.15.0 shrank this from four names to one** (DECISIONS #199), in the same commit that deleted
# what the other three named — `scope/`, `gates/`, `releases/` and the per-release security reviews,
# which also retires the `HISTORICAL_FILE_PREFIXES` half. The record they held is at `3ecf237` and
# `docs/record.md` says so.
#
# What is left is the decision log, excluded for the original reason and no other: an entry that
# recorded what v0.6.0 believed is not a claim about what is true today.
#
# The exclusion was this guard's biggest risk when it named four directories and a filename prefix.
# It is a much smaller one naming a single directory, and the test below pins it at exactly that.
HISTORICAL_DIRS = frozenset({"adr"})


def _is_historical(path: Path) -> bool:
    return any(part in HISTORICAL_DIRS for part in path.relative_to(REPO_ROOT).parts)


def live_docs() -> list[Path]:
    """Every forward-looking Markdown document: `docs/` minus the record taxonomy, plus the two
    root documents a stranger reads first."""
    out = [p for p in sorted(DOCS.rglob("*.md")) if not _is_historical(p)]
    out += [REPO_ROOT / "README.md", REPO_ROOT / "CONTRIBUTING.md"]
    return [p for p in out if p.is_file()]


# --- the single source of truth ----------------------------------------------------------

_TABLE_ROW = re.compile(
    r"^\|\s*\*\*(?P<release>v\d+\.\d+\.\d+)\*\*\s*\|"
    r"\s*(?P<theme>.+?)\s*\|"
    r"\s*`(?P<claim>[a-z0-9-]+)`\s*\|\s*$"
)


def release_table() -> dict[str, tuple[str, str]]:
    """`{release: (theme, claim_key)}` parsed from `docs/plans/releases.md`'s release table."""
    out: dict[str, tuple[str, str]] = {}
    for line in RELEASE_TABLE_DOC.read_text(encoding="utf-8").splitlines():
        match = _TABLE_ROW.match(line)
        if match:
            out[match["release"]] = (match["theme"], match["claim"])
    return out


def _version(release: str) -> tuple[int, ...]:
    return tuple(int(part) for part in release.lstrip("v").split("."))


# --- the two claim forms -----------------------------------------------------------------

# What a release IS. One marked line, parseable, invisible in rendered Markdown so the prose it
# formalises stays readable.
_RELEASE_CLAIM = re.compile(r"<!--\s*release-claim:\s*(v\d+\.\d+\.\d+)\s*=\s*([a-z0-9-]+)\s*-->")

# What a spec ELEMENT is planned for. The pre-existing convention, kept: `v0.8.0: planned` inside
# backticks or bold. Used ~25 times across the drafts.
_ELEMENT_TAG = re.compile(r"(v\d+\.\d+\.\d+):\s*planned")


def _blank(raw: str, pattern: str) -> str:
    """Blank every match of `pattern` length-for-length, keeping newlines.

    Every character after a blanked region keeps its real line number, so a match found later can
    still be reported at a true file:line.
    """
    return re.sub(
        pattern,
        lambda m: "".join("\n" if c == "\n" else " " for c in m[0]),
        raw,
        flags=re.DOTALL,
    )


def source_of(path: Path) -> str:
    """A document's prose: **fenced code blocks removed, inline code spans kept.**

    `docs/README.md` **documents** both claim forms, so it contains a fenced example of a
    `<!-- release-claim: ... -->` marker and a fenced example of a `vX.Y.Z: planned` tag. Parsing
    those as real claims would make the index appear to specify v0.8.0 — which is exactly the
    failure `test_structure.py::_strip_code` already guards against for links ("a ``[text](target)``
    written as documentation-about-links must not be treated as a link"). Same precedent, same fix.
    A fenced block is a worked example and is not a claim about anything.

    **v0.7.5: inline code spans are NOT stripped, and the reason this docstring used to give for
    stripping them was the wrong way round.** It said the strip was what made "the convention" work
    — that the marked forms were reserved for live assertions, so a historical mention written in
    backticks did not register. The convention is the opposite: `docs/README.md` specifies the
    **backticked** form as *the* way to tag an element, and every draft in the repository writes it
    that way. So the strip did not filter historical mentions; it filtered the live ones, and it was
    the only thing standing between `_ELEMENT_TAG` and the tags it exists to check.

    Measured on the v0.7.4 tree (`docs/gates/v0.7.5-phase-0.md` §3.1): **15 of 48 tags visible,
    31%**, with five of the eight tag-carrying documents entirely invisible — including
    `FEEDBACK-DATASET-0.8-DRAFT.md`, the v0.8.0 specification, and `SCORER-PLUGINS-0.13-DRAFT.md`,
    the half-finished supersession that `test_a_documents_element_tags_match_its_own_release_claim`
    names as its own motivating example. A stray `` `v0.8.0: planned` `` left in that document left
    the whole suite green. The guard written to stop the repository contradicting itself four lines
    apart contained that contradiction, four lines apart. DECISIONS #100.

    `_normalised` has always kept inline code for its own reasons, stated in its docstring; the two
    functions now agree, which is the second thing that was wrong here.
    """
    return _blank(path.read_text(encoding="utf-8"), r"```.*?```")


def _normalised(path: Path) -> tuple[str, list[int]]:
    """(text with emphasis stripped and whitespace collapsed, source line of each character).

    The same normalisation `test_governance.py::test_f32_scoping_is_not_tenant_isolation_is_
    documented` uses, and for the same reason: a claim that happens to be line-wrapped, or bolded
    in the middle, is still the claim. Matching raw lines would have missed
    `ROADMAP.md`'s *"a **prerequisite** for v0.8.0's / customer-supplied code"* — which wraps
    across two lines — and that is one of the contradictions this guard exists to catch.

    Only **fenced** blocks are removed here, not inline code spans — unlike `source_of`. The
    phrases this feeds are filenames and sentence fragments, and a filename is almost always
    written in backticks; stripping inline code would blind the check to exactly the references it
    exists to find. A fenced block is a worked example and is not a claim about anything.

    The parallel line list is what lets a match still be reported at a real file:line.
    """
    raw = _blank(path.read_text(encoding="utf-8"), r"```.*?```")
    # HTML comments are machinery, not prose: the release-claim markers live in them, and a marker
    # is checked by `_claims` against the table rather than scanned for phrasing. Blank them out
    # length-for-length so every following character keeps its real line number.
    raw = _blank(raw, r"<!--.*?-->")
    out: list[str] = []
    lines: list[int] = []
    line = 1
    pending_space = False
    i = 0
    while i < len(raw):
        char = raw[i]
        if char == "\n":
            line += 1
            pending_space = True
            i += 1
            continue
        if char.isspace():
            pending_space = True
            i += 1
            continue
        if char in "*_`":
            i += 1
            continue
        if char == "<" and (close := raw.find(">", i)) != -1 and close - i < 20:
            i = close + 1
            continue
        if pending_space and out:
            out.append(" ")
            lines.append(line)
        pending_space = False
        out.append(char)
        lines.append(line)
        i += 1
    return "".join(out), lines


def _claims(path: Path) -> list[tuple[str, str, int]]:
    return [
        (m[1], m[2], n)
        for n, line in enumerate(source_of(path).splitlines(), 1)
        for m in _RELEASE_CLAIM.finditer(line)
    ]


def _element_tags(path: Path) -> list[tuple[str, int]]:
    return [
        (m[1], n)
        for n, line in enumerate(source_of(path).splitlines(), 1)
        for m in _ELEMENT_TAG.finditer(line)
    ]


# --- guarding the guard ------------------------------------------------------------------


def test_the_release_table_parses() -> None:
    """A table this test could not read would make every assertion below vacuous — which is
    exactly the failure mode `apisource.py` was written for in v0.7.2."""
    table = release_table()
    # v0.12.0 resequenced the chain (DECISIONS #170): archetypes moved out of v0.12.0 to v0.15.0 on
    # the measurement that a corpus which cannot decide one comparison cannot decide `k` of them,
    # and the UI work took v0.12.0 and v0.13.0. The membership is pinned EXACTLY, so a later
    # release cannot quietly add a row or retire one; moving it is a deliberate edit with an ADR
    # beside it, which is what this line has just been.
    #
    # v0.14.0 resequenced it again (DECISIONS #184), on DECISIONS #183: the roadmap's claim that a
    # tree ensemble "can only enter through the ONNX door" drew a packaging conclusion from a
    # premise about packages, and three tree kinds now run in process with no new dependency. The
    # model family took v0.14.0; the cartridge and archetypes each moved one place. Nine rows.
    #
    # v0.15.0 resequenced it a third time (DECISIONS #202), on the plainest possible ground: the
    # table said v0.15.0 was the external cartridge, and v0.15.0 is the documentation restructure. A
    # table that disagrees with the release it governs is the exact defect this module exists to
    # catch, so it was caught here. The cartridge moved to v0.16.0 and archetypes to v0.17.0;
    # v0.15.1, v0.15.2 and v0.15.3 joined as a real series with briefs, not as patch numbers.
    # Thirteen rows.
    assert len(table) == 13, f"expected v0.8.0…v0.17.0, parsed {sorted(table)}"
    assert set(table) == {
        "v0.8.0",
        "v0.9.0",
        "v0.10.0",
        "v0.11.0",
        "v0.12.0",
        "v0.13.0",
        "v0.14.0",
        "v0.15.0",
        "v0.15.1",
        "v0.15.2",
        "v0.15.3",
        "v0.16.0",
        "v0.17.0",
    }
    claims = [claim for _theme, claim in table.values()]
    assert len(set(claims)) == len(claims), f"two releases share a claim key: {claims}"
    for release, (theme, claim) in table.items():
        assert theme.strip(), release
        assert claim.strip(), release


def test_live_documents_are_discovered() -> None:
    """A glob that matched nothing, or that swallowed the whole repository, would both be wrong.

    The two named documents are the ones whose disappearance would make the guard vacuous without
    making it fail: the release table itself, and a forward specification that carries claims. Until
    v0.15.0 the second was `MODULE-ARCHITECTURE.md`, which described what is *built* — it moved to
    `docs/architecture.md` and the drafts moved to `docs/plans/` (DECISIONS #198, #203).
    """
    docs = live_docs()
    assert len(docs) >= 8, [str(p) for p in docs]
    names = {p.name for p in docs}
    assert "ROADMAP.md" in names, "the roadmap is a live document and must be scanned"
    assert "releases.md" in names, "the release table itself must be among the documents scanned"
    assert "cartridge.md" in names, "the forward specifications must be scanned for claims"
    assert not any(_is_historical(p) for p in docs)


def test_the_historical_exclusion_is_exactly_the_record_taxonomy() -> None:
    """**The guard's biggest risk, pinned.**

    Every directory excluded here must be one that holds a *record* — something written at a point
    in time and never rewritten. Widening this set is how this guard would quietly stop guarding
    anything, so the set is asserted rather than merely defined.

    **v0.15.0 narrowed it to one name** (DECISIONS #199), which makes this assertion stronger rather
    than weaker: there is exactly one excluded thing, and it is the one whose exclusion was never in
    doubt. `plans/` in particular must never join it — that is where the forward specifications
    live, and a specification is a claim.

    The three names that left did so because their directories left with them. **An exclusion whose
    directory no longer exists is the most dangerous kind**: it excludes nothing today and would
    silently start excluding again the day somebody recreated the path.
    """
    assert set(HISTORICAL_DIRS) == {"adr"}
    for gone in ("scope", "gates", "releases"):
        assert gone not in HISTORICAL_DIRS, f"{gone}/ is deleted; its exclusion went with it"
        assert not (DOCS / gone).exists(), f"docs/{gone}/ is back, and nothing decided that"
    assert "plans" not in HISTORICAL_DIRS
    assert not _is_historical(DOCS / "ROADMAP.md")
    assert not _is_historical(DOCS / "findings.md")
    assert not _is_historical(DOCS / "record.md")
    assert not _is_historical(DOCS / "plans" / "releases.md")
    assert not _is_historical(DOCS / "analysis" / "PREREGISTRATION-0.9.0.md")
    assert _is_historical(DOCS / "adr" / "DECISIONS.md")


def test_every_release_in_the_table_is_claimed_by_the_roadmap_document() -> None:
    """A table nothing claims against is decoration. The roadmap document's own per-release
    sections are the claims, so the table and the prose beneath it cannot drift apart."""
    table = release_table()
    claimed = {release for release, _key, _n in _claims(RELEASE_TABLE_DOC)}
    missing = sorted(set(table) - claimed)
    assert not missing, (
        f"{RELEASE_TABLE_DOC.name} lists {missing} in its table but has no "
        "`<!-- release-claim: ... -->` section for them"
    )


# --- the rule ----------------------------------------------------------------------------


def test_every_release_claim_agrees_with_the_roadmap_table() -> None:
    """**The guard.** Exactly one answer to "what is release X", across every live document."""
    table = release_table()
    disagreements: list[str] = []
    for path in live_docs():
        for release, key, line in _claims(path):
            relative = path.relative_to(REPO_ROOT)
            if release not in table:
                disagreements.append(
                    f"{relative}:{line} claims {release}, which the roadmap table does not name"
                )
            elif table[release][1] != key:
                disagreements.append(
                    f"{relative}:{line} claims {release} = {key!r}, "
                    f"but the roadmap table says {table[release][1]!r}"
                )
    assert not disagreements, (
        "release claims that disagree with docs/plans/releases.md:\n  "
        + "\n  ".join(disagreements)
        + "\n\nThe table is the single source of truth. Change it there, with an ADR, and let the "
        "documents follow — never the other way round."
    )


def test_no_element_is_tagged_for_a_release_the_roadmap_does_not_name() -> None:
    """A superseded draft must not quietly keep its old tag.

    `SCORER-PLUGINS-0.13-DRAFT.md` was written as the v0.8.0 specification and retagged by v0.7.4
    (DECISIONS #93). Twenty-odd `vX.Y.Z: planned` tags moved with it. If one had been missed it
    would still name a real release, so the check below is not enough on its own — see
    `test_a_documents_element_tags_match_its_own_release_claim`, which is the half that catches it.
    """
    table = release_table()
    unknown: list[str] = []
    for path in live_docs():
        for release, line in _element_tags(path):
            if _version(release) >= GOVERNED_FROM and release not in table:
                unknown.append(
                    f"{path.relative_to(REPO_ROOT)}:{line} tags an element `{release}: planned`, "
                    "which the roadmap table does not name"
                )
    assert not unknown, "element tag(s) naming an ungoverned release:\n  " + "\n  ".join(unknown)


def test_a_documents_element_tags_match_its_own_release_claim() -> None:
    """A spec that says which release it specifies may not tag elements for a different one.

    This is what actually catches a half-finished supersession: a draft retagged to v0.13.0 that
    still carries a stray `v0.8.0: planned` names a release the table *does* name, so the check
    above passes it. Binding the tags to the document's own claim does not.

    Only documents making exactly one governed claim are checked — `plans/releases.md` makes one
    per row by design, and a document making none is tagging elements without asserting a theme,
    which is the pre-existing convention and not this test's business.
    """
    table = release_table()
    mismatches: list[str] = []
    for path in live_docs():
        governed = {r for r, _k, _n in _claims(path) if r in table}
        if len(governed) != 1:
            continue
        owner = governed.pop()
        for release, line in _element_tags(path):
            if _version(release) >= GOVERNED_FROM and release != owner:
                mismatches.append(
                    f"{path.relative_to(REPO_ROOT)}:{line} tags `{release}: planned` "
                    f"in a document that claims to specify {owner}"
                )
    assert not mismatches, (
        "element tag(s) inconsistent with the document's own release claim:\n  "
        + "\n  ".join(mismatches)
        + "\n\nA supersession retags every element, or it is not a supersession."
    )


# --- the narrow half, and it is honest about being narrow --------------------------------

# The checks above catch the general case *going forward*: a claim is a marked line, and a marked
# line that disagrees with the table fails. They would NOT have caught what actually happened here.
#
# `docs/ROADMAP.md` carried the contradiction in **ordinary prose**, in a deferred-items list —
# `- **Customer-supplied models → v0.8.0**` — with no tag, no marker and nothing to parse. A general
# guard against that would have to decide whether an arbitrary English sentence asserts a release
# theme, which is not something a test can do honestly.
#
# So this half is **enumeration, not construction**, and it is deliberately narrow: it prevents
# *this* contradiction returning, and nothing more. Do not read this list as the guarantee — the
# guarantee is the claim-form checks above, and this is the belt to their braces.
#
# **Provenance.** Every phrase below is one the repository actually carried, enumerated with its
# file and line on the untouched v0.7.3 tree in `docs/gates/v0.7.4-phase-0.md` §6.1. That document
# was deleted in v0.15.0 (DECISIONS #197) and is permanent at `3ecf237`:
#
#     git show 3ecf237:docs/gates/v0.7.4-phase-0.md
#
# Adding an entry here means finding it in the history the same way. An invented phrasing is a green
# line of coverage over nothing.
FORBIDDEN_PHRASINGS: tuple[tuple[str, str], ...] = (
    (
        "Customer-supplied models → v0.8.0",
        "customer models are v0.13.0 (DECISIONS #93); this is the exact string ROADMAP.md carried "
        "twice while also saying v0.8.0 was the feedback dataset",
    ),
    (
        "v0.8.0's customer-supplied code",
        "same claim, phrased as a possessive — the v0.7.0 preemption-harness line used it",
    ),
    (
        "Customer-supplied models — v0.8.0",
        "the repo-map.md phrasing of the same claim",
    ),
    (
        "SCORER-PLUGINS-0.8-DRAFT.md",
        "the draft was git mv-d to SCORER-PLUGINS-0.13-DRAFT.md; a surviving reference to the old "
        "name is a link to a file that no longer exists and a claim that v0.8.0 is the models "
        "release",
    ),
    (
        "specified for v0.8.0 in",
        "the EXTENSIBILITY-0.6 phrasing pointing at the models draft",
    ),
    (
        "a v0.8.0 prerequisite",
        "generalised per-link attribution is a prerequisite for the *models* release, which is "
        "v0.13.0 — ROADMAP.md attached it to v0.8.0",
    ),
    (
        "Customer-supplied scorers — v0.8.0 draft",
        "the title of SCORER-PLUGINS-0.8-DRAFT.md before the supersession",
    ),
    (
        "how v0.8.0 will let an operator run a customer-supplied model",
        "that draft's opening sentence; line-wrapped and bolded in the source, which is why the "
        "match runs over normalised text",
    ),
    (
        "v0.8.0's customer models plug into",
        "the EXTENSIBILITY-0.6 correction note",
    ),
    (
        "v0.8.0's richer scorers",
        "DESIGN.md attaching richer scorers to v0.8.0",
    ),
    (
        "a named v0.8.0 task",
        "DESIGN.md attaching generalised persisted attribution to v0.8.0",
    ),
)


def test_the_recorded_contradiction_is_gone() -> None:
    """The narrow half: the literal strings Phase 0 enumerated may not reappear.

    Matched over **normalised** text — emphasis stripped, whitespace collapsed — because several of
    the real occurrences are bolded mid-phrase or wrapped across two lines, and a raw line scan
    silently missed them the first time this guard was run (see `docs/gates/v0.7.4-phase-1.md` §3).

    Read the comment above this test before adding to the list, and before mistaking it for the
    complete guarantee. It is not; it is the specific-case belt.
    """
    found: list[str] = []
    for path in live_docs():
        text, lines = _normalised(path)
        for phrase, why in FORBIDDEN_PHRASINGS:
            start = text.find(phrase)
            while start != -1:
                found.append(f"{path.relative_to(REPO_ROOT)}:{lines[start]}: {phrase!r} — {why}")
                start = text.find(phrase, start + 1)
    assert not found, (
        "documentation still carries the v0.8.0 contradiction DECISIONS #93 resolved:\n  "
        + "\n  ".join(found)
        + "\n\nv0.8.0 is the operator-feedback dataset. Customer-supplied models are v0.13.0."
    )


# --- the guard's failure mode, demonstrated rather than asserted (v0.7.5) ----------------
#
# A guard whose failure mode has never been demonstrated is not a guard. That is the second time in
# two releases this has been the thing the guard actually needed: v0.7.4 added the element-tag check
# and never watched it go red, so nobody noticed that `source_of` blanked the very form the check
# was written to find. 31% of the tags were invisible for a whole release.
#
# These two tests inject the forgotten tag into a synthetic document and drive the **real** guard
# function over it — not a re-implementation of its logic, which would be free to agree with itself.


def _drive_guard_over(doc: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the guard at one synthetic document and nothing else.

    `REPO_ROOT` moves with `live_docs` because the guard reports a mismatch as
    `path.relative_to(REPO_ROOT)`. `RELEASE_TABLE_DOC` is deliberately **not** redirected: the
    release table stays the real one, so these tests check the real claim keys.
    """
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "live_docs", lambda: [doc])
    monkeypatch.setattr(module, "REPO_ROOT", doc.parent)


def _injected_draft(tmp_path: Path, tag: str) -> Path:
    """A document shaped like `SCORER-PLUGINS-0.13-DRAFT.md`: one claim, one stray element tag."""
    doc = tmp_path / "STRAY-0.13-DRAFT.md"
    doc.write_text(
        "# A draft retagged to v0.13.0, with one heading missed\n\n"
        "<!-- release-claim: v0.13.0 = external-cartridge -->\n\n"
        "## 1. The blessed ONNX path (`v0.13.0: planned`)\n\n"
        "Body.\n\n"
        f"## 2. A stray heading left by a half-finished supersession ({tag})\n\n"
        "Body.\n",
        encoding="utf-8",
    )
    return doc


def test_the_element_tag_check_goes_red_on_a_backticked_stray_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The demonstration this guard was missing, and the regression it hides.**

    The stray tag is written `` `v0.8.0: planned` `` — **inside backticks**, which is what
    `docs/README.md` specifies and what every draft in this repository actually writes. Until
    v0.7.5 `source_of` blanked inline code spans, so this exact document left the suite **green**
    (`docs/gates/v0.7.5-phase-0.md` §3.2 and `docs/gates/v0.7.5-phase-3.md` §1 record the run).

    If you are here because this test failed after you changed `source_of`, the question to ask is
    not "how do I make it pass" but "does the guard still see the form the convention writes".

    The backticks are the whole point. **Do not remove them to make a future change pass** — a
    version of this test with a bare tag asserts only what v0.7.4 already asserted, and would have
    been green throughout the entire period the guard was blind.
    """
    doc = _injected_draft(tmp_path, "`v0.8.0: planned`")
    assert "`v0.8.0: planned`" in doc.read_text(encoding="utf-8"), (
        "the injected tag must be backticked — that is the form the guard used to miss"
    )
    _drive_guard_over(doc, monkeypatch)

    with pytest.raises(AssertionError) as excinfo:
        test_a_documents_element_tags_match_its_own_release_claim()
    assert "v0.8.0: planned" in str(excinfo.value)
    assert "v0.13.0" in str(excinfo.value)


def test_a_consistently_tagged_document_stays_green(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control. A check that goes red on everything is not a check.

    Same document, same backticks, tag matching the claim — which must pass, or the test above is
    detecting the injection rather than the inconsistency.
    """
    doc = _injected_draft(tmp_path, "`v0.13.0: planned`")
    _drive_guard_over(doc, monkeypatch)
    test_a_documents_element_tags_match_its_own_release_claim()  # must not raise


def test_element_tags_inside_fenced_blocks_are_still_excluded(tmp_path: Path) -> None:
    """Dropping the inline-code strip must not drop the **fenced** strip with it.

    `docs/README.md` documents the tag convention by showing it, inside a fence. That example is
    not a claim about anything, and treating it as one would make the index appear to specify a
    release — the failure `test_structure.py::_strip_code` guards against for links. Exactly one
    tag in the repository is in this position; this test is what keeps it excluded.
    """
    doc = tmp_path / "DOCUMENTING-THE-CONVENTION.md"
    doc.write_text(
        "Write an element tag like this:\n\n"
        "```\n"
        "## 1. The blessed ONNX path (`v0.13.0: planned`)\n"
        "```\n\n"
        "…and a real one here (`v0.8.0: planned`).\n",
        encoding="utf-8",
    )
    tags = [release for release, _line in _element_tags(doc)]
    assert tags == ["v0.8.0"], (
        f"the fenced example must stay excluded and the prose tag must be seen; saw {tags}"
    )


@pytest.mark.parametrize("phrase,_why", FORBIDDEN_PHRASINGS)
def test_the_forbidden_phrase_check_goes_red_on_each_phrase(
    phrase: str, _why: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Guard the guard, and v0.15.0 changed which property it guards.**

    Until v0.15.0 this test asserted *provenance*: every entry must be a string the repository
    actually carried, matched against `docs/gates/v0.7.4-phase-0.md` §6.1, which enumerated each
    phrase with the file and line it came from on the untouched v0.7.3 tree. That document is
    deleted (DECISIONS #197). It is at `3ecf237` and one command reads it:

        git show 3ecf237:docs/gates/v0.7.4-phase-0.md

    **Re-copying the enumeration into a live document to keep the assertion alive would rebuild the
    pile this release is dismantling**, and hashing a constant against itself would be theatre.
    So the property changes to the one that matters more and is self-contained: **each phrase, put
    into a document, actually turns the check red.**

    That catches strictly more than provenance did. An invented phrasing was the risk provenance
    addressed; a phrase the check *cannot see* — mis-normalised, wrapped, stripped by `_normalised`
    — is the risk that actually bit this module, twice: v0.7.4 shipped an element-tag check that
    `source_of` blanked, and the phrase half was found spelling-sensitive (`->` versus `→`) a
    release later. A phrase that never fires is a green line of coverage over nothing, which is the
    same failure provenance was guarding against, arriving from the other direction.

    The provenance claim itself survives as a comment above `FORBIDDEN_PHRASINGS`, resolvable in one
    command by anyone who doubts it.
    """
    doc = tmp_path / "INJECTED.md"
    doc.write_text(f"# A document that carries the contradiction\n\nSomething {phrase} else.\n")
    _drive_guard_over(doc, monkeypatch)
    with pytest.raises(AssertionError) as excinfo:
        test_the_recorded_contradiction_is_gone()
    assert phrase in str(excinfo.value)


def test_a_document_carrying_none_of_the_phrases_stays_green(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control. A check that goes red on every document is not a check.

    Same shape, same driver, prose that says something adjacent and correct — v0.8.0 *is* the
    feedback dataset — which must pass, or the test above is detecting the injection rather than
    the phrase.
    """
    doc = tmp_path / "CLEAN.md"
    doc.write_text(
        "# A document that says the right thing\n\n"
        "v0.8.0 is the operator-feedback dataset, and customer-supplied models are v0.16.0.\n"
    )
    _drive_guard_over(doc, monkeypatch)
    test_the_recorded_contradiction_is_gone()  # must not raise
