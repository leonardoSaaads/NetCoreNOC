"""Personal data and secrets, sealed at rest (ADR #453).

What a copy of the database must not hand over, and where it lives:

* a person's **recovery address** (`app_user.email`, with `email_index`, a keyed digest, for the
  case-insensitive lookup a reset request needs), **display name** and **photo**
  (`user_avatar.image`);
* the **SMTP password** and the **SNMPv3 keys** (the `meta` documents ``config.email`` and
  ``config.snmp``), and the **community HMAC key** (``community_hmac_key``), which turns the stored
  community digests back into a dictionary attack if it leaks with them.

Each is sealed by `crosscutting/vault.py` on the way in and opened on the way out, here, so the
routes and the engine read the values they always read. A value an earlier release wrote in plain
text (or the SQLite import copied) still reads as itself until :meth:`SealingMixin.seal_plaintext`
— run at every start — seals it. Usernames stay plain: they are the sign-in identifier and the
audit log's actor, and a name chosen for a system is not the person's name.

**The key that sealed the database is recorded** (``vault.kid`` in `meta`, a key *identifier*, not
the key): a start with another key file stops with that said, instead of failing every password
and every sealed read one at a time.
"""

from __future__ import annotations

import logging
from typing import Any

from netcorenoc.crosscutting import vault
from netcorenoc.store.base import StoreBase

__all__ = [
    "AVATAR",
    "DISPLAY_NAME",
    "EMAIL",
    "SECRET_META",
    "SealingMixin",
    "email_index",
    "open_bytes",
    "open_person",
    "open_text",
    "seal_bytes",
    "seal_text",
]

EMAIL = "app_user.email"
DISPLAY_NAME = "app_user.display_name"
AVATAR = "user_avatar.image"
#: `meta` keys whose values are secrets.
SECRET_META = frozenset({"config.email", "config.snmp", "community_hmac_key"})
KID_META = "vault.kid"
#: The first audit id that had to be sealed (`crosscutting/audit.py`), itself sealed.
AUDIT_ANCHOR_META = "audit.sealed_from"

log = logging.getLogger("netcorenoc")


def seal_text(place: str, value: str | None) -> str | None:
    return None if value is None else vault.current().seal(place, value)


def open_text(place: str, value: Any) -> Any:
    """``value`` opened when it is sealed; a plain value an earlier release wrote, as it is."""
    return vault.current().open_text(place, value) if vault.is_sealed(value) else value


def seal_bytes(place: str, data: bytes) -> bytes:
    return vault.current().seal(place, data).encode("ascii")


def open_bytes(place: str, data: bytes) -> bytes:
    data = bytes(data)
    if data.startswith(vault.PREFIX.encode("ascii")):
        return vault.current().open(place, data.decode("ascii"))
    return data


def email_index(email: str | None) -> str | None:
    """The lookup digest of an address: trimmed and lower-cased, as a reset request matches it."""
    return None if email is None else vault.current().index(EMAIL, email.strip().lower())


def open_person(row: dict[str, Any]) -> dict[str, Any]:
    """A row that carries a person's sealed columns, with them opened (in place, and returned)."""
    for key, place in (
        ("email", EMAIL),
        ("display_name", DISPLAY_NAME),
        ("actor_display", DISPLAY_NAME),
    ):
        if key in row:
            row[key] = open_text(place, row[key])
    row.pop("email_index", None)
    return row


class SealingMixin(StoreBase):
    async def open_vault(self) -> int:
        """At start and after an import: refuse a key that is not this database's, then seal what
        is still plain. The caller holds the lock and commits; returns how many it sealed."""
        await self.check_vault()
        sealed = await self.seal_plaintext()
        if sealed:
            log.info("sealed %d personal value(s) and secret(s) left in plain text", sealed)
        return sealed

    async def check_vault(self) -> bool:
        """Refuse a key file that is not the one this database was sealed with.

        Records the key's identifier the first time; returns True when it did (the caller
        commits). The identifier says which key, and nothing about it.
        """
        kid = vault.current().kid.hex()
        cur = await self.conn.execute("SELECT value FROM meta WHERE key=?", (KID_META,))
        row = await cur.fetchone()
        if row is None:
            await self.conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (KID_META, kid))
            # From here on every audit row is sealed; the boundary is sealed too, so it cannot be
            # moved past a rewritten tail.
            cur = await self.conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM audit_log")
            first = str((await cur.fetchone())[0])  # type: ignore[index]
            anchor = vault.current().seal(f"meta:{AUDIT_ANCHOR_META}", first)
            await self.conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)", (AUDIT_ANCHOR_META, anchor)
            )
            return True
        if str(row[0]) != kid:
            raise vault.VaultError(
                f"this database was sealed with secret key {row[0]}, and the key file holds key "
                f"{kid}: restore the key file the appliance was using (NETCORENOC_SECRET_KEY_FILE, "
                "or secret.key in NETCORENOC_DATA_DIR). Without it the sealed values and every "
                "password are unreadable — see docs/troubleshoot.md, 'The secret key'."
            )
        return False

    async def audit_sealed_from(self) -> int | None:
        """The first audit id that must carry a seal, or None for a database never opened with a
        secret key. Raises :class:`vault.VaultError` when the anchor was removed or altered."""
        cur = await self.conn.execute(
            "SELECT key, value FROM meta WHERE key IN (?, ?)", (KID_META, AUDIT_ANCHOR_META)
        )
        found = {str(r[0]): str(r[1]) for r in await cur.fetchall()}
        if AUDIT_ANCHOR_META not in found:
            if KID_META in found:
                raise vault.VaultError("missing, although this database was sealed — removed")
            return None
        return int(vault.current().open_text(f"meta:{AUDIT_ANCHOR_META}", found[AUDIT_ANCHOR_META]))

    async def seal_plaintext(self) -> int:
        """Seal every personal value and secret still in plain text. Idempotent; the caller holds
        the lock and commits. Returns how many values it sealed."""
        sealed = 0
        cur = await self.conn.execute(
            "SELECT id, email, display_name FROM app_user "
            "WHERE (email IS NOT NULL AND email NOT LIKE 'nc1:%') "
            "   OR (display_name IS NOT NULL AND display_name NOT LIKE 'nc1:%') "
            "   OR (email IS NOT NULL AND email_index IS NULL) ORDER BY id"
        )
        for row in await cur.fetchall():
            email = open_text(EMAIL, row["email"])
            name = open_text(DISPLAY_NAME, row["display_name"])
            await self.conn.execute(
                "UPDATE app_user SET email=?, email_index=?, display_name=? WHERE id=?",
                (
                    seal_text(EMAIL, email),
                    email_index(email),
                    seal_text(DISPLAY_NAME, name),
                    row["id"],
                ),
            )
            sealed += (email is not None) + (name is not None)
        cur = await self.conn.execute("SELECT user_id, image FROM user_avatar ORDER BY user_id")
        for row in await cur.fetchall():
            if not bytes(row["image"]).startswith(vault.PREFIX.encode("ascii")):
                await self.conn.execute(
                    "UPDATE user_avatar SET image=? WHERE user_id=?",
                    (seal_bytes(AVATAR, bytes(row["image"])), row["user_id"]),
                )
                sealed += 1
        for key in sorted(SECRET_META):
            cur = await self.conn.execute("SELECT value FROM meta WHERE key=?", (key,))
            meta = await cur.fetchone()
            if meta is not None and not vault.is_sealed(meta[0]):
                await self.conn.execute(
                    "UPDATE meta SET value=? WHERE key=?",
                    (vault.current().seal(f"meta:{key}", str(meta[0])), key),
                )
                sealed += 1
        return sealed
