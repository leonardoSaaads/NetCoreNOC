"""F79 — the appliance cannot be left with no one who can administer it.

Two defects composed into a lockout, so there are two properties here and they are independent:

* **the invariant** — no transition through the HTTP surface may take the last enabled admin away
  (`DECISIONS #233`);
* **the recovery** — an appliance that reaches that state anyway (a hand-edited database, an older
  version, a restore) mints a new admin on its next boot instead of being lost (`#234`).

**Every refusal here has a control beside it**, and the controls are the reason the file is this
long: *"the request was refused"* is also what a broken endpoint says. F79's own control is that
creating a second user succeeds — without it, a 400 on the demotion could mean the route stopped
working rather than that the guard started.

What this file does NOT cover: the console. It may hide the control and it does, but that is an
affordance and this is the control (principle 6).
"""

from __future__ import annotations

from typing import Any

import httpx

from netcorenoc.crosscutting import administration, auth
from netcorenoc.store import Store

import authutil


async def _uid(store: Store, username: str) -> int:
    async with store.lock:
        row = await store.get_user_by_name(username)
    assert row is not None, username
    return int(row["id"])


async def _roles(store: Store) -> dict[str, str]:
    async with store.lock:
        return {str(r["username"]): str(r["role"]) for r in await store.list_users()}


async def _sole_admin(store: Store, app: Any) -> tuple[httpx.AsyncClient, int]:
    """An appliance whose only enabled admin is `adm`, and a client signed in as it.

    `authutil.make_users` creates the bootstrap `admin` **and** `adm`, so there are two admins
    until this demotes one — which it does through the route, proving the route still works on the
    way to setting up the case where it must refuse.
    """
    client = await authutil.client_as(app, "admin")
    demote = await client.post(
        f"/api/users/{await _uid(store, 'admin')}/role", json={"role": "viewer"}
    )
    assert demote.status_code == 200, demote.text
    async with store.lock:
        assert await store.count_enabled_admins() == 1
    return client, await _uid(store, "adm")


# --- the invariant: a role change ---------------------------------------------------------------


async def test_the_last_admin_cannot_demote_itself(store: Store) -> None:
    """**F79's treatment, refused.** The exact gesture that cost the maintainer an environment."""
    _engine, _queue, app = await authutil.make_env(store)
    client, adm = await _sole_admin(store, app)
    try:
        refused = await client.post(f"/api/users/{adm}/role", json={"role": "viewer"})
        assert refused.status_code == 400, refused.text
        assert "only enabled admin" in refused.json()["detail"]
        # The refusal is not cosmetic: the role did not move, and the session still works. A guard
        # that refused the request AFTER writing the row would pass a status-code assertion.
        assert (await _roles(store))["adm"] == "admin"
        assert (await client.get("/api/users")).status_code == 200
    finally:
        await client.aclose()


async def test_the_control_a_second_admin_makes_the_same_demotion_succeed(store: Store) -> None:
    """**The control for the refusal above**, and the one that matters most.

    Without it, the 400 could be a route that refuses every role change — which would satisfy the
    assertion above and be a worse defect than F79.
    """
    _engine, _queue, app = await authutil.make_env(store)
    client, adm = await _sole_admin(store, app)
    try:
        promote = await client.post(
            f"/api/users/{await _uid(store, 'edt')}/role", json={"role": "admin"}
        )
        assert promote.status_code == 200, promote.text
        allowed = await client.post(f"/api/users/{adm}/role", json={"role": "viewer"})
        assert allowed.status_code == 200, allowed.text
        assert (await _roles(store))["adm"] == "viewer"
        async with store.lock:
            assert await store.count_enabled_admins() == 1  # `edt`, now
    finally:
        await client.aclose()


async def test_promoting_and_re_promoting_are_never_refused(store: Store) -> None:
    """The other control: the invariant must be silent about transitions that remove no admin.

    An admin set to `admin` is a no-op and a viewer set to `admin` adds one. A guard that counted
    without asking what the transition *was* would refuse both.

    The self-directed no-op is driven **last and through its own client**, because a role change
    revokes the target's sessions unconditionally — the route does not ask whether the role
    actually moved, so an admin re-affirming its own role signs itself out. That is unchanged
    v0.12.0 behaviour, it errs in the safe direction, and this release does not touch it; the
    assertion is that the invariant stays silent, not that the session survives.
    """
    _engine, _queue, app = await authutil.make_env(store)
    client, adm = await _sole_admin(store, app)
    try:
        vwr = await _uid(store, "vwr")
        added = await client.post(f"/api/users/{vwr}/role", json={"role": "admin"})
        assert added.status_code == 200, added.text
        noop = await client.post(f"/api/users/{adm}/role", json={"role": "admin"})
        assert noop.status_code == 200, noop.text
        assert (await _roles(store))["adm"] == "admin"
        assert (await client.get("/api/me")).status_code == 401  # the unconditional revoke
    finally:
        await client.aclose()


# --- the invariant: a deletion -------------------------------------------------------------------


async def test_an_admin_token_cannot_delete_the_last_admin_account(store: Store) -> None:
    """The deletion arm, driven by the principal that can actually reach it.

    A **session** admin can never delete the last account — `principal.user_id == uid` refuses
    self-deletion first, and any other target leaves the caller. A **service token** with role
    `admin` has no `user_id`, so it can delete the last account that can sign in, and then the
    appliance is one `DELETE /api/tokens/{id}` from having no administrator at all. That is why
    `would_remove_last_admin` counts `user` rows and not principals.
    """
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        made = await admin.post("/api/tokens", json={"name": "ops", "role": "admin"})
        assert made.status_code == 200, made.text
        token = made.json()["token"]
        await admin.post(f"/api/users/{await _uid(store, 'admin')}/role", json={"role": "viewer"})
    finally:
        await admin.aclose()

    bearer = authutil.new_client(app)
    bearer.headers["Authorization"] = f"Bearer {token}"
    try:
        adm = await _uid(store, "adm")
        refused = await bearer.request("DELETE", f"/api/users/{adm}")
        assert refused.status_code == 400, refused.text
        assert "only enabled admin" in refused.json()["detail"]
        assert "adm" in await _roles(store), "the row was deleted despite the refusal"

        # THE CONTROL, through the same principal: a non-admin deletes normally, so the 400 above
        # is the guard rather than a token that may not delete anything.
        vwr = await _uid(store, "vwr")
        allowed = await bearer.request("DELETE", f"/api/users/{vwr}")
        assert allowed.status_code == 200, allowed.text
        assert "vwr" not in await _roles(store)
    finally:
        await bearer.aclose()


# --- the invariant: disabling, which has no route ------------------------------------------------


async def test_the_predicate_covers_disabling_even_though_no_route_disables(store: Store) -> None:
    """`disabling=True` is answered, and **nothing in the tree can ask it over HTTP yet.**

    Both halves are the point. The brief asked for three injections and the third is a transition
    this product does not have: `user.disabled` is read by `perform_login` and `get_session` and
    written by no route, no store method and no CLI command. Rather than add a disable feature to
    make a guard testable, the predicate takes the transition as a parameter — so the day the route
    arrives it cannot reach the column without passing through here (#233).

    `test_no_route_can_write_the_disabled_column` below is the half that would notice.
    """
    async with store.lock:
        await administration.bootstrap_admin(store, 0.0)
        await store.create_user("solo", auth.hash_password(authutil.PW), "viewer", False, 0.0)
        await store.commit()
        admin = await store.get_user_by_name("admin")
        viewer = await store.get_user_by_name("solo")
        assert admin is not None and viewer is not None

        assert await administration.would_remove_last_admin(store, admin, disabling=True) is True
        assert await administration.would_remove_last_admin(store, admin, deleting=True) is True
        assert await administration.would_remove_last_admin(store, admin, new_role="viewer") is True
        # The controls, one per branch that must stay silent.
        assert await administration.would_remove_last_admin(store, admin, new_role="admin") is False
        assert await administration.would_remove_last_admin(store, viewer, deleting=True) is False
        assert await administration.would_remove_last_admin(store, viewer, disabling=True) is False


def test_no_route_can_write_the_disabled_column() -> None:
    """**Ask what the guard cannot see.**

    The predicate above claims to cover a transition nothing performs. That claim expires the
    moment somebody adds a disable route without routing it through `would_remove_last_admin`, and
    this is what fails on that day: any `UPDATE`/`INSERT` naming `disabled` outside the store's own
    user creation is either the new route (which must call the predicate) or a way around it.
    """
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src" / "netcorenoc"
    writes: list[str] = []
    for path in sorted(src.rglob("*.py")):
        if path.name == "auth.py" and path.parent.name == "store":
            # The one module allowed to name the column at all; it reads it and never sets it.
            text = path.read_text(encoding="utf-8")
            assert not re.search(r"(?i)\bUPDATE\s+user\s+SET[^\"']*\bdisabled\s*=", text), (
                "store/auth.py now writes user.disabled; it must go through "
                "administration.would_remove_last_admin(disabling=True) and this guard must be "
                "updated"
            )
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"(?i)\bUPDATE\s+user\s+SET[^\"']*\bdisabled\s*=", text):
            writes.append(str(path.relative_to(src)))
    assert writes == [], (
        f"a module outside the store now writes user.disabled: {writes}. The last-admin invariant "
        f"takes `disabling` as a parameter for exactly this moment (DECISIONS #233)."
    )


# --- the recovery ---------------------------------------------------------------------------------


async def test_bootstrap_recovers_an_appliance_that_has_users_but_no_admin(store: Store) -> None:
    """**F79's second half.** The shipped guard counted users and returned None here.

    The database is put into the lost state directly rather than through the route, because the
    route now refuses to create it — which is the repair working. What is under test is that a
    database in that state, however it got there, recovers.
    """
    async with store.lock:
        first = await administration.bootstrap_admin(store, 0.0)
        assert first is not None
        await store.create_user("u2", auth.hash_password(authutil.PW), "viewer", False, 0.0)
        admin = await store.get_user_by_name("admin")
        assert admin is not None
        await store.set_user_role(int(admin["id"]), "viewer", 0.0)  # the F79 state, by hand
        await store.commit()
        assert await store.count_users() == 2, "the shipped guard's quantity"
        assert await store.count_enabled_admins() == 0, "the corrected one"

        recovered = await administration.bootstrap_admin(store, 1.0)
        await store.commit()

    assert recovered is not None, "a database with users and no admin did not recover"
    assert len(recovered.password) == administration.BOOTSTRAP_PASSWORD_CHARS
    assert recovered.username == administration.RECOVERY_USERNAME
    roles = await _roles(store)
    # `admin` is taken by the demoted account, so the recovered one takes the fallback name rather
    # than colliding — and the demoted account is left exactly as it was.
    assert roles["admin"] == "viewer", "recovery silently re-promoted an existing account"
    assert roles[administration.RECOVERY_USERNAME] == "admin"
    async with store.lock:
        row = await store.get_user_by_name(administration.RECOVERY_USERNAME)
    assert row is not None and row["must_change_password"] == 1


async def test_recovery_does_not_run_while_an_admin_is_still_there(store: Store) -> None:
    """The control for the recovery: it must be silent on every ordinary boot.

    A bootstrap that minted an admin whenever it was called would recover F79 and hand every
    restart a fresh administrator nobody asked for.
    """
    async with store.lock:
        assert await administration.bootstrap_admin(store, 0.0) is not None
        await store.create_user("u2", auth.hash_password(authutil.PW), "viewer", False, 0.0)
        await store.commit()
        assert await administration.bootstrap_admin(store, 1.0) is None
        assert await administration.bootstrap_admin(store, 2.0) is None
        await store.commit()
    assert sorted(await _roles(store)) == ["admin", "u2"]


async def test_a_disabled_admin_does_not_count_as_an_admin(store: Store) -> None:
    """The column is read by the invariant, not only by login.

    An appliance whose sole admin is disabled cannot be administered by anyone, so it is in the
    state recovery exists for — even though `count_users()` and a naive role count both say it has
    an admin.
    """
    async with store.lock:
        assert await administration.bootstrap_admin(store, 0.0) is not None
        admin = await store.get_user_by_name("admin")
        assert admin is not None
        await store.conn.execute("UPDATE user SET disabled=1 WHERE id=?", (admin["id"],))
        await store.commit()
        assert await store.count_enabled_admins() == 0
        recovered = await administration.bootstrap_admin(store, 1.0)
        await store.commit()
    assert recovered is not None
    assert (await _roles(store))[administration.RECOVERY_USERNAME] == "admin"


async def test_the_recovery_name_keeps_counting_when_both_names_are_taken(store: Store) -> None:
    """Two rounds of recovery on one database must not collide on the fallback name either."""
    async with store.lock:
        await store.create_user("admin", auth.hash_password(authutil.PW), "viewer", False, 0.0)
        await store.create_user(
            administration.RECOVERY_USERNAME, auth.hash_password(authutil.PW), "viewer", False, 0.0
        )
        await store.commit()
        assert await administration.bootstrap_admin(store, 0.0) is not None
        await store.commit()
    assert (await _roles(store))[f"{administration.RECOVERY_USERNAME}-2"] == "admin"
