"""ADR #499 — a person limited to customers sees those customers and nothing else.

"Claro - viewer" is made with Claro as its only customer, in the same commit as the account. It
then sees Claro's brokers (one with no element yet too), Claro's situations and map, and Claro's
side of the NOC; Vivo's broker, Vivo's book and Vivo's credentials are the same 404 as a record
that does not exist. An editor limited the same way, named a custodian, holds Claro's credentials
only — never Vivo's and never a shared one. Since ADR #508 an admin may be limited too; only the
super admin never is.
"""

from __future__ import annotations

import json
from typing import Any

from netcorenoc.crosscutting import shaping
from netcorenoc.store import Store

import authutil
import util

BASE = 1_700_000_000.0
PW = authutil.PW
STEP = {"username": "adm", "password": PW, "otp": authutil.OTP}


def test_org_selector_names_a_customers_elements_and_the_customers_it_reaches() -> None:
    nes = [
        {"id": 1, "ip": "10.0.0.1", "broker_id": 2, "organization_id": 5},
        {"id": 2, "ip": "10.0.0.1", "broker_id": 3, "organization_id": 6},
        {"id": 3, "ip": "10.0.0.2", "broker_id": 4, "organization_id": 5},
    ]
    doc = {"version": 1, "principals": {"user:9": ["org:5"], "user:8": ["broker:3"]}}
    policy = shaping.parse_scope_policy(json.dumps(doc))
    claro = shaping.visible_nes("viewer", "user:9", policy, nes)
    assert claro.ne_ids == frozenset({1, 3}), "every element Claro owns, in every broker"
    assert claro.orgs == claro.named_orgs == frozenset({5})
    assert claro.allows_org(5) and not claro.allows_org(6) and not claro.allows_org(None)
    other = shaping.visible_nes("viewer", "user:8", policy, nes)
    assert other.ne_ids == frozenset({2}) and other.orgs == frozenset({6})
    assert other.named_orgs == frozenset(), "a broker selector names no customer's brokers"
    # ADR #508: an admin is limited by its own entry; one without an entry is not.
    assert not shaping.visible_nes("admin", "user:9", policy, nes).unrestricted
    assert shaping.visible_nes("admin", "user:7", policy, nes).unrestricted
    assert shaping.scope_policy_errors(
        shaping.parse_scope_policy(json.dumps({"principals": {"user:1": ["org:claro"]}}))
    ), "a customer is named by its id"


async def _estate(store: Store) -> dict[str, Any]:
    engine, queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    orgs = {}
    for name in ("Claro", "Vivo"):
        made = await admin.post("/api/organizations", json={"name": name, "slug": name.lower()})
        assert made.status_code == 200, made.text
        orgs[name] = int(made.json()["id"])
    brokers = {}
    for name, body in (
        ("Claro Nordeste", {"organization_id": orgs["Claro"], "udp_port": 1162}),
        (
            "Claro Sul",
            {"organization_id": orgs["Claro"], "intake": "cidr", "networks": ["10.9.0.0/16"]},
        ),
        ("Vivo SP", {"organization_id": orgs["Vivo"], "intake": "proxy"}),
    ):
        made = await admin.post("/api/brokers", json={"name": name, **body})
        assert made.status_code == 200, made.text
        brokers[name] = int(made.json()["id"])
    events = util.fixture_events("fiber_cut.json", BASE)
    await util.drive(
        engine,
        queue,
        [e.model_copy(update={"port": 1162}) for e in events]
        + [e.model_copy(update={"broker": brokers["Vivo SP"]}) for e in events],
    )
    return {"app": app, "admin": admin, "orgs": orgs, "brokers": brokers}


async def _person(
    store: Store, app: object, admin: Any, username: str, role: str, orgs: list[int]
) -> Any:
    made = await admin.post(
        "/api/users",
        json={"username": username, "password": PW, "role": role, "organizations": orgs},
    )
    assert made.status_code == 200, made.text
    async with store.lock:  # signed in as the person, after their first password change
        user = await store.get_user_by_name(username)
        assert user is not None
        await store.set_must_change_password(int(user["id"]), False, 0.0)
        await store.commit()
    await authutil.enroll(store, username)
    client = authutil.new_client(app)
    signed = await authutil.sign_in(client, username, PW)
    assert signed.status_code == 200, signed.text
    return client


async def test_claro_viewer_sees_claro_and_nothing_of_vivo(store: Store) -> None:
    env = await _estate(store)
    admin, orgs, brokers = env["admin"], env["orgs"], env["brokers"]
    claro = orgs["Claro"]
    viewer = await _person(store, env["app"], admin, "claro-viewer", "viewer", [claro])

    listed = {u["username"]: u for u in (await admin.get("/api/users")).json()}
    assert listed["claro-viewer"]["organizations"] == [claro]
    assert listed["claro-viewer"]["scopable"] is True
    # ADR #508: only the super admin (the first admin) cannot be limited.
    assert listed["admin"]["scopable"] is False and listed["admin"]["owner"] is True
    assert listed["adm"]["scopable"] is True and listed["adm"]["organizations"] is None

    names = {b["name"] for b in (await viewer.get("/api/brokers")).json()["brokers"]}
    assert names == {"Claro Nordeste", "Claro Sul"}, "Claro's, the one with no element too"
    assert (await viewer.get(f"/api/brokers/{brokers['Vivo SP']}")).status_code == 404
    seen = (await viewer.get("/api/situations")).json()
    async with store.reading():
        of = await store.situation_brokers([int(s["id"]) for s in seen])
    assert seen and set(of.values()) == {brokers["Claro Nordeste"]}
    async with store.reading():
        cur = await store.conn.execute("SELECT id FROM ne WHERE organization_id = ?", (claro,))
        mine = {int(r[0]) for r in await cur.fetchall()}
    assert {n["id"] for n in (await viewer.get("/api/graph")).json()["nodes"]} == mine

    customers = (await viewer.get("/api/noc/customers")).json()["customers"]
    assert [c["name"] for c in customers] == ["Claro"]
    assert (await viewer.get(f"/api/noc/customers/{orgs['Vivo']}")).status_code == 404
    assert (await viewer.get(f"/api/noc/customers/{claro}")).status_code == 200
    for case in (await viewer.get("/api/noc/cases")).json()["cases"]:
        assert int(case["situation_id"]) in {int(s["id"]) for s in seen}

    # Every customer again: the entry's customers removed, through the one-subject route.
    ref = f"user:{listed['claro-viewer']['id']}"
    cleared = await admin.post("/api/scope/subject", json={"subject": ref, "organizations": None})
    assert cleared.status_code == 200, cleared.text
    # ADR #507: every customer — the Default one too.
    assert len((await viewer.get("/api/noc/customers")).json()["customers"]) == 3


async def test_the_limits_are_an_admins_and_never_an_admins_own(store: Store) -> None:
    env = await _estate(store)
    admin, orgs = env["admin"], env["orgs"]
    users = {u["username"]: u for u in (await admin.get("/api/users")).json()}
    for body, status in (
        # ADR #508: the super admin is never limited; any other admin may be (not done here: the
        # client below is `adm`, and limiting it would take its own administration away).
        ({"subject": f"user:{users['admin']['id']}", "organizations": [orgs["Claro"]]}, 400),
        ({"subject": "user:99999", "organizations": [orgs["Claro"]]}, 404),
        # ADR #507: the Default customer is a customer like any other.
        ({"subject": f"user:{users['vwr']['id']}", "organizations": [1]}, 200),
        ({"subject": f"user:{users['vwr']['id']}", "organizations": []}, 400),  # sees nothing
        ({"subject": f"user:{users['vwr']['id']}", "organizations": [orgs["Vivo"]]}, 200),
    ):
        resp = await admin.post("/api/scope/subject", json=body)
        assert resp.status_code == status, (body, resp.text)
    # ADR #508: an admin may be made limited, in the same commit as the account.
    made = await admin.post(
        "/api/users",
        json={"username": "x", "password": PW, "role": "admin", "organizations": [orgs["Claro"]]},
    )
    assert made.status_code == 200, made.text
    listed = {u["username"]: u for u in (await admin.get("/api/users")).json()}
    assert listed["x"]["organizations"] == [orgs["Claro"]]
    editor = await authutil.client_as(env["app"], "editor")
    denied = await editor.post(
        "/api/scope/subject", json={"subject": "user:1", "organizations": [orgs["Claro"]]}
    )
    assert denied.status_code == 403
    assert any(r["action"] == "scope.policy.update" for r in await _audit(store))


async def test_a_limited_custodian_holds_only_its_customers_credentials(store: Store) -> None:
    env = await _estate(store)
    admin, orgs = env["admin"], env["orgs"]
    made = {}
    for name, customer in (
        ("claro-core", orgs["Claro"]),
        ("vivo-core", orgs["Vivo"]),
        ("shared", None),
    ):
        resp = await admin.post(
            "/api/noc/secrets",
            json={
                "name": name,
                "kind": "ssh_password",
                "access_level": "diagnostic",
                "host": "10.0.0.1",
                "customer_id": customer,
                "fields": {"username": "noc", "password": "only-the-vault-knows"},
            },
        )
        assert resp.status_code == 200, resp.text
        made[name] = int(resp.json()["id"])
    editor = await _person(store, env["app"], admin, "claro-ops", "editor", [orgs["Claro"]])
    people = (await admin.get("/api/noc/grants")).json()["people"]
    ref = next(p["ref"] for p in people if p["username"] == "claro-ops")
    named = await admin.post(
        "/api/noc/grants",
        json={"subject": ref, "role": "custodian", "enabled": True, "stepup": STEP},
    )
    assert named.status_code == 200, named.text

    held = {s["name"] for s in (await editor.get("/api/noc/secrets")).json()["secrets"]}
    assert held == {"claro-core"}, "never another customer's, never a shared one"
    assert (await editor.get(f"/api/noc/secrets/{made['claro-core']}")).status_code == 200
    for other in ("vivo-core", "shared"):
        assert (await editor.get(f"/api/noc/secrets/{made[other]}")).status_code == 404
    filed = await editor.post(
        "/api/noc/secrets",
        json={
            "name": "vivo-2",
            "kind": "ssh_password",
            "access_level": "diagnostic",
            "host": "10.0.0.2",
            "customer_id": orgs["Vivo"],
            "fields": {"username": "noc", "password": "x"},
        },
    )
    assert filed.status_code == 400, "a credential is filed under one of its own customers"


async def _audit(store: Store) -> list[dict[str, Any]]:
    async with store.reading():
        return await store.audit_all()
