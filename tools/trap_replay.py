#!/usr/bin/env python3
"""Send real SNMPv2c trap PDUs over UDP — the true end-to-end ingestion path.

Two modes:

- Scenario replay: ``trap_replay.py eval/corpus/fiber_cut.json`` reads a JSON scenario
  (events with a ``delay`` offset in seconds, a ``source`` IP, a ``trap_oid`` and
  optional varbinds) and sends each event as a genuine trap PDU. Sources are simulated
  by binding secondary loopback addresses (127.0.0.x), so the receiver sees distinct
  devices with zero configuration.
- Synthetic load: ``trap_replay.py --synthetic 20 --classes 10 --rate 1000 --duration 60``
  generates a sustained burst for load testing.
"""

from __future__ import annotations

import argparse
import contextlib
import json
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


class Sender:
    """UDP sender that binds one socket per simulated source address."""

    def __init__(self, target: tuple[str, int]) -> None:
        self.target = target
        self.sockets: dict[str, socket.socket] = {}
        self.sent = 0

    def socket_for(self, source: str) -> socket.socket:
        sock = self.sockets.get(source)
        if sock is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # A non-bindable source falls back to the default local address.
            with contextlib.suppress(OSError):
                sock.bind((source, 0))
            self.sockets[source] = sock
        return sock

    def send(self, source: str, payload: bytes) -> None:
        self.socket_for(source).sendto(payload, self.target)
        self.sent += 1

    def close(self) -> None:
        for sock in self.sockets.values():
            sock.close()


def replay_fixture(sender: Sender, path: Path, community: str, time_scale: float) -> None:
    fixture = json.loads(path.read_text())
    events = sorted(fixture["events"], key=lambda e: float(e.get("delay", 0.0)))
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
        sender.send(event["source"], payload)


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
    parser.add_argument("--port", type=int, default=162)
    parser.add_argument("--community", default="public")
    parser.add_argument("--time-scale", type=float, default=1.0, help="0 replays at full speed")
    parser.add_argument("--loop", type=int, default=1, help="fixture repetitions")
    parser.add_argument("--loop-gap", type=float, default=2.0, help="seconds between repetitions")
    parser.add_argument("--synthetic", type=int, default=0, help="synthetic mode: device count")
    parser.add_argument("--classes", type=int, default=10)
    parser.add_argument("--rate", type=float, default=100.0, help="synthetic traps per second")
    parser.add_argument("--duration", type=float, default=10.0, help="synthetic seconds")
    args = parser.parse_args()

    sender = Sender((args.host, args.port))
    started = time.monotonic()
    try:
        if args.synthetic:
            synthetic_burst(
                sender, args.synthetic, args.classes, args.rate, args.duration, args.community
            )
        elif args.fixture:
            for iteration in range(args.loop):
                replay_fixture(sender, args.fixture, args.community, args.time_scale)
                if iteration + 1 < args.loop:
                    time.sleep(args.loop_gap)
        else:
            parser.error("provide a fixture path or --synthetic N")
    finally:
        elapsed = time.monotonic() - started
        sender.close()
        rate = sender.sent / elapsed if elapsed > 0 else 0.0
        print(f"sent {sender.sent} traps in {elapsed:.2f}s ({rate:.0f}/s)")


if __name__ == "__main__":
    main()
