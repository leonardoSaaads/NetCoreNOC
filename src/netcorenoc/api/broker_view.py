"""One broker's screens: a read narrowed to that network's elements (ADR #496).

A broker's map, Timeline, Entities and figures show its own network alone. That is a **view, not a
permission**: the perimeter decides what a principal may see (`Perimeter.scope_for`, the one
decision site, and the ``broker:<id>`` selector is how a policy limits a person to brokers), and
this narrows that answer to one broker's elements through `shaping.within` — so it can only hide.
The read paths then apply it through the predicate they already apply to a person's visibility,
which is why no read path needed a second filter of its own.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from netcorenoc.crosscutting import auth, shaping

if TYPE_CHECKING:
    from netcorenoc.store import Store

__all__ = ["BrokerScope", "broker_scope"]

#: ``await broker_scope(principal, broker_id)`` — the principal's scope, or one broker's part of it.
BrokerScope = Callable[[auth.Principal, int | None], Awaitable[shaping.Scope]]


def broker_scope(
    store: Store, scope_for: Callable[[auth.Principal], Awaitable[shaping.Scope]]
) -> BrokerScope:
    """Bind the resolver the read routes call when a screen names a broker."""

    async def resolve(principal: auth.Principal, broker_id: int | None) -> shaping.Scope:
        scope = await scope_for(principal)
        if broker_id is None:
            return scope
        async with store.reading():
            nes = await store.broker_nes(broker_id)
        return shaping.within(scope, nes)

    return resolve
