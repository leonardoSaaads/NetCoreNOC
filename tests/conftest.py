from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import pytest
from hypothesis import settings

from netcorenoc.crosscutting import auth, vault
from netcorenoc.store import Store

import paths
import pgtemp

FIXTURES = paths.FIXTURES

# ADR #453: the suite runs with one fixed secret key, as an appliance runs with its key file. A
# test that needs no key, or another one, configures its own and restores this one.
TEST_KEY = b"netcorenoc test secret key 32 b!"
TEST_VAULT = vault.Vault.from_key(TEST_KEY)
vault.configure(TEST_VAULT)

# --- the Hypothesis profile (v0.16.2, DECISIONS #279) --------------------------------------
#
# **No profile was registered anywhere** — not here, not in `pyproject.toml` — for the whole of
# this project's life, and the consequence was a coverage figure nobody could reproduce. The
# v0.16.1 handoff reports 95.90 / 95.94 / 95.96 on one tree and concludes coverage "is not
# reproducible"; an independent reviewer ran the same tree three times and got 95.88 every time.
# Both are right, and the difference between them is this file.
#
# Hypothesis defaults to a **random** seed and a **persistent example database** (`.hypothesis/`).
# A property test therefore explores different inputs on different runs, and replays previously
# shrunk failures on a machine that has one cached — so different branches execute, and the
# coverage percentage moves. It is not the suite that is unstable; it is the search.
#
#   derandomize=True   the seed is derived from the test, so two runs draw the same examples
#   database=None      no replay of a past failure, so a populated `.hypothesis/` cannot change
#                      which branches a run reaches
#
# **What this costs, stated plainly**: a derandomised search does not accumulate coverage of the
# input space across runs the way a random one does. That is a real loss and it is accepted for a
# reason — a property that only fails on one machine's cached example is a property nobody can act
# on, and a reproducible figure is what lets a release compare itself to the last one at all.
settings.register_profile("netcorenoc", derandomize=True, database=None)
settings.load_profile("netcorenoc")


@pytest.fixture(autouse=True)
def _secret_key_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR #453: the suite's key, in a file of the test's own — never `secret.key` in the working
    tree, which is where an appliance with the default `NETCORENOC_DATA_DIR` would create one.
    Settings read from the environment find it there; `vault.install` is given the same key
    directly, for the tests that build `Settings` with the default data directory."""
    import base64

    def install(_path: Path) -> vault.Vault:
        vault.configure(TEST_VAULT)
        return TEST_VAULT

    path = tmp_path / "secret.key"
    path.write_text(base64.b64encode(TEST_KEY).decode("ascii") + "\n")
    path.chmod(0o600)
    monkeypatch.setenv("NETCORENOC_SECRET_KEY_FILE", str(path))
    monkeypatch.setattr(vault, "install", install)
    vault.configure(TEST_VAULT)


@pytest.fixture(autouse=True)
def _fast_scrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lower the scrypt work factor for test speed. Production uses auth.PRODUCTION_SCRYPT_N
    (2**17), asserted by test_auth.test_production_scrypt_parameters; verification always
    reads the parameters embedded in each stored hash, so this only affects test cost."""
    monkeypatch.setattr(auth, "SCRYPT_N", 2**14)


@pytest.fixture(autouse=True)
def _no_packaged_league(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test runs on the fail-safe formula unless it asks for a model (v0.27.0).

    The packaged league is data a release retrains; a test of the engine's mechanics must not pass
    or fail with which model won this release's training, nor time five models at every engine
    start. So the league is presented empty here — the formula decides, exactly as on an appliance
    whose league could not be read — and a test that needs a model asks for one: `test_model` (one
    small GAM), `packaged_league` (what this build ships), or its own league.
    """
    from netcorenoc.engine.model import league

    monkeypatch.setattr(league, "load", lambda: league.League(()))


@pytest.fixture
def packaged_league(monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    """The league this build packages, as an appliance loads it (`league.load_dir` on the
    package's own directory) — undoing `_no_packaged_league` for the tests that are about it."""
    from importlib import resources

    from netcorenoc.engine.model import league

    packaged = league.load_dir(
        resources.files("netcorenoc.engine.model").joinpath(league.DIRECTORY)
    )
    monkeypatch.setattr(league, "load", lambda: packaged)
    yield packaged


@pytest.fixture
def new_database() -> Iterator[Callable[[], str]]:
    """Make fresh, migrated, empty PostgreSQL databases; each is dropped after the test.

    Where a test once named a second file in ``tmp_path``, it calls this again (ADR #452). The
    server is `tools/pgtemp.py`'s: ``NETCORENOC_TEST_DATABASE_URL`` or a private local cluster.
    """
    made: list[str] = []

    def make() -> str:
        made.append(pgtemp.create())
        return made[-1]

    yield make
    for url in made:
        pgtemp.drop(url)


@pytest.fixture
def db_url(new_database: Callable[[], str]) -> str:
    """One fresh database for the test: what ``tmp_path / "test.db"`` was."""
    return new_database()


@pytest.fixture
async def store(db_url: str) -> AsyncIterator[Store]:
    s = Store(db_url)
    await s.open()
    yield s
    await s.close()


@pytest.fixture
def test_model(monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    """`modelutil.TEST_MODEL` as the whole league — one GAM, so it is the champion — for the
    decider's mechanics (v0.26.0; the league since v0.27.0). Also the GAM a site search adapts."""
    from netcorenoc.engine.model import league, shipped

    from modelutil import TEST_MODEL, manifest

    model = shipped.load_from(TEST_MODEL, manifest(TEST_MODEL))
    member = league.member_from("gam", TEST_MODEL, manifest(TEST_MODEL))
    monkeypatch.setattr(shipped, "load", lambda: model)
    monkeypatch.setattr(league, "load", lambda: league.League((member,)))
    yield model
