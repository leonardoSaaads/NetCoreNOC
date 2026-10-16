"""`tools/storm_check.py` — the instrument for the per-trap deadline (#447-#449), driven small.

The tool's claim is that it reports every trap, misses included, through the real path. This drives
a storm small enough for the suite and holds it to that: every alarm sent is received and
classified, the report has the numbers an operator sizes a host with, and nothing is a miss.
"""

from __future__ import annotations

import asyncio

import pytest

from netcorenoc.ingest import process

import storm_check


def test_the_storm_is_almost_all_distinct_alarms() -> None:
    traps = storm_check.storm(500, devices=250, classes=60)
    assert len(traps) == 500
    assert len({payload for _source, payload in traps}) == 500
    assert len({source for source, _payload in traps}) > 100


@pytest.mark.parametrize("ingest_processes", [0, 1])
def test_every_trap_of_a_storm_is_accounted_for_and_every_miss_reported(
    ingest_processes: int,
) -> None:
    """The tool's claim, whatever this host's socket buffer: each trap sent is classified or
    counted as a miss — and the in-process receiver (0) may well miss some in the kernel, since a
    traced test run decodes slowly on its default buffer; that is #449's reason, reported here."""
    if ingest_processes and not process.supported():
        pytest.skip("POSIX descriptor inheritance")
    args = storm_check.main.__globals__["argparse"].Namespace(
        traps=300,
        devices=50,
        classes=12,
        rate=500.0,
        deadline=60.0,
        ingest_processes=ingest_processes,
        timeout=120.0,
    )
    report = asyncio.run(storm_check.check(args))
    kernel = report["kernel_dropped"] or 0
    assert report["sent"] == 300 and report["received"] <= 300
    assert report["received"] + kernel >= report["sent"], "a trap vanished uncounted"
    assert report["classified"] == report["received"] - report["dropped"]
    assert report["late"] == 0 and report["lost"] == 0
    assert report["missed"] == report["dropped"] + kernel + max(
        0, report["sent"] - report["received"] - kernel
    )
    assert report["decider"]
