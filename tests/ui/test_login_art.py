"""The sign-in artwork is the tool's output, byte for byte (v0.30.0, ADR #446).

A shipped image whose source nobody can rerun is an image nobody can change safely. This keeps the
SVG and `tools/login_art.py` in step, and keeps the picture inside what the console may load.
"""

from __future__ import annotations

import login_art


def test_the_shipped_artwork_is_what_the_tool_draws() -> None:
    assert login_art.OUT.read_text(encoding="utf-8") == login_art.draw(), (
        "src/netcorenoc/ui/login-bg.svg differs from tools/login_art.py's output; run "
        "`python tools/login_art.py` and commit the result"
    )


def test_the_artwork_is_self_contained_and_small() -> None:
    svg = login_art.draw()
    assert "<script" not in svg and "href=" not in svg and "url(http" not in svg
    assert len(svg.encode()) < 40_000
