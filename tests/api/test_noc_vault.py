"""NOC → Secrets through the HTTP API (ADRs #454-#458).

What a reviewer asks of a vault, each answered by driving the real routes:

* only custodians — admins, and editors an admin named — see or store anything, and only people;
* no list, detail or lease ever carries a credential; a reveal does, after a step-up, and is audited
  with who asked; a failed step-up is audited too, and counts against the sign-in throttle;
* agents are off by default; an agent token gets a lease, never a secret, and only for a credential
  a custodian switched on; changing the credential or switching agents off ends its leases;
* a row whose host is rewritten in the database no longer opens;
* the status light follows real connections.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from netcorenoc.crosscutting import audit
from netcorenoc.store import Store

import authutil

PW = authutil.PW
SECRET = "Backbone-r00t-only-the-vault-knows"
STEP = {"username": "adm", "password": PW}


def _new(
    name: str = "core-1 ssh", host: str = "10.0.0.1", port: int | None = None
) -> dict[str, Any]:
    return {
        "name": name,
        "kind": "ssh_password",
        "access_level": "root",
        "host": host,
        **({"port": port} if port else {}),
        "fields": {"username": "noc", "password": SECRET},
    }


async def _bearer(app: object, store: Store, name: str, role: str) -> tuple[httpx.AsyncClient, int]:
    value = await authutil.make_token(store, name, role)
    async with store.reading():
        token_id = next(int(t["id"]) for t in await store.list_tokens() if t["name"] == name)
    client = authutil.new_client(app)
    client.headers["Authorization"] = f"Bearer {value}"
    return client, token_id


async def _audit(store: Store) -> list[dict[str, Any]]:
    async with store.reading():
        return await store.audit_all()


async def test_only_custodians_hold_the_vault_and_no_list_carries_a_secret(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    editor = await authutil.client_as(app, "editor")
    viewer = await authutil.client_as(app, "viewer")
    try:
        made = await admin.post("/api/noc/secrets", json=_new())
        assert made.status_code == 200, made.text
        sid = made.json()["id"]
        assert made.json()["agent_access"] is False, "agents are off by default"
        listing = await admin.get("/api/noc/secrets")
        detail = await admin.get(f"/api/noc/secrets/{sid}")
        for response in (made, listing, detail):
            assert SECRET not in response.text and "ncs1:" not in response.text
        assert listing.json()["custodian"] is True
        assert listing.json()["secrets"][0]["status"]["state"] == "unknown"
        assert {k["id"] for k in listing.json()["catalogue"]["kinds"]} >= {"ssh_key", "snmp_v3"}

        # An editor is not a custodian until an admin names one — and naming takes a step-up.
        refused = (await editor.get("/api/noc/secrets")).json()
        assert refused["custodian"] is False and refused["secrets"] == []
        assert (await editor.post("/api/noc/secrets", json=_new("x"))).status_code == 403
        assert (await editor.get(f"/api/noc/secrets/{sid}")).status_code == 403
        assert (await viewer.get("/api/noc/secrets")).status_code == 403  # below the ceiling
        people = (await admin.get("/api/noc/grants")).json()["people"]
        edt = next(p for p in people if p["username"] == "edt")
        grant = {"subject": edt["ref"], "role": "custodian", "enabled": True}
        assert (await admin.post("/api/noc/grants", json=grant)).status_code == 400
        named = await admin.post("/api/noc/grants", json={**grant, "stepup": STEP})
        assert named.status_code == 200 and named.json()["changed"] is True
        assert (await editor.get("/api/noc/secrets")).json()["custodian"] is True
        assert (await editor.post("/api/noc/secrets", json=_new("y"))).status_code == 200
        withdraw = await admin.post("/api/noc/grants", json={**grant, "enabled": False})
        assert withdraw.status_code == 200, "withdrawing needs no step-up"
        assert (await editor.get("/api/noc/secrets")).json()["custodian"] is False
    finally:
        for client in (admin, editor, viewer):
            await client.aclose()


async def test_a_reveal_takes_a_step_up_and_both_outcomes_are_audited(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        sid = (await admin.post("/api/noc/secrets", json=_new())).json()["id"]
        url = f"/api/noc/secrets/{sid}/reveal"
        assert (await admin.post(url, json={})).status_code == 422
        wrong = await admin.post(url, json={"stepup": {**STEP, "password": "not-the-password!"}})
        assert wrong.status_code == 403 and SECRET not in wrong.text
        other = await admin.post(url, json={"stepup": {"username": "edt", "password": PW}})
        assert other.status_code == 403, "a colleague's password does not borrow their custody"
        shown = await admin.post(url, json={"stepup": {**STEP, "otp": "123456"}})
        assert shown.status_code == 200
        assert shown.json()["fields"] == {"username": "noc", "password": SECRET}
        assert shown.headers["cache-control"] == "no-store"
        rows = [r for r in await _audit(store) if r["action"] == "noc.secret.reveal"]
        assert [r["outcome"] for r in rows] == ["denied", "denied", "ok"]
        assert all(r["actor"] == "adm" and r["object_id"] == str(sid) for r in rows)
        details = json.loads(rows[-1]["details"])
        assert details["second_factor"] == "not-enrolled", "the placeholder says what it is"
        history = (await admin.get(f"/api/noc/secrets/{sid}")).json()["history"]
        assert [h["action"] for h in history][:1] == ["noc.secret.reveal"]
        assert history[-1]["action"] == "noc.secret.create" and history[-1]["actor"] == "adm"
        assert (await admin.get(f"/api/noc/secrets/{sid}")).json()["revealed_by"] == "adm"
        # Nothing secret anywhere in the chain, and the chain still verifies.
        dump = json.dumps([dict(r) for r in await _audit(store)], default=str)
        assert SECRET not in dump and "not-the-password" not in dump
        assert (await audit.verify_chain(store)).ok
    finally:
        await admin.aclose()


async def test_failed_step_ups_count_against_the_sign_in_throttle(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        sid = (await admin.post("/api/noc/secrets", json=_new())).json()["id"]
        url = f"/api/noc/secrets/{sid}/reveal"
        for _ in range(6):
            await admin.post(url, json={"stepup": {**STEP, "password": "wrong-password-x"}})
        locked = await admin.post(url, json={"stepup": STEP})
        assert locked.status_code == 403 and "too many attempts" in locked.text
    finally:
        await admin.aclose()


async def test_no_token_holds_the_vault_whatever_its_role(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    robot, _tid = await _bearer(app, store, "robot", "admin")
    try:
        sid = (await admin.post("/api/noc/secrets", json=_new())).json()["id"]
        assert (await robot.get("/api/noc/secrets")).json()["custodian"] is False
        assert (await robot.post("/api/noc/secrets", json=_new("z"))).status_code == 403
        reveal = await robot.post(f"/api/noc/secrets/{sid}/reveal", json={"stepup": STEP})
        assert reveal.status_code == 403 and SECRET not in reveal.text
    finally:
        await admin.aclose()
        await robot.aclose()


async def test_an_agent_gets_a_lease_never_a_secret_and_loses_it_when_things_change(
    store: Store,
) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    agent, agent_id = await _bearer(app, store, "diagnoser", "viewer")
    other, other_id = await _bearer(app, store, "intruder", "viewer")
    try:
        sid = (await admin.post("/api/noc/secrets", json=_new())).json()["id"]
        lease_url = f"/api/noc/secrets/{sid}/lease"
        assert (await agent.get("/api/noc/agent")).json()["agent"] is False
        assert (await agent.post(lease_url, json={})).status_code == 403, "not an agent yet"
        for tid in (agent_id, other_id):
            named = await admin.post(
                "/api/noc/grants",
                json={"subject": f"token:{tid}", "role": "agent", "enabled": True, "stepup": STEP},
            )
            assert named.status_code == 200, named.text
        assert (await agent.post(lease_url, json={})).status_code == 404, "agents are off"
        switch = f"/api/noc/secrets/{sid}/agents"
        assert (await admin.post(switch, json={"enabled": True})).status_code == 400
        assert (await admin.post(switch, json={"enabled": True, "stepup": STEP})).status_code == 200
        offered = (await agent.get("/api/noc/agent")).json()
        assert [s["id"] for s in offered["secrets"]] == [sid] and SECRET not in json.dumps(offered)

        issued = await agent.post(lease_url, json={"ttl_s": 120, "purpose": "situation 12"})
        assert issued.status_code == 200 and SECRET not in issued.text
        value = issued.json()["lease"]
        check = await agent.post("/api/noc/leases/introspect", json={"lease": value})
        assert check.json()["active"] is True and check.json()["scope"] == "root"
        assert SECRET not in check.text
        stolen = await other.post("/api/noc/leases/introspect", json={"lease": value})
        assert stolen.json() == {"active": False, "reason": "issued to another agent"}
        forged = await agent.post("/api/noc/leases/introspect", json={"lease": value[:-3] + "AAA"})
        assert forged.json()["active"] is False

        # A new password ends the lease: it was issued for the credential as it was.
        edit = {**_new(), "fields": {"username": "noc", "password": "rotated-password!!"}}
        assert (
            await admin.post(f"/api/noc/secrets/{sid}", json={**edit, "stepup": STEP})
        ).status_code == 200
        ended = await agent.post("/api/noc/leases/introspect", json={"lease": value})
        assert ended.json() == {"active": False, "reason": "revoked"}

        second = (await agent.post(lease_url, json={})).json()["lease"]
        assert (await admin.post(switch, json={"enabled": False})).json()["leases_ended"] == 1
        assert (await agent.post("/api/noc/leases/introspect", json={"lease": second})).json()[
            "active"
        ] is False
        actions = [r["action"] for r in await _audit(store)]
        for action in ("noc.lease.issue", "noc.lease.use", "noc.secret.agents", "noc.grant.change"):
            assert action in actions, action
        uses = [r for r in await _audit(store) if r["action"] == "noc.lease.use"]
        assert {r["actor"] for r in uses} == {"diagnoser", "intruder"}
        assert [r["outcome"] for r in uses if r["actor"] == "intruder"] == ["denied"]
    finally:
        for client in (admin, agent, other):
            await client.aclose()


async def test_a_rewritten_host_does_not_open_and_a_real_edit_re_seals(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        sid = (await admin.post("/api/noc/secrets", json=_new())).json()["id"]
        moved = {**_new(host="10.9.9.9"), "fields": None, "stepup": STEP}
        assert (await admin.post(f"/api/noc/secrets/{sid}", json=moved)).status_code == 200
        shown = await admin.post(f"/api/noc/secrets/{sid}/reveal", json={"stepup": STEP})
        assert shown.json()["fields"]["password"] == SECRET, "re-sealed under the new host"
        async with store.lock:
            await store.conn.execute("UPDATE noc_secret SET host='203.0.113.66' WHERE id=?", (sid,))
            await store.commit()
        refused = await admin.post(f"/api/noc/secrets/{sid}/reveal", json={"stepup": STEP})
        assert refused.status_code == 409 and SECRET not in refused.text
        gone = await admin.post(f"/api/noc/secrets/{sid}/delete", json={"stepup": STEP})
        assert gone.status_code == 200
        assert (await admin.get(f"/api/noc/secrets/{sid}")).status_code == 404
    finally:
        await admin.aclose()


@pytest.mark.parametrize(
    ("greeting", "state"), [(b"SSH-2.0-test\r\n", "up"), (b"HTTP/1.1 400\r\n", "down")]
)
async def test_check_now_follows_a_real_connection(
    store: Store, greeting: bytes, state: str
) -> None:
    async def answer(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(greeting)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(answer, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        sid = (await admin.post("/api/noc/secrets", json=_new(host="127.0.0.1", port=port))).json()[
            "id"
        ]
        checked = await admin.post(f"/api/noc/secrets/{sid}/check")
        assert checked.status_code == 200
        assert checked.json()["status"]["state"] == state, checked.json()["status"]
    finally:
        await admin.aclose()
        server.close()
        await server.wait_closed()
