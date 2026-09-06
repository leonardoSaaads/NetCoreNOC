"""The host sampler, and chiefly the branches where the host declines to answer.

`engine/operate/resources.py` exists because v0.16.4's premise — that CPU, memory and storage
needed `psutil` — was wrong. What makes it safe to have is not that the happy path works; it is
that **every failure yields `None` and never a zero**, because `0%` reads as *"idle"* and a
measurement nobody took must not look like one (DECISIONS #289, kept by #300).

Those branches are unreachable on a healthy Linux box, which is exactly why they need tests rather
than a live run: the first sample with no delta, a cgroup that says `max`, a `/proc` that is not
there, a `statvfs` that raises, a counter that went backwards. Each is forced here.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from netcorenoc.engine.operate import resources as res

PROC_STAT = "cpu  100 0 50 800 20 0 5 0 0 0\ncpu0 25 0 12 200 5 0 1 0 0 0\nintr 12345\n"


def _fake_reader(files: dict[str, str]) -> Any:
    """A `_read_text` that knows only the paths given, and returns None for everything else."""

    def read(path: Path) -> str | None:
        return files.get(str(path))

    return read


# --------------------------------------------------------------------------- CPU


def test_cpu_counters_exclude_idle_and_iowait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Busy is everything but `idle` and `iowait` — fields 4 and 5.

    Counting iowait as busy would report a **storage** problem as a compute one, which sends an
    operator to the wrong panel. 100+0+50+5 = 155 busy of 975 total.
    """
    monkeypatch.setattr(res, "_read_text", _fake_reader({"/proc/stat": PROC_STAT}))
    busy, total = res.read_cpu_counters()  # type: ignore[misc]
    assert total == 975.0
    assert busy == 155.0


@pytest.mark.parametrize(
    "content",
    [
        None,  # no /proc at all
        "intr 1\nctxt 2\n",  # a /proc/stat with no `cpu ` line
        "cpu  not a number\n",  # a line that does not parse
        "cpu  1 2 3\n",  # too few fields to locate idle and iowait
    ],
    ids=["absent", "no-cpu-line", "unparseable", "truncated"],
)
def test_cpu_counters_return_none_rather_than_a_guess(
    monkeypatch: pytest.MonkeyPatch, content: str | None
) -> None:
    files = {} if content is None else {"/proc/stat": content}
    monkeypatch.setattr(res, "_read_text", _fake_reader(files))
    assert res.read_cpu_counters() is None


def test_read_text_swallows_an_oserror_and_says_nothing_more(tmp_path: Path) -> None:
    """`_read_text` is the one place a failed read becomes "we do not know this number"."""
    assert res._read_text(tmp_path / "does-not-exist") is None
    assert res._read_text(tmp_path) is None  # a directory: IsADirectoryError, still an OSError


# --------------------------------------------------------------------------- memory


def test_memory_prefers_the_cgroup_limit_over_the_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    """**The reason the order matters** (DECISIONS #300).

    Inside the 512 MiB container v0.16.4's compose file defines, `/proc/meminfo` reports the host's
    memory. A panel reading it would say 15 GiB free while the kernel prepares to OOM-kill the
    process, so the cgroup is read first and the reading names which source it came from.
    """
    monkeypatch.setattr(
        res,
        "_read_text",
        _fake_reader(
            {
                str(res._CGROUP / "memory.current"): "268435456\n",
                str(res._CGROUP / "memory.max"): "536870912\n",
                "/proc/meminfo": "MemTotal: 16000000 kB\nMemAvailable: 15000000 kB\n",
            }
        ),
    )
    used, total, source = res.read_memory()  # type: ignore[misc]
    assert (used, total, source) == (268_435_456, 536_870_912, "cgroup")


def test_an_unlimited_cgroup_falls_through_to_the_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """`max` means the cgroup exists and constrains nothing, so its number IS the host's.

    Reporting it as a container limit would be the mislabelling this module exists to avoid.
    """
    monkeypatch.setattr(
        res,
        "_read_text",
        _fake_reader(
            {
                str(res._CGROUP / "memory.current"): "268435456\n",
                str(res._CGROUP / "memory.max"): "max\n",
                "/proc/meminfo": "MemTotal: 1000 kB\nMemAvailable: 400 kB\n",
            }
        ),
    )
    used, total, source = res.read_memory()  # type: ignore[misc]
    assert source == "host"
    assert (used, total) == (600 * 1024, 1000 * 1024)


def test_a_malformed_cgroup_value_falls_through_rather_than_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        res,
        "_read_text",
        _fake_reader(
            {
                str(res._CGROUP / "memory.current"): "not-a-number\n",
                str(res._CGROUP / "memory.max"): "536870912\n",
                "/proc/meminfo": "MemTotal: 1000 kB\nMemAvailable: 400 kB\n",
            }
        ),
    )
    assert res.read_memory() == (600 * 1024, 1000 * 1024, "host")


def test_meminfo_is_converted_from_kb_and_used_is_total_minus_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`MemAvailable` and not `MemFree`: cache the kernel will hand back is not memory in use."""
    monkeypatch.setattr(
        res,
        "_read_text",
        _fake_reader(
            {"/proc/meminfo": "MemTotal: 2048 kB\nMemFree: 100 kB\nMemAvailable: 512 kB\n"}
        ),
    )
    assert res.read_memory() == ((2048 - 512) * 1024, 2048 * 1024, "host")


@pytest.mark.parametrize(
    "meminfo",
    [None, "MemTotal: 2048 kB\n", "MemAvailable: 512 kB\n", "MemTotal: 0 kB\nMemAvailable: 0 kB\n"],
    ids=["absent", "no-available", "no-total", "zero-total"],
)
def test_memory_returns_none_when_the_host_will_not_answer(
    monkeypatch: pytest.MonkeyPatch, meminfo: str | None
) -> None:
    files = {} if meminfo is None else {"/proc/meminfo": meminfo}
    monkeypatch.setattr(res, "_read_text", _fake_reader(files))
    assert res.read_memory() is None


# --------------------------------------------------------------------------- storage


def test_storage_counts_only_what_this_appliance_may_write(monkeypatch: pytest.MonkeyPatch) -> None:
    """`f_bavail`, not `f_bfree`. The blocks reserved for root are not room this process has, and
    counting them would promise space that does not exist."""

    class Stat:
        f_blocks, f_frsize, f_bavail, f_bfree = 1000, 4096, 250, 400

    monkeypatch.setattr(os, "statvfs", lambda _p: Stat())
    used, total, source = res.read_storage("/anywhere")  # type: ignore[misc]
    assert (used, total, source) == (750 * 4096, 1000 * 4096, "statvfs")


def test_storage_returns_none_when_statvfs_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_path: object) -> None:
        raise OSError("no such filesystem")

    monkeypatch.setattr(os, "statvfs", boom)
    assert res.read_storage("/anywhere") is None


def test_a_filesystem_reporting_no_blocks_is_not_a_filesystem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Stat:
        f_blocks, f_frsize, f_bavail = 0, 4096, 0

    monkeypatch.setattr(os, "statvfs", lambda _p: Stat())
    assert res.read_storage("/anywhere") is None


# --------------------------------------------------------------------------- the sampler


def _blind(monkeypatch: pytest.MonkeyPatch) -> None:
    """A host that answers nothing at all: no /proc, no cgroup, no statvfs."""
    monkeypatch.setattr(res, "_read_text", _fake_reader({}))

    def boom(_path: object) -> None:
        raise OSError

    monkeypatch.setattr(os, "statvfs", boom)


def test_an_unreadable_host_yields_none_everywhere_and_zero_nowhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**The property the health control's honesty rests on.**

    Every figure is `None`, and not one of them is `0`. The panel renders those as an em dash and
    the words "not measured"; a zero would render as a bar at rest, which is a claim.
    """
    _blind(monkeypatch)
    sampler = res.ResourceSampler(path="/anywhere")
    sampler.sample()
    snapshot = sampler.snapshot()
    for key in ("cpu_pct", "mem_pct", "mem_used", "mem_total", "mem_source", "disk_pct"):
        assert snapshot[key] is None, f"{key} is {snapshot[key]!r} on a host that answered nothing"
    assert snapshot["cpu_series"] == [None]


def test_the_first_sample_has_no_cpu_reading_because_a_delta_needs_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/proc/stat` is monotonic since boot, so one reading means nothing.

    The same contract the trap rate already follows (DECISIONS #222): until a second sample arrives
    the answer is "waiting", not a number.
    """
    monkeypatch.setattr(res, "_read_text", _fake_reader({"/proc/stat": PROC_STAT}))
    monkeypatch.setattr(os, "statvfs", lambda _p: (_ for _ in ()).throw(OSError()))
    sampler = res.ResourceSampler(path="/anywhere")
    sampler.sample()
    assert sampler.snapshot()["cpu_pct"] is None

    # Second reading, 55 busy of 100 elapsed.
    later = "cpu  155 0 50 845 20 0 5 0 0 0\n"  # busy 210 of 1075: +55 of +100
    monkeypatch.setattr(res, "_read_text", _fake_reader({"/proc/stat": later}))
    sampler.sample()
    assert sampler.snapshot()["cpu_pct"] == pytest.approx(55.0)


def test_a_counter_that_went_backwards_yields_no_reading_rather_than_a_wrong_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wrapped or reset counter produces a negative delta. That is not 0 % and not 100 %."""
    monkeypatch.setattr(res, "_read_text", _fake_reader({"/proc/stat": PROC_STAT}))
    monkeypatch.setattr(os, "statvfs", lambda _p: (_ for _ in ()).throw(OSError()))
    sampler = res.ResourceSampler(path="/anywhere")
    sampler.sample()
    monkeypatch.setattr(res, "_read_text", _fake_reader({"/proc/stat": "cpu  1 0 1 1 1 0 0\n"}))
    sampler.sample()
    assert sampler.snapshot()["cpu_pct"] is None


def test_a_stalled_clock_yields_no_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two identical readings mean zero elapsed jiffies, and 0/0 is not 0 %."""
    monkeypatch.setattr(res, "_read_text", _fake_reader({"/proc/stat": PROC_STAT}))
    monkeypatch.setattr(os, "statvfs", lambda _p: (_ for _ in ()).throw(OSError()))
    sampler = res.ResourceSampler(path="/anywhere")
    sampler.sample()
    sampler.sample()
    assert sampler.snapshot()["cpu_pct"] is None


def test_the_series_means_each_bucket_and_keeps_a_hole_a_hole() -> None:
    """A bucket with nothing readable in it stays `None`, so the sparkline breaks there.

    Interpolating across a gap would draw a line through a period nobody measured, which is the one
    thing a graph must never do — and `Spark` splits its polyline on exactly this value.
    """
    sampler = res.ResourceSampler(path="/anywhere")
    per = max(1, res.SAMPLES_KEPT // res.SERIES_POINTS)
    for value in [10.0] * per + [None] * per + [20.0, 40.0] + [None] * (per - 2):
        sampler._cpu.append(value)
    series = sampler._series(sampler._cpu)
    assert series[0] == 10.0, "a full bucket is the mean of its readings"
    assert series[1] is None, "a bucket with no reading must not become a zero"
    assert series[2] == 30.0, "a partly-readable bucket means only what it has"


def test_an_empty_ring_serves_an_empty_series_rather_than_a_flat_line() -> None:
    """Before the first sample there is no line to draw, and a zero-length one is not a floor."""
    sampler = res.ResourceSampler(path="/anywhere")
    assert sampler._series(sampler._cpu) == []
    assert sampler.snapshot()["cpu_series"] == []


def test_the_ring_never_grows_past_the_window() -> None:
    """Two hours at the sampling interval, and a long-running appliance must not accumulate."""
    sampler = res.ResourceSampler(path="/anywhere")
    for _ in range(res.SAMPLES_KEPT * 3):
        sampler._cpu.append(1.0)
    assert len(sampler._cpu) == res.SAMPLES_KEPT
    assert res.SAMPLES_KEPT * res.SAMPLE_INTERVAL_S == res.WINDOW_S


def test_the_snapshot_names_its_window_so_a_reader_knows_what_they_are_looking_at() -> None:
    """A rate with no window is a number nobody can act on (DECISIONS #222), and so is a series."""
    sampler = res.ResourceSampler(path="/anywhere")
    snapshot = sampler.snapshot()
    assert snapshot["window_s"] == res.WINDOW_S
    assert snapshot["interval_s"] == res.SAMPLE_INTERVAL_S
