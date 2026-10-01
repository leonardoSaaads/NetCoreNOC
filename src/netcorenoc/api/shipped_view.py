"""Why a site search cannot start, in the words the console shows (v0.26.0; v0.27.0).

v0.26.0 kept the single shipped model's chart presentation here. v0.27.0 ships a league, and its
presentation is `api/league_view.py`; what stays is the one question that is still about the GAM
alone — the in-product search adapts that family to a site (ADR #413), so without one there is
nothing to adapt, and the request is refused with the reason before a run is opened.
"""

from __future__ import annotations

from netcorenoc.engine.model import shipped

__all__ = ["search_blocked"]


def search_blocked() -> str | None:
    """Why a site search cannot start on this build, or ``None``: a search adapts the league's GAM
    (ADR #413), so a build that carries none — or whose GAM was refused — has nothing to adapt."""
    try:
        shipped.load()
    except shipped.ShippedModelError as exc:
        return f"there is no GAM to adapt: {exc}"
    return None
