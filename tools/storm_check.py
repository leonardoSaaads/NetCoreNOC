#!/usr/bin/env python3
"""Storm check: is every trap classified within the deadline of its own arrival?

ADRs #447-#449. Sends a storm of **distinct** alarms — new fingerprints, the expensive case, not
repeats — as real SNMPv2c datagrams from a separate process, through the real receive path (the
ingest process by default) into the real engine with the packaged league deciding, and reports each
trap's classification time from its own arrival::

    python tools/storm_check.py --traps 20000 --rate 5000          # 20 000 alarms in 4 s
    python tools/storm_check.py --traps 20000 --rate 0             # all at once
    python tools/storm_check.py --traps 2000 --ingest-processes 0  # the in-process receiver

It exits 1 when any trap missed the deadline — classified late, lost, or dropped at a full queue or
by the kernel — so it can gate a build on reference hardware. The numbers are the host's: run it
where the appliance runs. Kernel drops (`RcvbufErrors` in ``/proc/net/snmp``) are counted on Linux;
they are the drops no process can see, and a burst larger than the socket buffer produces them
until ``net.core.rmem_max`` is raised (`docs/configure.md`).

It needs nothing running: a temporary database, the appliance's own modules, loopback sources.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import subprocess  # nosec B404 - starts this script's own sender with this interpreter
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from netcorenoc.engine.operate.engine import Engine
from netcorenoc.ingest import process as ingest_process
from netcorenoc.ingest import snmpconf
from netcorenoc.ingest.receiver import QueueItem, start_receiver
from netcorenoc.store import Store

import trap_replay


def kernel_udp_drops() -> int | None:
    """`RcvbufErrors` from ``/proc/net/snmp``: datagrams the kernel dropped for a full buffer."""
    try:
        lines = [line.split() for line in Path("/proc/net/snmp").read_text().splitlines()]
    except OSError:
        return None
    udp = [line for line in lines if line and line[0] == "Udp:"]
    if len(udp) < 2 or "RcvbufErrors" not in udp[0]:
        return None
    return int(udp[1][udp[0].index("RcvbufErrors")])


def storm(traps: int, devices: int, classes: int, seed: int = 1) -> list[tuple[str, bytes]]:
    """``(source, datagram)`` for ``traps`` alarms, almost all of them distinct fingerprints."""
    rng = random.Random(seed)  # nosec B311 - synthetic traffic, not crypto
    out = []
    for i in range(traps):
        device, cls, port = rng.randrange(devices), rng.randrange(classes), rng.randrange(48)
        payload = trap_replay.encode_trap(
            f"1.3.6.1.4.1.2011.5.25.{cls % 6}.{cls}",
            [
                {"oid": f"1.3.6.1.2.1.2.2.1.1.{port}", "kind": "int", "value": str(port)},
                {
                    "oid": "1.3.6.1.4.1.2011.5.25.99",
                    "value": f"127.0.1.{1 + rng.randrange(devices)}",
                },
            ],
            "public",
            i,
        )
        out.append((f"127.0.1.{1 + device}", payload))
    return out


def send(port: int, traps: int, devices: int, classes: int, rate: float) -> int:
    """The sender process: encode first, then send at ``rate`` per second (0: all at once)."""
    datagrams = storm(traps, devices, classes)
    sender = trap_replay.Sender(("127.0.0.1", port))
    started = time.monotonic()
    for i, (source, payload) in enumerate(datagrams):
        if rate > 0:
            due = started + i / rate
            if (wait := due - time.monotonic()) > 0:
                time.sleep(wait)
        sender.send(source, payload)
    print(json.dumps({"sent": sender.sent, "send_s": round(time.monotonic() - started, 3)}))
    sender.close()
    return 0


async def check(args: argparse.Namespace) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(str(Path(tmp) / "storm.db"))
        await store.open()
        queue: asyncio.Queue[QueueItem] = asyncio.Queue(maxsize=100_000)
        engine = Engine(store, queue)
        engine.deadlines.deadline_s = args.deadline
        await engine.start()
        receiver: Any
        if args.ingest_processes:
            sock = await ingest_process.bind("127.0.0.1", 0)
            port = sock.getsockname()[1]
            receiver = ingest_process.IngestProcess(queue, sock, "", None, snmpconf.DEFAULT_POLICY)
            await receiver.start()
            transport: Any = receiver
        else:
            transport, receiver = await start_receiver(queue, "127.0.0.1", 0)
            port = transport.get_extra_info("sockname")[1]
        engine.dropped_provider = lambda: receiver.stats.dropped
        drops_before = kernel_udp_drops()
        runner = asyncio.create_task(engine.run())
        started = time.monotonic()
        sender = await asyncio.create_subprocess_exec(  # nosec B603 - this script, no shell
            sys.executable,
            __file__,
            "--sender",
            str(port),
            "--traps",
            str(args.traps),
            "--devices",
            str(args.devices),
            "--classes",
            str(args.classes),
            "--rate",
            str(args.rate),
            stdout=subprocess.PIPE,
        )
        out, _ = await sender.communicate()
        sent = json.loads(out.decode().strip().splitlines()[-1])
        peak_head = 0.0
        first: float | None = None  # when the first trap was seen, for the throughput figure
        while time.monotonic() - started < args.timeout:
            await asyncio.sleep(0.5)
            if first is None and (engine.deadlines.classified or not queue.empty()):
                first = time.monotonic() - 0.5
            peak_head = max(peak_head, engine.deadlines.head_age(queue, time.time()))
            stats = receiver.stats
            # Every trap sent is accounted for: classified, lost, dropped by the queue or by the
            # kernel, quarantined, or denied. The receiver's counters can lag by a report.
            in_kernel = (kernel_udp_drops() or 0) - (drops_before or 0)
            accounted = (
                engine.deadlines.classified
                + engine.deadlines.lost
                + stats.dropped
                + stats.quarantined
                + stats.denied
                + in_kernel
            )
            if queue.empty() and accounted >= sent["sent"]:
                break
        elapsed = time.monotonic() - (first if first is not None else started)
        runner.cancel()
        transport.close()
        if args.ingest_processes:
            await receiver.wait_closed()
        drops_after = kernel_udp_drops()
        tracker = engine.deadlines
        await store.close()
    kernel = None if drops_before is None or drops_after is None else drops_after - drops_before
    report = tracker.report(asyncio.Queue(), time.time(), receiver.stats.dropped)
    report.update(
        sent=sent["sent"],
        send_s=sent["send_s"],
        received=receiver.stats.received,
        kernel_dropped=kernel,
        elapsed_s=round(elapsed, 2),
        traps_per_s=round(tracker.classified / elapsed, 1) if elapsed else 0.0,
        peak_head_age_s=round(peak_head, 2),
        ingest_processes=args.ingest_processes,
        decider=engine.decider_ref,
    )
    report["missed"] = (
        report["late"]
        + report["lost"]
        + report["dropped"]
        + (kernel or 0)
        + max(0, sent["sent"] - receiver.stats.received - (kernel or 0))
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--traps", type=int, default=20_000, help="alarms in the storm")
    parser.add_argument("--devices", type=int, default=250, help="source addresses (<= 254)")
    parser.add_argument("--classes", type=int, default=60, help="distinct trap types")
    parser.add_argument("--rate", type=float, default=5000.0, help="traps/s; 0 = all at once")
    parser.add_argument("--deadline", type=float, default=60.0, help="seconds per trap")
    parser.add_argument("--ingest-processes", type=int, choices=(0, 1), default=1)
    parser.add_argument("--timeout", type=float, default=600.0, help="give up after (s)")
    parser.add_argument("--sender", type=int, metavar="PORT", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.sender is not None:
        return send(args.sender, args.traps, args.devices, args.classes, args.rate)
    report = asyncio.run(check(args))
    print(json.dumps(report, indent=2))
    return 1 if report["missed"] else 0


if __name__ == "__main__":
    sys.exit(main())
