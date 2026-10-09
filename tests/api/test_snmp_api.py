"""Settings → SNMP over HTTP: `GET/POST /api/snmp` (v0.30.0, ADR #444)."""

from __future__ import annotations

from typing import Any

from netcorenoc.crosscutting.runtime import RuntimeConfig
from netcorenoc.ingest import snmpconf
from netcorenoc.store import Store

import authutil

USER = {
    "name": "noc",
    "auth": "SHA-256",
    "priv": "AES-128",
    "auth_passphrase": "auth-passphrase-1",
    "priv_passphrase": "priv-passphrase-1",
}


async def _env(store: Store) -> tuple[Any, RuntimeConfig, list[snmpconf.SnmpPolicy]]:
    pushed: list[snmpconf.SnmpPolicy] = []
    runtime = RuntimeConfig(allowlist="", retention_days=7.0, on_snmp_change=pushed.append)
    _engine, _queue, app = await authutil.make_env(store, runtime=runtime)
    return app, runtime, pushed


async def test_the_default_accepts_every_v1_and_v2c_trap(store: Store) -> None:
    app, _runtime, _pushed = await _env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        body = (await admin.get("/api/snmp")).json()
    finally:
        await admin.aclose()
    assert (body["v1"], body["v2c"], body["v3"]) == (True, True, True)
    assert body["communities"] == {"any": True, "entries": []}
    assert body["users"] == [] and body["time_window"] is True
    assert "SHA-256" in body["protocols"]["auth"] and "AES-128" in body["protocols"]["priv"]


async def test_a_save_reaches_the_live_receiver_and_never_returns_a_secret(store: Store) -> None:
    app, runtime, pushed = await _env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        saved = await admin.post(
            "/api/snmp",
            json={"users": [USER], "communities": {"any": False, "add": ["s3cret-community"]}},
        )
        assert saved.status_code == 200, saved.text
        body = (await admin.get("/api/snmp")).json()
    finally:
        await admin.aclose()
    expected = {"name": "noc", "auth": "SHA-256", "priv": "AES-128", "level": "authPriv"}
    assert body["users"] == [{**expected, "engine_id": None}]
    entry = body["communities"]["entries"][0]
    assert entry["hint"].startswith("s") and "(16)" in entry["hint"]
    text = saved.text + str(body)
    for secret in ("auth-passphrase-1", "priv-passphrase-1", "s3cret-community"):
        assert secret not in text
    assert pushed and pushed[-1].users[b"noc"].auth_key == snmpconf.hash_passphrase(
        "auth-passphrase-1", "SHA-256"
    )
    assert runtime.snmp is pushed[-1]
    async with store.lock:
        stored = await store.get_meta(snmpconf.META_KEY)
        rows = [r for r in await store.audit_all() if r["action"] == "snmp.config.change"]
    assert stored is not None and "s3cret-community" not in stored
    assert rows and "passphrase" not in rows[-1]["details"]


async def test_a_passphrase_left_out_keeps_the_key_until_the_protocol_changes(
    store: Store,
) -> None:
    app, _runtime, pushed = await _env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        assert (await admin.post("/api/snmp", json={"users": [USER]})).status_code == 200
        kept = {k: v for k, v in USER.items() if "passphrase" not in k}
        assert (await admin.post("/api/snmp", json={"users": [kept]})).status_code == 200
        assert pushed[-1].users[b"noc"].auth_key == pushed[0].users[b"noc"].auth_key
        changed = await admin.post("/api/snmp", json={"users": [{**kept, "auth": "SHA"}]})
        assert changed.status_code == 400
        assert "passphrase is required" in changed.json()["detail"]
    finally:
        await admin.aclose()


async def test_nonsense_is_refused_with_the_reason(store: Store) -> None:
    app, _runtime, _pushed = await _env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        short = await admin.post(
            "/api/snmp", json={"users": [{**USER, "auth_passphrase": "short"}]}
        )
        assert short.status_code == 422
        nothing = await admin.post("/api/snmp", json={"v1": False, "v2c": False, "v3": False})
        assert nothing.status_code == 400 and "no trap would arrive" in nothing.json()["detail"]
        empty = await admin.post("/api/snmp", json={"communities": {"any": False}})
        assert empty.status_code == 400
        bad = await admin.post("/api/snmp", json={"users": [{**USER, "priv": "ROT13"}]})
        assert bad.status_code == 400 and "privacy protocol" in bad.json()["detail"]
        twice = await admin.post("/api/snmp", json={"users": [USER, USER]})
        assert twice.status_code == 400
        engine = await admin.post("/api/snmp", json={"users": [{**USER, "engine_id": "zz"}]})
        assert engine.status_code == 400 and "engine ID" in engine.json()["detail"]
    finally:
        await admin.aclose()


async def test_only_an_admin_reads_or_writes_it(store: Store) -> None:
    app, _runtime, _pushed = await _env(store)
    editor = await authutil.client_as(app, "editor")
    try:
        assert (await editor.get("/api/snmp")).status_code == 403
        assert (await editor.post("/api/snmp", json={})).status_code == 403
    finally:
        await editor.aclose()
