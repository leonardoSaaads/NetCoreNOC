"""The brokers' own UDP ports, kept in step with the engine's routing (ADR #494).

The engine rebuilds its routing snapshot at the reload point (`lanes.Lanes.refresh`); this loop asks
the ingest process to listen on exactly the ports that snapshot names, and gives the engine the
ingest process's ``append``, through which a broker's remote proxy's records reach the spool. A port
that could not be opened (in use, refused) is tried again on every pass and reported in the
operator's warnings until it opens.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from netcorenoc.ingest.process import IngestProcess

if TYPE_CHECKING:  # pragma: no cover - type-only, no runtime edge (tests/repo/test_layers.py)
    from netcorenoc.engine.operate.engine import Engine

__all__ = ["EVERY_S", "loop"]

#: How often the ports are compared with the routing snapshot.
EVERY_S = 2.0


async def loop(engine: Engine, transport: Any) -> None:
    if not isinstance(transport, IngestProcess):
        return  # the in-process receiver has one socket and no spool: no broker ports, no proxies
    engine.proxy_spool = transport.append
    while True:
        wanted = engine.lanes.routing.ports()
        if wanted != transport.ports or transport.port_errors:
            await transport.listen(wanted)
            engine.broker_port_errors = dict(transport.port_errors)  # what a broker's card says
        await asyncio.sleep(EVERY_S)
