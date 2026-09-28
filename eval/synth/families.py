"""The incident families: what one real fault looks like on the wire, in each domain.

Each family is a function that draws **one incident** on an estate: which elements it touches, which
fault roles they report, in what order, how fast, how far it fans out, and when (if ever) it clears.
Every one of those is drawn from a range, never fixed — the fan-out of a fibre cut, the BGP hold
timer, the speed of an optical degradation, whether a battery outlasts a mains failure. The shape of
each family is taken from how the fault propagates in a real network (ITU-T G.798/G.984 for the
optical and GPON consequent actions, RFC 4271 hold timers for BGP, RFC 2328 for OSPF dead
intervals); the *numbers* are ranges wide enough that a model which keys on any one of them keys on
nothing.

A family returns its events with **causal** times relative to its own start. Arrival order, loss,
duplication and per-element latency are applied later by `compose.py`, because they are properties
of the path between the network and the appliance, not of the fault.

`FAMILIES` maps a family name to its function and to what the estate must contain for it to be
drawn; `compose.py` never asks for a family the estate cannot host.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field

from synth.catalogue import render_first
from synth.emit import Event, trap
from synth.estate import Element, Estate, LineSystem, Link

__all__ = ["FAMILIES", "NOISE", "Builder", "Family", "Spec", "hosts"]


@dataclass
class Builder:
    """Accumulates one incident's events. Every helper takes causal seconds from incident start."""

    rng: random.Random
    estate: Estate
    key: str
    family: str
    events: list[Event] = field(default_factory=list)

    def say(
        self,
        el: Element,
        chain: tuple[str, ...],
        entity: str,
        t: float,
        *,
        root: bool = False,
        clear_after: float | None = None,
        peer: str = "",
    ) -> None:
        """One raise, and its clear ``clear_after`` seconds later when the fault has one."""
        rendering = render_first(chain, el.vendor)
        raised = trap(
            rendering, el, entity, t=t, incident=self.key, family=self.family, root=root, peer=peer
        )
        if raised is not None:
            self.events.append(raised)
        if clear_after is not None:
            cleared = trap(
                rendering,
                el,
                entity,
                t=t + clear_after,
                incident=self.key,
                family=self.family,
                clear=True,
                peer=peer,
            )
            if cleared is not None:
                self.events.append(cleared)

    def spread(self, mean_s: float) -> float:
        """A positive delay with a heavy right tail around ``mean_s``."""
        return self.rng.expovariate(1.0 / max(mean_s, 1e-3))

    def repair(self, lo_min: float, hi_min: float) -> float | None:
        """Seconds until the repair, or ``None`` when the fault outlives the stream (~15 %)."""
        if self.rng.random() < 0.15:
            return None
        return self.rng.uniform(lo_min, hi_min) * 60.0


Family = Callable[[Builder], None]


def _el(b: Builder, ip: str) -> Element:
    return b.estate.elements[ip]


def _link_ends(b: Builder, link: Link, t: float, clear_after: float | None, root: bool) -> float:
    """Both ends of a physical link go down (each after its own detection delay); the protocols
    riding it follow on their own timers. Returns the latest symptom time."""
    latest = t
    for end, port, peer in ((link.a, link.a_port, link.a_peer), (link.b, link.b_port, link.b_peer)):
        at = t + b.spread(0.4)
        b.say(_el(b, end), ("link_down",), port, at, root=root, clear_after=clear_after)
        root = False
        if link.ospf and b.rng.random() < 0.8:
            dead = at + b.rng.uniform(0.0, 40.0)  # RFC 2328 dead interval, often shortened by BFD
            b.say(_el(b, end), ("ospf_nbr",), peer, dead, clear_after=clear_after, peer=peer)
            latest = max(latest, dead)
        if link.bgp and b.rng.random() < 0.85:
            hold = at + b.rng.choice((b.rng.uniform(0.0, 3.0), b.rng.uniform(30.0, 180.0)))
            b.say(_el(b, end), ("bgp_down",), peer, hold, clear_after=clear_after, peer=peer)
            latest = max(latest, hold)
    return latest


# -- GPON access ------------------------------------------------------------------------------


def _olt(b: Builder) -> Element:
    return b.rng.choice(b.estate.of_kind("olt"))


def gpon_fibre_cut(b: Builder) -> None:
    """A feeder (whole PON) or distribution (part of one) fibre cut. G.984.3: the OLT raises LOS on
    the PON port only when every ONU is lost; each ONU's LOS/LOF follows within the ranging cycle."""
    olt = _olt(b)
    pon = b.rng.choice(sorted(olt.onus))
    onus = olt.onus[pon]
    feeder = b.rng.random() < 0.6
    hit = onus if feeder else b.rng.sample(onus, max(1, len(onus) // b.rng.randint(2, 4)))
    fix = b.repair(20, 360)
    if feeder:
        b.say(olt, ("pon_los",), pon, 0.0, root=True, clear_after=fix)
    burst = b.rng.uniform(0.3, 12.0)
    for i, onu in enumerate(hit):
        at = b.rng.uniform(0.0, burst)
        back = None if fix is None else fix + b.rng.uniform(5.0, 240.0)
        b.say(olt, ("onu_los",), onu, at, root=not feeder and i == 0, clear_after=back)


def onu_power_outage(b: Builder) -> None:
    """A mains outage over a neighbourhood: ONUs send dying gasp (G.984.3 DG) across several PON
    ports and often several OLTs of one site, with no PON LOS — the fibre is fine."""
    olt = _olt(b)
    olts = [o for o in b.estate.of_kind("olt") if o.site == olt.site]
    fix = b.repair(30, 480)
    start = 0.0
    first = True
    for o in b.rng.sample(olts, b.rng.randint(1, len(olts))):
        for pon in b.rng.sample(sorted(o.onus), b.rng.randint(1, min(4, len(o.onus)))):
            for onu in o.onus[pon]:
                if b.rng.random() < 0.7:
                    at = start + b.spread(b.rng.uniform(1.0, 20.0))
                    back = None if fix is None else fix + b.rng.uniform(30.0, 300.0)
                    b.say(o, ("onu_dying_gasp",), onu, at, root=first, clear_after=back)
                    first = False


def olt_card_failure(b: Builder) -> None:
    """A GPON line card fails: the board alarm, every PON port on it, every ONU behind them —
    the storm whose true grouping is coarse."""
    olt = _olt(b)
    slot = b.rng.choice(sorted(olt.cards))
    fix = b.repair(40, 600)
    b.say(olt, ("board_fail",), f"slot-{slot}", 0.0, root=True, clear_after=fix)
    for pon in olt.cards[slot]:
        at = b.spread(1.5)
        b.say(olt, ("pon_los",), pon, at, clear_after=None if fix is None else fix + 20.0)
        for onu in olt.onus[pon]:
            back = None if fix is None else fix + b.rng.uniform(30.0, 400.0)
            b.say(olt, ("onu_los",), onu, at + b.spread(2.0), clear_after=back)


def olt_uplink_failure(b: Builder) -> None:
    """An OLT loses its uplink: a failing optic often degrades first, minutes ahead (slow)."""
    olt = _olt(b)
    uplinks = b.estate.links_of(olt.ip)
    if not uplinks:
        raise ValueError("olt_uplink_failure drawn on an OLT with no uplink")
    link = b.rng.choice(uplinks)
    fix = b.repair(15, 240)
    t = 0.0
    if b.rng.random() < 0.5:
        side = link.a if link.a == olt.ip else link.b
        port = link.a_port if side == link.a else link.b_port
        b.say(olt, ("rx_power_low", "signal_degrade"), port, 0.0, root=True, clear_after=fix)
        t = b.rng.uniform(60.0, 1800.0)
    _link_ends(b, link, t, fix, root=t == 0.0)


# -- routing and transport --------------------------------------------------------------------


def _backbone(b: Builder) -> list[Link]:
    return [
        link
        for link in b.estate.links
        if _el(b, link.a).kind == "router" and _el(b, link.b).kind == "router"
    ]


def router_link_cut(b: Builder) -> None:
    """A backbone fibre cut outside any DWDM line: both ends, then OSPF and BGP on their timers."""
    link = b.rng.choice(_backbone(b))
    _link_ends(b, link, 0.0, b.repair(30, 480), root=True)


def router_board_failure(b: Builder) -> None:
    """A router line card fails and takes several links with it, cross-element through them."""
    links = _backbone(b)
    router = _el(b, b.rng.choice(links).a)
    fix = b.repair(30, 360)
    b.say(router, ("board_fail",), f"slot-{b.rng.randint(1, 8)}", 0.0, root=True, clear_after=fix)
    mine = [lk for lk in links if router.ip in (lk.a, lk.b)]
    for link in b.rng.sample(mine, b.rng.randint(1, len(mine))):
        _link_ends(b, link, b.spread(1.0), fix, root=False)


def _line(b: Builder) -> LineSystem | None:
    return b.rng.choice(b.estate.lines) if b.estate.lines else None


def dwdm_degradation(b: Builder) -> None:
    """Optical degradation along an amplifier chain: power falls stage by stage over minutes to
    hours (slow), until the far terminal loses signal and the routers riding the wave drop."""
    line = _line(b)
    if line is None:
        raise ValueError("dwdm_degradation drawn on an estate with no line system")
    fix = b.repair(60, 720)
    chain = [*line.amplifiers, line.terminals[1]]
    t = 0.0
    step = b.rng.choice((b.rng.uniform(20.0, 120.0), b.rng.uniform(300.0, 1800.0)))
    for i, ip in enumerate(chain[:-1]):
        role = ("amp_output_low", "rx_power_low", "signal_degrade")
        b.say(_el(b, ip), role, f"line-{i}", t, root=i == 0, clear_after=fix)
        t += b.spread(step)
    term = _el(b, chain[-1])
    b.say(term, ("optical_los",), "line-in", t, clear_after=fix)
    b.say(term, ("loss_of_frame",), "client-1", t + b.spread(0.5), clear_after=fix)
    for link in line.carried:
        _link_ends(b, link, t + b.spread(1.0), fix, root=False)


def dwdm_line_cut(b: Builder) -> None:
    """A span cut between two amplifiers: everything downstream loses signal within a second."""
    line = _line(b)
    if line is None:
        raise ValueError("dwdm_line_cut drawn on an estate with no line system")
    fix = b.repair(60, 600)
    chain = [*line.amplifiers, line.terminals[1]]
    cut_at = b.rng.randrange(len(chain))
    for i, ip in enumerate(chain[cut_at:]):
        el = _el(b, ip)
        b.say(el, ("optical_los",), f"line-{i}", b.spread(0.3), root=i == 0, clear_after=fix)
        if b.rng.random() < 0.5:
            b.say(el, ("amp_abnormal", "loss_of_frame"), f"amp-{i}", b.spread(0.8), clear_after=fix)
    for link in line.carried:
        _link_ends(b, link, b.spread(0.6), fix, root=False)


def optical_protection(b: Builder) -> None:
    """A protection switch: the working path degrades, the transport NE switches, service holds.
    Short, local, and it looks exactly like the start of a line cut that never happens."""
    line = _line(b)
    if line is None:
        raise ValueError("optical_protection drawn on an estate with no line system")
    term = _el(b, b.rng.choice(line.terminals))
    back = b.rng.uniform(30.0, 600.0)
    b.say(term, ("signal_degrade",), "working", 0.0, root=True, clear_after=back)
    b.say(term, ("protection_switch",), "psg-1", b.spread(0.05), clear_after=back)
    if b.rng.random() < 0.4:
        b.say(term, ("protection_switch",), "psg-1", back + b.spread(5.0))


def bgp_flap(b: Builder) -> None:
    """A BGP session flaps: both speakers report every transition, several times, no link alarm."""
    links = [lk for lk in _backbone(b) if lk.bgp] or _backbone(b)
    link = b.rng.choice(links)
    t = 0.0
    for n in range(b.rng.randint(1, 6)):
        up = b.rng.uniform(5.0, 240.0)
        for end, peer in ((link.a, link.a_peer), (link.b, link.b_peer)):
            b.say(_el(b, end), ("bgp_down",), peer, t + b.spread(1.0), root=n == 0 and end == link.a,
                  clear_after=up, peer=peer)
        t += up + b.rng.uniform(20.0, 900.0)


def ospf_flap(b: Builder) -> None:
    """An OSPF adjacency flaps (MTU, timers, a unidirectional link): both ends, repeatedly."""
    link = b.rng.choice(_backbone(b))
    t = 0.0
    for n in range(b.rng.randint(1, 5)):
        up = b.rng.uniform(10.0, 120.0)
        for end, peer in ((link.a, link.a_peer), (link.b, link.b_peer)):
            b.say(_el(b, end), ("ospf_nbr",), peer, t + b.spread(2.0), root=n == 0 and end == link.a,
                  clear_after=up, peer=peer)
        t += up + b.rng.uniform(30.0, 1200.0)


def port_flapping(b: Builder) -> None:
    """One port bouncing for a while — sometimes regular (the flap detector's case), often not.
    The irregular kind is what bridges unrelated incidents if a correlator lets it."""
    el = b.rng.choice([e for e in b.estate.elements.values() if e.kind in ("router", "switch", "olt")])
    port = f"access-{b.rng.randint(1, 48)}"
    regular = b.rng.random() < 0.4
    period = b.rng.uniform(15.0, 300.0)
    t = 0.0
    for n in range(b.rng.randint(4, 30)):
        down = b.rng.uniform(1.0, min(30.0, period / 2)) if regular else b.spread(8.0)
        b.say(el, ("link_down",), port, t, root=n == 0, clear_after=down)
        t += period if regular else b.spread(period)


def ne_reboot(b: Builder) -> None:
    """An unplanned reboot: the element goes silent, its neighbours see the links drop and the
    sessions time out, and it comes back with a cold start and a configuration change."""
    router = _el(b, b.rng.choice(_backbone(b)).a)
    back = b.rng.uniform(90.0, 900.0)
    if b.rng.random() < 0.5:
        b.say(router, ("dying_gasp", "cold_start"), "chassis", 0.0, root=True)
    for link in b.estate.links_of(router.ip):
        far, port, peer = (link.b, link.b_port, link.b_peer) if link.a == router.ip else (
            link.a, link.a_port, link.a_peer)
        at = b.spread(1.5)
        b.say(_el(b, far), ("link_down",), port, at, clear_after=back - at)
        if link.bgp:
            b.say(_el(b, far), ("bgp_down",), peer, at + b.rng.uniform(0.0, 180.0),
                  clear_after=back + 30.0, peer=peer)
    b.say(router, ("cold_start",), "chassis", back, root=not b.events)
    if b.rng.random() < 0.6:
        b.say(router, ("config_change",), "chassis", back + b.spread(20.0))


def planned_maintenance(b: Builder) -> None:
    """Planned work on one element with no maintenance window declared: cards pulled and put back,
    a reload, neighbours bouncing — one incident, spread over tens of minutes, at night."""
    el = b.rng.choice([e for e in b.estate.elements.values() if e.kind in ("router", "olt")])
    t = 0.0
    for n in range(b.rng.randint(1, 4)):
        b.say(el, ("board_removed",), f"slot-{b.rng.randint(1, 8)}", t, root=n == 0,
              clear_after=b.rng.uniform(60.0, 900.0))
        t += b.rng.uniform(60.0, 1200.0)
        b.say(el, ("config_change",), "chassis", t + b.spread(5.0))
    if b.rng.random() < 0.5:
        b.say(el, ("warm_start",), "chassis", t + b.spread(30.0))
        for link in b.estate.links_of(el.ip)[:4]:
            far, port = (link.b, link.b_port) if link.a == el.ip else (link.a, link.a_port)
            b.say(_el(b, far), ("link_down",), port, t + b.spread(2.0), clear_after=b.spread(120.0))


def site_power_loss(b: Builder) -> None:
    """Mains fails at a site. The UPS reports; power supplies with one feed fault; if the battery
    runs out the site goes dark and the neighbours see it go — minutes to an hour later."""
    ups = [e for e in b.estate.of_kind("ups")]
    site = b.rng.choice(ups).site if ups else b.rng.randrange(b.estate.sites)
    local = b.estate.at_site(site)
    fix = b.repair(20, 300)
    for el in local:
        if el.kind == "ups":
            b.say(el, ("mains_fail",), "input", 0.0, root=True, clear_after=fix)
            b.say(el, ("on_battery",), "battery", b.spread(2.0))
    for el in local:
        if el.kind != "ups" and b.rng.random() < 0.6:
            b.say(el, ("psu_fail",), "psu-1", b.spread(3.0), root=not b.events, clear_after=fix)
    battery = b.rng.uniform(600.0, 5400.0)
    if fix is None or battery < fix:
        for el in local:
            if el.kind == "router":
                b.say(el, ("dying_gasp", "psu_fail"), "chassis", battery + b.spread(30.0))
                for link in b.estate.links_of(el.ip):
                    far, port = (link.b, link.b_port) if link.a == el.ip else (link.a, link.a_port)
                    if b.estate.elements[far].site != site:
                        down = battery + b.spread(20.0)
                        b.say(_el(b, far), ("link_down",), port, down,
                              clear_after=None if fix is None else fix - down + 120.0)
        if fix is not None:
            for el in local:
                if el.kind in ("router", "olt", "switch"):
                    b.say(el, ("cold_start",), "chassis", fix + b.spread(90.0))


def environment(b: Builder) -> None:
    """A cooling problem on one element: a fan, then the temperature, over minutes (slow)."""
    el = b.rng.choice([e for e in b.estate.elements.values() if e.kind != "ups"])
    fix = b.repair(20, 400)
    t = 0.0
    if b.rng.random() < 0.6:
        b.say(el, ("fan_fail", "temp_high"), "fan-1", 0.0, root=True, clear_after=fix)
        t = b.rng.uniform(120.0, 2400.0)
    b.say(el, ("temp_high", "fan_fail"), "sensor-1", t, root=t == 0.0,
          clear_after=None if fix is None else max(60.0, fix - t))


# -- background: independent faults nobody should group with anything ------------------------


def noise(b: Builder) -> None:
    """One independent everyday event: a customer ONU powered off, a customer port bouncing, a
    login failure, a configuration save, a CPU threshold. Each is its own incident."""
    kind = b.rng.random()
    olts = b.estate.of_kind("olt")
    if kind < 0.4 and olts:
        olt = b.rng.choice(olts)
        onu = b.rng.choice(olt.onus[b.rng.choice(sorted(olt.onus))])
        role = ("onu_dying_gasp",) if b.rng.random() < 0.6 else ("onu_los",)
        b.say(olt, role, onu, 0.0, root=True, clear_after=b.rng.uniform(60.0, 7200.0))
        return
    el = b.rng.choice(list(b.estate.elements.values()))
    if kind < 0.65:
        b.say(el, ("link_down",), f"cust-{b.rng.randint(1, 96)}", 0.0, root=True,
              clear_after=b.rng.uniform(2.0, 600.0))
    elif kind < 0.8:
        b.say(el, ("auth_failure",), "snmp", 0.0, root=True)
    elif kind < 0.92:
        b.say(el, ("config_change",), "chassis", 0.0, root=True)
    else:
        b.say(el, ("cpu_high", "config_change"), "cpu-0", 0.0, root=True,
              clear_after=b.rng.uniform(30.0, 900.0))


@dataclass(frozen=True)
class Spec:
    function: Family
    needs: frozenset[str]  # element kinds / structures the estate must have
    group: str  # the family group used for holdout


def _spec(fn: Family, needs: tuple[str, ...], group: str) -> Spec:
    return Spec(fn, frozenset(needs), group)


#: Every family, what it needs, and its **group** — the unit a holdout removes whole.
FAMILIES: dict[str, Spec] = {
    "gpon_fibre_cut": _spec(gpon_fibre_cut, ("olt",), "gpon"),
    "onu_power_outage": _spec(onu_power_outage, ("olt",), "gpon"),
    "olt_card_failure": _spec(olt_card_failure, ("olt",), "gpon"),
    "olt_uplink_failure": _spec(olt_uplink_failure, ("olt",), "gpon"),
    "router_link_cut": _spec(router_link_cut, ("backbone",), "routing"),
    "router_board_failure": _spec(router_board_failure, ("backbone",), "routing"),
    "bgp_flap": _spec(bgp_flap, ("backbone",), "protocol"),
    "ospf_flap": _spec(ospf_flap, ("backbone",), "protocol"),
    "dwdm_degradation": _spec(dwdm_degradation, ("line",), "optical"),
    "dwdm_line_cut": _spec(dwdm_line_cut, ("line",), "optical"),
    "optical_protection": _spec(optical_protection, ("line",), "optical"),
    "port_flapping": _spec(port_flapping, (), "flapping"),
    "ne_reboot": _spec(ne_reboot, ("backbone",), "equipment"),
    "planned_maintenance": _spec(planned_maintenance, (), "equipment"),
    "site_power_loss": _spec(site_power_loss, (), "power"),
    "environment": _spec(environment, (), "equipment"),
}

NOISE = _spec(noise, (), "noise")


def hosts(estate: Estate, spec: Spec) -> bool:
    """Whether ``estate`` can host a family with these needs."""
    have = {e.kind for e in estate.elements.values()}
    if any(estate.elements[lk.a].kind == estate.elements[lk.b].kind == "router" for lk in estate.links):
        have.add("backbone")
    if estate.lines:
        have.add("line")
    return spec.needs <= have
