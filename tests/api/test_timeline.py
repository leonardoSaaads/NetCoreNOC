"""`GET /api/timeline` — the two filters v0.16.1 adds, and the rule they had to obey.

*"Any filter you add is a query filter, never a render filter."* That rule is not stylistic: v0.7.0
truncated the timeline globally and then filtered the result in Python, comparing the **rendered**
`COALESCE(label, ip)` string against a scope's address set. Two findings came out of it — F35 (a
non-unique display string used as an authorization key) and F38 (a scoped principal's own marks
becoming a function of traffic they cannot see) — and DECISIONS #67 and #72 are the answers.

So the element filter is an `ne_id`, the **same key the scope predicate uses**, and the window is a
pair of timestamps in the `WHERE` clause. Both are `AND`ed with the scope, so neither can widen
anything; and both bind before `LIMIT`, so the limit bounds the filtered set.

**The decisive test in this file is `…_bounds_the_filtered_set_and_not_a_truncated_page`.** It is
the one a render filter fails: with a busy neighbour and `limit=1`, a filter applied after
truncation returns nothing at all, which reads on screen as *"this element is quiet"* — the worst
possible wrong answer for a monitoring console.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from netcorenoc.store import Store

import authutil

BASE = 1_700_000_000.0
SCOPE_POLICY = json.dumps({"version": 1, "roles": {"editor": ["10.1.0.0/16"]}})


async def _install_scope(store: Store) -> None:
    async with store.lock:
        pid = await store.insert_governance_policy(
            "scope",
            SCOPE_POLICY,
            hashlib.sha256(SCOPE_POLICY.encode()).hexdigest(),
            "adm",
            BASE,
            "",
        )
        await store.set_active_governance_policy("scope", pid, "adm", BASE)
        await store.commit()


async def _element(store: Store, ip: str) -> tuple[int, int, int]:
    """One device, its NE and one alarm class — the three ids an alarm needs."""
    cur = await store.conn.execute(
        "INSERT INTO device (ip, first_seen, last_seen) VALUES (?, ?, ?) RETURNING id",
        (ip, BASE, BASE),
    )
    device = int((await cur.fetchone())[0])  # type: ignore[index]
    cur = await store.conn.execute(
        "INSERT INTO ne (ip, first_seen, last_seen) VALUES (?, ?, ?) RETURNING id", (ip, BASE, BASE)
    )
    ne = int((await cur.fetchone())[0])  # type: ignore[index]
    cur = await store.conn.execute(
        # v0.16.3: `alarm_class` no longer stores `name` — it held `trap_name(oid)` for 48 of 48
        # classes on a real corpus, so `0016` dropped it and readers derive it (DECISIONS #280).
        # The OID is what a class IS, and it is what this fixture varies per element.
        "INSERT INTO alarm_class (oid, first_seen, last_seen) VALUES (?, ?, ?) RETURNING id",
        (f"1.3.6.1.4.1.99.{ne}", BASE, BASE),
    )
    return device, ne, int((await cur.fetchone())[0])  # type: ignore[index]


async def _alarm(
    store: Store,
    ids: tuple[int, int, int],
    raised: float,
    cleared: float | None,
    instance: str = "",
) -> None:
    """`alarm` is `UNIQUE (device_id, class_id, instance)`, so a busy element needs distinct
    instances — which is what a real one has: twenty ports, not twenty copies of one."""
    device, ne, cls = ids
    await store.conn.execute(
        "INSERT INTO alarm (device_id, class_id, ne_id, instance, severity, status, first_seen, "
        "last_seen, cleared_at) VALUES (?, ?, ?, ?, 3, 'active', ?, ?, ?)",
        (device, cls, ne, instance, raised, raised, cleared),
    )


async def _marks(app: object, role: str, query: str = "") -> list[dict[str, Any]]:
    client = await authutil.client_as(app, role)
    try:
        response = await client.get(f"/api/timeline?{query}")
        assert response.status_code == 200, response.text
        return list(response.json()["marks"])
    finally:
        await client.aclose()


async def _seed(store: Store) -> dict[str, int]:
    """A loud element and a quiet one, in scope; and a third the scoped editor may not see."""
    async with store.lock:
        loud = await _element(store, "10.1.0.1")
        quiet = await _element(store, "10.1.0.2")
        outside = await _element(store, "10.9.0.1")
        for offset in range(20):
            await _alarm(store, loud, BASE + 1000 + offset, None, f"Gi0/{offset}")
        await _alarm(store, quiet, BASE + 10, BASE + 20)
        # Older than the loud element's, so the newest row globally is one the
        # scoped editor may see — which is what makes the control below about
        # ORDERING rather than about the scope filter.
        await _alarm(store, outside, BASE + 500, None)
        await store.commit()
    return {"loud": loud[1], "quiet": quiet[1], "outside": outside[1]}


# --- the element filter -------------------------------------------------------------------------


async def test_the_element_filter_narrows_and_the_mark_carries_the_key_it_narrows_on(
    store: Store,
) -> None:
    """`ne_id` in, `ne_id` out. The console cannot filter by a key the marks do not carry.

    The one intentional shape change in this response: a mark now carries `ne_id`. It discloses
    nothing new — `/api/entities` has served NE ids to viewers since v0.5.0 — and it is what lets
    the element control send an identifier rather than the `device` string, which is the whole of
    why this filter is not the v0.7.0 defect asked for on purpose.
    """
    _engine, _queue, app = await authutil.make_env(store)
    ne = await _seed(store)

    everything = await _marks(app, "admin", "limit=1000")
    assert all("ne_id" in mark for mark in everything), "a mark carries the key it is filtered on"
    assert {mark["ne_id"] for mark in everything} == set(ne.values())

    only_quiet = await _marks(app, "admin", f"limit=1000&ne_id={ne['quiet']}")
    assert {mark["ne_id"] for mark in only_quiet} == {ne["quiet"]}
    assert len(only_quiet) == 2, "one alarm, raised and cleared, is two marks"


async def test_the_element_filter_bounds_the_filtered_set_and_not_a_truncated_page(
    store: Store,
) -> None:
    """**The test a render filter fails**, and the reason the rule exists (F38).

    Twenty alarms on the loud element are newer than the quiet element's one. Asked for `limit=1`
    and the quiet element:

      * a **query** filter narrows first, so the limit applies to the quiet element's own marks;
      * a **render** filter would take the newest row globally — the loud element's — and then
        drop it, returning an empty timeline that reads as *"this element is quiet"*.

    The control is the same limit with no element filter, which must come back with the loud
    element's mark: without it, an empty answer could mean the fixture produced nothing.
    """
    _engine, _queue, app = await authutil.make_env(store)
    ne = await _seed(store)

    narrowed = await _marks(app, "admin", f"limit=1&ne_id={ne['quiet']}")
    assert narrowed, "the element filter was applied after the limit, so the answer was empty"
    assert {mark["ne_id"] for mark in narrowed} == {ne["quiet"]}

    unfiltered = await _marks(app, "admin", "limit=1")
    assert {mark["ne_id"] for mark in unfiltered} == {ne["loud"]}, (
        "CONTROL: without the filter the newest row is the loud element's, which is what makes "
        "the assertion above a statement about ordering rather than about the fixture"
    )


async def test_a_scoped_editor_naming_an_element_outside_their_scope_gets_nothing(
    store: Store,
) -> None:
    """The narrowing filter is `AND`ed with the scope and can never widen it.

    The refusal is a **non-answer**, not an error: an element outside the scope returns exactly
    what an element that does not exist returns, which is the same one-code-path property
    DECISIONS #60 gives the situation routes. The control is `admin`, who sees it.
    """
    _engine, _queue, app = await authutil.make_env(store)
    ne = await _seed(store)
    await _install_scope(store)

    assert await _marks(app, "editor", f"limit=1000&ne_id={ne['outside']}") == []
    assert await _marks(app, "editor", "limit=1000&ne_id=999999") == [], (
        "an element that does not exist and one outside the scope answer identically"
    )
    assert {
        m["ne_id"] for m in await _marks(app, "admin", f"limit=1000&ne_id={ne['outside']}")
    } == {ne["outside"]}, (
        "CONTROL: an unrestricted principal sees it, so the refusal is the scope filter"
    )
    assert {m["ne_id"] for m in await _marks(app, "editor", "limit=1000")} == {
        ne["loud"],
        ne["quiet"],
    }, "CONTROL: the scoped editor's own elements are unaffected"


# --- the window filter --------------------------------------------------------------------------


async def test_the_window_bounds_the_marks_and_not_only_the_rows(store: Store) -> None:
    """A cleared alarm is two marks at two times, so a row filter alone is not a window filter.

    The quiet element raised at `BASE + 10` and cleared at `BASE + 20`. A window that starts at
    `BASE + 15` contains the clear and not the raise — and the SQL predicate keeps the row,
    because one of its marks qualifies. Emitting both would put a mark outside the window the
    operator asked for on their screen.
    """
    _engine, _queue, app = await authutil.make_env(store)
    ne = await _seed(store)

    window = await _marks(app, "admin", f"limit=1000&ne_id={ne['quiet']}&since={BASE + 15}")
    assert [mark["kind"] for mark in window] == ["clear"]
    assert window[0]["ts"] == BASE + 20

    both = await _marks(app, "admin", f"limit=1000&ne_id={ne['quiet']}&since={BASE}")
    assert sorted(mark["kind"] for mark in both) == ["clear", "raise"], "CONTROL: the wider window"


async def test_the_window_and_the_element_compose(store: Store) -> None:
    """Two filters that each narrow must narrow together, or one silently replaces the other."""
    _engine, _queue, app = await authutil.make_env(store)
    ne = await _seed(store)

    marks = await _marks(
        app, "admin", f"limit=1000&ne_id={ne['loud']}&since={BASE + 1010}&until={BASE + 1012}"
    )
    assert {mark["ne_id"] for mark in marks} == {ne["loud"]}
    assert [mark["ts"] for mark in marks] == [BASE + 1010, BASE + 1011, BASE + 1012]


async def test_an_unfiltered_request_is_what_it_has_always_been(store: Store) -> None:
    """The control for the whole file: with no filter, the response is the v0.7.0 shape plus
    `ne_id`, and nothing narrows. A release that broke the default while adding options would
    have broken the screen for every operator who never touches a filter."""
    _engine, _queue, app = await authutil.make_env(store)
    ne = await _seed(store)
    marks = await _marks(app, "admin", "limit=1000")
    assert len(marks) == 23, "20 raises, the quiet element's raise and clear, and one more"
    assert {mark["ne_id"] for mark in marks} == set(ne.values())
    assert marks == sorted(marks, key=lambda m: m["ts"]), "still oldest-first for the drawing"
    assert set(marks[0]) == {"ts", "ne_id", "device", "class", "kind"}
