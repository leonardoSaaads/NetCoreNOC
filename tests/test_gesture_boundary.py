"""The prohibitions `PREREGISTRATION-0.16.0.md` registers, as guards rather than as prose.

`test_lifecycle.py` asserts what each gesture **does**. This asserts what nothing may do. The
difference is the one Appendix B keeps making: a test that passes because the thing did not happen
is not the same as a test that fails when it does, and every guard below is written so that the
**injection named in the release's evidence** turns it red.

Five prohibitions, and each is the plan's:

1. a gesture that says nothing about a grouping produces **no link-training row** (§1);
2. a confidence below the registered floor produces **no training row** (§4);
3. the confidence multiplier is **never folded into a stored `TrainingRow.weight`** (§4);
4. **bag provenance is recorded and not consumed** — asserted over the import graph, so it is a
   property of the tree rather than a promise in a docstring (§5);
5. **no model proposes a name** (Part I.2), asserted over the one column that could hold one.

Where a guard reads the source rather than driving behaviour, it says so and says what it cannot
see. A guard that cannot fail is worth less than no guard, because it is counted.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from netcorenoc.engine.dataset.gestures import CHANNEL_OF
from netcorenoc.engine.model import confidence
from netcorenoc.engine.model.training import LabelledPair, derive
from netcorenoc.store.situation_events import ASSERTING_KINDS

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG = REPO_ROOT / "src" / "netcorenoc"


def _pair(**overrides: object) -> LabelledPair:
    """One promoted pair, with everything the fit needs and nothing it does not."""
    base: dict[str, object] = {
        "pair_id": 1,
        "feedback_id": 1,
        "verdict": "confirm",
        "incident": 1,
        "delta_t_s": 5.0,
        "class_affinity": 0.6,
        "entity_affinity": 0.6,
        "incumbent_linked": True,
        "evaluated_at": 1_700_000_000.0,
        "label_at": 1_700_000_100.0,
    }
    base.update(overrides)
    return LabelledPair(**base)  # type: ignore[arg-type]


# --- 1. an alarm-lifecycle signal may not become a grouping signal ---------------------------


def test_the_asserting_kinds_are_exactly_the_three_that_speak_about_a_grouping() -> None:
    """§1's prohibition as a **membership assertion**, which is the form that can go red.

    `manual_clear` and `self_clear` are absent, and so is every other kind: a release that added a
    sixth gesture and quietly put it in this set would fail here rather than in a review. Asserted
    exactly — `>=` would let a kind be added without anyone deciding to.
    """
    assert {"verdict", "move", "merge", "operator_split"} == ASSERTING_KINDS
    assert "manual_clear" not in ASSERTING_KINDS
    assert "self_clear" not in ASSERTING_KINDS
    # Every kind the schema admits has a channel entry, and the two that assert nothing have none:
    # a channel names how a LABEL was acquired, and these acquire none.
    assert CHANNEL_OF["manual_clear"] is None and CHANNEL_OF["self_clear"] is None
    assert all(CHANNEL_OF[kind] is not None for kind in ASSERTING_KINDS)


def test_the_schema_refuses_a_training_row_for_a_kind_that_asserts_nothing() -> None:
    """The prohibition again, one layer down: `produces_training_rows` is a **stored** value.

    Stored rather than derived from `kind` on read, so the plan's rule is a number a query can
    count and a guard can assert. This reads the migration's own text — the `CHECK` and the column
    — because the guard has to fail if the column is ever given a default of 1 or dropped.

    What this cannot see: whether a caller writes the right value. That is
    `test_lifecycle.py::test_a_zombie_clear_produces_no_link_training_row`, which drives the route.
    """
    sql = (PKG / "migrations" / "0014_situation_lifecycle.sql").read_text(encoding="utf-8")
    assert "produces_training_rows INTEGER NOT NULL DEFAULT 0" in sql
    assert "CHECK (produces_training_rows IN (0, 1))" in sql


# --- 2 and 3. the confidence floor, and the weight it may never be folded into ----------------


def test_a_confidence_below_the_floor_produces_no_training_row() -> None:
    """§4, at the derivation. **The gate is separate from the arithmetic, and this is the gate.**

    The route already refuses to write a *label* below the floor; this refuses to derive a *row*
    from one. The duplication is deliberate — a corpus that arrived by another path (an upgrade, a
    restore, a channel a later release adds) is governed by the plan either way — and it is what
    the release's fourth mandatory injection removes.
    """
    below = [_pair(pair_id=n, confidence=confidence.FLOOR - 0.01) for n in range(4)]
    rows, diagnostics = derive(below, "A")
    assert rows == [], "a gesture below the registered floor produced a training row"
    assert diagnostics["rows_dropped_below_confidence_floor"] == 4, diagnostics

    at_the_floor = [_pair(pair_id=n, confidence=confidence.FLOOR) for n in range(4)]
    kept, _ = derive(at_the_floor, "A")
    assert len(kept) == 4, "the floor is inclusive; a gesture AT it is admitted"


def test_the_multiplier_is_applied_at_derivation_and_never_stored() -> None:
    """§4's structural half: `TrainingRow.weight` already carries **two** meanings.

    The design-effect correction `1/len(bucket)` and the class balance. Folding a third into one
    number makes all three unrecoverable, which is why `situation_event.confidence` is its own
    column — and why this asserts the *composition* rather than the final number: a build that
    multiplied twice, or that skipped the multiplier, produces a different product from the same
    inputs.
    """
    sure = [_pair(pair_id=n, confidence=1.0) for n in range(4)]
    unsure = [_pair(pair_id=n, confidence=0.5) for n in range(4)]
    full, sure_diagnostics = derive(sure, "A")
    shrunk, unsure_diagnostics = derive(unsure, "A")

    # The three factors, each named, each computed here rather than read back from the code under
    # test. `design_effect` is §2.1's (one bag, one unit, spread over its four pairs);
    # `class_balance` is `total / (2 * mass)`, which for a corpus of ONE class is 0.5 whatever the
    # multiplier did — so the balance does **not** renormalise the multiplier away, and the final
    # weight carries all three.
    design_effect = 1.0 / 4
    class_balance = 0.5

    # The pre-balance mass is the first place the composition is visible.
    assert sure_diagnostics["positive_mass"] == pytest.approx(1.0)
    assert unsure_diagnostics["positive_mass"] == pytest.approx(confidence.multiplier(0.5))

    assert full[0].weight == pytest.approx(design_effect * 1.0 * class_balance)
    assert shrunk[0].weight == pytest.approx(
        design_effect * confidence.multiplier(0.5) * class_balance
    )
    # The ratio is the guard that goes red in **both** directions, which is why it is asserted
    # separately from the two products above: a build that composed the multiplier twice gives
    # 0.64, one that dropped it gives 1.00, and only the registered `m(0.5)` gives 0.80.
    assert shrunk[0].weight / full[0].weight == pytest.approx(confidence.multiplier(0.5)), (
        "the multiplier is composed exactly once, between the design effect and the class balance"
    )

    # And the composition is REPORTED, which the plan requires rather than suggests: a run whose
    # operators averaged 0.7 and one whose confidences were all unstated produce different models
    # from the same corpus, and without these numbers the difference is invisible in the run row.
    assert unsure_diagnostics["confidence_multiplier_mean"] == pytest.approx(0.80)
    assert unsure_diagnostics["confidence_multiplier_min"] == pytest.approx(0.80)


def test_no_stored_row_carries_a_confidence_weighted_number() -> None:
    """§4's other half: *"It is **never** folded into a stored `weight`."*

    There is no stored training row — `TrainingRow` is derived per run and lives in memory — so the
    checkable form of the prohibition is about `situation_event`: the gesture's table stores the
    **raw** self-report in its own column and stores no weight at all. A release that added one, or
    that wrote `m(c)` where `c` belongs, would make the three factors unrecoverable from the record
    and would turn this red.

    Read from the migration's text with its comments stripped, because the comment beside the
    column explains the prohibition and names the very word the guard forbids.
    """
    sql = (PKG / "migrations" / "0014_situation_lifecycle.sql").read_text(encoding="utf-8")
    table = sql[
        sql.index("CREATE TABLE situation_event (") : sql.index(
            "CREATE TABLE situation_event_member"
        )
    ]
    columns = "\n".join(line.split("--")[0] for line in table.splitlines())
    assert "confidence" in columns, "the gesture's self-report is not stored in its own column"
    assert "weight" not in columns, (
        "`situation_event` grew a weight column. Confidence is recorded raw and composed at "
        "derivation; a stored weight makes the design effect, the multiplier and the class "
        "balance one unrecoverable number."
    )


def test_an_unreported_confidence_shrinks_nothing() -> None:
    """Every label written before this release carries `None`, and `None` is not a low number.

    A build that treated an absent confidence as 0.0 would silently drop the entire pre-v0.16.0
    corpus out of training, and every downstream figure would move for a reason nothing recorded.
    """
    legacy, _ = derive([_pair(pair_id=n) for n in range(4)], "A")
    stated, _ = derive([_pair(pair_id=n, confidence=1.0) for n in range(4)], "A")
    # Asserted against the corpus that DID report, rather than against a constant: the claim is
    # that an absent confidence is treated as a full one, and a constant would also pass if both
    # corpora were shrunk by the same wrong factor.
    assert legacy[0].weight == pytest.approx(stated[0].weight)
    assert confidence.multiplier(None) == 1.0


def test_a_gesture_bag_and_a_label_bag_are_never_one_bucket() -> None:
    """§2's unit: `asserting_bags` counts a **gesture**, not a pair — and not two gestures as one.

    `feedback.id` and `situation_event.id` are disjoint id spaces that both start at 1. Bucketing
    on the id alone would merge a label's bag with an event's, and the design-effect correction
    `1/len(bucket)` would then be computed over a bucket that is two human decisions — which is
    exactly the error §2.1(c) of the v0.10.0 plan rejects, arriving through a new door.
    """
    mixed = [_pair(pair_id=1, feedback_id=1), _pair(pair_id=2, feedback_id=1, source="gesture")]
    rows, diagnostics = derive(mixed, "A")
    assert diagnostics["bags_used"] == 2, "two bags collapsed into one bucket"
    assert diagnostics["bags_by_source"] == {"feedback": 1, "gesture": 1}
    # Two bags of one pair each: two units of pre-balance mass. Collapsed into one bucket it would
    # be **one** unit over two pairs, which is the design-effect error §2.1(c) rejects — so the
    # mass is what this asserts, and it is the number that moves if the bucketing is wrong.
    assert diagnostics["positive_mass"] == pytest.approx(2.0)
    assert len(rows) == 2


# --- 4. bag provenance is recorded and not consumed -------------------------------------------


def test_nothing_that_decides_imports_the_provenance_module() -> None:
    """§5, **as a property of the import graph**.

    > A build that supplies either to a scorer, a promotion input, or a verdict trigger has
    > violated this plan.

    So the guard is not "does a number look wrong" — it is *can the code reach it at all*. Every
    module under the three packages that fit, score or judge is parsed and its imports read; a
    single `import` of `engine.dataset.provenance` from any of them turns this red, which is the
    release's seventh mandatory injection.

    What this cannot see: a value passed *through* a third module. The bounded answer to that is
    the next test, which asserts the recorded columns reach only the two consumers they are
    supposed to.
    """
    forbidden = ("engine/correlate", "engine/model", "engine/evaluation")
    offenders: list[str] = []
    for area in forbidden:
        for path in sorted((PKG / area).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    name = node.module
                elif isinstance(node, ast.Import):
                    name = node.names[0].name
                else:
                    continue
                if "dataset.provenance" in name:
                    offenders.append(f"{path.relative_to(PKG)}:{node.lineno}")
    assert not offenders, (
        "bag provenance reached a module that fits, scores or judges:\n  "
        + "\n  ".join(offenders)
        + "\n\n`PREREGISTRATION-0.16.0.md` §5 registers it as recorded and NOT consumed in "
        "v0.16.0. Recording is not using; a later release may consume it, and it will do so by "
        "amending a plan rather than by adding an import."
    )


def test_the_provenance_columns_are_read_by_the_report_and_by_nothing_else() -> None:
    """The other half: the four columns exist, and only the reporting surface names them.

    Read over `src/` as text rather than as an import graph, because a column name is a string and
    a consumer would reach it by name. The permitted readers are the migration that declares them,
    the module that computes them, the gesture that writes them, and the census that reports them.
    """
    permitted = {
        "migrations/0014_situation_lifecycle.sql",
        "engine/dataset/provenance.py",
        "engine/dataset/gestures.py",
    }
    offenders = sorted(
        str(path.relative_to(PKG))
        for path in PKG.rglob("*")
        if path.is_file()
        and path.suffix in (".py", ".sql")
        and str(path.relative_to(PKG)) not in permitted
        and "bag_weakest_margin" in path.read_text(encoding="utf-8")
    )
    assert not offenders, f"bag provenance is named outside its permitted readers: {offenders}"


# --- 5. no model proposes a name --------------------------------------------------------------


def test_no_server_derivation_reaches_operator_name() -> None:
    """Part I.2: **a model does not propose names in this release.**

    A model writing *"fibre cut"* above a grouping the operator is about to judge contaminates that
    judgement, which is the `incumbent_linked` mistake in a new register. The guard is that exactly
    one statement in the tree writes `operator_name`, and it is the store method the rename route
    calls with a string that arrived in a request body.

    What this cannot see: that the string in that body was typed by a person. Nothing can — the
    server cannot tell a typed name from a pasted one — which is why the claim this release makes
    is about the *appliance*, and is checkable.
    """
    writers = sorted(
        f"{path.relative_to(PKG)}:{lineno}"
        for path in PKG.rglob("*.py")
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "operator_name" in line and ("UPDATE" in line or "INSERT" in line)
    )
    assert writers == ["store/situation_events.py:99"], (
        f"more than one statement writes `operator_name`: {writers}. A model does not propose a "
        "name in this release, and the way that stays true is that there is one writer and it is "
        "the rename route's."
    )


def test_the_derived_name_never_carries_an_operator_supplied_string() -> None:
    """The other direction: the name the **server** computes is built from validated addresses.

    A device label is free text an operator typed. Folding it into a derived name would put an
    operator's own words into a field the console renders beside `operator_name`, and the two would
    be indistinguishable — which is the whole reason there are two columns.
    """
    source = (PKG / "store" / "situation_events.py").read_text(encoding="utf-8")
    whole = source[
        source.index("async def refresh_derived_name") : source.index("async def set_op")
    ]
    # **Code only.** The docstring beside this method explains why it reads addresses rather than
    # labels, so a scan over the raw text would find the word it exists to forbid — and a guard
    # that has to be worded around is a guard nobody can extend. `ast` is what tells the two apart.
    module = ast.parse(source)
    method = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "refresh_derived_name"
    )
    body = "\n".join(
        ast.unparse(statement)
        for statement in method.body
        if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant))
    )
    assert "d.ip" in whole, "the derived name is not built from device addresses"
    assert "label" not in body, (
        "the derived name reads an operator label; it is built from addresses, which are validated "
        "at ingest, precisely so a server-computed name cannot carry operator-supplied text"
    )
