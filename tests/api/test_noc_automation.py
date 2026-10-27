"""The executor through the API, and a playbook running a case end to end (ADRs #462, #463).

Against a real SSH server in process: an agent's lease becomes one read-only command and its
answer — never the credential; the host key is pinned; every act is audited under the agent's
name and, when a case is named, posted to its feed. A playbook matching a new case runs before the
NOC is called: the owner's example — the alarm is no longer active on the device, so the case is
resolved and its traps cleared — and, when the device says otherwise, the case reaches the queue.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import asyncssh
import httpx
import pytest

from netcorenoc.api import case_close
from netcorenoc.engine.noc import case_loop, playbook, runbook
from netcorenoc.store import Store

import authutil
import util

PASSWORD = "Backbone-r00t-only-the-executor-sees"
STEP = {"username": "adm", "password": authutil.PW}
ANSWER = {"show alarm list active-alarms type ALL": "No active alarms\n"}


class _Server(asyncssh.SSHServer):
    def begin_auth(self, username: str) -> bool:
        return True

    def password_auth_supported(self) -> bool:
        return True

    def validate_password(self, username: str, password: str) -> bool:
        return username == "noc" and password == PASSWORD


async def _answer(process: Any) -> None:
    process.stdout.write(ANSWER.get(process.command or "", "% unknown\n"))
    process.exit(0)


@pytest.fixture
async def device_port() -> AsyncIterator[int]:
    key = asyncssh.generate_private_key("ssh-ed25519")
    server = await asyncssh.create_server(
        _Server, "127.0.0.1", 0, server_host_keys=[key], process_factory=_answer
    )
    try:
        yield int(server.sockets[0].getsockname()[1])
    finally:
        server.close()
        await server.wait_closed()


async def _agent_with_access(
    store: Store, app: Any, port: int
) -> tuple[httpx.AsyncClient, int, str]:
    """A credential for the device, agents switched on, a token named an agent; its client and
    the credential's id and token ref."""
    admin = await authutil.client_as(app, "admin")
    try:
        made = await admin.post(
            "/api/noc/secrets",
            json={
                "name": "device ssh",
                "kind": "ssh_password",
                "access_level": "view_only",
                "host": "127.0.0.1",
                "port": port,
                "monitor": False,
                "fields": {"username": "noc", "password": PASSWORD},
            },
        )
        sid = int(made.json()["id"])
        on = await admin.post(
            f"/api/noc/secrets/{sid}/agents", json={"enabled": True, "stepup": STEP}
        )
        assert on.status_code == 200, on.text
        # Admin-role: writing playbooks is an admin's power (ADR #463); narrowed in production.
        value = await authutil.make_token(store, "diagnoser", "admin")
        async with store.reading():
            token_id = next(
                int(t["id"]) for t in await store.list_tokens() if t["name"] == "diagnoser"
            )
        named = await admin.post(
            "/api/noc/grants",
            json={"subject": f"token:{token_id}", "role": "agent", "enabled": True, "stepup": STEP},
        )
        assert named.status_code == 200, named.text
    finally:
        await admin.aclose()
    agent = authutil.new_client(app)
    agent.headers["Authorization"] = f"Bearer {value}"
    return agent, sid, f"token:{token_id}"


async def test_an_agent_runs_a_read_only_command_with_its_lease(
    store: Store, device_port: int
) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    agent, secret_id, _ref = await _agent_with_access(store, app, device_port)
    try:
        lent = (
            await agent.post(
                f"/api/noc/secrets/{secret_id}/lease", json={"ttl_s": 600, "purpose": "diagnose"}
            )
        ).json()
        ran = await agent.post(
            "/api/noc/exec",
            json={
                "lease": lent["lease"],
                "action": "ssh",
                "command": "show alarm list active-alarms type ALL",
            },
        )
        assert ran.status_code == 200, ran.text
        body = ran.json()
        assert body["ok"] and body["output"] == {"untrusted": True, "text": "No active alarms\n"}
        assert PASSWORD not in ran.text
        async with store.reading():
            pinned = await store.noc_get_secret(secret_id, sealed=True)
        assert pinned is not None and str(pinned["host_key"]).startswith("ssh-ed25519 ")
        refused = await agent.post(
            "/api/noc/exec", json={"lease": lent["lease"], "action": "ssh", "command": "reload"}
        )
        assert refused.status_code == 400 and "read-only" in refused.json()["detail"]
        forged = await agent.post(
            "/api/noc/exec", json={"lease": lent["lease"] + "x", "action": "reach"}
        )
        assert forged.status_code == 403
        async with store.reading():
            actions = [r["action"] for r in await store.audit_all()]
        assert actions.count("noc.exec") == 1, "a refused command never reached the device"
    finally:
        await agent.aclose()


async def _open_case(store: Store, device: str) -> int:
    now = time.time()
    async with store.lock:
        sid = await store.create_situation(now, None)
        alarm = await store.ingest(util.event(device=device, ts=now))
        await store.add_alarm_to_situation(sid, alarm.alarm_id)
        await store.promote_situation(sid, now, "promote")
        await store.commit()
    return sid


PLAYBOOK: dict[str, Any] = {
    "name": "Alarm already gone",
    "match": {"trap_oids": ["1.3.6.1.4.1"]},
    "timeout_s": 60,
    "start": "alarms",
    "steps": {
        "alarms": {
            "do": {"ssh": {"command": "show alarm list active-alarms type ALL"}},
            "next": [{"if": {"contains": "No active alarms"}, "go": "clear"}, {"go": "noc"}],
        },
        "clear": {
            "end": "resolve",
            "cause": "cleared_before_isolation",
            "solution": "Not active on the device; the clear trap never arrived.",
            "clear_alarms": True,
        },
        "noc": {"end": "queue", "note": "Still active on the device."},
    },
}


async def test_a_playbook_resolves_a_case_whose_alarm_is_gone_and_queues_one_that_is_not(
    store: Store, device_port: int
) -> None:
    engine, _queue, app = await authutil.make_env(store)
    agent, _secret, _ref = await _agent_with_access(store, app, device_port)
    try:
        stored = await agent.post("/api/noc/playbooks", json={"document": PLAYBOOK})
        assert stored.status_code == 200, stored.text
        bad = await agent.post(
            "/api/noc/playbooks",
            json={
                "document": {
                    **PLAYBOOK,
                    "steps": {
                        **PLAYBOOK["steps"],
                        "alarms": {
                            "do": {"ssh": {"command": "configure terminal"}},
                            "next": [{"go": "noc"}],
                        },
                    },
                }
            },
        )
        assert bad.status_code == 400 and "read-only" in bad.json()["detail"]
    finally:
        await agent.aclose()
    broker = case_close.automation_broker(store, engine)
    gone = await _open_case(store, "127.0.0.1")
    await case_loop.tick(store, automation=runbook.automation)
    async with store.reading():
        assert (await store.noc_case(gone) or {})["status"] == "auto"
    assert await runbook.run_case(store, gone, broker) == "resolved"
    async with store.reading():
        case = await store.noc_case(gone)
        posts = await store.noc_posts(gone)
        actions = [r["action"] for r in await store.audit_all()]
    assert case is not None and case["status"] == "resolved" and case["resolution"] == "automation"
    assert case["alarms_cleared"] and case["situation_status"] == "resolved"
    assert any("No active alarms" in p["body"] for p in posts), "the device's answer is in the feed"
    assert {"noc.exec", "noc.playbook.run", "alarm.clear"} <= set(actions)
    ANSWER["show alarm list active-alarms type ALL"] = "Port 3/4 LOS critical\n"
    try:
        burning = await _open_case(store, "127.0.0.1")
        await case_loop.tick(store, automation=runbook.automation)
        assert await runbook.run_case(store, burning, broker) == "queue"
        async with store.reading():
            queued = await store.noc_case(burning)
            cycles = await store.noc_case_cycles(burning)
        assert queued is not None and queued["status"] == "queue"
        assert [c["reason"] for c in cycles] == ["automation"]
    finally:
        ANSWER["show alarm list active-alarms type ALL"] = "No active alarms\n"


async def test_a_conclusion_that_cannot_be_applied_sends_the_case_to_the_noc(store: Store) -> None:
    """Left `auto`, a case whose leaf failed would be run again every two seconds, against the
    device each time; it goes to the queue instead, and the run is recorded as an error."""
    document = {
        "name": "Resolve at once",
        "match": {"trap_oids": ["1.3.6.1.4.1"]},
        "start": "done",
        "steps": {
            "done": {
                "end": "resolve",
                "cause": "cleared_before_isolation",
                "solution": "Nothing active on {ip}.",
            }
        },
    }
    async with store.lock:
        await store.noc_create_playbook(
            "Resolve at once", playbook.canonical(playbook.validate(document)), None, 0.0, "t"
        )
        await store.commit()

    async def refuse(sid: int, clear: bool, actor: str) -> bool:
        raise RuntimeError("the broker is unavailable")

    sid = await _open_case(store, "127.0.0.1")
    await case_loop.tick(store, automation=runbook.automation)
    broker = runbook.Broker(close=refuse, forget=lambda sid: None)
    assert await runbook.run_case(store, sid, broker) == "error"
    async with store.reading():
        case = await store.noc_case(sid)
        runs = await store.noc_runs(situation_id=sid)
    assert case is not None and case["status"] == "queue" and case["resolution"] is None
    assert [r["outcome"] for r in runs] == ["error"]
