from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from netcorenoc import auth
from netcorenoc.store import Store

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

FIXTURES = Path(__file__).parent / "fixtures"


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
