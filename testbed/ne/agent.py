"""One simulated network element: bind an address, emit genuine trap PDUs (DECISIONS #329).

**This is not a new simulator.** `tools/trap_replay.py` already encodes real SNMPv2c trap PDUs and
already binds one socket per simulated source; this process is a loop around it that watches the
lab's phase file and sends that phase's events for *one* host. Part VII.2 refuses a new simulator
dependency, and there is nothing here `trap_replay` was not already doing — the new part is
*"which events, and when does an operator decide"*, not *"how is a trap encoded"*.

**One process per host, and why that is the right grain.** The maintainer's stated need is two
different hosts. A single process can send from two source addresses — `Sender` keys its sockets by
source — but then one crash takes both NEs down, one `--host` flag points both at the same
collector, and the container boundary that makes them *look* like two devices to an operator is
missing. One process per host costs a few megabytes and buys a lab where killing one NE is a thing
you can do, which is the kind of thing a lab is for (#332).

**The failure this file has to avoid** is the silent one. `trap_replay.Sender.socket_for` binds the
source address under `contextlib.suppress(OSError)`, so a source it cannot bind falls back to the
default local address **with no error** — and two "distinct" hosts quietly become one. That is fine
for a load generator and fatal for a lab whose entire claim is two sources. So this agent binds its
own address up front and **exits non-zero** if it cannot, before a single trap is sent.
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import trap_replay  # noqa: E402
from ne import control, scenario  # noqa: E402


class BindRefusedError(RuntimeError):
    """The agent could not bind its own source address, so it must not pretend to be that host."""


def verify_bindable(address: str) -> None:
    """Bind the address once, loudly, before any trap is sent.

    `Sender` suppresses this failure by design — a load generator should not stop because one
    synthetic source is unavailable. A lab must: an agent that silently sends from the wrong
    address produces a console showing one device where the operator was promised two, and nothing
    anywhere says why.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.bind((address, 0))
    except OSError as exc:
        raise BindRefusedError(
            f"cannot bind {address}: {exc}\n\n"
            "This agent must send FROM that address or it is not simulating that host. On Linux "
            "any 127.0.0.0/8 address is bindable with no setup; under compose the address comes "
            "from the lab network. Fix the address or the network — do not fall back, because a "
            "fallback makes two hosts look like one and says nothing."
        ) from exc
    finally:
        probe.close()


def send_phase(
    sender: trap_replay.Sender,
    phase: scenario.Phase,
    host_id: str,
    address: str,
    community: str,
) -> int:
    """Send this host's share of one phase, honouring each event's offset. Returns traps sent."""
    mine = [event for event in phase.events if event.host == host_id]
    if not mine:
        return 0
    start = time.monotonic()
    sent = 0
    for event in mine:
        wait = event.at_s - (time.monotonic() - start)
        if wait > 0:
            time.sleep(wait)
        payload = trap_replay.encode_trap(
            event.trap_oid,
            [dict(vb) for vb in event.varbinds],
            community,
            uptime_ticks=int((time.monotonic() - start) * 100),
        )
        sender.send(address, payload)
        sent += 1
    return sent


def run(
    host_id: str,
    scenario_name: str,
    collector: tuple[str, int],
    community: str = "public",
    max_transitions: int = 0,
    address: str | None = None,
) -> int:
    """Watch the phase file and send this host's events on every transition.

    `max_transitions` bounds the loop so a test can drive the agent to completion; 0 runs forever,
    which is what the container does.

    **`address` overrides the scenario's, and compose needs it.** The scenario carries loopback
    addresses (127.0.0.2, 127.0.0.3) because that is what the no-Docker path binds. Inside a
    container the NE's address is whatever the lab network gave it — 172.31.7.21 — and binding
    127.0.0.2 there would fail. So the address is a property of *where the agent is running*, not of
    the scenario, and the scenario's value is the default rather than the truth. Either way the
    agent proves it can bind before sending, because a silent fallback makes two hosts one.
    """
    scen = scenario.load(scenario_name)
    host = scen.host(host_id)
    source = address or host.address
    verify_bindable(source)

    sender = trap_replay.Sender(collector)
    seen = control.Phase("", -1)
    transitions = 0
    total = 0
    try:
        while True:
            current = control.read()
            if (current.name, current.seq) != (seen.name, seen.seq):
                phase = scen.phases.get(current.name)
                if phase is not None:
                    count = send_phase(sender, phase, host_id, source, community)
                    total += count
                    print(  # noqa: T201
                        f"[{host_id} {source}] phase={current.name} seq={current.seq} "
                        f"sent={count} total={total}",
                        flush=True,
                    )
                seen = current
                transitions += 1
                if max_transitions and transitions >= max_transitions:
                    return total
            else:
                phase = scen.phases.get(current.name)
                if phase is not None and phase.repeat_every_s:
                    total += send_phase(sender, phase, host_id, source, community)
                    time.sleep(phase.repeat_every_s)
                else:
                    time.sleep(0.25)
    finally:
        sender.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One simulated NE in the NetCoreNOC testbed.")
    parser.add_argument("host_id", help="a host id from the scenario, e.g. olt-a")
    parser.add_argument("--scenario", default="pon_fiber_cut")
    parser.add_argument("--collector", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1162)
    parser.add_argument("--community", default="public")
    parser.add_argument("--max-transitions", type=int, default=0)
    parser.add_argument(
        "--address",
        help="source address to send from; defaults to the scenario's (compose passes the "
        "container's address on the lab network)",
    )
    args = parser.parse_args(argv)
    try:
        run(
            args.host_id,
            args.scenario,
            (args.collector, args.port),
            args.community,
            args.max_transitions,
            args.address,
        )
    except BindRefusedError as exc:
        print(f"error: {exc}", file=sys.stderr)  # noqa: T201
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
