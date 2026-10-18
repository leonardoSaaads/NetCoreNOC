"""The engine's intake from the spool: read, validate, deliver, and commit the cursor (ADR #450).

`ingest/spool.py` says why traps are spooled and how. This is the main process's side:

* **Decode, then deliver with backpressure.** Records are read after the cursor and decoded — raw
  datagrams by the decoder process (`ingest/decoder.py`), each event validated field by field here,
  because the boundary carries data, never code — and put on the engine's queue with
  ``await queue.put``: when the engine is behind, reading waits and the spool grows on disk. A full
  queue is no longer a reason to drop a trap; it is the reason the spool exists.
* **Commit the cursor with the batch.** :meth:`SpoolFeed.stage` writes the sequence number of the
  batch's last record into ``meta`` inside the batch's own transaction, so the database's contents
  and the cursor are one atomic fact.
* **Replay after a crash, and set a poison record aside.** If the engine task crashes,
  :meth:`SpoolFeed.rewind` discards what was in memory and reads again from the cursor. A record
  that crashes the engine every time would stop the pipeline, so after a crash the records that were
  in flight are delivered **one at a time**; the one the engine crashes on is written to
  ``dead-letter.jsonl`` with the error's name, the cursor passes it, and everything else continues —
  the dead-letter queue every message broker has.
* **Delete what is safely classified.** A segment wholly at or before the cursor is deleted only
  after the database has made that cursor durable: the batch commits without waiting for its WAL
  to reach disk (ADR #452), so a power cut can undo it, and the segment is what it would be
  replayed from.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

from netcorenoc.crosscutting.settings import Settings
from netcorenoc.ingest import decoder, process, records, snmpconf, spool
from netcorenoc.ingest.receiver import QueueItem
from netcorenoc.store import Store

__all__ = ["CURSOR_KEY", "QUEUE_SIZE", "Feed", "NullFeed", "SpoolFeed", "start_spooled"]

log = logging.getLogger("netcorenoc")

#: With the spool the engine's queue only keeps the engine fed — twenty batches — and the spool on
#: disk is the buffer, so a storm costs disk, not memory.
QUEUE_SIZE = 10_000
#: The ``meta`` row the cursor is committed in.
CURSOR_KEY = "spool.cursor"
#: Records read per pass.
READ_BATCH = 2048
#: How long reading waits for the writer's "written" notice before it looks anyway.
IDLE_POLL_S = 0.25
#: How often fully classified segments are looked for.
PRUNE_EVERY_S = 10.0


class Feed(Protocol):
    """What the engine's batch transaction calls (ADR #450)."""

    async def stage(self, items: Sequence[QueueItem]) -> int | None: ...

    def committed(self, position: int | None) -> None: ...


class NullFeed:
    """A queue nothing spools (tests, the in-process receiver): no cursor to keep."""

    async def stage(self, items: Sequence[QueueItem]) -> int | None:
        return None

    def committed(self, position: int | None) -> None:
        return None


class SpoolFeed:
    """Reads the spool into the engine's queue. One per process; see the module docstring."""

    def __init__(
        self, directory: Path, queue: asyncio.Queue[QueueItem], store: Store, cursor: int
    ) -> None:
        self.directory = directory
        self.queue = queue
        self.store = store
        self.cursor = cursor  # the last record classified, committed in the database
        self.read = cursor  # the last record put on the queue
        self.head = cursor  # the last record the writer made durable
        self.peak = 0  # the largest backlog since the sampler last took it
        self.dead_lettered = 0
        self.invalid = 0
        self.torn = self.corrupt = self.skipped = 0  # the spool reader's counters, cumulative
        self._counted = (0, 0, 0)  # what readers before the current one counted
        self.isolate_until = 0  # while the cursor is below this, one record at a time
        self._pending: deque[tuple[QueueItem, int]] = deque()  # delivered, not yet committed
        self._seq_of: dict[int, int] = {}
        self._wake = asyncio.Event()
        self._progress = asyncio.Event()
        self._pump: asyncio.Task[None] | None = None
        self._keeper: asyncio.Task[None] | None = None
        self._runs = 0
        # The writer's own report on the spool — size, limit, refusals — set by the runner to the
        # ingest process's latest; empty without one.
        self.disk: Callable[[], dict[str, Any]] = dict
        # What turns records into events: the decoder process for raw datagrams; records that are
        # already events (a test's, a replay's) are only validated.
        self.decode: Callable[[Sequence[bytes]], Awaitable[list[decoder.Result]]] = decoder.local
        self.ignored = 0  # records that decoded to nothing to classify

    @classmethod
    async def open(
        cls, directory: Path, queue: asyncio.Queue[QueueItem], store: Store
    ) -> SpoolFeed:
        """A feed that resumes after the cursor the database holds."""
        async with store.lock:
            stored = await store.get_meta(CURSOR_KEY)
        return cls(directory, queue, store, int(stored) if stored else 0)

    # -- what the engine calls, inside and after its transaction -------------------------------

    async def stage(self, items: Sequence[QueueItem]) -> int | None:
        """Write the cursor for ``items`` (a committed prefix, in order) in the open transaction.

        And let that transaction commit without its own fsync (ADR #452): every trap in it is
        still in the spool, which :meth:`prune` deletes only once ``store.checkpoint()`` has made
        the commit durable — so a power cut can cost a replay, never a trap.
        """
        if not items:
            return None
        seq = self._seq_of.get(id(items[-1]))
        if seq is not None:
            await self.store.relax_durability()
            await self.store.set_meta(CURSOR_KEY, str(seq))
        return seq

    def committed(self, position: int | None) -> None:
        if position is None:
            return
        self.cursor = max(self.cursor, position)
        while self._pending and self._pending[0][1] <= self.cursor:
            item, _seq = self._pending.popleft()
            self._seq_of.pop(id(item), None)
        self._progress.set()

    # -- what the ingest process tells it -----------------------------------------------------

    def written(self, head: int) -> None:
        """The writer made every record up to ``head`` durable."""
        if head > self.head:
            self.head = head
            self.peak = max(self.peak, self.backlog)
        self._wake.set()

    # -- what the console and the sampler read ------------------------------------------------

    @property
    def backlog(self) -> int:
        """Records written and not yet classified: on disk, on the queue, or in the batch."""
        return max(0, self.head - self.cursor)

    def oldest(self) -> float | None:
        """When the oldest trap not yet classified arrived, if one is in memory."""
        return self._pending[0][0].ts if self._pending else None

    def take_peak(self) -> int:
        """The largest backlog since the last call, then start again from the current one."""
        peak, self.peak = max(self.peak, self.backlog), self.backlog
        return peak

    def report(self) -> dict[str, Any]:
        return {
            "head": self.head,
            "cursor": self.cursor,
            "backlog": self.backlog,
            "on_disk": max(0, self.head - self.read),
            "dead_lettered": self.dead_lettered,
            "invalid": self.invalid,
            "ignored": self.ignored,
            "torn_bytes": self.torn,
            "corrupt_bytes": self.corrupt,
            "missing": self.skipped,
            "isolating": self.isolate_until > self.cursor,
        }

    def warnings(self) -> list[str]:
        out = []
        if self.dead_lettered or self.invalid:
            out.append(
                f"{self.dead_lettered + self.invalid} trap record(s) could not be classified and "
                f"were set aside in {self.directory / spool.DEAD_LETTER} — kept, not lost. The "
                "log names each one; the file can be replayed once the cause is fixed."
            )
        if self.skipped:
            out.append(
                f"{self.skipped} trap record(s) were missing from the spool when it was read "
                "(segments deleted outside the appliance)."
            )
        return out

    # -- life cycle -----------------------------------------------------------------------------

    def start(self) -> None:
        self._pump = asyncio.create_task(self._run_pump(), name="spool-feed")
        if self._keeper is None:
            self._keeper = asyncio.create_task(self._keep(), name="spool-prune")

    async def stop(self) -> None:
        for task in (self._pump, self._keeper):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._pump = self._keeper = None

    async def rewind(self, reason: str = "") -> None:
        """Before the engine task (re)starts: read again from the cursor.

        The first call is the start. Any later one follows a crash: what was in memory is
        discarded and replayed. The records **in flight** — taken off the queue by the engine and
        not committed — are the ones the crash may be about. If records were already being
        delivered one at a time, the single one in flight is the one the engine cannot classify, and
        it is set aside; otherwise the ones in flight are replayed one at a time to find it.
        """
        self._runs += 1
        if self._pump is not None:
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pump
        in_flight = len(self._pending) - self.queue.qsize()
        while not self.queue.empty():
            self.queue.get_nowait()
        if self._runs > 1 and in_flight > 0:
            if self.isolate_until > self.cursor and in_flight == 1:
                item, seq = self._pending[0]
                self.dead_lettered += 1
                await self._set_aside(
                    seq, records.encode(item), f"the engine crashed on it ({reason})", advance=True
                )
            else:
                self.isolate_until = self._pending[in_flight - 1][1]
                log.warning(
                    "the engine stopped with %d trap(s) in flight; they are replayed one at a time "
                    "to find any it cannot classify",
                    in_flight,
                )
        self._pending.clear()
        self._seq_of.clear()
        self.read = self.cursor
        self.start()

    # -- the pump -------------------------------------------------------------------------------

    async def _run_pump(self) -> None:
        reader = spool.SpoolReader(self.directory, self.cursor)
        try:
            while True:
                batch = await asyncio.to_thread(reader.read, READ_BATCH)
                self._account(reader)
                if not batch:
                    self._wake.clear()
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._wake.wait(), IDLE_POLL_S)
                    continue
                results = await self.decode([payload for _seq, payload in batch])
                for (seq, payload), result in zip(batch, results, strict=True):
                    await self._deliver(seq, payload, result)
        finally:
            self._account(reader, last=True)
            reader.close()

    def _account(self, reader: spool.SpoolReader, *, last: bool = False) -> None:
        # Cumulative over every reader this feed has had; a rewind starts a new one.
        torn, corrupt, skipped = self._counted
        self.torn, self.corrupt, self.skipped = (
            torn + reader.torn,
            corrupt + reader.corrupt,
            skipped + reader.skipped,
        )
        if last:
            self._counted = (self.torn, self.corrupt, self.skipped)

    async def _deliver(self, seq: int, payload: bytes, item: decoder.Result) -> None:
        self.head = max(self.head, seq)
        if item is None:
            self.ignored += 1  # a later commit moves the cursor past it
            return
        if isinstance(item, str):
            self.invalid += 1
            await self._settled()  # what came before is classified, so the cursor may pass this
            await self._set_aside(seq, payload, item, advance=True)
            return
        if self.isolate_until >= seq:
            await self._settled()  # one at a time: the previous one is classified first
        await self.queue.put(item)  # waits while the queue is full: backpressure, never a drop
        # Recorded after the put, with no await between: a put a rewind cancelled delivered nothing.
        self._pending.append((item, seq))
        self._seq_of[id(item)] = seq
        self.read = seq
        self.peak = max(self.peak, self.backlog)

    async def _settled(self) -> None:
        """Until every record delivered so far is committed."""
        while self._pending:
            self._progress.clear()
            await self._progress.wait()

    async def _set_aside(self, seq: int, payload: bytes, reason: str, *, advance: bool) -> None:
        """Keep the record in the dead-letter file; when ``advance``, commit the cursor past it."""
        await asyncio.to_thread(spool.dead_letter, self.directory, seq, payload, reason)
        if advance:
            async with self.store.lock:
                await self.store.set_meta(CURSOR_KEY, str(seq))
                await self.store.commit()
            self.cursor = max(self.cursor, seq)
        log.error(
            "trap record %d was set aside in %s: %s",
            seq,
            self.directory / spool.DEAD_LETTER,
            reason,
        )

    # -- deleting what is classified ------------------------------------------------------------

    async def _keep(self) -> None:
        while True:
            await asyncio.sleep(PRUNE_EVERY_S)
            await self.prune()

    async def prune(self) -> int:
        """Delete the segments wholly at or before a cursor the database has made durable."""
        doomed = spool.prunable(self.directory, self.cursor)
        if not doomed:
            return 0
        async with self.store.lock:
            durable = await self.store.checkpoint()
        if not durable:
            return 0
        for path in doomed:
            path.unlink(missing_ok=True)
        return len(doomed)


def oldest_wait(feed: SpoolFeed | None, queue: asyncio.Queue[Any], now: float) -> float:
    """Seconds the oldest trap not yet classified has waited; the queue's head without a feed."""
    if feed is not None:
        oldest = feed.oldest()
        return 0.0 if oldest is None else max(0.0, now - oldest)
    pending = getattr(queue, "_queue", None)
    if not pending:
        return 0.0
    return max(0.0, now - float(getattr(pending[0], "ts", now)))


async def start_spooled(
    store: Store,
    settings: Settings,
    allowlist: str,
    community_key: bytes | None,
    policy: snmpconf.SnmpPolicy,
) -> tuple[asyncio.Queue[QueueItem], SpoolFeed, process.IngestProcess]:
    """The engine's queue, the feed that fills it from the spool, and the ingest process that
    writes the spool — on a socket bound here, so a port in use fails start-up as it always did."""
    queue: asyncio.Queue[QueueItem] = asyncio.Queue(maxsize=QUEUE_SIZE)
    directory = settings.spool_path
    feed = await SpoolFeed.open(directory, queue, store)
    sock = await process.bind(settings.trap_host, settings.trap_port)
    ingest = process.IngestProcess(
        sock,
        allowlist,
        community_key,
        policy,
        directory,
        int(settings.spool_max_mb * 2**20),
        on_written=feed.written,
    )
    feed.disk = lambda: ingest.spool
    feed.decode = ingest.decoder.decode
    try:
        await ingest.start()
    except BaseException:
        sock.close()
        raise
    log.info(
        "traps are spooled in %s; classification resumes after record %d", directory, feed.cursor
    )
    return queue, feed, ingest
