"""Where things are in the repository, stated once for the whole suite.

Tests live one folder below `tests/` (`tests/<area>/test_*.py`) and helpers in `tests/support/`,
so a test that counted `Path(__file__).parent` levels to find the repository would break the day it
moved. Every test asks this module instead.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["EVAL", "FIXTURES", "LAB", "PKG", "REPO_ROOT", "SRC", "SUPPORT", "TESTS", "TOOLS"]

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
PKG = SRC / "netcorenoc"
TESTS = REPO_ROOT / "tests"
SUPPORT = TESTS / "support"
FIXTURES = TESTS / "fixtures"
LAB = TESTS / "lab"
EVAL = REPO_ROOT / "eval"
TOOLS = REPO_ROOT / "tools"
