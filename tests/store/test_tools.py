"""`tools/pgtemp.py` and `tools/db_bench.py` — the database the suite runs on, and the benchmark
behind ADR #452's measurements. Small runs: each phase reports, and nothing leaks a credential."""

from __future__ import annotations

import argparse
import asyncio

import asyncpg

from netcorenoc.store import Store

import db_bench
import pgtemp


async def _tables(url: str) -> set[str]:
    conn = await asyncpg.connect(url)
    try:
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'netcorenoc'"
        )
        return {row["table_name"] for row in rows}
    finally:
        await conn.close()


async def _exists(name: str) -> bool:
    conn = await asyncpg.connect(pgtemp.url_for("postgres"))
    try:
        return bool(await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name))
    finally:
        await conn.close()


def test_a_temporary_database_is_migrated_and_dropped_after_the_block() -> None:
    with pgtemp.database() as url:
        name = url.rsplit("/", 1)[-1].split("?", 1)[0]
        tables = asyncio.run(_tables(url))
        assert {"alarm", "ne", "situation", "audit_log", "schema_migrations"} <= tables
        assert "device" not in tables, "the alarm's device table was folded into `ne` (0031)"
    assert not asyncio.run(_exists(name))


def test_an_empty_database_has_no_schema_until_a_store_opens_it() -> None:
    with pgtemp.empty_database() as url:
        assert asyncio.run(_tables(url)) == set()

        async def first_boot() -> int:
            store = Store(url, readers=1)
            await store.open()
            try:
                return await store.schema_version()
            finally:
                await store.close()

        assert asyncio.run(first_boot()) == Store.latest_schema_version()


def test_redact_masks_a_password_and_leaves_a_url_without_one_alone() -> None:
    assert pgtemp.redact("postgresql://u:s3cret@db:5432/x?sslmode=require") == (
        "postgresql://u:***@db:5432/x?sslmode=require"
    )
    plain = "postgresql://u@/x?host=%2Fvar%2Frun%2Fpostgresql"
    assert pgtemp.redact(plain) == plain


def test_the_benchmark_reports_every_phase(db_url: str) -> None:
    args = argparse.Namespace(
        sample_days=0.05,
        traps=150,
        devices=20,
        classes=6,
        readers=1,
        think_ms=20.0,
        repeat=1,
        timeout=120.0,
    )
    report = asyncio.run(db_bench.bench(args, db_url))
    assert report["samples"]["rows"] == 144
    assert report["ingest"]["traps_per_s"] > 0
    assert set(report["reads"]) == set(db_bench.ROUTES)
    assert report["size_bytes"] > 0
    assert report["db"] == pgtemp.redact(db_url), "the report never carries a password"
