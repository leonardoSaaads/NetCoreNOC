"""The live statistics payload, assembled **once** for both surfaces that serve it (F115).

`GET /api/stats` and the `/api/events` stream carry the same object, and until v0.16.7 they built
it twice — two blocks, in two modules, that happened to agree. They stopped agreeing the moment
this release added a key: the census went onto `/api/stats`, the console reads its live figures
from the **stream**, and the Overview's new band rendered *"— critical of an unread count"* over an
appliance whose census was correct on the other route. Found in a browser, which is the only place
it was visible: the behaviour record lists `/api/events` as `NOT_DRIVEN` — a stream has no single
response to hash — so nothing in this repository compares the two.

The duplication is the defect, not the missing key, so the repair is one function rather than a
second copy of the addition. What the two surfaces genuinely differ about stays visible at the call
site: `/api/stats` adds the ingest-gap members, and the stream does not carry them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from netcorenoc.crosscutting.shaping import Scope

if TYPE_CHECKING:
    from netcorenoc.main import Engine
    from netcorenoc.store import Store


async def live_stats(
    store: Store,
    engine: Engine,
    scope: Scope,
    all_warnings: Callable[[], list[str]],
    extra_stats: Callable[[], dict[str, Any]] | None,
) -> dict[str, Any]:
    """Every counter both surfaces publish, computed over the principal's visible set.

    **The caller must hold `store.lock`.** Both call sites already did, around a wider block than
    this — `/api/events` also reads the graph and the situation list under the same lock — so
    taking it here would either deadlock or split one consistent read into two.

    Every enumerating counter is scoped, including the severity census (v0.16.7): a rising
    `unplaced` with nothing visible to explain it is the volume oracle F32 named, one key along.
    """
    out: dict[str, Any] = dict(
        await store.stats()
        if scope.unrestricted
        else await store.scoped_stats(scope.ne_ids, scope.ips)
    )
    out["severity"] = await store.severity_census(None if scope.unrestricted else scope.ne_ids)
    out["latency_p95_s"] = round(engine.latency_p95(), 4)
    out["queue_depth"] = engine.queue.qsize()
    out["warnings"] = all_warnings()
    if extra_stats is not None:
        out.update(extra_stats())
    return out
