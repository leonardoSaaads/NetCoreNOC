"""People: display names and profile photos (v0.25.0, ADR #401, #402).

* `GET /api/avatars/{uid}` — a photo. Your own always; another person's only for a role that may
  see who other people are (`shaping.sees_people`, the rule the gesture history's names follow).
  Served as the type the bytes were checked to be, `nosniff`, under a closed CSP, with the digest
  as its ETag — the console asks for `?v=<digest>`, so each photo is fetched once per browser.
* `POST|DELETE /api/me/avatar`, `POST /api/me/profile` — your own photo and name.
* `POST|DELETE /api/users/{uid}/avatar`, `POST /api/users/{uid}/profile` — an admin's, for anyone.

A photo is sent as the request body (`image/png`, `image/webp` or `image/jpeg`), read with a hard
cap so an oversized body is refused before it is held, and checked by `crosscutting/avatar.py`
from its own bytes. Every change is audited; the audit row carries the digest, never the image.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models import ProfileIn
from netcorenoc.crosscutting import auth, avatar, shaping

#: The headers every photo is served with. `sandbox` and `default-src 'none'` mean that even a file
#: crafted to parse as something else too cannot run or load anything if opened directly.
IMAGE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Content-Disposition": 'inline; filename="avatar"',
    # The URL carries the digest, so a cached copy can never be a stale one.
    "Cache-Control": "private, max-age=31536000, immutable",
}


async def _read_capped(request: Request) -> bytes:
    """The request body, refused as soon as it passes the cap — never read whole first."""
    out = bytearray()
    async for chunk in request.stream():
        out.extend(chunk)
        if len(out) > avatar.MAX_BYTES:
            raise HTTPException(
                status_code=413, detail=f"the image is larger than {avatar.MAX_BYTES // 1024} KiB"
            )
    return bytes(out)


def _checked(data: bytes) -> avatar.Avatar:
    try:
        return avatar.validate(data)
    except avatar.AvatarError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the people routes on `app`."""
    store, security = ctx.store, ctx.security
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)

    def _self(principal: auth.Principal) -> int:
        if principal.user_id is None:
            raise HTTPException(status_code=403, detail="service tokens have no profile")
        return int(principal.user_id)

    # The three writes, each run INSIDE the handler's `write_txn()` — the helpers never open one,
    # so every mutating handler below visibly takes the transaction itself.
    async def _put_avatar(
        uid: int, photo: avatar.Avatar, request: Request, principal: auth.Principal, action: str
    ) -> dict[str, Any]:
        if await store.get_user(uid) is None:
            raise HTTPException(status_code=404, detail="no such user")
        await store.put_avatar(uid, photo, time.time())
        await audit_row(
            request,
            principal,
            action,
            "ok",
            object_type="user",
            object_id=str(uid),
            details={
                "field": "avatar",
                "sha256": photo.sha256,
                "bytes": len(photo.image),
                "mime": photo.mime,
            },
        )
        return {"avatar": photo.sha256}

    async def _drop_avatar(
        uid: int, request: Request, principal: auth.Principal, action: str
    ) -> dict[str, Any]:
        if await store.get_user(uid) is None:
            raise HTTPException(status_code=404, detail="no such user")
        if await store.delete_avatar(uid):
            await audit_row(
                request,
                principal,
                action,
                "ok",
                object_type="user",
                object_id=str(uid),
                details={"field": "avatar", "removed": True},
            )
        return {"avatar": None}

    async def _set_name(
        uid: int, body: ProfileIn, request: Request, principal: auth.Principal, action: str
    ) -> dict[str, Any]:
        name = (body.display_name or "").strip() or None
        if await store.get_user(uid) is None:
            raise HTTPException(status_code=404, detail="no such user")
        await store.set_display_name(uid, name, time.time())
        await audit_row(
            request,
            principal,
            action,
            "ok",
            object_type="user",
            object_id=str(uid),
            details={"field": "display_name", "display_name": name},
        )
        return {"display_name": name}

    @route.get("/api/avatars/{uid}")
    async def get_avatar(
        uid: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> Response:
        """One photo. Someone else's only for a role that may see who other people are; a
        refusal and an absent photo are the same 404, so this is not a way to list accounts."""
        own = principal.user_id is not None and int(principal.user_id) == uid
        if not own and not shaping.sees_people(principal.role):
            raise HTTPException(status_code=404, detail="no photo")
        async with store.lock:
            row = await store.get_avatar(uid)
        if row is None:
            raise HTTPException(status_code=404, detail="no photo")
        etag = f'"{row["sha256"]}"'
        headers = {**IMAGE_HEADERS, "ETag": etag}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return Response(content=bytes(row["image"]), media_type=row["mime"], headers=headers)

    @route.post("/api/me/avatar")
    async def set_my_avatar(
        request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        uid, photo = _self(principal), _checked(await _read_capped(request))
        async with write_txn():
            return await _put_avatar(uid, photo, request, principal, "profile.update")

    @route.delete("/api/me/avatar")
    async def drop_my_avatar(
        request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        async with write_txn():
            return await _drop_avatar(_self(principal), request, principal, "profile.update")

    @route.post("/api/me/profile")
    async def set_my_profile(
        body: ProfileIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        async with write_txn():
            return await _set_name(_self(principal), body, request, principal, "profile.update")

    @route.post("/api/users/{uid}/avatar")
    async def set_user_avatar(
        uid: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        photo = _checked(await _read_capped(request))
        async with write_txn():
            return await _put_avatar(uid, photo, request, principal, "user.update")

    @route.delete("/api/users/{uid}/avatar")
    async def drop_user_avatar(
        uid: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        async with write_txn():
            return await _drop_avatar(uid, request, principal, "user.update")

    @route.post("/api/users/{uid}/profile")
    async def set_user_profile(
        uid: int, body: ProfileIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        async with write_txn():
            return await _set_name(uid, body, request, principal, "user.update")
