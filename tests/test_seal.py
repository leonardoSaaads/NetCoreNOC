"""The sealed holdout (v0.10.0, Workstream 2): cut once, logged always, and **not spent**.

`PREREGISTRATION-0.10.0.md` §3.3 fixes the construction and §4.3 fixes the protections. The four
properties this file exists to hold, in the order the plan states them:

1. the seal is constructed **once**, and a second construction is **refused, not re-derived**;
2. it is **structurally unreadable** from the estimator — asserted by parsing the tree, not by
   convention;
3. **every** access appends exactly one row, granted or refused;
4. a read **requires a ratified plan already on record**, and v0.10.0's query count is **0**.

Property 4's last clause is the release's headline discipline rather than a footnote, so it is
asserted directly and separately.
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

from netcorenoc import seal
from netcorenoc.store import Store

import util

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG = REPO_ROOT / "src" / "netcorenoc"

PLAN = "c03aef0181554c0c71482e57d03677f25964c3a5ac20a7bf1b1d74bff1ba1e01"
OTHER_PLAN = "0" * 64
TS = 1_700_000_000.0

# Nine incidents, one second apart. Nine // 3 = 3 sealed, 6 kept.
NINE = {100 + i: TS + i for i in range(9)}


async def _sealed(store: Store, first_label_at: dict[int, float] | None = None) -> seal.SealSummary:
    return await seal.construct(
        store,
        release="v0.10.0",
        plan_sha256=PLAN,
        now=TS,
        first_label_at=NINE if first_label_at is None else first_label_at,
    )


# --- the construction --------------------------------------------------------------------------


def test_the_order_is_earliest_label_then_incident_id() -> None:
    """§3.3(2). The tie-break is not a detail.

    This project's own corpus applies 41 verdicts **one second apart**, and collisions are ordinary.
    An order that depended on a stable sort of equal keys would depend on the row order the store
    happened to return — which is not a property of the data.
    """
    tied = {7: TS, 3: TS, 5: TS, 9: TS - 1.0}
    assert seal.plan_ordered_incidents(tied) == [9, 3, 5, 7]


def test_the_sealed_portion_is_the_last_third_and_never_more() -> None:
    """§3.3(3). The KEPT portion rounds up, so the seal never exceeds a third."""
    assert seal.sealed_third(list(range(9))) == [6, 7, 8]
    assert seal.sealed_third(list(range(37))) == list(range(25, 37))  # 12 sealed, 25 kept
    assert len(seal.sealed_third(list(range(10)))) == 3, "10 // 3 == 3, not 4"
    assert seal.sealed_third([]) == []


def test_the_digest_is_over_the_ordered_membership() -> None:
    """A seal holding the same incidents in another order was cut by a different rule."""
    assert seal.digest_of([1, 2, 3]) != seal.digest_of([3, 2, 1])
    assert seal.digest_of([1, 2, 3]) == seal.digest_of([1, 2, 3])


async def test_the_seal_records_what_it_was_cut_from(store: Store) -> None:
    summary = await _sealed(store)
    assert summary.exists
    assert summary.incident_count == 3, "9 // 3 == 3 sealed"
    assert summary.corpus_incidents == 9, "the corpus it was cut from must be recorded"
    assert summary.plan_sha256 == PLAN
    assert summary.digest == seal.digest_of([106, 107, 108])


async def test_a_second_construction_is_refused_and_not_re_derived(store: Store) -> None:
    """**A seal that can be rebuilt is a seal that can be rebuilt AFTER seeing a result.**

    The refusal is the schema's, so it holds against a later method and against anyone with a
    SQLite prompt. The second call here uses a *different, larger* corpus, so a silent re-derivation
    would be visible as a changed membership rather than as a no-op.
    """
    first = await _sealed(store)
    bigger = {200 + i: TS + 100 + i for i in range(30)}
    with pytest.raises(sqlite3.IntegrityError):
        await _sealed(store, bigger)

    after = await seal.summary(store)
    assert after.digest == first.digest, "the seal's membership moved"
    assert after.corpus_incidents == 9, "the seal was re-cut against a different corpus"


# --- structural unreadability -------------------------------------------------------------------


def test_the_estimator_and_the_training_path_cannot_reach_the_seal() -> None:
    """**Property 2, asserted by parsing rather than by reading.**

    The estimator and the training path must be *unable* to read the sealed ids. The seal was built
    in its own phase, **before** the estimator, so the estimator is written against an interface
    that never offered them: reading them is not a rule it must obey but a thing it cannot express
    (DECISIONS #145).

    **The report is deliberately NOT on this list, and that distinction is the whole design.** A
    report must print the seal's *state* — how many incidents, cut when, and above all the query
    count, which §4.3(4) requires beside every holdout number ever published. `seal.summary` returns
    a `SealSummary`, which **cannot carry the membership**: there is no field for it and no method
    that yields it. So the report imports the module and still cannot read the ids, which is a
    stronger guarantee than forbidding the import would be — forbidding it would only have moved the
    query count out of the report.

    What actually protects the membership is the next test: **one** expression returns it, and
    exactly one function calls that expression.

    `ast.parse` rather than `grep`, for the reason the project's own layer test gives: a grep once
    reported `engine.py` importing `netcorenoc.api` and it was the docstring saying it never must.
    """
    for module in ("shadow_cv.py", "training.py", "census.py", "shadow.py"):
        tree = ast.parse(util.module_path(module).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "netcorenoc":
                    imported.update(alias.name for alias in node.names)
                elif node.module.startswith("netcorenoc."):
                    imported.add(node.module.split(".")[1])
            elif isinstance(node, ast.Import):
                imported.update(
                    alias.name.split(".")[1]
                    for alias in node.names
                    if alias.name.startswith("netcorenoc.")
                )
        assert "seal" not in imported, (
            f"{module} imports the seal. The estimator and the training path must be UNABLE to "
            "reach the sealed ids — if this becomes a convention rather than a structure, the "
            "release's central claim is unenforceable."
        )


def test_only_the_access_path_calls_the_one_expression_that_returns_the_membership() -> None:
    """**The guarantee that actually protects the membership.**

    `Store.sealed_incident_ids` is the only expression in the package that returns the sealed ids
    (asserted below), and `seal.spend` is the only thing that calls it. Every other module — the
    reports included — can hold the module, the summary and the query count without any way to
    obtain the list.

    A second caller would be a way to spend the holdout without appending a row, and the query
    count would then be a measurement of one code path rather than of the holdout.
    """
    callers: list[str] = []
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "sealed_incident_ids"
            ):
                callers.append(str(path.relative_to(PKG)))
    assert callers == ["seal.py"], (
        f"the sealed membership is reachable from {callers}; only seal.spend may call it"
    )


def test_the_guard_would_notice_an_import() -> None:
    """**CONTROL for the guard above.** A scan that found nothing would pass for any tree.

    `maintenance.py` legitimately imports the seal — it is what constructs it — so the same parse
    applied there must come back positive. Without this, a broken extractor would report every
    module clean.
    """
    tree = ast.parse(util.module_path("maintenance.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "netcorenoc":
            imported.update(alias.name for alias in node.names)
    assert "seal" in imported, (
        "the import extractor found nothing where an import is known to exist, so the isolation "
        "guard above proves nothing"
    )


def test_the_store_exposes_the_membership_through_exactly_one_expression() -> None:
    """No unguarded `SELECT` over `holdout_seal_member` exists anywhere in the package.

    A second read — a convenience helper, a debug dump, a report shortcut — would be a way to spend
    the holdout without appending a row, and the query count would then be a measurement of one code
    path rather than of the holdout.
    """
    offenders: list[str] = []
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # Docstrings are prose ABOUT the rule and are not reads of the table. Collected first and
        # subtracted, rather than filtered by a keyword: `store/seal.py`'s own docstring names the
        # table, and a scan that could not tell prose from SQL would report it as a second read.
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if id(node) in docstrings:
                continue
            text = node.value.upper()
            if "HOLDOUT_SEAL_MEMBER" in text and "FROM" in text and "INSERT" not in text:
                offenders.append(f"{path.relative_to(PKG)}: {node.value[:60]}")
    assert len(offenders) == 1, (
        f"expected exactly one read of holdout_seal_member; found {len(offenders)}: {offenders}"
    )
    assert offenders[0].startswith("store/seal.py"), offenders


# --- the access log -----------------------------------------------------------------------------


async def test_every_read_appends_exactly_one_row(store: Store) -> None:
    await _sealed(store)
    before = len(await store.access_log())
    await seal.spend(store, release="v0.11.0", plan_sha256=PLAN, purpose="probe", now=TS + 1)
    assert len(await store.access_log()) == before + 1


async def test_a_refused_read_is_logged_too(store: Store) -> None:
    """A log recording only successful reads answers *"how often was it spent"* and not *"how often
    did somebody try"* — and the second is the question a reviewer of a four-release tuning loop
    needs. It is also the only trace a missing-plan refusal leaves anywhere."""
    await _sealed(store)
    access = await seal.spend(
        store, release="v0.11.0", plan_sha256=OTHER_PLAN, purpose="probe", now=TS + 1
    )
    assert not access.granted and access.ids is None
    rows = await store.access_log()
    assert rows[-1]["granted"] == 0
    assert "no ratified pre-registration" in str(rows[-1]["outcome"])


async def test_reading_without_a_ratified_plan_is_refused(store: Store) -> None:
    """§4.3(3). **A release cannot look first and register after.**"""
    await _sealed(store)
    refused = await seal.spend(
        store, release="v0.11.0", plan_sha256=OTHER_PLAN, purpose="evaluate", now=TS + 1
    )
    assert not refused.granted

    await seal.ratify(store, release="v0.11.0", plan_sha256=OTHER_PLAN, now=TS + 2)
    granted = await seal.spend(
        store, release="v0.11.0", plan_sha256=OTHER_PLAN, purpose="evaluate", now=TS + 3
    )
    assert granted.granted, "a registered plan must be able to read"
    assert granted.ids == [106, 107, 108]


async def test_reading_a_seal_that_does_not_exist_is_refused_and_logged(store: Store) -> None:
    """The third refusal, and the one a caller is most likely to meet first."""
    access = await seal.spend(store, release="v0.10.0", plan_sha256=PLAN, purpose="probe", now=TS)
    assert not access.granted
    assert "no seal has been constructed" in access.outcome
    assert len(await store.access_log()) == 1, "a refusal on an unsealed store still logs"


async def test_ratifying_is_not_spending(store: Store) -> None:
    """Registering a plan is not opening the envelope, and the counter must say so."""
    await _sealed(store)
    await seal.ratify(store, release="v0.11.0", plan_sha256=OTHER_PLAN, now=TS + 1)
    assert (await seal.summary(store)).query_count == 0
    assert len(await store.access_log()) == 1, "the ratify row is still recorded"


async def test_the_access_log_is_append_only(store: Store) -> None:
    """A release that could delete its own query count could report 0 having spent the seal."""
    await _sealed(store)
    await seal.spend(store, release="v0.11.0", plan_sha256=PLAN, purpose="probe", now=TS + 1)
    for sql in ("UPDATE holdout_access SET granted = 0", "DELETE FROM holdout_access"):
        with pytest.raises(sqlite3.IntegrityError):
            await store.conn.execute(sql)


# --- the query count ----------------------------------------------------------------------------


async def test_the_summary_reads_no_membership_and_logs_nothing(store: Store) -> None:
    """The report prints the seal's state on every run. A counter that moved when a report ran would
    be counting reports rather than counting the times the holdout was spent."""
    await _sealed(store)
    for _ in range(5):
        summary = await seal.summary(store)
    assert summary.query_count == 0
    assert await store.access_log() == []


async def test_the_query_count_moves_only_on_a_granted_read(store: Store) -> None:
    """**CONTROL for `test_v0100_spends_nothing`.** A counter that could never move would make
    "query count 0" a statement about the code rather than about this release's conduct."""
    await _sealed(store)
    assert (await seal.summary(store)).query_count == 0

    await seal.spend(store, release="x", plan_sha256=OTHER_PLAN, purpose="p", now=TS + 1)
    assert (await seal.summary(store)).query_count == 0, "a REFUSED read is not a query"

    await seal.spend(store, release="x", plan_sha256=PLAN, purpose="p", now=TS + 2)
    summary = await seal.summary(store)
    assert summary.query_count == 1, "a granted read must be counted"
    assert summary.spent


async def test_exactly_one_code_path_can_spend_the_seal_and_it_is_the_promotion_gate(
    store: Store,
) -> None:
    """**v0.10.0's headline discipline, rewritten for the release that makes the seal spendable.**

    v0.10.0 asserted that *no* code path calls `seal.spend`, and that was the whole of its claim.
    **v0.11.0 adds exactly one**, deliberately, and softening the guard to "some code path may"
    would have retired the only mechanical statement anyone had about who can spend the holdout.

    So the assertion is **narrower than v0.10.0's, not looser**: not *"nobody spends"* but
    *"exactly one module spends, and it is the one the plan authorises."* A second call site — a
    report that peeked, a CLI that read the membership to print it — fails here, which is precisely
    the drift the original guard existed to catch.

    Reading and summarising still move nothing: `seal.summary` is called twice below and the count
    stays 0.
    """
    await _sealed(store)
    await seal.summary(store)
    summary = await seal.summary(store)
    assert summary.query_count == 0, "summarising the seal moved the query count"
    assert not summary.spent

    callers = sorted(
        {
            str(path.relative_to(PKG))
            for path in sorted(PKG.rglob("*.py"))
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "spend"
        }
    )
    assert callers == ["promotion.py"], (
        f"the set of modules that can spend the sealed holdout changed: {callers}. Exactly one is "
        "authorised — `netcorenoc.promotion`, at the point PREREGISTRATION-0.11.0.md §3 permits, "
        "after the floors and the power condition have BOTH passed."
    )


def test_the_one_spend_is_guarded_by_the_floors_and_the_power_condition() -> None:
    """**The plan's §3 evaluation order, asserted structurally rather than only behaviourally.**

    `tests/test_promotion_gate.py` proves the seal stays unread when either check fails. This proves
    *why*: the single `seal.spend` call sits inside a branch testing **both** `floors_met` and
    `power.sufficient`. A behavioural test alone would pass against code that happened to short-
    circuit for an unrelated reason, and would go quiet the day that reason changed.
    """
    tree = ast.parse(util.module_path("promotion.py").read_text(encoding="utf-8"))
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    spends = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "spend"
    ]
    assert len(spends) == 1, f"{len(spends)} spend call sites in promotion.py"

    guards: list[str] = []
    walker: ast.AST | None = spends[0]
    while walker is not None:
        walker = parents.get(walker)
        if isinstance(walker, ast.If):
            guards.append(ast.unparse(walker.test))
    assert guards, "the spend is UNGUARDED — it would read the seal on every evaluation"
    condition = " ".join(guards)
    assert "floors_met" in condition, f"the spend is not guarded by the floors: {condition}"
    assert "sufficient" in condition, f"the spend is not guarded by the power: {condition}"
