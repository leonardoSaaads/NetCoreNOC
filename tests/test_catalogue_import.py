"""Importing a trap list: bounded, validated row by row, and all-or-nothing (v0.22.0, ADR #385).

The parser is pure, so most of this drives `catalogue_import.parse` directly; the route is driven
for the two properties only it has — the size cap is enforced before the body is read, and a file
with one bad row changes nothing in the store.
"""

from __future__ import annotations

from netcorenoc.api import catalogue_import as importer
from netcorenoc.store import Store

import authutil

GOOD = """class,trap_oid,vendor,severity
linkDown,1.3.6.1.4.1.2011.2.15.1,Huawei,major
fanFail,.1.3.6.1.4.1.2011.2.15.2,Huawei,critical
coldStart,1.3.6.1.4.1.2011.2.15.3,,
"""


def test_a_good_file_parses_every_row_and_normalises_the_leading_dot() -> None:
    parsed = importer.parse(
        GOOD.replace("coldStart,1.3.6.1.4.1.2011.2.15.3,,", "x,1.3.6.1.9,,minor")
    )
    assert parsed.ok, parsed.problems
    assert [r.oid for r in parsed.rows][:2] == [
        "1.3.6.1.4.1.2011.2.15.1",
        "1.3.6.1.4.1.2011.2.15.2",
    ]
    assert parsed.rows[1].severity == "critical"


def test_every_bad_row_is_reported_with_its_line_field_and_rule() -> None:
    text = (
        "trap_oid;class;severity\n"
        "1.3.6.1.4.1.9.1;ok;minor\n"
        "1.3.6.x;bad oid;minor\n"
        "1.3.6.1.4.1.9.1;dup;minor\n"
        "1.3.6.1.4.1.9.3;sev;medium\n"
        "1.3.6.1.4.1.9.4;;\n"
        "1.3.6.1.4.1.9.5;" + "n" * 200 + ";\n"
    )
    parsed = importer.parse(text)
    assert not parsed.ok
    found = {(p.line, p.field) for p in parsed.problems}
    assert found == {
        (3, "trap_oid"),
        (4, "trap_oid"),
        (5, "severity"),
        (6, "class"),
        (7, "class"),
    }, parsed.problems
    assert "repeats line 2" in next(p.rule for p in parsed.problems if p.line == 4)


def test_a_file_without_a_trap_oid_column_is_refused_as_a_whole() -> None:
    assert importer.parse("name,severity\nx,minor\n").refused
    assert importer.parse("   \n\n").refused


def test_the_size_and_row_caps_refuse_rather_than_truncate() -> None:
    big = "trap_oid,class\n" + "1.3.6.1.4.1.9.1,x\n" * (importer.MAX_BYTES // 10)
    assert importer.parse(big).refused and "KiB" in (importer.parse(big).refused or "")
    rows = "trap_oid,class\n" + "".join(
        f"1.3.6.1.4.1.9.{i},c\n" for i in range(importer.MAX_ROWS + 1)
    )
    parsed = importer.parse(rows)
    assert parsed.refused and str(importer.MAX_ROWS) in parsed.refused
    # The control: exactly the cap is accepted.
    at_cap = "trap_oid,class\n" + "".join(
        f"1.3.6.1.4.1.9.{i},c\n" for i in range(importer.MAX_ROWS)
    )
    assert importer.parse(at_cap).ok


def test_the_parser_stops_at_its_deadline() -> None:
    """`now` fixes the start in the past, so the first data row is already over the deadline."""
    parsed = importer.parse(GOOD, now=-1e9)
    assert parsed.refused and "longer than" in parsed.refused


def test_a_control_character_is_a_problem_and_not_stored() -> None:
    parsed = importer.parse("trap_oid,class\n1.3.6.1.4.1.9.1,a\x07b\n")
    assert [p.field for p in parsed.problems] == ["class"]


async def test_one_bad_row_imports_nothing_and_a_dry_run_imports_nothing(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    editor = await authutil.client_as(app, "editor")
    try:
        bad = GOOD + "oops,1.3.6.x,,\n"
        refused = await editor.post("/api/catalogue/import", json={"filename": "f", "text": bad})
        assert refused.status_code == 200 and refused.json()["outcome"] == "refused"
        checked = await editor.post(
            "/api/catalogue/import?dry_run=true", json={"filename": "f", "text": GOOD}
        )
        assert checked.json()["outcome"] == "checked" and checked.json()["new"] == 3
        rules = (await editor.get("/api/catalogue/rules")).json()
        assert rules["total"] == 0, "a refused or checked file changed the catalogue"

        done = await editor.post("/api/catalogue/import", json={"filename": "f", "text": GOOD})
        assert done.json()["outcome"] == "imported", done.text
        assert (await editor.get("/api/catalogue/rules")).json()["total"] == 3
        # Idempotent: the same file again updates three rows and adds none.
        again = await editor.post("/api/catalogue/import", json={"filename": "f", "text": GOOD})
        assert again.json()["new"] == 0 and again.json()["updated"] == 3

        # Withdraw-imported leaves a declared rule standing.
        await editor.post(
            "/api/catalogue/rules", json={"oid": "1.3.6.1.4.1.2011", "name": "Huawei"}
        )
        assert (await editor.delete("/api/catalogue/imported")).status_code == 200
        left = (await editor.get("/api/catalogue/rules")).json()
        assert [r["source"] for r in left["rules"]] == ["declared"], left
    finally:
        await editor.aclose()


async def test_an_oversized_body_is_refused_before_it_is_read(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    editor = await authutil.client_as(app, "editor")
    try:
        huge = "x" * (importer.MAX_BYTES * 2 + 8192)
        resp = await editor.post("/api/catalogue/import", json={"filename": "f", "text": huge})
        assert resp.status_code == 413, resp.text
        malformed = await editor.post("/api/catalogue/import", json={"nope": 1})
        assert malformed.status_code == 422, malformed.text
    finally:
        await editor.aclose()


async def test_a_viewer_cannot_import(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    viewer = await authutil.client_as(app, "viewer")
    try:
        resp = await viewer.post("/api/catalogue/import", json={"filename": "f", "text": GOOD})
        assert resp.status_code == 403
    finally:
        await viewer.aclose()
