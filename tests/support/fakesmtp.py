"""A minimal SMTP server on a local port, so outgoing email is tested against a real conversation.

`smtplib` talks to it exactly as it talks to Gmail on port 587 without STARTTLS: EHLO, AUTH PLAIN,
MAIL FROM, RCPT TO, DATA, QUIT. Each delivered message is kept, parsed, for the test to read.
Plain text only — TLS is the standard library's, and is not what these tests are about.
"""

from __future__ import annotations

import base64
import socketserver
import threading
from email import message_from_bytes
from email.message import Message
from typing import Any


class FakeSmtp:
    """Start with `with FakeSmtp(user, password) as server:`; read `server.messages`."""

    def __init__(self, username: str = "", password: str = "") -> None:
        self.username, self.password = username, password
        self.messages: list[Message] = []
        self.logins: list[tuple[str, str]] = []
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                outer._session(self.rfile, self.wfile)

        self._server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> FakeSmtp:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _session(self, rfile: Any, wfile: Any) -> None:
        def say(line: str) -> None:
            wfile.write(line.encode() + b"\r\n")
            wfile.flush()

        say("220 fake.test ESMTP")
        while True:
            raw = rfile.readline()
            if not raw:
                return
            line = raw.decode().rstrip("\r\n")
            verb = line.split(" ", 1)[0].upper()
            if verb in ("EHLO", "HELO"):
                say("250-fake.test")
                say("250 AUTH PLAIN")
            elif verb == "AUTH":
                _user, name, secret = base64.b64decode(line.split()[2]).decode().split("\0")
                self.logins.append((name, secret))
                ok = (name, secret) == (self.username, self.password)
                say("235 2.7.0 accepted" if ok else "535 5.7.8 bad credentials")
            elif verb in ("MAIL", "RCPT", "RSET", "NOOP"):
                say("250 OK")
            elif verb == "DATA":
                say("354 go ahead")
                body = bytearray()
                while (chunk := rfile.readline()) not in (b".\r\n", b""):
                    body.extend(chunk[1:] if chunk.startswith(b"..") else chunk)
                self.messages.append(message_from_bytes(bytes(body)))
                say("250 OK queued")
            elif verb == "QUIT":
                say("221 bye")
                return
            else:
                say("502 not implemented")
