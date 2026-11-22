"""The ingest process's ports for brokers, and their remote proxies' traps (ADR #494).

Split from `process.py` (its 400-line guard): :class:`IngestProcess` inherits this. Each broker with
a port of its own gets a socket, bound in the main process so a port in use is reported to the
admin on the broker that asked for it; when the set changes the ingest process is started again
with every socket, and the sockets stay open here meanwhile so the kernel buffers what arrives. A
broker's remote proxy's records reach the spool through the ingest process, its only writer, which
acknowledges them once they are durable.
"""

from __future__ import annotations

import asyncio
import base64
import socket
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

__all__ = ["BrokerPortsMixin"]


class BrokerPortsMixin:
    _host: str
    _ports: dict[int, socket.socket]
    port_errors: dict[int, str]
    _swapping: bool
    _request: int
    _pending: dict[int, asyncio.Future[bool]]
    _loop: asyncio.AbstractEventLoop | None
    _control: Any
    _proc: Any
    _closing: bool
    _bind: Callable[[str, int], Awaitable[socket.socket]]

    def _send(self, message: list[Any]) -> None:  # pragma: no cover - IngestProcess's
        raise NotImplementedError

    # -- ADR #494: brokers' own ports, and their remote proxies' traps --------------------------

    @property
    def ports(self) -> tuple[int, ...]:
        """The broker ports listened on now."""
        return tuple(sorted(self._ports))

    async def listen(self, ports: Iterable[int]) -> dict[int, str]:
        """Listen on exactly these broker ports besides the standard one; why any could not be.

        A port is bound in this process, so "address already in use" is reported to the admin on
        the broker that asked for it. When the set changes, the ingest process is started again
        with every socket; the sockets stay open here meanwhile, so the kernel buffers what arrives
        and the new process reads it — the same guarantee as a restart after a crash.
        """
        wanted = set(ports)
        errors: dict[int, str] = {}
        changed = False
        for port in sorted(set(self._ports) - wanted):
            self._ports.pop(port).close()
            changed = True
        for port in sorted(wanted - set(self._ports)):
            try:
                self._ports[port] = await self._bind(self._host, port)
                changed = True
            except OSError as exc:
                errors[port] = exc.strerror or type(exc).__name__
        self.port_errors = errors
        if changed and self._proc is not None and not self._closing:
            self._swapping = True
            self._send(["stop"])
        return errors

    async def append(self, payloads: list[bytes], timeout_s: float = 10.0) -> bool:
        """Spool a remote proxy's records (each ``records.for_broker``); True once **durable**.

        The ingest process is the spool's only writer, so the records go through it and it says
        when they are on disk. False when it refused them (no room) or did not answer in time:
        the caller tells the proxy to send them again, and the proxy still has them.
        """
        if self._loop is None or self._control is None:
            return False
        self._request += 1
        request = self._request
        future: asyncio.Future[bool] = self._loop.create_future()
        self._pending[request] = future
        self._send(["append", request, [base64.b64encode(p).decode("ascii") for p in payloads]])
        try:
            return await asyncio.wait_for(future, timeout_s)
        except TimeoutError:
            return False
        finally:
            self._pending.pop(request, None)

    def _acked(self, request: int, ok: bool) -> None:
        future = self._pending.get(request)
        if future is not None and not future.done():
            future.set_result(ok)
