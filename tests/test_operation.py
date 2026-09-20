"""**The operation test**: a real appliance, real UDP, real HTTP (DECISIONS #224).

Everything else in this suite drives objects. This drives a *process*:
`python -m netcorenoc.main` in a subprocess on a throwaway database, sixteen genuine SNMPv2c PDUs
over a real UDP socket arriving over five seconds of wall clock, and every observation read back
over TCP through the routes the console calls, as a signed-in principal. Nothing here reaches into
`netcorenoc` as a library after the process starts.

## Why this exists

`eval/simulation/` already contains this test and **nothing ran it**. `drive_http.py` is 295 lines
that boot an appliance, replay a generated network over a socket and label through
`POST /api/situations/{sid}/feedback`; it is imported by no module and no test in the tree. Its
ten-increment loop is half an hour of wall clock, which is why. `tests/test_simulation.py` — the
only file that touches the package — asserts the *generator's* proportions, its seed and its
determinism, and boots no appliance at all.

So the repair is a **bounded** drive that runs on every `make qa`, not a smaller reimplementation
of a harness that works. `eval/simulation/` keeps the generated network and the appliance host it
built; this file is the part that was missing, and it lives in `tests/` because `eval/` is the
frozen corpus and the deterministic offline harness.

## The scenario, and what driving it found

`eval/corpus/dual_incident.json`, whose own description is what the appliance is supposed to do:

> Two unrelated incidents overlap in time on disjoint NEs; must stay separate.

Sixteen events, four devices, two ground-truth incidents, 0.3 s apart. **Not** replayed as a batch:
the receiver stamps a trap on arrival, so sending them with their real gaps is what puts the
correlator's time window in the loop rather than around it.

**They do not stay separate.** A real appliance puts all sixteen alarms in one situation within
five seconds, and the same corpus replayed offline scores `over_merge_rate 1.0` and `ari 0.0` for
this scenario while `make eval` reports `pairwise_f1 1.0000` in aggregate — because a sixteen-event
scenario is 0.2 % of a corpus dominated by a 1 051-event storm. That is F76, and it is F61's
arithmetic arriving at the product: `MIN_EDGE_N` is cleared by six ordinary alarms.

So `test_the_two_incidents_are_merged_into_one_situation_and_that_is_a_defect` pins the failure
rather than the requirement, deliberately and loudly, and its message says what to do the day it
goes red. Repairing the correlator is F61's own disposition — *"the next release that touches the
correlator owns it"* — and touching it here would move `eval`'s frozen hash and the trap path in a
release that is about neither.

## Determinism, honestly

A live appliance stamps wall-clock times, so nothing containing a timestamp can be byte-identical
and this file never claims one is. What is compared across two runs in two processes is a
**canonical projection**: each situation as the sorted set of `(device, class OID, instance)` of
its members, the whole list sorted. It carries no clock, no row id and no ordering that arrival
could perturb, and it is exactly the thing the appliance is supposed to decide.

## The ground-truth prohibition

`PREREGISTRATION-0.14.0.md` §1: a label the machine produced does not judge the machine. This test
reads `truth` to *check* the appliance, which is legitimate, and the appliance cannot read it back
because it is a different process reached only through a socket — but "cannot" is a claim, so
`test_the_wire_carries_no_ground_truth` asserts it of the actual bytes rather than of the design.
"""

from __future__ import annotations

import json
import socket
import sys
import time
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
for _extra in (REPO_ROOT / "eval", REPO_ROOT / "tools"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from simulation.appliance import Appliance  # noqa: E402

import trap_replay  # noqa: E402

SCENARIO = REPO_ROOT / "eval" / "corpus" / "dual_incident.json"

#: How long to wait for the last trap to become an alarm and settle into a situation. The engine
#: batches on a short cadence; this is a ceiling with a poll inside it, never a fixed sleep.
SETTLE_TIMEOUT_S = 30.0


def _to_wire(ip: str) -> str:
    """`203.0.113.4` -> `127.0.113.4`. **The transport rewrite, and where it now lives.**

    The corpus is addressed in TEST-NET-3, and a UDP source address must be *bindable*: this host
    has no interface in `203.0.113.0/24`, so `sendto` from it fails and every device would arrive
    as `127.0.0.1`, four NEs silently becoming one.

    **v0.18.0: this is `trap_replay.loopback_alias`, not a copy of it (F128).** Until this release
    the rewrite existed *here only*, so this file drove the scenario correctly while
    `tools/trap_replay.py` — the command the README's quickstart prints and `make replay` runs —
    collapsed the same four devices into one on every machine. The one test that drives this
    scenario over a socket had already worked around the defect and therefore could not see it.
    Delegating means the test exercises the tool's rule: if the tool regresses, this goes red.
    """
    return trap_replay.loopback_alias(ip)


def _events() -> list[dict[str, Any]]:
    return sorted(
        json.loads(SCENARIO.read_text(encoding="utf-8"))["events"],
        key=lambda e: float(e["delay"]),
    )


def _truth_by_wire_device() -> dict[str, str]:
    """`{wire source: situation_key}`. Every source in this corpus belongs to one incident."""
    out: dict[str, str] = {}
    for event in _events():
        key = event["truth"]["situation_key"]
        wire = _to_wire(event["source"])
        assert out.setdefault(wire, key) == key, f"{wire} spans two incidents; the lookup is wrong"
    return out


def _free_port(kind: int) -> int:
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _send_over_udp(target: tuple[str, int]) -> int:
    """Sixteen real SNMPv2c PDUs, from four bindable source addresses, **spread over time**.

    **Driven through `trap_replay.Sender`, which is the point (F128).** This used to manage its
    own sockets precisely so it could let a bind failure raise, because the shipped `Sender`
    suppressed it. Now the shipped `Sender` raises too, so the test can use the real thing — and
    a future change that reintroduces the silent fallback fails here instead of being absorbed.
    """
    sender = trap_replay.Sender(target)
    started = time.monotonic()
    try:
        for event in _events():
            wait = float(event["delay"]) - (time.monotonic() - started)
            if wait > 0:
                time.sleep(wait)
            payload = trap_replay.encode_trap(
                event["trap_oid"],
                event.get("varbinds", []),
                "public",
                uptime_ticks=int((time.monotonic() - started) * 100),
            )
            sender.send(_to_wire(event["source"]), payload)
    finally:
        sent = sender.sent
        assert sender.sources_used() == 4, (
            f"{sender.sources_used()} source address(es) went on the wire, not 4: the sender "
            "collapsed distinct devices, which is F128"
        )
        sender.close()
    return sent


def _member_key(alarm: dict[str, Any]) -> tuple[str, str, str]:
    """One member, with nothing in it that a clock or a row id could move."""
    return (
        str(alarm["device_ip"]),
        str(alarm["class_oid"]),
        str(alarm.get("instance") or ""),
    )


class Drive:
    """One appliance's whole life: booted, fed, read back, stopped."""

    def __init__(self) -> None:
        self.projection: list[tuple[tuple[str, str, str], ...]] = []
        self.details: list[dict[str, Any]] = []
        self.stats: dict[str, Any] = {}
        self.sent = 0
        self.viewer_refusals: dict[str, int] = {}
        self.viewer_devices: set[str] = set()


def _drive(tmp: Path, name: str) -> Drive:
    out = Drive()
    appliance = Appliance(
        str(tmp / f"{name}.db"), _free_port(socket.SOCK_DGRAM), _free_port(socket.SOCK_STREAM)
    )
    with appliance:
        admin = appliance.sign_in()
        viewer = Appliance.mint(admin, f"{name}-viewer", "viewer")

        out.sent = _send_over_udp(("127.0.0.1", appliance.trap_port))

        deadline = time.monotonic() + SETTLE_TIMEOUT_S
        seen = -1
        while time.monotonic() < deadline:
            stats = admin.get("/api/stats")
            if stats["active_alarms"] == out.sent and stats["active_alarms"] == seen:
                break
            seen = stats["active_alarms"]
            time.sleep(1.0)
        out.stats = admin.get("/api/stats")

        # **Open situations only, and read as admin.** A merged situation keeps its id with zero
        # members so that anything referring to it still resolves (`operate.md` §5), and including
        # those would put empty tuples in the projection. Admin because `shaping.shape` coarsens a
        # device address to its /24 below editor — which is the visibility model working, and is
        # asserted separately below rather than silently flattening the projection.
        for row in sorted(admin.get("/api/situations?status=new"), key=lambda s: int(s["id"])):
            detail = admin.get(f"/api/situations/{row['id']}")
            out.details.append(detail)
            out.projection.append(tuple(sorted(_member_key(a) for a in detail["alarms"])))
        out.projection.sort()

        # RBAC and visibility on the live surface, not on a constructed request: a viewer token
        # reading over TCP, through the same perimeter a browser meets.
        for row in viewer.get("/api/situations?status=new"):
            detail = viewer.get(f"/api/situations/{row['id']}")
            out.viewer_devices |= {str(a["device_ip"]) for a in detail["alarms"]}
        for path in ("/api/users", "/api/audit"):
            out.viewer_refusals[path] = viewer.status_of("GET", path)
    return out


@pytest.fixture(scope="module")
def drives(tmp_path_factory: pytest.TempPathFactory) -> tuple[Drive, Drive]:
    """Two appliances, booted one after the other, each on its own empty database.

    Module-scoped because booting a process, feeding it for five seconds and letting it settle is
    the expensive part, and every assertion below reads the same two outcomes.
    """
    tmp = tmp_path_factory.mktemp("operation")
    return _drive(tmp, "first"), _drive(tmp, "second")


# -- the guard on the guard ----------------------------------------------------------------------


def test_the_transport_rewrite_is_a_bijection() -> None:
    """Two devices collapsing onto one address would not fail — it would quietly measure a
    different network. Asserted over the addresses this corpus contains, not argued from octets."""
    sources = sorted({event["source"] for event in _events()})
    assert len(sources) > 1
    wire = [_to_wire(ip) for ip in sources]
    assert len(set(wire)) == len(set(sources)), "the rewrite collapsed two devices onto one address"
    assert all(address.startswith("127.") for address in wire)


def test_the_wire_carries_no_ground_truth() -> None:
    """**The prohibition, asserted of the bytes** (PREREGISTRATION-0.14.0.md §1).

    The appliance is a separate process reached only through a socket, so it *cannot* read the
    generator's answer — but "cannot" is a claim about a design, and this checks the encoding. Not
    one of the truth keys, nor the word `truth`, appears in any datagram this test sends.
    """
    keys = {event["truth"]["situation_key"] for event in _events()}
    assert keys, "the corpus carries no ground truth, so this test is checking nothing"
    for event in _events():
        payload = trap_replay.encode_trap(event["trap_oid"], event.get("varbinds", []), "public", 1)
        for key in keys:
            assert key.encode() not in payload, f"{key!r} reached the wire"
        assert b"truth" not in payload
        assert b"is_root" not in payload


# -- the drive -----------------------------------------------------------------------------------


def test_a_real_appliance_ingests_every_trap_off_a_real_socket(drives: tuple[Drive, Drive]) -> None:
    """Sixteen datagrams in, sixteen alarms out, counted by the appliance's own receiver."""
    first, _second = drives
    assert first.sent == 16
    receiver = first.stats["receiver"]
    assert receiver["received"] == 16, receiver
    assert receiver["accepted"] == 16, receiver
    assert receiver["denied"] == 0 and receiver["quarantined"] == 0, receiver
    assert first.stats["devices"] == 4, "four bindable sources must be four network elements"
    assert first.stats["active_alarms"] == 16, first.stats


def test_no_situation_carries_members_of_two_ground_truth_incidents(
    drives: tuple[Drive, Drive],
) -> None:
    """**The purity assertion F76's placeholder stood in for** (v0.18.0).

    `dual_incident.json` describes itself as *"Two unrelated incidents overlap in time on disjoint
    NEs; must stay separate."* Until this release they did not: a real appliance fed those sixteen
    traps over a real socket put **all of them in one situation** inside five seconds, and
    `test_the_two_incidents_are_merged_into_one_situation_and_that_is_a_defect` pinned that
    failure on purpose, with a message saying to replace it with this assertion once the
    correlator was repaired. This is that replacement.

    **What repaired it**, measured rather than asserted: the two incidents are `1.3.6.1.4.1.1271`
    (Ciena) and `1.3.6.1.4.1.2636` (Juniper), disjoint at the enterprise arc, and v0.18.0 stops
    applying the *learned* cross-element affinity term to a pair that is on two different
    elements and in two different subtrees (`scoring.cross_subtree_elements`, F76/F135). A score
    threshold could not have done it — the seven cross-incident links scored 0.5857 to 0.7243
    against within-incident links at 0.6161 to 0.7684, overlapping, with one pair either side of the
    boundary at 0.7134 and 0.7131.

    Both halves are asserted. Purity alone would pass on an appliance that formed sixteen
    singletons, so coverage — every incident reaching a situation — is checked first.
    """
    first, _second = drives
    truth = _truth_by_wire_device()
    assert first.projection, "no situation formed, so this test measured nothing"
    per_situation = [
        {truth[device] for device, _cls, _instance in members} for members in first.projection
    ]
    represented = set().union(*per_situation)
    assert represented == set(truth.values()), (
        f"only {sorted(represented)} of {sorted(set(truth.values()))} reached a situation"
    )
    mixed = [sorted(keys) for keys in per_situation if len(keys) > 1]
    assert not mixed, (
        f"{len(mixed)} situation(s) mix two ground-truth incidents: {mixed}. `dual_incident` "
        "requires them to stay separate, and v0.18.0 made that hold — this is F76 returning."
    )
    # …and the grouping is not shattered either: each incident is held together, not scattered
    # across a situation per alarm. Without this the assertion above is satisfied by doing nothing.
    assert len(first.projection) == len(set(truth.values())), (
        f"the appliance formed {len(first.projection)} open situation(s) for "
        f"{len(set(truth.values()))} ground-truth incident(s); the two incidents must each be one "
        "situation, not several"
    )


def test_every_link_decomposes_into_terms_that_sum_to_its_score(
    drives: tuple[Drive, Drive],
) -> None:
    """Principle 2, end to end over HTTP: *"every decision decomposes into per-feature
    contributions that sum to the score exactly."*

    Read off the payload the console renders, on an appliance that learned this network from the
    wire — not from a scorer called directly with constructed features.
    """
    first, _second = drives
    links = [link for detail in first.details for link in detail.get("links", [])]
    assert links, "no link was explained, so the contract was not exercised"
    for link in links:
        terms = link["terms"]
        assert {t["name"] for t in terms} == {"temporal", "class_affinity", "entity_affinity"}
        total = sum(float(t["contribution"]) for t in terms)
        assert abs(total - float(link["score"])) < 1e-9, (link["score"], terms)


def test_a_viewer_token_is_refused_the_admin_routes_over_tcp(drives: tuple[Drive, Drive]) -> None:
    """The perimeter, met the way a client meets it, by a bearer token minted over the API."""
    first, _second = drives
    assert first.viewer_refusals == {"/api/users": 403, "/api/audit": 403}, first.viewer_refusals
    # The control: the same token DID read the situations and their details, so the refusals above
    # are about those two routes rather than about a token that could not read anything.
    assert first.viewer_devices, "the viewer token read no situation, so the refusals prove nothing"


def test_a_viewer_sees_the_network_coarsened_to_a_prefix(drives: tuple[Drive, Drive]) -> None:
    """`shaping.shape` coarsens a device address below editor, and the live surface does it too.

    Four distinct network elements reach an admin as four addresses and a viewer as one `/24`. It
    is asserted here because the projection every other test in this file reads is taken as admin,
    and a reader is entitled to know that the choice of principal changes what the payload says.
    """
    first, _second = drives
    admin_devices = {device for members in first.projection for device, _c, _i in members}
    assert len(admin_devices) == 4, admin_devices
    assert first.viewer_devices == {"127.0.113.0/24"}, first.viewer_devices


def test_two_appliances_decide_the_same_thing(drives: tuple[Drive, Drive]) -> None:
    """**Determinism, on the claim that can honestly be made.**

    Two processes, two empty databases, the same sixteen datagrams over the same real socket at the
    same real gaps. Wall-clock stamps differ and always will, so what is compared is the canonical
    projection: each situation as the sorted `(device, class, instance)` of its members, sorted.
    That is what the appliance decided, with nothing in it a clock or a row id could move.
    """
    first, second = drives
    assert first.projection, "the first drive formed no situation"
    assert first.projection == second.projection, (
        "two appliances given the same traps decided differently:\n"
        f"  first:  {first.projection}\n  second: {second.projection}"
    )
    # The projection must be able to tell two outcomes apart, or the equality above is empty. It
    # is not a set of identical tuples and it is not a list of empty ones.
    assert len(set(first.projection)) == len(first.projection)
    assert all(members for members in first.projection), first.projection
    assert sum(len(members) for members in first.projection) == first.sent


def test_the_appliance_answers_healthz_and_reports_its_version(drives: tuple[Drive, Drive]) -> None:
    """The one route a deployment checks. Driven here because everything above it is HTTP too, and
    a drive that never asked would leave the process's own liveness unasserted."""
    from netcorenoc import __version__

    first, _second = drives
    assert first.stats["queue_depth"] == 0, "the queue never drained"
    assert __version__  # the version the appliance reports is the one this tree carries
