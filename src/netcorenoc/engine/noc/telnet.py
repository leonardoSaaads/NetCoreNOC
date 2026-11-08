"""Telnet for the executor: one read-only command on equipment managed over Telnet (ADR #481).

Some equipment offers nothing else. The owner asked for *secure* Telnet, so the credential's
version says how the connection is made:

* **Telnet over TLS** (the default, port 992): the session runs inside TLS. Network equipment
  rarely carries a certificate a public authority signed, so the certificate is **pinned on first
  use**, as the SSH host key is (ADR #462): its SHA-256 is returned for the caller to record, and
  every later connection must present the same one — a different certificate is refused before the
  username is sent.
* **Telnet**: plain, for equipment that cannot do better. The password crosses the network in the
  clear; the console says so where the version is chosen.

The protocol is the smallest that logs in: every option the server proposes is refused except
echo and suppress-go-ahead, the login and password prompts are answered, the shell prompt is
awaited, the command (`commands.check`, read-only) is typed, a pager's *More* is answered with a
space, and the session is left. What comes back is bounded (`executor.MAX_OUTPUT`) and untrusted.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import ssl
import time
from collections.abc import Mapping
from typing import Any

from netcorenoc.engine.noc import commands
from netcorenoc.engine.noc.executor import MAX_OUTPUT, TIMEOUT_S, Outcome

__all__ = ["run"]

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
_ACCEPT = {1, 3}  # ECHO, SUPPRESS-GO-AHEAD: what a CLI needs to answer at all
_LOGIN = re.compile(rb"(login|user ?name|user)\s*:\s*$", re.IGNORECASE)
_PASSWORD = re.compile(rb"pass(word|code)?\s*:\s*$", re.IGNORECASE)
_PROMPT = re.compile(rb"[\w\-.()/@:\[\]~]{1,64}[>#$%\]]\s*$")
_MORE = re.compile(rb"-+\s*\(?more\b.{0,20}\)?\s*-+|--more--", re.IGNORECASE)
_REFUSED = re.compile(rb"(login incorrect|authentication failed|access denied|% bad)", re.I)


class _Session:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader, self.writer = reader, writer
        self.buffer = b""

    def _negotiate(self, data: bytes) -> bytes:
        """The text in ``data``, with each option the server proposed answered."""
        out = bytearray()
        i = 0
        while i < len(data):
            byte = data[i]
            if byte != IAC:
                out.append(byte)
                i += 1
                continue
            if i + 1 >= len(data):
                break
            verb = data[i + 1]
            if verb == IAC:
                out.append(IAC)
                i += 2
            elif verb in (DO, DONT, WILL, WONT) and i + 2 < len(data):
                option = data[i + 2]
                if verb == DO:
                    self.writer.write(bytes([IAC, WONT, option]))
                elif verb == WILL:
                    answer = DO if option in _ACCEPT else DONT
                    self.writer.write(bytes([IAC, answer, option]))
                i += 3
            elif verb == SB:
                end = data.find(bytes([IAC, SE]), i)
                i = len(data) if end < 0 else end + 2
            else:
                i += 2
        return bytes(out)

    async def until(self, *patterns: re.Pattern[bytes], deadline: float) -> tuple[int, bytes]:
        """Read until the tail of what arrived matches one of ``patterns``: its index, the text."""
        while True:
            tail = self.buffer[-200:].rstrip(b"\x00")
            for index, pattern in enumerate(patterns):
                if pattern.search(tail):
                    text, self.buffer = self.buffer, b""
                    return index, text
            if _MORE.search(tail):
                self.buffer = _MORE.sub(b"", self.buffer)
                self.writer.write(b" ")
            if len(self.buffer) > MAX_OUTPUT * 2:
                text, self.buffer = self.buffer, b""
                return -1, text
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError
            chunk = await asyncio.wait_for(self.reader.read(4096), timeout=left)
            if not chunk:
                text, self.buffer = self.buffer, b""
                return -2, text
            self.buffer += self._negotiate(chunk)
            await self.writer.drain()

    def send(self, line: str) -> None:
        self.writer.write(line.encode("utf-8", "replace").replace(bytes([IAC]), bytes([IAC, IAC])))
        self.writer.write(b"\r\n")


def _tls() -> ssl.SSLContext:
    """TLS whose peer is checked by pinning, not by an authority (module docstring)."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # nosec B501 - the certificate is pinned on first use
    return context


async def run(
    row: Mapping[str, Any],
    fields: Mapping[str, str],
    command: str,
    *,
    pinned: str | None,
    timeout: float = TIMEOUT_S,
) -> Outcome:
    """Run one read-only command over Telnet (over TLS unless the credential says plain)."""
    start = time.monotonic()
    line = commands.check(command, str(row["access_level"]))
    secure = str(row.get("protocol_version") or "") != "Telnet"
    deadline = start + timeout

    def ms() -> float:
        return round((time.monotonic() - start) * 1000.0, 1)

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                str(row["host"]),
                int(row["port"]),
                ssl=_tls() if secure else None,
                server_hostname=str(row["host"]) if secure else None,
            ),
            timeout=timeout,
        )
    except TimeoutError:
        return Outcome(False, "telnet", "", f"no answer within {timeout:g} s", ms())
    except (OSError, ssl.SSLError) as exc:
        return Outcome(False, "telnet", "", f"failed: {type(exc).__name__}", ms())
    seen = None
    try:
        if secure:
            der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True) or b""
            fingerprint = "sha256:" + hashlib.sha256(der).hexdigest()
            if pinned and pinned != fingerprint:
                return Outcome(
                    False, "telnet", "", "refused: the certificate is not the one pinned", ms()
                )
            seen = None if pinned else fingerprint
        session = _Session(reader, writer)
        found, _ = await session.until(_LOGIN, _PASSWORD, _PROMPT, deadline=deadline)
        if found == 0:
            session.send(fields.get("username") or "")
            found, _ = await session.until(_PASSWORD, _PROMPT, deadline=deadline)
            found = 1 if found == 0 else 2
        if found == 1:
            session.send(fields.get("password") or "")
            found, text = await session.until(_PROMPT, _LOGIN, _PASSWORD, deadline=deadline)
            if found != 0 or _REFUSED.search(text):
                return Outcome(
                    False, "telnet", "", "refused: the credential was not accepted", ms()
                )
        elif found != 2:
            return Outcome(False, "telnet", "", "no login prompt from the device", ms())
        session.send(line)
        found, text = await session.until(_PROMPT, deadline=deadline)
        session.send("exit")
    except TimeoutError:
        return Outcome(False, "telnet", "", f"no answer within {timeout:g} s", ms(), host_key=seen)
    except (OSError, ssl.SSLError) as exc:
        return Outcome(False, "telnet", "", f"failed: {type(exc).__name__}", ms(), host_key=seen)
    finally:
        writer.close()
    body = text.decode("utf-8", "replace").replace("\r\n", "\n")
    lines = body.split("\n")
    if lines and line in lines[0]:
        lines = lines[1:]  # the device's echo of the command
    if found == 0 and lines:
        lines = lines[:-1]  # the prompt it returned to
    output = "\n".join(lines).strip("\n")
    data = output.encode("utf-8", "replace")
    cut = len(data) > MAX_OUTPUT
    if cut:
        output = data[:MAX_OUTPUT].decode("utf-8", "ignore")
    return Outcome(True, "telnet", output, "ran", ms(), truncated=cut, host_key=seen)
