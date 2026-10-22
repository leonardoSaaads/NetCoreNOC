#!/usr/bin/env python3
"""Storm check: is every trap kept, and classified within the KPI of its own arrival?

ADRs #447-#451. Sends a storm of **distinct** alarms — new fingerprints, the expensive case, not
repeats — as real SNMPv2c datagrams from a separate process, through the real receive path (the
ingest process and the spool by default) into the real engine with the packaged league deciding,
and reports each trap's decision latency from its own arrival against the KPI::

    python tools/storm_check.py --traps 20000 --rate 5000          # 20 000 alarms in 4 s
    python tools/storm_check.py --traps 20000 --rate 0             # all at once
    python tools/storm_check.py --traps 5000 --restart-at 0.5      # restart the engine halfway
    python tools/storm_check.py --traps 2000 --ingest-processes 0  # the in-process receiver

``--restart-at F`` stops the engine and its spool reader once a fraction F of the storm is
classified and starts a **new** engine on the same database and spool — what a restart of the main
process does — while the ingest process keeps receiving. Nothing may be lost or classified twice.

It exits 1 when any trap was not kept — dropped by the kernel, refused by the spool, or unaccounted
for — or, with ``--require-kpi``, classified past the KPI limit. A trap past the limit is still
classified (the KPI measures; it never discards), so without the flag the run reports it and passes.
The numbers are the host's: run it where the appliance runs. Kernel drops (`RcvbufErrors` in
``/proc/net/snmp``) are counted on Linux; a burst larger than the socket buffer produces them until
``net.core.rmem_max`` is raised (`docs/configure.md`).

It needs a PostgreSQL to make a throwaway database on (`tools/pgtemp.py`: the server
``NETCORENOC_TEST_DATABASE_URL`` names, or a private local cluster), a temporary spool, the
appliance's own modules, loopback.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import random
import subprocess  # nosec B404 - starts this script's own sender with this interpreter
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from netcorenoc.crosscutting.supervisor import Supervisor
from netcorenoc.engine.operate.deadline import Kpi
from netcorenoc.engine.operate.engine import Engine
from netcorenoc.engine.operate.intake import QUEUE_SIZE, SpoolFeed
from netcorenoc.ingest import process as ingest_process
from netcorenoc.ingest import snmpconf
from netcorenoc.ingest.receiver import QueueItem, start_receiver
from netcorenoc.store import Store

import pgtemp
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


class Pipeline:
    """The engine side as the runner wires it: a supervised engine that rewinds its feed first."""

    def __init__(self, store: Store, kpi: Kpi, directory: Path | None, decode: Any = None) -> None:
        self.store, self.kpi, self.directory, self.decode = store, kpi, directory, decode
        self.queue: asyncio.Queue[QueueItem] = asyncio.Queue(
            maxsize=QUEUE_SIZE if directory else 100_000
        )
        self.feed: SpoolFeed | None = None
        self.engine = Engine(store, self.queue)
        self.engine.deadlines.kpi = kpi
        self.supervisor = Supervisor()
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self.directory is not None:
            self.feed = await SpoolFeed.open(self.directory, self.queue, self.store)
            self.feed.decode = self.decode
            self.engine.feed = self.feed
        await self.engine.start()

        async def run_engine() -> None:
            if self.feed is not None:
                await self.feed.rewind(self.supervisor.last_error.get("engine", ""))
            await self.engine.run()

        self.task = asyncio.create_task(self.supervisor.run("engine", run_engine))

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        if self.feed is not None:
            await self.feed.stop()


async def check(args: argparse.Namespace) -> dict[str, Any]:
    kpi = Kpi(args.target, args.limit)
    with tempfile.TemporaryDirectory() as tmp:
        url = pgtemp.create()  # a throwaway database (ADR #452); the spool stays in `tmp`
        store = Store(url)
        await store.open()
        directory = Path(tmp) / "spool" if args.ingest_processes else None
        pipelines: list[Pipeline] = []
        receiver: Any

        def written(head: int) -> None:
            feed = pipelines[-1].feed if pipelines else None
            if feed is not None:
                feed.written(head)

        if directory is not None:
            sock = await ingest_process.bind("127.0.0.1", 0)
            port = sock.getsockname()[1]
            receiver = ingest_process.IngestProcess(
                sock,
                "",
                None,
                snmpconf.DEFAULT_POLICY,
                directory,
                args.spool_mb * 2**20,
                on_written=written,
            )
            await receiver.start()
            transport: Any = receiver
            pipelines.append(Pipeline(store, kpi, directory, receiver.decoder.decode))
            await pipelines[0].start()
        else:
            pipelines.append(Pipeline(store, kpi, None))
            await pipelines[0].start()
            transport, receiver = await start_receiver(pipelines[0].queue, "127.0.0.1", 0)
            port = transport.get_extra_info("sockname")[1]
        drops_before = kernel_udp_drops()
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
        peaks = {"backlog": 0, "oldest_wait_s": 0.0, "spool_mb": 0.0}
        first: float | None = None
        classified = 0
        while time.monotonic() - started < args.timeout:
            await asyncio.sleep(0.25)
            current = pipelines[-1]
            classified = sum(p.engine.deadlines.classified for p in pipelines)
            if first is None and (classified or not current.queue.empty()):
                first = time.monotonic() - 0.25
            feed = current.feed
            backlog = feed.backlog if feed is not None else current.queue.qsize()
            oldest = feed.oldest() if feed is not None else None
            peaks["backlog"] = max(peaks["backlog"], backlog)
            if oldest is not None:
                peaks["oldest_wait_s"] = max(peaks["oldest_wait_s"], time.time() - oldest)
            if directory is not None:
                peaks["spool_mb"] = max(peaks["spool_mb"], receiver.spool.get("bytes", 0) / 2**20)
            if (
                args.restart_at
                and len(pipelines) == 1
                and classified >= args.restart_at * sent["sent"]
            ):
                await current.stop()  # what a crash or a restart of the main process does
                pipelines.append(Pipeline(store, kpi, directory, current.decode))
                await pipelines[-1].start()
                continue
            stats = receiver.stats
            in_kernel = (kernel_udp_drops() or 0) - (drops_before or 0)
            accounted = classified + stats.dropped + stats.quarantined + stats.denied + in_kernel
            idle = backlog == 0 if feed is not None else current.queue.empty()
            if idle and accounted >= sent["sent"]:
                break
        elapsed = time.monotonic() - (first if first is not None else started)
        for pipeline in pipelines:
            await pipeline.stop()
        transport.close()
        if directory is not None:
            await receiver.wait_closed()
        drops_after = kernel_udp_drops()
        async with store.lock:
            cur = await store.conn.execute("SELECT COUNT(*) FROM alarm")
            row = await cur.fetchone()
        alarms = int(row[0]) if row else 0
        await store.close()
        pgtemp.drop(url)
    kernel = None if drops_before is None or drops_after is None else drops_after - drops_before
    trackers = [p.engine.deadlines for p in pipelines]
    merged = trackers[0]
    for tracker in trackers[1:]:  # one report across a restart: counts add, latencies pool
        merged.classified += tracker.classified
        merged.over_target += tracker.over_target
        merged.over_limit += tracker.over_limit
        merged.worst_s = max(merged.worst_s, tracker.worst_s)
        merged.latencies.extend(tracker.latencies)
        merged.delays = [a + b for a, b in zip(merged.delays, tracker.delays, strict=True)]
    stats = receiver.stats
    report = merged.report(0.0, stats.dropped)
    for key in ("backlog", "queue", "spool", "oldest_wait_s"):
        report.pop(key, None)
    unaccounted = max(
        0,
        sent["sent"]
        - merged.classified
        - stats.dropped
        - stats.quarantined
        - stats.denied
        - (kernel or 0),
    )
    report.update(
        sent=sent["sent"],
        send_s=sent["send_s"],
        received=stats.received,
        alarms_stored=alarms,
        quarantined=stats.quarantined,
        kernel_dropped=kernel,
        unaccounted=unaccounted,
        not_kept=stats.dropped + (kernel or 0) + unaccounted,
        restarts=len(pipelines) - 1,
        dead_lettered=sum(p.feed.dead_lettered for p in pipelines if p.feed is not None),
        elapsed_s=round(elapsed, 2),
        traps_per_s=round(merged.classified / elapsed, 1) if elapsed else 0.0,
        peak_backlog=peaks["backlog"],
        peak_oldest_wait_s=round(peaks["oldest_wait_s"], 2),
        peak_spool_mb=round(peaks["spool_mb"], 2),
        ingest_processes=args.ingest_processes,
        decider=pipelines[-1].engine.decider_ref,
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--traps", type=int, default=20_000, help="alarms in the storm")
    parser.add_argument("--devices", type=int, default=250, help="source addresses (<= 254)")
    parser.add_argument("--classes", type=int, default=60, help="distinct trap types")
    parser.add_argument("--rate", type=float, default=5000.0, help="traps/s; 0 = all at once")
    parser.add_argument("--target", type=float, default=60.0, help="KPI target, s per trap")
    parser.add_argument("--limit", type=float, default=120.0, help="KPI limit, s per trap")
    parser.add_argument("--require-kpi", action="store_true", help="exit 1 past the limit too")
    parser.add_argument(
        "--restart-at",
        type=float,
        default=0.0,
        metavar="F",
        help="restart the engine once this fraction is classified (0: never)",
    )
    parser.add_argument("--spool-mb", type=int, default=2048, help="the spool's maximum size")
    parser.add_argument("--ingest-processes", type=int, choices=(0, 1), default=1)
    parser.add_argument("--timeout", type=float, default=900.0, help="give up after (s)")
    parser.add_argument("--sender", type=int, metavar="PORT", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.sender is not None:
        return send(args.sender, args.traps, args.devices, args.classes, args.rate)
    if Kpi.problems(args.target, args.limit):
        parser.error("; ".join(Kpi.problems(args.target, args.limit)))
    pgtemp.throwaway_vault()  # the engine seals what it audits (ADR #453)
    report = asyncio.run(check(args))
    print(json.dumps(report, indent=2))
    failed = report["not_kept"] or (args.require_kpi and report["over_limit"])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
