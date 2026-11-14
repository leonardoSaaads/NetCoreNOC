"""The audit log, narrowed (ADR #486): by period, person, section, outcome, text and page."""

from __future__ import annotations

import json

from netcorenoc.crosscutting import audit, audit_sections
from netcorenoc.store import Store

import authutil


def test_every_action_in_the_catalogue_is_in_exactly_one_section() -> None:
    homeless = sorted(a for a in audit.ACTIONS if audit_sections.section_of(a) is None)
    assert not homeless, f"actions no section of the audit log's filter covers: {homeless}"
    words = [w for _label, ws in audit_sections.SECTIONS.values() for w in ws]
    assert len(words) == len(set(words)), "a first word filed under two sections"


async def _seed(store: Store) -> None:
    rows = [
        (100.0, "adm", "login.ok", "ok", "user", "1"),
        (200.0, "edt", "login.fail", "denied", "user", "2"),
        (300.0, "adm", "noc.secret.reveal", "ok", "noc_secret", "7"),
        (400.0, "adm", "config.change", "ok", None, None),
        (500.0, "edt", "noc.case.open", "denied", "situation", "41"),
    ]
    async with store.lock:
        for ts, actor, action, outcome, otype, oid in rows:
            await audit.write_event(
                store,
                ts=ts,
                actor=actor,
                role="admin",
                source_ip="10.0.0.1",
                action=action,
                outcome=outcome,
                object_type=otype,
                object_id=oid,
                details={},
            )
        await store.commit()


async def test_the_log_narrows_by_period_person_section_outcome_and_text(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    await _seed(store)
    admin = await authutil.client_as(app, "admin")
    try:

        async def actions(query: str) -> list[str]:
            got = await admin.get(f"/api/audit?{query}")
            assert got.status_code == 200, got.text
            return [r["action"] for r in got.json() if r["action"] != "audit.read"]

        assert await actions("since=150&until=450") == [
            "config.change",
            "noc.secret.reveal",
            "login.fail",
        ]
        assert await actions("actor=edt") == ["noc.case.open", "login.fail"]
        assert await actions("section=noc") == ["noc.case.open", "noc.secret.reveal"]
        assert set(await actions("section=sign-in&until=1000")) == {"login.ok", "login.fail"}
        assert await actions("outcome=denied&until=1000") == ["noc.case.open", "login.fail"]
        assert await actions("q=SECRET") == ["noc.secret.reveal"]
        assert await actions("q=situation/41") == ["noc.case.open"]
        # The read itself is audited, with the filters it used.
        cur = await store.conn.execute(
            "SELECT details FROM audit_log WHERE action='audit.read' ORDER BY id DESC LIMIT 1"
        )
        row = await cur.fetchone()
        assert row is not None and json.loads(row[0])["filters"] == {"q": "situation/41"}
        assert await actions("q=%25") == [], "a % is a character, not a wildcard"
        first = (await admin.get("/api/audit?limit=2&until=1000")).json()
        older = (await admin.get(f"/api/audit?limit=2&until=1000&before={first[-1]['id']}")).json()
        assert [r["action"] for r in older] == ["noc.secret.reveal", "login.fail"]
        assert (await admin.get("/api/audit?section=nowhere")).status_code == 400
        assert (await admin.get("/api/audit?outcome=maybe")).status_code == 400
    finally:
        await admin.aclose()


async def test_facets_count_people_and_sections_and_are_admin_only(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    await _seed(store)
    admin = await authutil.client_as(app, "admin")
    editor = await authutil.client_as(app, "editor")
    try:
        facets = (await admin.get("/api/audit/facets")).json()
        sections = {s["id"]: s["count"] for s in facets["sections"]}
        assert sections["noc"] == 2 and sections["settings"] == 1
        assert sections["sign-in"] >= 2  # the seeded two, and the test clients' own sign-ins
        actors = {a["actor"]: a["count"] for a in facets["actors"]}
        assert actors["edt"] >= 2 and facets["first"] == 100.0 and facets["total"] >= 5
        assert (await editor.get("/api/audit/facets")).status_code == 403
    finally:
        await admin.aclose()
        await editor.aclose()
