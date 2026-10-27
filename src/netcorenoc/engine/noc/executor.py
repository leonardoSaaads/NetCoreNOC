"""The executor: what reaches a device on an agent's behalf, so the secret never leaves (ADR #462).

An agent holds a lease; the executor holds the credential. It is handed the credential's row and
its opened fields by its caller — the API, after checking the lease, or the playbook runner — and
it does exactly one of three things:

* **ssh** — one read-only command (`commands.check`) over SSH. ``exec`` mode runs it on an exec
  channel; ``shell`` mode, for CLIs that only answer an interactive session, types it into one and
  closes the session after. **The host key is pinned on first use** (the caller records what this
  returns) and every later connection must present the same key: a different one is refused and
  reported, and the credential is never offered to an impostor. asyncssh is the optional `noc`
  extra; without it SSH answers "unavailable" and the rest works.
* **snmp_get** — up to twenty OIDs, read with the credential (v1/v2c community or v3 USM). A GET
  changes nothing on a device by construction.
* **reach** — is the host there: an ICMP echo through the system's `ping` when it exists and may
  be used, otherwise a TCP connection to the credential's port. The method is in the answer.

**What comes back is bounded and untrusted**: at most 64 KiB, and the caller marks it as device
text — a model must read it as evidence, never as instructions (`docs/plans/noc-phase2.md`, G6).
Never on the ingest path, never holding `store.lock` (#454): the caller writes its records after.
"""

from __future__ import annotations

import asyncio
import ipaddress
import shutil
import socket
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from netcorenoc.engine.noc import commands

__all__ = ["MAX_OUTPUT", "TIMEOUT_S", "Outcome", "available", "reach", "snmp_get", "ssh"]

MAX_OUTPUT = 64 * 1024
TIMEOUT_S = 20.0
MAX_OIDS = 20


@dataclass(frozen=True)
class Outcome:
    """One act on a device. ``host_key`` is a key seen for the first time, for the caller to pin."""

    ok: bool
    action: str
    output: str
    detail: str
    duration_ms: float
    exit_status: int | None = None
    truncated: bool = False
    host_key: str | None = None
    values: tuple[tuple[str, str], ...] = ()


def available() -> bool:
    """Is the SSH client installed (the `noc` extra)?"""
    try:
        import asyncssh  # noqa: F401
    except ImportError:
        return False
    return True


def _bounded(text: str) -> tuple[str, bool]:
    data = text.encode("utf-8", "replace")
    if len(data) <= MAX_OUTPUT:
        return text, False
    return data[:MAX_OUTPUT].decode("utf-8", "ignore"), True


def _text(value: Any) -> str:
    """A channel's output as text, whatever the library handed back."""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return "" if value is None else str(value)


def _ms(start: float) -> float:
    return round((time.monotonic() - start) * 1000.0, 1)


async def ssh(
    row: Mapping[str, Any],
    fields: Mapping[str, str],
    command: str,
    *,
    pinned: str | None,
    mode: str = "exec",
    timeout: float = TIMEOUT_S,
) -> Outcome:
    """Run one read-only command over SSH with the credential in ``fields``."""
    start = time.monotonic()
    line = commands.check(command, str(row["access_level"]))
    try:
        import asyncssh
    except ImportError:
        return Outcome(False, "ssh", "", "unavailable: install the `noc` extra", _ms(start))
    known: Any = None
    if pinned:
        known = ([asyncssh.import_public_key(pinned)], [], [])
    keys = None
    if fields.get("private_key"):
        keys = [
            asyncssh.import_private_key(fields["private_key"], fields.get("passphrase") or None)
        ]
    try:
        async with asyncio.timeout(timeout):
            conn = await asyncssh.connect(
                str(row["host"]),
                int(row["port"]),
                username=fields.get("username") or None,
                password=fields.get("password") or None,
                client_keys=keys,
                known_hosts=known,
                agent_path=None,
                config=[],
            )
            async with conn:
                seen = None
                if not pinned:
                    server_key = conn.get_server_host_key()
                    if server_key is not None:
                        seen = server_key.export_public_key("openssh").decode().strip()
                if mode == "shell":
                    output, status = await _shell(conn, line)
                else:
                    result = await conn.run(line, check=False)
                    output = _text(result.stdout) + _text(result.stderr)
                    status = result.exit_status
    except asyncssh.HostKeyNotVerifiable:
        return Outcome(False, "ssh", "", "refused: the host key is not the one pinned", _ms(start))
    except asyncssh.PermissionDenied:
        return Outcome(False, "ssh", "", "refused: the credential was not accepted", _ms(start))
    except TimeoutError:
        return Outcome(False, "ssh", "", f"no answer within {timeout:g} s", _ms(start))
    except (OSError, asyncssh.Error) as exc:
        return Outcome(False, "ssh", "", f"failed: {type(exc).__name__}", _ms(start))
    text, cut = _bounded(str(output))
    return Outcome(
        status in (0, None),
        "ssh",
        text,
        "ran" if status in (0, None) else f"exit status {status}",
        _ms(start),
        exit_status=status,
        truncated=cut,
        host_key=seen,
    )


async def _shell(conn: Any, line: str) -> tuple[str, int | None]:
    """Type one command into an interactive session, then leave it."""
    process = await conn.create_process(term_type="vt100", term_size=(200, 50))
    process.stdin.write(f"{line}\nexit\n")
    chunks: list[str] = []
    size = 0
    while size <= MAX_OUTPUT:
        chunk = await process.stdout.read(4096)
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    process.close()
    return "".join(chunks), None


_AUTH = {
    "MD5": "usmHMACMD5AuthProtocol",
    "SHA": "usmHMACSHAAuthProtocol",
    "SHA-224": "usmHMAC128SHA224AuthProtocol",
    "SHA-256": "usmHMAC192SHA256AuthProtocol",
    "SHA-384": "usmHMAC256SHA384AuthProtocol",
    "SHA-512": "usmHMAC384SHA512AuthProtocol",
}
_PRIV = {
    "none": "usmNoPrivProtocol",
    "DES": "usmDESPrivProtocol",
    "3DES": "usm3DESEDEPrivProtocol",
    "AES-128": "usmAesCfb128Protocol",
    "AES-192": "usmAesCfb192Protocol",
    "AES-256": "usmAesCfb256Protocol",
}


def _oid(text: str) -> str:
    oid = text.strip().lstrip(".")
    parts = oid.split(".")
    if not 2 <= len(parts) <= 128 or not all(p.isdigit() and len(p) <= 10 for p in parts):
        raise commands.CommandError(f"oids: {text!r} is not a numeric OID")
    return oid


async def snmp_get(
    row: Mapping[str, Any],
    fields: Mapping[str, str],
    oids: Sequence[str],
    *,
    timeout: float = 5.0,
) -> Outcome:
    """Read ``oids`` with the credential: GET only, which changes nothing on the device."""
    start = time.monotonic()
    if not 1 <= len(oids) <= MAX_OIDS:
        raise commands.CommandError(f"oids: between 1 and {MAX_OIDS}")
    wanted = [_oid(o) for o in oids]
    from pysnmp.hlapi.v3arch import asyncio as hlapi

    if row["kind"] == "snmp_v3":
        auth: Any = hlapi.UsmUserData(
            fields["username"],
            authKey=fields.get("auth_key"),
            privKey=fields.get("priv_key") or None,
            authProtocol=getattr(hlapi, _AUTH[fields["auth_protocol"]]),
            privProtocol=getattr(hlapi, _PRIV[fields.get("priv_protocol") or "none"]),
        )
    else:
        model = 0 if row["protocol_version"] == "v1" else 1
        auth = hlapi.CommunityData(fields["community"], mpModel=model)
    try:
        target = await hlapi.UdpTransportTarget.create(
            (str(row["host"]), int(row["port"])), timeout=timeout, retries=1
        )
        error, status, _index, binds = await hlapi.get_cmd(
            hlapi.SnmpEngine(),
            auth,
            target,
            hlapi.ContextData(),
            *(hlapi.ObjectType(hlapi.ObjectIdentity(o)) for o in wanted),
        )
    except (OSError, ValueError) as exc:
        return Outcome(False, "snmp_get", "", f"failed: {type(exc).__name__}", _ms(start))
    if error:
        return Outcome(False, "snmp_get", "", f"no answer: {error}", _ms(start))
    if status:
        return Outcome(False, "snmp_get", "", f"error: {status.prettyPrint()}", _ms(start))
    values = tuple((str(name), value.prettyPrint()) for name, value in binds)
    text, cut = _bounded("\n".join(f"{name} = {value}" for name, value in values))
    return Outcome(True, "snmp_get", text, "read", _ms(start), truncated=cut, values=values)


async def reach(host: str, port: int, *, timeout: float = 3.0) -> Outcome:
    """Is the host there? ICMP through the system's `ping` if it runs, else a TCP connection."""
    start = time.monotonic()
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
        address = str(ipaddress.ip_address(infos[0][4][0]))
    except (OSError, ValueError, IndexError):
        return Outcome(False, "reach", "", "unresolvable", _ms(start))
    ping = shutil.which("ping")
    if ping:
        try:
            process = await asyncio.create_subprocess_exec(  # nosec B603 - a literal IP, no shell
                ping,
                "-c",
                "1",
                "-W",
                str(max(1, int(timeout))),
                address,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            async with asyncio.timeout(timeout + 1):
                out, _ = await process.communicate()
            if process.returncode in (0, 1):
                text, cut = _bounded(out.decode("utf-8", "replace"))
                answered = process.returncode == 0
                detail = "ICMP echo answered" if answered else "no ICMP echo"
                return Outcome(answered, "reach", text, detail, _ms(start), truncated=cut)
        except (OSError, TimeoutError):
            pass  # no permission to send ICMP here: fall back to TCP
    try:
        async with asyncio.timeout(timeout):
            _reader, writer = await asyncio.open_connection(address, port)
        writer.close()
        return Outcome(True, "reach", "", f"TCP {port} answered", _ms(start))
    except (OSError, TimeoutError):
        return Outcome(False, "reach", "", f"TCP {port} did not answer", _ms(start))
