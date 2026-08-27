"""Command-line entry point: ``python -m netcorenoc <command>``.

Subcommands:

- ``audit verify`` — walk the audit hash chain and report the first broken link.
- ``audit export`` — emit the audit log as NDJSON plus the final chain hash.
- ``dataset bias`` — the feedback-dataset bias report (v0.8.0). **Deterministic**, so it doubles
  as a `make qa` guard: run over a fixture with its output compared byte-for-byte, it goes red the
  day capture changes shape. Emits aggregates only.
- ``dataset stats`` — what capture currently costs, in rows.
- ``dataset agreement`` — **how well the champion already agrees with the operators** (v0.9.0),
  conditioned by bag size, storm, mixed-versus-uniform, scope, operator and capture provenance.
  A sibling subcommand rather than a section of ``dataset bias`` (DECISIONS #115): the two reports
  answer different questions, each is a byte-for-byte gate, and a gate should go red for one reason.
  Deterministic, aggregates only, and it trains nothing.
- ``dataset shadow`` — **the shadow-mode report** (v0.9.0): the sufficiency verdict first, then
  both label-derivation policies, partition-level over/under-merge against the human verdicts,
  bag-level calibration, the admission filter run against the champion too, and the
  training/serving skew rate. Deterministic; it re-derives offline and **fits nothing**.

The trap correlator itself still runs via ``python -m netcorenoc.main`` (unchanged).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

from netcorenoc import model_version, promotion, scoring, seal
from netcorenoc.crosscutting import audit
from netcorenoc.engine.report import (
    agreement,
    agreement_report,
    bias,
    bias_report,
    shadow_render,
    shadow_report,
)
from netcorenoc.main import legacy_env_error, legacy_env_names
from netcorenoc.store import Store


def _db_path() -> str:
    """NETCORENOC_DB, or the default file. The OPTICORR_DB alias was removed in v0.6.0.

    The CLI refuses rather than silently reading the wrong database: pointing `audit verify` at
    the default file while the operator believes it is checking their real one would produce a
    confidently wrong answer about the integrity of the audit chain."""
    legacy = legacy_env_names()
    if legacy:
        print(str(legacy_env_error(tuple(legacy))), file=sys.stderr)
        raise SystemExit(2)
    return os.environ.get("NETCORENOC_DB", "netcorenoc.db")


async def _verify(db_path: str) -> int:
    store = Store(db_path)
    await store.open()
    try:
        result = await audit.verify_chain(store)
    finally:
        await store.close()
    print(result.render())
    return 0 if result.ok else 1


async def _bias(db_path: str) -> int:
    """The bias report. A CLI subcommand rather than a route: it adds no HTTP surface to the
    dataset's scope bypass, and — the stronger reason — a deterministic CLI report can be a
    guard, which no UI card could ever be."""
    store = Store(db_path)
    await store.open()
    try:
        async with store.lock:
            measurements = await bias.collect(store)
    finally:
        await store.close()
    print(bias_report.render(measurements), end="")
    return 0


async def _agreement(db_path: str) -> int:
    """The champion-agreement report — v0.9.0's primary deliverable, and it needs no model.

    Same posture as `_bias`: no HTTP surface over a scope bypass, and a deterministic CLI report
    that `make qa` can compare byte-for-byte.
    """
    store = Store(db_path)
    await store.open()
    try:
        async with store.lock:
            measurements = await agreement.collect(store)
    finally:
        await store.close()
    print(agreement_report.render(measurements), end="")
    return 0


async def _shadow(db_path: str) -> int:
    """The shadow-mode report. Same posture as `_bias` and `_agreement`: no HTTP surface over a
    scope bypass, and a deterministic CLI report `make qa` can compare byte-for-byte.

    **It fits nothing.** The report re-derives the challenger's opinion offline from stored
    features and from the coefficients a training run recorded; training happens in the engine's
    slow loop, and a report that trained would be a report whose numbers changed when it was read.
    """
    store = Store(db_path)
    await store.open()
    try:
        async with store.lock:
            measurements = await shadow_report.collect(store)
    finally:
        await store.close()
    print(shadow_render.render(measurements), end="")
    return 0


async def _dataset_stats(db_path: str) -> int:
    """What capture costs, in rows. Zero-config means the default is good, not that the operator
    is blind about what the appliance is storing."""
    store = Store(db_path)
    await store.open()
    try:
        async with store.lock:
            stats = await store.dataset_stats()
    finally:
        await store.close()
    for key in sorted(stats):
        print(f"{key:<32} {stats[key]}")
    window = stats.get("sink_window_days")
    if window is not None:
        print()
        print(f"The sink currently spans {window} days of traffic.")
        print("This is the window in which a label can still find its pairs. It is set by")
        print("whichever bound binds FIRST — the row cap or the age limit — and at most")
        print("traffic rates that is the ROW CAP, not the configured retention in days.")
    return 0


async def _promotion_list(db_path: str) -> int:
    """**What has this appliance been asked to deploy, and why was it refused?**

    The read half of the promotion surface, as a CLI rather than a screen (DECISIONS #163). Every
    decision, refusals included — a table of successes answers the smaller question.
    """
    store = Store(db_path)
    await store.open()
    try:
        async with store.lock:
            rows = await store.list_promotions(50)
            versions = await store.list_model_versions(50)
            summary = await seal.summary(store)
    finally:
        await store.close()

    # §4.3(4): printed beside every holdout number this project ever publishes.
    print(f"sealed-holdout query count: {summary.query_count}")
    print(f"ratified plan in force:     {promotion.RATIFIED_PLAN_SHA256}")
    print()
    print(f"model versions ({len(versions)}):")
    for row in versions:
        active = " ACTIVE" if row.get("active") else ""
        run = row["challenger_run_id"] or "-"
        print(f"  [{row['id']}] {row['kind']:<10} run={run:<4} {row['params_hash'][:16]}…{active}")
    print()
    print(f"promotion decisions ({len(rows)}):")
    if not rows:
        print("  (none — nothing has been proposed)")
    for row in rows:
        print(
            f"  [{row['id']}] {row['outcome']:<8} {row['verdict']:<22} "
            f"model={row['model_version_id']} by={row['approved_by']} "
            f"queries={row['query_count']}"
        )
        if row["refusal_reason"]:
            for line in str(row["refusal_reason"]).splitlines():
                print(f"        {line}")
    return 0


async def _promotion_register(db_path: str, kind: str, params: str, run_id: int | None) -> int:
    """Register an artefact. **Registering is not promoting**, and nothing here moves a pointer.

    The document is validated by the same per-kind validator the load path uses, so a payload that
    would be refused at load is refused at registration too — one validator, not two that could
    disagree.
    """
    try:
        model_version.validate_document(kind, scoring.CONTRACT_VERSION, params)
    except model_version.ModelPayloadError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    store = Store(db_path)
    await store.open()
    try:
        async with store.lock:
            new_id = await store.insert_model_version(
                kind=kind,
                contract_version=scoring.CONTRACT_VERSION,
                params_document=params,
                params_hash=model_version.params_hash(kind, scoring.CONTRACT_VERSION, params),
                challenger_run_id=run_id,
                created_by="cli",
                created_at=time.time(),
                note="registered from the CLI",
            )
            await store.commit()
    finally:
        await store.close()
    print(f"registered model version {new_id} ({kind})")
    print("This is an ARTEFACT, not a promotion. Nothing is running it, and nothing will until an")
    print("admin proposes it through POST /api/promotion and the server's verdict permits it.")
    return 0


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
    dataset_parser = sub.add_parser("dataset", help="feedback-dataset tooling (v0.8.0)")
    dataset_sub = dataset_parser.add_subparsers(dest="dataset_command", required=True)
    dataset_sub.add_parser("bias", help="the bias report (aggregates only, deterministic)")
    dataset_sub.add_parser("stats", help="what capture currently costs, in rows")
    dataset_sub.add_parser(
        "agreement", help="how well the champion already agrees with the operators (v0.9.0)"
    )
    dataset_sub.add_parser(
        "shadow", help="the shadow-mode report: sufficiency, both policies, skew (v0.9.0)"
    )
    promotion_parser = sub.add_parser("promotion", help="champion/challenger tooling (v0.11.0)")
    promotion_sub = promotion_parser.add_subparsers(dest="promotion_command", required=True)
    promotion_sub.add_parser(
        "list", help="what has been proposed and why each was refused (refusals included)"
    )
    register = promotion_sub.add_parser(
        "register", help="register a model version — an ARTEFACT, never a promotion"
    )
    register.add_argument("--kind", required=True, choices=sorted(model_version.SUPPORTED_KINDS))
    register.add_argument("--params", required=True, help="the parameter document, as JSON")
    register.add_argument("--challenger-run", type=int, default=None)
    args = parser.parse_args(argv)

    db_path = _db_path()
    if args.command == "audit":
        if args.audit_command == "verify":
            return asyncio.run(_verify(db_path))
        if args.audit_command == "export":
            return asyncio.run(_export(db_path))
    if args.command == "dataset":
        if args.dataset_command == "bias":
            return asyncio.run(_bias(db_path))
        if args.dataset_command == "stats":
            return asyncio.run(_dataset_stats(db_path))
        if args.dataset_command == "agreement":
            return asyncio.run(_agreement(db_path))
        if args.dataset_command == "shadow":
            return asyncio.run(_shadow(db_path))
    if args.command == "promotion":
        if args.promotion_command == "list":
            return asyncio.run(_promotion_list(db_path))
        if args.promotion_command == "register":
            return asyncio.run(
                _promotion_register(db_path, args.kind, args.params, args.challenger_run)
            )
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
