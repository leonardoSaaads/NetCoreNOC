"""`GET /api/reference`: the OpenAPI schema, for the API reference on Service tokens (v0.30.0).

The same document `/openapi.json` serves — every operation carrying its summary, group, capability
and scope posture (`api/reference.py`) — behind `tokens.manage`, because the console reads only
declared routes (`tests/ui/test_ui_invariants.py`), and the screen it feeds is the token screen.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the API reference route on `app`."""
    route = DeclaredRoutes(app)

    @route.get("/api/reference", dependencies=ctx.guarded)
    async def api_reference() -> dict[str, Any]:
        """The schema FastAPI builds once and caches; nothing here is per request."""
        return app.openapi()
