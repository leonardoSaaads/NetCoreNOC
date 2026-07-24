"""Command-line entry point: ``python -m netcorenoc <command>``.

Subcommands:

- ``audit verify`` — walk the audit hash chain and report the first broken link.
- ``audit export`` — emit the audit log as NDJSON plus the final chain hash.

The trap correlator itself still runs via ``python -m netcorenoc.main`` (unchanged).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from netcorenoc import audit
from netcorenoc.store import Store


def _db_path() -> str:
    new = os.environ.get("NETCORENOC_DB")
    if new is not None:
        return new
    legacy = os.environ.get("OPTICORR_DB")
    if legacy is not None:
        print(
            "warning: OPTICORR_DB is deprecated and will be removed in v0.6.0; use NETCORENOC_DB",
            file=sys.stderr,
        )
        return legacy
    return "netcorenoc.db"


async def _verify(db_path: str) -> int:
    store = Store(db_path)
    await store.open()
    try:
        result = await audit.verify_chain(store)
    finally:
        await store.close()
    print(result.render())
    return 0 if result.ok else 1


async def _export(db_path: str) -> int:
    store = Store(db_path)
    await store.open()
    try:
        lines, final_hash = await audit.export_ndjson(store)
    finally:
        await store.close()
    for line in lines:
        print(line)
    print(f"# final_chain_hash {final_hash}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="netcorenoc", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    audit_parser = sub.add_parser("audit", help="audit-log tooling")
    audit_sub = audit_parser.add_subparsers(dest="audit_command", required=True)
    audit_sub.add_parser("verify", help="verify the audit hash chain")
    audit_sub.add_parser("export", help="export the audit log as NDJSON")
    args = parser.parse_args(argv)

    db_path = _db_path()
    if args.command == "audit":
        if args.audit_command == "verify":
            return asyncio.run(_verify(db_path))
        if args.audit_command == "export":
            return asyncio.run(_export(db_path))
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
