"""How this appliance comes to have an administrator, and how it cannot stop having one.

Split out of ``auth.py`` in v0.15.3 (DECISIONS #242). The two things here are one decision —
*who may administer this appliance* — and they are the two halves of F79:

* :func:`bootstrap_admin` mints an admin when none exists, at first boot **and** as the recovery
  from a database that lost its last one;
* :func:`would_remove_last_admin` is the predicate every transition that could cause that state
  asks first.

They belong together because they are each other's counterweight: the invariant makes the lost
state unreachable through the product, and the bootstrap makes it survivable when something outside
the product creates it anyway. Reading one without the other leaves the reasoning half-stated.

**Deliberately NOT re-exported from** ``auth``. The `rbac/` precedent re-exports because its
consumers must reach one authority object; here the dependency runs the other way — this module
needs `auth.hash_password` — so a re-export would be an import cycle. Call sites name the concern
they are using instead, which is the more honest read at each one: `routes_admin.py` asks about the
invariant, `runner.py` asks about the bootstrap, and neither is asking about scrypt.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, NamedTuple

from netcorenoc.crosscutting.auth import hash_password

if TYPE_CHECKING:
    from netcorenoc.store import Store

# -- bootstrap -----------------------------------------------------------------------

#: How many URL-safe characters the minted password carries.
BOOTSTRAP_PASSWORD_CHARS = 20

#: The username the bootstrap admin takes, and the base it falls back to when that name is already
#: occupied by somebody who is no longer an admin — which is exactly the F79 database.
BOOTSTRAP_USERNAME = "admin"
RECOVERY_USERNAME = "recovery-admin"


async def _free_username(store: Store, *names: str) -> str:
    """The first of `names` nobody holds, else the last one with a counter appended."""
    for name in names:
        if await store.get_user_by_name(name) is None:
            return name
    suffix = 2
    while await store.get_user_by_name(f"{names[-1]}-{suffix}") is not None:
        suffix += 1
    return f"{names[-1]}-{suffix}"


class Bootstrap(NamedTuple):
    """The account a bootstrap minted, and its password — shown once.

    Both halves, because the caller prints both. Until v0.15.3 this returned the password alone and
    `runner._print_bootstrap_banner` printed a hard-coded ``username: admin`` beside it, which was
    true only because the name could never be anything else. Recovery can take `recovery-admin`
    (#234), so the banner would have printed a username nobody could sign in with — a restated
    truth going stale the moment the thing it restated changed.
    """

    username: str
    password: str


async def bootstrap_admin(store: Store, now: float) -> Bootstrap | None:
    """Mint an admin with a random password when the appliance has none; return it once.

    **The guard is "no enabled admin", not "no users" (F79).** It counted users, which is a
    different question and gave a different answer: demote the sole admin on an appliance that has
    any second account and this function never runs again, so a restart does not recover and the
    only remedy is deleting the database. The function's own name says what it is for; the count is
    now the one that matches it.

    Recovery is deliberately the same experience as first boot — restart, read the password from
    the log, sign in, change it — because the operator this exists for has a browser and a restart
    and nothing else. The security position, stated rather than assumed: anyone who can restart the
    process and read its log already owns the appliance, so minting an admin on a database that has
    none lowers no barrier that was still standing (DECISIONS #234).
    """
    if await store.count_enabled_admins() > 0:
        return None
    password = secrets.token_urlsafe(BOOTSTRAP_PASSWORD_CHARS)[:BOOTSTRAP_PASSWORD_CHARS]
    username = await _free_username(store, BOOTSTRAP_USERNAME, RECOVERY_USERNAME)
    await store.create_user(
        username=username,
        password_hash=hash_password(password),
        role="admin",
        must_change_password=True,
        now=now,
    )
    return Bootstrap(username, password)


# -- the last-admin invariant (F79, DECISIONS #233) -----------------------------------

#: What a route says when the invariant would be broken. One sentence, naming the invariant and
#: the way out, because a bare refusal on the only screen that can create the second admin teaches
#: an operator that the product is broken rather than that they are one step short.
LAST_ADMIN_REFUSAL = (
    "this is the only enabled admin; promote or create another admin first, "
    "or the appliance would have no one who can administer it"
)


async def would_remove_last_admin(
    store: Store,
    user: dict[str, object],
    *,
    new_role: str | None = None,
    deleting: bool = False,
    disabling: bool = False,
) -> bool:
    """Would this transition leave the appliance with no enabled admin?

    One predicate for all three transitions, taking the transition as a parameter rather than
    existing three times. `disabling` has no route behind it today — `user.disabled` is read by
    `perform_login` and `get_session` and written by nothing in the tree — and it is a parameter
    anyway so that the day a disable route is added it cannot reach the column without passing
    through here (DECISIONS #233).

    Answers False for a transition that does not remove an admin at all, so a caller may ask
    unconditionally: promoting a viewer, renaming, and a role change from admin to admin are all
    no-ops against this invariant.

    **An admin service token is not an admin for this purpose.** The count is over `user` rows, and
    that is deliberate: a token is a credential that can be revoked and cannot change its own role,
    so an appliance whose only administrator is a token is one revocation from the state this
    invariant exists to prevent. It is also what makes the deletion route reachable at all — a
    session principal can never delete itself, but a token-authenticated admin can delete the last
    account that can sign in.
    """
    if user.get("role") != "admin" or user.get("disabled"):
        return False  # not an enabled admin; removing it removes nothing
    if not deleting and not disabling and new_role == "admin":
        return False  # still an admin afterwards
    remaining = await store.count_enabled_admins(excluding=int(user["id"]))  # type: ignore[call-overload]
    return remaining == 0
