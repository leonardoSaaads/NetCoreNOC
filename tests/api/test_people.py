"""People: display names, profile photos and per-person access (v0.25.0, ADR #401-#404).

What is asserted, the refusals first:

* a photo is checked from its own bytes — SVG, HTML, GIF, a PNG with a lying header, an oversized
  body and a 1000 px image are all refused, whatever `Content-Type` the client sent;
* a photo is served as the type it was checked to be, `nosniff`, under a closed CSP, with an ETag
  and a 304 — and a viewer cannot fetch another person's photo (the rule the history's names obey);
* a display name and a photo belong to their account: your own under `self.read`, anyone's under
  `users.manage`, audited both ways, deleted with the account;
* one subject's access — a role's baseline or one person's set — is a new version of the policy,
  and it can only narrow: a viewer given `users.manage` still cannot use it.
"""

from __future__ import annotations

import struct
import zlib
from typing import Any

import pytest

from netcorenoc.crosscutting import avatar
from netcorenoc.store import Store

import authutil


def png(width: int, height: int) -> bytes:
    """A real, minimal PNG — built by hand, so the test needs no image library either."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(kind + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = b"".join(b"\x00" + b"\x7f\x20\x90" * width for _ in range(height))
    return (
        avatar.PNG_MAGIC
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )


def webp_lossless(width: int, height: int) -> bytes:
    bits = (width - 1) | ((height - 1) << 14)
    frame = b"\x2f" + bits.to_bytes(4, "little") + b"\x00" * 11
    body = b"VP8L" + struct.pack("<I", len(frame)) + frame
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


def jpeg(width: int, height: int) -> bytes:
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8\xff\xe0" + struct.pack(">H", 4) + b"JF" + sof + b"\xff\xd9"


# -- the validator -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "mime"),
    [
        (png(64, 64), "image/png"),
        (webp_lossless(192, 192), "image/webp"),
        (jpeg(320, 200), "image/jpeg"),
    ],
)
def test_the_three_formats_are_read_from_their_own_headers(data: bytes, mime: str) -> None:
    checked = avatar.validate(data)
    assert checked.mime == mime
    assert len(checked.sha256) == 64


@pytest.mark.parametrize(
    ("data", "why"),
    [
        (b"", "no image"),
        (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "only PNG"),
        (b"<html><script>alert(1)</script></html>", "only PNG"),
        (b"GIF89a" + b"\x00" * 40, "only PNG"),
        (avatar.PNG_MAGIC + b"\x00" * 40, "no header"),
        (png(1000, 1000), "each side"),
        (png(8, 8), "each side"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * (avatar.MAX_BYTES + 1), "larger than"),
        (b"RIFF\xff\xff\xff\x00WEBPVP8L" + b"\x00" * 20, "length"),
    ],
)
def test_anything_else_is_refused_with_the_rule_it_breaks(data: bytes, why: str) -> None:
    with pytest.raises(avatar.AvatarError, match=why):
        avatar.validate(data)


# -- the routes ----------------------------------------------------------------------------------


async def _upload(client: Any, path: str, data: bytes, mime: str = "image/png") -> Any:
    return await client.post(path, content=data, headers={"content-type": mime})


async def test_a_photo_round_trips_with_cache_headers_and_no_sniffing(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    editor = await authutil.client_as(app, "editor")
    try:
        me = (await editor.get("/api/me")).json()
        assert me["avatar"] is None
        uploaded = await _upload(editor, "/api/me/avatar", png(96, 96))
        assert uploaded.status_code == 200, uploaded.text
        digest = uploaded.json()["avatar"]
        me = (await editor.get("/api/me")).json()
        assert me["avatar"] == digest and me["user_id"] is not None
        photo = await editor.get(f"/api/avatars/{me['user_id']}?v={digest}")
        assert photo.status_code == 200 and photo.content == png(96, 96)
        assert photo.headers["content-type"] == "image/png"
        assert photo.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in photo.headers["content-security-policy"]
        assert "immutable" in photo.headers["cache-control"]
        again = await editor.get(
            f"/api/avatars/{me['user_id']}", headers={"if-none-match": photo.headers["etag"]}
        )
        assert again.status_code == 304 and again.content == b""
        # The declared type is ignored: an SVG sent as image/png is still an SVG.
        svg = await _upload(editor, "/api/me/avatar", b"<svg onload=alert(1)/>", "image/png")
        assert svg.status_code == 400
        too_big = await _upload(editor, "/api/me/avatar", b"\x00" * (avatar.MAX_BYTES + 10))
        assert too_big.status_code == 413
        assert (await editor.delete("/api/me/avatar")).status_code == 200
        gone = await editor.get(f"/api/avatars/{me['user_id']}")
        assert gone.status_code == 404
    finally:
        await editor.aclose()


async def test_a_viewer_sees_only_their_own_photo(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    editor = await authutil.client_as(app, "editor")
    viewer = await authutil.client_as(app, "viewer")
    try:
        editor_id = (await editor.get("/api/me")).json()["user_id"]
        viewer_id = (await viewer.get("/api/me")).json()["user_id"]
        await _upload(editor, "/api/me/avatar", png(64, 64))
        await _upload(viewer, "/api/me/avatar", png(64, 64))
        assert (await viewer.get(f"/api/avatars/{viewer_id}")).status_code == 200
        # Refused exactly as an absent photo is: not a way to enumerate accounts.
        refused = await viewer.get(f"/api/avatars/{editor_id}")
        absent = await viewer.get("/api/avatars/999999")
        assert refused.status_code == absent.status_code == 404
        assert refused.json() == absent.json()
        assert (await editor.get(f"/api/avatars/{viewer_id}")).status_code == 200
    finally:
        await editor.aclose()
        await viewer.aclose()


async def test_names_and_photos_are_yours_or_an_admins_and_are_audited(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    editor = await authutil.client_as(app, "editor")
    try:
        editor_id = (await editor.get("/api/me")).json()["user_id"]
        mine = await editor.post("/api/me/profile", json={"display_name": "  Ana Souza  "})
        assert mine.json()["display_name"] == "Ana Souza"
        assert (await editor.get("/api/me")).json()["display_name"] == "Ana Souza"
        # An editor may not change someone else's.
        other = await editor.post(f"/api/users/{editor_id}/profile", json={"display_name": "x"})
        assert other.status_code == 403
        assert (
            await _upload(admin, f"/api/users/{editor_id}/avatar", webp_lossless(128, 128))
        ).status_code == 200
        created = await admin.post(
            "/api/users",
            json={
                "username": "joao",
                "password": "correct-horse-9",
                "role": "viewer",
                "display_name": "João Lima",
            },
        )
        assert created.status_code == 200, created.text
        listing = {u["username"]: u for u in (await admin.get("/api/users")).json()}
        assert listing["joao"]["display_name"] == "João Lima"
        assert listing["edt"]["avatar"] is not None
        assert "image" not in listing["edt"], "the list must carry the digest, never the image"
        async with store.lock:
            cur = await store.conn.execute(
                "SELECT action, details, outcome FROM audit_log WHERE action IN "
                "('profile.update', 'user.update') ORDER BY id"
            )
            every = [(r[0], r[1], r[2]) for r in await cur.fetchall()]
        rows = [(a, d) for a, d, outcome in every if outcome == "ok"]
        assert [a for a, _ in rows] == ["profile.update", "user.update"], rows
        # The editor's refused attempt on someone else's profile is on the record too.
        assert ("user.update", "denied") in [(a, o) for a, _, o in every]
        assert "sha256" in rows[1][1] and '"image"' not in rows[1][1]
        # Deleting the account deletes the photo.
        assert (await admin.delete(f"/api/users/{editor_id}")).status_code == 200
        async with store.lock:
            cur = await store.conn.execute(
                "SELECT COUNT(*) FROM user_avatar WHERE user_id=?", (editor_id,)
            )
            row = await cur.fetchone()
        assert row is not None and row[0] == 0
    finally:
        await admin.aclose()
        await editor.aclose()


async def test_a_service_token_has_no_profile(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        made = await admin.post(
            "/api/tokens", json={"name": "grafana", "role": "viewer", "purpose": "dashboards"}
        )
        assert made.json()["purpose"] == "dashboards"
        listed = (await admin.get("/api/tokens")).json()
        assert listed[0]["purpose"] == "dashboards"
        bearer = authutil.new_client(app)
        try:
            refused = await bearer.post(
                "/api/me/profile",
                json={"display_name": "x"},
                headers={"Authorization": f"Bearer {made.json()['token']}"},
            )
            assert refused.status_code == 403
        finally:
            await bearer.aclose()
    finally:
        await admin.aclose()


# -- per-subject access --------------------------------------------------------------------------


async def test_one_persons_access_is_a_versioned_narrowing(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    editor = await authutil.client_as(app, "editor")
    viewer = await authutil.client_as(app, "viewer")
    try:
        editor_id = (await editor.get("/api/me")).json()["user_id"]
        viewer_id = (await viewer.get("/api/me")).json()["user_id"]
        before = set((await editor.get("/api/me")).json()["capabilities"])
        assert "alarm.clear" in before
        narrowed = sorted(before - {"alarm.clear"})
        wrote = await admin.post(
            "/api/rbac/subject", json={"subject": f"user:{editor_id}", "capabilities": narrowed}
        )
        assert wrote.status_code == 200, wrote.text
        after = set((await editor.get("/api/me")).json()["capabilities"])
        assert after == before - {"alarm.clear"}
        # A viewer "granted" users.manage still cannot manage users: the ceiling holds.
        await admin.post(
            "/api/rbac/subject",
            json={"subject": f"user:{viewer_id}", "capabilities": ["self.read", "users.manage"]},
        )
        assert "users.manage" not in (await viewer.get("/api/me")).json()["capabilities"]
        assert (await viewer.get("/api/users")).status_code == 403
        # A role baseline, then its removal: inherit again.
        await admin.post(
            "/api/rbac/subject",
            json={"subject": "role:viewer", "capabilities": ["self.read", "stats.read"]},
        )
        policy = (await admin.get("/api/rbac")).json()
        assert policy["subjects"]["roles"]["viewer"] == ["self.read", "stats.read"]
        assert f"user:{editor_id}" in policy["subjects"]["principals"]
        assert policy["minimum_role"]["users.manage"] == "admin"
        await admin.post("/api/rbac/subject", json={"subject": f"user:{editor_id}"})
        assert set((await editor.get("/api/me")).json()["capabilities"]) == before
        history = (await admin.get("/api/rbac")).json()["history"]
        assert len(history) == 4, "each change is its own version, and each can be rolled back"
        missing = await admin.post(
            "/api/rbac/subject", json={"subject": "user:999999", "capabilities": []}
        )
        assert missing.status_code == 404
        bad = await admin.post(
            "/api/rbac/subject", json={"subject": "user:1 OR 1=1", "capabilities": []}
        )
        assert bad.status_code == 422
        # An editor cannot write access at all.
        refused = await editor.post(
            "/api/rbac/subject", json={"subject": f"user:{editor_id}", "capabilities": []}
        )
        assert refused.status_code == 403
    finally:
        await admin.aclose()
        await editor.aclose()
        await viewer.aclose()
