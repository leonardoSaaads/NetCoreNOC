"""`tools/storm_check.py` — the instrument for the per-trap KPI and the spool (#447-#451), small.

The tool's claim is that it accounts for every trap through the real path. This drives a storm small
enough for the suite and holds it to that: every alarm sent is kept and classified exactly once —
across a restart of the engine halfway through, too — and the report has the numbers an operator
sizes a host with.
"""

from __future__ import annotations

import argparse
import asyncio

import pytest

from netcorenoc.ingest import process

import storm_check


def test_the_storm_is_almost_all_distinct_alarms() -> None:
    traps = storm_check.storm(500, devices=250, classes=60)
    assert len(traps) == 500
    assert len({payload for _source, payload in traps}) == 500
    assert len({source for source, _payload in traps}) > 100


def _args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "traps": 300,
        "devices": 50,
        "classes": 12,
        "rate": 500.0,
        "target": 60.0,
        "limit": 120.0,
        "require_kpi": False,
        "restart_at": 0.0,
        "spool_mb": 64,
        "ingest_processes": 1,
        "timeout": 120.0,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_every_trap_of_a_storm_is_kept_and_classified_once() -> None:
    """The spooled path (the default): nothing is lost, and a restart of the engine halfway
    through classifies nothing twice — it stores exactly the alarms an uninterrupted run does."""
    if not process.supported():
        pytest.skip("POSIX descriptor inheritance")
    reports = [asyncio.run(storm_check.check(_args(restart_at=at))) for at in (0.0, 0.5)]
    for report, restarts in zip(reports, (0, 1), strict=True):
        kernel = report["kernel_dropped"] or 0
        assert report["sent"] == 300 and report["restarts"] == restarts
        assert report["not_kept"] == kernel, "nothing lost past the kernel's own buffer"
        assert report["classified"] + kernel == 300 and report["unaccounted"] == 0
        assert report["dead_lettered"] == 0 and report["over_limit"] == 0 and report["decider"]
    if not any(r["kernel_dropped"] for r in reports):
        assert reports[0]["alarms_stored"] == reports[1]["alarms_stored"], "nothing twice"


def test_the_in_process_receiver_is_still_accounted_for() -> None:
    """`NETCORENOC_INGEST_PROCESSES=0`: no spool, so a full queue or the kernel may drop — counted,
    never silent. A traced test run decodes slowly on the default buffer; that is #449's reason."""
    report = asyncio.run(storm_check.check(_args(ingest_processes=0)))
    kernel = report["kernel_dropped"] or 0
    assert report["received"] + kernel >= report["sent"], "a trap vanished uncounted"
    assert report["classified"] == report["received"] - report["dropped"]
    assert report["unaccounted"] == 0 and report["not_kept"] == report["dropped"] + kernel
