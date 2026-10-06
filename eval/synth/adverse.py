"""Adverse field conditions: the families and the network regimes an appliance meets on a bad day.

v0.29.0 (ADR #439). The v0.27.0 generator drew one fault at a time onto a quiet network with a
well-behaved management path. A NOC's bad days are not like that, and a model trained only on the
good ones learns rules that hold only there. This module adds both halves of a bad day.

**Families** — incidents whose *shape* is hard, each a single cause the truth keeps as one incident:

* ``cascade_site_outage`` — a site goes dark at once: its routers, OLTs and switches stop, every
  neighbour reports its links and protocol sessions, the OLT's uplink partner reports the access
  links. Many elements, many trap types, one cause.
* ``rolling_upgrade`` — firmware rolled across several elements of one vendor, one after another,
  minutes apart: reloads, cold starts, configuration changes, neighbours bouncing. One change.
* ``power_flicker`` — a mains glitch: every element at a site restarts within seconds and the ONUs
  on its OLTs send dying gasp. Nothing is broken afterwards.
* ``chatter_storm`` — one misconfigured element floods the same few traps for an hour (a community
  scan, a looping configuration save, a CPU threshold set too low). Dense, repetitive, one cause —
  and the thing that must not swallow a real incident on the same element.
* ``intermittent_optics`` — a dirty connector at a splitter: a subset of the ONUs on one PON lose
  and regain signal at irregular intervals for hours.
* ``control_plane_overload`` — a router's CPU saturates (an attack, a route leak): its BGP sessions
  drop and return, the peers report the same sessions, login failures accompany it.
* ``hvac_failure`` — a site's cooling fails: fans and temperatures across its elements over tens of
  minutes, and the hottest card shuts down.

No family here uses an optical or held-out protocol-flap shape, so the held-out tests stay held out.

**Regimes** — properties of the path and the hour, drawn per stream with an intensity (`apply`):
congestion windows that drop traps in bursts and hold others back for minutes; elements behind a
slow relay; duplicated traps from a second trap destination; storm windows where faults arrive at
several times the usual rate; noise that follows the working day.
"""

from __future__ import annotations

import math
import random

from synth.emit import Event
from synth.estate import Element, Estate, Link
from synth.families import Builder, Spec, _el, _spec

__all__ = ["ADVERSE_FAMILIES", "Regime", "draw_regime"]


def _far(link: Link, ip: str) -> tuple[str, str, str]:
    """The other end of ``link`` from ``ip``: its address, its port, the peer name it uses."""
    return (
        (link.b, link.b_port, link.b_peer) if link.a == ip else (link.a, link.a_port, link.a_peer)
    )


def cascade_site_outage(b: Builder) -> None:
    sites = sorted({e.site for e in b.estate.elements.values() if e.kind in ("router", "olt")})
    site = b.rng.choice(sites)
    local = {e.ip for e in b.estate.at_site(site)}
    fix = b.repair(15, 240)
    first = True
    for el in sorted(b.estate.at_site(site), key=lambda e: e.ip):
        if el.kind in ("router", "switch", "olt") and b.rng.random() < 0.5:
            # A last gasp from the ones whose management path outlives the power by a moment.
            b.say(el, ("dying_gasp", "psu_fail"), "chassis", b.spread(0.5), root=first)
            first = False
        for link in b.estate.links_of(el.ip):
            far, port, peer = _far(link, el.ip)
            if far in local:
                continue
            down = b.spread(1.5)
            back = None if fix is None else fix + b.rng.uniform(30.0, 240.0)
            b.say(_el(b, far), ("link_down",), port, down, root=first, clear_after=back, far=el.ip)
            first = False
            if link.bgp and b.rng.random() < 0.9:
                hold = down + b.rng.choice((b.rng.uniform(0.0, 3.0), b.rng.uniform(30.0, 180.0)))
                b.say(_el(b, far), ("bgp_down",), peer, hold, clear_after=back, peer=peer)
            if link.ospf and b.rng.random() < 0.8:
                dead = down + b.rng.uniform(0.0, 40.0)
                b.say(_el(b, far), ("ospf_nbr",), peer, dead, clear_after=back, peer=peer)
    if fix is not None:
        for el in b.estate.at_site(site):
            if el.kind in ("router", "switch", "olt"):
                b.say(el, ("cold_start",), "chassis", fix + b.spread(120.0))


def rolling_upgrade(b: Builder) -> None:
    boxes = [e for e in b.estate.elements.values() if e.kind in ("router", "switch", "olt")]
    vendor = b.rng.choice(sorted({e.vendor for e in boxes}))
    same = sorted((e for e in boxes if e.vendor == vendor), key=lambda e: e.ip)
    chosen = b.rng.sample(same, min(len(same), b.rng.randint(2, 6)))
    t = 0.0
    for n, el in enumerate(chosen):
        b.say(el, ("config_change",), "chassis", t, root=n == 0)
        reload_at = t + b.rng.uniform(20.0, 120.0)
        back = b.rng.uniform(120.0, 600.0)
        for link in b.estate.links_of(el.ip)[:6]:
            far, port, peer = _far(link, el.ip)
            at = reload_at + b.spread(1.0)
            b.say(_el(b, far), ("link_down",), port, at, clear_after=back, far=el.ip)
            if link.bgp:
                b.say(
                    _el(b, far),
                    ("bgp_down",),
                    peer,
                    at + b.spread(30.0),
                    clear_after=back,
                    peer=peer,
                )
        b.say(el, ("cold_start", "warm_start"), "chassis", reload_at + back)
        t = reload_at + back + b.rng.uniform(120.0, 900.0)


def power_flicker(b: Builder) -> None:
    sites = sorted({e.site for e in b.estate.elements.values()})
    site = b.rng.choice(sites)
    first = True
    for el in sorted(b.estate.at_site(site), key=lambda e: e.ip):
        if el.kind == "ups":
            b.say(
                el, ("mains_fail",), "input", 0.0, root=first, clear_after=b.rng.uniform(1.0, 8.0)
            )
            first = False
        elif b.rng.random() < 0.85:
            b.say(
                el, ("cold_start", "warm_start"), "chassis", b.rng.uniform(30.0, 240.0), root=first
            )
            first = False
        if el.kind == "olt":
            for pon in b.rng.sample(sorted(el.onus), min(len(el.onus), b.rng.randint(1, 6))):
                for onu in el.onus[pon]:
                    if b.rng.random() < 0.6:
                        b.say(
                            el,
                            ("onu_dying_gasp",),
                            onu,
                            b.spread(2.0),
                            clear_after=b.rng.uniform(60.0, 400.0),
                        )


def chatter_storm(b: Builder) -> None:
    el = b.rng.choice(sorted(b.estate.elements.values(), key=lambda e: e.ip))
    chain, entity = b.rng.choice(
        (
            (("auth_failure",), "snmp"),
            (("config_change",), "chassis"),
            (("cpu_high", "config_change"), "cpu-0"),
        )
    )
    period = b.rng.uniform(0.5, 6.0)
    span = b.rng.uniform(600.0, 5400.0)
    t = 0.0
    n = 0
    while t < span and n < 1200:
        hold = b.rng.uniform(1.0, period) if chain[0] == "cpu_high" else None
        b.say(el, chain, entity, t, root=n == 0, clear_after=hold)
        t += b.spread(period)
        n += 1


def intermittent_optics(b: Builder) -> None:
    olts = b.estate.of_kind("olt")
    olt = b.rng.choice(olts)
    pon = b.rng.choice(sorted(olt.onus))
    hit = b.rng.sample(olt.onus[pon], max(1, len(olt.onus[pon]) // b.rng.randint(2, 6)))
    span = b.rng.uniform(1800.0, 6 * 3600.0)
    first = True
    for onu in hit:
        t = b.rng.uniform(0.0, 600.0)
        while t < span:
            down = b.rng.uniform(2.0, 120.0)
            b.say(olt, ("onu_los",), onu, t, root=first, clear_after=down)
            first = False
            t += down + b.spread(b.rng.uniform(300.0, 2400.0))


def control_plane_overload(b: Builder) -> None:
    routers = [
        e for e in b.estate.of_kind("router") if any(lk.bgp for lk in b.estate.links_of(e.ip))
    ]
    if not routers:
        routers = b.estate.of_kind("router")
    router = b.rng.choice(sorted(routers, key=lambda e: e.ip))
    span = b.rng.uniform(300.0, 3600.0)
    b.say(router, ("cpu_high", "config_change"), "cpu-0", 0.0, root=True, clear_after=span)
    for link in b.estate.links_of(router.ip):
        if not link.bgp:
            continue
        own_peer = link.a_peer if link.a == router.ip else link.b_peer
        far, _port, far_peer = _far(link, router.ip)
        t = b.rng.uniform(5.0, 120.0)
        while t < span:
            up = b.rng.uniform(10.0, 300.0)
            b.say(router, ("bgp_down",), own_peer, t, clear_after=up, peer=own_peer)
            b.say(
                _el(b, far),
                ("bgp_down",),
                far_peer,
                t + b.spread(1.0),
                clear_after=up,
                peer=far_peer,
            )
            t += up + b.spread(400.0)
    for _ in range(b.rng.randint(0, 12)):
        b.say(router, ("auth_failure",), "ssh", b.rng.uniform(0.0, span))


def hvac_failure(b: Builder) -> None:
    sites = sorted({e.site for e in b.estate.elements.values() if e.kind != "ups"})
    site = b.rng.choice(sites)
    local = sorted((e for e in b.estate.at_site(site) if e.kind != "ups"), key=lambda e: e.ip)
    fix = b.repair(30, 360)
    t = 0.0
    hottest: Element | None = None
    for n, el in enumerate(local):
        b.say(
            el,
            ("temp_high", "fan_fail"),
            "sensor-1",
            t,
            root=n == 0,
            clear_after=None if fix is None else max(60.0, fix - t),
        )
        hottest = el
        t += b.spread(b.rng.uniform(60.0, 600.0))
    if hottest is not None and b.rng.random() < 0.5:
        b.say(hottest, ("board_fail",), f"slot-{b.rng.randint(1, 8)}", t + b.spread(300.0))


#: The new families, each a training family (ADR #439): none uses a held-out optical shape or the
#: protocol-flap families' own pattern, so `test_optical` and `test_protocol` stay unseen.
ADVERSE_FAMILIES: dict[str, Spec] = {
    "cascade_site_outage": _spec(cascade_site_outage, ("backbone",), "power"),
    "rolling_upgrade": _spec(rolling_upgrade, ("backbone",), "equipment"),
    "power_flicker": _spec(power_flicker, (), "power"),
    "chatter_storm": _spec(chatter_storm, (), "noise"),
    "intermittent_optics": _spec(intermittent_optics, ("olt",), "gpon"),
    "control_plane_overload": _spec(control_plane_overload, ("backbone",), "routing"),
    "hvac_failure": _spec(hvac_failure, (), "environment"),
}


# -- regimes: the path and the hour ---------------------------------------------------------------


class Regime:
    """One stream's bad-day conditions, at an ``intensity`` in [0, 1]; 0 changes nothing."""

    def __init__(
        self, rng: random.Random, intensity: float, horizon: float, estate: Estate
    ) -> None:
        self.intensity = intensity
        self.horizon = horizon
        k = intensity
        self.congestion: list[tuple[float, float, float, float]] = []
        for _ in range(rng.randint(0, round(6 * k)) if k > 0 else 0):
            start = rng.uniform(0.0, horizon)
            self.congestion.append(
                (
                    start,
                    start + rng.uniform(120.0, 1800.0),
                    rng.uniform(0.1, 0.4) * k,
                    rng.uniform(5.0, 180.0) * k,
                )
            )
        self.slow: dict[str, float] = {
            ip: rng.uniform(30.0, 300.0)
            for ip in sorted(estate.elements)
            if k > 0 and rng.random() < 0.04 * k
        }
        self.dup: dict[str, float] = {
            ip: rng.uniform(0.05, 0.25) * k
            for ip in sorted(estate.elements)
            if k > 0 and rng.random() < 0.15 * k
        }
        self.storms: list[tuple[float, float, float]] = []
        for _ in range(rng.randint(0, round(3 * k)) if k > 0 else 0):
            start = rng.uniform(0.0, horizon)
            self.storms.append((start, start + rng.uniform(600.0, 2700.0), rng.uniform(3.0, 8.0)))
        self.diurnal = k > 0 and rng.random() < 0.7

    def storm_onsets(self, rng: random.Random, base_per_hour: float) -> list[float]:
        """Extra incident onsets inside the storm windows, over the stream's ordinary rate."""
        out: list[float] = []
        for start, end, factor in self.storms:
            rate = base_per_hour * (factor - 1.0) / 3600.0
            t = start
            while rate > 0:
                t += rng.expovariate(rate)
                if t >= min(end, self.horizon):
                    break
                out.append(t)
        return out

    def noise_factor(self, t: float) -> float:
        """Working-day modulation of the noise rate: busier by day, quieter by night."""
        if not self.diurnal:
            return 1.0
        hour = (t / 3600.0) % 24.0
        return 1.0 + 0.7 * self.intensity * math.sin((hour - 8.0) / 24.0 * 2.0 * math.pi)

    def deliver(self, rng: random.Random, e: Event, arrival: float) -> list[float]:
        """The arrivals of one trap under this regime: none (lost), one, or two (duplicated)."""
        arrival += self.slow.get(e.source, 0.0)
        for start, end, loss, delay in self.congestion:
            if start <= arrival < end:
                if rng.random() < loss:
                    return []
                arrival += rng.uniform(0.0, delay)
        out = [arrival]
        if rng.random() < self.dup.get(e.source, 0.0):
            out.append(arrival + rng.uniform(0.01, 5.0))
        return out


def draw_regime(rng: random.Random, intensity: float, horizon: float, estate: Estate) -> Regime:
    return Regime(rng, intensity, horizon, estate)
