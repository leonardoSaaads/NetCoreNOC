"""**A real appliance, booted as a process, spoken to over a socket and over HTTP.**

`PREREGISTRATION-0.14.0.md` §5.3 step 1 is *"boot a real appliance on an empty database, real UDP,
migrations applied at boot"*, and step 3 is *"label through `POST /api/situations/{sid}/feedback`,
the route the console calls, **never by writing to the store**"*. This module makes those two
sentences literal: `python -m netcorenoc.main` in a subprocess, traps over a UDP socket, labels over
TCP with a real bearer token.

`drive.py` drives the same loop **in process** — same parser, same `apply_feedback` — and is where
the ten-increment census comes from, because ten increments over a real socket is half an hour of
wall clock for a number that does not change. This module is the **witness** that the two agree:
`drive_http.py` runs the first increments over the real surfaces and compares the census.

## The one thing this host cannot do, stated rather than hidden

The generated devices are in `10.0.0.0/8`. A UDP source address must be **bindable**, and this host
has no interface in that block, so `sendto` from `10.11.1.1` is `EADDRNOTAVAIL` — every trap would
arrive from `127.0.0.1` and the whole network would collapse onto a single NE before the correlator
saw it. `tools/trap_replay.py`'s `Sender` already swallows that failure with a
`contextlib.suppress(OSError)`, which is right for a burst generator and wrong here.

So the harness **translates the address for transport only**: `10.a.b.c` is sent from `127.a.b.c`,
which is inside the loopback block and therefore bindable without any interface configuration. It is
a bijection on a corpus whose second octet is always 11-19, the appliance sees exactly as many
distinct devices in exactly the same topology, and `from_wire` maps back for the truth lookup.

**This changes the transport and not the corpus.** The generated document is byte-identical either
way; `drive.py` sends the unmodified `10.*` addresses through the same parser in process, and the
census the two produce is compared rather than assumed equal. It is recorded here, in the gate, and
in the release's list of what was not verified on real hardware.
"""

from __future__ import annotations

import contextlib
import http.cookiejar
import json
import os
import socket
import subprocess  # nosec B404 - booting the appliance under test is this module's whole purpose
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

__all__ = ["Appliance", "Http", "Sender", "from_wire", "to_wire"]

# The address the maintainer signs in as on a first boot, and the one this harness rotates
# immediately. It is a **test fixture on a throwaway database**, never a default anywhere in the
# product: `auth.bootstrap_admin` generates a random password and prints it once, and the appliance
# refuses every authenticated route until it is changed.
DEMO_PASSWORD = "sim-Demonstration-0.14.0"  # nosec B105 - throwaway demo database, rotated at boot

BOOT_TIMEOUT_S = 40.0
HTTP_TIMEOUT_S = 30.0
# `perimeter.RATE_CAPACITY` / `RATE_REFILL`: 30 burst, then 10 per second, per client address.
RATE_BACKOFF_S = 0.4
MAX_RATE_BACKOFF_S = 5.0
RATE_RETRIES = 6


def to_wire(ip: str) -> str:
    """`10.a.b.c` -> `127.a.b.c`. The transport rewrite, and the module docstring is its reason."""
    first, rest = ip.split(".", 1)
    return f"127.{rest}" if first == "10" else ip


def from_wire(ip: str) -> str:
    """The inverse of `to_wire`, for the truth lookup. A bijection on this corpus's addresses."""
    first, rest = ip.split(".", 1)
    return f"10.{rest}" if first == "127" else ip


class Sender:
    """One UDP socket per simulated source, and **a bind that is allowed to fail loudly.**

    `tools/trap_replay.py` suppresses the bind error because a burst generator does not care which
    address its packets claim to come from. This does care: an unbindable source silently becomes
    `127.0.0.1` and every device in the corpus becomes one NE, which would not fail — it would
    quietly produce a different network and a census nobody could explain.
    """

    def __init__(self, target: tuple[str, int]) -> None:
        self.target = target
        self.sockets: dict[str, socket.socket] = {}
        self.sent = 0

    def socket_for(self, source: str) -> socket.socket:
        sock = self.sockets.get(source)
        if sock is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind((to_wire(source), 0))  # raises rather than falling back — see the docstring
            self.sockets[source] = sock
        return sock

    def send(self, source: str, payload: bytes) -> None:
        self.socket_for(source).sendto(payload, self.target)
        self.sent += 1

    def close(self) -> None:
        for sock in self.sockets.values():
            sock.close()
        self.sockets.clear()


class Http:
    """The API over TCP, with a session cookie **or** a bearer token.

    Both, because the release exercises both: a console signs in and carries a cookie, and a service
    principal carries a token. `urllib` and `http.cookiejar` so `eval/` stays dependency-free — the
    five runtime dependencies are the release's headline and a test harness that added a sixth would
    be the wrong kind of convenient.
    """

    def __init__(self, base: str, token: str | None = None, console: bool = False) -> None:
        self.base = base.rstrip("/")
        self.token = token
        self.console = console
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def _once(self, method: str, path: str, body: Any = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        if self.console:
            # `perimeter.csrf_ok`: a **cookie-authenticated mutation** must carry an `Origin` whose
            # netloc equals `Host`, and the header the shipped console sets. A bearer principal is
            # exempt because a token is not sent by a browser, so the token clients below do not
            # send these — the harness carries each principal's real headers rather than the union.
            request.add_header("Origin", self.base)
            request.add_header("X-NetCoreNOC-Client", "ui")
        with self.opener.open(request, timeout=HTTP_TIMEOUT_S) as response:
            raw = response.read().decode()
        return json.loads(raw) if raw else None

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        """One call, **waiting out the rate limiter rather than raising through it.**

        `perimeter` allows a burst of 30 and refills 10 per second, keyed on the client address —
        and every principal in this harness comes from `127.0.0.1`, so they share one bucket. A
        driver that treated 429 as an error would report the loopback bucket rather than the
        appliance; one that *disabled* the limiter would demonstrate a different appliance. So it
        backs off and retries, which is what a console does. Bounded: a 429 that will not clear is
        still a failure and still raises.
        """
        delay = RATE_BACKOFF_S
        for _attempt in range(RATE_RETRIES):
            try:
                return self._once(method, path, body)
            except urllib.error.HTTPError as exc:
                if exc.code != 429:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, MAX_RATE_BACKOFF_S)
        return self._once(method, path, body)

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def post(self, path: str, body: Any) -> Any:
        return self._request("POST", path, body)

    def status_of(self, method: str, path: str, body: Any = None) -> int:
        """The status code alone, for the calls whose **refusal** is the observation."""
        try:
            self._request(method, path, body)
        except urllib.error.HTTPError as exc:
            return int(exc.code)
        return 200


class Appliance:
    """`python -m netcorenoc.main` on a throwaway database, on loopback ports.

    Nothing here reaches into `netcorenoc` as a library. The appliance is a **process**: it applies
    its own migrations, generates its own bootstrap password, binds its own sockets, and is spoken
    to only over UDP and TCP. That is the difference between demonstrating the chain and
    asserting it.
    """

    def __init__(self, db_path: str, trap_port: int, http_port: int) -> None:
        self.db_path = db_path
        self.trap_port = trap_port
        self.http_port = http_port
        self.base = f"http://127.0.0.1:{http_port}"
        self.process: subprocess.Popen[str] | None = None
        self.bootstrap_password: str | None = None
        self.log: list[str] = []

    def __enter__(self) -> Appliance:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    def start(self) -> None:
        Path(self.db_path).unlink(missing_ok=True)
        env = dict(os.environ)
        env.update(
            {
                "NETCORENOC_DB": self.db_path,
                "NETCORENOC_TRAP_HOST": "127.0.0.1",
                "NETCORENOC_TRAP_PORT": str(self.trap_port),
                "NETCORENOC_HTTP_HOST": "127.0.0.1",
                "NETCORENOC_HTTP_PORT": str(self.http_port),
                "PYTHONPATH": str(REPO_ROOT / "src"),
                "PYTHONUNBUFFERED": "1",
            }
        )
        self.process = subprocess.Popen(
            [sys.executable, "-m", "netcorenoc.main"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._read_bootstrap_banner()
        self._await_health()

    def _read_bootstrap_banner(self) -> None:
        """Read the one-time password off stdout. **The only sanctioned place it is ever printed.**

        `runner._print_bootstrap_banner` writes it once, at first startup, and F3 is the finding
        that made it once. Reading it here is what a maintainer does at a first boot; it is not
        stored, and the first thing the driver does with it is change it.
        """
        assert self.process is not None and self.process.stdout is not None
        deadline = time.monotonic() + BOOT_TIMEOUT_S
        while time.monotonic() < deadline:
            line = self.process.stdout.readline()
            if not line:
                break
            self.log.append(line.rstrip())
            if "password:" in line:
                self.bootstrap_password = line.split("password:", 1)[1].strip()
                return
        raise RuntimeError(
            "the appliance never printed a bootstrap banner:\n  " + "\n  ".join(self.log[-20:])
        )

    def _await_health(self) -> None:
        deadline = time.monotonic() + BOOT_TIMEOUT_S
        while time.monotonic() < deadline:
            with (
                contextlib.suppress(Exception),
                urllib.request.urlopen(f"{self.base}/healthz", timeout=2.0) as response,
            ):
                if response.status == 200:
                    return
            time.sleep(0.25)
        raise RuntimeError(f"the appliance did not answer /healthz within {BOOT_TIMEOUT_S}s")

    def sign_in(self) -> Http:
        """Sign in as the bootstrap admin, **rotating the one-time password the banner demands.**

        Two calls, because that is the contract: a first-boot admin is `must_change_password`, so
        the first `POST /api/login` carries `new_password` and the second is an ordinary sign-in.
        The returned client holds the session cookie.
        """
        assert self.bootstrap_password is not None, "start() first"
        client = Http(self.base, console=True)
        first = client.post(
            "/api/login",
            {
                "username": "admin",
                "password": self.bootstrap_password,
                "new_password": DEMO_PASSWORD,
            },
        )
        assert not first.get("must_change_password"), first
        session = client.post("/api/login", {"username": "admin", "password": DEMO_PASSWORD})
        assert session["role"] == "admin", session
        return client

    @staticmethod
    def mint(admin: Http, name: str, role: str) -> Http:
        """A named service token, and a client that carries it as a bearer.

        A token rather than a second user account because the operator-concentration floor counts
        `principal_ref`, which is `token:<id>` — the **row** identity. Three tokens are three
        principals by the same definition the governance layer uses, without three password
        rotations.
        """
        created = admin.post("/api/tokens", {"name": name, "role": role})
        return Http(admin.base, token=str(created["token"]))

    def stop(self) -> None:
        if self.process is None:
            return
        self.process.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.process.wait(timeout=15.0)
        if self.process.poll() is None:  # pragma: no cover - only on a wedged appliance
            self.process.kill()
            self.process.wait(timeout=5.0)
        if self.process.stdout is not None:
            with contextlib.suppress(Exception):
                self.log.extend(line.rstrip() for line in self.process.stdout)
        self.process = None
