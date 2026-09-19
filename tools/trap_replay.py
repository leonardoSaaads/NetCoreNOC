#!/usr/bin/env python3
"""Send real SNMPv2c trap PDUs over UDP — the true end-to-end ingestion path.

Two modes:

- Scenario replay: ``trap_replay.py eval/corpus/fiber_cut.json`` reads a JSON scenario
  (events with a ``delay`` offset in seconds, a ``source`` IP, a ``trap_oid`` and
  optional varbinds) and sends each event as a genuine trap PDU. Sources are simulated
  by binding loopback addresses, so the receiver sees distinct devices with zero
  configuration — see :class:`SourceMap` for what happens when a corpus address is not
  bindable, which is the ordinary case rather than the exceptional one.
- Synthetic load: ``trap_replay.py --synthetic 20 --classes 10 --rate 1000 --duration 60``
  generates a sustained burst for load testing.

## v0.18.0 — the silent collapse, and why it was invisible (F128)

Until this release :meth:`Sender.socket_for` bound the source address under
``contextlib.suppress(OSError)``. A source the host cannot bind fell back to the default local
address **with no message**, so every such device arrived as ``127.0.0.1`` and N network elements
silently became one.

That was not a rare failure. **Eight of the corpus's twenty-five source addresses are TEST-NET-3
(``203.0.113.0/24``), which no host has an interface in**, so `make replay
SCENARIO=dual_incident` — a scenario whose entire point is *"two unrelated incidents on disjoint
NEs"* — delivered four devices as one, on every machine, every time. The appliance then merged
them, correctly, because on the wire they really were one device.

`tests/test_operation.py` carried a private ``_to_wire`` rewrite that did the right thing, so the
one test that drives this scenario over a socket **could not see the tool's defect** — it had
already worked around it. The rewrite now lives here, in the tool an operator actually runs, and
the test calls it. One implementation, both callers.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import socket
import time
from pathlib import Path
from typing import Any

from pyasn1.codec.ber import encoder
from pysnmp.proto import api

_PMOD = api.PROTOCOL_MODULES[api.SNMP_VERSION_2C]
SYS_UPTIME_OID = "1.3.6.1.2.1.1.3.0"
SNMP_TRAP_OID = "1.3.6.1.6.3.1.1.4.1.0"

_KINDS: dict[str, Any] = {
    "int": _PMOD.Integer,
    "str": _PMOD.OctetString,
    "oid": _PMOD.ObjectIdentifier,
    "ticks": _PMOD.TimeTicks,
    "gauge": _PMOD.Gauge32,
    "counter": _PMOD.Counter32,
}


def encode_trap(
    trap_oid: str, varbinds: list[dict[str, str]], community: str, uptime_ticks: int
) -> bytes:
    pdu = _PMOD.TrapPDU()
    _PMOD.apiTrapPDU.set_defaults(pdu)
    bound = [
        (SYS_UPTIME_OID, _PMOD.TimeTicks(uptime_ticks)),
        (SNMP_TRAP_OID, _PMOD.ObjectIdentifier(trap_oid)),
    ]
    for vb in varbinds:
        maker = _KINDS.get(vb.get("kind", "str"), _PMOD.OctetString)
        bound.append((vb["oid"], maker(vb["value"])))
    _PMOD.apiPDU.set_varbinds(pdu, bound)
    message = _PMOD.Message()
    _PMOD.apiMessage.set_defaults(message)
    _PMOD.apiMessage.set_community(message, community)
    _PMOD.apiMessage.set_pdu(message, pdu)
    return bytes(encoder.encode(message))


class SourceBindError(RuntimeError):
    """A simulated source address could not be bound, so it must not be sent from.

    Raised rather than suppressed: a sender that carries on from the wrong address produces a
    different network than the one the scenario describes, and says nothing about it.
    """


def bindable(address: str) -> bool:
    """Can a UDP socket on this host actually claim `address` as its source?"""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.bind((address, 0))
    except OSError:
        return False
    else:
        return True
    finally:
        probe.close()


def loopback_alias(address: str) -> str:
    """``203.0.113.4`` -> ``127.0.113.4``: the same device, at an address a host can bind.

    Only the first octet is replaced. The last three are what distinguish a corpus's devices from
    each other, so keeping them makes the rewrite injective for any set of addresses that already
    differ below the first octet — which :meth:`SourceMap.for_sources` verifies rather than
    assumes, over the addresses actually present.

    Loopback is the target block because every address in ``127.0.0.0/8`` is bindable on Linux
    with no interface configuration, which is what lets the corpus replay need zero setup.
    """
    return "127." + address.split(".", 1)[1]


class SourceMap:
    """Scenario source address -> the address this host will actually send from.

    Identity wherever the scenario's own address is bindable. Where it is not, the
    :func:`loopback_alias` rewrite, **reported rather than applied silently** — the whole point
    is that an operator is told their four devices are arriving as four devices at different
    addresses, instead of discovering hours later that they arrived as one.

    Injectivity is checked over the addresses present, not argued from the octets: two distinct
    sources that would land on the same wire address is exactly the collapse this class exists
    to prevent, so it is a refusal.
    """

    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping

    @classmethod
    def for_sources(cls, sources: list[str], *, remap: bool = True) -> SourceMap:
        mapping: dict[str, str] = {}
        for source in sorted(set(sources)):
            if bindable(source):
                mapping[source] = source
                continue
            if not remap:
                raise SourceBindError(
                    f"cannot bind source address {source}, and --no-remap forbids rewriting it.\n"
                    "Drop --no-remap to send it from "
                    f"{loopback_alias(source)} instead, or configure an interface in its range."
                )
            wire = loopback_alias(source)
            if not bindable(wire):
                raise SourceBindError(
                    f"cannot bind source address {source}, and its loopback alias {wire} is not "
                    "bindable either.\n\nOn Linux every 127.0.0.0/8 address binds with no setup; "
                    "on macOS add the alias first:\n"
                    f"    sudo ifconfig lo0 alias {wire}"
                )
            mapping[source] = wire
        collisions: dict[str, list[str]] = {}
        for source, wire in mapping.items():
            collisions.setdefault(wire, []).append(source)
        merged = {w: s for w, s in collisions.items() if len(s) > 1}
        if merged:
            detail = "; ".join(f"{', '.join(sorted(s))} -> {w}" for w, s in sorted(merged.items()))
            raise SourceBindError(
                f"the address rewrite would collapse distinct devices onto one source: {detail}.\n"
                "Those devices would arrive as a single network element and the replay would be "
                "measuring a different network than the scenario describes."
            )
        return cls(mapping)

    def wire(self, source: str) -> str:
        return self.mapping.get(source, source)

    def rewritten(self) -> dict[str, str]:
        """Only the entries that are not the identity — what an operator needs told."""
        return {s: w for s, w in self.mapping.items() if s != w}

    def describe(self) -> list[str]:
        """Operator-facing lines: how many devices, and every address that was rewritten."""
        lines = [f"{len(self.mapping)} distinct source address(es) in this scenario"]
        rewritten = self.rewritten()
        if rewritten:
            lines.append(
                f"  {len(rewritten)} not bindable on this host; sending them from a loopback "
                "alias so they stay distinct devices:"
            )
            lines += [f"    {s:15} -> {w}" for s, w in sorted(rewritten.items())]
        return lines


class Sender:
    """UDP sender that binds one socket per simulated source address.

    **A bind failure raises.** v0.17.1 and earlier suppressed it, which is how two hosts became
    one with nothing said (F128). Callers that legitimately do not care which address their
    packets claim — there is one, the synthetic burst — get that by passing sources that bind.
    """

    def __init__(self, target: tuple[str, int]) -> None:
        self.target = target
        self.sockets: dict[str, socket.socket] = {}
        self.sent = 0

    def socket_for(self, source: str) -> socket.socket:
        sock = self.sockets.get(source)
        if sock is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.bind((source, 0))
            except OSError as exc:
                sock.close()
                raise SourceBindError(
                    f"cannot bind source address {source}: {exc}\n\n"
                    "Refusing to fall back to the default local address: that would send this "
                    "device's traps from the same address as every other unbindable one, and the "
                    "receiver would record one network element where the scenario has several."
                ) from exc
            self.sockets[source] = sock
        return sock

    def send(self, source: str, payload: bytes) -> None:
        self.socket_for(source).sendto(payload, self.target)
        self.sent += 1

    def sources_used(self) -> int:
        """How many distinct addresses actually went on the wire — the honest counter.

        Printed at the end of every run. An operator who expects four devices and is told
        ``from 1 source address`` knows within a second, which is the whole defect F128 was.
        """
        return len(self.sockets)

    def close(self) -> None:
        for sock in self.sockets.values():
            sock.close()


def fixture_sources(path: Path) -> list[str]:
    """Every distinct ``source`` in a scenario file, in a stable order."""
    fixture = json.loads(path.read_text())
    return sorted({str(event["source"]) for event in fixture["events"]})


def replay_fixture(
    sender: Sender,
    path: Path,
    community: str,
    time_scale: float,
    sources: SourceMap | None = None,
) -> None:
    """Replay one scenario. ``sources`` resolves each scenario address to a bindable one.

    Built here when the caller passes none, so the tool is correct however it is driven — a
    default that has to be supplied is a default that will be forgotten.
    """
    fixture = json.loads(path.read_text())
    events = sorted(fixture["events"], key=lambda e: float(e.get("delay", 0.0)))
    if sources is None:
        sources = SourceMap.for_sources([str(e["source"]) for e in events])
    start = time.monotonic()
    for event in events:
        due = float(event.get("delay", 0.0)) * time_scale
        wait = due - (time.monotonic() - start)
        if wait > 0:
            time.sleep(wait)
        payload = encode_trap(
            event["trap_oid"],
            event.get("varbinds", []),
            community,
            uptime_ticks=int((time.monotonic() - start) * 100),
        )
        sender.send(sources.wire(str(event["source"])), payload)


def synthetic_burst(
    sender: Sender, devices: int, classes: int, rate: float, duration: float, community: str
) -> None:
    rng = random.Random(1)  # nosec B311 - deterministic synthetic traffic, not crypto
    start = time.monotonic()
    period = 1.0 / rate
    next_due = start
    while (now := time.monotonic()) - start < duration:
        if now < next_due:
            time.sleep(next_due - now)
        device = rng.randrange(devices)
        payload = encode_trap(
            f"1.3.6.1.4.1.9.9.999.0.{rng.randrange(classes)}",
            [{"oid": "1.3.6.1.2.1.2.2.1.1.1", "kind": "int", "value": str(rng.randrange(8))}],
            community,
            uptime_ticks=int((now - start) * 100),
        )
        sender.send(f"127.0.1.{1 + device}", payload)
        next_due += period


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", nargs="?", type=Path, help="JSON fixture to replay")
    parser.add_argument("--host", default="127.0.0.1")
    # Defaulted from the environment the appliance itself reads, so `export
    # NETCORENOC_TRAP_PORT=1162` configures both halves of the quickstart with one line. A
    # replay aimed at the wrong port is silent — traps go nowhere and nothing says so — and
    # the two halves disagreeing by default is how that happens (F129).
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("NETCORENOC_TRAP_PORT") or 162),
        help="collector trap port (default: $NETCORENOC_TRAP_PORT, else 162)",
    )
    parser.add_argument("--community", default="public")
    parser.add_argument(
        "--no-remap",
        action="store_true",
        help="refuse rather than rewrite a source address this host cannot bind",
    )
    parser.add_argument("--time-scale", type=float, default=1.0, help="0 replays at full speed")
    parser.add_argument("--loop", type=int, default=1, help="fixture repetitions")
    parser.add_argument("--loop-gap", type=float, default=2.0, help="seconds between repetitions")
    parser.add_argument("--synthetic", type=int, default=0, help="synthetic mode: device count")
    parser.add_argument("--classes", type=int, default=10)
    parser.add_argument("--rate", type=float, default=100.0, help="synthetic traps per second")
    parser.add_argument("--duration", type=float, default=10.0, help="synthetic seconds")
    args = parser.parse_args()

    sender = Sender((args.host, args.port))
    expected_sources = 0
    started = time.monotonic()
    try:
        if args.synthetic:
            expected_sources = args.synthetic
            synthetic_burst(
                sender, args.synthetic, args.classes, args.rate, args.duration, args.community
            )
        elif args.fixture:
            # Resolved ONCE, before the first packet, and printed. Resolving per iteration would
            # re-probe the same addresses; printing after the run would tell the operator what
            # happened once it was too late to stop it.
            sources = SourceMap.for_sources(fixture_sources(args.fixture), remap=not args.no_remap)
            expected_sources = len(sources.mapping)
            for line in sources.describe():
                print(line)
            print(f"-> {args.host}:{args.port}/udp")
            for iteration in range(args.loop):
                replay_fixture(sender, args.fixture, args.community, args.time_scale, sources)
                if iteration + 1 < args.loop:
                    time.sleep(args.loop_gap)
        else:
            parser.error("provide a fixture path or --synthetic N")
    finally:
        elapsed = time.monotonic() - started
        used = sender.sources_used()
        sender.close()
        rate = sender.sent / elapsed if elapsed > 0 else 0.0
        print(
            f"sent {sender.sent} traps from {used} source address(es) "
            f"in {elapsed:.2f}s ({rate:.0f}/s)"
        )
        if expected_sources and used != expected_sources:
            # Cannot happen now that a bind failure raises — which is exactly why it is worth
            # asserting: a future change that reintroduces a fallback is caught by the run that
            # reintroduces it, not by the person who wonders why there is one device.
            print(
                f"WARNING: expected {expected_sources} distinct source address(es) and used "
                f"{used}; the receiver will record fewer network elements than the scenario has."
            )


if __name__ == "__main__":
    main()
