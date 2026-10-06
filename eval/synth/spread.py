"""Incidents spread over time: one cause, alarms minutes to an hour apart, isolated alerts around.

v0.29.0 (ADR #442). The analysis of the v0.29.0 training set (`docs/correlation.md`, *Alarms at
different times*) measured what a field review had noticed: 92 % of the positive training mass was
pairs **under ten seconds apart**, every model grouped only about a third of an incident's alarm
pairs once they were more than a minute apart, and the most common fault a NOC works — a span that
degrades, breaks, takes services with it and is repaired — looks nothing like a storm. This module
adds that shape, the way `adverse.py` added the bad day.

**Families** — one cause each, the truth keeps every alarm in one incident:

* ``staged_link_failure`` — a routed link's optics degrade for minutes, then the link breaks: both
  ends, the protocols on their timers, the services behind each end tens of seconds later, an
  operator's reroute, and after the repair the clean-up. The field review's own script, on a link
  the trained families own (the DWDM line systems stay held out).
* ``slow_card_failure`` — a line card overheats, its ports throw errors now and then for half an
  hour, then it fails with every port; the far ends see their links go; a replacement follows.
* ``gpon_degrading_feeder`` — ONUs on one PON drop and recover one at a time for up to an hour,
  then the feeder goes and the PON with it; after the repair the ONUs return over many minutes.
* ``upstream_loss_trickle`` — a core router goes dark: neighbours report their links at once,
  OSPF and BGP on their dead and hold timers, the access elements at its site lose their uplink;
  it returns tens of minutes later with a cold start and a configuration change.
* ``staged_power_failure`` — mains fails at a site and every element runs on its own battery: each
  goes dark at its own time, the far ends see it go, and the cold starts come back staggered.

**Regime** (`SpreadRegime`, a stream's ``spread`` intensity): some incidents are **stretched** —
the same events, each offset from the incident's start multiplied, as a slow management plane and
long protocol timers stretch them — and some get **bystanders**: isolated alerts on an element the
incident touches, inside its span, that belong to nothing (a login failure, a configuration save, a
customer port). They are the negatives a time-spread correlator meets most: same element, same
hour, unrelated.

Nothing here uses an optical-transport fault role (a line system's loss of signal or frame, its
amplifiers, a protection switch) or the protocol-flap families' repeated pattern, so `test_optical`
and `test_protocol` stay held out — as `adverse.py`, a power loss may take a site's DWDM shelf down
with everything else, and says so in power roles.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from synth.emit import Event
from synth.estate import Element, Link
from synth.families import Builder, Spec, _el, _spec

__all__ = ["SPREAD_FAMILIES", "SpreadRegime", "bystander", "stretch"]


def _far(link: Link, ip: str) -> tuple[str, str, str]:
    """The other end of ``link`` from ``ip``: its address, its port, the peer name it uses."""
    return (
        (link.b, link.b_port, link.b_peer) if link.a == ip else (link.a, link.a_port, link.a_peer)
    )


def _routed(b: Builder) -> list[Link]:
    return [
        lk
        for lk in b.estate.links
        if _el(b, lk.a).kind == "router" and _el(b, lk.b).kind == "router"
    ]


def _until(fix: float | None, at: float, base: float) -> float | None:
    """``clear_after`` for an alarm raised at ``at`` that clears ``fix`` after ``base``."""
    return None if fix is None else max(1.0, base + fix - at)


def staged_link_failure(b: Builder) -> None:
    link = b.rng.choice(_routed(b) or b.estate.links)
    fix = b.repair(30, 480)
    ends = ((link.a, link.a_port, link.b), (link.b, link.b_port, link.a))
    step = b.rng.choice((b.rng.uniform(20.0, 120.0), b.rng.uniform(120.0, 900.0)))
    t = 0.0
    stages = b.rng.randint(1, 4)
    times = []
    for _ in range(stages):
        times.append(t)
        t += b.spread(step)
    brk = t
    for n, at in enumerate(times):
        ip, port, far = b.rng.choice(ends)
        b.say(
            _el(b, ip),
            ("rx_power_low", "signal_degrade"),
            port,
            at,
            root=n == 0,
            clear_after=_until(fix, at, brk),
            far=far,
        )
    for ip, port, far in ends:
        at = brk + b.spread(0.5)
        b.say(_el(b, ip), ("link_down",), port, at, clear_after=fix, far=far)
        if link.ospf and b.rng.random() < 0.8:
            dead = at + b.rng.uniform(5.0, 40.0)
            peer = link.a_peer if ip == link.a else link.b_peer
            b.say(
                _el(b, ip), ("ospf_nbr",), peer, dead, clear_after=_until(fix, dead, at), peer=peer
            )
        if link.bgp and b.rng.random() < 0.85:
            hold = at + b.rng.uniform(30.0, 180.0)
            peer = link.a_peer if ip == link.a else link.b_peer
            b.say(
                _el(b, ip), ("bgp_down",), peer, hold, clear_after=_until(fix, hold, at), peer=peer
            )
        if b.rng.random() < 0.6:
            for _ in range(b.rng.randint(1, 3)):
                at_svc = brk + b.rng.uniform(10.0, 120.0)
                b.say(
                    _el(b, ip),
                    ("link_down",),
                    f"svc-{b.rng.randint(1, 64)}",
                    at_svc,
                    clear_after=_until(fix, at_svc, brk),
                )
    if b.rng.random() < 0.5:
        ip = b.rng.choice(ends)[0]
        b.say(_el(b, ip), ("config_change",), "chassis", brk + b.rng.uniform(60.0, 900.0))
    if fix is not None and b.rng.random() < 0.6:
        for ip, _port, _far in ends:
            b.say(_el(b, ip), ("config_change",), "chassis", brk + fix + b.rng.uniform(30.0, 900.0))


def slow_card_failure(b: Builder) -> None:
    boxes = [e for e in b.estate.elements.values() if e.kind in ("router", "switch", "olt")]
    el = b.rng.choice(sorted(boxes, key=lambda e: e.ip))
    slot = b.rng.randint(1, 8)
    fix = b.repair(30, 480)
    span = b.rng.uniform(300.0, 2400.0)
    fail = span + b.spread(60.0)
    b.say(
        el,
        ("temp_high", "fan_fail"),
        f"slot-{slot}",
        0.0,
        root=True,
        clear_after=_until(fix, 0.0, fail),
    )
    for _ in range(b.rng.randint(2, 8)):
        b.say(
            el,
            ("link_down",),
            f"ge-{slot}/0/{b.rng.randint(0, 23)}",
            b.rng.uniform(0.0, span),
            clear_after=b.spread(20.0) + 1.0,
        )
    b.say(el, ("board_fail",), f"slot-{slot}", fail, clear_after=fix)
    for port in b.rng.sample(range(24), b.rng.randint(4, 24)):
        at = fail + b.rng.uniform(0.0, 3.0)
        back = None if fix is None else fix + b.rng.uniform(1.0, 60.0)
        b.say(el, ("link_down",), f"ge-{slot}/0/{port}", at, clear_after=back)
    for link in b.estate.links_of(el.ip)[:3]:
        if b.rng.random() < 0.5:
            far, port, _peer = _far(link, el.ip)
            at = fail + b.spread(1.0)
            b.say(_el(b, far), ("link_down",), port, at, clear_after=fix, far=el.ip)
    if fix is not None:
        b.say(el, ("board_removed",), f"slot-{slot}", fail + 0.6 * fix, clear_after=0.4 * fix)
        b.say(el, ("config_change",), "chassis", fail + fix + b.spread(120.0))


def gpon_degrading_feeder(b: Builder) -> None:
    olt = b.rng.choice(sorted(b.estate.of_kind("olt"), key=lambda e: e.ip))
    pon = b.rng.choice(sorted(olt.onus))
    onus = olt.onus[pon]
    fix = b.repair(30, 360)
    span = b.rng.uniform(600.0, 3600.0)
    first = True
    for onu in b.rng.sample(onus, max(1, len(onus) // b.rng.randint(3, 6))):
        for _ in range(b.rng.randint(1, 3)):
            b.say(
                olt,
                ("onu_los",),
                onu,
                b.rng.uniform(0.0, span),
                root=first,
                clear_after=b.spread(60.0) + 1.0,
            )
            first = False
    cut = span + b.spread(120.0)
    b.say(olt, ("pon_los",), pon, cut, clear_after=fix)
    for onu in onus:
        back = None if fix is None else fix + b.rng.uniform(5.0, 900.0)
        b.say(olt, ("onu_los",), onu, cut + b.rng.uniform(0.0, 8.0), clear_after=back)


def upstream_loss_trickle(b: Builder) -> None:
    routers = sorted({lk.a for lk in _routed(b)} | {lk.b for lk in _routed(b)})
    router = _el(b, b.rng.choice(routers))
    back = b.rng.uniform(300.0, 2400.0)
    if b.rng.random() < 0.6:
        b.say(router, ("dying_gasp", "psu_fail"), "chassis", 0.0, root=True)
    for link in b.estate.links_of(router.ip):
        far, port, peer = _far(link, router.ip)
        at = b.spread(2.0)
        b.say(_el(b, far), ("link_down",), port, at, clear_after=max(1.0, back - at), far=router.ip)
        if link.ospf and b.rng.random() < 0.7:
            dead = at + b.rng.uniform(30.0, 45.0)
            b.say(
                _el(b, far), ("ospf_nbr",), peer, dead, clear_after=max(1.0, back - dead), peer=peer
            )
        if link.bgp and b.rng.random() < 0.9:
            hold = at + b.rng.uniform(60.0, 180.0)
            again = back + b.rng.uniform(30.0, 300.0)
            b.say(
                _el(b, far),
                ("bgp_down",),
                peer,
                hold,
                clear_after=max(1.0, again - hold),
                peer=peer,
            )
    b.say(router, ("cold_start",), "chassis", back, root=not b.events)
    b.say(router, ("config_change",), "chassis", back + b.spread(60.0))


def staged_power_failure(b: Builder) -> None:
    ups = sorted(b.estate.of_kind("ups"), key=lambda e: e.ip)
    site = b.rng.choice(ups).site if ups else b.rng.randrange(b.estate.sites)
    local = sorted(b.estate.at_site(site), key=lambda e: e.ip)
    fix = b.repair(60, 600)
    first = True
    for el in local:
        if el.kind == "ups":
            b.say(el, ("mains_fail",), "input", 0.0, root=first, clear_after=fix)
            b.say(el, ("on_battery",), "battery", b.spread(2.0))
            first = False
    for el in local:
        if el.kind == "ups":
            continue
        dies = b.rng.uniform(600.0, 5400.0)
        if fix is not None and dies >= fix:
            continue
        b.say(el, ("dying_gasp", "psu_fail"), "chassis", dies, root=first)
        first = False
        for link in b.estate.links_of(el.ip):
            far, port, _peer = _far(link, el.ip)
            if _el(b, far).site == site:
                continue
            down = dies + b.spread(5.0)
            b.say(
                _el(b, far),
                ("link_down",),
                port,
                down,
                clear_after=_until(fix, down, 120.0),
                far=el.ip,
            )
        if fix is not None and el.kind in ("router", "olt", "switch"):
            b.say(el, ("cold_start",), "chassis", fix + b.rng.uniform(60.0, 900.0))


#: Training families (ADR #442), each in the group of the domain it lives in.
SPREAD_FAMILIES: dict[str, Spec] = {
    "staged_link_failure": _spec(staged_link_failure, ("backbone",), "routing"),
    "slow_card_failure": _spec(slow_card_failure, (), "equipment"),
    "gpon_degrading_feeder": _spec(gpon_degrading_feeder, ("olt",), "gpon"),
    "upstream_loss_trickle": _spec(upstream_loss_trickle, ("backbone",), "routing"),
    "staged_power_failure": _spec(staged_power_failure, (), "power"),
}


# -- the regime: stretched incidents, and bystanders ----------------------------------------------


def stretch(events: list[Event], factor: float) -> None:
    """Multiply every event's offset from the incident's first event by ``factor``, in place."""
    if not events or factor == 1.0:
        return
    start = min(e.t for e in events)
    for e in events:
        e.t = start + (e.t - start) * factor


def bystander(b: Builder, el: Element) -> None:
    """One isolated alert on ``el``: the everyday events of `families.noise`, on a given element."""
    kind = b.rng.random()
    if kind < 0.35:
        b.say(
            el,
            ("link_down",),
            f"cust-{b.rng.randint(1, 96)}",
            0.0,
            root=True,
            clear_after=b.rng.uniform(2.0, 600.0),
        )
    elif kind < 0.6:
        b.say(el, ("auth_failure",), "ssh", 0.0, root=True)
    elif kind < 0.85:
        b.say(el, ("config_change",), "chassis", 0.0, root=True)
    else:
        b.say(
            el,
            ("cpu_high", "config_change"),
            "cpu-0",
            0.0,
            root=True,
            clear_after=b.rng.uniform(30.0, 900.0),
        )


@dataclass(frozen=True)
class SpreadRegime:
    """A stream's ``spread`` intensity in [0, 1]; 0 draws nothing, so other draws stay put."""

    intensity: float

    def stretch_factor(self, rng: random.Random) -> float:
        """1.0 (untouched) or a factor in [2, 2 + 10 * intensity]."""
        k = self.intensity
        if k <= 0 or rng.random() >= 0.5 * k:
            return 1.0
        return rng.uniform(2.0, 2.0 + 10.0 * k)

    def bystanders(self, rng: random.Random) -> int:
        """How many isolated alerts to place around one incident."""
        k = self.intensity
        if k <= 0 or rng.random() >= 0.6 * k:
            return 0
        return rng.randint(1, 3)
