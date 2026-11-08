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
        """Every address the customer registered: the update's recipients and copies, and the
        email of each person in its workbook — whom a reply may come from (lower-cased)."""
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
        *,
        subject: str = "",
        sender: str = "",
        recipients: str = "",
        post_id: int | None = None,
        detail: str = "",
    ) -> bool:
        """One message, once: False when this message (by its Message-ID) is already recorded."""
        cur = await self.conn.execute(
            "INSERT INTO noc_case_mail (organization_id, situation_id, direction, outcome, "
            "message_id, subject, sender, recipients, post_id, detail, at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (organization_id, direction, message_id) DO NOTHING RETURNING id",
            (
                organization_id,
                situation_id,
                direction,
                outcome,
                message_id[:998],
                subject[:998],
                sender[:254],
                recipients[:4000],
                post_id,
                detail[:500],
                now,
            ),
        )
        return await cur.fetchone() is not None

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
