"""CLI (`python -m netcorenoc audit ...`), runtime config, and JSON logging coverage.

ADR #452: every command reads `NETCORENOC_DATABASE_URL`. A mistyped `.db` path is now a URL to a
database, or a schema name, with no NetCoreNOC tables in it; the backup is a `pg_dump` archive,
checked here by restoring it into an empty database and verifying the audit chain there.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import subprocess  # nosec B404 - runs pg_restore, a fixed binary
from collections.abc import Callable, Iterator
from pathlib import Path

import asyncpg
import pytest

import pgtemp
from netcorenoc import __main__ as cli
from netcorenoc.crosscutting import audit
from netcorenoc.crosscutting.logsetup import JsonFormatter
from netcorenoc.crosscutting.runtime import RuntimeConfig
from netcorenoc.store import Store


def _use(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    """Point the CLI at ``url``, as the appliance's environment does (ADR #452)."""
    monkeypatch.delenv("NETCORENOC_DB", raising=False)
    monkeypatch.delenv("NETCORENOC_DB_SCHEMA", raising=False)
    monkeypatch.setenv("NETCORENOC_DATABASE_URL", url)


@pytest.fixture
def empty_database() -> Iterator[Callable[[], str]]:
    """Databases with no NetCoreNOC schema in them, each dropped after the test."""
    names: list[str] = []

    def make() -> str:
        names.append(f"netcorenoc_cli_{os.getpid()}_{secrets.token_hex(4)}")
        pgtemp._run(f'CREATE DATABASE "{names[-1]}"')
        return pgtemp.url_for(names[-1])

    yield make
    for name in names:
        pgtemp._run(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


async def _schema_exists(url: str, schema: str) -> bool:
    raw = await asyncpg.connect(url)
    try:
        return bool(
            await raw.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = $1)", schema
            )
        )
    finally:
        await raw.close()


async def _seed(db: str, actions: list[str]) -> None:
    store = Store(db)
    await store.open()
    async with store.lock:
        for action in actions:
            await audit.write_event(
                store,
                ts=1.0,
                actor="a",
                role="admin",
                source_ip="-",
                action=action,
                outcome="ok",
                details={},
            )
        await store.commit()
    await store.close()


def test_audit_verify_cli_empty(
    db_url: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, db_url)
    asyncio.run(_seed(db_url, []))
    rc = cli.main(["audit", "verify"])
    assert rc == 0
    assert "audit chain OK" in capsys.readouterr().out


def test_audit_verify_cli_detects_tamper(
    db_url: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, db_url)
    asyncio.run(_seed(db_url, ["login.ok", "feedback", "logout"]))

    async def tamper() -> None:
        """Someone with the database's credentials, behind the appliance's back."""
        raw = await asyncpg.connect(db_url)
        try:
            await raw.execute("DROP TRIGGER audit_log_append_only ON netcorenoc.audit_log")
            await raw.execute("UPDATE netcorenoc.audit_log SET actor = 'x' WHERE id = 2")
        finally:
            await raw.close()

    asyncio.run(tamper())
    rc = cli.main(["audit", "verify"])
    assert rc == 1
    assert "BROKEN at id 2" in capsys.readouterr().out


def test_audit_export_cli(
    db_url: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, db_url)
    asyncio.run(_seed(db_url, ["login.ok", "logout"]))
    rc = cli.main(["audit", "export"])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.count("\n") == 2  # one NDJSON line per row
    assert "final_chain_hash" in captured.err


def test_f26_cli_refuses_a_removed_legacy_env_alias(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """F26: the audit CLI refuses rather than silently reading the default database — pointing
    `audit verify` at the wrong file would give a confidently wrong integrity answer."""
    monkeypatch.delenv("NETCORENOC_DB", raising=False)
    monkeypatch.setenv("OPTICORR_DB", str(tmp_path / "c.db"))
    with pytest.raises(SystemExit) as caught:
        cli.main(["audit", "verify"])
    assert caught.value.code == 2
    err = capsys.readouterr().err
    assert "OPTICORR_DB" in err and "NETCORENOC_DB" in err and "MIGRATION.md" in err


def _refuses(
    url: str, schema: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, url)
    monkeypatch.setenv("NETCORENOC_DB_SCHEMA", schema)
    with pytest.raises(SystemExit) as caught:
        cli.main(["audit", "verify"])
    assert caught.value.code == 2
    assert "no NetCoreNOC database" in capsys.readouterr().err
    assert not asyncio.run(_schema_exists(url, schema)), "refusing must not leave an empty schema"


def test_the_cli_refuses_a_database_that_is_not_there(
    empty_database: Callable[[], str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v0.28.0. Opening a missing path creates an empty database, and `audit verify` then said
    `audit chain OK` about a file that was not the appliance's — the confidently wrong integrity
    answer F26 exists to prevent, reached by a typo instead of a legacy variable. Since ADR #452
    the typo is a URL to a database with no NetCoreNOC schema, where opening would create one."""
    _refuses(empty_database(), "netcorenoc", capsys, monkeypatch)


def test_the_cli_refuses_a_schema_that_is_not_there(
    db_url: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same typo one level down: the right database, a mistyped `NETCORENOC_DB_SCHEMA`."""
    _refuses(db_url, "netcorenoc_typo", capsys, monkeypatch)


def test_admin_reset_password_is_one_time_audited_and_revokes_sessions(
    db_url: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The forgotten-password path when the one who forgot is the only admin (v0.28.0)."""
    from netcorenoc.crosscutting.auth import verify_password

    db = db_url
    _use(monkeypatch, db)

    async def seed() -> int:
        store = Store(db)
        await store.open()
        uid = await store.create_user(
            username="alice", password_hash="x", role="admin", must_change_password=False, now=1.0
        )
        await store.create_session("h" * 64, int(uid), 1.0, 1.0, 1e10, 1e10, None)
        await store.commit()
        await store.close()
        return int(uid)

    uid = asyncio.run(seed())
    assert cli.main(["admin", "reset-password", "alice"]) == 0
    password = capsys.readouterr().out.split("alice: ", 1)[1].split()[0]

    async def check() -> None:
        store = Store(db)
        await store.open()
        user = await store.get_user(uid)
        assert user is not None and verify_password(password, str(user["password_hash"]))
        assert user["must_change_password"] == 1
        assert await store.get_session("h" * 64) is None, "the old sessions must be revoked"
        result = await audit.verify_chain(store)
        assert result.ok
        cur = await store.conn.execute("SELECT action, actor, object_id FROM audit_log")
        assert [tuple(r) for r in await cur.fetchall()] == [("user.update", "cli", str(uid))]
        await store.close()

    asyncio.run(check())
    assert cli.main(["admin", "reset-password", "nobody"]) == 1


def test_backup_copies_a_consistent_database_and_never_overwrites(
    db_url: str,
    empty_database: Callable[[], str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use(monkeypatch, db_url)
    asyncio.run(_seed(db_url, ["login.ok", "logout"]))
    target = tmp_path / "netcorenoc.dump"
    assert cli.main(["backup", str(target)]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"backup written to {target} ") and out.rstrip().endswith(": ok"), out
    restored = empty_database()
    subprocess.run(  # nosec B603 - a fixed binary, the test's own database and file
        ["pg_restore", f"--dbname={restored}", str(target)], check=True
    )
    _use(monkeypatch, restored)
    assert cli.main(["audit", "verify"]) == 0, "the copy carries the audit chain intact"
    assert "audit chain OK: 2 entries verified" in capsys.readouterr().out
    assert cli.main(["backup", str(target)]) == 2, "an existing file is never overwritten"


def test_the_server_entry_point_answers_help_instead_of_starting(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`python -m netcorenoc.main --help` started the appliance (v0.28.0)."""
    from netcorenoc import main as server

    for argv, code in ((["--help"], 0), (["--version"], 0), (["--port", "1"], 2)):
        with pytest.raises(SystemExit) as caught:
            server.main(argv)
        assert caught.value.code == code, argv
    out = capsys.readouterr()
    assert "NETCORENOC_TRAP_PORT" in out.out and "takes no arguments" in out.err


def test_cli_requires_subcommand() -> None:
    with pytest.raises(SystemExit):
        cli.main([])
    with pytest.raises(SystemExit):
        cli.main(["audit"])


# -- runtime config ------------------------------------------------------------------


def test_runtime_networks_parsing() -> None:
    cfg = RuntimeConfig(allowlist="10.0.0.0/8,192.168.1.0/24", retention_days=7.0)
    nets = cfg.networks()
    assert nets is not None and len(nets) == 2
    assert RuntimeConfig(allowlist="", retention_days=7.0).networks() is None


def test_runtime_apply_allowlist_notifies() -> None:
    seen: list[object] = []
    cfg = RuntimeConfig(allowlist="", retention_days=7.0, on_allowlist_change=seen.append)
    cfg.apply_allowlist("10.0.0.0/8")
    assert cfg.allowlist == "10.0.0.0/8"
    assert len(seen) == 1 and seen[0] is not None  # the parsed networks were pushed


# -- JSON logging --------------------------------------------------------------------


def test_json_formatter_emits_json() -> None:
    import json
    import logging

    record = logging.LogRecord("netcorenoc", logging.INFO, __file__, 1, "hello", None, None)
    payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "hello" and payload["level"] == "INFO"


# --- v0.8.0: the dataset CLI ----------------------------------------------------------------


async def _dataset_fixture(db: str) -> None:
    """A database with a capture run and one labelled situation, built without a wall clock."""
    from biasfixture import build_fixture

    store = Store(db)
    await store.open()
    await build_fixture(store)
    await store.close()


def test_dataset_bias_cli_emits_the_report(
    db_url: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`python -m netcorenoc dataset bias` — the release's deliverable, over the CLI it ships on."""
    _use(monkeypatch, db_url)
    asyncio.run(_dataset_fixture(db_url))
    assert cli.main(["dataset", "bias"]) == 0
    out = capsys.readouterr().out
    assert "NetCoreNOC feedback-dataset bias report" in out
    assert "EFFECTIVE SAMPLE SIZE" in out
    assert "*n* IS THE NUMBER OF INDEPENDENT BAGS, NOT THE NUMBER OF PAIRS." in out
    assert "what this report CANNOT tell you" in out


def test_dataset_bias_cli_is_deterministic_across_invocations(
    db_url: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two separate CLI runs must be byte-identical.

    Stronger than an in-process repeat, which would still pass if the report read a clock once and
    cached it. This is what makes the report usable as a gate.
    """
    _use(monkeypatch, db_url)
    asyncio.run(_dataset_fixture(db_url))
    assert cli.main(["dataset", "bias"]) == 0
    first = capsys.readouterr().out
    assert cli.main(["dataset", "bias"]) == 0
    assert capsys.readouterr().out == first


def test_dataset_stats_cli_reports_the_observed_window(
    db_url: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """§7.3: what capture costs, **and the window the operator actually has**.

    Not the configured `sink_days`, which the row cap almost always reaches first — an operator
    reading `21` and concluding they have three weeks to label something would be wrong, and
    nothing else would tell them.
    """
    _use(monkeypatch, db_url)
    asyncio.run(_dataset_fixture(db_url))
    assert cli.main(["dataset", "stats"]) == 0
    out = capsys.readouterr().out
    assert "dataset_pair.dataset" in out
    assert "capture_run" in out


# --- v0.9.0: the champion-agreement CLI -----------------------------------------------------


def test_dataset_agreement_cli_emits_the_report(
    db_url: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`python -m netcorenoc dataset agreement` — v0.9.0's primary deliverable, on the CLI it
    ships on. A sibling of `dataset bias`, not a section of it (DECISIONS #115)."""
    _use(monkeypatch, db_url)
    asyncio.run(_dataset_fixture(db_url))
    assert cli.main(["dataset", "agreement"]) == 0
    out = capsys.readouterr().out
    assert "NetCoreNOC champion-agreement report" in out
    assert "AGREEMENT IS NOT CORRECTNESS" in out
    assert "A UNIFORM BAG CONTAINED NO DECISION" in out
    assert "what this report CANNOT tell you" in out
    # It is its own report, not the bias one wearing a new heading.
    assert "feedback-dataset bias report" not in out


def test_dataset_agreement_cli_is_deterministic_across_invocations(
    db_url: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two separate processes, byte-identical — including the bootstrap interval, which is the
    only part of the report with a generator in it."""
    _use(monkeypatch, db_url)
    asyncio.run(_dataset_fixture(db_url))
    assert cli.main(["dataset", "agreement"]) == 0
    first = capsys.readouterr().out
    assert cli.main(["dataset", "agreement"]) == 0
    assert capsys.readouterr().out == first


def test_dataset_agreement_on_an_empty_database_reports_nothing_rather_than_zero(
    db_url: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh appliance has no labels. `(none)` is the honest rendering; `0.0%` would be a
    claim about a population that does not exist."""
    _use(monkeypatch, db_url)  # a fresh, migrated, empty database: a new appliance's
    assert cli.main(["dataset", "agreement"]) == 0
    out = capsys.readouterr().out
    assert "champion agreement" in out
    assert "(none)" in out
    assert "0.0%" not in out


# --- v0.9.0: the shadow-mode CLI ------------------------------------------------------------


def test_dataset_shadow_cli_leads_with_the_sufficiency_verdict(
    db_url: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`python -m netcorenoc dataset shadow`. On the bias fixture the corpus is far below every
    floor, so the report must say INSUFFICIENT, fit nothing, and say when."""
    _use(monkeypatch, db_url)
    asyncio.run(_dataset_fixture(db_url))
    assert cli.main(["dataset", "shadow"]) == 0
    out = capsys.readouterr().out
    assert "NetCoreNOC shadow-mode report" in out
    assert "THE BUILT-IN SCORER DECIDES EVERYTHING" in out
    assert "INSUFFICIENT" in out
    assert "No model was fitted" in out
    assert out.index("SUFFICIENCY") < out.index("the corpus")


def test_dataset_shadow_cli_is_deterministic_across_invocations(
    db_url: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two separate processes, byte-identical — the admission filter does not run on an
    insufficient corpus, so there is no measured duration to normalise here."""
    _use(monkeypatch, db_url)
    asyncio.run(_dataset_fixture(db_url))
    assert cli.main(["dataset", "shadow"]) == 0
    first = capsys.readouterr().out
    assert cli.main(["dataset", "shadow"]) == 0
    assert capsys.readouterr().out == first


def test_dataset_shadow_on_an_empty_database_is_insufficient_not_broken(
    db_url: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh appliance has no labels. That is INSUFFICIENT with an `undefined` projection —
    never a crash, and never a number extrapolated from nothing."""
    _use(monkeypatch, db_url)  # a fresh, migrated, empty database: a new appliance's
    assert cli.main(["dataset", "shadow"]) == 0
    out = capsys.readouterr().out
    assert "INSUFFICIENT" in out
    assert "undefined (no measurable labelling rate yet)" in out


def test_dataset_stats_on_an_empty_database_says_nothing_it_cannot_know(
    db_url: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh appliance has no sink, so there is no window to report — and it invents none."""
    _use(monkeypatch, db_url)  # a fresh, migrated, empty database: a new appliance's
    assert cli.main(["dataset", "stats"]) == 0
    out = capsys.readouterr().out
    assert "sink_window_days                 None" in out
    assert "The sink currently spans" not in out


# --- the promotion CLI (v0.11.0): the other half of "route plus CLI, no UI" ---------------


def test_promotion_list_on_an_appliance_that_has_been_asked_nothing(
    db_url: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The zero state is the one every operator sees first, so it is the one that must read well.

    **The seal's query count is printed first**, per `PREREGISTRATION-0.10.0.md` §4.3(4): *beside
    every holdout number this project ever publishes*. An empty promotion history is not silence —
    it says so.
    """
    _use(monkeypatch, db_url)
    assert cli.main(["promotion", "list"]) == 0
    out = capsys.readouterr().out
    assert "sealed-holdout query count: 0" in out
    assert "ratified plan in force:" in out
    assert "(none — nothing has been proposed)" in out


def test_promotion_register_creates_an_artefact_and_says_it_is_not_a_promotion(
    db_url: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Registering is not promoting**, and the CLI says so in the operator's own words rather
    than leaving it to be inferred from a table name."""
    _use(monkeypatch, db_url)
    params = (
        '{"intercept":-1.5,"decay":2.0,"class_affinity":1.2,"entity_affinity":0.8,"threshold":0.0}'
    )
    assert cli.main(["promotion", "register", "--kind", "logistic", "--params", params]) == 0
    out = capsys.readouterr().out
    assert "registered model version 1 (logistic)" in out
    assert "ARTEFACT, not a promotion" in out

    assert cli.main(["promotion", "list"]) == 0
    listing = capsys.readouterr().out
    assert "logistic" in listing
    assert "ACTIVE" not in listing, "registering must not have activated anything"


def test_promotion_register_refuses_a_degenerate_payload_through_the_same_validator(
    db_url: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One validator, not two that could disagree: a payload the load path would refuse is refused
    at registration, with the same message and a non-zero exit."""
    _use(monkeypatch, db_url)
    zeroes = (
        '{"intercept":0.0,"decay":0.0,"class_affinity":0.0,"entity_affinity":0.0,"threshold":0.0}'
    )
    assert cli.main(["promotion", "register", "--kind", "logistic", "--params", zeroes]) == 2
    assert "every feature weight is zero" in capsys.readouterr().err
