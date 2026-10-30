"""The NOC stops serving a customer (ADR #477).

* only an admin ends a customer, typing its name again and confirming who they are; a wrong
  password is refused and is an audit row of its own;
* ending deletes its book — instructions, memory, contacts, documents, circuits — returns its
  elements to the default organization and keeps the row, ended, for the history that names it;
* an ended customer is on no list and answers 404; no agent tool reaches the act;
* adding it again under its handle reinstates the same row, its book empty;
* the default organization is never a customer, so it cannot be ended.
"""

from __future__ import annotations

import json
from typing import Any

from netcorenoc.api import mcp_tools
from netcorenoc.store import Store

import authutil

STEP = {"username": "adm", "password": authutil.PW}


async def _audit(store: Store, action: str) -> list[dict[str, Any]]:
    async with store.reading():
        cur = await store.conn.execute(
            "SELECT outcome, object_id, details FROM audit_log WHERE action = ? ORDER BY id",
            (action,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def test_an_admin_ends_a_customer_and_its_history_stays(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    async with store.lock:
        ne = await store.ne_id("10.7.0.1", 1.0)
        akamai = await store.create_organization("Akamai", "akamai", 1.0)
        await store.set_ne_organization(ne, akamai)
        await store.commit()
    admin = await authutil.client_as(app, "admin")
    editor = await authutil.client_as(app, "editor")
    base = f"/api/noc/customers/{akamai}"
    try:
        assert (
            await admin.post(base, json={"instructions": "Call Zayo first."})
        ).status_code == 200
        contact = {
            "party": "Zayo",
            "party_kind": "provider",
            "escalation_level": 1,
            "name": "Zayo NOC",
            "role": "24x7",
            "email": "noc@zayo.example",
            "phone": "+1 555 0100",
            "hours": "24x7",
            "notes": "",
        }
        assert (await admin.post(f"{base}/contacts", json=contact)).status_code == 200
        circuit = {"designation": "AKAM-GRU-01", "a_end": "GRU", "z_end": "SILA", "path": []}
        assert (await admin.post(f"{base}/circuits", json=circuit)).status_code == 200

        end = f"{base}/end"
        assert (await editor.post(end, json={"name": "Akamai", "stepup": STEP})).status_code == 403
        typo = await admin.post(end, json={"name": "Akamay", "stepup": STEP})
        assert typo.status_code == 400 and "name" in typo.json()["detail"]
        wrong = {"name": "akamai", "stepup": {**STEP, "password": "not-it-at-all"}}
        assert (await admin.post(end, json=wrong)).status_code == 403
        assert [r["outcome"] for r in await _audit(store, "noc.customer.end")] == ["denied"]
        assert (await admin.get(base)).status_code == 200, "a refused end changes nothing"

        ended = await admin.post(end, json={"name": " akamai ", "stepup": STEP})
        assert ended.status_code == 200, ended.text
        assert (
            ended.json() | {"contacts": 1, "circuits": 1, "book": 1, "elements": 1} == ended.json()
        )
        assert (await admin.get(base)).status_code == 404
        assert (await admin.get("/api/noc/customers")).json()["customers"] == []
        orgs = (await admin.get("/api/organizations")).json()["organizations"]
        assert [o["slug"] for o in orgs] == ["default"], "an ended customer is on no picker"
        async with store.reading():
            cur = await store.conn.execute(
                "SELECT o.is_default, x.ended_at IS NOT NULL, x.ended_by FROM ne n "
                "JOIN organization o ON o.id = n.organization_id, organization x "
                "WHERE n.id = ? AND x.id = ?",
                (ne, akamai),
            )
            row = await cur.fetchone()
            for table in ("noc_contact", "noc_circuit", "noc_customer", "noc_customer_doc"):
                cur = await store.conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE organization_id = ?",  # nosec B608
                    (akamai,),
                )
                assert (await cur.fetchone())[0] == 0, table  # type: ignore[index]
        assert row is not None and row[0] and row[1] and row[2] == "adm"
        done = (await _audit(store, "noc.customer.end"))[-1]
        assert done["outcome"] == "ok" and done["object_id"] == str(akamai)
        details = json.loads(done["details"])
        assert (
            details["slug"] == "akamai" and details["step_up"] == "ok" and details["circuits"] == 1
        )
        assert (await admin.post(end, json={"name": "Akamai", "stepup": STEP})).status_code == 404

        # The NOC serves Akamai again: the same row, its book empty.
        again = await admin.post("/api/organizations", json={"name": "Akamai", "slug": "akamai"})
        assert again.json() == {"created": True, "id": akamai, "reinstated": True}
        book = (await admin.get(base)).json()
        assert book["instructions"] == "" and book["circuits"] == 0
        dup = await admin.post("/api/organizations", json={"name": "Akamai", "slug": "akamai"})
        assert dup.status_code == 409, "a live customer is not added twice"
    finally:
        await admin.aclose()
        await editor.aclose()


async def test_the_default_organization_is_never_ended_and_no_agent_can_end_one(
    store: Store,
) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        default = (await admin.get("/api/organizations")).json()["default_organization_id"]
        refused = await admin.post(
            f"/api/noc/customers/{default}/end", json={"name": "Default", "stepup": STEP}
        )
        assert refused.status_code == 404
    finally:
        await admin.aclose()
    paths = {tool.path for tool in mcp_tools.TOOLS}
    assert not any(path.endswith("/end") for path in paths), "ending a customer is a person's act"
