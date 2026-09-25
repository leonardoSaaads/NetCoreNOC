"""The graph's element panel: three scoped reads, and no existence oracle (v0.22.0, item 11).

Selecting a node reads `GET /api/elements/{ne_id}`, `GET /api/situations?ne_id=` and
`GET /api/activity/groups?ne_id=`. The first names one element and 404s on one the caller cannot
see exactly as on one that does not exist; the other two are collections, and an element outside
the scope narrows them to nothing — the filter is ANDed with the scope in SQL, so it can only
narrow (F35, F38).
"""

from __future__ import annotations

from netcorenoc.store import Store
from test_declaration import NARROW, _activate_scope

import authutil
import util

BASE = 1_700_000_000.0


async def _ne(store: Store, ip: str) -> int:
    async with store.lock:
        cur = await store.conn.execute("SELECT id FROM ne WHERE ip=?", (ip,))
        row = await cur.fetchone()
    assert row is not None, ip
    return int(row[0])


async def test_the_panel_reads_are_scoped_and_answer_like_a_missing_element(store: Store) -> None:
    engine, queue, app = await authutil.make_env(store)
    await util.drive(
        engine,
        queue,
        [
            util.event(device="10.0.0.1", trap_oid=util.CIENA_TRAP, ts=BASE + 1),
            util.event(device="192.168.50.1", trap_oid=util.HUAWEI_TRAP, ts=BASE + 2),
        ],
    )
    inside = await _ne(store, "10.0.0.1")
    outside = await _ne(store, "192.168.50.1")

    editor = await authutil.client_as(app, "editor")
    try:
        # The control, before any policy: both elements answer, and the filter narrows.
        for ne in (inside, outside):
            assert (await editor.get(f"/api/elements/{ne}")).status_code == 200
        mine = (await editor.get("/api/situations", params={"ne_id": inside})).json()
        assert mine, "the element's situation was not found by its own filter"
        everyone = (await editor.get("/api/situations")).json()
        assert len(everyone) > len(mine), "the ne_id filter did not narrow"
    finally:
        await editor.aclose()

    await _activate_scope(store, NARROW)
    editor = await authutil.client_as(app, "editor")
    try:
        hidden = await editor.get(f"/api/elements/{outside}")
        missing = await editor.get("/api/elements/999999")
        assert hidden.status_code == missing.status_code == 404
        assert hidden.json() == missing.json(), "the 404 body tells a hidden element apart"
        assert (await editor.get(f"/api/elements/{inside}")).status_code == 200
        assert (await editor.get("/api/situations", params={"ne_id": outside})).json() == []
        groups = await editor.get(
            "/api/activity/groups", params={"ne_id": outside, "range_s": 10**9}
        )
        assert groups.json()["total"] == 0 and groups.json()["repeated"] == 0
    finally:
        await editor.aclose()
