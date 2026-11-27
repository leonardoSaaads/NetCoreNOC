"""Incoming email: a customer's mailbox, read for replies to its customer updates (ADR #481).

The standard library's `imaplib` and `ssl` do the work, so this adds no dependency. Reading is
blocking I/O and is only ever called through `asyncio.to_thread` — never on the event loop, never
on the trap path.

What a read does, and why each limit is there:

* **TLS only** — IMAPS (implicit TLS) or STARTTLS, the certificate verified. A mailbox password
  crosses the network to log in, and the messages are the customer's; there is no plain mode.
* **Unread messages in INBOX, at most ``limit`` per read**, oldest first: a mailbox with a backlog
  is drained a little each pass rather than in one long hold.
* **At most :data:`MAX_BYTES` of each message**, fetched with ``BODY.PEEK[]`` so a read that fails
  half-way leaves the message unread for the next pass; it is marked ``\\Seen`` only once its bytes
  are in hand. A reply's text is near the top; an attachment is not read.
"""

from __future__ import annotations

import imaplib
import ssl
from contextlib import suppress
from dataclasses import dataclass

from netcorenoc.crosscutting import mailauth

__all__ = ["MAX_BYTES", "InboxError", "Mailbox", "check", "fetch_unseen"]

#: The most of one message read: enough for a reply and its headers, never an attachment.
MAX_BYTES = 256 * 1024
TIMEOUT_S = 20.0
SECURITY_MODES = ("tls", "starttls")


class InboxError(Exception):
    """A read that failed, in a sentence an admin can act on — never a stack trace or a password."""


@dataclass(frozen=True)
class Mailbox:
    host: str
    port: int
    security: str  # "tls" (IMAPS) or "starttls"
    username: str
    password: str
    timeout_s: float = TIMEOUT_S
    #: ADR #510: a mailbox signed in with Microsoft — its registration and refresh token.
    oauth_client_id: str = ""
    oauth_tenant: str = "consumers"
    oauth_refresh: str = ""


def _connect(box: Mailbox) -> imaplib.IMAP4:
    context = ssl.create_default_context()
    if box.security == "tls":
        return imaplib.IMAP4_SSL(box.host, box.port, ssl_context=context, timeout=box.timeout_s)
    if box.security == "starttls":
        client = imaplib.IMAP4(box.host, box.port, timeout=box.timeout_s)
        client.starttls(ssl_context=context)
        return client
    raise InboxError(f"security: one of {', '.join(SECURITY_MODES)}")


def _login(client: imaplib.IMAP4, box: Mailbox) -> None:
    """A password, or Microsoft's sign-in as XOAUTH2 (ADR #510)."""
    try:
        if box.oauth_refresh:
            token = mailauth.access_token(box.oauth_client_id, box.oauth_tenant, box.oauth_refresh)
            raw = f"user={box.username}\x01auth=Bearer {token}\x01\x01".encode()
            client.authenticate("XOAUTH2", lambda _challenge: raw)
        else:
            client.login(box.username, box.password)
    except mailauth.AuthError as exc:
        raise InboxError(f"Microsoft sign-in: {exc}") from exc
    except imaplib.IMAP4.error as exc:
        said = str(exc)
        if "basic" in said.lower() or "AUTHENTICATE failed" in said:
            raise InboxError(
                "the server refused the login. Outlook.com and Hotmail accept only Microsoft's "
                "sign-in now: choose Outlook.com and Sign in with Microsoft."
            ) from exc
        raise InboxError(
            "the server refused the username or password. Gmail, Microsoft 365 and Yahoo "
            "need an app password rather than the account's own."
        ) from exc


def check(box: Mailbox) -> None:
    """Sign in and open INBOX, reading nothing — the wizard's test (ADR #510). Blocking."""
    client = _open(box)
    try:
        _login(client, box)
        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            raise InboxError("the mailbox has no INBOX this account may open")
    except InboxError:
        raise
    except (OSError, imaplib.IMAP4.error) as exc:
        raise InboxError(f"reading {box.host}:{box.port} failed: {exc}") from exc
    finally:
        with suppress(Exception):
            client.logout()


def _open(box: Mailbox) -> imaplib.IMAP4:
    try:
        return _connect(box)
    except InboxError:
        raise
    except ssl.SSLError as exc:
        raise InboxError(
            f"the TLS handshake with {box.host}:{box.port} failed ({exc.reason or exc}). 993 is "
            "IMAPS; 143 is STARTTLS."
        ) from exc
    except TimeoutError as exc:
        raise InboxError(f"no answer from {box.host}:{box.port} within the timeout") from exc
    except (OSError, imaplib.IMAP4.error) as exc:
        raise InboxError(f"could not reach {box.host}:{box.port}: {exc}") from exc


def fetch_unseen(box: Mailbox, *, limit: int = 20) -> list[bytes]:
    """The unread messages of INBOX, oldest first, each marked read once fetched. **Blocking**:
    call through `asyncio.to_thread`. Raises InboxError."""
    try:
        client = _connect(box)
    except InboxError:
        raise
    except ssl.SSLError as exc:
        raise InboxError(
            f"the TLS handshake with {box.host}:{box.port} failed ({exc.reason or exc}). 993 is "
            "IMAPS; 143 is STARTTLS."
        ) from exc
    except TimeoutError as exc:
        raise InboxError(f"no answer from {box.host}:{box.port} within the timeout") from exc
    except (OSError, imaplib.IMAP4.error) as exc:
        raise InboxError(f"could not reach {box.host}:{box.port}: {exc}") from exc
    try:
        _login(client, box)
        status, _ = client.select("INBOX")
        if status != "OK":
            raise InboxError("the mailbox has no INBOX this account may open")
        status, found = client.uid("SEARCH", "UNSEEN")
        if status != "OK":
            raise InboxError("the server refused the search for unread messages")
        uids = [u.decode("ascii") for u in (found[0] or b"").split()[: max(0, limit)]]
        messages: list[bytes] = []
        for uid in uids:
            status, parts = client.uid("FETCH", uid, f"(BODY.PEEK[]<0.{MAX_BYTES}>)")
            raw = next((bytes(p[1]) for p in parts or () if isinstance(p, tuple)), None)
            if status != "OK" or raw is None:
                continue
            messages.append(raw)
            client.uid("STORE", uid, "+FLAGS", "(\\Seen)")
        return messages
    except InboxError:
        raise
    except TimeoutError as exc:
        raise InboxError(f"{box.host}:{box.port} stopped answering") from exc
    except (OSError, imaplib.IMAP4.error) as exc:
        raise InboxError(f"reading {box.host}:{box.port} failed: {exc}") from exc
    finally:
        with suppress(Exception):
            client.logout()
