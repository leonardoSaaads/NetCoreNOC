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


# --- v0.9.0: the champion-agreement CLI -----------------------------------------------------


def test_dataset_agreement_cli_emits_the_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`python -m netcorenoc dataset agreement` — v0.9.0's primary deliverable, on the CLI it
    ships on. A sibling of `dataset bias`, not a section of it (DECISIONS #115)."""
    db = str(tmp_path / "agreement.db")
    monkeypatch.setenv("NETCORENOC_DB", db)
    asyncio.run(_dataset_fixture(db))
    assert cli.main(["dataset", "agreement"]) == 0
    out = capsys.readouterr().out
    assert "NetCoreNOC champion-agreement report" in out
    assert "AGREEMENT IS NOT CORRECTNESS" in out
    assert "A UNIFORM BAG CONTAINED NO DECISION" in out
    assert "what this report CANNOT tell you" in out
    # It is its own report, not the bias one wearing a new heading.
    assert "feedback-dataset bias report" not in out


def test_dataset_agreement_cli_is_deterministic_across_invocations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two separate processes, byte-identical — including the bootstrap interval, which is the
    only part of the report with a generator in it."""
    db = str(tmp_path / "agreement-det.db")
    monkeypatch.setenv("NETCORENOC_DB", db)
    asyncio.run(_dataset_fixture(db))
    assert cli.main(["dataset", "agreement"]) == 0
    first = capsys.readouterr().out
    assert cli.main(["dataset", "agreement"]) == 0
    assert capsys.readouterr().out == first


def test_dataset_agreement_on_an_empty_database_reports_nothing_rather_than_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh appliance has no labels. `(none)` is the honest rendering; `0.0%` would be a
    claim about a population that does not exist."""
    db = str(tmp_path / "agreement-empty.db")
    monkeypatch.setenv("NETCORENOC_DB", db)

    async def _open() -> None:
        store = Store(db)
        await store.open()
        await store.close()

    asyncio.run(_open())
    assert cli.main(["dataset", "agreement"]) == 0
    out = capsys.readouterr().out
    assert "champion agreement" in out
    assert "(none)" in out
    assert "0.0%" not in out


# --- v0.9.0: the shadow-mode CLI ------------------------------------------------------------


def test_dataset_shadow_cli_leads_with_the_sufficiency_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`python -m netcorenoc dataset shadow`. On the bias fixture the corpus is far below every
    floor, so the report must say INSUFFICIENT, fit nothing, and say when."""
    db = str(tmp_path / "shadow.db")
    monkeypatch.setenv("NETCORENOC_DB", db)
    asyncio.run(_dataset_fixture(db))
    assert cli.main(["dataset", "shadow"]) == 0
    out = capsys.readouterr().out
    assert "NetCoreNOC shadow-mode report" in out
    assert "THE BUILT-IN SCORER DECIDES EVERYTHING" in out
    assert "INSUFFICIENT" in out
    assert "No model was fitted" in out
    assert out.index("SUFFICIENCY") < out.index("the corpus")


def test_dataset_shadow_cli_is_deterministic_across_invocations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two separate processes, byte-identical — the admission filter does not run on an
    insufficient corpus, so there is no measured duration to normalise here."""
    db = str(tmp_path / "shadow-det.db")
    monkeypatch.setenv("NETCORENOC_DB", db)
    asyncio.run(_dataset_fixture(db))
    assert cli.main(["dataset", "shadow"]) == 0
    first = capsys.readouterr().out
    assert cli.main(["dataset", "shadow"]) == 0
    assert capsys.readouterr().out == first


def test_dataset_shadow_on_an_empty_database_is_insufficient_not_broken(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh appliance has no labels. That is INSUFFICIENT with an `undefined` projection —
    never a crash, and never a number extrapolated from nothing."""
    db = str(tmp_path / "shadow-empty.db")
    monkeypatch.setenv("NETCORENOC_DB", db)

    async def _open() -> None:
        store = Store(db)
        await store.open()
        await store.close()

    asyncio.run(_open())
    assert cli.main(["dataset", "shadow"]) == 0
    out = capsys.readouterr().out
    assert "INSUFFICIENT" in out
    assert "undefined (no measurable labelling rate yet)" in out


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


# --- the promotion CLI (v0.11.0): the other half of "route plus CLI, no UI" ---------------


def test_promotion_list_on_an_appliance_that_has_been_asked_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The zero state is the one every operator sees first, so it is the one that must read well.

    **The seal's query count is printed first**, per `PREREGISTRATION-0.10.0.md` §4.3(4): *beside
    every holdout number this project ever publishes*. An empty promotion history is not silence —
    it says so.
    """
    db = str(tmp_path / "p.db")
    asyncio.run(_open_and_close(db))
    monkeypatch.setenv("NETCORENOC_DB", db)
    assert cli.main(["promotion", "list"]) == 0
    out = capsys.readouterr().out
    assert "sealed-holdout query count: 0" in out
    assert "ratified plan in force:" in out
    assert "(none — nothing has been proposed)" in out


def test_promotion_register_creates_an_artefact_and_says_it_is_not_a_promotion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Registering is not promoting**, and the CLI says so in the operator's own words rather
    than leaving it to be inferred from a table name."""
    db = str(tmp_path / "p.db")
    asyncio.run(_open_and_close(db))
    monkeypatch.setenv("NETCORENOC_DB", db)
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
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One validator, not two that could disagree: a payload the load path would refuse is refused
    at registration, with the same message and a non-zero exit."""
    db = str(tmp_path / "p.db")
    asyncio.run(_open_and_close(db))
    monkeypatch.setenv("NETCORENOC_DB", db)
    zeroes = (
        '{"intercept":0.0,"decay":0.0,"class_affinity":0.0,"entity_affinity":0.0,"threshold":0.0}'
    )
    assert cli.main(["promotion", "register", "--kind", "logistic", "--params", zeroes]) == 2
    assert "every feature weight is zero" in capsys.readouterr().err


async def _open_and_close(db: str) -> None:
    store = Store(db)
    await store.open()
    await store.close()
