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
import signal
import socket
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


class PortAlreadyServingError(RuntimeError):
    """Something was already answering on the lab's port, so the lab would drive *that*."""


def port_free(port: int, kind: int) -> bool:
    """Can this run bind `port`? The same question `refuse_a_foreign_appliance` asks."""
    probe = socket.socket(socket.AF_INET, kind)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(("127.0.0.1", port))
    except OSError:
        return False
    else:
        return True
    finally:
        probe.close()


def _free_port(kind: int) -> int:
    """A port the kernel says is free right now."""
    probe = socket.socket(socket.AF_INET, kind)
    try:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
    finally:
        probe.close()


def choose_port(requested: int | None, default: int, kind: int, label: str) -> tuple[int, str]:
    """Resolve one port, returning it and a line to print about how it was chosen.

    **v0.18.0 (F132): the lab moves out of the way by itself.** The maintainer could not start
    the lab because a stranger process held a port. The guard refused — correctly, that is F122
    — but refusing was the end of it, and every command he ran afterwards tested nothing.

    So the two cases are now treated differently, because they are different:

    * **No port was requested.** The default is a convenience, not a requirement. If it is busy,
      take a free one and say so. A lab that starts is worth more than a lab that is on 8080.
    * **A port was requested** with `--http-port` / `--trap-port`. Then it is a requirement, and
      silently moving would be worse than refusing: the operator has something pointed at it.
      Refuse, and name the command that identifies the holder.
    """
    if requested is not None:
        if port_free(requested, kind):
            return requested, f"{label} {requested} (requested)"
        proto = "tcp" if kind == socket.SOCK_STREAM else "udp"
        raise PortAlreadyServingError(
            f"{label} {requested} was requested with an explicit flag and is already in use.\n\n"
            "Find what holds it:\n"
            f"    ss -lnp{'t' if kind == socket.SOCK_STREAM else 'u'} 'sport = :{requested}'\n\n"
            f"Or drop the flag and the lab will pick a free {proto} port by itself."
        )
    if port_free(default, kind):
        return default, f"{label} {default}"
    chosen = _free_port(kind)
    return chosen, f"{label} {chosen} (default {default} was busy; picked a free one)"


def refuse_a_foreign_appliance(trap_port: int, http_port: int) -> None:
    """**Refuse if either port is already taken, before starting anything** (F122).

    Found by the live pass. An appliance left over from an earlier session still held 8080, so this
    run's own appliance failed to bind, `wait_for_health` succeeded **against the stranger**, and
    both NE agents spent two minutes sending traps into a database this run had never opened. It
    reported *"appliance healthy in 0.0 s"*, produced an empty lab, and said nothing about why.

    Same shape as the source-address fallback in `agent.py`: a component that carries on plausibly
    when its premise is false. Same remedy — check the premise first, and refuse.

    **The premise is "can this run bind the ports", so that is what is tested.** The first version
    asked whether anything answered `/healthz` with a 200, and an injection walked straight past it:
    a plain `http.server` on 8080 answers 404, which is not a 200, so the check said the port was
    free. Anything holding the port defeats this lab whatever it serves — and the trap port matters
    more than the HTTP one, because a lab whose traps land in a stranger's receiver looks *healthy*.
    """
    for port, kind, proto in (
        (http_port, socket.SOCK_STREAM, "TCP"),
        (trap_port, socket.SOCK_DGRAM, "UDP"),
    ):
        probe = socket.socket(socket.AF_INET, kind)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise PortAlreadyServingError(
                f"{proto} port {port} is already in use: {exc}\n\n"
                "The lab will not start beside whatever holds it. During v0.17.0's live pass an "
                "appliance from an earlier session still held 8080: this run health-checked the "
                "stranger, and both NE agents sent their traps into a database this run had never "
                "opened — silently, for two minutes.\n\n"
                "Stop the other process, or move the lab with --http-port / --trap-port."
            ) from exc
        finally:
            probe.close()


def wait_for_health(port: int, child: subprocess.Popen[bytes], timeout_s: float = 60.0) -> float:
    """Block until `/healthz` answers 200. Returns the seconds it took.

    `child` is the appliance this run started: if it exits, waiting for it to become healthy is
    waiting for something that cannot happen, so that is reported rather than timed out.
    """
    start = time.monotonic()
    url = f"http://127.0.0.1:{port}/healthz"
    while time.monotonic() - start < timeout_s:
        if child.poll() is not None:
            raise RuntimeError(
                f"the appliance exited with code {child.returncode} before answering {url}. "
                f"Its output is in testbed/logs/appliance.log."
            )
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
    # Refuse before starting anything, so a stranger on the port is a message rather than a
    # two-minute run into somebody else's database (F122).
    refuse_a_foreign_appliance(trap_port, http_port)
    appliance = _spawn([_python(), "-m", "netcorenoc.main"], env, logs / "appliance.log")
    children = [appliance]
    elapsed = wait_for_health(http_port, appliance)
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


def _install_signal_handlers(children: list[subprocess.Popen[bytes]]) -> None:
    """Stop the appliance and the agents when this process is asked to stop.

    Python's default SIGTERM handler exits **without unwinding**, so `main`'s `finally` never runs
    and the children outlive their parent. That is how v0.17.0's own verification run left an
    appliance holding port 8080 — which the F122 guard then correctly refused to start beside, in a
    different directory, twenty minutes later. An orphaned lab is a port conflict with a long fuse.
    """

    def _bye(signum: int, _frame: object) -> None:  # pragma: no cover - signal path
        _stop(children)
        raise SystemExit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _bye)


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
    # `default=None` is load-bearing: it is how `choose_port` tells "the operator asked for this
    # port" from "nobody said", which are the two cases that deserve opposite behaviour (F132).
    parser.add_argument(
        "--trap-port",
        type=int,
        default=None,
        help=f"trap port (default {DEFAULT_TRAP_PORT}, or a free one if that is busy)",
    )
    parser.add_argument(
        "--http-port",
        type=int,
        default=None,
        help=f"console port (default {DEFAULT_HTTP_PORT}, or a free one if that is busy)",
    )
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

    # Resolved before anything is started, and both lines are printed, so an operator who ends up
    # on a different port than they expected reads why rather than guessing (F132).
    http_port, http_note = choose_port(
        args.http_port, DEFAULT_HTTP_PORT, socket.SOCK_STREAM, "console port"
    )
    trap_port, trap_note = choose_port(
        args.trap_port, DEFAULT_TRAP_PORT, socket.SOCK_DGRAM, "trap port"
    )
    print(f"  {http_note}\n  {trap_note}")  # noqa: T201

    children, elapsed = up(args.scenario, trap_port, http_port, args.db, args.state)
    _install_signal_handlers(children)
    # Written only once the appliance answers /healthz, so a descriptor's existence means the
    # lab is reachable — not merely that something was spawned.
    control.write_lab(
        control.Lab(
            http_port=http_port,
            trap_port=trap_port,
            pid=os.getpid(),
            scenario=args.scenario,
            db=str(args.db),
        ),
        args.state,
    )
    print(f"appliance healthy in {elapsed:.1f}s -> http://127.0.0.1:{http_port}/")  # noqa: T201
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
        # The descriptor outlives nothing: a stale one sends the next `control.py cut` at a port
        # with nothing behind it, which is the defect this whole mechanism exists to remove.
        control.clear_lab(args.state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
