"""The runtime role (ADR #453): what the appliance's own connection may do to its schema.

Every attack the database audit demonstrated against a role that owned the schema — disabling the
append-only triggers, truncating, deleting audit rows under the prune setting, dropping the trigger
function, adding a table — is run here as the runtime role and must be refused by the server, while
everything the appliance does in operation still works.

The two roles are taken with the ``role`` startup parameter on the suite's superuser connection, so
the test needs no passwords; the server checks every privilege against the role in effect.
"""

from __future__ import annotations

import asyncio
import itertools
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass

import asyncpg
import pytest

from netcorenoc.crosscutting import audit
from netcorenoc.store import Store, StoreRefusedError

import pgtemp

_count = itertools.count(1)


@dataclass
class Roles:
    owner_url: str
    app_url: str
    app: str


def _as(url: str, role: str) -> str:
    return f"{url}{'&' if '?' in url else '?'}role={role}"


@pytest.fixture
async def roles() -> AsyncIterator[Roles]:
    n = f"{os.getpid()}_{next(_count)}"
    owner, app, name = f"nc_owner_{n}", f"nc_app_{n}", f"netcorenoc_priv_{n}"
    statements = [
        f"CREATE ROLE {owner} NOLOGIN",
        f"CREATE ROLE {app} NOLOGIN",
        f'CREATE DATABASE "{name}" OWNER {owner}',
    ]
    await asyncio.to_thread(pgtemp._run, *statements)
    base = pgtemp.url_for(name)
    try:
        yield Roles(_as(base, owner), _as(base, app), app)
    finally:
        await asyncio.to_thread(
            pgtemp._run,
            f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)',
            f"DROP ROLE IF EXISTS {app}",
            f"DROP ROLE IF EXISTS {owner}",
        )


async def _migrated(roles: Roles) -> Store:
    store = Store(roles.app_url, owner_dsn=roles.owner_url, readers=1)
    await store.open()
    return store


async def test_the_runtime_role_cannot_undo_the_append_only_rules(roles: Roles) -> None:
    store = await _migrated(roles)
    try:
        assert not store.owns_schema, "the appliance's role must not own its schema"
        async with store.lock:
            await audit.write_event(
                store,
                ts=1.0,
                actor="a",
                role="admin",
                source_ip="-",
                action="user.create",
                outcome="ok",
                details={},
            )
            await store.commit()
    finally:
        await store.close()
    raw = await asyncpg.connect(roles.app_url, server_settings={"search_path": "netcorenoc"})
    try:
        attacks = [
            "DELETE FROM audit_log",
            "UPDATE audit_log SET actor = 'someone else'",
            "TRUNCATE audit_log",
            "TRUNCATE league_decision",
            "ALTER TABLE audit_log DISABLE TRIGGER ALL",
            "DROP FUNCTION forbid_change() CASCADE",
            "CREATE TABLE stash (secret text)",
            "INSERT INTO schema_migrations (version, name, checksum) VALUES (99, 'x', 'x')",
        ]
        for attack in attacks:
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                await raw.execute(attack)
        # A grant by a role that owns nothing grants nothing (the server only warns).
        await raw.execute("GRANT SELECT ON app_user TO PUBLIC")
        assert not await raw.fetchval(
            "SELECT count(*) FROM information_schema.role_table_grants "
            "WHERE table_name = 'app_user' AND grantee = 'PUBLIC'"
        )
        # The prune setting is a session variable anyone may set; without DELETE it opens nothing.
        async with raw.transaction():
            await raw.execute("SELECT set_config('netcorenoc.prune', 'audit_log', true)")
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                await raw.execute("DELETE FROM audit_log")
        # Pruning goes through the owner's function, and never reaches the recent past.
        with pytest.raises(asyncpg.exceptions.RestrictViolationError, match="less than a day"):
            await raw.fetchval("SELECT prune_audit_log(extract(epoch FROM now())::float8)")
        assert await raw.fetchval("SELECT prune_audit_log(2.0)") == 1
    finally:
        await raw.close()


async def test_the_runtime_role_still_does_everything_the_appliance_does(roles: Roles) -> None:
    store = await _migrated(roles)
    try:
        async with store.lock:
            uid = await store.create_user("vera", "x", "viewer", True, 1.0)
            await store.set_user_email(uid, "Vera@Example.com", 1.0)
            await store.set_meta("config.email", '{"password": "smtp-secret"}')
            await store.seal_plaintext()
            await store.check_vault()
            await store.prune(1_000_000.0, 7 * 86400.0)
            await store.commit()
        assert (await store.recovery_accounts("vera@example.com"))[0]["id"] == uid
        async with store.reading():
            assert await store.list_users()
    finally:
        await store.close()


async def test_a_runtime_role_on_a_current_schema_runs_no_ddl(roles: Roles) -> None:
    """Started without the owner's URL on a schema the owner already migrated: no CREATE is
    attempted, so the role needs no CREATE privilege at all."""
    first = Store(roles.app_url, owner_dsn=roles.owner_url, readers=1)
    await first.open()
    await first.close()
    store = Store(roles.app_url, readers=1)
    await store.open()
    try:
        assert await store.schema_version() == Store.latest_schema_version()
    finally:
        await store.close()


async def test_a_runtime_role_is_refused_pending_migrations_with_the_command_to_run(
    roles: Roles,
) -> None:
    raw = await asyncpg.connect(roles.owner_url)
    try:
        await raw.execute("CREATE SCHEMA netcorenoc")
    finally:
        await raw.close()
    store = Store(roles.app_url, readers=1)
    with pytest.raises(StoreRefusedError, match="db migrate"):
        await store.open()
    await store.close()


async def test_truncate_is_refused_even_for_the_owner(store: Store) -> None:
    """0032's statement-level trigger: one role owning everything still cannot empty the
    append-only tables in one statement (the row triggers never fire for TRUNCATE)."""
    with pytest.raises(asyncpg.exceptions.RestrictViolationError, match="append-only"):
        async with store.lock:
            await store.conn.execute("TRUNCATE audit_log")
    await store.rollback()
