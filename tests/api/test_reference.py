"""The API reference (v0.30.0): every route documented in the schema, authorization included."""

from __future__ import annotations

from netcorenoc.api.reference import REFERENCE
from netcorenoc.crosscutting import rbac
from netcorenoc.store import Store

import authutil


def test_every_route_has_one_entry_and_every_entry_a_route() -> None:
    declared = set(rbac.ROUTE_PERMISSIONS) | set(rbac.PUBLIC_ROUTES)
    assert sorted(declared - set(REFERENCE)) == [], "a route with no reference entry"
    assert sorted(set(REFERENCE) - declared) == [], "a reference entry with no route"
    for key, entry in REFERENCE.items():
        assert entry.summary and len(entry.summary) <= 70, key


async def test_the_schema_carries_what_the_perimeter_enforces(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    anon = authutil.new_client(app)
    try:
        schema = (await anon.get("/openapi.json")).json()
    finally:
        await anon.aclose()
    seen = 0
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            key = (method.upper(), path)
            if key not in REFERENCE:
                continue
            seen += 1
            assert operation["summary"] == REFERENCE[key].summary
            assert operation["tags"] == [REFERENCE[key].group]
            assert operation.get("x-capability") == rbac.ROUTE_PERMISSIONS.get(key)
            assert operation["x-scope"] == rbac.ROUTE_SCOPE.get(key, "public")
    assert seen == len(REFERENCE)
