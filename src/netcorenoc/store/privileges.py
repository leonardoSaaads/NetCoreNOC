"""The runtime role: what the appliance may do to its schema while it runs (ADR #453).

One role owning every table can alter, drop and truncate them, disable the append-only triggers and
rewrite the audit log — and so can any SQL injection or code-execution bug running as it. The
schema is therefore owned by one role and used by another:

* the **owner** (``NETCORENOC_DATABASE_OWNER_URL``, or ``db migrate`` run as it) creates the schema,
  applies the migrations and grants;
* the **runtime role** (``NETCORENOC_DATABASE_URL``) gets ``SELECT, INSERT, UPDATE, DELETE`` on the
  tables, and nothing more: no ``TRUNCATE``, ``REFERENCES`` or ``TRIGGER``, no DDL (it owns
  nothing), no write to ``schema_migrations``, and no ``UPDATE`` or ``DELETE`` on the twelve
  append-only tables — the triggers stay as a second wall, and the audit log is pruned only
  through ``prune_audit_log``, which runs as the owner and refuses the recent past.

:func:`grant_runtime` is idempotent and re-run after every migration, so a table a later release
adds is granted the same way. One role doing both (no owner URL) still works, and is reported.
"""

from __future__ import annotations

import asyncpg

__all__ = ["append_only_tables", "grant_runtime"]


async def append_only_tables(raw: asyncpg.Connection, schema: str) -> list[str]:
    """The tables guarded by ``forbid_change()``: no update, no delete, ever."""
    rows = await raw.fetch(
        "SELECT DISTINCT c.relname FROM pg_trigger t "
        "JOIN pg_class c ON c.oid = t.tgrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_proc p ON p.oid = t.tgfoid "
        "WHERE n.nspname = $1 AND p.proname = 'forbid_change' ORDER BY c.relname",
        schema,
    )
    return [str(r[0]) for r in rows]


async def grant_runtime(owner: asyncpg.Connection, schema: str, role: str) -> bool:
    """Grant ``role`` the runtime privileges on ``schema``, as its owner. False when ``role`` is
    the owner itself, which needs no grant (and cannot be narrowed)."""
    if role == await owner.fetchval("SELECT current_user"):
        return False
    quoted_schema = await owner.fetchval("SELECT quote_ident($1)", schema)
    quoted_role = await owner.fetchval("SELECT quote_ident($1)", role)
    statements = [
        f"GRANT USAGE ON SCHEMA {quoted_schema} TO {quoted_role}",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA {quoted_schema} FROM {quoted_role}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {quoted_schema} "
        f"TO {quoted_role}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {quoted_schema} TO {quoted_role}",
        f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {quoted_schema} TO {quoted_role}",
        f"REVOKE INSERT, UPDATE, DELETE ON {quoted_schema}.schema_migrations FROM {quoted_role}",
    ]
    for table in await append_only_tables(owner, schema):
        quoted_table = await owner.fetchval("SELECT quote_ident($1)", table)
        statements.append(
            f"REVOKE UPDATE, DELETE ON {quoted_schema}.{quoted_table} FROM {quoted_role}"
        )
    async with owner.transaction():
        for statement in statements:
            await owner.execute(statement)  # nosec B608 - identifiers quoted by the server
    return True
