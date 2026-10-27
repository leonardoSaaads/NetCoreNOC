"""The case's rules without a database: its state machine, its clocks, its feed (ADRs #459-#461)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from netcorenoc.engine.noc import cases, feed, kpi
from netcorenoc.engine.noc.cases import CaseError

MIGRATION = (
    Path(__file__).resolve().parents[2] / "src/netcorenoc/migrations/0034_noc_cases.sql"
).read_text()


def _check(column: str) -> set[str]:
    block = MIGRATION[MIGRATION.index(f"    {column} ") :]
    block = block[: block.index("),") + 1]
    return set(re.findall(r"'([a-z_]+)'", block))


def test_the_vocabularies_are_the_databases() -> None:
    assert _check("status") == set(cases.STATUSES)
    assert _check("priority") == set(cases.PRIORITIES)
    assert _check("resolution") == set(cases.RESOLUTIONS)
    assert _check("reason") == set(cases.CYCLE_REASONS)
    assert _check("visibility") == set(feed.VISIBILITIES)
    for name, (_label, cap) in cases.FIELD_LIMITS.items():
        assert f"length({name}) <= {cap}" in MIGRATION, name


def test_the_state_machine() -> None:
    assert cases.transition("take", "queue") == "working"
    assert cases.transition("take", "pending_customer") == "working"
    assert cases.transition("pend", "working", to="pending_dispatch") == "pending_dispatch"
    assert cases.transition("release", "working") == "queue"
    assert cases.transition("resolve", "validate") == "resolved"
    with pytest.raises(CaseError) as refused:
        cases.transition("resolve", "queue")
    assert refused.value.status == 409
    with pytest.raises(CaseError) as wrong:
        cases.transition("pend", "working", to="resolved")
    assert wrong.value.status == 400
    with pytest.raises(CaseError):
        cases.transition("take", "closed")
    assert cases.super_state("pending_rca") == "pending" and cases.super_state("new") == "queue"
    assert [cases.priority_of(s) for s in ("critical", "major", "minor", "warning", None)] == [
        "critical",
        "major",
        "minor",
        "minor",
        "minor",
    ]


def test_what_a_case_must_carry() -> None:
    now = 1000.0
    full = {
        "title": "t",
        "description": "d",
        "executive_summary": "e",
        "current_analysis": "a",
        "noc_next_step": "n",
        "customer_next_step": "c",
        "next_update_at": now + 60,
    }
    assert cases.missing(full, "pending_customer", now=now) == []
    assert cases.missing({**full, "next_update_at": now - 1}, "pending_customer", now=now) == [
        "next_update_at"
    ]
    assert cases.missing({**full, "title": "  "}, "pending_customer", now=now) == ["title"]
    assert cases.missing(full, "resolved", now=now) == ["cause", "solution"]


def test_the_take_clock_counts_the_overrun() -> None:
    cycle = {"priority": "critical", "entered_at": 0.0, "taken_at": None, "ended_at": None}
    assert kpi.take(cycle, 600.0)["state"] == "running"
    late = kpi.take(cycle, 40 * 60.0)
    assert late["state"] == "late" and late["overrun_s"] == 15 * 60.0
    assert kpi.take({**cycle, "taken_at": 20 * 60.0}, 99e9)["state"] == "met"
    missed = kpi.take({**cycle, "taken_at": 30 * 60.0}, 99e9)
    assert missed["state"] == "missed" and missed["overrun_s"] == 5 * 60.0
    gone = kpi.take({**cycle, "ended_at": 99 * 60.0}, 99e9)
    assert gone["state"] == "withdrawn" and gone["overrun_s"] == 0.0
    assert kpi.take({**cycle, "priority": "minor"}, 0.0)["target_s"] == 2 * 3600.0


def test_the_document_clock_and_the_summary() -> None:
    taken = {"priority": "major", "entered_at": 0.0, "taken_at": 100.0, "ended_at": None}
    assert kpi.document({**taken, "taken_at": None}, 0.0) is None
    done = kpi.document({**taken, "documented_at": 1000.0}, 9e9)
    assert done is not None and done["state"] == "met"
    left = kpi.document({**taken, "documented_at": None, "left_at": 500.0}, 9e9)
    assert left is not None and left["state"] == "missed"
    total = kpi.summary(
        [
            {**taken, "documented_at": 200.0},
            {"priority": "critical", "entered_at": 0.0, "taken_at": 3600.0},
            {"priority": "minor", "entered_at": 0.0, "ended_at": 10.0},
        ],
        9e9,
    )
    assert total["cycles"] == 3 and total["take"]["met"] == 1 and total["take"]["missed"] == 1
    assert total["take"]["withdrawn"] == 1 and total["take"]["met_pct"] == 50.0
    assert total["take"]["overrun_s"] == 3600.0 - 25 * 60.0


def test_the_update_cadence_is_advice() -> None:
    case = {"status": "pending_customer", "priority": "critical", "next_update_at": 10_000.0}
    advice = kpi.cadence(case, 0.0)
    assert advice is not None and advice["beyond_cadence"] and not advice["overdue"]
    assert kpi.cadence({**case, "status": "queue"}, 0.0) is None


def test_addresses_are_shown_as_networks_wherever_they_sit() -> None:
    text = "LOS at 10.0.0.1, peer (10.0.0.77) and 2001:db8::1; ifIndex 3.4.5 stays"
    assert feed.mask_addresses(text) == (
        "LOS at 10.0.0.0/24, peer (10.0.0.0/24) and 2001:db8::/48; ifIndex 3.4.5 stays"
    )
    assert feed.mask_addresses("version 999.1.2.3 build") == "version 999.1.2.3 build"
    assert feed.mask_addresses("time 10:30 and port 1/1/3") == "time 10:30 and port 1/1/3"


def test_who_reads_a_post() -> None:
    noc = feed.Reader(ref="user:2", user_id=2, role="editor", works=True, admin=False)
    customer = feed.Reader(ref="user:3", user_id=3, role="viewer", works=False, admin=False)
    named = {"visibility": "specified", "author_ref": "user:2", "readers": [3]}
    assert feed.visible({"visibility": "internal", "author_ref": "user:9"}, noc)
    assert not feed.visible({"visibility": "internal", "author_ref": "user:9"}, customer)
    assert feed.visible(named, customer) and feed.visible(named, noc)
    assert not feed.visible({**named, "readers": [4], "author_ref": "user:9"}, customer)
    pending = {"status": "pending_customer", "owner_ref": "user:2"}
    assert feed.requeues(pending, author_kind="person", author_ref="user:3")
    assert not feed.requeues(pending, author_kind="person", author_ref="user:2")
    assert not feed.requeues(pending, author_kind="agent", author_ref="token:1")
    assert not feed.requeues({**pending, "status": "working"}, author_kind="person", author_ref="x")
