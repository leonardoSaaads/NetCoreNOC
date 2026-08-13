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

from netcorenoc import census
from netcorenoc.incidents import MAX_CHAIN_DEPTH, IncidentMap, resolve, resolve_all, stamp
from netcorenoc.store import Store

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
        tree = ast.parse((PKG / module).read_text(encoding="utf-8"))
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
