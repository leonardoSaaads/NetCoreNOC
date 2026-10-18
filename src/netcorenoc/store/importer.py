"""Copy a v0.30.0 SQLite database into an empty PostgreSQL schema, once, verified (ADR #452).

``python -m netcorenoc db import-sqlite netcorenoc.db`` — the one-way bridge from the file the
appliance used until v0.30.0. It reads the file with the standard library's ``sqlite3``, read-only,
so no SQLite driver is a runtime dependency any more, and it refuses rather than guesses:

* the file must be at **schema 30** (v0.30.0) and pass ``PRAGMA integrity_check`` — an older
  appliance is upgraded with v0.30.0 first, which applies its own migrations;
* the target schema must hold **no history**: only the four rows a new schema starts with, which
  the source's own copies replace.

Everything is copied in **one transaction** with ``COPY`` (one round trip per table), in foreign-key
order, each value converted to the column's PostgreSQL type: an epoch becomes a ``timestamptz``,
0/1 a ``boolean``, an address an ``inet``, a varbind list ``jsonb``. ``device`` is not copied — it
duplicated ``ne`` row for row — and its id columns are dropped. The identity sequences are moved
past the copied ids, and the result is verified before anything is committed: every table's row
count against the source's, then the audit hash chain, link by link. A failure rolls the whole
import back, so a schema is never left half imported. The import is itself audited (``db.import``).
"""

from __future__ import annotations

import ipaddress
import json
import sqlite3
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import asyncpg

from netcorenoc.store.base import StoreBase
from netcorenoc.store.db import _decode_instant, _encode_instant

__all__ = ["SOURCE_SCHEMA", "ImportRefusedError", "ImportReport", "import_sqlite"]

#: The last SQLite schema (v0.30.0, migration `0030`). Anything else is refused.
SOURCE_SCHEMA = 30

#: Every table, in an order that satisfies the foreign keys. ``(source, target)``.
TABLES: tuple[tuple[str, str], ...] = (
    ("organization", "organization"),
    ("ne", "ne"),
    ("alarm_class", "alarm_class"),
    ("entity", "entity"),
    ("label", "label"),
    ("class_rule", "class_rule"),
    ("state_clear", "state_clear"),
    ("varbind_profile", "varbind_profile"),
    ("maintenance_window", "maintenance_window"),
    ("maintenance_window_target", "maintenance_window_target"),
    ("maintenance_window_rule", "maintenance_window_rule"),
    ("alarm", "alarm"),
    ("maintenance_ledger", "maintenance_ledger"),
    ("quarantine", "quarantine"),
    ("ingest_gap", "ingest_gap"),
    ("edge", "edge"),
    ("episode_pair", "episode_pair"),
    ("meta", "meta"),
    ("scorer_config", "scorer_config"),
    ("capture_run", "capture_run"),
    ("challenger_run", "challenger_run"),
    ("model_version", "model_version"),
    ("scorer_active", "scorer_active"),
    ("evaluation_fold", "evaluation_fold"),
    ("promotion", "promotion"),
    ("search_run", "search_run"),
    ("search_trial", "search_trial"),
    ("decider_setting", "decider_setting"),
    ("autonomy_setting", "autonomy_setting"),
    ("league_decision", "league_decision"),
    ("situation", "situation"),
    ("situation_alarm", "situation_alarm"),
    ("link", "link"),
    ("situation_event", "situation_event"),
    ("situation_event_member", "situation_event_member"),
    ("situation_event_moved", "situation_event_moved"),
    ("autonomy_decision", "autonomy_decision"),
    ("proposal_decision", "proposal_decision"),
    ("proposal_decision_member", "proposal_decision_member"),
    ("feedback", "feedback"),
    ("feedback_member", "feedback_member"),
    ("feedback_exclusion", "feedback_exclusion"),
    ("dataset_observation", "dataset_observation"),
    ("dataset_pair", "dataset_pair"),
    ("shadow_opinion", "shadow_opinion"),
    ("holdout_seal", "holdout_seal"),
    ("holdout_seal_member", "holdout_seal_member"),
    ("holdout_access", "holdout_access"),
    ("user", "app_user"),
    ("user_avatar", "user_avatar"),
    ("session", "session"),
    ("password_reset", "password_reset"),
    ("notice_snooze", "notice_snooze"),
    ("api_token", "api_token"),
    ("governance_policy", "governance_policy"),
    ("governance_active", "governance_active"),
    ("audit_log", "audit_log"),
    ("host_sample", "host_sample"),
)

#: The rows a new schema starts with (`0031`'s seeds). The source has its own, which replace them.
SEEDED = ("scorer_active", "decider_setting", "scorer_config", "organization")

#: Tables whose append-only trigger must let the seeds go, inside the import's transaction only.
_GUARDED_SEEDS = ("decider_setting", "scorer_config")


class ImportRefusedError(Exception):
    """The source or the target is not one this import may proceed with; nothing was written."""


@dataclass
class ImportReport:
    """What was copied and whether it verified."""

    source: str
    tables: list[tuple[str, int, int]] = field(default_factory=list)  # (table, source, imported)
    audit: str = ""
    seconds: float = 0.0
    ok: bool = False

    def render(self) -> str:
        width = max((len(name) for name, _, _ in self.tables), default=10)
        lines = [f"imported {self.source} in {self.seconds:.1f} s"]
        for name, expected, got in self.tables:
            mark = "" if expected == got else "   <-- MISMATCH"
            lines.append(f"  {name:<{width}} {got:>10} of {expected:>10}{mark}")
        lines.append(f"audit chain: {self.audit}")
        lines.append("OK" if self.ok else "FAILED: nothing was committed")
        return "\n".join(lines)


def _converter(udt: str) -> Callable[[Any], Any]:
    """Source value -> the value asyncpg's binary COPY encodes for a column of type ``udt``."""
    if udt == "bool":
        return bool
    if udt in ("int2", "int4", "int8"):
        return int
    if udt == "float8":
        return float
    if udt == "timestamptz":
        return float
    if udt == "inet":
        return lambda value: ipaddress.ip_address(str(value))
    if udt == "bytea":
        return lambda value: bytes(value) if not isinstance(value, str) else value.encode()
    if udt == "jsonb":
        return lambda value: json.dumps(json.loads(value)) if isinstance(value, str) else value
    return lambda value: value.decode() if isinstance(value, bytes) else str(value)


def _source(path: str) -> sqlite3.Connection:
    if not Path(path).is_file():
        raise ImportRefusedError(f"no SQLite database at {path!r}")
    source = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    version = int(source.execute("PRAGMA user_version").fetchone()[0])
    if version != SOURCE_SCHEMA:
        source.close()
        raise ImportRefusedError(
            f"{path!r} is at SQLite schema {version}; this import reads schema {SOURCE_SCHEMA}. "
            "Start NetCoreNOC v0.30.0 on it once, so it applies its own migrations, then import."
        )
    verdict = str(source.execute("PRAGMA integrity_check").fetchone()[0])
    if verdict.lower() != "ok":
        source.close()
        raise ImportRefusedError(
            f"{path!r} fails PRAGMA integrity_check ({verdict}); restore a backup"
        )
    return source


def _rows(
    source: sqlite3.Connection, table: str, columns: list[str], kinds: dict[str, str]
) -> Iterator[tuple[Any, ...]]:
    """The source table's rows as target tuples, converted per column; NULL stays NULL."""
    available = {str(r[1]) for r in source.execute(f'PRAGMA table_info("{table}")')}
    select = []
    for column in columns:
        if column in available:
            select.append(f'"{column}"')
        elif column == "ne_id" and "device_id" in available:  # device and ne shared ids
            select.append('"device_id"')
        else:
            select.append("NULL")
    if table in ("alarm", "dataset_observation") and "ne_id" in columns:
        select[columns.index("ne_id")] = 'COALESCE("ne_id", "device_id")'
    convert = [_converter(kinds[column]) for column in columns]
    cursor = source.execute(  # nosec B608 - table and column names are this module's constants
        f'SELECT {", ".join(select)} FROM "{table}"'
    )
    for row in cursor:
        yield tuple(None if v is None else f(v) for f, v in zip(convert, row, strict=True))


async def import_sqlite(store: StoreBase, path: str) -> ImportReport:
    """Copy ``path`` into the store's empty schema in one transaction (the module docstring)."""
    from netcorenoc.crosscutting import audit

    started = time.monotonic()
    source = _source(path)
    report = ImportReport(source=path)
    db = store._db
    raw = await asyncpg.connect(db.dsn, server_settings={"search_path": db.schema})
    try:
        # Native binary codecs for COPY, and the store's instant codec: epoch seconds in.
        await raw.set_type_codec(
            "timestamptz",
            schema="pg_catalog",
            encoder=_encode_instant,
            decoder=_decode_instant,
            format="tuple",
        )
        history = [
            name
            for _, name in TABLES
            if name not in SEEDED and await raw.fetchval(f'SELECT EXISTS (SELECT 1 FROM "{name}")')  # nosec B608
        ]
        if history:
            raise ImportRefusedError(
                f"schema {db.schema!r} already holds history ({', '.join(history[:5])}...); "
                "import only into a new, empty schema"
            )
        async with raw.transaction():
            for table in _GUARDED_SEEDS:
                await raw.execute(f"ALTER TABLE {table} DISABLE TRIGGER {table}_append_only")
            for table in SEEDED:
                await raw.execute(f"DELETE FROM {table}")  # nosec B608 - a constant
            for table in _GUARDED_SEEDS:
                await raw.execute(f"ALTER TABLE {table} ENABLE TRIGGER {table}_append_only")
            for source_table, target in TABLES:
                await _copy(raw, source, source_table, target, report)
            await _reset_identities(raw)
            # The chain as PostgreSQL now holds it, verified before the transaction commits: a
            # value that did not survive the conversion fails here and nothing is written.
            chain = audit.verify_rows(
                await raw.fetch(
                    "SELECT id, ts, actor, role, source_ip, action, object_type, object_id, "
                    "outcome, details, prev_hash, entry_hash FROM audit_log ORDER BY id"
                )
            )
            report.audit = chain.render()
            if not chain.ok:
                raise ImportRefusedError(f"the imported audit log does not verify: {report.audit}")
        report.seconds = time.monotonic() - started
    finally:
        await raw.close()
        source.close()
    report.ok = True
    async with store.lock:
        await audit.write_event(
            store,
            ts=time.time(),
            actor="cli",
            role=None,
            source_ip=None,
            action="db.import",
            outcome="ok" if report.ok else "error",
            object_type="database",
            object_id=db.schema,
            details={"source": Path(path).name, "rows": sum(r[2] for r in report.tables)},
        )
        await store.commit()
    return report


async def _copy(
    raw: asyncpg.Connection,
    source: sqlite3.Connection,
    source_table: str,
    target: str,
    report: ImportReport,
) -> None:
    exists = source.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (source_table,)
    ).fetchone()
    if exists is None:
        report.tables.append((target, 0, 0))
        return
    described = await raw.fetch(
        "SELECT column_name, udt_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = $1 ORDER BY ordinal_position",
        target,
    )
    columns = [str(r["column_name"]) for r in described]
    kinds = {str(r["column_name"]): str(r["udt_name"]) for r in described}
    expected = int(source.execute(f'SELECT COUNT(*) FROM "{source_table}"').fetchone()[0])  # nosec B608
    if expected:
        await raw.copy_records_to_table(
            target, columns=columns, records=_rows(source, source_table, columns, kinds)
        )
    got = int(await raw.fetchval(f'SELECT COUNT(*) FROM "{target}"'))  # nosec B608 - a constant
    report.tables.append((target, expected, got))
    if got != expected:
        raise ImportRefusedError(f"{target}: {got} rows imported of {expected}")


async def _reset_identities(raw: asyncpg.Connection) -> None:
    """Move every identity column's sequence past the largest id the import wrote."""
    for row in await raw.fetch(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND is_identity = 'YES'"
    ):
        table, column = str(row["table_name"]), str(row["column_name"])
        await raw.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', '{column}'), "  # nosec B608
            f'COALESCE(MAX("{column}"), 1), MAX("{column}") IS NOT NULL) FROM "{table}"'
        )
