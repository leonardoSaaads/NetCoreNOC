"""The console's stylesheets, read as the browser applies them (ADR #504).

`ui/style.css` was one file of five thousand lines; it is now `ui/styles/<area>/<topic>.css`, cut at
its own section marks and linked by `index.html` in one order — the cascade. A guard that reads
"the stylesheet" reads them all, in that order, so a rule is found wherever it now lives.
"""

from __future__ import annotations

import re
from pathlib import Path

import netcorenoc

UI = Path(netcorenoc.__file__).resolve().parent / "ui"


def sheets() -> list[Path]:
    """Every stylesheet `index.html` links, in its order."""
    index = (UI / "index.html").read_text(encoding="utf-8")
    found = re.findall(r'<link rel="stylesheet" href="/([^"]+)">', index)
    assert found, "index.html links no stylesheet"
    return [UI / name for name in found]


def stylesheet() -> str:
    """The console's CSS as one text, in cascade order."""
    return "".join(path.read_text(encoding="utf-8") for path in sheets())
