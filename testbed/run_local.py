#!/usr/bin/env python3
"""The lab without Docker: one appliance, two NE agents, on loopback (v0.17.0, DECISIONS #333).

    python testbed/run_local.py            # bring it up, then use testbed/control.py
    python testbed/run_local.py --demo     # bring it up, cut, wait, repair, report, exit

**Why this exists beside `docker compose up`.** Two reasons, and the second is the honest one.

1. It is the faster loop. The console is static ES modules served from disk and the appliance is one
   process, so there is nothing to build — `docker-compose.yml` says the same thing about developing
   against the product. A lab that needs an image rebuild to change one trap offset is a lab people
   stop using.

2. **It is the path v0.17.0 could actually execute.** The environment this release was built in has
   a Docker daemon and no reachable registry — `registry-1.docker.io` answers 403 to the egress
   proxy, an organisation policy denial — so `docker compose up` was never run here. Everything this
   release claims about the testbed working end to end was measured through *this* script: two
   distinct sources recorded in the database, the situation forming, the clears on repair. The
   compose file is written, validated by `docker compose config`, and **not executed**. That is
   stated here rather than in a footnote, because Part VIII resolves an unrun testbed to "it does
   not work" and the honest report is which half was run.

Loopback aliases are what make two hosts real without containers: on Linux every 127.0.0.0/8 address
is bindable with no setup, so `olt-a` genuinely sends from 127.0.0.2 and `olt-b` from 127.0.0.3 and
the receiver records two sources because there are two.
"""

from __future__ import annotations

import argparse
import os
import subprocess  # nosec B404 - fixed argv, shell=False; see _spawn
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from ne import control, scenario  # noqa: E402

DEFAULT_TRAP_PORT = 1162
DEFAULT_HTTP_PORT = 8080


def _python() -> str:
    """The interpreter running this script, so a venv is inherited without being named."""
    return sys.executable


def _spawn(argv: list[str], env: dict[str, str], log: Path) -> subprocess.Popen[bytes]:
    """Start a child with a fixed argument list and no shell."""
    log.parent.mkdir(parents=True, exist_ok=True)
    handle = log.open("wb")
    return subprocess.Popen(  # nosec B603 - fixed argv, shell=False, no user input on the path
        argv, stdout=handle, stderr=subprocess.STDOUT, env=env, cwd=str(REPO_ROOT)
    )


def wait_for_health(port: int, timeout_s: float = 60.0) -> float:
    """Block until `/healthz` answers 200. Returns the seconds it took."""
    start = time.monotonic()
    url = f"http://127.0.0.1:{port}/healthz"
    while time.monotonic() - start < timeout_s:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:  # nosec B310 - fixed http URL
                if response.status == 200:
                    return time.monotonic() - start
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(0.2)
    raise TimeoutError(f"{url} did not answer 200 within {timeout_s}s")


def up(
    scenario_name: str,
    trap_port: int,
    http_port: int,
    db: Path,
    state: Path,
) -> tuple[list[subprocess.Popen[bytes]], float]:
    """Start the appliance and one agent per host. Returns (children, seconds to /healthz)."""
    scen = scenario.load(scenario_name)
    logs = HERE / "logs"
    env = {
        **os.environ,
        "NETCORENOC_DB": str(db),
        "NETCORENOC_TRAP_PORT": str(trap_port),
        "NETCORENOC_HTTP_PORT": str(http_port),
        "NETCORENOC_TESTBED_STATE": str(state),
        "PYTHONPATH": str(REPO_ROOT / "src"),
    }
    children = [
        _spawn([_python(), "-m", "netcorenoc.main"], env, logs / "appliance.log"),
    ]
    elapsed = wait_for_health(http_port)
    for host in scen.hosts:
        children.append(
            _spawn(
                [
                    _python(),
                    str(HERE / "ne" / "agent.py"),
                    host.id,
                    "--scenario",
                    scenario_name,
                    "--port",
                    str(trap_port),
                ],
                env,
                logs / f"{host.id}.log",
            )
        )
    return children, elapsed


def _stop(children: list[subprocess.Popen[bytes]]) -> None:
    for child in reversed(children):
        child.terminate()
    for child in reversed(children):
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - only on a wedged child
            child.kill()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="pon_fiber_cut")
    parser.add_argument("--trap-port", type=int, default=DEFAULT_TRAP_PORT)
    parser.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT)
    parser.add_argument("--db", type=Path, default=HERE / "state" / "testbed.db")
    parser.add_argument("--state", type=Path, default=HERE / "state")
    parser.add_argument("--demo", action="store_true", help="cut, wait, repair, report, exit")
    parser.add_argument("--cycles", type=int, default=1, help="--demo: cut/repair cycles")
    parser.add_argument("--hold-s", type=float, default=25.0, help="--demo: seconds cut")
    parser.add_argument(
        "--settle-s", type=float, default=15.0, help="--demo: quiet time at the end"
    )
    args = parser.parse_args(argv)

    args.db.parent.mkdir(parents=True, exist_ok=True)
    os.environ["NETCORENOC_TESTBED_STATE"] = str(args.state)
    control.write("steady", args.state)

    children, elapsed = up(args.scenario, args.trap_port, args.http_port, args.db, args.state)
    print(  # noqa: T201
        f"appliance healthy in {elapsed:.1f}s -> http://127.0.0.1:{args.http_port}/"
    )
    scen = scenario.load(args.scenario)
    for host in scen.hosts:
        print(f"  {host.id:6} sending from {host.address:9} — {host.role}")  # noqa: T201
    try:
        if not args.demo:
            print("\n  python testbed/control.py cut      # then watch Situations")  # noqa: T201
            print("  python testbed/control.py repair")  # noqa: T201
            print("\nCtrl-C to stop.")  # noqa: T201
            while True:
                time.sleep(3600)
        time.sleep(3)
        for cycle in range(1, args.cycles + 1):
            print(f"\n--- cycle {cycle}: cutting the fibre ---", flush=True)  # noqa: T201
            control.write("cut", args.state)
            time.sleep(args.hold_s)
            print(f"--- cycle {cycle}: repairing ---", flush=True)  # noqa: T201
            control.write("repair", args.state)
            time.sleep(args.hold_s)
        print("--- settling ---", flush=True)  # noqa: T201
        control.write("steady", args.state)
        time.sleep(args.settle_s)
    except KeyboardInterrupt:  # pragma: no cover - interactive
        pass
    finally:
        _stop(children)
    return 0


if __name__ == "__main__":
    sys.exit(main())
