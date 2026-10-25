"""Reachability checks for the vault's credentials (ADR #457).

**Off every path that matters.** The checks run in a supervised loop of their own (`runner.py`) and
from one route (a custodian's "Check now"); never on the ingest path, and never holding
``store.lock`` while they wait on the network — they read on a reader, connect with nothing held,
and take the lock only to write the result, for one short transaction.

**They never open the seal.** A check connects to the credential's host and port and, where the
protocol greets first, reads the greeting: ``SSH-`` for SSH and SFTP, ``220`` for SMTP. Nothing is
sent, so nothing secret can leak to a host that is not what it claims to be. That is also its limit,
stated beside the light (`health.py`): an answer proves the endpoint, not the credential.

Bounded: at most :data:`CONCURRENCY` checks at once, each within :data:`TIMEOUT_S`; a greeting is
read to :data:`MAX_GREETING` bytes and kept as printable ASCII, because it is text a device chose.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from netcorenoc.engine.noc.kinds import KINDS

if TYPE_CHECKING:
    from netcorenoc.store import Store

__all__ = [
    "CONCURRENCY",
    "TIMEOUT_S",
    "CheckResult",
    "check",
    "check_loop",
    "check_secret",
    "run_due",
]

TIMEOUT_S = 5.0
CONCURRENCY = 16
MAX_GREETING = 255
#: How often the loop looks for credentials whose last check is older than the interval.
LOOK_EVERY_S = 30.0


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    latency_ms: float | None
    detail: str


def _printable(raw: bytes) -> str:
    text = raw[:MAX_GREETING].decode("ascii", errors="replace")
    return "".join(c if " " <= c <= "~" else "?" for c in text).strip()[:80]


async def check(method: str, host: str, port: int, *, timeout: float = TIMEOUT_S) -> CheckResult:
    """Connect to ``host:port`` and, for a protocol that greets first, read its greeting."""
    started = time.monotonic()
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except TimeoutError:
        return CheckResult(False, None, f"no connection within {timeout:g} s")
    except OSError as exc:
        return CheckResult(False, None, (exc.strerror or type(exc).__name__).lower())
    try:
        if method not in ("ssh", "smtp"):
            return CheckResult(True, (time.monotonic() - started) * 1000.0, "TCP port open")
        remaining = max(0.1, timeout - (time.monotonic() - started))
        try:
            greeting = await asyncio.wait_for(reader.readline(), remaining)
        except (TimeoutError, ValueError):
            return CheckResult(False, None, "connected, but no greeting")
        except OSError as exc:
            return CheckResult(False, None, (exc.strerror or type(exc).__name__).lower())
        latency = (time.monotonic() - started) * 1000.0
        expected = b"SSH-" if method == "ssh" else b"220"
        if not greeting.startswith(expected):
            return CheckResult(False, None, f"answered, but not {method.upper()}")
        return CheckResult(True, latency, _printable(greeting) or method.upper())
    finally:
        writer.close()
        with contextlib.suppress(OSError, TimeoutError):
            await asyncio.wait_for(writer.wait_closed(), 1.0)


async def check_secret(row: Mapping[str, Any], *, timeout: float = TIMEOUT_S) -> CheckResult | None:
    """The check a credential's kind calls for, or None when its kind cannot be checked."""
    kind = KINDS.get(str(row["kind"]))
    if kind is None or kind.check is None:
        return None
    return await check(kind.check, str(row["host"]), int(row["port"]), timeout=timeout)


async def run_due(
    store: Store,
    every_s: float,
    *,
    now: Callable[[], float] = time.time,
    timeout: float = TIMEOUT_S,
) -> int:
    """Check every monitored credential not checked for ``every_s``; returns how many were."""
    checkable = sorted(k.id for k in KINDS.values() if k.check is not None)
    async with store.reading():
        due = await store.noc_due_checks(now() - every_s, checkable)
    if not due:
        return 0
    gate = asyncio.Semaphore(CONCURRENCY)

    async def one(row: Mapping[str, Any]) -> tuple[int, CheckResult | None]:
        async with gate:
            return int(row["id"]), await check_secret(row, timeout=timeout)

    results = [r for r in await asyncio.gather(*(one(row) for row in due)) if r[1] is not None]
    async with store.lock:
        at = now()
        for secret_id, result in results:
            assert result is not None
            await store.noc_record_check(
                secret_id, at, result.ok, result.latency_ms, result.detail, "schedule"
            )
        await store.commit()
    return len(results)


async def check_loop(store: Store, every_s: float) -> None:
    """The supervised loop (a crash is the supervisor's to count and restart). ``every_s`` of 0
    switches scheduled checks off, and the loop returns."""
    while every_s > 0:
        await run_due(store, every_s)
        await asyncio.sleep(min(LOOK_EVERY_S, every_s))
