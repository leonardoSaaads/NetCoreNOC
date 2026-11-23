"""ADR #501 — a routine keeps every version it had, why each was written, and how each fared.

An agent improves a routine with the whole tree and a note naming the case that taught it; the
routine's page lists its versions newest first, each with its note, its author and how its runs
ended, and an earlier one is read whole. An earlier version is read under its own tier and
customer: a routine raised from tier 2 to tier 1 does not show a tier-1 agent the tier-2 tree it
was, and one moved from a customer to every customer does not show its old tree to another's
reader. Versions are append-only and every write of one is audited.
"""

from __future__ import annotations

import json
from typing import Any

import test_noc_routines as routines

from netcorenoc.store import Store

import authutil

device_port = routines.device_port


async def _audit(store: Store, action: str) -> list[dict[str, Any]]:
    async with store.reading():
        cur = await store.conn.execute(
            "SELECT details FROM audit_log WHERE action = ? ORDER BY id", (action,)
        )
        return [json.loads(r[0]) for r in await cur.fetchall()]


async def test_an_agent_improves_a_routine_and_each_version_is_kept(
    store: Store, device_port: int
) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    estate = await routines._estate(store, app, device_port)
    lead = await authutil.client_as(app, "admin")
    agent = routines._as(app, estate["keys"][1])
    try:
        made = await lead.post(
            "/api/noc/playbooks",
            json={"document": routines._routine("Link down"), "customer_id": estate["acme"]},
        )
        assert made.status_code == 200, made.text
        pid = made.json()["id"]
        assert [v["version"] for v in made.json()["versions"]] == [1]

        sid = await routines._case_of(store, estate["acme"])
        ran = await agent.post(f"/api/noc/playbooks/{pid}/run", json={"situation_id": sid})
        assert ran.status_code == 200, ran.text

        better = {**routines._routine("Link down"), "description": "Reach before anything else."}
        note = "Reach first: the last case was a dead path, not a dead port"
        improved = await agent.post(
            f"/api/noc/playbooks/{pid}",
            json={"document": better, "customer_id": estate["acme"], "note": note},
        )
        assert improved.status_code == 200, improved.text
        assert improved.json()["version"] == 2

        read = (await agent.get(f"/api/noc/playbooks/{pid}")).json()
        versions = {v["version"]: v for v in read["versions"]}
        assert [v["version"] for v in read["versions"]] == [2, 1], "newest first"
        assert versions[2]["note"] == note and versions[1]["note"] == ""
        assert versions[2]["written_by"] != versions[1]["written_by"], "the agent, then the lead"
        assert versions[1]["outcomes"] == {"advised": 1}, "the run is counted on its version"
        assert versions[2]["outcomes"] == {}

        first = await agent.get(f"/api/noc/playbooks/{pid}/versions/1")
        assert first.status_code == 200, first.text
        assert first.json()["document"].get("description") != "Reach before anything else."
        assert first.json()["document"]["name"] == "Link down"
        assert (await agent.get(f"/api/noc/playbooks/{pid}/versions/3")).status_code == 404

        audited = await _audit(store, "noc.playbook.update")
        assert {"version": 2, "note": note} in audited
        too_long = await agent.post(
            f"/api/noc/playbooks/{pid}",
            json={"document": better, "customer_id": estate["acme"], "note": "x" * 301},
        )
        assert too_long.status_code == 422, "a note is one line, not a document"
    finally:
        for client in (lead, agent):
            await client.aclose()


async def test_an_earlier_version_is_read_under_its_own_tier_and_customer(
    store: Store, device_port: int
) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    estate = await routines._estate(store, app, device_port)
    lead = await authutil.client_as(app, "admin")
    tier1 = routines._as(app, estate["keys"][1])
    try:
        made = await lead.post(
            "/api/noc/playbooks",
            json={"document": routines._routine("Core"), "customer_id": estate["acme"], "tier": 2},
        )
        assert made.status_code == 200, made.text
        pid = made.json()["id"]
        assert (await tier1.get(f"/api/noc/playbooks/{pid}")).status_code == 404

        lowered = await lead.post(
            f"/api/noc/playbooks/{pid}",
            json={"document": routines._routine("Core"), "tier": 1, "note": "for every agent"},
        )
        assert lowered.status_code == 200, lowered.text
        seen = (await tier1.get(f"/api/noc/playbooks/{pid}")).json()
        assert [v["version"] for v in seen["versions"]] == [2], "the tier-2 version is not listed"
        hidden = await tier1.get(f"/api/noc/playbooks/{pid}/versions/1")
        assert hidden.status_code == 404 and hidden.json()["detail"] == "no such version"
        assert (await tier1.get(f"/api/noc/playbooks/{pid}/versions/2")).status_code == 200
        assert (await lead.get(f"/api/noc/playbooks/{pid}/versions/1")).status_code == 200
        viewer = await authutil.client_as(app, "viewer")
        try:
            assert (await viewer.get(f"/api/noc/playbooks/{pid}/versions/2")).status_code == 403
        finally:
            await viewer.aclose()
    finally:
        for client in (lead, tier1):
            await client.aclose()
