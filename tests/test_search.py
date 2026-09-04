"""`GET /api/situations?q=` — what an operator can find, and what they still cannot.

`docs/plans/v0.16.1-visualisation.md` §5: the console's search box filtered on
`` `#${s.id} ${s.status}` `` — id and status only — so an operator who had just named a situation
*"fibre cut, Ridgeway ring"* could not find it by that name.

**Half of this file is about what the search must NOT find**, and that half is the reason it is a
query filter. Two axes fail differently and both are covered:

* **Scope** — a scoped viewer legitimately receives a situation with one in-scope member and one
  they may not see, redacted to a count. If they could *find* it by typing the address of the
  member they cannot see, the redaction would be decorative: the search would answer a question
  the listing refuses to answer. That is F35 and F38 arriving through a text box.
* **Shaping** — a role below `editor` is shown `10.1.2.0/24` where an editor is shown
  `10.1.2.3`. Matching the raw column for such a role would confirm the fourth octet just as well
  as rendering it would. The gate is `shaping.sees_raw_addresses`, derived from `FIELD_RULES`, so
  the day `ip`'s minimum role moves the search moves with it.

Everything here goes over HTTP as a real principal. A test that called `store.list_situations`
directly would be testing the SQL and not the decision about who may run it.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from netcorenoc.store import Store

import authutil

BASE = 1_700_000_000.0

#: An editor confined to `10.1.0.0/16`. Devices in `10.9.…` exist, correlate, and are invisible.
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


async def _alarm(store: Store, sid: int, ip: str, oid: str, instance: str) -> int:
    """One alarm on its own device, NE and class, joined to `sid`."""
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
        "INSERT INTO alarm_class (oid, name, first_seen, last_seen) VALUES (?, ?, ?, ?) "
        "RETURNING id",
        (oid, "linkDown", BASE, BASE),
    )
    cls = int((await cur.fetchone())[0])  # type: ignore[index]
    cur = await store.conn.execute(
        "INSERT INTO alarm (device_id, class_id, ne_id, instance, severity, status, first_seen, "
        "last_seen) VALUES (?, ?, ?, ?, 3, 'active', ?, ?) RETURNING id",
        (device, cls, ne, instance, BASE, BASE),
    )
    alarm = int((await cur.fetchone())[0])  # type: ignore[index]
    await store.conn.execute(
        "INSERT INTO situation_alarm (situation_id, alarm_id) VALUES (?, ?)", (sid, alarm)
    )
    return alarm


async def _seed(store: Store) -> dict[str, int]:
    """Two situations an editor may see and one they may not, plus a mixed one.

    visible   10.1.0.1   named "fibre cut, Ridgeway ring"
    hidden    10.9.0.1   no name
    mixed     10.1.0.2 + 10.9.0.2   the redaction case
    """
    async with store.lock:
        visible = await store.create_situation(BASE, None)
        await _alarm(store, visible, "10.1.0.1", "1.3.6.1.4.1.9.1", "GigabitEthernet0/1")
        await store.conn.execute(
            "UPDATE situation SET operator_name=? WHERE id=?",
            ("fibre cut, Ridgeway ring", visible),
        )
        hidden = await store.create_situation(BASE + 1, None)
        await _alarm(store, hidden, "10.9.0.1", "1.3.6.1.4.1.2636.9", "xe-0/0/9")
        mixed = await store.create_situation(BASE + 2, None)
        await _alarm(store, mixed, "10.1.0.2", "1.3.6.1.4.1.9.2", "GigabitEthernet0/2")
        await _alarm(store, mixed, "10.9.0.2", "1.3.6.1.4.1.2636.7", "xe-0/0/7")
        await store.commit()
    return {"visible": visible, "hidden": hidden, "mixed": mixed}


async def _search(app: object, role: str, needle: str, **params: Any) -> list[dict[str, Any]]:
    client = await authutil.client_as(app, role)
    try:
        query = "".join(f"&{k}={v}" for k, v in params.items())
        response = await client.get(f"/api/situations?q={needle}{query}")
        assert response.status_code == 200, response.text
        return list(response.json())
    finally:
        await client.aclose()


def _ids(rows: list[dict[str, Any]]) -> set[int]:
    return {int(row["id"]) for row in rows}


# --- what an operator can now find ------------------------------------------------------------


async def test_the_search_finds_a_situation_by_the_name_an_operator_gave_it(store: Store) -> None:
    """**The headline case, and the one v0.16.0 was forbidden to fix.**

    `operator_name` is free text a person typed. `shape` passes it through for every role — a name
    is a label, and a label is not an address — so this works for a viewer as well as an editor,
    and the assertion covers both rather than assuming they behave alike.
    """
    _engine, _queue, app = await authutil.make_env(store)
    ids = await _seed(store)
    for role in ("editor", "viewer", "admin"):
        found = await _search(app, role, "ridgeway")
        assert _ids(found) == {ids["visible"]}, role
    assert _ids(await _search(app, "admin", "RIDGEWAY")) == {ids["visible"]}, "case-insensitive"


async def test_the_search_finds_a_situation_by_device_oid_and_instance(store: Store) -> None:
    """The three things §V.3 names beside the two names, each asserted on its own.

    Asserted separately rather than as one `any of` so a clause that silently stopped matching —
    a `JOIN` that became an `INNER JOIN` on a nullable column, say — fails as itself.
    """
    _engine, _queue, app = await authutil.make_env(store)
    ids = await _seed(store)
    assert _ids(await _search(app, "admin", "10.1.0.1")) == {ids["visible"]}, "by device address"
    assert _ids(await _search(app, "admin", "1.3.6.1.4.1.2636.9")) == {ids["hidden"]}, "by OID"
    assert _ids(await _search(app, "admin", "GigabitEthernet0/2")) == {ids["mixed"]}, "by instance"


async def test_the_search_narrows_with_the_status_tab_rather_than_replacing_it(
    store: Store,
) -> None:
    """`q` and `status` compose. The console's tab sits above the box and must still mean what
    it says, or an operator would search inside "New" and be shown a resolved situation."""
    _engine, _queue, app = await authutil.make_env(store)
    ids = await _seed(store)
    async with store.lock:
        await store.conn.execute(
            "UPDATE situation SET status='resolved' WHERE id=?", (ids["visible"],)
        )
        await store.commit()
    assert _ids(await _search(app, "admin", "ridgeway", status="resolved")) == {ids["visible"]}
    assert await _search(app, "admin", "ridgeway", status="new") == []


async def test_an_empty_or_blank_query_is_no_search_and_not_a_search_for_nothing(
    store: Store,
) -> None:
    """A whitespace box must return the list, not an empty result.

    The distinction is the same one `excluded_count` draws between NULL and 0: *"the operator asked
    for nothing"* and *"the operator asked for the empty string"* are different questions, and a
    console that cleared its box and saw "no situations match" would look broken.
    """
    _engine, _queue, app = await authutil.make_env(store)
    ids = await _seed(store)
    assert _ids(await _search(app, "admin", "")) == set(ids.values())
    assert _ids(await _search(app, "admin", "%20%20")) == set(ids.values())


async def test_a_wildcard_is_matched_literally_rather_than_expanded(store: Store) -> None:
    """`%` and `_` are `LIKE`'s, not the operator's.

    Escaped rather than stripped: a search for `10.1.0_1` must look for that string. Unescaped,
    `_` matches any character and `%` matches everything — so a single `%` would return the whole
    corpus and read as a working search rather than as a bug.
    """
    _engine, _queue, app = await authutil.make_env(store)
    ids = await _seed(store)
    assert await _search(app, "admin", "%25") == [], "a bare % must match nothing, not everything"
    assert _ids(await _search(app, "admin", "10.1.0.1")) == {ids["visible"]}
    assert await _search(app, "admin", "10.1.0_1") == [], "_ is a literal underscore here"


# --- what the search must NOT find --------------------------------------------------------------


async def test_a_scoped_editor_cannot_find_a_situation_they_cannot_see(store: Store) -> None:
    """**The scope axis.** The alarm-side match carries the listing's own `ne_id IN (…)`.

    Without that clause the search would answer *"a situation involving 10.9.0.1 exists"* to a
    principal the listing refuses to show it to — an existence oracle over exactly the population
    scoping exists to hide. The control is the same needle as `admin`, which must find it: a test
    where nobody finds it would pass against a search that matched nothing at all.
    """
    _engine, _queue, app = await authutil.make_env(store)
    ids = await _seed(store)
    await _install_scope(store)

    assert await _search(app, "editor", "10.9.0.1") == [], "the scoped editor must find nothing"
    assert _ids(await _search(app, "admin", "10.9.0.1")) == {ids["hidden"]}, (
        "CONTROL: an unrestricted principal finds it, so the refusal above is the scope filter "
        "rather than a search that matches nothing"
    )


async def test_a_scoped_editor_cannot_find_a_visible_situation_by_its_hidden_member(
    store: Store,
) -> None:
    """**The harder half of the scope axis, and the one a render filter would get wrong.**

    `mixed` has one member the editor may see and one they may not, so the listing *does* return
    it — redacted to a count, which is correct. A search that matched on the whole situation's
    alarms would let the editor confirm the hidden member's address through a situation they are
    allowed to hold, which is the leak the redaction exists to prevent, reached from the side.

    The control is the same situation found by the member they CAN see.
    """
    _engine, _queue, app = await authutil.make_env(store)
    ids = await _seed(store)
    await _install_scope(store)

    assert await _search(app, "editor", "10.9.0.2") == [], (
        "the editor found a situation by the address of a member the redaction hides from them"
    )
    assert _ids(await _search(app, "editor", "10.1.0.2")) == {ids["mixed"]}, (
        "CONTROL: the same situation, found by the member the editor may see"
    )


async def test_a_viewer_cannot_confirm_an_address_the_console_coarsens_for_them(
    store: Store,
) -> None:
    """**The shaping axis**, which is a different axis from scope and fails differently.

    A viewer here is unrestricted — every situation is theirs to list — and is nonetheless shown
    `10.1.0.0/24` where an editor is shown `10.1.0.1`. Matching the raw column would let them
    confirm the fourth octet by typing it, which is the coarsening undone through a text box.

    Two controls, because one would not be enough: the editor finds it (so the needle is real),
    and the viewer finds the same situation by its **operator name** (so the viewer's search works
    at all and this is a field rule rather than a broken principal).
    """
    _engine, _queue, app = await authutil.make_env(store)
    ids = await _seed(store)

    assert await _search(app, "viewer", "10.1.0.1") == [], (
        "a viewer matched a raw device address the console coarsens on the way out"
    )
    assert _ids(await _search(app, "editor", "10.1.0.1")) == {ids["visible"]}, (
        "CONTROL: an editor sees raw addresses and finds it"
    )
    assert _ids(await _search(app, "viewer", "ridgeway")) == {ids["visible"]}, (
        "CONTROL: the viewer's search works; only the address clause is withheld"
    )


async def test_a_device_label_is_searchable_by_every_role_and_an_address_is_not(
    store: Store,
) -> None:
    """The rule is `FIELD_RULES`, not "anything that identifies a device".

    A label is free text a person typed and `shape` passes it through for every role — the same
    reason `operator_name` is not coarsened. So a viewer who can *read* `core-sw-1` on their own
    screen can search for it, and cannot search for the address underneath. Asserting both in one
    test is deliberate: they are two halves of one rule and a release that kept one would look
    green.
    """
    _engine, _queue, app = await authutil.make_env(store)
    ids = await _seed(store)
    async with store.lock:
        cur = await store.conn.execute("SELECT id FROM device WHERE ip='10.1.0.1'")
        device = int((await cur.fetchone())[0])  # type: ignore[index]
        await store.set_label("device", device, "core-sw-1", BASE)
        await store.commit()

    assert _ids(await _search(app, "viewer", "core-sw-1")) == {ids["visible"]}, "the label is text"
    assert await _search(app, "viewer", "10.1.0.1") == [], "the address underneath it is not"


async def test_the_needle_is_bounded_rather_than_rejected(store: Store) -> None:
    """A 4 KB query string is truncated, answered 200, and matches nothing surprising.

    Rejection is the wrong primitive for a search box — the same argument `ClientFingerprint`
    makes about a client's report one layer down — and an unbounded needle would reach `LIKE` and
    be scanned against every alarm of every listed situation.
    """
    _engine, _queue, app = await authutil.make_env(store)
    await _seed(store)
    assert await _search(app, "admin", "z" * 4000) == []
    assert _ids(await _search(app, "admin", "ridgeway" + "z" * 4000)) == set(), (
        "the truncation must not turn a long needle into a shorter, matching one"
    )
