#!/usr/bin/env python3
"""Throwaway PostgreSQL databases for the test suite, `make eval` and the benchmarks (ADR #452).

Where SQLite gave every test a file in ``tmp_path`` and the evaluation an in-memory database, the
store now needs a server. This module finds one and makes databases on it quickly:

1. ``NETCORENOC_TEST_DATABASE_URL`` — a server and a role that may ``CREATE DATABASE`` (CI's
   service container, ``docker compose -f docker-compose.test.yml up -d``, a developer's own);
2. otherwise a **private cluster** started from the local PostgreSQL binaries (``pg_ctl``, found
   on ``PATH`` or under ``/usr/lib/postgresql/*/bin``) in a temporary directory, listening on a
   Unix socket only, with ``fsync`` off — it is deleted when the process exits.

Each database is a copy of a **template** migrated once per process, so a test pays for a
``CREATE DATABASE ... TEMPLATE`` (tens of milliseconds), not for the schema. Names carry the
process id, and :func:`cleanup` drops whatever this process made::

    with pgtemp.database() as dsn:      # a fresh, migrated, empty database
        store = Store(dsn)

    python tools/pgtemp.py              # print the server's URL (starting a cluster if needed)
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import glob
import itertools
import os
import shutil
import subprocess  # nosec B404 - starts the local PostgreSQL binaries, no shell
import sys
import tempfile
import threading
from collections.abc import Callable, Coroutine, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

ENV = "NETCORENOC_TEST_DATABASE_URL"

_server: str | None = None
_template: str | None = None
_made: list[str] = []
_counter = itertools.count(1)


def _binaries() -> Path | None:
    found = shutil.which("pg_ctl")
    if found:
        return Path(found).parent
    candidates = sorted(glob.glob("/usr/lib/postgresql/*/bin/pg_ctl"), reverse=True)
    return Path(candidates[0]).parent if candidates else None


def _local_cluster() -> str:
    """Start a private cluster from the local binaries; its URL as a superuser."""
    bindir = _binaries()
    if bindir is None:
        raise RuntimeError(
            f"no PostgreSQL to test against: set {ENV} to a server where this role may CREATE "
            "DATABASE (docker compose -f docker-compose.test.yml up -d), or install PostgreSQL 15+"
        )
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise RuntimeError(f"PostgreSQL refuses to run as root: set {ENV} to a running server")
    root = Path(tempfile.mkdtemp(prefix="netcorenoc-pg-"))
    data, socket = root / "data", root / "s"
    socket.mkdir()
    subprocess.run(  # nosec B603 - a fixed binary, fixed arguments
        [str(bindir / "initdb"), "-D", str(data), "-U", "postgres", "--auth=trust", "-E", "UTF8"],
        check=True,
        capture_output=True,
    )
    options = (
        f"-k {socket} -c listen_addresses='' -c fsync=off -c synchronous_commit=off "
        "-c full_page_writes=off -c max_connections=200"
    )
    subprocess.run(  # nosec B603 - a fixed binary, fixed arguments
        [
            str(bindir / "pg_ctl"),
            "-D",
            str(data),
            "-o",
            options,
            "-w",
            "-l",
            str(root / "log"),
            "start",
        ],
        check=True,
        capture_output=True,
    )

    def stop() -> None:
        subprocess.run(  # nosec B603 - a fixed binary, fixed arguments
            [str(bindir / "pg_ctl"), "-D", str(data), "-m", "immediate", "stop"],
            check=False,
            capture_output=True,
        )
        shutil.rmtree(root, ignore_errors=True)

    atexit.register(stop)
    return f"postgresql://postgres@/postgres?host={quote(str(socket), safe='')}"


def server() -> str:
    """The URL of the server to make databases on (a role that may ``CREATE DATABASE``)."""
    global _server
    if _server is None:
        _server = os.environ.get(ENV) or _local_cluster()
    return _server


def url_for(name: str) -> str:
    """The server's URL with its database replaced by ``name``."""
    parts = urlsplit(server())
    return urlunsplit((parts.scheme, parts.netloc, "/" + quote(name), parts.query, parts.fragment))


def redact(url: str) -> str:
    """``url`` with its password, if any, replaced by ``***`` — for a log line or a state file."""
    parts = urlsplit(url)
    if parts.password is None:
        return url
    host = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit(
        (parts.scheme, f"{parts.username}:***@{host}", parts.path, parts.query, parts.fragment)
    )


async def _admin(*statements: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(url_for("postgres"))
    try:
        for statement in statements:
            await conn.execute(statement)
    finally:
        await conn.close()


def _sync(factory: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """Run a coroutine to completion from synchronous code — in a thread of its own when the
    caller is already inside an event loop (a fixture of an async test)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(factory())
        return
    failure: list[BaseException] = []

    def work() -> None:
        try:
            asyncio.run(factory())
        except BaseException as exc:
            failure.append(exc)

    thread = threading.Thread(target=work)
    thread.start()
    thread.join()
    if failure:
        raise failure[0]


def _run(*statements: str) -> None:
    _sync(lambda: _admin(*statements))


async def _migrate(url: str) -> None:
    from netcorenoc.store import Store

    store = Store(url, readers=1)
    await store.open()
    await store.close()


def template() -> str:
    """The name of this process's migrated template database, created on first use."""
    global _template
    if _template is None:
        name = f"netcorenoc_tpl_{os.getpid()}"
        _run(f'DROP DATABASE IF EXISTS "{name}"', f'CREATE DATABASE "{name}"')
        _made.append(name)
        _sync(lambda: _migrate(url_for(name)))
        _template = name
    return _template


def create() -> str:
    """A new database copied from the template; its URL."""
    name = f"netcorenoc_t_{os.getpid()}_{next(_counter)}"
    _run(f'CREATE DATABASE "{name}" TEMPLATE "{template()}" STRATEGY FILE_COPY')
    _made.append(name)
    return url_for(name)


def drop(url: str) -> None:
    """Drop one database this process made, disconnecting whatever still holds it."""
    name = urlsplit(url).path.lstrip("/")
    if name in _made:
        _made.remove(name)
        _run(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@contextlib.contextmanager
def database() -> Iterator[str]:
    """A fresh migrated database for the block, dropped afterwards."""
    url = create()
    try:
        yield url
    finally:
        drop(url)


def cleanup() -> None:
    """Drop every database this process made (the template last)."""
    global _template
    names = [n for n in _made if n != _template] + ([_template] if _template else [])
    if names and _server is not None:
        with contextlib.suppress(Exception):
            _run(*(f'DROP DATABASE IF EXISTS "{n}" WITH (FORCE)' for n in names))
    _made.clear()
    _template = None


atexit.register(cleanup)


if __name__ == "__main__":
    print(server())
    sys.exit(0)
