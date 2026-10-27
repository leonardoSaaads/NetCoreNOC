"""NOC → Cases → Feed: the case's conversation and history (ADR #461).

Reading a feed is `cases.read` on a case in the caller's scope; each post is then shown only to
those its visibility admits (`engine/noc/feed.py`) — `internal` to the NOC, `specified` to its
author, the people it names and admins, `all` to everyone who may read the case.

Posting is `cases.post`, which a viewer holds: the customer's side of the conversation. Someone who
cannot read `internal` posts cannot write one either. **A person's post on a pending case sends
it back to the NOC queue** in the same transaction — the customer has answered, and nobody should
have to wait for the next update to read it.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc.api.case_view import actor_of, post_view, reader_of
from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models_cases import PostIn
from netcorenoc.crosscutting import auth, shaping
from netcorenoc.engine.noc import case_ops, feed

NO_SUCH = "no such case"
#: The newest posts read before filtering by visibility; a page is cut from what is visible.
SCAN = 500


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the case feed's routes on `app`."""
    store, security, scope_for = ctx.store, ctx.security, ctx.scope_for
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)

    async def visible_case(sid: int, scope: shaping.Scope) -> dict[str, Any]:
        async with store.reading():
            case = await store.noc_case(sid)
        if case is None or not await ctx.perimeter.situation_in_scope(sid, scope):
            raise HTTPException(status_code=404, detail=NO_SUCH)
        return case

    @route.get("/api/noc/cases/{sid}/feed", operation_id="noc_case_feed")
    async def case_feed(
        sid: int,
        request: Request,
        before: int | None = None,
        limit: int = 50,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """The posts you may read, newest first; ``before`` (a post id) pages back."""
        await visible_case(sid, await scope_for(principal))
        reader = reader_of(request, principal)
        async with store.reading():
            rows = await store.noc_posts(sid, limit=SCAN, before=before)
        shown = [r for r in rows if feed.visible(r, reader)]
        page = shown[: min(max(limit, 1), 200)]
        return {
            "posts": [post_view(r, reader) for r in page],
            "more": len(shown) > len(page) or len(rows) == SCAN,
            # Naming readers needs the people list, which is the NOC's (`/api/noc/people`).
            "can_post": sorted(feed.VISIBILITIES) if reader.works else ["all"],
        }

    @route.post("/api/noc/cases/{sid}/feed", operation_id="noc_post_to_case")
    async def post_to_case(
        sid: int, body: PostIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Post on the case feed. ``internal`` is the NOC's (`cases.work`); ``specified`` names
        its readers. A person's post on a pending case returns it to the NOC queue."""
        await visible_case(sid, await scope_for(principal))
        reader = reader_of(request, principal)
        if body.visibility == "internal" and not reader.works:
            raise HTTPException(status_code=403, detail="internal posts are the NOC's")
        if body.visibility == "specified" and not body.readers:
            raise HTTPException(status_code=400, detail="readers: name who a specified post is for")
        readers = body.readers if body.visibility == "specified" else []
        if readers:
            async with store.reading():
                known = [int(p["id"]) for p in await store.noc_people()]
            unknown = feed.unknown_readers(readers, known)
            if unknown:
                raise HTTPException(status_code=400, detail=f"readers: no such person {unknown[0]}")
        actor = actor_of(principal)
        now = time.time()
        async with write_txn():
            case = await store.noc_case(sid)
            assert case is not None
            post_id = await store.noc_post(
                sid,
                actor.author(),
                visibility=body.visibility,
                kind="post",
                body=body.body.strip() or body.body,
                readers=readers,
                now=now,
            )
            requeued = feed.requeues(case, author_kind=actor.kind, author_ref=actor.ref)
            if requeued:
                await case_ops.requeue(
                    store, sid, "customer_post", now, note=f"{actor.name} posted."
                )
            await audit_row(
                request,
                principal,
                "noc.case.post",
                "ok",
                object_type="noc_case",
                object_id=str(sid),
                details={
                    "post": post_id,
                    "visibility": body.visibility,
                    "readers": len(readers),
                    "requeued": requeued,
                },
            )
        return {"id": post_id, "requeued": requeued}

    @route.get("/api/noc/people", operation_id="noc_list_people")
    async def people(principal: auth.Principal = Depends(security)) -> dict[str, Any]:
        """The people a `specified` post may name: id and username."""
        async with store.reading():
            rows = await store.noc_people()
        return {"people": [{"id": r["id"], "username": r["username"]} for r in rows]}
