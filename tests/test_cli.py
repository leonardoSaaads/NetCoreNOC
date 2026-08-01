"""CLI (`python -m netcorenoc audit ...`), runtime config, and JSON logging coverage."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from netcorenoc import __main__ as cli
from netcorenoc import audit
from netcorenoc.logsetup import JsonFormatter
from netcorenoc.runtime import RuntimeConfig
from netcorenoc.store import Store


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
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db = str(tmp_path / "a.db")
    monkeypatch.setenv("NETCORENOC_DB", db)
    asyncio.run(_seed(db, []))
    rc = cli.main(["audit", "verify"])
    assert rc == 0
    assert "audit chain OK" in capsys.readouterr().out


def test_audit_verify_cli_detects_tamper(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db = str(tmp_path / "b.db")
    monkeypatch.setenv("NETCORENOC_DB", db)
    asyncio.run(_seed(db, ["login.ok", "feedback", "logout"]))
    raw = sqlite3.connect(db)
    raw.execute("DROP TRIGGER audit_log_no_update")
    raw.execute("UPDATE audit_log SET actor='x' WHERE id=2")
    raw.commit()
    raw.close()
    rc = cli.main(["audit", "verify"])
    assert rc == 1
    assert "BROKEN at id 2" in capsys.readouterr().out


def test_audit_export_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db = str(tmp_path / "c.db")
    monkeypatch.setenv("NETCORENOC_DB", db)
    asyncio.run(_seed(db, ["login.ok", "logout"]))
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
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from test_bias import build_fixture

    store = Store(db)
    await store.open()
    await build_fixture(store)
    await store.close()


def test_dataset_bias_cli_emits_the_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`python -m netcorenoc dataset bias` — the release's deliverable, over the CLI it ships on."""
    db = str(tmp_path / "bias.db")
    monkeypatch.setenv("NETCORENOC_DB", db)
    asyncio.run(_dataset_fixture(db))
    assert cli.main(["dataset", "bias"]) == 0
    out = capsys.readouterr().out
    assert "NetCoreNOC feedback-dataset bias report" in out
    assert "EFFECTIVE SAMPLE SIZE" in out
    assert "*n* IS THE NUMBER OF INDEPENDENT BAGS, NOT THE NUMBER OF PAIRS." in out
    assert "what this report CANNOT tell you" in out


def test_dataset_bias_cli_is_deterministic_across_invocations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two separate CLI runs must be byte-identical.

    Stronger than an in-process repeat, which would still pass if the report read a clock once and
    cached it. This is what makes the report usable as a gate.
    """
    db = str(tmp_path / "det.db")
    monkeypatch.setenv("NETCORENOC_DB", db)
    asyncio.run(_dataset_fixture(db))
    assert cli.main(["dataset", "bias"]) == 0
    first = capsys.readouterr().out
    assert cli.main(["dataset", "bias"]) == 0
    assert capsys.readouterr().out == first


def test_dataset_stats_cli_reports_the_observed_window(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """§7.3: what capture costs, **and the window the operator actually has**.

    Not the configured `sink_days`, which the row cap almost always reaches first — an operator
    reading `21` and concluding they have three weeks to label something would be wrong, and
    nothing else would tell them.
    """
    db = str(tmp_path / "stats.db")
    monkeypatch.setenv("NETCORENOC_DB", db)
    asyncio.run(_dataset_fixture(db))
    assert cli.main(["dataset", "stats"]) == 0
    out = capsys.readouterr().out
    assert "dataset_pair.dataset" in out
    assert "capture_run" in out


def test_dataset_stats_on_an_empty_database_says_nothing_it_cannot_know(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh appliance has no sink, so there is no window to report — and it invents none."""
    db = str(tmp_path / "empty.db")
    monkeypatch.setenv("NETCORENOC_DB", db)

    async def _open() -> None:
        store = Store(db)
        await store.open()
        await store.close()

    asyncio.run(_open())
    assert cli.main(["dataset", "stats"]) == 0
    out = capsys.readouterr().out
    assert "sink_window_days                 None" in out
    assert "The sink currently spans" not in out
