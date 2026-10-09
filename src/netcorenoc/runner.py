"""The process runner: wire everything together and supervise it.

This module is the **process entry point**, not the domain. It opens the store, starts the
receiver, builds the `Engine`, builds the HTTP application, and supervises the long-lived tasks
until shutdown.

That distinction is the point of the v0.7.3 separation. `MODULE-ARCHITECTURE.md` §1 recorded one
genuine layer violation — `main.py` importing `netcorenoc.api` — because `main.py` was the `Engine`
(domain) *and* the thing that builds the HTTP server, in one module. The entry point may
legitimately reach up into `http`; **the `Engine` may not**. Splitting them resolves the violation
structurally rather than by exemption, and
`tests/repo/test_layers.py::test_the_engine_does_not_import_the_http_layer` holds the line.

Shutdown is graceful and bounded (§A.5): stop accepting datagrams, cancel the tasks, drain what is
still queued within a deadline, run one final maintenance pass, close the store. The audit chain
only advances on commit, so an interrupted drain leaves it consistent.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import uvicorn

from netcorenoc.api import QuietServer, create_app
from netcorenoc.crosscutting import administration, posture, shaping
from netcorenoc.crosscutting.runtime import RuntimeConfig
from netcorenoc.crosscutting.settings import (
    ENV_PREFIX,
    LegacyTokenRemovedError,
    Settings,
    SettingsError,
    legacy_env_error,
)
from netcorenoc.crosscutting.supervisor import Supervisor as Supervisor
from netcorenoc.engine.operate.engine import Engine
from netcorenoc.engine.operate.resources import SAMPLE_INTERVAL_S, ResourceSampler
from netcorenoc.ingest import snmpconf
from netcorenoc.ingest.receiver import (
    QueueItem,
    ReceiverStats,
    parse_allowlist,
    start_receiver,
)
from netcorenoc.store import Store

log = logging.getLogger("netcorenoc")

QUEUE_SIZE = 100_000
SHUTDOWN_DRAIN_S = 5.0  # bounded deadline to drain queued traps on graceful shutdown (§A.5)


class HttpServerStartError(RuntimeError):
    """The HTTP server could not start. Raised as an ordinary exception, deliberately (F66).

    uvicorn calls ``sys.exit(STARTUP_FAILURE)`` when it cannot bind, and ``SystemExit`` is a
    ``BaseException``: asyncio re-raises it out of ``run_until_complete`` rather than storing it on
    the task, so the ``run()`` coroutine is never resumed and **every one of its ``finally`` blocks
    is skipped** — including the store close. The interpreter then blocks forever in
    ``threading._shutdown`` waiting on aiosqlite's non-daemon connection thread, which is what
    "the appliance hangs when port 8080 is already in use" actually was.

    Converting it here puts the failure back on the path every other startup error takes: it
    propagates through ``asyncio.gather``, the cleanup runs, the store closes, the process exits
    non-zero, and a supervisor restarts it.
    """


async def _sample_resources(
    sampler: ResourceSampler,
    store: Store,
    queue: asyncio.Queue[QueueItem],
    received: Callable[[], int] | None = None,
    latency_p95_s: Callable[[], float] | None = None,
) -> None:
    """Read CPU, memory, storage and queue depth every ``SAMPLE_INTERVAL_S``, and keep each.

    Supervised: a crash costs a graph, never the process. Each reading is also a `host_sample` row
    (v0.22.0, #380, F154): one INSERT and one bounded DELETE per 30 s, off the datagram path.

    v0.29.0 (ADR #437): the trap rate over the interval — a difference of the receiver's counter,
    read, never incremented here — and the engine's batch latency p95, so the Overview can say
    whether the WORK is keeping up and not only the host. The first interval has no previous count
    and records no rate rather than a made-up one.
    """
    previous: tuple[float, int] | None = None
    while True:
        await asyncio.sleep(SAMPLE_INTERVAL_S)
        sampler.sample()
        now = time.time()
        rate: float | None = None
        if received is not None:
            count = received()
            if previous is not None and now > previous[0] and count >= previous[1]:
                rate = round((count - previous[1]) / (now - previous[0]), 3)
            previous = (now, count)
        latency = round(latency_p95_s() * 1000.0, 2) if latency_p95_s is not None else None
        async with store.lock:
            await store.record_host_sample(
                now, sampler.reading(), queue.qsize(), traps_per_s=rate, latency_ms=latency
            )
            await store.commit()


async def _serve_http(server: QuietServer, url: str) -> None:
    """Run the HTTP server, and treat "it never started" as the failure it is.

    The second half matters as much as the first: a uvicorn that logs a bind error and *returns*
    would otherwise leave an appliance that ingests traps and serves no console, for as long as
    nobody looks.
    """
    try:
        await server.serve()
    except SystemExit as exc:  # uvicorn's own startup failure, already logged by uvicorn
        raise HttpServerStartError(f"the HTTP server could not start on {url}") from exc
    if not server.started:
        raise HttpServerStartError(f"the HTTP server exited without starting on {url}")


def _check_allowlist(spec: str, *, stored: bool) -> None:
    """Refuse an unparseable allowlist by name, and say which of its two homes holds it (F69).

    The allowlist has two sources — the environment and an admin-saved `meta` row that overrides it
    — and an operator staring at a traceback cannot tell which one produced the bad value. The
    stored one is the awkward case, because the screen that would let them fix it is served by the
    appliance that will not start; so that branch says where the value is and how to clear it.
    """
    try:
        parse_allowlist(spec)
    except ValueError as exc:
        source = (
            "the stored trap allowlist, saved from the Settings screen by an admin running a "
            "version that did not validate it. Clear it and the environment default applies "
            "again:  sqlite3 <NETCORENOC_DB> \"DELETE FROM meta WHERE key='config.allowlist'\""
            if stored
            else f"{ENV_PREFIX}ALLOWLIST"
        )
        raise SettingsError(f"{source} is not usable: {exc}") from exc


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "::ffff:127.0.0.1"}


def receiver_warnings(stats: ReceiverStats, allowlist: str) -> list[str]:
    """**A denied trap has to reach a human** (F68, DECISIONS #227).

    Measured, an allowlist that refused every source produced 0 log lines, 0 warnings and 0
    rendered counters, against a control that accepted all 8 traps — so an operator whose
    allowlist is wrong watched the appliance receive traffic and produce nothing, in silence. The
    `warnings` channel already exists, already reaches the banner above the work area on every
    screen, and is already how the *empty* allowlist is reported; a wrong one was quieter than no
    one at all.

    A **counter read**, not a log line per packet. Principle 4: this is evaluated where the other
    warnings are — per `/api/stats` request, off the trap path — and the receiver's own
    `datagram_received` is untouched.

    ## v0.16.4 (F107): the entry COUNT, not the entries

    This interpolated the allowlist verbatim. `stats.read` is a **viewer** capability and the
    warning list passes through no shaping at all, so a reader whose `/api/graph` is coarsened to
    `127.0.0.0/24` received the estate's real management prefixes in prose from the same session.
    Measured, with the control being `denied = 0`, where the string is absent.

    #227 already settled the identical question for the boot banner — *"the allowlist's entries are
    not printed; a count answers 'did it load what I set' without publishing the estate's
    addressing"* — and the same sentence decides this. An admin who may act on it reads the value
    on Settings, which is where the warning now points and which the bell links to.
    """
    if not stats.denied:
        return []
    entries = len([part for part in allowlist.split(",") if part.strip()])
    held = "1 entry" if entries == 1 else f"{entries} entries"
    return [
        f"{stats.denied} trap(s) refused: their source address is not in the trap allowlist "
        f"({held}; the value is on Settings). Denied datagrams are counted, never silently "
        f"dropped — if your equipment is behind NAT or a relay, the allowlist must name the "
        f"address the appliance actually sees."
    ]


def operator_warnings(allowlist: str, tls_enabled: bool, http_host: str) -> list[str]:
    """F6: persistent, admin-visible warnings about insecure deployment defaults."""
    warns: list[str] = []
    if not allowlist.strip():
        warns.append(posture.ALLOWLIST_EMPTY)
    if not tls_enabled and http_host not in LOOPBACK_HOSTS:
        warns.append(posture.CLEAR_TEXT_HTTP)
    return warns


async def run(settings: Settings) -> None:
    if settings.legacy_env:
        raise legacy_env_error(settings.legacy_env)
    if settings.api_token:
        # §5.8: the deprecated shared token is removed in v0.3.0. Fail fast, naming the path.
        raise LegacyTokenRemovedError(
            "NETCORENOC_API_TOKEN (and the legacy OPTICORR_API_TOKEN) was removed in v0.3.0. "
            "Unset it and issue a named service token instead (admin -> Tokens in the UI, or "
            "POST /api/tokens); send it as 'Authorization: Bearer <value>'. See MIGRATION.md."
        )
    store = Store(settings.db_path)
    try:
        await store.open()
    except sqlite3.Error as exc:
        # `sqlite3.OperationalError: unable to open database file` names neither the setting nor
        # the path, and the path defaults to the **working directory** — so an operator who ran the
        # appliance from somewhere unexpected cannot tell which file it wanted (F69).
        await store.close()
        raise SettingsError(
            f"{ENV_PREFIX}DB={settings.db_path!r} could not be opened: {exc}. The path is relative "
            f"to the working directory unless it is absolute, and its directory must exist and be "
            f"writable by the process. See docs/configure.md."
        ) from exc
    try:
        await _serve(settings, store)
    finally:
        # **The store is closed on every exit path** (F66, DECISIONS #225). `aiosqlite` runs its
        # connection on a **non-daemon** thread, so a store left open holds the interpreter alive
        # after the exception has been printed — the process does not crash, it hangs, and every
        # restart policy written for it (`Restart=on-failure`, `restart: unless-stopped`) is keyed
        # on an exit that never comes. Measured: five ordinary misconfigurations, including "the
        # HTTP port is already in use", survived SIGTERM and needed SIGKILL.
        await store.close()


async def _serve(settings: Settings, store: Store) -> None:
    """Everything between an open store and a closed one. `run()` owns the store's lifetime."""
    community_key = await store.community_hmac_key()
    await store.commit()

    # Config precedence: admin-saved meta values override env defaults (DESIGN v0.2).
    saved_allow = await store.get_meta("config.allowlist")
    saved_ret = await store.get_meta("config.retention_days")
    effective_allowlist = saved_allow if saved_allow is not None else settings.allowlist
    effective_retention = float(saved_ret) if saved_ret is not None else settings.retention_days
    _check_allowlist(effective_allowlist, stored=saved_allow is not None)
    snmp_policy = snmpconf.stored_policy(await store.get_meta(snmpconf.META_KEY))

    queue: asyncio.Queue[QueueItem] = asyncio.Queue(maxsize=QUEUE_SIZE)
    transport, receiver = await start_receiver(
        queue,
        settings.trap_host,
        settings.trap_port,
        effective_allowlist,
        community_key,
        snmp_policy,
    )
    runtime = RuntimeConfig(
        allowlist=effective_allowlist,
        retention_days=effective_retention,
        on_allowlist_change=lambda nets: setattr(receiver, "networks", nets),
        snmp=snmp_policy,
        on_snmp_change=lambda accepted: setattr(receiver, "accepts", accepted),
        refusals=lambda: dict(receiver.reasons),
    )
    engine = Engine(store, queue)
    engine.audit_retention_days = settings.audit_retention_days
    engine.rearm_s = settings.rearm_s  # ADR #431
    engine.dropped_provider = lambda: receiver.stats.dropped  # §5.6 queue-full gap source
    await engine.start()

    # `existing_users` is read BEFORE the bootstrap, so the banner can tell a first run from a
    # recovery (#234). After it, both look identical — an admin exists either way.
    existing_users = await store.count_users()
    minted = await administration.bootstrap_admin(store, time.time())
    await store.commit()
    if minted is not None:
        administration.print_banner(minted, recovery=existing_users > 0)

    # The host readings the health control draws (v0.16.5). Sampled by a supervised loop rather
    # than on demand: CPU is a delta between two readings, and a series sampled only while somebody
    # has the panel open would be a graph of when people looked at it. The database's directory is
    # the storage that matters — the filesystem that fills up and stops this appliance, not the
    # host's root.
    resources = ResourceSampler(
        path=str(Path(settings.db_path).resolve().parent),
        db_path=str(Path(settings.db_path).resolve()),
    )
    resources.sample()  # one reading now, so CPU has a baseline to difference the next one against

    # **D2's startup self-check** (v0.21.0). Computed once, here, in the *running* appliance —
    # against the `tzdata` this image actually has rather than the one the build machine had.
    # `tests/api/test_timezones.py` asserts the curated list resolves on the build machine, and that
    # is exactly the trap F131 records one layer up: a list validated where it was written and not
    # where it runs. Silent on a healthy install; on an image with no time-zone database it is the
    # one sentence that explains why every window is being refused.
    timezone_warnings = shaping.timezone_selfcheck()
    for warning in timezone_warnings:
        log.warning("%s", warning)

    def receiver_stats() -> dict[str, Any]:
        return {"receiver": asdict(receiver.stats), "resources": resources.snapshot()}

    supervisor = Supervisor()

    app = create_app(
        engine,
        extra_stats=receiver_stats,
        tls_enabled=settings.tls_enabled,
        runtime=runtime,
        warnings=lambda: (
            operator_warnings(runtime.allowlist, settings.tls_enabled, settings.http_host)
            + receiver_warnings(receiver.stats, runtime.allowlist)
            + engine.entity_cap_warnings()
            + engine.db_error_warnings()
            + engine.scorer_warning_list()
            # v0.16.2 (DECISIONS #275). A situation nobody has touched for an hour while one of
            # its alarms is still on. The sweep no longer resolves those — it used to, which was
            # the defect this release is named for — so the operator is told instead, through the
            # channel that already carries seven other degradations.
            + engine.stale_situation_warnings()
            # v0.9.0: shadow mode degrades loudly. A training failure, an unreadable floor policy
            # or a truncated sample all reach the operator through the channel that already exists.
            + engine.shadow.warnings()
            + engine.capture.warnings()
            + list(store.integrity_warnings)
            # v0.21.0: a curated time zone this host cannot resolve. A constant per process,
            # so it is computed once above rather than per request like the rest of this list.
            + timezone_warnings
            + supervisor.warnings()
        ),
    )
    scheme = "https" if settings.tls_enabled else "http"
    server = QuietServer(
        uvicorn.Config(
            app,
            host=settings.http_host,
            port=settings.http_port,
            log_level="warning",
            ssl_certfile=settings.tls_cert or None,
            ssl_keyfile=settings.tls_key or None,
        )
    )
    url = f"{scheme}://{settings.http_host}:{settings.http_port}/"
    # **What an operator needs to know a boot did what they meant** (DECISIONS #227). The database
    # path because it defaults to the *working directory* and is the whole of the state; the
    # allowlist because an empty one accepts everything and a wrong one accepts nothing, and
    # neither is visible from the outside; the retention because it decides what is deleted. The
    # allowlist's entries are not printed — F9 records that the allowlist reveals security posture,
    # and a count answers "did it load what I set" without publishing the estate's addressing.
    log.info("database %s (schema version %d)", settings.db_path, await store.schema_version())
    log.info(
        "trap allowlist: %s; operational retention %g day(s)",
        f"{len(parse_allowlist(effective_allowlist) or [])} network(s)"
        if effective_allowlist.strip()
        else "empty - every source is accepted",
        effective_retention,
    )
    # v0.28.0: which model groups the traps, and the re-arm window — the two answers an operator
    # otherwise had to sign in and open Settings to get, on the morning something grouped oddly.
    log.info(
        "grouping decided by %s; a repeat silent over %gs is a new occurrence",
        engine.decider_ref,
        settings.rearm_s,
    )
    log.info(
        "listening for traps on %s:%d/udp (%s)",
        settings.trap_host,
        settings.trap_port,
        snmpconf.describe(snmp_policy),
    )
    log.info("web UI and API on %s", url)
    tasks = [
        asyncio.create_task(supervisor.run("engine", engine.run)),
        asyncio.create_task(
            supervisor.run(
                "maintenance",
                lambda: engine.maintenance_loop(lambda: runtime.retention_days),
            )
        ),
        asyncio.create_task(
            supervisor.run(
                "resources",
                lambda: _sample_resources(
                    resources,
                    store,
                    queue,
                    received=lambda: receiver.stats.received,
                    latency_p95_s=engine.latency_p95,
                ),
            )
        ),
        asyncio.create_task(_serve_http(server, url)),
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        transport.close()  # stop accepting new datagrams
        server.should_exit = True
        for task in tasks:
            task.cancel()
        # `return_exceptions=True` rather than suppressing `CancelledError` (F66). A task that ended
        # in an **exception** rather than a cancellation — uvicorn failing to bind, for instance —
        # has that exception stored, and `gather` re-raises it here. Suppressing only
        # `CancelledError` therefore let it escape the cleanup and skip everything below: the
        # bounded drain, the final maintenance pass, and the store close in `run()`. The exception
        # still reaches the caller, from the `try` above, which is where it belongs.
        await asyncio.gather(*tasks, return_exceptions=True)
        # Graceful shutdown (§A.5): drain the traps still queued, then a final maintenance pass
        # (flushes the profiler and learned state). The audit chain only advances on commit, so
        # an interrupted drain leaves it consistent.
        await engine.drain(deadline_s=SHUTDOWN_DRAIN_S)
        await engine.maintenance(time.time(), runtime.retention_days)
        log.info("receiver stats: %s", receiver.stats)
