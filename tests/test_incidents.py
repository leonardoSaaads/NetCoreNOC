"""Incident identity (v0.10.0, Workstream 1): the chain, the two guards, and the one implementation.

**The corpus cannot demonstrate any of this**, and that is the reason the file is shaped as it is.
`docs/gates/v0.10.0-phase-1.md` §5.1 measured the fullest corpus this repository can construct: four
merge edges, every chain exactly **one hop**, zero cycles. `COALESCE(merged_into, situation_id)` and
a transitive resolution both return 37 incidents on it, so quoting "37 = 37" would be evidence of
nothing.

So the semantics are proved on purpose-built fixtures — exactly as v0.9.1 had to prove its exclusion
set — and every test that claims a difference carries the one-hop answer beside the transitive one,
so a resolution that silently stopped following the chain fails here rather than in a report nobody
re-reads.
"""

from __future__ import annotations

import ast
from itertools import pairwise
from pathlib import Path

from netcorenoc.engine.dataset import census
from netcorenoc.engine.dataset.incidents import (
    MAX_CHAIN_DEPTH,
    IncidentMap,
    _cycle_members,
    resolve,
    resolve_all,
    stamp,
)
from netcorenoc.store import Store

import util

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG = REPO_ROOT / "src" / "netcorenoc"

# 201 -> 202 -> 203 -> 204. One hop reports three incidents; there is one.
CHAIN = {201: 202, 202: 203, 203: 204}
CHAIN_BAGS = [201, 202, 203, 204]


# --- the chain -------------------------------------------------------------------------------


def test_an_unmerged_situation_is_its_own_incident() -> None:
    """The common case, and the one that must stay cheap."""
    assert resolve(7, {}).incident == 7
    assert not resolve(7, {}).cycle


def test_one_hop_is_resolved() -> None:
    """The case `COALESCE` already got right. **A control**: if this failed, every difference
    measured below would be the resolution being broken rather than the chain being followed."""
    assert resolve(1, {1: 2}).incident == 2


def test_the_chain_is_followed_to_a_fixed_point() -> None:
    """**The claim.** Three hops resolve to one incident, and one hop does not."""
    resolved = {resolve(bag, CHAIN).incident for bag in CHAIN_BAGS}
    one_hop = {CHAIN.get(bag, bag) for bag in CHAIN_BAGS}
    assert resolved == {204}, f"the chain did not reach its fixed point: {resolved}"
    assert one_hop == {202, 203, 204}, "the fixture must separate the two, or it proves nothing"
    assert len(one_hop) == 3 and len(resolved) == 1, (
        "one hop reports three incidents where there is one — the difference this module exists for"
    )


def test_every_resolution_of_a_sound_chain_meets_neither_guard() -> None:
    outcomes = [resolve(bag, CHAIN) for bag in CHAIN_BAGS]
    assert not any(o.cycle or o.unterminated for o in outcomes)


# --- the cycle guard -------------------------------------------------------------------------


def test_a_cycle_terminates_and_is_flagged() -> None:
    """A cycle must be caught, not looped on forever. The schema forbids none."""
    cycle = {301: 302, 302: 303, 303: 301}
    outcome = resolve(301, cycle)
    assert outcome.cycle, "the cycle was not flagged"
    assert not outcome.unterminated, "a cycle is not a depth-bound failure and must not say it is"


def test_every_entry_point_into_one_cycle_gets_the_same_incident() -> None:
    """**The property that makes the cycle handling correct rather than merely safe.**

    Resolving to "wherever the walk stopped" would give three different incidents for three bags in
    one cycle — silently *inflating* the incident count, which is the exact error the transitive
    resolution exists to remove, re-entering through the failure path.
    """
    cycle = {301: 302, 302: 303, 303: 301}
    assert {resolve(entry, cycle).incident for entry in (301, 302, 303)} == {301}


def test_a_two_node_cycle_is_a_cycle() -> None:
    """`a -> b -> a` is the smallest one, and the one a one-hop reader cannot see at all."""
    outcome = resolve(1, {1: 2, 2: 1})
    assert outcome.cycle and outcome.incident == 1


# --- F50: the minimum is over the CYCLE, not over the walk --------------------------------------
#
# The four tests below exist because the test above them passes on a fixture where every entry point
# is already a cycle member, so `min(the walk) == min(the cycle)` by coincidence of the fixture. A
# TAIL leading into the cycle is what separates the two, and until v0.10.1 nothing exercised one.


def test_a_tail_below_the_cycle_minimum_does_not_become_its_own_incident() -> None:
    """**F50.** `min` over the walk includes the tail; `min` over the cycle does not.

    `{1: 7, 7: 8, 8: 7}` is one incident: situation 1 merged into a cycle of 7 and 8. Resolving to
    `min(seen)` gives situation 1 the answer `1` — because the walk `1 -> 7 -> 8` contains 1 — while
    7 and 8 both answer `7`. **Two incidents where there is one**, which is precisely the inflation
    the transitive resolution exists to remove, re-entering through the failure path the docstring
    says it closes.
    """
    tailed = {1: 7, 7: 8, 8: 7}
    mapping = resolve_all([1, 7, 8], tailed)
    assert mapping.incidents == 1, (
        f"the tail was assigned its own incident: {dict(mapping.incident_of)}"
    )
    assert {resolve(entry, tailed).incident for entry in (1, 7, 8)} == {7}
    assert mapping.cycles == frozenset({1, 7, 8}), "every walk that reaches the cycle is flagged"


def test_a_tail_above_the_cycle_minimum_resolves_to_the_cycle_minimum_too() -> None:
    """**CONTROL**, and it does two jobs the test above cannot do alone.

    The tail sits **above** every cycle member, so `min(the walk)` and `min(the cycle)` agree and
    **this test passes on the defective code too** — which is what makes it a control rather than a
    second probe: it has to hold in the red run and the green one.

    It also pins **which** incident, not merely that there is one. A repair returning `max(cycle)`
    would answer `8` everywhere, report one incident, and satisfy the F50 test above — one incident
    is still one incident. The assertion on `incident_of[99]` is what rejects it.
    """
    tailed = {99: 7, 7: 8, 8: 7}
    mapping = resolve_all([7, 8, 99], tailed)
    assert mapping.incidents == 1
    assert mapping.incident_of[99] == 7, "the tail must join the cycle's incident, not keep its own"
    assert set(mapping.incident_of.values()) == {7}, (
        "the cycle's minimum is 7; resolving to max(cycle) would say 8 and still report one "
        "incident"
    )


def test_two_separate_tails_into_one_cycle_agree_with_each_other() -> None:
    """The property in its general form: **which** door you came through must not matter.

    One tail below the cycle minimum and one above it. Under `min(seen)` the two walks contain
    different ids, so the two tails answer differently — from each other as well as from the cycle.
    """
    tailed = {1: 7, 99: 7, 7: 8, 8: 7}
    mapping = resolve_all([1, 7, 8, 99], tailed)
    assert mapping.incident_of[1] == mapping.incident_of[99] == 7
    assert mapping.incidents == 1, (
        f"three incidents where there is one: {dict(mapping.incident_of)}"
    )


def test_a_self_merge_is_a_one_node_cycle() -> None:
    """`a -> a`. The degenerate cycle, and the one whose member walk terminates immediately."""
    outcome = resolve(7, {7: 7})
    assert outcome.cycle and outcome.incident == 7


def test_the_cycle_walk_terminates_even_when_its_precondition_is_violated() -> None:
    """**Found by the mutation ledger, and it hung rather than failing.**

    `_cycle_members` is called with the **re-visited** node, which is on the cycle by construction,
    and its `while node != entry` loop terminates because of that and nothing else. Seeding the
    obvious one-token mistake — passing the walk's *start* instead — makes the walk enter the cycle
    and never come back to a tail that is not on it. The mutant did not fail; the process stopped
    responding, and a ten-minute harness timeout was the only thing that noticed.

    A module whose entire subject is merge chains the schema does not forbid should not contain a
    walk whose only stopping condition is an invariant. Bounded by `MAX_CHAIN_DEPTH`, like
    :func:`resolve` itself, and returning what it has rather than raising — a wrong number is
    visible where a hung process is a support ticket.

    Called directly because **no input to `resolve` can reach this state**: the caller always passes
    a node on the cycle. That is the honest reason for a private-function test, and stating it is
    better than inventing a public path that does not exist.
    """
    tail_not_on_the_cycle = 1
    outcome = _cycle_members(tail_not_on_the_cycle, {1: 7, 7: 8, 8: 7})
    assert outcome, "the bounded walk must return what it collected rather than nothing"
    assert 1 in outcome, "the entry it was given is always a member of what it returns"


def test_the_bound_is_never_reached_on_a_real_cycle() -> None:
    """**CONTROL for the bound.** A bound that fired early would silently truncate a long cycle and
    resolve it to the minimum of a *prefix* — a wrong incident, quietly."""
    # A cycle of EXACTLY MAX_CHAIN_DEPTH members: 0 -> 1 -> … -> 63 -> 0. The walk needs exactly
    # MAX_CHAIN_DEPTH iterations to come back to 0, so this sits on the bound rather than near it.
    at_the_bound = {i: (i + 1) % MAX_CHAIN_DEPTH for i in range(MAX_CHAIN_DEPTH)}
    members = _cycle_members(0, at_the_bound)
    assert len(members) == MAX_CHAIN_DEPTH, f"the cycle was truncated to {len(members)}"
    assert min(members) == 0
    # And the whole resolution agrees: every entry into it answers 0, not a prefix minimum.
    assert {resolve(entry, at_the_bound).incident for entry in (0, 17, 63)} == {0}


# --- the depth guard -------------------------------------------------------------------------


def test_a_chain_longer_than_the_bound_is_flagged_as_unterminated_and_not_as_a_cycle() -> None:
    """The two guards detect two different defects and must not be collapsed into one.

    A long acyclic chain is `unterminated`; a short cyclic one is `cycle`. A reader has to be able
    to tell "these situations point at each other" from "this chain is implausibly long".
    """
    long_chain = {i: i + 1 for i in range(MAX_CHAIN_DEPTH + 5)}
    outcome = resolve(0, long_chain)
    assert outcome.unterminated, "the depth bound did not fire"
    assert not outcome.cycle, "an acyclic chain was reported as a cycle"


def test_a_chain_exactly_at_the_bound_still_resolves() -> None:
    """**CONTROL for the depth guard.** A bound that fired one step early would make every long
    chain look corrupt, and the test above would pass for the wrong reason."""
    exact = {i: i + 1 for i in range(MAX_CHAIN_DEPTH - 1)}
    outcome = resolve(0, exact)
    assert not (outcome.cycle or outcome.unterminated), "a chain within the bound was rejected"
    assert outcome.incident == MAX_CHAIN_DEPTH - 1


# --- the map ---------------------------------------------------------------------------------


def test_the_map_carries_both_answers_and_their_difference() -> None:
    """The census prints the reduction, not only the result — including when it is zero."""
    mapping = resolve_all(CHAIN_BAGS, CHAIN)
    assert mapping.incidents == 1
    assert mapping.incidents_one_hop == 3
    assert mapping.incidents_one_hop - mapping.incidents == 2
    assert not mapping.unsound_situations


def test_a_corpus_with_no_merges_reduces_by_zero() -> None:
    """**The corpus's own case, and a control.** Zero is a real answer; a `reduction` that could
    never be zero would be reporting an artefact of the code."""
    mapping = resolve_all([1, 2, 3], {})
    assert mapping.incidents == mapping.incidents_one_hop == 3


def test_a_one_hop_corpus_reduces_by_zero_between_the_two_resolutions() -> None:
    """The measured shape of this project's corpus: four one-hop merges, and the two resolutions
    agree. Pinned so that a later corpus growing a real chain is visible as a change."""
    edges = {29: 28, 30: 28, 31: 28, 33: 32}
    mapping = resolve_all(sorted({28, 29, 30, 31, 32, 33}), edges)
    assert mapping.incidents == mapping.incidents_one_hop == 2
    assert not mapping.unsound_situations


def test_the_map_reports_both_unsound_conditions_separately() -> None:
    edges = {1: 2, 2: 1}
    edges.update({i: i + 1 for i in range(100, 100 + MAX_CHAIN_DEPTH + 5)})
    mapping = resolve_all([1, 100, 500], edges)
    assert mapping.cycles == frozenset({1})
    assert mapping.unterminated == frozenset({100})
    assert mapping.unsound_situations == frozenset({1, 100})


def test_stamp_writes_the_incident_and_nothing_else_does() -> None:
    rows = [{"situation_id": 201}, {"situation_id": 204}]
    stamp(rows, resolve_all(CHAIN_BAGS, CHAIN))
    assert [row["incident"] for row in rows] == [204, 204]


def test_stamping_a_row_whose_situation_was_not_resolved_falls_back_to_itself() -> None:
    """A row read after the edges were snapshotted must not raise."""
    rows = [{"situation_id": 999}]
    stamp(rows, resolve_all([1], {}))
    assert rows[0]["incident"] == 999


# --- the single implementation, asserted structurally ------------------------------------------


def test_the_store_no_longer_decides_what_an_incident_is() -> None:
    """**The structural guard.** `store/shadow.py` computed identity twice, in SQL, with a
    `COALESCE`. It now returns edges and the arithmetic happens once, in `incidents.py`.

    Text rather than AST because the thing being asserted is the *content of a SQL string*, which no
    parse of the Python tree can reach — and the project's own rule is to use the tool that can see
    the thing (Appendix B: a grep once reported `engine.py` importing `netcorenoc.api`, and it was
    the docstring saying it never must). So the assertion is narrowed to the two SQL statements'
    select lists rather than the file, and `merge_edges`' own docstring mentions `COALESCE` freely.
    """
    source = (PKG / "store" / "shadow.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    joins = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name in ("labelled_pairs", "labelled_bags")
    ]
    assert len(joins) == 2, "the two training joins were renamed; this guard is now vacuous"
    for node in joins:
        literals = " ".join(
            sub.value
            for sub in ast.walk(node)
            if isinstance(sub, ast.Constant)
            if isinstance(sub.value, str)
        )
        assert "merged_into" not in literals, (
            f"{node.name} computes incident identity in SQL again. One hop is not incident "
            "identity, and a second implementation is how the estimator and the seal come to "
            "disagree about which incidents exist with nothing going red."
        )
        assert "AS incident" not in literals


def test_both_consumers_resolve_identity_through_the_one_function() -> None:
    """The two call sites the plan's §3.3 warns must not compute identity separately.

    Asserted by parsing rather than by reading: `shadow.Shadow.train` — the slow loop that fits
    the challenger — and `shadow_report.collect` must each contain a call to `resolve_identity`.
    """
    for module, function in (("shadow.py", "train"), ("shadow_report.py", "collect")):
        tree = ast.parse(util.module_path(module).read_text(encoding="utf-8"))
        target = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == function
        ]
        assert target, f"{module}::{function} not found; this guard is vacuous"
        called = {
            node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            for node in ast.walk(target[0])
            if isinstance(node, ast.Call)
        }
        assert "resolve_identity" in called, (
            f"{module}::{function} no longer resolves incident identity through the one "
            "implementation"
        )


# --- end to end, through the store -------------------------------------------------------------


async def test_a_real_merge_chain_resolves_through_the_store(store: Store) -> None:
    """The whole path: real `situation` rows, real `merge_edges`, one resolution.

    Four situations chained by three merges. Every consumer must see **one** incident; a one-hop
    reader sees three.
    """
    async with store.lock:
        sids = [await store.create_situation(1_000.0 + i, None) for i in range(4)]
        for source, destination in pairwise(sids):
            await store.conn.execute(
                "UPDATE situation SET merged_into = ?, status = 'merged' WHERE id = ?",
                (destination, source),
            )
        await store.commit()

    edges = await store.merge_edges()
    assert edges == {sids[0]: sids[1], sids[1]: sids[2], sids[2]: sids[3]}

    mapping = resolve_all(sids, edges)
    assert mapping.incidents == 1, "the store's chain did not resolve to one incident"
    assert mapping.incidents_one_hop == 3, "the fixture must separate the two"

    rows = [{"situation_id": sid} for sid in sids]
    stamp(rows, mapping)
    assert {row["incident"] for row in rows} == {sids[3]}


async def test_resolve_identity_stamps_bags_and_pairs_from_one_map(store: Store) -> None:
    """Bags and pairs are stamped from the **same** map, so they cannot disagree."""
    async with store.lock:
        a = await store.create_situation(1_000.0, None)
        b = await store.create_situation(1_001.0, None)
        await store.conn.execute(
            "UPDATE situation SET merged_into = ?, status = 'merged' WHERE id = ?", (b, a)
        )
        await store.commit()
    bags = [{"situation_id": a}]
    pairs = [{"situation_id": a}, {"situation_id": b}]
    identity = await census.resolve_identity(store, bags, pairs)
    assert bags[0]["incident"] == b
    assert {row["incident"] for row in pairs} == {b}
    assert isinstance(identity, IncidentMap)


async def test_pre_v080_merges_are_counted_rather_than_assumed_absent(store: Store) -> None:
    """§3.3. A situation merged before `0008` carries `status='merged'` and **no destination**, so
    it looks independent and is not — and no column distinguishes it from one that is."""
    async with store.lock:
        orphan = await store.create_situation(1_000.0, None)
        keeper = await store.create_situation(1_001.0, None)
        merged = await store.create_situation(1_002.0, None)
        await store.conn.execute("UPDATE situation SET status = 'merged' WHERE id = ?", (orphan,))
        await store.conn.execute(
            "UPDATE situation SET merged_into = ?, status = 'merged' WHERE id = ?",
            (keeper, merged),
        )
        await store.commit()
    assert await store.pre_v080_merges() == 1, "the destination-less merge was not counted"
    # CONTROL: a merge WITH a destination is recoverable and must not be counted as unrecoverable.
    assert merged in await store.merge_edges()


# --- B2: the expression is forbidden, not merely fixed where it was found -----------------------
#
# v0.10.0 fixed two of the four consumers and NAMED the other two; v0.10.1 fixed those two. Fixing
# instances closes instances. This closes the CLASS: no module may express incident identity in SQL
# at all, so the fifth consumer cannot be written rather than having to be found.
#
# `ast`, not `grep`, and Appendix B says why: a grep once reported `engine.py` importing
# `netcorenoc.api` when it was the docstring saying it never must. **Six modules name this exact
# expression in prose right now** — `bias.py`, `census.py` (twice), `store/shadow.py`,
# `incidents.py` (twice), `shadow_render.py` and `agreement_bags.py` — so a scan that could not tell
# a SQL literal from a sentence about one would report the whole package guilty and be switched off.


def _coalesced_identity_literals(source: str) -> list[str]:
    """Every **non-docstring** string constant in ``source`` that computes identity with a COALESCE.

    The extractor both guards and vacuity-checks, and it must be the same function for the second to
    say anything about the first — a vacuity check against a different implementation proves that
    *some* extractor works.

    Docstrings are collected first and subtracted by object identity rather than filtered by
    keyword: `incidents.py`'s own module docstring quotes the expression while being the module that
    exists to replace it. `#` comments never reach the tree at all, which is why three of the six
    prose mentions are invisible here for free.
    """
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings:
            continue
        text = node.value.upper()
        if "COALESCE" in text and "MERGED_INTO" in text:
            found.append(node.value)
    return found


# A file where the expression is KNOWN to exist, in both forms. The docstring copy is the half that
# makes this a discrimination test rather than only a smoke test.
_VACUITY_FIXTURE = '''
"""A module docstring that names COALESCE(s.merged_into, f.situation_id) in prose."""

# A comment naming COALESCE(s.merged_into, f.situation_id), which never reaches the AST.

QUERY = """
SELECT COALESCE(s.merged_into, f.situation_id) AS incident FROM feedback f
"""


def f() -> None:
    """Another docstring quoting COALESCE(merged_into, situation_id)."""
    return None
'''


def test_the_expression_extractor_finds_the_expression_where_it_is_known_to_exist() -> None:
    """**The vacuity check.** Without it, an extractor broken in any way reports every module clean.

    This is the failure mode the guard below cannot detect about itself: `assert not offenders`
    passes just as happily when `offenders` is empty because nothing was scanned, because the parse
    silently returned nothing, or because the substring test was inverted.
    """
    found = _coalesced_identity_literals(_VACUITY_FIXTURE)
    assert len(found) == 1, f"the extractor must find exactly the SQL literal, not prose: {found}"
    assert "AS incident" in found[0], found[0]


def test_no_module_computes_incident_identity_in_sql() -> None:
    """**The class, closed.** `COALESCE(<anything>merged_into<anything>)` exists in no module's SQL.

    Not "the four known consumers are fixed" — *the expression cannot be written*. A fifth consumer
    would have to add it and would fail here, which is the difference between a defect that was
    repaired and a defect that cannot recur.

    Why this matters more than it looks: on this corpus all four consumers agree at 37 incidents,
    because every merge chain in it is exactly one hop. A corpus with a longer chain makes them
    disagree with **nothing going red** — and two of the four are the estimator and the seal, so the
    seal would be reserving a different set from the one the estimator excluded. A guarantee whose
    failure is invisible has to be structural.
    """
    offenders = [
        f"{path.relative_to(PKG)}: {literal.strip()[:70]}"
        for path in sorted(PKG.rglob("*.py"))
        for literal in _coalesced_identity_literals(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        "incident identity is computed in SQL again:\n  "
        + "\n  ".join(offenders)
        + "\n\nOne hop is not incident identity. Read the merge EDGES and resolve them through "
        "netcorenoc.incidents.resolve_all, as store/shadow.py, census.py, bias.py and "
        "agreement_bags.py all do."
    )


def test_all_four_consumers_resolve_identity_through_the_one_implementation() -> None:
    """**CONTROL for the guard above**, and the half it structurally cannot check.

    A package where every consumer had simply *stopped counting incidents* would pass
    `test_no_module_computes_incident_identity_in_sql` perfectly. This asserts the other side: each
    of the four still resolves identity, and does it by calling into `netcorenoc.incidents`.
    """
    consumers = {
        "census.py": ("resolve_all", "stamp"),
        "bias.py": ("resolve_all",),
        "agreement_bags.py": ("resolve_all", "stamp"),
        "store/shadow.py": ("merge_edges",),
    }
    for module, expected in consumers.items():
        # `store/shadow.py` is named by path: it is the SQL half, in a package with its own
        # naming space, and `util.module_path` deliberately does not reach into it.
        path = (PKG / module) if "/" in module else util.module_path(module)
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {
            node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        }
        if module == "store/shadow.py":
            # The store does not resolve; it EXPOSES the edges and refuses to decide. Asserted by
            # the method existing, because that is the whole of its contribution.
            assert any(
                isinstance(n, ast.AsyncFunctionDef) and n.name == "merge_edges"
                for n in ast.walk(tree)
            ), "store/shadow.py no longer exposes the merge edges"
            continue
        assert set(expected) <= names, (
            f"{module} no longer resolves incident identity through netcorenoc.incidents; "
            f"found calls {sorted(names)}"
        )


# --- the three survivors of the v0.10.1 mutation ledger, closed ---------------------------------
#
# A8, A9 and A10 survived because **the frozen corpora cannot demonstrate the properties**: the bias
# fixture has 4 situations and 4 labelled ones (zero unlabelled), and neither the bias nor the
# agreement fixture contains a single merge edge. Measured, not assumed:
#
#     bias fixture:      situations=4    labelled=4   unlabelled=0   merge edges=0
#     agreement fixture: situations=12   labelled=12  unlabelled=0   merge edges=0
#
# That is this file's own opening sentence arriving in three new places — *the corpus cannot
# demonstrate any of this* — and the answer is the same one v0.9.1 used for its exclusion set:
# purpose-built fixtures, here, rather than a change to a byte-frozen corpus. **A test's fixture is
# part of the guard.**


async def test_the_bias_incident_count_is_over_labelled_situations_only(store: Store) -> None:
    """**Ledger A8.** `bias._incident_map` resolving *every* situation survived every test.

    `distinct_incidents` is the effective sample size of the **labelled** corpus — it is `n` for
    every floor expressed in incidents — so an unlabelled situation must not enter it. The frozen
    fixture has none, so the report is blind to the difference; this fixture has one.
    """
    from netcorenoc.engine.report import bias

    async with store.lock:
        labelled = await store.create_situation(1_000.0, None)
        unlabelled = await store.create_situation(1_001.0, None)
        await store.add_feedback(labelled, "confirm", 1_002.0)
        await store.commit()
    mapping = await bias._incident_map(store)
    assert mapping.incidents == 1, (
        f"the unlabelled situation {unlabelled} entered the labelled corpus's incident count: "
        f"{dict(mapping.incident_of)}"
    )
    assert set(mapping.incident_of) == {labelled}


async def test_agreement_bags_follow_a_real_merge_chain(store: Store) -> None:
    """**Ledger A9.** `agreement_bags.load_bags` resolving against an EMPTY edge map survived.

    Both frozen corpora contain **zero merge edges**, so `resolve_all(ids, {})` and
    `resolve_all(ids, edges)` return the same thing and the byte-frozen report cannot tell them
    apart. Two hops here, so a one-hop reader and an edge-less reader both fail.
    """
    from netcorenoc.engine.report.agreement_bags import load_bags

    async with store.lock:
        a = await store.create_situation(1_000.0, None)
        b = await store.create_situation(1_001.0, None)
        c = await store.create_situation(1_002.0, None)
        for source, destination in ((a, b), (b, c)):
            await store.conn.execute(
                "UPDATE situation SET merged_into = ?, status = 'merged' WHERE id = ?",
                (destination, source),
            )
        for sid in (a, b, c):
            await store.add_feedback(sid, "confirm", 1_010.0)
        await store.commit()

    bags = await load_bags(store)
    assert len(bags) == 3, "the fixture must produce one bag per situation"
    assert {bag.incident for bag in bags} == {c}, (
        f"the two-hop chain did not resolve to one incident: {[b.incident for b in bags]}"
    )
    # CONTROL: one hop would report TWO incidents here, and no edges at all would report three —
    # so the fixture separates all three readings rather than only the first two.
    assert len({b, c}) == 2 and len({a, b, c}) == 3


async def test_the_seal_ordering_uses_the_earliest_label_and_not_the_latest(store: Store) -> None:
    """**Ledger A10.** `census.first_label_per_incident` taking the LATEST label survived.

    `PREREGISTRATION-0.10.0.md` §3.3(2): the seal holds the most recent third of the corpus **by
    when each incident was first labelled**, *earliest rather than latest because a bag relabelled
    today does not make its incident new*.

    **Why no existing fixture could reach this, and it is not simply that none happened to.**
    `store.labelled_bags()` already returns **one row per situation** — the latest verdict, by its
    own sub-select — so `min` and `max` over a single-situation incident are identical *by
    construction of the query*, not by accident of the corpus. The `min` in this function does work
    only when **two or more situations resolve to one incident**, which needs a merge, and neither
    frozen corpus contains a single merge edge.

    So the fixture is two situations merged into one, labelled 4 000 seconds apart.
    """
    async with store.lock:
        early = await store.create_situation(1_000.0, None)
        late = await store.create_situation(1_001.0, None)
        await store.conn.execute(
            "UPDATE situation SET merged_into = ?, status = 'merged' WHERE id = ?", (late, early)
        )
        for sid, at in ((early, 1_100.0), (late, 5_100.0)):
            await store.conn.execute(
                "INSERT INTO feedback (situation_id, verdict, created_at, capture_provenance, "
                "member_count) VALUES (?, 'confirm', ?, 'current', 2)",
                (sid, at),
            )
        await store.commit()

    earliest = await census.first_label_per_incident(store)
    assert earliest == {late: 1_100.0}, (
        f"the seal's ordering took the later label instead of the first: {earliest}. Both "
        f"situations are one incident ({late}); a later bag does not make its incident new."
    )
