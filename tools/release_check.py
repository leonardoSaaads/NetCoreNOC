"""Pre-tag release check: the version must agree across the three places that declare it.

Verifies that ``[project].version`` in ``pyproject.toml``, ``__version__`` in
``src/netcorenoc/__init__.py``, and the top version heading in ``CHANGELOG.md`` are identical,
so a release can never be tagged with a mismatched version. Exits non-zero (naming the mismatch)
if they disagree.

    python tools/release_check.py
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def pyproject_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def package_version() -> str:
    text = (ROOT / "src" / "netcorenoc" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if match is None:
        raise SystemExit("could not find __version__ in src/netcorenoc/__init__.py")
    return match.group(1)


def changelog_version() -> str:
    """The first ``## [X.Y.Z]`` heading in CHANGELOG.md (an ``## [Unreleased]`` is skipped)."""
    for line in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^##\s*\[(\d+\.\d+\.\d+)\]", line.strip())
        if match:
            return match.group(1)
    raise SystemExit("could not find a '## [X.Y.Z]' heading in CHANGELOG.md")


def main() -> int:
    versions = {
        "pyproject.toml": pyproject_version(),
        "src/netcorenoc/__init__.py": package_version(),
        "CHANGELOG.md": changelog_version(),
    }
    unique = set(versions.values())
    if len(unique) == 1:
        print(f"release-check OK: all sources agree on version {unique.pop()}")
        return 0
    print("release-check FAILED: version mismatch", file=sys.stderr)
    for source, version in versions.items():
        print(f"  {source}: {version}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
