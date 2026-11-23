"""ADR #494 — the remote proxy: nothing lost while its link is down, nothing spooled twice.

* traps are on the proxy's disk before they are forwarded; a link that fails, or an answer that is
  lost after the server took the batch, costs neither a trap nor a duplicate;
* a restarted proxy resumes after the last batch the server confirmed; a reinstalled one starts a
  new stream, which the server numbers from 1 again;
* the batch the proxy sends is the one the appliance's door accepts, end to end;
* the token only travels encrypted.
"""

from __future__ import annotations

import asyncio
import base64
import json
import shutil
import socket
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from netcorenoc.ingest import forwarder, records
from netcorenoc.store import Store

import authutil


class FakeServer:
    """The door's numbering rule, and a link that can fail before or after the server takes it."""

    def __init__(self) -> None:
        self.seq: dict[str, int] = {}
        self.spooled: list[bytes] = []
        self.down = False
        self.lose_answer = False

    def post(self, body: bytes) -> tuple[int, dict[str, object]]:
        if self.down:
            raise ConnectionError("link down")
        batch = json.loads(body)
        seen = self.seq.get(batch["stream"], 0)
        fresh = max(0, batch["last"] - max(seen, batch["first"] - 1))
        payloads = [base64.b64decode(r) for r in batch["records"]]
        self.spooled.extend(payloads[len(payloads) - fresh :] if fresh else [])
        self.seq[batch["stream"]] = max(seen, batch["last"])
        if self.lose_answer:
            raise ConnectionError("the answer was lost")
        return 200, {"accepted": batch["last"]}


def _config(tmp_path: Path) -> forwarder.ProxyConfig:
    return forwarder.ProxyConfig(
        server="http://127.0.0.1:8080", token="ncp_test", spool_dir=tmp_path / "spool"
    )


def _datagrams(start: int, count: int) -> list[tuple[bytes, str, int]]:
    return [(b"\x30\x03" + i.to_bytes(4, "big"), "10.0.0.1", 40000) for i in range(start, count)]


def _ids(payloads: list[bytes]) -> list[int]:
    return [int.from_bytes(records.unraw(p)[3][2:6], "big") for p in payloads]


def _drain(proxy: forwarder.Forwarder) -> None:
    while proxy.send_once():
        pass


def test_a_link_that_drops_loses_nothing_and_duplicates_nothing(tmp_path: Path) -> None:
    server = FakeServer()
    proxy = forwarder.Forwarder(_config(tmp_path), post=server.post)
    try:
        for start in range(0, 1200, 100):
            proxy.admit(_datagrams(start, start + 100))
        server.down = True
        with pytest.raises(ConnectionError):
            proxy.send_once()
        server.down = False
        server.lose_answer = True  # the server takes the batch; the proxy never hears so
        with pytest.raises(ConnectionError):
            proxy.send_once()
        server.lose_answer = False
        _drain(proxy)
        assert _ids(server.spooled) == list(range(1200)), "each trap once, in order"
        assert proxy.cursor == 1200
    finally:
        proxy.close()


def test_a_restart_resumes_and_a_reinstall_starts_a_new_stream(tmp_path: Path) -> None:
    server = FakeServer()
    proxy = forwarder.Forwarder(_config(tmp_path), post=server.post)
    proxy.admit(_datagrams(0, 300))
    proxy.send_once()  # 300 records: one batch
    proxy.admit(_datagrams(300, 400))  # on disk, not yet sent
    stream = proxy.stream
    proxy.close()

    again = forwarder.Forwarder(_config(tmp_path), post=server.post)
    try:
        assert (again.stream, again.cursor) == (stream, 300)
        _drain(again)
        assert _ids(server.spooled) == list(range(400))
    finally:
        again.close()

    shutil.rmtree(tmp_path / "spool")  # the proxy reinstalled: its numbers start at 1 again
    fresh = forwarder.Forwarder(_config(tmp_path), post=server.post)
    try:
        assert fresh.stream != stream
        fresh.admit(_datagrams(400, 410))
        _drain(fresh)
        assert _ids(server.spooled) == list(range(410)), "a new stream is not a repeat"
    finally:
        fresh.close()


def test_traps_reach_the_spool_through_the_socket(tmp_path: Path) -> None:
    proxy = forwarder.Forwarder(_config(tmp_path), post=FakeServer().post)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    thread = threading.Thread(target=proxy.receive, args=(sock,), daemon=True)
    thread.start()
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for i in range(200):
            sender.sendto(b"\x30\x03" + i.to_bytes(4, "big"), ("127.0.0.1", port))
        deadline = time.monotonic() + 5
        while proxy.received < 200 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert proxy.received == 200
    finally:
        proxy.stop.set()
        thread.join(timeout=3)
        sender.close()
        sock.close()
        proxy.close()


def test_the_token_only_travels_encrypted() -> None:
    assert forwarder.check_server("https://noc.example.com/") == "https://noc.example.com"
    assert forwarder.check_server("http://127.0.0.1:8080") == "http://127.0.0.1:8080"
    for bad in ("http://noc.example.com", "ftp://noc.example.com", "https://"):
        with pytest.raises(ValueError):
            forwarder.check_server(bad)


async def test_the_proxys_batch_is_the_one_the_appliance_accepts(
    store: Store, tmp_path: Path
) -> None:
    engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    made = await admin.post(
        "/api/brokers", json={"name": "Remote", "organization_id": 1, "intake": "proxy"}
    )
    broker = made.json()
    spooled: list[bytes] = []

    async def spool(batch: list[bytes]) -> bool:
        spooled.extend(batch)
        return True

    engine.proxy_spool = spool
    door = authutil.new_client(app)
    loop = asyncio.get_running_loop()

    def post(body: bytes) -> tuple[int, dict[str, Any]]:
        call = door.post(
            "/api/brokers/ingest",
            content=body,
            headers={
                "Authorization": f"Bearer {broker['token']}",
                "Content-Type": "application/json",
            },
        )
        response = asyncio.run_coroutine_threadsafe(call, loop).result(timeout=10)
        return response.status_code, response.json()

    config = forwarder.ProxyConfig(
        server="http://127.0.0.1", token=broker["token"], spool_dir=tmp_path / "spool"
    )
    proxy = forwarder.Forwarder(config, post=post)
    try:
        proxy.admit(_datagrams(0, 50))
        assert await asyncio.to_thread(proxy.send_once) == 50
    finally:
        proxy.close()
    assert len(spooled) == 50
    assert {records.envelope(p)[1] for p in spooled} == {broker["id"]}
    async with store.reading():
        row = await store.broker(int(broker["id"]))
    assert row is not None and row["proxy_seq"] == 50


def test_a_quiet_proxy_says_it_is_alive(tmp_path: Path) -> None:
    server = FakeServer()
    proxy = forwarder.Forwarder(_config(tmp_path), post=server.post)
    try:
        proxy.admit(_datagrams(0, 3))
        proxy.send_once()
        proxy.heartbeat()  # nothing new: an empty batch after the last one sent
        assert server.seq[proxy.stream] == 3 and len(server.spooled) == 3
    finally:
        proxy.close()
