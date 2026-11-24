"""A customer's mailbox settings and every message sent to it or read from it (ADR #481).

Storage only. What a message says and how a reply finds its case is `engine/noc/custmail.py`; when
the mailbox is read is `engine/noc/mail_loop.py`; who may change the settings is
`api/routes/customer_mail.py`. The settings name two credentials of the customer's in the vault by
id; nothing here reads a sealed column.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from netcorenoc.store.base import StoreBase

_SETTINGS = (
    "m.organization_id, m.recipients, m.cc, m.sender, m.sender_name, m.smtp_secret_id, "
    "m.imap_secret_id, m.send_updates, m.read_replies, m.polled_at, m.poll_error, m.version, "
    "m.updated_at, m.updated_by"
)
#: The settings a person writes; the rest the appliance keeps.
EDITABLE = (
    "recipients",
    "cc",
    "sender",
    "sender_name",
    "smtp_secret_id",
    "imap_secret_id",
    "send_updates",
    "read_replies",
)


#: A message's own columns beyond its key, each with its bound (0: not text).
MAIL_FIELDS: dict[str, int] = {
    "subject": 998,
    "sender": 254,
    "sender_name": 200,
    "recipients": 4000,
    "cc": 4000,
    "bcc": 4000,
    "in_reply_to": 998,
    "refs": 8000,
    "body_text": 20000,
    "body_doc": 100000,
    "sent_by": 200,
    "post_id": 0,
    "detail": 500,
}
_MAIL = "id, organization_id, situation_id, direction, outcome, message_id, at, " + ", ".join(
    MAIL_FIELDS
)


class NocMailMixin(StoreBase):
    async def noc_customer_mail(self, organization_id: int) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            f"SELECT {_SETTINGS} FROM noc_customer_mail m "  # nosec B608 - a literal
            "WHERE m.organization_id = ?",
            (organization_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def noc_customer_mails_to_read(self) -> list[dict[str, Any]]:
        """The customers whose mailbox is read for replies: switched on, with an IMAP credential,
        and still served."""
        cur = await self.conn.execute(
            f"SELECT {_SETTINGS} FROM noc_customer_mail m "  # nosec B608 - a literal
            "JOIN organization o ON o.id = m.organization_id "
            "WHERE m.read_replies AND m.imap_secret_id IS NOT NULL AND o.ended_at IS NULL "
            "ORDER BY m.polled_at NULLS FIRST, m.organization_id"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def noc_set_customer_mail(
        self, organization_id: int, fields: Mapping[str, Any], now: float, actor: str
    ) -> None:
        """Create or replace the settings a person writes (:data:`EDITABLE`, every one given)."""
        values = [fields[k] for k in EDITABLE]
        names = ", ".join(EDITABLE)
        marks = ", ".join("?" for _ in EDITABLE)
        updates = ", ".join(f"{k} = EXCLUDED.{k}" for k in EDITABLE)
        await self.conn.execute(
            f"INSERT INTO noc_customer_mail (organization_id, {names}, updated_at, updated_by) "  # nosec B608
            f"VALUES (?, {marks}, ?, ?) ON CONFLICT (organization_id) DO UPDATE SET {updates}, "
            "version = noc_customer_mail.version + 1, updated_at = EXCLUDED.updated_at, "
            "updated_by = EXCLUDED.updated_by",
            (organization_id, *values, now, actor),
        )

    async def noc_mail_polled(self, organization_id: int, now: float, error: str) -> None:
        await self.conn.execute(
            "UPDATE noc_customer_mail SET polled_at = ?, poll_error = ? WHERE organization_id = ?",
            (now, error[:500], organization_id),
        )

    async def noc_customer_addresses(self, organization_id: int) -> set[str]:
        """Every address a reply may come from (lower-cased): the customer's old recipients and
        copies, each person in its workbook, and everyone the NOC emailed on its cases."""
        out: set[str] = set()
        mail = await self.noc_customer_mail(organization_id)
        if mail is not None:
            for text in (mail["recipients"], mail["cc"]):
                out.update(a.strip().lower() for a in str(text).replace(";", ",").split(","))
        cur = await self.conn.execute(
            "SELECT lower(email) FROM noc_contact WHERE organization_id = ? AND email <> ''",
            (organization_id,),
        )
        out.update(str(r[0]) for r in await cur.fetchall())
        # ADR #500: and everyone the NOC wrote to or copied on this customer's cases.
        cur = await self.conn.execute(
            "SELECT recipients, cc FROM noc_case_mail WHERE organization_id = ? "
            "AND direction = 'out' AND outcome = 'sent'",
            (organization_id,),
        )
        for row in await cur.fetchall():
            for text in (row["recipients"], row["cc"]):
                out.update(a.strip().lower() for a in str(text).replace(";", ",").split(","))
        out.discard("")
        return out

    # -- the messages --------------------------------------------------------------------------

    async def noc_mail_record(
        self,
        organization_id: int,
        situation_id: int | None,
        direction: str,
        outcome: str,
        message_id: str,
        now: float,
        **fields: Any,
    ) -> bool:
        """One message, once: False when this message (by its Message-ID) is already recorded."""
        found = await self.noc_mail_add(
            organization_id, situation_id, direction, outcome, message_id, now, **fields
        )
        return found is not None

    async def noc_mail_add(
        self,
        organization_id: int,
        situation_id: int | None,
        direction: str,
        outcome: str,
        message_id: str,
        now: float,
        **fields: Any,
    ) -> int | None:
        """Record one message and return its id; None when it (by Message-ID) is already kept.
        ``fields`` are the columns :data:`MAIL_FIELDS` names, each bounded to its column."""
        unknown = set(fields) - set(MAIL_FIELDS)
        if unknown:
            raise ValueError(f"not a message's column: {sorted(unknown)}")
        values = {k: fields.get(k, None if k == "post_id" else "") for k in MAIL_FIELDS}
        for key, size in MAIL_FIELDS.items():
            if size and values[key] is not None:
                values[key] = str(values[key])[:size]
        names = ", ".join(MAIL_FIELDS)
        marks = ", ".join("?" for _ in MAIL_FIELDS)
        cur = await self.conn.execute(
            "INSERT INTO noc_case_mail (organization_id, situation_id, direction, outcome, "  # nosec B608
            f"message_id, at, {names}) VALUES (?, ?, ?, ?, ?, ?, {marks}) "
            "ON CONFLICT (organization_id, direction, message_id) DO NOTHING RETURNING id",
            (
                organization_id,
                situation_id,
                direction,
                outcome,
                message_id[:998],
                now,
                *values.values(),
            ),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else None

    async def noc_mail_attach(self, mail_id: int, post_id: int) -> None:
        """The feed post that shows a message (a post is append-only; the message points at it)."""
        await self.conn.execute(
            "UPDATE noc_case_mail SET post_id = ? WHERE id = ?", (post_id, mail_id)
        )

    async def noc_mail(self, mail_id: int) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            f"SELECT {_MAIL} FROM noc_case_mail WHERE id = ?",  # nosec B608 - a literal
            (mail_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def noc_mails_for_posts(self, post_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
        """The message each of these feed posts shows, by post id."""
        wanted = sorted({int(p) for p in post_ids})
        if not wanted:
            return {}
        cur = await self.conn.execute(
            f"SELECT {_MAIL} FROM noc_case_mail WHERE post_id = ANY(?)",  # nosec B608
            (wanted,),
        )
        return {int(r["post_id"]): dict(r) for r in await cur.fetchall()}

    async def noc_case_last_mail(self, situation_id: int) -> dict[str, Any] | None:
        """The case's newest message the NOC sent or read: what an update is emailed in reply to."""
        cur = await self.conn.execute(
            f"SELECT {_MAIL} FROM noc_case_mail WHERE situation_id = ? "  # nosec B608
            "AND outcome IN ('sent', 'posted') ORDER BY at DESC, id DESC LIMIT 1",
            (situation_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def noc_case_mail_addresses(self, situation_id: int) -> list[str]:
        """Every address on this case's messages, newest first: whom an email is usually for."""
        cur = await self.conn.execute(
            "SELECT sender, recipients, cc FROM noc_case_mail WHERE situation_id = ? "
            "AND outcome IN ('sent', 'posted') ORDER BY at DESC, id DESC LIMIT 50",
            (situation_id,),
        )
        out: list[str] = []
        for row in await cur.fetchall():
            for text in (row["sender"], row["recipients"], row["cc"]):
                for part in str(text or "").replace(";", ",").split(","):
                    address = part.strip().lower()
                    if address and address not in out:
                        out.append(address)
        return out

    async def noc_mail_known(self, organization_id: int, message_id: str) -> bool:
        cur = await self.conn.execute(
            "SELECT 1 FROM noc_case_mail WHERE organization_id = ? AND direction = 'in' "
            "AND message_id = ?",
            (organization_id, message_id[:998]),
        )
        return await cur.fetchone() is not None

    async def noc_mail_case_for(self, organization_id: int, refs: Iterable[str]) -> int | None:
        """The case a reply answers, by the Message-IDs it references: the newest sent match."""
        wanted = [r[:998] for r in refs if r]
        if not wanted:
            return None
        cur = await self.conn.execute(
            "SELECT situation_id FROM noc_case_mail WHERE organization_id = ? "
            "AND direction = 'out' AND message_id = ANY(?) AND situation_id IS NOT NULL "
            "ORDER BY at DESC LIMIT 1",
            (organization_id, wanted),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else None

    async def noc_mail_log(
        self,
        *,
        organization_id: int | None = None,
        situation_id: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """The newest messages of one customer or one case."""
        where, args = ("organization_id = ?", organization_id)
        if situation_id is not None:
            where, args = ("situation_id = ?", situation_id)
        cur = await self.conn.execute(
            "SELECT id, organization_id, situation_id, direction, outcome, message_id, subject, "  # nosec B608
            f"sender, recipients, post_id, detail, at FROM noc_case_mail WHERE {where} "
            "ORDER BY at DESC, id DESC LIMIT ?",
            (args, min(max(limit, 1), 200)),
        )
        return [dict(r) for r in await cur.fetchall()]
