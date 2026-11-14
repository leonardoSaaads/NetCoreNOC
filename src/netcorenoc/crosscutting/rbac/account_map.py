"""The caller's own account: the capability each route requires and its scope posture.

Split from `route_map.py` at the 400-line guard when two-factor sign-in added two routes (ADR
#482), on the seam the table already had: everything here acts on the caller's own session or
account — signing out, who am I, the password, the photo and name, the authenticator — and names no
network element. `route_map.py` folds both tables into `ROUTE_PERMISSIONS` and `ROUTE_SCOPE`, which
stay the one authority; nothing reads these two names but it.

Every entry is `self.read`; every scope entry carries its reason on the line above it, because
`tests/api/test_declaration.py` reads them.
"""

from __future__ import annotations

from typing import Literal

ACCOUNT_ROUTE_PERMISSIONS: dict[tuple[str, str], str] = {
    ("POST", "/api/logout"): "self.read",
    ("GET", "/api/me"): "self.read",
    ("POST", "/api/password"): "self.read",
    # v0.25.0 (ADR #401, #402): your own name and photo are yours, like your password. Reading a
    # photo is `self.read`; whose photo is readable is the handler's call (`shaping.sees_people`).
    ("GET", "/api/avatars/{uid}"): "self.read",
    ("POST", "/api/me/avatar"): "self.read",
    ("DELETE", "/api/me/avatar"): "self.read",
    ("POST", "/api/me/profile"): "self.read",
    # ADR #482: your own authenticator — new recovery codes, or forgetting it for a new phone. Each
    # also asks for the password and a current code in its body.
    ("POST", "/api/me/recovery-codes"): "self.read",
    ("POST", "/api/me/two-factor/reset"): "self.read",
}

ACCOUNT_ROUTE_SCOPE: dict[tuple[str, str], Literal["scoped", "unscoped", "admin_only"]] = {
    # Acts on the caller's own session; references no network element.
    ("POST", "/api/logout"): "unscoped",
    # Reports the caller's own scope summary (scoped?, ne_count), so the body follows the policy.
    ("GET", "/api/me"): "scoped",
    # Acts on the caller's own password; references no network element.
    ("POST", "/api/password"): "unscoped",
    # A person's photo, and the caller's own name and photo: an account, never a network element.
    ("GET", "/api/avatars/{uid}"): "unscoped",
    # The caller's own photo: bytes about an account, never about a network element.
    ("POST", "/api/me/avatar"): "unscoped",
    # Removing the caller's own photo, for the same reason.
    ("DELETE", "/api/me/avatar"): "unscoped",
    # The caller's own display name: a label on an account, naming no network element.
    ("POST", "/api/me/profile"): "unscoped",
    # The caller's own recovery codes: an account's way back in, never a network element.
    ("POST", "/api/me/recovery-codes"): "unscoped",
    # Forgetting the caller's own authenticator: an account's sign-in, never a network element.
    ("POST", "/api/me/two-factor/reset"): "unscoped",
}
