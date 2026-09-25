"""What the host is doing: CPU, memory and storage, measured rather than estimated.

## Why this exists, and why it did not before

v0.16.4's health control showed the four numbers ``/api/stats`` already served and said, in the
panel, *"CPU, memory and disk are not measured by this appliance"* (DECISIONS #289). That was true
and it was the honest thing to render, but it was an answer to the wrong question: the reason those
three were absent was that reading them was believed to need ``psutil``, and a new runtime
dependency is not something a console release gets to add.

It does not. All three are **three stdlib reads**:

* **CPU** — ``/proc/stat``'s first line, differenced against the previous sample. A single reading
  is meaningless (the counters are monotonic since boot), which is why this module is a sampler and
  not a function.
* **Memory** — ``/sys/fs/cgroup/memory.{current,max}`` where cgroup v2 is mounted, else
  ``/proc/meminfo``'s ``MemTotal`` and ``MemAvailable``.
* **Storage** — ``os.statvfs`` on the directory holding the database, which is the filesystem that
  actually fills up and stops this appliance.

So the dependency count is still five and there is no ``psutil`` anywhere in ``src/``.

## cgroup first, and why the order matters

v0.16.4's compose file gave the container 1.0 CPU and 512 MiB (DECISIONS #298). Inside a container
so limited, ``/proc/meminfo`` reports **the host's** memory — 16 GiB where the process may use 512
MiB — so a panel reading it would tell an operator the appliance has 15 GiB free while the kernel
is about to OOM-kill it. The cgroup files are the ones that describe the limit the process actually
lives under, so they are read first and ``/proc`` is the fallback for a bare-metal install. Each
reading says which source produced it, because *"94 % of 512 MiB"* and *"94 % of 16 GiB"* are
different sentences and the operator needs to know which one they are reading.

## A failed read is never a zero

Every metric is independently optional. A file that is missing, unreadable or malformed yields
``None`` and the panel says the appliance does not measure it — it never yields ``0``, which would
read as *"idle"*. This is #289's rule surviving the change that made the numbers available: the
release that adds a measurement does not get to relax the standard that kept it out.
"""

from __future__ import annotations

import contextlib
import os
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: How often the supervised loop takes a reading. Two hours of these is the window the panel draws.
SAMPLE_INTERVAL_S = 30.0

#: The window the panel covers, and therefore how many samples are kept.
WINDOW_S = 2 * 60 * 60
SAMPLES_KEPT = int(WINDOW_S / SAMPLE_INTERVAL_S)  # 240

#: Points in the served series. A sparkline 208 px wide cannot resolve 240 of anything, and the
#: payload rides on every `/api/stats` poll, so the samples are meaned into five-minute buckets.
SERIES_POINTS = 24

_CGROUP = Path("/sys/fs/cgroup")


def _read_text(path: Path) -> str | None:
    """One read, and every failure is the same failure: we do not know this number."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def read_cpu_counters() -> tuple[float, float] | None:
    """``(busy, total)`` jiffies from ``/proc/stat``, or ``None`` where there is no ``/proc``.

    Idle is ``idle + iowait`` — fields 4 and 5. A process waiting on disk is not using the CPU, and
    counting iowait as busy would report a storage problem as a compute one, which sends an
    operator to the wrong panel.
    """
    raw = _read_text(Path("/proc/stat"))
    if not raw:
        return None
    for line in raw.splitlines():
        if not line.startswith("cpu "):
            continue
        try:
            fields = [float(v) for v in line.split()[1:]]
        except ValueError:
            return None
        if len(fields) < 5:
            return None
        total = sum(fields)
        idle = fields[3] + fields[4]
        return total - idle, total
    return None


def read_memory() -> tuple[int, int, str] | None:
    """``(used, total, source)`` in bytes — the cgroup's limit if there is one, else the host's."""
    current = _read_text(_CGROUP / "memory.current")
    limit = _read_text(_CGROUP / "memory.max")
    if current and limit:
        text = limit.strip()
        # "max" means the cgroup exists but is unlimited, so its number is the host's after all and
        # claiming otherwise would be the mislabelling this module exists to avoid.
        if text != "max":
            try:
                return int(current.strip()), int(text), "cgroup"
            except ValueError:
                pass
    raw = _read_text(Path("/proc/meminfo"))
    if not raw:
        return None
    values: dict[str, int] = {}
    for line in raw.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            values[name] = int(parts[0]) * 1024  # meminfo is in kB and every consumer wants bytes
    total, available = values.get("MemTotal"), values.get("MemAvailable")
    if not total or available is None:
        return None
    return total - available, total, "host"


def read_storage(path: str | os.PathLike[str]) -> tuple[int, int, str] | None:
    """``(used_bytes, total_bytes, source)`` for the filesystem holding ``path``.

    ``f_bavail`` and not ``f_bfree``: the reserved blocks only root may use are not space this
    appliance can write into, and counting them would promise room that does not exist.
    """
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    if total <= 0:
        return None
    return total - free, total, "statvfs"


def read_database(db_path: str | os.PathLike[str]) -> tuple[int, int] | None:
    """``(database_bytes, journal_bytes)`` for the SQLite file and its write-ahead log.

    **Two `stat` calls, and deliberately not a query.** ``PRAGMA page_count`` would be the precise
    answer and it is the wrong instrument here: this runs on a supervised loop every thirty seconds
    and a pragma takes the same connection every write takes, so the panel that tells an operator
    the appliance is busy would be adding to it. The file size is what fills the filesystem, which
    is the question the chart beside it is already answering.

    The WAL is reported separately rather than summed. A database of 40 MiB with a 300 MiB WAL is
    a checkpointing problem and a database of 340 MiB is a retention one, and an operator seeing
    one number cannot tell those apart.
    """
    main = Path(db_path)
    try:
        size = main.stat().st_size
    except OSError:
        return None
    journal = 0
    for suffix in ("-wal", "-journal"):
        # Absent is the normal case for whichever journal mode is not in use, so a missing file
        # contributes nothing rather than failing the reading that the main file already gave us.
        with contextlib.suppress(OSError):
            journal += main.with_name(main.name + suffix).stat().st_size
    return size, journal


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


@dataclass
class ResourceSampler:
    """A two-hour ring of readings, and the current one.

    ``path`` is the directory whose filesystem is reported as storage — the database's, because
    that is the one whose filling up stops the appliance rather than merely the host.
    """

    path: str
    #: The database file itself, for the size series. Empty means the deployment did not name one
    #: and the database chart is absent rather than zero — the rule the module header states.
    db_path: str = ""
    _cpu_prev: tuple[float, float] | None = None
    _cpu: deque[float | None] = field(default_factory=lambda: deque(maxlen=SAMPLES_KEPT))
    _mem: deque[float | None] = field(default_factory=lambda: deque(maxlen=SAMPLES_KEPT))
    _disk: deque[float | None] = field(default_factory=lambda: deque(maxlen=SAMPLES_KEPT))
    _db: deque[float | None] = field(default_factory=lambda: deque(maxlen=SAMPLES_KEPT))
    _latest: dict[str, Any] = field(default_factory=dict)
    _db_mb: float | None = None

    def sample(self) -> None:
        """Take one reading. Safe in a supervised loop: it raises nothing an OS read can."""
        counters = read_cpu_counters()
        cpu_pct: float | None = None
        if counters is not None:
            if self._cpu_prev is not None:
                busy = counters[0] - self._cpu_prev[0]
                total = counters[1] - self._cpu_prev[1]
                # A wrapped or stalled counter yields no reading rather than a nonsense one.
                if total > 0 and busy >= 0:
                    cpu_pct = round(min(100.0, 100.0 * busy / total), 1)
            self._cpu_prev = counters

        memory = read_memory()
        storage = read_storage(self.path)
        mem_pct = round(100.0 * memory[0] / memory[1], 1) if memory and memory[1] else None
        disk_pct = round(100.0 * storage[0] / storage[1], 1) if storage and storage[1] else None

        database = read_database(self.db_path) if self.db_path else None
        # Megabytes on the wire, because that is the unit the chart's axis carries and rounding
        # here keeps the series small enough to ride on every `/api/stats` poll.
        db_mb = round((database[0] + database[1]) / 1e6, 2) if database else None

        self._cpu.append(cpu_pct)
        self._mem.append(mem_pct)
        self._disk.append(disk_pct)
        self._db.append(db_mb)
        self._db_mb = db_mb
        self._latest = {
            "db_bytes": database[0] if database else None,
            "db_journal_bytes": database[1] if database else None,
            "cpu_pct": cpu_pct,
            "cpu_count": os.cpu_count(),
            "mem_pct": mem_pct,
            "mem_used": memory[0] if memory else None,
            "mem_total": memory[1] if memory else None,
            "mem_source": memory[2] if memory else None,
            "disk_pct": disk_pct,
            "disk_used": storage[0] if storage else None,
            "disk_total": storage[1] if storage else None,
        }

    def reading(self) -> dict[str, float | None]:
        """The four numbers `host_sample` persists (v0.22.0): the latest reading, in chart units."""
        return {
            "cpu_pct": self._latest.get("cpu_pct"),
            "mem_pct": self._latest.get("mem_pct"),
            "disk_pct": self._latest.get("disk_pct"),
            "db_mb": self._db_mb,
        }

    def _per_bucket(self) -> int:
        """Samples meaned into one served point, from the readings actually taken.

        **One computation, used by both the series and `bucket_s`.** The console derives its time
        axis from `bucket_s`, so a bucket width the series used and the payload did not report
        would draw ticks at the wrong times — which is the failure the `bucket_s` field was added
        to prevent, reached from the other direction. The rings are appended together on every
        sample, so their lengths agree and the longest is the count.
        """
        taken = max(len(self._cpu), len(self._mem), len(self._disk), len(self._db))
        return max(1, taken // SERIES_POINTS)

    def _series(self, ring: deque[float | None]) -> list[float | None]:
        """The ring meaned into ``SERIES_POINTS`` buckets, oldest first.

        A bucket with no readable sample in it is ``None`` and stays ``None``: interpolating across
        a gap would draw a line through a period nobody measured, which is the one thing a graph
        must never do. Buckets before the appliance started are absent rather than zero, so a
        console up for ten minutes draws ten minutes of line and not two hours of floor.
        """
        if not ring:
            return []
        samples = list(ring)
        # **Bucket against the samples in hand, not against the ring's capacity** (v0.19.0, F141).
        #
        # `SAMPLES_KEPT // SERIES_POINTS` is 10, so a freshly started appliance put its first ten
        # readings into one bucket and served a single point — and a `line` needs two, so all four
        # host charts rendered "only one reading so far" for **five minutes**, and drew no line at
        # all for ten. Measured on a restart: three minutes of uptime, six readings taken, four
        # empty charts. Meanwhile the caption underneath read "5 min of a 2.0 h window", which is
        # a claim about data the chart was not drawing.
        #
        # Dividing by what is present makes the second reading the second point, and converges on
        # exactly the old behaviour once the ring is full — at `SAMPLES_KEPT` samples this is the
        # same integer it always was.
        per = self._per_bucket()
        out: list[float | None] = []
        for start in range(0, len(samples), per):
            chunk = [v for v in samples[start : start + per] if v is not None]
            out.append(round(_mean(chunk), 1) if chunk else None)
        return out

    def snapshot(self) -> dict[str, Any]:
        """What ``/api/stats`` carries: the current reading, the window, and three series."""
        return {
            **self._latest,
            "window_s": WINDOW_S,
            "interval_s": SAMPLE_INTERVAL_S,
            # **v0.16.6: how wide one served point is.** `window_s` is the ring's span and
            # `interval_s` is the sampling period; neither says how much time ONE POINT of the
            # series covers, because that is `WINDOW_S / SERIES_POINTS` and `SERIES_POINTS` was
            # not on the wire. A console drawing a time axis therefore had nothing to put on it,
            # and the caption said "last 2 hours" over a series that might cover one minute — the
            # exact failure DECISIONS #306 exists to prevent, reached from the one direction that
            # release did not look. Additive, and the only number the axis needed.
            # **Derived from the readings taken, not from the ring's capacity** (F141). While
            # the ring is filling, one point is one reading, and an axis built from the
            # capacity-derived 300 s would have spaced two readings taken 30 s apart five
            # minutes apart on screen.
            "bucket_s": self._per_bucket() * SAMPLE_INTERVAL_S,
            "cpu_series": self._series(self._cpu),
            "mem_series": self._series(self._mem),
            "disk_series": self._series(self._disk),
            # **In megabytes, not per cent.** Every other series here is a share of something with
            # a ceiling; a database has none, and the question an operator asks of it is "is it
            # growing and how fast", which is a size over time. `_series` means each bucket, which
            # for a monotone-ish size is the size during that bucket.
            "db_series": self._series(self._db),
        }
