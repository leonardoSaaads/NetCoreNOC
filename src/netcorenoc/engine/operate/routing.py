"""Which broker a trap belongs to: one pure decision over an immutable snapshot (ADR #494).

A broker is one monitored network (`migrations/0047_brokers.sql`). How a trap reaches one is chosen
when the broker is created:

* **its own port** (`port`) — every trap that arrives on that UDP port is the broker's;
* **a remote proxy** (`proxy`) — the proxy's token names the broker, and the central server writes
  the trap to the spool with the broker already on it;
* **ranges** (`cidr`, and a `port` broker's ranges on the standard port) — the source address is
  matched against every broker's CIDRs and **the longest prefix wins**, as in a routing table.

Anything else is the Default broker's. The decision is made per trap, inside the engine's batch,
so it takes no lock, does no I/O and reads nothing but :class:`Routing` — a snapshot the
maintenance loop rebuilds at the reload point and swaps in whole, like the maintenance-window
index. A broker created or changed between two reloads takes effect at the next one (≤ 5 s).
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "CACHE_MAX",
    "DEFAULT_BROKER",
    "INTAKES",
    "MAX_NETWORKS",
    "BrokerRoute",
    "Routing",
    "RoutingError",
    "build",
    "canonical_networks",
    "overlaps",
]

#: The broker every unclaimed trap, and everything from before brokers, belongs to.
DEFAULT_BROKER = 1
#: The three ways a trap reaches a broker; `port` is the default when one is created.
INTAKES = ("port", "proxy", "cidr")
#: The most ranges one broker may claim.
MAX_NETWORKS = 256
#: The most sources whose broker a snapshot remembers.
CACHE_MAX = 200_000

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


class RoutingError(ValueError):
    """A range list that cannot be stored: the message says which entry and why."""


def canonical_networks(text: str | Iterable[str]) -> tuple[str, ...]:
    """Parse ranges (comma-, space- or newline-separated) into their canonical, sorted, unique form.

    A bare address is its own /32 or /128. Host bits are refused rather than silently masked
    (``10.0.0.1/8`` is almost always a typo for ``10.0.0.0/8``), and so is anything that is not a
    network at all.
    """
    items = text.replace(",", " ").split() if isinstance(text, str) else [str(t) for t in text]
    out: dict[str, Network] = {}
    for raw in items:
        try:
            net = ipaddress.ip_network(raw.strip(), strict=True)
        except ValueError as exc:
            raise RoutingError(f"{raw!r} is not a network such as 10.20.0.0/16 ({exc})") from exc
        out[str(net)] = net
    if len(out) > MAX_NETWORKS:
        raise RoutingError(f"at most {MAX_NETWORKS} ranges per broker")
    return tuple(sorted(out, key=lambda key: (out[key].version, out[key])))


def overlaps(mine: Iterable[str], others: Mapping[int, Iterable[str]]) -> list[tuple[str, int]]:
    """Each of ``mine`` that **another broker already claims exactly** — the one conflict refused.

    A narrower or wider range is fine (the longest prefix wins, as in routing); the same range in
    two brokers would leave the choice to the order rows happen to load in, so it is refused.
    """
    claimed: dict[str, int] = {}
    for broker_id, nets in others.items():
        for net in nets:
            claimed.setdefault(net, broker_id)
    return [(net, claimed[net]) for net in mine if net in claimed]


@dataclass(frozen=True)
class BrokerRoute:
    """What routing needs to know about one broker."""

    id: int
    name: str
    intake: str
    port: int | None = None
    networks: tuple[str, ...] = ()
    enabled: bool = True


@dataclass(frozen=True)
class Routing:
    """The snapshot: ports, proxies and ranges, indexed for one lookup per trap."""

    brokers: Mapping[int, BrokerRoute] = field(default_factory=dict)
    by_port: Mapping[int, int] = field(default_factory=dict)
    #: (prefix length, network, broker) — longest prefix first, so the first match wins.
    ranges: tuple[tuple[int, Network, int], ...] = ()
    #: Each source's answer, once computed: the elements of an estate are a finite set, so the
    #: range scan runs once per address per snapshot, not once per trap. Bounded by
    #: :data:`CACHE_MAX`, and thrown away with the snapshot at the next reload.
    cache: dict[str, int] = field(default_factory=dict, compare=False, repr=False)

    def resolve(self, source: str, port: int = 0, broker: int = 0) -> int:
        """The broker a trap belongs to. ``port`` is the local port it arrived on (0: the standard
        port), ``broker`` the one a proxy's token named (0: none)."""
        if broker:
            # A proxy names its broker; a broker since removed or switched to another intake has
            # no claim on what its old proxy still sends, so that falls to the Default broker.
            known = self.brokers.get(broker)
            return broker if known and known.enabled and known.intake == "proxy" else DEFAULT_BROKER
        if port:
            owner = self.by_port.get(port)
            if owner is not None:
                return owner
        if not self.ranges:
            return DEFAULT_BROKER
        cached = self.cache.get(source)
        if cached is not None:
            return cached
        owner = DEFAULT_BROKER
        try:
            address = ipaddress.ip_address(source)
        except ValueError:
            address = None
        if address is not None:
            for _length, net, candidate in self.ranges:
                if address.version == net.version and address in net:
                    owner = candidate
                    break
        if len(self.cache) >= CACHE_MAX:
            self.cache.clear()
        self.cache[source] = owner
        return owner

    def ports(self) -> tuple[int, ...]:
        """The UDP ports the ingest process must listen on besides the standard one."""
        return tuple(sorted(self.by_port))


def build(rows: Iterable[Mapping[str, Any]]) -> Routing:
    """A snapshot from the store's broker rows. A disabled broker routes nothing: its port is not
    listened on and its ranges claim nothing, so its traps fall to the Default broker."""
    brokers: dict[int, BrokerRoute] = {}
    by_port: dict[int, int] = {}
    ranges: list[tuple[int, Network, int]] = []
    for row in rows:
        route = BrokerRoute(
            id=int(row["id"]),
            name=str(row["name"]),
            intake=str(row["intake"]),
            port=None if row.get("udp_port") is None else int(row["udp_port"]),
            networks=canonical_networks(str(row.get("networks") or "")),
            enabled=bool(row.get("enabled", True)),
        )
        brokers[route.id] = route
        if not route.enabled:
            continue
        if route.intake == "port" and route.port:
            by_port[route.port] = route.id
        if route.intake in ("port", "cidr"):
            for text in route.networks:
                net = ipaddress.ip_network(text)
                ranges.append((net.prefixlen, net, route.id))
    ranges.sort(key=lambda r: (-r[0], r[2]))
    return Routing(brokers=brokers, by_port=by_port, ranges=tuple(ranges))
