"""NOC → Cases → Feed → **Send email**, Reply and Reply all (ADR #500).

A customer's Email tab keeps the server side — which credentials send and read, and as whom —
and every message is written on the case it is about:

* ``POST …/email`` — send one: To, Cc and Bcc, a subject (the case's tag kept), and a body that is
  either the feed editor's **document** (`engine/noc/richtext.py`; never HTML) or plain ``text``
  (an agent's). With ``reply_to`` (a message on this case) it is threaded under that message and,
  when no ``to`` is given, goes to whom ``reply_all`` says — the sender, or everyone on it but us.
  The message is kept whole and shown on the feed; who sent it is audited.
* ``GET …/email/draft`` — what the editor opens with: for a reply, its recipients, ``Re:`` once and
  the original quoted; for the customer update (``update``), its text and whom the case's newest
  message went to; and the addresses this case's emails usually go to.

`cases.work` for both, on a case in the caller's scope, for a customer the caller serves. Blind
copies are the NOC's: the feed shows them to who works cases only.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from netcorenoc.api.case_view import actor_of
from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.crosscutting import auth, mail, shaping
from netcorenoc.engine.noc import custmail, feed, mail_loop, richtext

NO_SUCH = "no such case"
#: The most addresses one message may carry, To, Cc and Bcc together.
MAX_ADDRESSES = 50


class EmailIn(BaseModel):
    """One email written on a case. ``doc`` or ``text``; ``reply_to`` a message of this case."""

    model_config = ConfigDict(extra="forbid")
    to: list[str] = Field(default_factory=list, max_length=MAX_ADDRESSES)
    cc: list[str] = Field(default_factory=list, max_length=MAX_ADDRESSES)
    bcc: list[str] = Field(default_factory=list, max_length=MAX_ADDRESSES)
    subject: str = Field(default="", max_length=180)
    doc: list[dict[str, Any]] | None = Field(default=None, max_length=richtext.MAX_BLOCKS)
    text: str | None = Field(default=None, max_length=richtext.MAX_CHARS)
    reply_to: int | None = Field(default=None, ge=1)
    reply_all: bool = False
    customer_id: int | None = Field(default=None, ge=1)


def _addresses(values: list[str], field: str) -> list[str]:
    out: list[str] = []
    for value in values:
        address = value.strip().strip("<>")
        if not mail.valid_address(address):
            raise HTTPException(status_code=400, detail=f"{field}: {value!r} is not an address")
        if address.lower() not in map(str.lower, out):
            out.append(address)
    return out


def mail_view(row: dict[str, Any], *, works: bool) -> dict[str, Any]:
    """A message as the feed shows it; Bcc to who works cases only."""
    try:
        doc = json.loads(row["body_doc"]) if row.get("body_doc") else None
    except ValueError:
        doc = None
    return {
        "id": row["id"],
        "direction": row["direction"],
        "outcome": row["outcome"],
        "from": row["sender"],
        "from_name": row.get("sender_name") or "",
        "to": row["recipients"],
        "cc": row.get("cc") or "",
        "bcc": (row.get("bcc") or "") if works else "",
        "subject": row["subject"],
        "doc": doc,
        "text": row.get("body_text") or "",
        "detail": row["detail"],
        "at": row["at"],
        "sent_by": row.get("sent_by") or "",
    }


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the case email routes on `app`."""
    store, security, scope_for = ctx.store, ctx.security, ctx.scope_for
    audit_row = ctx.perimeter.audit_row
    route = DeclaredRoutes(app)

    async def worked_case(sid: int, scope: shaping.Scope) -> dict[str, Any]:
        """The case, or the 404 an unknown case and one outside the caller's scope answer alike."""
        async with store.reading():
            case = await store.noc_case(sid)
        if case is None or not await ctx.perimeter.situation_in_scope(sid, scope):
            raise HTTPException(status_code=404, detail=NO_SUCH)
        return case

    async def original(sid: int, mail_id: int | None) -> dict[str, Any] | None:
        if mail_id is None:
            return None
        async with store.reading():
            row = await store.noc_mail(mail_id)
        if row is None or row["situation_id"] != sid:
            raise HTTPException(status_code=400, detail="reply_to: not a message of this case")
        return row

    async def customer_of(
        sid: int, scope: shaping.Scope, wanted: int | None, answered: dict[str, Any] | None
    ) -> tuple[int, str]:
        """The customer an email is sent as: the message's it answers, else the case's own."""
        async with store.reading():
            customers = [
                (int(o["id"]), str(o["name"]))
                for o in await store.noc_case_organizations(sid)
                if not o["is_default"] and scope.allows_org(int(o["id"]))
            ]
        if answered is not None:
            wanted = int(answered["organization_id"])
        found = [c for c in customers if wanted is None or c[0] == wanted]
        if not found:
            raise HTTPException(status_code=400, detail="this case has no customer to email")
        if len(found) > 1:
            raise HTTPException(
                status_code=400, detail="this case has several customers: name one (customer_id)"
            )
        return found[0]

    async def ours(organization_id: int) -> list[str]:
        async with store.reading():
            cfg = await store.noc_customer_mail(organization_id)
            fallback = mail.from_document(await store.get_meta(mail.META_KEY))
        return [a for a in (cfg["sender"] if cfg else "", fallback.sender) if a]

    @route.post("/api/noc/cases/{sid}/email", operation_id="noc_send_case_email")
    async def send_email(
        sid: int, body: EmailIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Email the customer from this case: To/Cc/Bcc, a subject (the case's tag is kept) and a
        body — ``doc`` from the editor, or plain ``text`` (blank lines part paragraphs, ``- ``
        lines make a list). With ``reply_to`` (a message id from the feed) it answers that message;
        leave ``to`` empty to reply to its sender, or everyone on it with ``reply_all``. The
        message and its replies appear on the case's feed; a customer's reply puts a pending case
        back in the queue."""
        scope = await scope_for(principal)
        case = await worked_case(sid, scope)
        answered = await original(sid, body.reply_to)
        organization_id, _name = await customer_of(sid, scope, body.customer_id, answered)
        to = _addresses(body.to, "to")
        cc = _addresses(body.cc, "cc")
        bcc = _addresses(body.bcc, "bcc")
        if answered is not None and not to:
            to, cc = custmail.reply_recipients(
                answered, await ours(organization_id), everyone=body.reply_all
            )
        if not to:
            raise HTTPException(status_code=400, detail="to: name at least one address")
        if len(to) + len(cc) + len(bcc) > MAX_ADDRESSES:
            raise HTTPException(status_code=400, detail=f"at most {MAX_ADDRESSES} addresses")
        try:
            if body.doc is not None:
                doc = richtext.validate(body.doc)
            else:
                doc = richtext.validate(richtext.from_text(body.text or ""))
        except richtext.RichTextError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        subject = body.subject.strip()
        refs: tuple[str, ...] = ()
        in_reply_to = ""
        if answered is not None:
            subject = subject or custmail.reply_subject(sid, str(answered["subject"]))
            in_reply_to = str(answered["message_id"])
            refs = tuple(str(answered.get("refs") or "").split())
        subject = custmail.tagged(sid, subject or str(case.get("title") or ""))
        draft = mail_loop.Draft(
            to=to, cc=cc, bcc=bcc, subject=subject, doc=doc, in_reply_to=in_reply_to, refs=refs
        )
        actor = actor_of(principal)
        result = await mail_loop.send_case_email(store, case, organization_id, draft, actor)
        async with ctx.write_txn():
            await audit_row(
                request,
                principal,
                "noc.case.email",
                "ok" if result["outcome"] == "sent" else "error",
                object_type="noc_case",
                object_id=str(sid),
                details={
                    "mail": result["mail_id"],
                    "to": len(to),
                    "cc": len(cc),
                    "bcc": len(bcc),
                    "reply_to": body.reply_to,
                    "outcome": result["outcome"],
                },
            )
        return {**result, "to": to, "cc": cc, "subject": subject}

    @route.get("/api/noc/cases/{sid}/email/draft", operation_id="noc_case_email_draft")
    async def email_draft(
        sid: int,
        reply_to: int | None = Query(default=None, ge=1),
        all_: bool = Query(default=False, alias="all"),
        update: bool = False,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """What an email from this case starts as: a reply's recipients, subject and quote
        (``reply_to``, ``all``), or the customer update to send (``update``) — and the addresses
        this case's messages usually go to (``suggest``) and the sender it goes out as."""
        scope = await scope_for(principal)
        case = await worked_case(sid, scope)
        answered = await original(sid, reply_to)
        async with store.reading():
            latest = answered or await store.noc_case_last_mail(sid)
            suggest = await store.noc_case_mail_addresses(sid)
        try:
            organization_id, name = await customer_of(sid, scope, None, latest)
        except HTTPException:
            return {
                "available": False,
                "reason": "this case has no customer to email",
                "to": [],
                "cc": [],
                "subject": "",
                "doc": [],
                "suggest": suggest,
            }
        sender = await ours(organization_id)
        to: list[str] = []
        cc: list[str] = []
        doc: list[dict[str, Any]] = []
        subject = custmail.tagged(sid, str(case.get("title") or ""))
        if latest is not None and (answered is not None or update):
            to, cc = custmail.reply_recipients(latest, sender, everyone=all_ or update)
            subject = custmail.reply_subject(sid, str(latest["subject"]))
        if update:
            text = feed.customer_update(case) if case.get("next_update_at") else ""
            doc = richtext.from_text(text or str(case.get("title") or ""))
        if answered is not None:
            who = answered["sender_name"] or answered["sender"]
            doc = [{"p": []}, richtext.quote(f"{who} wrote:", str(answered["body_text"]))]
        async with store.reading():
            contacts = await store.noc_customer_addresses(organization_id)
        return {
            "available": True,
            "customer": name,
            "from": sender[0] if sender else "",
            "to": to,
            "cc": cc,
            "subject": subject,
            "doc": doc,
            "reply_to": reply_to,
            "reply_all": all_,
            "suggest": suggest + sorted(a for a in contacts if a not in suggest),
        }
