"""A generated network estate: sites, elements, the links between them, and who rides what.

Each generated stream gets its own estate, drawn from a seed. The estate is the ground the
incident families stand on: a fibre cut needs an OLT with ONUs behind a PON port, a DWDM
degradation needs an amplifier chain with routers riding the wave, a power loss needs a site with
more than one element in it. **Nothing here is ever shown to the appliance** — it receives traps
and nothing else, exactly as in production — so the estate exists to make the *traps* coherent,
not to be learned.

Sizes, vendors, element counts and addressing are **randomised per estate**. That is the first
defence against a model learning the generator (Part II.2 of the brief): no two streams share a
topology, a vendor mix or an address plan, so a model can only profit from relations that hold
across all of them.

Addresses are drawn from RFC 5737/RFC 1918 space **with no structure tying an address to a site**,
deliberately: an address plan is an estate-specific convention, and a generator that encoded one
would teach a model a relation the next customer does not have.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

__all__ = ["Element", "Estate", "LineSystem", "Link", "build_estate"]

ROUTER_VENDORS = ("cisco", "juniper", "nokia", "huawei")
OLT_VENDORS = ("huawei", "zte")
SWITCH_VENDORS = ("cisco", "huawei", "zte", "juniper")
DWDM_VENDORS = ("adva", "ciena", "huawei")


@dataclass
class Element:
    """One network element: one management address, one vendor, one site."""

    ip: str
    kind: str  # router | olt | switch | dwdm | ups
    vendor: str
    site: int
    #: Port name -> ifIndex. Ports are created on demand as links attach.
    ports: dict[str, int] = field(default_factory=dict)
    #: OLT only: slot -> PON port names on it.
    cards: dict[int, list[str]] = field(default_factory=dict)
    #: OLT only: PON port -> ONU identifiers behind it (they have no address of their own).
    onus: dict[str, list[str]] = field(default_factory=dict)
    #: v0.27.0 (ADR #426): this element's firmware stamps every trap with a sequence number and an
    #: event time — varbinds unique per trap that look like identifiers and are not. The entity
    #: profiler must refuse them; `eval/corpus`'s `decoy_varbinds` is the shape.
    decoys: bool = False
    serial: int = 0
    #: v0.29.0 (ADR #442): this element's traps about a link or a span name the element at the
    #: other end by its management address, as the field review's own traps did. Some vendors'
    #: firmware does, some does not — drawn per vendor per estate.
    names_far: bool = False

    def port(self, name: str) -> int:
        """The ifIndex of ``name``, allocating the next one on first use."""
        if name not in self.ports:
            self.ports[name] = 1 + len(self.ports) + 100 * (1 + len(self.ports) // 48)
        return self.ports[name]


@dataclass(frozen=True)
class Link:
    """A physical adjacency between two elements, one port at each end."""

    a: str
    a_port: str
    b: str
    b_port: str
    bgp: bool  # an eBGP/iBGP session runs over it
    ospf: bool  # an OSPF adjacency runs over it
    #: The address each end's routing protocols name the OTHER end by. In half the estates it is
    #: the far end's management address (loopback-as-router-id, a common design); in the other half
    #: it is an interface address that matches no management address at all — so a model cannot
    #: learn that a peer address always names a device the appliance has heard from.
    a_peer: str = ""
    b_peer: str = ""


@dataclass(frozen=True)
class LineSystem:
    """A DWDM line between two terminals, with in-line amplifiers, carrying router links."""

    terminals: tuple[str, str]
    amplifiers: tuple[str, ...]
    carried: tuple[Link, ...]  # the router-to-router links riding this line


@dataclass
class Estate:
    elements: dict[str, Element]
    links: list[Link]
    lines: list[LineSystem]
    sites: int

    def of_kind(self, kind: str) -> list[Element]:
        return [e for e in self.elements.values() if e.kind == kind]

    def at_site(self, site: int) -> list[Element]:
        return [e for e in self.elements.values() if e.site == site]

    def links_of(self, ip: str) -> list[Link]:
        return [link for link in self.links if ip in (link.a, link.b)]


def _address(rng: random.Random, used: set[str]) -> str:
    """A fresh management address, drawn from three unrelated blocks with no site structure."""
    while True:
        block = rng.choice(("10.{}.{}.{}", "172.{}.{}.{}", "198.51.{}.{}"))
        if block.startswith("172"):
            ip = block.format(rng.randint(16, 31), rng.randint(0, 255), rng.randint(1, 254))
        elif block.startswith("198"):
            ip = block.format(rng.randint(0, 255), rng.randint(1, 254))
        else:
            ip = block.format(rng.randint(0, 255), rng.randint(0, 255), rng.randint(1, 254))
        if ip not in used:
            used.add(ip)
            return ip


def build_estate(rng: random.Random) -> Estate:
    """Draw one estate. Every count below is a range, never a constant."""
    used: set[str] = set()
    sites = rng.randint(3, 18)
    # A vendor mix per estate: most operators run two or three router vendors, one access vendor.
    router_vendors = rng.sample(ROUTER_VENDORS, rng.randint(1, 3))
    olt_vendor = rng.choice(OLT_VENDORS)
    dwdm_vendor = rng.choice(DWDM_VENDORS)
    elements: dict[str, Element] = {}

    def add(kind: str, vendor: str, site: int) -> Element:
        el = Element(_address(rng, used), kind, vendor, site)
        elements[el.ip] = el
        return el

    routers_by_site: dict[int, list[Element]] = {}
    for site in range(sites):
        routers_by_site[site] = [
            add("router", rng.choice(router_vendors), site) for _ in range(rng.randint(1, 2))
        ]
        if rng.random() < 0.7:
            for _ in range(rng.randint(1, 3)):
                olt = add("olt", olt_vendor, site)
                for slot in range(1, rng.randint(2, 5)):
                    pons = [f"0/{slot}/{p}" for p in range(rng.randint(2, 8))]
                    olt.cards[slot] = pons
                    for pon in pons:
                        olt.onus[pon] = [f"{pon}/{n}" for n in range(1, rng.randint(4, 48))]
        if rng.random() < 0.5:
            add("switch", rng.choice(SWITCH_VENDORS), site)
        if rng.random() < 0.8:
            add("ups", rng.choice(router_vendors), site)

    links: list[Link] = []
    peers_by_interface = rng.random() < 0.5
    subnet = [rng.randint(0, 250)]

    def connect(a: Element, b: Element, routed: bool) -> Link:
        a_port = f"ge-{len(a.ports)}/0/{rng.randint(0, 3)}"
        b_port = f"ge-{len(b.ports)}/0/{rng.randint(0, 3)}"
        a.port(a_port)
        b.port(b_port)
        if peers_by_interface:
            subnet[0] = (subnet[0] + 1) % 256
            base = f"192.0.{subnet[0]}"
            a_peer, b_peer = f"{base}.2", f"{base}.1"  # each end names the other's interface
        else:
            a_peer, b_peer = b.ip, a.ip
        link = Link(
            a.ip,
            a_port,
            b.ip,
            b_port,
            bgp=routed and rng.random() < 0.6,
            ospf=routed,
            a_peer=a_peer,
            b_peer=b_peer,
        )
        links.append(link)
        return link

    # The backbone: a ring over the sites plus a few chords, so most sites have two ways out.
    order = list(range(sites))
    rng.shuffle(order)
    backbone: list[Link] = []
    for i, site in enumerate(order):
        nxt = order[(i + 1) % sites]
        if sites > 2 or i == 0:
            backbone.append(
                connect(rng.choice(routers_by_site[site]), rng.choice(routers_by_site[nxt]), True)
            )
    for _ in range(rng.randint(0, max(0, sites // 3))):
        a, b = rng.sample(range(sites), 2)
        backbone.append(
            connect(rng.choice(routers_by_site[a]), rng.choice(routers_by_site[b]), True)
        )
    # Access: every OLT and switch uplinks to a router at its own site.
    for el in list(elements.values()):
        if el.kind in ("olt", "switch"):
            connect(el, rng.choice(routers_by_site[el.site]), routed=False)

    # DWDM: some backbone spans ride a line system with in-line amplifiers at intermediate sites.
    lines: list[LineSystem] = []
    for link in backbone:
        if rng.random() < 0.45:
            ta = add("dwdm", dwdm_vendor, elements[link.a].site)
            tb = add("dwdm", dwdm_vendor, elements[link.b].site)
            amps = tuple(
                add("dwdm", dwdm_vendor, rng.randrange(sites)).ip for _ in range(rng.randint(1, 4))
            )
            lines.append(LineSystem((ta.ip, tb.ip), amps, (link,)))
    # Drawn last, so every draw above is unchanged by it: which vendors' firmware stamps decoys.
    stamping = {v for v in sorted({e.vendor for e in elements.values()}) if rng.random() < 0.3}
    naming = {v for v in sorted({e.vendor for e in elements.values()}) if rng.random() < 0.5}
    for el in elements.values():
        el.decoys = el.vendor in stamping
        el.names_far = el.vendor in naming
    return Estate(elements, links, lines, sites)
