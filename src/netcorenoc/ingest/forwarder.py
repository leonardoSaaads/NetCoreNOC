"""The remote proxy: a customer's traps, spooled where they arrive, forwarded over HTTPS (ADR #494).

A broker in **proxy** mode is a network this appliance cannot hear directly — behind a customer's
firewall, across a WAN. `python -m netcorenoc proxy` runs there, like a Zabbix proxy:

* it **receives** the traps on the local UDP port and writes each, raw, to its own spool before
  anything else (the same spool, the same record, the same fsync as the appliance — ADR #450), so a
  WAN link that drops loses nothing: the traps wait on the proxy's disk;
* it **forwards** them in numbered batches to `POST /api/brokers/ingest`, authenticated by the
  broker's token, and moves its cursor only once the server answered that the batch is durable
  there. The numbers are the spool's own; a batch sent again because the answer was lost is
  recognised by the server and not spooled twice. A reinstalled proxy starts a new **stream**.

It does not parse the traps: the appliance's decoder does that, with its SNMP policy and its
catalogue, so the proxy carries no configuration that could disagree with the appliance. It never
sends its token anywhere but the server named, and only over HTTPS (or to a loopback address, for a
test).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from netcorenoc.ingest import records, spool
from netcorenoc.ingest.receiver import parse_allowlist
from netcorenoc.ingest.worker import allowed

__all__ = ["BACKOFF_S", "Forwarder", "ProxyConfig", "check_server", "run"]

log = logging.getLogger("netcorenoc.proxy")

#: How long to wait before sending again after a failure, step by step, then the last for ever.
BACKOFF_S = (1.0, 2.0, 5.0, 10.0, 30.0, 60.0)
#: With nothing to send for this long, the proxy says it is alive (an empty batch), so the server
#: can tell a quiet network from a proxy that is down — Zabbix's proxy heartbeat, for that reason.
HEARTBEAT_S = 60.0
#: The most records in one batch, and the most base64 characters: well inside the server's 1 MiB.
BATCH_RECORDS = 500
BATCH_CHARS = 600_000
STREAM_FILE = "stream"
CURSOR_FILE = "cursor"

#: ``post(body) -> (status, answer)``: one batch to the server. Injected by a test.
Post = Callable[[bytes], tuple[int, dict[str, object]]]


@dataclass(frozen=True)
class ProxyConfig:
    server: str
    token: str
    spool_dir: Path
    host: str = "0.0.0.0"  # nosec B104 - a trap receiver listens on the network it serves
    port: int = 162
    max_bytes: int = 1024 * 1024 * 1024
    ca_file: str | None = None
    allowlist: str = ""
    version: str = ""


def check_server(url: str) -> str:
    """The server's base URL, refused unless the token would travel encrypted."""
    parts = urlsplit(url)
    loopback = parts.hostname in {"localhost", "127.0.0.1", "::1"}
    if parts.scheme != "https" and not (parts.scheme == "http" and loopback):
        raise ValueError("the server must be an https:// URL: the proxy's token travels with it")
    if not parts.hostname:
        raise ValueError(f"{url!r} names no host")
    return url.rstrip("/")


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None


def _write(path: Path, text: str) -> None:
    """Replace a small file durably: a temporary, fsynced, then renamed over it."""
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, text.encode("ascii"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temp, path)


class Forwarder:
    """The proxy's two halves over one spool: `receive` (one thread) and `forward` (another)."""

    def __init__(self, config: ProxyConfig, post: Post | None = None) -> None:
        self.config = config
        self.server = check_server(config.server)
        config.spool_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        stream_path = config.spool_dir / STREAM_FILE
        self.stream = _read(stream_path) or ""
        if not self.stream:
            self.stream = secrets.token_hex(16)
            _write(stream_path, self.stream)
        self.cursor = int(_read(config.spool_dir / CURSOR_FILE) or 0)
        self.writer = spool.SpoolWriter(config.spool_dir, config.max_bytes)
        self.reader = spool.SpoolReader(config.spool_dir, self.cursor)
        self.networks = parse_allowlist(config.allowlist)
        self.post = post or self._https
        self.pending: list[tuple[int, bytes]] = []
        self.received = 0
        self.dropped = 0  # the spool was full: the oldest unsent traps are kept, the newest lost
        self.forwarded = 0
        self.failures = 0
        self.stop = threading.Event()

    # -- receiving ------------------------------------------------------------------------------

    def admit(self, datagrams: list[tuple[bytes, str, int]]) -> None:
        """Spool what arrived, raw, durable on return. Thread-confined: the receiving thread."""
        now = time.time()
        payloads = [
            records.raw(source, port, now, data)
            for data, source, port in datagrams
            if allowed(self.networks, source)
        ]
        if not payloads:
            return
        try:
            self.writer.append(payloads)
            self.received += len(payloads)
        except spool.SpoolFullError:
            self.dropped += len(payloads)

    def receive(self, sock: socket.socket) -> None:
        """Read the socket until stopped, spooling each burst in one group commit."""
        sock.settimeout(0.5)
        last_measure = time.monotonic()
        while not self.stop.is_set():
            try:
                data, (source, port, *_rest) = sock.recvfrom(65535)
            except TimeoutError:
                continue
            burst = [(data, source, port)]
            sock.setblocking(False)
            try:
                while len(burst) < 256:
                    data, (source, port, *_rest) = sock.recvfrom(65535)
                    burst.append((data, source, port))
            except BlockingIOError:
                pass
            finally:
                sock.settimeout(0.5)
            self.admit(burst)
            if time.monotonic() - last_measure > 5.0:
                self.writer.refresh_usage()  # the forwarding thread deletes what was sent
                last_measure = time.monotonic()

    # -- forwarding -----------------------------------------------------------------------------

    def _batch(self) -> list[tuple[int, bytes]]:
        if len(self.pending) < BATCH_RECORDS:
            self.pending.extend(self.reader.read(BATCH_RECORDS - len(self.pending)))
        batch: list[tuple[int, bytes]] = []
        chars = 0
        for seq, payload in self.pending:
            size = (len(payload) + 2) // 3 * 4
            if batch and chars + size > BATCH_CHARS:
                break
            batch.append((seq, payload))
            chars += size
        return batch

    def send_once(self) -> int:
        """Send the next batch; the number of records the server confirmed (0: nothing to send).

        Raises `ConnectionError` when the server did not take it — the same batch goes next time.
        """
        batch = self._batch()
        if not batch:
            return 0
        body = json.dumps(
            {
                "stream": self.stream,
                "first": batch[0][0],
                "last": batch[-1][0],
                "version": self.config.version[:40],
                "records": [base64.b64encode(p).decode("ascii") for _seq, p in batch],
            }
        ).encode()
        status, answer = self.post(body)
        if status == 422:
            # The server cannot read these records and never will: keep them for an administrator
            # and move on, rather than send them for ever.
            for seq, payload in batch:
                spool.dead_letter(self.config.spool_dir, seq, payload, f"refused: {answer}")
        elif status != 200 or answer.get("accepted") != batch[-1][0]:
            raise ConnectionError(f"the server answered {status}: {answer}")
        del self.pending[: len(batch)]
        self.cursor = batch[-1][0]
        _write(self.config.spool_dir / CURSOR_FILE, str(self.cursor))
        for path in spool.prunable(self.config.spool_dir, self.cursor):
            path.unlink(missing_ok=True)
        self.forwarded += len(batch)
        return len(batch)

    def heartbeat(self) -> None:
        """An empty batch after the last one sent: the proxy is alive and has nothing to send."""
        body = json.dumps(
            {
                "stream": self.stream,
                "first": self.cursor + 1,
                "last": self.cursor,
                "version": self.config.version[:40],
                "records": [],
            }
        ).encode()
        status, answer = self.post(body)
        if status != 200:
            raise ConnectionError(f"the server answered {status}: {answer}")

    def forward(self) -> None:
        """Send until stopped; after a failure, wait longer each time, up to a minute."""
        step = 0
        quiet_since = time.monotonic()
        while not self.stop.is_set():
            try:
                sent = self.send_once()
                if sent:
                    quiet_since = time.monotonic()
                elif time.monotonic() - quiet_since >= HEARTBEAT_S:
                    self.heartbeat()
                    quiet_since = time.monotonic()
            except (ConnectionError, OSError) as exc:
                self.failures += 1
                wait = BACKOFF_S[min(step, len(BACKOFF_S) - 1)]
                step += 1
                log.warning("proxy: %s; trying again in %.0f s", exc, wait)
                self.stop.wait(wait)
                continue
            step = 0
            if not sent:
                self.stop.wait(0.5)

    def _https(self, body: bytes) -> tuple[int, dict[str, object]]:
        request = urllib.request.Request(
            f"{self.server}/api/brokers/ingest",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.config.token}",
                "Content-Type": "application/json",
                "User-Agent": f"netcorenoc-proxy/{self.config.version or 'dev'}",
            },
        )
        context = ssl.create_default_context(cafile=self.config.ca_file)
        try:
            with urllib.request.urlopen(request, timeout=30, context=context) as response:  # nosec B310
                return int(response.status), dict(json.loads(response.read() or b"{}"))
        except urllib.error.HTTPError as exc:
            try:
                detail: dict[str, object] = dict(json.loads(exc.read() or b"{}"))
            except ValueError:
                detail = {}
            return int(exc.code), detail
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ConnectionError(str(exc)) from exc

    def close(self) -> None:
        self.stop.set()
        self.writer.close()
        self.reader.close()


def run(config: ProxyConfig) -> int:
    """`python -m netcorenoc proxy`: receive and forward until interrupted."""
    forwarder = Forwarder(config)
    family = socket.AF_INET6 if ":" in config.host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_DGRAM)
    sock.bind((config.host, config.port))
    log.info(
        "proxy: listening on %s:%d, forwarding to %s (stream %s, cursor %d)",
        config.host,
        config.port,
        forwarder.server,
        forwarder.stream,
        forwarder.cursor,
    )
    sender = threading.Thread(target=forwarder.forward, name="proxy-forward", daemon=True)
    sender.start()
    try:
        forwarder.receive(sock)
    except KeyboardInterrupt:
        pass
    finally:
        forwarder.stop.set()
        sender.join(timeout=5)
        sock.close()
        forwarder.close()
    return 0
