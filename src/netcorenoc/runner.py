"""The process runner: wire everything together and supervise it.

This module is the **process entry point**, not the domain. It opens the store, starts the
receiver, builds the `Engine`, builds the HTTP application, and supervises the long-lived tasks
until shutdown.

That distinction is the point of the v0.7.3 separation. `MODULE-ARCHITECTURE.md` §1 recorded one
genuine layer violation — `main.py` importing `netcorenoc.api` — because `main.py` was the `Engine`
(domain) *and* the thing that builds the HTTP server, in one module. The entry point may
legitimately reach up into `http`; **the `Engine` may not**. Splitting them resolves the violation
structurally rather than by exemption, and
`tests/test_layers.py::test_the_engine_does_not_import_the_http_layer` holds the line.

Shutdown is graceful and bounded (§A.5): stop accepting datagrams, cancel the tasks, drain what is
still queued within a deadline, run one final maintenance pass, close the store. The audit chain
only advances on commit, so an interrupted drain leaves it consistent.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import uvicorn

from netcorenoc.api import QuietServer, create_app
from netcorenoc.crosscutting import auth
from netcorenoc.crosscutting.runtime import RuntimeConfig
from netcorenoc.crosscutting.settings import (
    ENV_PREFIX,
    LegacyTokenRemovedError,
    Settings,
    SettingsError,
    legacy_env_error,
)
from netcorenoc.engine.operate.engine import Engine
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


SUPERVISOR_BACKOFF_BASE_S = 1.0
SUPERVISOR_BACKOFF_MAX_S = 30.0


@dataclass
class Supervisor:
    """Keeps long-lived background tasks alive (§A.5, F10).

    A supervised task that raises is logged (through the redaction filter), counted, and — where
    a restart is safe — restarted with capped exponential backoff; the crash is surfaced through
    ``operator_warnings()`` so it is never silent. A *cancelled* task (graceful shutdown) is never
    restarted. This supervises the engine and the maintenance loop only; the trap datagram path
    lives in the receiver's UDP callback, which cannot raise into the event loop, and is untouched.
    """

    crashes: dict[str, int] = field(default_factory=dict)
    last_error: dict[str, str] = field(default_factory=dict)
    backoff_base: float = SUPERVISOR_BACKOFF_BASE_S
    backoff_max: float = SUPERVISOR_BACKOFF_MAX_S

    async def run(self, name: str, factory: Callable[[], Any], *, restart: bool = True) -> None:
        delay = self.backoff_base
        while True:
            try:
                await factory()
                return  # a task that returns cleanly is done (infinite loops never do)
            except asyncio.CancelledError:
                raise  # shutdown — propagate, never restart
            except Exception as exc:  # the supervisor's whole job is to catch and recover
                self.crashes[name] = self.crashes.get(name, 0) + 1
                self.last_error[name] = type(exc).__name__
                log.exception("supervised task %r crashed (restart=%s)", name, restart)
                if not restart:
                    return
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.backoff_max)

    def warnings(self) -> list[str]:
        return [
            f"Background task '{name}' crashed {n} time(s) (last: {self.last_error.get(name, '?')})"
            " and was restarted; ingestion/correlation may have paused. Check the logs."
            for name, n in sorted(self.crashes.items())
            if n > 0
        ]


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
    """
    if not stats.denied:
        return []
    return [
        f"{stats.denied} trap(s) refused: their source address is not in the trap allowlist "
        f"({allowlist!r}). Denied datagrams are counted, never silently dropped — if your "
        f"equipment is behind NAT or a relay, the allowlist must name the address the appliance "
        f"actually sees."
    ]


def operator_warnings(allowlist: str, tls_enabled: bool, http_host: str) -> list[str]:
    """F6: persistent, admin-visible warnings about insecure deployment defaults."""
    warns: list[str] = []
    if not allowlist.strip():
        warns.append(
            "Trap allowlist is empty: all sources are accepted. Set an allowlist to enforce."
        )
    if not tls_enabled and http_host not in LOOPBACK_HOSTS:
        warns.append(
            "HTTP is not using TLS on a non-loopback bind. Set NETCORENOC_TLS_CERT/KEY or "
            "front NetCoreNOC with a TLS reverse proxy."
        )
    return warns


async def _community_key(store: Store) -> bytes:
    """A per-install 32-byte HMAC key for community tagging, created once in `meta` (F4)."""
    key_hex = await store.get_meta("community_hmac_key")
    if key_hex is None:
        key_hex = os.urandom(32).hex()
        await store.set_meta("community_hmac_key", key_hex)
        await store.commit()
    return bytes.fromhex(key_hex)


def _print_bootstrap_banner(password: str) -> None:
    """The single sanctioned place a secret is printed — once, at first startup (F3)."""
    line = "=" * 70
    print(f"\n{line}", flush=True)  # noqa: T201
    print("  NetCoreNOC bootstrap admin created (first run)", flush=True)  # noqa: T201
    print("      username: admin", flush=True)  # noqa: T201
    print(f"      password: {password}", flush=True)  # noqa: T201
    print("  Sign in and change this password immediately. It is shown ONCE.", flush=True)  # noqa: T201
    print(f"{line}\n", flush=True)  # noqa: T201


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
    community_key = await _community_key(store)

    # Config precedence: admin-saved meta values override env defaults (DESIGN v0.2).
    saved_allow = await store.get_meta("config.allowlist")
    saved_ret = await store.get_meta("config.retention_days")
    effective_allowlist = saved_allow if saved_allow is not None else settings.allowlist
    effective_retention = float(saved_ret) if saved_ret is not None else settings.retention_days
    _check_allowlist(effective_allowlist, stored=saved_allow is not None)

    queue: asyncio.Queue[QueueItem] = asyncio.Queue(maxsize=QUEUE_SIZE)
    transport, receiver = await start_receiver(
        queue, settings.trap_host, settings.trap_port, effective_allowlist, community_key
    )
    runtime = RuntimeConfig(
        allowlist=effective_allowlist,
        retention_days=effective_retention,
        on_allowlist_change=lambda nets: setattr(receiver, "networks", nets),
    )
    engine = Engine(store, queue)
    engine.audit_retention_days = settings.audit_retention_days
    engine.dropped_provider = lambda: receiver.stats.dropped  # §5.6 queue-full gap source
    await engine.start()

    bootstrap_password = await auth.bootstrap_admin(store, time.time())
    await store.commit()
    if bootstrap_password is not None:
        _print_bootstrap_banner(bootstrap_password)

    def receiver_stats() -> dict[str, Any]:
        return {"receiver": asdict(receiver.stats)}

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
            # v0.9.0: shadow mode degrades loudly. A training failure, an unreadable floor policy
            # or a truncated sample all reach the operator through the channel that already exists.
            + engine.shadow.warnings()
            + engine.capture.warnings()
            + list(store.integrity_warnings)
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
    log.info("listening for traps on %s:%d/udp", settings.trap_host, settings.trap_port)
    log.info("web UI and API on %s", url)
    tasks = [
        asyncio.create_task(supervisor.run("engine", engine.run)),
        asyncio.create_task(
            supervisor.run(
                "maintenance",
                lambda: engine.maintenance_loop(lambda: runtime.retention_days),
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
