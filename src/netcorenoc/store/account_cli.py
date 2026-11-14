"""The CLI's account-recovery commands: ``admin reset-password`` and ``admin reset-two-factor``.

Split from ``__main__.py`` at the 400-line guard when the second arrived (ADR #482). Both are for
the account whose owner cannot get in and has no other admin to ask; both need the host's database
credentials and the appliance's key file — the trust boundary the bootstrap admin already rests on
(DECISIONS #234) — and both are audited as ``cli`` and revoke every session of the account.
"""

from __future__ import annotations

import secrets
import sys
import time

from netcorenoc.crosscutting import audit
from netcorenoc.crosscutting.administration import BOOTSTRAP_PASSWORD_CHARS
from netcorenoc.crosscutting.auth import hash_password
from netcorenoc.crosscutting.settings import Settings
from netcorenoc.store.cli import _key, _open

__all__ = ["_reset_password", "_reset_two_factor"]


async def _reset_password(settings: Settings, username: str) -> int:
    """A fresh one-time password for `username`, printed once; every session of theirs revoked.

    The forgotten-password path the console cannot offer when the person who forgot is the only
    admin. `must_change_password` is set, so the printed password works for exactly one sign-in.
    The hash is keyed with the appliance's secret key (ADR #453), which this loads and checks.
    """
    _key(settings, create=False)
    store = await _open(settings)
    try:
        async with store.lock:
            await store.check_vault()
            user = await store.get_user_by_name(username)
            if user is None:
                print(f"no account named {username!r}", file=sys.stderr)
                return 1
            password = secrets.token_urlsafe(BOOTSTRAP_PASSWORD_CHARS)[:BOOTSTRAP_PASSWORD_CHARS]
            now = time.time()
            uid = int(user["id"])
            # Must be changed at the next sign-in, and no reset link left to race it (ADR #453).
            await store.replace_password(uid, hash_password(password), now)
            revoked = await store.revoke_user_sessions(uid)
            await audit.write_event(
                store,
                ts=now,
                actor="cli",
                role=None,
                source_ip=None,
                action="user.update",
                outcome="ok",
                object_type="user",
                object_id=str(uid),
                details={"change": "one-time sign-in issued", "sessions_revoked": revoked},
            )
            await store.commit()
    finally:
        await store.close()
    print(f"new one-time password for {username}: {password}")
    print("Sign in with it and choose a new password; it is shown once and not stored.")
    if user.get("disabled"):
        print("Note: this account is DISABLED; an admin must enable it before it can sign in.")
    return 0


async def _reset_two_factor(settings: Settings, username: str) -> int:
    """Forget ``username``'s authenticator; the next sign-in enrols a new one (ADR #482)."""
    _key(settings, create=False)
    store = await _open(settings)
    try:
        async with store.lock:
            await store.check_vault()
            user = await store.get_user_by_name(username)
            if user is None:
                print(f"no account named {username!r}", file=sys.stderr)
                return 1
            uid, now = int(user["id"]), time.time()
            had = await store.clear_second_factor(uid)
            revoked = await store.revoke_user_sessions(uid)
            await audit.write_event(
                store,
                ts=now,
                actor="cli",
                role=None,
                source_ip=None,
                action="twofactor.reset",
                outcome="ok",
                object_type="user",
                object_id=str(uid),
                details={"by": "cli", "had_one": had, "sessions_revoked": revoked},
            )
            await store.commit()
    finally:
        await store.close()
    print(f"{username}'s authenticator is forgotten; the next sign-in sets up a new one.")
    return 0
