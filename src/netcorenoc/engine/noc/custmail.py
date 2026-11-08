"""A customer's email: the customer update written to it, and its replies read back (ADR #481).

Pure: no network, no store. `mail_loop.py` sends and reads; this decides what a message says, how a
reply finds its case, and what of a reply reaches the case's feed.

**Sending.** The update goes to the customer's recipients (and copies), from its sender, with the
case's tag in the subject — ``[NetCoreNOC #123] Fibre span down …`` — and a Message-ID the appliance
keeps, so a reply can be matched to the case two ways: by ``In-Reply-To``/``References`` naming a
message it sent, or by the tag in the subject when a mail client dropped the headers.

**Reading.** A reply is evidence of what the customer said, and untrusted text: only its plain text
is kept (an HTML-only message is reduced to text), the quoted history below it is cut, and it is
bounded. Whether it is posted at all is the caller's: the sender must be an address the customer
registered, and an automatic reply (an out-of-office) is never posted.
"""

from __future__ import annotations

import hashlib
import html
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from typing import Any

from netcorenoc.crosscutting import inbox, mail
from netcorenoc.engine.noc import feed

__all__ = [
    "MAX_REPLY",
    "Reply",
    "addresses",
    "compose_update",
    "mailbox",
    "match",
    "parse",
    "post_body",
    "smtp_config",
    "strip_quoted",
    "subject_for",
]

#: The most of a reply that reaches the feed (a post holds 8 000 characters).
MAX_REPLY = 6000
_TAG = re.compile(r"\[NetCoreNOC #(\d{1,12})\]")
_REF = re.compile(r"<[^<>\s]{1,900}>")
#: Where a mail client starts quoting what it answers — English, Portuguese and Spanish clients.
_QUOTE_HEAD = re.compile(
    r"^(On .{1,200} wrote:|Em .{1,200} escreveu:|El .{1,200} escribió:|"
    r"-{2,}\s*(Original Message|Mensagem original|Mensaje original)\s*-{2,}|"
    r"(From|De):\s.{1,300})\s*$",
    re.IGNORECASE,
)
_SECURITY = {"STARTTLS": "starttls", "SMTPS": "tls", "plain": "none"}
_IMAP_SECURITY = {"IMAPS": "tls", "STARTTLS": "starttls"}


def addresses(text: str) -> list[str]:
    """The valid addresses in a list typed by a person (commas, semicolons or spaces), in order,
    each once. An invalid one is dropped here and refused by the route that stores the list."""
    out: list[str] = []
    for part in re.split(r"[,;\s]+", text or ""):
        address = part.strip().strip("<>")
        if address and mail.valid_address(address) and address.lower() not in map(str.lower, out):
            out.append(address)
    return out


def subject_for(sid: int, title: str) -> str:
    return f"[NetCoreNOC #{sid}] {(title or 'Case update').strip()}"[:200]


def compose_update(
    case: Mapping[str, Any],
    *,
    sender: str,
    sender_name: str,
    to: Iterable[str],
    cc: Iterable[str] = (),
) -> EmailMessage:
    """The customer update as an email. Headers go through `email`'s policy, which refuses
    injection; the Message-ID is the appliance's own, kept to match replies."""
    sid = int(case["situation_id"])
    message = EmailMessage()
    message["From"] = formataddr((sender_name or "NetCoreNOC", sender))
    message["To"] = ", ".join(to)
    copies = list(cc)
    if copies:
        message["Cc"] = ", ".join(copies)
    message["Subject"] = subject_for(sid, str(case.get("title") or ""))
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1])
    message["X-NetCoreNOC-Case"] = str(sid)
    message.set_content(
        f"{feed.customer_update(case)}\n\n"
        f"-- \nReply to this email to answer the NOC: your reply is added to case #{sid}. "
        f"Please keep [NetCoreNOC #{sid}] in the subject.\n"
    )
    return message


@dataclass(frozen=True)
class Reply:
    message_id: str
    sender: str
    subject: str
    refs: tuple[str, ...]
    text: str
    automatic: bool


def _text_of(message: Any) -> str:
    body = message.get_body(preferencelist=("plain", "html"))
    if body is None:
        return ""
    try:
        content = str(body.get_content())
    except (LookupError, ValueError):
        return ""
    if body.get_content_type() == "text/html":
        content = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", content)
        content = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", content)
        content = html.unescape(re.sub(r"<[^>]{0,2000}>", " ", content))
    return content


def parse(raw: bytes) -> Reply:
    """A message as the NOC reads it. Never raises on a malformed message: what cannot be read
    is empty, and a message with no Message-ID gets one derived from its bytes."""
    message = message_from_bytes(raw, policy=policy.default)
    found = _REF.findall(str(message.get("Message-ID", "") or ""))
    message_id = found[0] if found else f"<{hashlib.sha256(raw).hexdigest()}@netcorenoc.invalid>"
    refs = _REF.findall(
        f"{message.get('In-Reply-To', '') or ''} {message.get('References', '') or ''}"
    )
    auto = str(message.get("Auto-Submitted", "no") or "no").strip().lower() != "no"
    auto = auto or bool(message.get("X-Autoreply") or message.get("X-Autorespond"))
    auto = auto or str(message.get("Precedence", "")).strip().lower() in ("auto_reply", "bulk")
    return Reply(
        message_id=message_id,
        sender=parseaddr(str(message.get("From", "") or ""))[1].strip().lower(),
        subject=str(message.get("Subject", "") or "")[:998],
        refs=tuple(refs[-20:]),
        text=strip_quoted(_text_of(message))[:MAX_REPLY],
        automatic=auto,
    )


def strip_quoted(text: str) -> str:
    """The reply without the history it quotes: lines from the first quote header on, and every
    line that starts with ``>``, are cut; blank runs collapse."""
    kept: list[str] = []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        if _QUOTE_HEAD.match(line.strip()):
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line.rstrip())
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return out


def match(reply: Reply, sent_case: int | None) -> int | None:
    """The case a reply answers: the one whose message it references (``sent_case``, looked up by
    the caller), else the tag in its subject."""
    if sent_case is not None:
        return sent_case
    found = _TAG.search(reply.subject)
    return int(found.group(1)) if found else None


def post_body(reply: Reply) -> str:
    """What the case's feed shows of a reply."""
    head = f"Email from the customer ({reply.sender}) — {reply.subject.strip() or '(no subject)'}"
    return f"{head}\n\n{reply.text or '(the message had no readable text)'}"[:7900]


def smtp_config(
    secret: Mapping[str, Any], fields: Mapping[str, str], *, sender: str, sender_name: str
) -> mail.MailConfig:
    """A customer's SMTP credential as the sender `mail.send` takes; its password is passed to
    the send itself, never kept in the configuration."""
    return mail.MailConfig(
        enabled=True,
        host=str(secret["host"]),
        port=int(secret["port"]),
        security=_SECURITY.get(str(secret["protocol_version"]), "starttls"),
        username=str(fields.get("username") or ""),
        sender=sender or str(fields.get("username") or ""),
        sender_name=sender_name or "NetCoreNOC",
    )


def mailbox(secret: Mapping[str, Any], fields: Mapping[str, str]) -> inbox.Mailbox:
    """A customer's IMAP credential as the mailbox `inbox.fetch_unseen` reads."""
    return inbox.Mailbox(
        host=str(secret["host"]),
        port=int(secret["port"]),
        security=_IMAP_SECURITY.get(str(secret["protocol_version"]), "tls"),
        username=str(fields.get("username") or ""),
        password=str(fields.get("password") or ""),
    )
