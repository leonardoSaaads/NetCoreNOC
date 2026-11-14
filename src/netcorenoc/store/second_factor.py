"""Two-factor storage: each person's authenticator, recovery codes, and sign-in challenges
(ADR #482).

Storage only. The codes, the secret's format and every decision about who may do what are
`crosscutting/totp.py` and `api/routes/two_factor.py`; this module keeps the rows, and the rules
only SQL keeps cheaply:

* **A code's step is accepted once**: :meth:`second_factor_use` advances ``last_step`` only
  forward, in the statement that checks it, so two requests racing with one code cannot both win.
* **A recovery code is consumed by the statement that checks it** (:meth:`take_recovery_code`).
* **A challenge counts its own failures** (:meth:`login_challenge_failed`) and expired ones are
  removed whenever a new one is made.

The secret is stored sealed (``vault.seal``, place ``app_user.totp``); it is opened only by
:meth:`second_factor_secret`, for the one comparison that needs it.
"""

from __future__ import annotations

import json
from typing import Any

from netcorenoc.crosscutting import vault
from netcorenoc.store.base import StoreBase

__all__ = ["CHALLENGE_PLACE", "SECRET_PLACE", "SecondFactorMixin"]

SECRET_PLACE = "app_user.totp"  # nosec B105 - a vault place label (AAD), never a secret
CHALLENGE_PLACE = "login_challenge.secret"


class SecondFactorMixin(StoreBase):
    # -- the enrolled authenticator ---------------------------------------------------------------

    async def second_factor(self, user_id: int) -> dict[str, Any] | None:
        """Whether ``user_id`` has an authenticator: when it was enrolled and last used, how many
        recovery codes are left. **Never the secret.**"""
        cur = await self.conn.execute(
            "SELECT f.enrolled_at, f.last_used_at, "
            "(SELECT COUNT(*) FROM user_recovery_code r WHERE r.user_id=f.user_id "
            "AND r.used_at IS NULL) AS recovery_left "
            "FROM user_second_factor f WHERE f.user_id=?",
            (user_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row is not None else None

    async def second_factor_secret(self, user_id: int) -> tuple[str, int] | None:
        """The opened secret and the last accepted step, for the one check that needs them."""
        cur = await self.conn.execute(
            "SELECT secret, last_step FROM user_second_factor WHERE user_id=?", (user_id,)
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return vault.current().open_text(SECRET_PLACE, str(row[0])), int(row[1])

    async def enroll_second_factor(
        self, user_id: int, secret: str, step: int, recovery_digests: list[str], now: float
    ) -> None:
        """Make ``secret`` the person's authenticator (replacing any) and ``recovery_digests``
        their recovery codes. ``step`` is the step the enrolling code was accepted for."""
        await self.conn.execute(
            "INSERT INTO user_second_factor "
            "(user_id, secret, enrolled_at, last_step, last_used_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (user_id) DO UPDATE SET secret=excluded.secret, "
            "enrolled_at=excluded.enrolled_at, last_step=excluded.last_step, "
            "last_used_at=excluded.last_used_at",
            (user_id, vault.current().seal(SECRET_PLACE, secret), now, step, now),
        )
        await self.replace_recovery_codes(user_id, recovery_digests, now)

    async def second_factor_use(self, user_id: int, step: int, now: float) -> bool:
        """Record that a code for ``step`` was accepted — **only if** no code for this step or a
        later one was. False means the code was already used."""
        cur = await self.conn.execute(
            "UPDATE user_second_factor SET last_step=?, last_used_at=? "
            "WHERE user_id=? AND last_step<? RETURNING user_id",
            (step, now, user_id, step),
        )
        return await cur.fetchone() is not None

    async def clear_second_factor(self, user_id: int) -> bool:
        """Forget the person's authenticator and recovery codes; they enrol again at sign-in."""
        await self.conn.execute("DELETE FROM user_recovery_code WHERE user_id=?", (user_id,))
        await self.conn.execute("DELETE FROM login_challenge WHERE user_id=?", (user_id,))
        cur = await self.conn.execute(
            "DELETE FROM user_second_factor WHERE user_id=? RETURNING user_id", (user_id,)
        )
        return await cur.fetchone() is not None

    async def second_factor_users(self) -> set[int]:
        """The ids of every person with an authenticator (for the people list)."""
        cur = await self.conn.execute("SELECT user_id FROM user_second_factor")
        return {int(r[0]) for r in await cur.fetchall()}

    # -- recovery codes ---------------------------------------------------------------------------

    async def replace_recovery_codes(self, user_id: int, digests: list[str], now: float) -> None:
        await self.conn.execute("DELETE FROM user_recovery_code WHERE user_id=?", (user_id,))
        for digest in digests:
            await self.conn.execute(
                "INSERT INTO user_recovery_code (user_id, digest, created_at) VALUES (?, ?, ?)",
                (user_id, digest, now),
            )

    async def take_recovery_code(self, user_id: int, digest: str, now: float) -> bool:
        """Use one recovery code: true once, false for an unknown or already used one."""
        cur = await self.conn.execute(
            "UPDATE user_recovery_code SET used_at=? WHERE user_id=? AND digest=? "
            "AND used_at IS NULL RETURNING user_id",
            (now, user_id, digest),
        )
        return await cur.fetchone() is not None

    # -- the step between the password and the code -----------------------------------------------

    async def create_login_challenge(
        self,
        challenge_hash: str,
        user_id: int,
        purpose: str,
        now: float,
        expires_at: float,
        source_ip: str | None,
        *,
        secret: str | None = None,
        recovery_digests: list[str] | None = None,
    ) -> None:
        """A challenge for ``user_id``. One per person at a time: an older one is replaced."""
        await self.conn.execute("DELETE FROM login_challenge WHERE expires_at<?", (now,))
        await self.conn.execute("DELETE FROM login_challenge WHERE user_id=?", (user_id,))
        await self.conn.execute(
            "INSERT INTO login_challenge (challenge_hash, user_id, purpose, secret, recovery, "
            "created_at, expires_at, source_ip) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                challenge_hash,
                user_id,
                purpose,
                vault.current().seal(CHALLENGE_PLACE, secret) if secret is not None else None,
                json.dumps(recovery_digests) if recovery_digests is not None else None,
                now,
                expires_at,
                source_ip,
            ),
        )

    async def login_challenge(self, challenge_hash: str, now: float) -> dict[str, Any] | None:
        """A live challenge with its person's name and role, the enrolment secret opened — or
        None (unknown or expired)."""
        cur = await self.conn.execute(
            "SELECT c.*, u.username, u.role, u.disabled FROM login_challenge c "
            "JOIN app_user u ON u.id=c.user_id WHERE c.challenge_hash=? AND c.expires_at>=?",
            (challenge_hash, now),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        out = dict(row)
        if out["secret"] is not None:
            out["secret"] = vault.current().open_text(CHALLENGE_PLACE, str(out["secret"]))
        out["recovery"] = json.loads(out["recovery"]) if out["recovery"] else []
        return out

    async def login_challenge_failed(self, challenge_hash: str) -> int:
        """Count one wrong code; returns how many the challenge has had."""
        cur = await self.conn.execute(
            "UPDATE login_challenge SET attempts=attempts+1 WHERE challenge_hash=? "
            "RETURNING attempts",
            (challenge_hash,),
        )
        row = await cur.fetchone()
        return int(row[0]) if row is not None else 0

    async def delete_login_challenge(self, challenge_hash: str) -> None:
        await self.conn.execute(
            "DELETE FROM login_challenge WHERE challenge_hash=?", (challenge_hash,)
        )
