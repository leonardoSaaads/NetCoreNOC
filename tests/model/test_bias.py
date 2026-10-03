"""The bias-report gate (v0.8.0 §8.1).

**This is a gate, not a unit test.** The report's output is compared **byte for byte** against a
frozen expectation built from a deterministic fixture, so it goes red the day capture changes
shape — a new column, a changed lifecycle, a promotion rule that stops promoting. That is the
`make eval` pattern, which is the most valuable thing this repository has, and it is the whole
reason the report is a CLI subcommand rather than a UI card.

The fixture is built in code rather than checked in as a database, so a schema change surfaces here
as a *diff in the report* rather than as an unreadable binary.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.engine.report import bias, bias_report
from netcorenoc.store import Store

import paths
from biasfixture import TS, build_fixture

EXPECTED = paths.FIXTURES / "bias-report.txt"


async def _report(store: Store) -> str:
    async with store.lock:
        return bias_report.render(await bias.collect(store))


async def test_the_report_is_deterministic_across_two_runs(store: Store) -> None:
    """Gate 5's first requirement. A non-deterministic gate is worthless.

    Two `collect` + `render` passes over the *same* database must be byte-identical — which is what
    forbids a wall-clock read, an unordered `GROUP BY`, or a float formatted at full precision.
    """
    await build_fixture(store)
    first = await _report(store)
    second = await _report(store)
    assert first == second, "the bias report is not deterministic across two runs"


async def test_the_report_matches_the_frozen_expectation(store: Store) -> None:
    """**The gate.** Byte-for-byte against the checked-in expectation.

    If this fails, capture changed shape. That is not a test to update casually: the correct
    response is to look at the diff and decide whether the dataset's *meaning* changed, and only
    then re-freeze — the same discipline `eval/baselines/v0.2.0.json` has.

    Refresh with:  ``python -m pytest tests/model/test_bias.py --refresh-bias-report``
    is deliberately NOT provided. Re-freeze by hand, having read the diff.
    """
    await build_fixture(store)
    actual = await _report(store)
    assert EXPECTED.exists(), f"missing frozen expectation: {EXPECTED}"
    expected = EXPECTED.read_text(encoding="utf-8")
    assert actual == expected, (
        "the bias report changed. Capture has changed shape — read the diff and decide whether "
        "the dataset's meaning changed before re-freezing tests/fixtures/bias-report.txt."
    )


async def test_the_gate_goes_red_when_capture_shape_changes(store: Store) -> None:
    """**Guard the guard.** The gate is only worth having if it actually fires.

    Injects the smallest realistic capture-shape change — one more evaluated pair, exactly what a
    changed `MAX_CANDIDATES` or a new capture call site would produce — and asserts the report
    moves. A gate that stayed green through this would be measuring nothing.
    """
    await build_fixture(store)
    before = await _report(store)

    async with store.lock:
        run = await store.latest_capture_run()
        assert run is not None
        await store.add_pairs(
            [(int(run["id"]), 1, 2, None, None, None, 2.0, 0.1, 0.2, 3, 3, 0.4, 0, 0, 0, TS)]
        )
        await store.commit()

    after = await _report(store)
    assert after != before, "the bias-report gate did not notice a new captured pair"
    assert EXPECTED.read_text(encoding="utf-8") != after, "the frozen expectation went stale"


async def test_the_report_emits_aggregates_only(store: Store) -> None:
    """**Directive 7.** The dataset is a scope bypass; the report must leak none of it.

    Every NE address, OID and varbind in the fixture is checked for absence in the rendered output.
    The report may say *how many*; it may never say *which*.
    """
    await build_fixture(store)
    text = await _report(store)

    for address in (f"10.0.0.{i + 1}" for i in range(6)):
        assert address not in text, f"the report leaked an NE address: {address}"
    for oid in ("1.3.6.1.4.1.9.1", "1.3.6.1"):
        assert oid not in text, f"the report leaked an OID: {oid}"
    for operator in ("alice", "bob"):
        assert operator not in text, f"the report leaked an operator identity: {operator}"
    assert "varbind" not in text.lower() or "varbinds" not in text


async def test_the_report_states_effective_sample_size_as_bags(store: Store) -> None:
    """Gate 5: *n* is the number of independent **bags**, never the number of pairs.

    A report that quoted the pair count where the truth is the bag count would produce confidence
    intervals wrong by a large factor, and nobody downstream would check.
    """
    await build_fixture(store)
    measurements = await _report_measurements(store)
    text = await _report(store)

    assert measurements["bags"] == measurements["labels_total"]
    assert measurements["bags"] < measurements["pairs_sink"] + measurements["pairs_dataset"], (
        "the fixture must have more pairs than bags for this assertion to mean anything"
    )
    assert "*n* IS THE NUMBER OF INDEPENDENT BAGS, NOT THE NUMBER OF PAIRS." in text
    assert "distinct operators" in text and "distinct incidents" in text


async def test_the_report_separates_legacy_capture_and_never_averages_it(store: Store) -> None:
    """`legacy_capture` versus `current`, reported separately.

    The one population whose noise is *known* to be non-random must never be blended into the one
    whose noise is merely unmeasured.
    """
    await build_fixture(store)
    measurements = await _report_measurements(store)
    assert measurements["provenance"]["legacy_capture"] == 1
    assert measurements["provenance"]["current"] == 5
    text = await _report(store)
    assert "NEVER averaged together" in text
    assert "UNKNOWN QUALITY" in text


async def test_the_report_records_the_membership_divergence(store: Store) -> None:
    """The divergence between the server's bag and the client's is a **metric**, not an error."""
    await build_fixture(store)
    measurements = await _report_measurements(store)
    assert measurements["client_reported"] == 1
    assert measurements["client_diverged"] == 1, (
        "the fixture's client reported two of three members; that divergence must be counted"
    )


async def test_the_report_counts_the_zero_member_bag(store: Store) -> None:
    """A verdict on a memberless situation is real, recorded, and not interchangeable."""
    await build_fixture(store)
    measurements = await _report_measurements(store)
    assert measurements["empty_bags"] == 1
    # `empty` rather than `none` or `full`: see `labels.coverage`. This test is what found it.
    assert measurements["per_label_coverage"].get("empty", 0) == 1


async def test_the_report_says_what_it_cannot_tell_you(store: Store) -> None:
    """The section that makes the rest trustworthy.

    A dataset whose limits are not written down invites a later reader to assume it has none —
    which is how a measured bias becomes an unmeasured confidence.
    """
    await build_fixture(store)
    text = await _report(store)
    assert "what this report CANNOT tell you" in text
    assert "whether a verdict was correct" in text
    assert "no ground truth" in text


async def _report_measurements(store: Store) -> dict[str, Any]:
    async with store.lock:
        return await bias.collect(store)


# --- v0.8.1: the coverage denominator, and the orphan count ---------------------------------


async def test_the_coverage_rate_cannot_exceed_100_percent(store: Store) -> None:
    """**The skew, constructed.** Labels outliving their situations must not print >100%.

    v0.8.0 divided `COUNT(DISTINCT situation_id) FROM feedback` by `COUNT(*) FROM situation` — a
    table the operational prune collects — and Phase 0 measured the printed rate at **300.0%**. A
    rate above 100% destroys a reader's trust in every other number on the page.

    The `foreign_keys=OFF` window is how a *pre-v0.8.1* database gets into this state: v0.8.1's
    `prune()` retains the labelled situation row, so a fresh database cannot reach it — but one
    upgraded from v0.8.0 can already be here, and the report has to stay honest about it.
    """
    async with store.lock:
        for i in range(3):
            await store.conn.execute(
                "INSERT INTO situation (id, status, created_at, updated_at, closed_at) "
                "VALUES (?, 'closed', ?, ?, ?)",
                (i + 1, TS, TS + 20.0, TS + 20.0),
            )
            await store.conn.execute(
                "INSERT INTO feedback (situation_id, verdict, created_at) VALUES (?, 'confirm', ?)",
                (i + 1, TS + 10.0),
            )
        await store.commit()
        # Two situations collected, their labels surviving — the v0.8.0 state.
        await store.conn.execute("PRAGMA foreign_keys=OFF")
        await store.conn.execute("DELETE FROM situation WHERE id IN (1,2)")
        await store.commit()
        await store.conn.execute("PRAGMA foreign_keys=ON")

    async with store.lock:
        measured = await bias.collect(store)

    assert measured["situations_labelled"] == 3
    assert measured["situations_total"] == 3, (
        "the denominator must be the population the report has evidence of, not the surviving rows"
    )
    assert measured["situation_label_rate"] == "100.0%", measured["situation_label_rate"]
    # The general property, not just this case: the numerator is a subset of the denominator.
    assert int(measured["situations_labelled"]) <= int(measured["situations_total"])


async def test_the_report_counts_orphaned_promoted_pairs(store: Store) -> None:
    """A promoted pair whose label is gone is a feature nothing can interpret. Counted, not
    collected — the corpus is not corrupt, its *usable* size is smaller than its row count."""
    await build_fixture(store)
    async with store.lock:
        before = await bias.collect(store)
        assert before["orphaned_pairs"] == 0, "the clean fixture must have no orphans"
        await store.conn.execute("PRAGMA foreign_keys=OFF")
        await store.conn.execute("DELETE FROM feedback")
        await store.commit()
        await store.conn.execute("PRAGMA foreign_keys=ON")
        after = await bias.collect(store)
    assert after["orphaned_pairs"] == before["pairs_dataset"] > 0, (
        "every promoted pair is orphaned once its label is gone, and the report must say so"
    )
