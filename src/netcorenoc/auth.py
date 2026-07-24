"""Authentication: scrypt passwords, login throttling, sessions, service tokens, bootstrap.

All persistence goes through :class:`~netcorenoc.store.Store`; this module owns the crypto
and the policy. HTTP concerns (cookies, audit rows, responses) live in ``api.py``. The
scrypt cost parameters are module constants so they can be upgraded later (the per-hash
string records the parameters used) — production keeps ``n=2**17`` (asserted by a policy
test); the test suite lowers them for speed via an autouse fixture.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from netcorenoc.store import Store

# scrypt parameters (NIST-aligned; upgradeable). See docs/gates/v0.2-phase-1.md.
# PRODUCTION_SCRYPT_N is the shipped work factor (asserted by a policy test); SCRYPT_N is
# the live value the test suite may lower for speed — production always uses 2**17.
PRODUCTION_SCRYPT_N = 2**17
SCRYPT_N = PRODUCTION_SCRYPT_N
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_MAXMEM = 256 * 1024 * 1024

# Password policy (NIST SP 800-63B): length only, no composition rules, no expiry.
MIN_PASSWORD = 12
MAX_PASSWORD = 128

# Sessions.
COOKIE_NAME = "netcorenoc_session"
IDLE_TIMEOUT_S = 30 * 60
ABSOLUTE_TIMEOUT_S = 12 * 60 * 60

# Login throttle: after this many consecutive failures, exponential lockout to a cap.
LOCKOUT_THRESHOLD = 5
LOCKOUT_BASE_S = 1.0
LOCKOUT_MAX_S = 15 * 60.0

BOOTSTRAP_PASSWORD_CHARS = 20

_DUMMY_SALT = b"\x00" * 16


# -- passwords -----------------------------------------------------------------------


def hash_password(password: str) -> str:
    """scrypt$<n>$<r>$<p>$<salt_b64>$<hash_b64> using the current cost parameters."""
    salt = os.urandom(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
        maxmem=SCRYPT_MAXMEM,
    )
    salt_b64 = base64.b64encode(salt).decode("ascii")
    hash_b64 = base64.b64encode(derived).decode("ascii")
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt_b64}${hash_b64}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verify against a stored hash, reading its embedded parameters."""
    try:
        scheme, n_s, r_s, p_s, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n_s), int(r_s), int(p_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=len(expected),
        maxmem=SCRYPT_MAXMEM,
    )
    return hmac.compare_digest(derived, expected)


def dummy_verify(password: str) -> bool:
    """Do one scrypt of equal cost when the user is absent — no timing enumeration."""
    hashlib.scrypt(
        password.encode("utf-8"),
        salt=_DUMMY_SALT,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
        maxmem=SCRYPT_MAXMEM,
    )
    return False


def validate_password(password: str) -> str | None:
    """Return an error message if the password violates policy, else None."""
    if len(password) < MIN_PASSWORD:
        return f"password must be at least {MIN_PASSWORD} characters"
    if len(password) > MAX_PASSWORD:
        return f"password must be at most {MAX_PASSWORD} characters"
    return None


# -- login throttle (in-memory, single process; DECISIONS v0.2 #15) ------------------


@dataclass
class _Attempts:
    fails: int = 0
    locked_until: float = 0.0


class LoginThrottle:
    """Per-username and per-source-IP exponential lockout after repeated failures."""

    def __init__(self) -> None:
        self._by_user: dict[str, _Attempts] = {}
        self._by_ip: dict[str, _Attempts] = {}

    @staticmethod
    def _delay(fails: int) -> float:
        if fails < LOCKOUT_THRESHOLD:
            return 0.0
        return float(min(LOCKOUT_BASE_S * (2 ** (fails - LOCKOUT_THRESHOLD)), LOCKOUT_MAX_S))

    def locked_for(self, username: str, source_ip: str, now: float) -> float:
        """Seconds of lockout remaining (0 if the caller may attempt a login)."""
        remaining = 0.0
        for state in (self._by_user.get(username), self._by_ip.get(source_ip)):
            if state is not None:
                remaining = max(remaining, state.locked_until - now)
        return max(0.0, remaining)

    def record_failure(self, username: str, source_ip: str, now: float) -> None:
        for table, key in ((self._by_user, username), (self._by_ip, source_ip)):
            state = table.setdefault(key, _Attempts())
            state.fails += 1
            delay = self._delay(state.fails)
            if delay > 0.0:
                state.locked_until = now + delay
        self._bound()

    def record_success(self, username: str, source_ip: str) -> None:
        self._by_user.pop(username, None)
        self._by_ip.pop(source_ip, None)

    def _bound(self) -> None:
        # Bound memory against username/address churn, like the HTTP rate limiter.
        for table in (self._by_user, self._by_ip):
            if len(table) > 4096:
                table.clear()


# -- identity ------------------------------------------------------------------------


@dataclass(frozen=True)
class Principal:
    """A resolved caller: a logged-in user session or a service token."""

    actor: str
    role: str
    kind: str  # "session" | "token"
    user_id: int | None = None
    must_change_password: bool = False


def hash_token(token: str) -> str:
    """SHA-256 hex of a session id or service-token value (never store the value)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


async def open_session(store: Store, user_id: int, source_ip: str | None, now: float) -> str:
    """Mint a fresh session (new id every login — fixation defence) and persist its hash."""
    token = new_session_token()
    await store.create_session(
        session_hash=hash_token(token),
        user_id=user_id,
        created_at=now,
        last_seen_at=now,
        idle_expiry=now + IDLE_TIMEOUT_S,
        absolute_expiry=now + ABSOLUTE_TIMEOUT_S,
        source_ip=source_ip,
    )
    return token


async def resolve_session(store: Store, token: str, now: float) -> Principal | None:
    """Resolve a session cookie to a Principal, sliding idle expiry; None if invalid."""
    row = await store.get_session(hash_token(token))
    if row is None or row["disabled"]:
        return None
    if now > row["absolute_expiry"] or now > row["idle_expiry"]:
        await store.delete_session(row["session_hash"])
        return None
    await store.touch_session(row["session_hash"], now, now + IDLE_TIMEOUT_S)
    return Principal(
        actor=row["username"],
        role=row["role"],
        kind="session",
        user_id=int(row["user_id"]),
        must_change_password=bool(row["must_change_password"]),
    )


async def resolve_bearer(store: Store, token: str, now: float) -> Principal | None:
    """Resolve a Bearer token to a service-token identity (the legacy shared token was
    removed in v0.3.0, §5.8)."""
    row = await store.get_token(hash_token(token))
    if row is not None and not row["revoked"]:
        await store.touch_token_used(row["token_hash"], now)
        return Principal(actor=row["name"], role=row["role"], kind="token")
    return None


# -- bootstrap -----------------------------------------------------------------------


async def bootstrap_admin(store: Store, now: float) -> str | None:
    """Create the initial admin with a random password if no users exist; return it once."""
    if await store.count_users() > 0:
        return None
    password = secrets.token_urlsafe(BOOTSTRAP_PASSWORD_CHARS)[:BOOTSTRAP_PASSWORD_CHARS]
    await store.create_user(
        username="admin",
        password_hash=hash_password(password),
        role="admin",
        must_change_password=True,
        now=now,
    )
    return password


@dataclass(frozen=True)
class LoginOutcome:
    status: str  # "ok" | "fail" | "locked" | "must_change" | "policy"
    principal: Principal | None = None
    session_token: str | None = None
    message: str | None = None


async def perform_login(
    store: Store,
    throttle: LoginThrottle,
    *,
    username: str,
    password: str,
    new_password: str | None,
    source_ip: str,
    now: float,
) -> LoginOutcome:
    """Authenticate and (on success) open a session. Auditing/cookies are the caller's job.

    Timing and the failure message are identical for "no such user" and "wrong password".
    """
    if throttle.locked_for(username, source_ip, now) > 0:
        return LoginOutcome("locked", message="too many attempts; try again later")
    user = await store.get_user_by_name(username)
    if user is None or user["disabled"]:
        dummy_verify(password)
        throttle.record_failure(username, source_ip, now)
        return LoginOutcome("fail", message="invalid username or password")
    if not verify_password(password, user["password_hash"]):
        throttle.record_failure(username, source_ip, now)
        return LoginOutcome("fail", message="invalid username or password")

    throttle.record_success(username, source_ip)
    user_id = int(user["id"])
    if user["must_change_password"]:
        if not new_password:
            return LoginOutcome("must_change", message="password change required")
        policy = validate_password(new_password)
        if policy is not None:
            return LoginOutcome("policy", message=policy)
        await store.update_user_password(user_id, hash_password(new_password), now)
        await store.revoke_user_sessions(user_id)

    token = await open_session(store, user_id, source_ip, now)
    principal = Principal(actor=username, role=user["role"], kind="session", user_id=user_id)
    return LoginOutcome("ok", principal=principal, session_token=token)
