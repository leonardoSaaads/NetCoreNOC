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
    # Deliberately exercise the legacy alias: the CLI honours OPTICORR_DB for one version and
    # warns once on stderr (rebrand, DECISIONS #34).
    monkeypatch.delenv("NETCORENOC_DB", raising=False)
    monkeypatch.setenv("OPTICORR_DB", db)
    asyncio.run(_seed(db, ["login.ok", "logout"]))
    rc = cli.main(["audit", "export"])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.count("\n") == 2  # one NDJSON line per row
    assert "final_chain_hash" in captured.err
    assert "OPTICORR_DB is deprecated" in captured.err


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
