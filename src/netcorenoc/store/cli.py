"""The maintenance CLI's database plumbing: settings, a side-by-side store, backup, import.

`python -m netcorenoc` (`__main__.py`) runs the commands; this module is how each of them reaches
the appliance's PostgreSQL database (ADR #452). Every command opens the store **without** the
instance lock, so it runs beside a live appliance, and **without** creating the schema — except
``db migrate`` and ``db import-sqlite``, whose job is to create it.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable

from netcorenoc.crosscutting.settings import (
    Settings,
    SettingsError,
    legacy_env_error,
    legacy_env_names,
    redact_url,
    split_password,
)
from netcorenoc.store import OPEN_ERRORS, Store, StoreRefusedError

__all__ = ["_backup", "_db_status", "_import_sqlite", "_open", "_settings"]

#: What opening the store can raise that an operator fixes by pointing at the right database: a
#: refused or unreachable server, a role or password it does not accept, a database or schema that
#: is not there.
_OPEN_ERRORS: tuple[type[BaseException], ...] = (StoreRefusedError, *OPEN_ERRORS)


def _settings(*, importing: bool = False) -> Settings:
    """The appliance's own database settings. The OPTICORR_* aliases were removed in v0.6.0.

    The CLI refuses rather than silently reading the wrong database: pointing `audit verify` at
    a default while the operator believes it is checking their real one would produce a
    confidently wrong answer about the integrity of the audit chain. So `NETCORENOC_DB` — the
    SQLite file until v0.30.0 — is refused here as the appliance refuses it, except by the one
    command whose job is to read that file."""
    legacy = legacy_env_names()
    if legacy:
        print(str(legacy_env_error(tuple(legacy))), file=sys.stderr)
        raise SystemExit(2)
    try:
        settings = Settings.from_env()
    except SettingsError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    if settings.legacy_db and not importing:
        print(
            f"NETCORENOC_DB={settings.legacy_db!r} names a SQLite database; NetCoreNOC keeps its "
            "data in PostgreSQL since v0.31.0 (NETCORENOC_DATABASE_URL). Import the file once with "
            "`python -m netcorenoc db import-sqlite <file>`, then unset NETCORENOC_DB.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return settings


def _store(settings: Settings, *, create: bool = False) -> Store:
    """A store beside the running appliance: one reader, no instance lock."""
    return Store(
        settings.database_url,
        schema=settings.db_schema,
        readers=1,
        statement_cache_size=settings.db_statement_cache,
        exclusive=False,
        create=create,
    )


async def _open(settings: Settings, *, create: bool = False) -> Store:
    store = _store(settings, create=create)
    try:
        await store.open()
    except _OPEN_ERRORS as exc:
        await store.close()
        print(
            f"no NetCoreNOC database at {redact_url(settings.database_url)} (schema "
            f"{settings.db_schema}): {exc}. Set NETCORENOC_DATABASE_URL to the appliance's "
            "database (in the container: docker compose exec netcorenoc python -m netcorenoc ...).",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc
    return store


def _backup(settings: Settings, target: str) -> int:
    """Dump the live schema to `target` with ``pg_dump``, then read the dump back.

    Safe while the appliance is running: ``pg_dump`` reads one consistent snapshot. The custom
    format is compressed and restores selectively (``pg_restore -d <db> <file>``). Refuses to
    overwrite, so a mistyped target cannot replace an earlier backup. The password travels in the
    child's environment, never on its command line, where ``ps`` would show it.
    """
    import shutil
    import subprocess  # nosec B404 - runs the PostgreSQL client tools, no shell

    if os.path.exists(target):
        print(f"{target!r} already exists; choose a new file name", file=sys.stderr)
        return 2
    dump, restore = shutil.which("pg_dump"), shutil.which("pg_restore")
    if dump is None or restore is None:
        print(
            "pg_dump and pg_restore are needed (the PostgreSQL client tools). With Docker "
            "Compose they run in the postgres container, with the generated password: "
            "docs/operate.md, Backup.",
            file=sys.stderr,
        )
        return 2
    asyncio.run(_close(_open(settings)))  # the schema must be there: an empty dump is no backup
    url, password = split_password(settings.database_url)
    env = {**os.environ, **({"PGPASSWORD": password} if password else {})}
    dumped = subprocess.run(  # nosec B603 - fixed binary, the operator's own URL and file name
        [dump, "--format=custom", f"--schema={settings.db_schema}", f"--file={target}", url],
        check=False,
        env=env,
    )
    if dumped.returncode != 0:
        if os.path.exists(target):  # never leave a partial file that a retry would refuse
            os.remove(target)
        print(f"pg_dump failed (exit {dumped.returncode}); nothing was written", file=sys.stderr)
        return 1
    listing = subprocess.run(  # nosec B603 - fixed binary, the file just written
        [restore, "--list", target], check=False, capture_output=True, text=True
    )
    entries = sum(1 for line in listing.stdout.splitlines() if line and not line.startswith(";"))
    verdict = "ok" if listing.returncode == 0 and entries else "unreadable"
    print(
        f"backup written to {target} ({os.path.getsize(target)} bytes, {entries} entries): "
        f"{verdict}"
    )
    return 0 if verdict == "ok" else 1


async def _close(opening: Awaitable[Store]) -> None:
    await (await opening).close()


async def _db_status(settings: Settings) -> int:
    store = await _open(settings)
    try:
        status = await store.server_status()
    finally:
        await store.close()
    for key, value in status.items():
        print(f"{key:<16} {value}")
    return 0


async def _db_migrate(settings: Settings) -> int:
    """Create the schema when it is missing and apply every pending migration. Idempotent, and safe
    beside a running appliance: each migration runs under its own advisory lock."""
    store = await _open(settings, create=True)
    try:
        version = await store.schema_version()
    finally:
        await store.close()
    print(f"schema {settings.db_schema} at version {version}: migrations applied")
    return 0


async def _import_sqlite(settings: Settings, source: str) -> int:
    from netcorenoc.store import importer

    store = await _open(settings, create=True)
    try:
        report = await importer.import_sqlite(store, source)
    except importer.ImportRefusedError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    finally:
        await store.close()
    print(report.render())
    return 0 if report.ok else 1
