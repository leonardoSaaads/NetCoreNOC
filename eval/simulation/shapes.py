"""The six registered shapes of `PREREGISTRATION-0.14.0.md` §5.1, one builder each.

Split from `generator.py` at the 400-line rule, on a seam that was already there: this module
answers *what each shape is*, and `generator.py` answers *how an increment is assembled from them*.
Every builder is a pure function of `(incident, base)` and returns `Emission`s the DSL expands.

The trap classes, the discriminator and the decoy varbinds live in `generator.py` and arrive here by
import, so there is exactly one place each OID is written down.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simulation.generator import (
    CARD_FAIL,
    FIBRE_LOF,
    INCREMENT_INCIDENTS,
    LINK_FLAP,
    PON_DYING_GASP,
    PON_LOS,
    PORT_DOWN,
    POWER_ALARM,
    RING_EAST,
    RING_NORTH,
    RING_SOUTH,
    RING_WEST,
    SEED,
    SPREAD_DELAYS,
    STORM_ONUS,
    device,
    draw,
    emit,
)

from scenario_dsl import Emission

__all__ = [
    "flap_during_incident",
    "independent_overlap",
    "merge_chain",
    "quiet_noise",
    "spread_beyond_tau",
    "storm_concealing",
]


# -- the six shapes -----------------------------------------------------------------------------


def independent_overlap(incident: int, base: float) -> list[Emission]:
    """**30 %.** Two independent faults on distant NEs, starting within `TAU_S`.

    Temporal term high, entity affinity low, so the pair sits near the threshold — and a model that
    merges them raises `over_merge_rate`. Two truth keys, deliberately: a formed situation
    containing both is a **mixed** bag, which is where an asserting bag comes from.
    """
    first = device(incident, 11, 1)
    second = device(incident, 12, 1)
    gap = 4.0 + draw(SEED, incident, 1, 200) / 10.0  # 4.0 .. 23.9 s, inside TAU_S
    out = [
        emit(
            first,
            CARD_FAIL,
            "card-1",
            f"i{incident}-a",
            base,
            incident=incident,
            salt=1,
            severity="critical",
            is_root=True,
        ),
    ]
    out += [
        emit(
            first,
            PORT_DOWN,
            f"card-1-port-{p}",
            f"i{incident}-a",
            base + 0.3 + p * 0.2,
            incident=incident,
            salt=2 + p,
        )
        for p in range(3)
    ]
    out.append(
        emit(
            second,
            FIBRE_LOF,
            "span-7",
            f"i{incident}-b",
            base + gap,
            incident=incident,
            salt=9,
            severity="critical",
            is_root=True,
        )
    )
    out += [
        emit(
            second,
            PORT_DOWN,
            f"span-7-if-{p}",
            f"i{incident}-b",
            base + gap + 0.4 + p * 0.2,
            incident=incident,
            salt=10 + p,
        )
        for p in range(2)
    ]
    return out


def spread_beyond_tau(incident: int, base: float) -> list[Emission]:
    """**20 %.** One fault on one NE whose alarms spread past `TAU_S`.

    Temporal term low, entity affinity high (same NE, so structurally 1.0), so the pair sits on the
    other side of the threshold and a model that separates them raises `under_merge_rate`. One truth
    key: every alarm belongs to the same situation however far apart they arrive.
    """
    node = device(incident, 13, 1)
    return [
        emit(
            node,
            POWER_ALARM,
            "psu-a",
            f"i{incident}-a",
            base + delay,
            incident=incident,
            salt=20 + index,
            severity="critical" if index == 0 else "major",
            is_root=index == 0,
        )
        for index, delay in enumerate(SPREAD_DELAYS)
    ]


def storm_concealing(incident: int, base: float) -> list[Emission]:
    """**15 %.** A dying-gasp storm hiding a simultaneous, unrelated fault.

    Fifty-six ONUs clear `learn.STORM_ALARMS` (50), so `STORM_DAMPING` (0.1) applies and
    `MAX_LINKS_PER_ALARM` (5) truncates. The concealed fault is a fibre cut on a different NE at the
    same moment — the case an operator most wants the appliance to keep separate, and the one a
    storm is most likely to swallow.
    """
    olt = device(incident, 14, 1)
    cut = device(incident, 15, 1)
    onset = 1.0 + draw(SEED, incident, 30, 60) / 10.0
    out = [
        emit(
            olt,
            PON_LOS,
            "pon-1",
            f"i{incident}-storm",
            base,
            incident=incident,
            salt=31,
            severity="critical",
            is_root=True,
        )
    ]
    out += [
        emit(
            olt,
            PON_DYING_GASP,
            f"onu-{n}",
            f"i{incident}-storm",
            base + 0.2 + n * 0.05,
            incident=incident,
            salt=40 + n,
            severity="major",
        )
        for n in range(STORM_ONUS)
    ]
    out.append(
        emit(
            cut,
            FIBRE_LOF,
            "span-2",
            f"i{incident}-cut",
            base + onset,
            incident=incident,
            salt=200,
            severity="critical",
            is_root=True,
        )
    )
    out.append(
        emit(
            cut,
            PORT_DOWN,
            "span-2-if-0",
            f"i{incident}-cut",
            base + onset + 0.5,
            incident=incident,
            salt=201,
        )
    )
    return out


def flap_during_incident(incident: int, base: float) -> list[Emission]:
    """**15 %.** A flapping port during a real incident, on the same NE.

    The flap suppressor against legitimate signal: the same alarm raised and cleared repeatedly on
    one interface, while a genuine card failure on the same device is unfolding. Two truth keys, so
    a bag holding both is mixed — but unlike the overlap shape, the entity affinity is **1.0**,
    which is what makes this hard rather than merely adversarial.
    """
    node = device(incident, 16, 1)
    out = [
        emit(
            node,
            CARD_FAIL,
            "card-9",
            f"i{incident}-a",
            base,
            incident=incident,
            salt=60,
            severity="critical",
            is_root=True,
        )
    ]
    out += [
        emit(
            node,
            PORT_DOWN,
            f"card-9-port-{p}",
            f"i{incident}-a",
            base + 0.4 + p * 0.3,
            incident=incident,
            salt=61 + p,
        )
        for p in range(3)
    ]
    out += [
        emit(
            node,
            LINK_FLAP,
            "gi0-0-7",
            f"i{incident}-flap",
            base + 2.0 + n * 3.0,
            incident=incident,
            salt=70 + n,
            severity="warning",
        )
        for n in range(6)
    ]
    return out


def ringdevice(incident: int, role: int) -> str:
    """The merge-chain ring's NEs — **fixed, and reused by every merge-chain incident.**

    Every other shape gives each incident its own network elements. This one cannot, and the reason
    is arithmetic rather than taste: `learn.entity_affinity` returns the learned
    `E.npmi(ne_i, ne_j)` for a cross-NE pair and **zero until that pair has mass >= MIN_EDGE_N**
    (5). Fresh NEs per incident mean every ring pair is seen once and `E` is zero forever, so
    nothing can ever bridge two NEs and a merge chain is unbuildable.

    Two rings, because there are two merge-chain slots per increment and two concurrent incidents on
    one ring would merge into each other. Increments are more than `WINDOW_S` apart, so the same
    ring in two increments still forms two situations.
    """
    slot = (incident % INCREMENT_INCIDENTS) % 2
    return f"10.19.{slot + 1}.{role + 1}"


def merge_chain(incident: int, base: float) -> list[Emission]:
    """**10 %.** A situation merge chain of length >= 2 — **the case no existing corpus contains.**

    `DATA-LINEAGE.md` §4 records that no corpus holds a merge edge, which is why v0.10.1's
    byte-frozen reports could not have verified incident-identity consolidation. `store/shadow.py`
    says it from the other side: a `COALESCE(s.merged_into, f.situation_id)` is **one hop**, and
    only a chain of length >= 2 distinguishes a one-hop resolution from a transitive one.

    ## Why the order of the bridges is the whole design

    `engine._assign_situation` keeps `min(sids)` — **the lowest situation id always survives**. So a
    chain of length 2 needs the *lowest* id to be absorbed **last**:

        t=0    ring role 0 alarms       -> S1   (lowest id)
        t=6    ring role 1 alarms       -> S2
        t=12   ring role 2 alarms       -> S3
        t=20   RING_EAST/WEST bridge S2 and S3   -> S3.merged_into = S2   (S2 < S3)
        t=26   RING_NORTH/SOUTH bridge S1 and S2 -> S2.merged_into = S1   (S1 < S2)

    leaving `S3 -> S2 -> S1`. Bridging the lowest pair first gives two edges both pointing at S1 —
    two merges and a chain of length **one**, which is what the first version of this shape produced
    and what `measure.py` caught. The measurement is the reason this docstring can be specific.

    The bridge classes fire only here and always in pairs, and the ring's NEs are fixed, so both `A`
    and `E` accumulate across increments. The chain therefore appears **once the appliance has
    learned enough**, not in the first increment — which is the honest behaviour of a system that
    starts knowing nothing, and it is reported as such rather than engineered away.
    """
    first = ringdevice(incident, 0)
    second = ringdevice(incident, 1)
    third = ringdevice(incident, 2)
    key = f"i{incident}-a"
    out: list[Emission] = []
    for index, node in enumerate((first, second, third)):
        out += [
            emit(
                node,
                PON_LOS,
                f"ring-{index}",
                key,
                base + index * 6.0,
                incident=incident,
                salt=80 + index * 3,
                severity="critical",
                is_root=index == 0,
            ),
            emit(
                node,
                PORT_DOWN,
                f"ring-{index}-if-0",
                key,
                base + index * 6.0 + 0.4,
                incident=incident,
                salt=81 + index * 3,
            ),
        ]
    # Bridge the two HIGHER situations first, so the lowest id absorbs an already-merged one.
    out.append(
        emit(
            second,
            RING_EAST,
            "ring-1",
            key,
            base + 20.0,
            incident=incident,
            salt=90,
            severity="critical",
        )
    )
    out.append(
        emit(
            third,
            RING_WEST,
            "ring-2",
            key,
            base + 20.5,
            incident=incident,
            salt=91,
            severity="critical",
        )
    )
    out.append(
        emit(
            first,
            RING_NORTH,
            "ring-0",
            key,
            base + 26.0,
            incident=incident,
            salt=92,
            severity="critical",
        )
    )
    out.append(
        emit(
            second,
            RING_SOUTH,
            "ring-1",
            key,
            base + 26.5,
            incident=incident,
            salt=93,
            severity="critical",
        )
    )
    return out


def quiet_noise(incident: int, base: float) -> list[Emission]:
    """**10 %.** Background noise that should produce no situation at all.

    Isolated alarms on their own NEs, far enough apart in time that nothing correlates. Each is its
    own truth key. This is the share of the network that must stay quiet, and a model that starts
    grouping it is over-merging in the place an operator will notice first.
    """
    return [
        emit(
            device(incident, 18, index + 1),
            POWER_ALARM,
            f"env-{index}",
            f"i{incident}-n{index}",
            base + index * 40.0,
            incident=incident,
            salt=100 + index,
            severity="warning",
            is_root=True,
        )
        for index in range(2)
    ]
