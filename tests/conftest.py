from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from hypothesis import settings

from netcorenoc.crosscutting import auth
from netcorenoc.store import Store

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

FIXTURES = Path(__file__).parent / "fixtures"

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
def _fast_scrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lower the scrypt work factor for test speed. Production uses auth.PRODUCTION_SCRYPT_N
    (2**17), asserted by test_auth.test_production_scrypt_parameters; verification always
    reads the parameters embedded in each stored hash, so this only affects test cost."""
    monkeypatch.setattr(auth, "SCRYPT_N", 2**14)


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    s = Store(str(tmp_path / "test.db"))
    await s.open()
    yield s
    await s.close()
