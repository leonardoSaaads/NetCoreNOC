"""One lane per broker: its correlator, its learned state and its decider (ADR #495).

A broker is one monitored network (ADR #494). Until now the engine held one correlator, one
`Learner`, one `Precedence` and one decider, so provider A's co-failures moved provider B's
affinities and a champion chosen on A's labels grouped B's alarms. A **lane** is everything that
must be one network's own:

* the correlation window, recall, episodes and grouping (`Correlator`) — so a situation never
  spans two brokers;
* the learned class and element affinities (`Learner`'s two matrices) and the lead precedence;
* the decider — the mode an admin chose for this broker, the judge's champion on this broker's
  labels, the challengers' live shadow — loaded at the reload point by `ScorerLifecycleMixin`,
  which runs per lane.

What is not per broker is shared on purpose: the raise/clear pairs and state clears are the trap
catalogue's knowledge (every lane's `Learner` holds the Default lane's two objects), and the
latency of each league member is measured once.

**Decisions only, no I/O on the trap path.** The engine asks :meth:`Lanes.resolve` which broker a
trap belongs to — a lookup in an immutable routing snapshot — and :meth:`Lanes.of` for that lane.
Lanes are created, and the snapshot rebuilt, at the reload point (:meth:`Lanes.refresh`), under the
store lock and between batches, like the maintenance-window index.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from netcorenoc.engine.correlate.correlate import Correlator
from netcorenoc.engine.correlate.learn import Learner
from netcorenoc.engine.correlate.rootcause import Precedence
from netcorenoc.engine.evaluation.league_shadow import LeagueShadow
from netcorenoc.engine.model import league as league_model
from netcorenoc.engine.operate import routing
from netcorenoc.engine.operate.scorer_lifecycle import ScorerLifecycleMixin

if TYPE_CHECKING:  # pragma: no cover - type-only, no runtime edge (tests/repo/test_layers.py)
    from netcorenoc.ingest.events import TrapEvent
    from netcorenoc.store import Store

__all__ = ["Lane", "Lanes"]

log = logging.getLogger("netcorenoc")


class Lane(ScorerLifecycleMixin):
    """One broker's correlator, learned state and decider."""

    def __init__(self, broker_id: int, store: Store, latency_us: dict[str, float]) -> None:
        self.broker_id = broker_id
        self.store = store
        self.latency_us = latency_us  # shared: a member's cost does not depend on the network
        self.correlator = Correlator()
        self.learner = Learner()
        self.precedence = Precedence()
        self.league_shadow = LeagueShadow()
        self.decider_ref = "additive:default"
        self.champion: league_model.Member | None = None
        self.challengers: tuple[league_model.Member, ...] = ()
        self.league_members: league_model.League | None = None
        self.league_view: dict[str, Any] | None = None
        self.scorer_config_id: int | None = None
        self.scorer_model_version_id: int | None = None
        self.scorer_warnings: list[str] = []
        self._loaded_key: tuple[object, ...] | None = None

    async def restore(self, store: Store, *, shared: bool) -> None:
        """This broker's learned state as the store holds it (at start, or when the lane is new)."""
        await self.learner.load(store, self.broker_id, shared=shared)
        self.correlator.episodes.load(await store.load_episodes(self.broker_id))
        await self.precedence.load(store, self.broker_id)

    async def save(self, store: Store, now: float, *, shared: bool) -> None:
        await self.learner.save(store, now, self.broker_id, shared=shared)
        await self.precedence.save(store, now, self.broker_id)


class Lanes:
    """Every broker's lane, the routing snapshot, and the questions asked across them."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.latency_us: dict[str, float] = {}
        self.routing = routing.Routing()
        self.by_id: dict[int, Lane] = {}
        self.names: dict[int, str] = {routing.DEFAULT_BROKER: "Default"}
        self.default = self._new(routing.DEFAULT_BROKER)

    def _new(self, broker_id: int) -> Lane:
        lane = Lane(broker_id, self.store, self.latency_us)
        if broker_id != routing.DEFAULT_BROKER:
            # The trap catalogue's knowledge is one, whichever network taught it.
            lane.learner.clears = self.default.learner.clears
            lane.learner.states = self.default.learner.states
        self.by_id[broker_id] = lane
        return lane

    def __iter__(self) -> Iterator[Lane]:
        return iter(list(self.by_id.values()))

    def of(self, broker_id: int) -> Lane:
        """A broker's lane; the Default's for a broker the snapshot does not know yet."""
        lane = self.by_id.get(broker_id)
        return self.default if lane is None else lane

    def resolve(self, event: TrapEvent) -> int:
        """Which broker a trap belongs to: its port, its proxy, or its source's longest range."""
        broker = self.routing.resolve(event.device, event.port, event.broker)
        return broker if broker in self.by_id else routing.DEFAULT_BROKER

    # -- across every lane: cheap, and each id lives in exactly one lane --------------------------

    def remove(self, alarm_id: int) -> None:
        """A cleared or re-activated alarm leaves whichever window holds it."""
        for lane in self.by_id.values():
            lane.correlator.remove(alarm_id)

    def forget(self, sid: int) -> None:
        """A closed or merged situation leaves whichever grouper holds it."""
        for lane in self.by_id.values():
            lane.correlator.grouper.forget(sid)

    def take_overflow(self) -> int:
        return sum(lane.correlator.take_overflow() for lane in self.by_id.values())

    # -- the reload point -------------------------------------------------------------------------

    async def restore(self, store: Store) -> None:
        """At start: the routing snapshot, a lane per broker, and each lane's learned state."""
        await self.refresh(store, restore=False)
        for lane in self:
            await lane.restore(store, shared=lane is self.default)

    async def refresh(self, store: Store, *, restore: bool = True) -> bool:
        """Rebuild the routing snapshot from the brokers as they are now; True when the set of
        ports to listen on changed. A broker created since the last reload gets a lane (with its
        learned state, if it has any); one deleted loses its lane. Called under the store lock,
        between batches."""
        rows = await store.broker_routes()
        before = self.routing.ports()
        self.routing = routing.build(rows)
        self.names = {int(r["id"]): str(r["name"]) for r in rows}
        present = {int(r["id"]) for r in rows} | {routing.DEFAULT_BROKER}
        for broker_id in sorted(present - set(self.by_id)):
            lane = self._new(broker_id)
            if restore:
                await lane.restore(store, shared=False)
        for broker_id in sorted(set(self.by_id) - present):
            del self.by_id[broker_id]
        return self.routing.ports() != before

    async def load_scorers(self) -> None:
        """Each lane's decider, as its broker's settings and judge's decisions say."""
        for lane in self:
            await lane.load_scorer_config()

    async def save(self, store: Store, now: float) -> None:
        for lane in self:
            await lane.save(store, now, shared=lane is self.default)

    async def audit_fallbacks(self, now: float) -> None:
        for lane in self:
            await lane._audit_scorer_fallback(now)

    def warnings(self) -> list[str]:
        """Every lane's scoring warnings, each other than the Default's naming its broker."""
        out: list[str] = []
        for lane in self:
            prefix = (
                "" if lane is self.default else f"Broker {self.names.get(lane.broker_id, '?')}: "
            )
            out.extend(prefix + warning for warning in lane.scorer_warning_list())
        return out


class LanesMixin:
    """The engine's side of the lanes: the reload point and what it reports (ADR #495)."""

    lanes: Lanes
    store: Store

    async def load_scorer_config(self) -> None:
        """The reload point: the brokers as they are now — routing, a lane for a new one — then
        each lane's decider. Called at start and at the top of each maintenance pass, so a broker
        or a decider an admin changed takes effect within one interval and never mid-batch."""
        await self.lanes.refresh(self.store)
        await self.lanes.load_scorers()

    async def _audit_scorer_fallback(self, now: float) -> None:
        await self.lanes.audit_fallbacks(now)

    def scorer_warning_list(self) -> list[str]:
        return self.lanes.warnings()
