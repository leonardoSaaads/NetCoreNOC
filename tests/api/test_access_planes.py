"""ADR #508 — a person reaches the brokers and the NOC customers they are given, separately.

The owner's words: a person may reach brokers and not the NOC, or the reverse; a viewer, an editor
or an admin limited to Claro sees nothing of Vivo — not a broker, not a customer, not a case; and
one super admin always reaches everything, whoever tries to take it away, themselves included.
"""

from __future__ import annotations

import json
from typing import Any

import test_customer_access as ca

from netcorenoc.crosscutting import shaping
from netcorenoc.store import Store

import authutil

PW = authutil.PW


def test_the_two_planes_resolve_separately() -> None:
    nes = [
        {"id": 1, "ip": "10.0.0.1", "broker_id": 2, "organization_id": 5},
        {"id": 2, "ip": "10.0.0.2", "broker_id": 3, "organization_id": 6},
    ]
    doc = {
        "version": 1,
        "principals": {
            "user:1": ["broker:2", "noc:none"],
            "user:2": ["noc:6"],
            "user:3": ["broker:*", "noc:*"],
            "user:4": ["broker:2"],
        },
    }
    policy = shaping.parse_scope_policy(json.dumps(doc))
    broker_only = shaping.visible_nes("viewer", "user:1", policy, nes)
    assert broker_only.ne_ids == {1} and broker_only.broker_plane
    assert not broker_only.noc_plane and broker_only.for_noc().ne_ids == frozenset()
    noc_only = shaping.visible_nes("editor", "user:2", policy, nes)
    assert noc_only.ne_ids == frozenset() and not noc_only.broker_plane
    assert noc_only.for_noc().ne_ids == {2} and noc_only.for_noc().allows_org(6)
    assert not noc_only.for_noc().allows_org(5)
    everything = shaping.visible_nes("viewer", "user:3", policy, nes)
    assert everything.ne_ids == {1, 2} and everything.for_noc().unrestricted
    legacy = shaping.visible_nes("viewer", "user:4", policy, nes)
    assert legacy.noc is None and legacy.for_noc() is legacy, "no noc: selector — as before"
    # An admin is limited by its own entry; one with no entry, and the owner, are not.
    assert not shaping.visible_nes("admin", "user:1", policy, nes).unrestricted
    assert shaping.visible_nes("admin", "user:9", policy, nes).unrestricted
    assert shaping.never_scoped("admin", "user:1", True, policy)
    assert not shaping.scope_policy_errors(policy)


async def _limited(
    store: Store, env: dict[str, Any], username: str, role: str, **planes: list[int] | None
) -> Any:
    made = await env["admin"].post(
        "/api/users", json={"username": username, "password": PW, "role": role, **planes}
    )
    assert made.status_code == 200, made.text
    async with store.lock:
        user = await store.get_user_by_name(username)
        assert user is not None
        await store.set_must_change_password(int(user["id"]), False, 0.0)
        await store.commit()
    await authutil.enroll(store, username)
    client = authutil.new_client(app := env["app"])
    _ = app
    signed = await authutil.sign_in(client, username, PW)
    assert signed.status_code == 200, signed.text
    return client


async def test_brokers_without_the_noc_and_the_noc_without_brokers(store: Store) -> None:
    env = await ca._estate(store)
    admin, orgs, brokers = env["admin"], env["orgs"], env["brokers"]
    watcher = await _limited(
        store, env, "claro-watch", "viewer", brokers=[brokers["Claro Nordeste"]], customers=[]
    )
    worker = await _limited(store, env, "vivo-noc", "editor", brokers=[], customers=[orgs["Vivo"]])
    try:
        me = (await watcher.get("/api/me")).json()["scope"]["planes"]
        assert me == {"broker": True, "noc": False}
        names = {b["name"] for b in (await watcher.get("/api/brokers")).json()["brokers"]}
        assert names == {"Claro Nordeste"}
        assert (await watcher.get("/api/situations")).json(), "Claro Nordeste's situations"
        assert (await watcher.get("/api/noc/customers")).json()["customers"] == []
        assert (await watcher.get(f"/api/noc/customers/{orgs['Claro']}")).status_code == 404
        assert (await watcher.get("/api/noc/cases")).json()["cases"] == []

        me = (await worker.get("/api/me")).json()["scope"]["planes"]
        assert me == {"broker": False, "noc": True}
        assert (await worker.get("/api/brokers")).json()["brokers"] == []
        assert (await worker.get("/api/situations")).json() == []
        listed = (await worker.get("/api/noc/customers")).json()["customers"]
        assert [c["name"] for c in listed] == ["Vivo"]
        assert (await worker.get(f"/api/noc/customers/{orgs['Claro']}")).status_code == 404

        users = {u["username"]: u for u in (await admin.get("/api/users")).json()}
        assert users["claro-watch"]["access"]["brokers"] == [brokers["Claro Nordeste"]]
        assert users["claro-watch"]["access"]["customers"] == []
        assert users["vivo-noc"]["access"] == {
            "brokers": [],
            "customers": [orgs["Vivo"]],
            "organizations": None,
        }
    finally:
        for client in (watcher, worker):
            await client.aclose()


async def test_a_limited_admin_administers_its_own_and_never_the_appliance(store: Store) -> None:
    env = await ca._estate(store)
    orgs, brokers = env["orgs"], env["brokers"]
    claro_admin = await _limited(
        store,
        env,
        "claro-admin",
        "admin",
        brokers=[brokers["Claro Nordeste"], brokers["Claro Sul"]],
        customers=[orgs["Claro"]],
    )
    try:
        caps = set((await claro_admin.get("/api/me")).json()["capabilities"])
        assert not caps & {"users.manage", "scope.write", "config.write", "audit.read"}
        assert "cases.work" in caps
        assert (await claro_admin.get("/api/users")).status_code == 403
        refused = await claro_admin.post(
            "/api/scope/subject", json={"subject": "user:1", "brokers": None, "customers": None}
        )
        assert refused.status_code == 403, "a limited admin cannot widen anyone, itself included"
        assert (
            await claro_admin.post("/api/organizations", json={"name": "Tim", "slug": "tim"})
        ).status_code == 403
        names = {b["name"] for b in (await claro_admin.get("/api/brokers")).json()["brokers"]}
        assert names == {"Claro Nordeste", "Claro Sul"}
        customers = (await claro_admin.get("/api/noc/customers")).json()["customers"]
        assert [c["name"] for c in customers] == ["Claro"]
    finally:
        await claro_admin.aclose()


async def test_the_super_admin_reaches_everything_and_nobody_can_take_it(store: Store) -> None:
    env = await ca._estate(store)
    admin = env["admin"]
    async with store.reading():
        owner = await store.get_user_by_name("admin")  # the bootstrap admin
    assert owner is not None and owner["is_owner"]
    uid = int(owner["id"])
    listed = {u["username"]: u for u in (await admin.get("/api/users")).json()}
    assert listed["admin"]["owner"] is True and listed["admin"]["scopable"] is False
    assert listed["adm"]["owner"] is False and listed["adm"]["scopable"] is True

    limited = await admin.post(
        "/api/scope/subject", json={"subject": f"user:{uid}", "brokers": [], "customers": []}
    )
    assert limited.status_code == 400 and "super admin" in limited.json()["detail"]
    raw = await admin.post(
        "/api/scope",
        json={"document": {"version": 1, "principals": {f"user:{uid}": ["noc:none"]}}},
    )
    assert raw.status_code == 400, "the raw policy cannot name the super admin either"
    demoted = await admin.post(f"/api/users/{uid}/role", json={"role": "viewer"})
    assert demoted.status_code == 400 and "super admin" in demoted.json()["detail"]
    assert (await admin.delete(f"/api/users/{uid}")).status_code == 400
    async with store.lock:
        try:
            await store.conn.execute("UPDATE app_user SET disabled=true WHERE id=?", (uid,))
        except Exception as exc:
            assert "app_user_owner_is_enabled_admin" in str(exc)
        else:
            raise AssertionError("the database let the super admin be disabled")
        finally:
            await store.rollback()
