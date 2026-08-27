"""`.well-known/security.txt` — RFC 9116 validity and the served static route (v0.5.0).

The file is committed in the package and served by the app from a fixed static route under the
existing CSP/security-headers middleware — public, unauthenticated, additive to the static
allowlist (not a new dynamic surface). These tests assert it parses per RFC 9116, that its
``Expires`` is in the future, and that the served route carries the security headers and needs
no authentication.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest

from netcorenoc.api import CSP, UI_DIR, create_app
from netcorenoc.ingest.receiver import QueueItem
from netcorenoc.main import Engine
from netcorenoc.store import Store

SECURITY_TXT = UI_DIR / ".well-known" / "security.txt"


def _parse(text: str) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        assert ":" in line, f"non-field, non-comment line: {line!r}"
        name, _, value = line.partition(":")
        fields.setdefault(name.strip(), []).append(value.strip())
    return fields


def test_security_txt_is_shipped_in_the_package() -> None:
    assert SECURITY_TXT.is_file(), "security.txt must ship inside the package (ui/.well-known/)"


def test_security_txt_parses_and_has_required_fields() -> None:
    fields = _parse(SECURITY_TXT.read_text(encoding="utf-8"))
    # RFC 9116: Contact and Expires are mandatory.
    assert fields.get("Contact"), "RFC 9116 requires at least one Contact field"
    assert all(c.startswith(("https://", "mailto:", "tel:")) for c in fields["Contact"])
    assert len(fields.get("Expires", [])) == 1, "exactly one Expires field is required"
    assert fields.get("Policy"), "Policy should point at the disclosure policy"
    assert fields.get("Preferred-Languages"), "Preferred-Languages declared"


def test_security_txt_expires_is_in_the_future() -> None:
    fields = _parse(SECURITY_TXT.read_text(encoding="utf-8"))
    raw = fields["Expires"][0]
    expires = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    assert expires > datetime.now(UTC), (
        f"security.txt Expires={raw} is not in the future — refresh it"
    )


@pytest.fixture
async def app_client(store: Store) -> AsyncIterator[httpx.AsyncClient]:
    queue: asyncio.Queue[QueueItem] = asyncio.Queue()
    engine = Engine(store, queue)
    await engine.start()
    app = create_app(engine)
    transport = httpx.ASGITransport(app=app)
    # No Authorization header — the served route must be public.
    async with httpx.AsyncClient(transport=transport, base_url="http://netcorenoc.test") as c:
        yield c


async def test_security_txt_is_served_publicly_with_security_headers(
    app_client: httpx.AsyncClient,
) -> None:
    resp = await app_client.get("/.well-known/security.txt")
    assert resp.status_code == 200, "served route must be public (no auth) and reachable"
    assert "text/plain" in resp.headers["content-type"]
    # Under the existing security-headers middleware (same CSP as every route class).
    assert resp.headers["content-security-policy"] == CSP
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"
    # It is a static file, not a dynamic surface: the served bytes equal the shipped file.
    assert resp.text == SECURITY_TXT.read_text(encoding="utf-8")


async def test_well_known_route_needs_no_authentication(app_client: httpx.AsyncClient) -> None:
    # Contrast: an /api route with no credential is 401; the well-known file is 200.
    assert (await app_client.get("/api/stats")).status_code == 401
    assert (await app_client.get("/.well-known/security.txt")).status_code == 200
