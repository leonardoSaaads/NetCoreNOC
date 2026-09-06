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


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


@dataclass
class ResourceSampler:
    """A two-hour ring of readings, and the current one.

    ``path`` is the directory whose filesystem is reported as storage — the database's, because
    that is the one whose filling up stops the appliance rather than merely the host.
    """

    path: str
    _cpu_prev: tuple[float, float] | None = None
    _cpu: deque[float | None] = field(default_factory=lambda: deque(maxlen=SAMPLES_KEPT))
    _mem: deque[float | None] = field(default_factory=lambda: deque(maxlen=SAMPLES_KEPT))
    _disk: deque[float | None] = field(default_factory=lambda: deque(maxlen=SAMPLES_KEPT))
    _latest: dict[str, Any] = field(default_factory=dict)

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

        self._cpu.append(cpu_pct)
        self._mem.append(mem_pct)
        self._disk.append(disk_pct)
        self._latest = {
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
        per = max(1, SAMPLES_KEPT // SERIES_POINTS)
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
            "cpu_series": self._series(self._cpu),
            "mem_series": self._series(self._mem),
            "disk_series": self._series(self._disk),
        }
