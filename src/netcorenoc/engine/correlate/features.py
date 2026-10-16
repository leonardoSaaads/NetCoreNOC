"""The pair feature vector (v2): everything the appliance already knows about two alarms.

Until v0.26.0 every model family this project could run — additive, logistic, tree, forest,
boosted — was fed the same three numbers: time decay, class affinity, entity affinity. Five
estimators over three numbers carry the information of three numbers. This vector is what the
appliance knows and the model could not see.

**One function builds it** (:func:`vectors`; :func:`vector` is its one-pair case). The engine
calls it on the ingest path, the offline replay calls it on generated streams, and training reads
the rows the replay wrote — so the feature a model was trained on and the feature it is served are
the same arithmetic by construction, and `tests/correlation/test_features.py` replays a stream
through both paths to prove it.

## The features, and what each is for

Every feature is a **relation between the two alarms**, never an identifier: an element id, a class
id or an OID in a feature vector teaches a model one customer's estate (migration 0008, rule 2).

* ``dt`` — seconds between the two activations, capped at an hour; *fan-out is fast, coincidence is
  uniform*.
* ``same_ne`` — both on one network element; *the strongest structural prior there is*.
* ``same_class`` — the identical trap type; *both ends of one span raise the same trap*.
* ``oid_arcs`` — leading OID arcs the two trap types share, on arc boundaries; *same vendor module ≈
  same subsystem*.
* ``class_affinity`` — learned NPMI of the two classes (``A``); *the stream's own opinion, kept*.
* ``entity_affinity`` — learned NPMI of the two elements (``E``), structural within one element;
  *the stream's own opinion, kept*.
* ``ne_episodes`` — separate past occasions these two elements co-failed; ***memory**: relatedness
  is recurrence*.
* ``class_episodes`` — separate past occasions these two classes co-occurred; *memory at the class
  level*.
* ``item_episodes`` — separate past occasions *this fault here* and *that fault there* co-occurred;
  *memory at its finest: the same fault again*.
* ``severity`` — the less severe of the two X.733 ranks, 5 when either is unknown; *two criticals at
  once are rarely a coincidence*.
* ``burst`` — live alarms in the correlation window when the decision was made; *in a storm
  everything co-occurs*.
* ``chatter`` — the busier of the two fingerprints' activations in the past hour; *a chattering port
  co-occurs with everything*.
* ``degree`` — the larger number of distinct elements either has ever co-failed with; *a hub's
  co-failures say less each*.
* ``cross_ref`` — one alarm's varbinds name the other's management address; *topology the trap
  itself carried*.
* ``hour`` — UTC hour of the newer alarm; *offered to the ablation; see ADR #409 for its fate*.

``same_oid_root`` (v0.18.0) is ``oid_arcs >= 7`` and is no longer carried separately.

## Cost

Constant per pair: two dictionary reads per episode table, one bounded loop over OID arcs, and
arithmetic. No clock, no I/O, no allocation beyond the returned tuple.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type-only imports; no runtime edge
    from netcorenoc.engine.correlate.correlate import WindowAlarm
    from netcorenoc.engine.correlate.episodes import EpisodeMemory
    from netcorenoc.engine.correlate.learn import Learner

__all__ = [
    "FEATURE_NAMES",
    "MAX_ARCS",
    "MAX_DT_S",
    "MAX_REFS",
    "UNKNOWN_SEVERITY",
    "common_arcs",
    "references",
    "vector",
    "vectors",
]

FEATURE_NAMES: tuple[str, ...] = (
    "dt",
    "same_ne",
    "same_class",
    "oid_arcs",
    "class_affinity",
    "entity_affinity",
    "ne_episodes",
    "class_episodes",
    "item_episodes",
    "severity",
    "burst",
    "chatter",
    "degree",
    "cross_ref",
    "hour",
)

MAX_DT_S = 3600.0
MAX_ARCS = 16
UNKNOWN_SEVERITY = 5


#: At most this many addresses are kept from one trap's varbinds: a bound on per-activation work.
MAX_REFS = 4


def references(values: list[str], source: str) -> frozenset[str]:
    """The IPv4 addresses a trap's varbind values name, other than its own source.

    Parsed by hand rather than with `ipaddress` because it runs once per activation on every
    varbind: four dot-separated decimal octets, each 0-255, and nothing else. At most
    :data:`MAX_REFS` are kept.
    """
    found: set[str] = set()
    for value in values:
        parts = value.split(".")
        if len(parts) != 4 or value == source:
            continue
        if all(p.isdigit() and len(p) <= 3 and int(p) <= 255 for p in parts):
            found.add(value)
            if len(found) >= MAX_REFS:
                break
    return frozenset(found)


def common_arcs(a: tuple[str, ...], b: tuple[str, ...]) -> int:
    """How many leading arcs two OIDs share — compared **arc by arc**, never as strings.

    `1.3.6.1.4.1.2011.1.2` and `1.3.6.1.4.1.2011.1.12` share seven arcs, not eight: a string
    prefix would count the `1` of `12` (Appendix B, *"a prefix that is not a subtree"*).
    """
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y or n >= MAX_ARCS:
            break
        n += 1
    return n


def vector(
    new: WindowAlarm,
    old: WindowAlarm,
    learner: Learner,
    episodes: EpisodeMemory,
    burst: int,
) -> tuple[float, ...]:
    """The v2 feature vector for one candidate pair, in :data:`FEATURE_NAMES` order."""
    return vectors(new, (old,), learner, episodes, burst)[0]


def vectors(
    new: WindowAlarm,
    olds: Sequence[WindowAlarm],
    learner: Learner,
    episodes: EpisodeMemory,
    burst: int,
) -> list[tuple[float, ...]]:
    """:func:`vector` for each of ``olds``, in order — **the one implementation of the arithmetic**.

    ADR #448: the correlator scores up to `MAX_CANDIDATES` + `retrieval.MAX_RECALL` candidates per
    activation, and a storm puts many of them on the same few classes and elements. Every lookup
    that depends only on the candidate's class or element is therefore made once per distinct value
    and reused. That is exact rather than approximate: nothing the lookups read moves while one
    activation's candidates are scored (the correlator observes after it scores), so a repeated
    lookup could only have returned the same number.
    """
    now = new.ts
    hour = float(int(now // 3600) % 24)
    density = float(burst)
    new_degree = episodes.degree(new.device_id)
    new_item = (new.device_id, new.class_id)
    class_affinity: dict[int, float] = {}
    entity_affinity: dict[tuple[int, int], float] = {}
    ne_episodes: dict[int, float] = {}
    class_episodes: dict[int, float] = {}
    item_episodes: dict[tuple[int, int], float] = {}
    degree: dict[int, float] = {}
    out: list[tuple[float, ...]] = []
    for old in olds:
        dt = abs(new.ts - old.ts)
        ranks = (new.severity_rank, old.severity_rank)
        severity = UNKNOWN_SEVERITY if min(ranks) < 0 else max(ranks)
        cls, ne = old.class_id, old.device_id
        a = class_affinity.get(cls)
        if a is None:
            a = class_affinity[cls] = learner.class_affinity(new.class_id, cls)
        entity = (old.entity_id, ne)
        e = entity_affinity.get(entity)
        if e is None:
            e = entity_affinity[entity] = learner.entity_affinity(
                new.entity_id, new.device_id, old.entity_id, ne
            )
        ne_count = ne_episodes.get(ne)
        if ne_count is None:
            ne_count = ne_episodes[ne] = float(episodes.ne_prior(new.device_id, ne, now))
        cls_count = class_episodes.get(cls)
        if cls_count is None:
            cls_count = class_episodes[cls] = float(episodes.cls_prior(new.class_id, cls, now))
        item = (ne, cls)
        item_count = item_episodes.get(item)
        if item_count is None:
            item_count = item_episodes[item] = float(episodes.item_prior(new_item, item, now))
        hub = degree.get(ne)
        if hub is None:
            hub = degree[ne] = float(max(new_degree, episodes.degree(ne)))
        out.append(
            (
                dt if dt < MAX_DT_S else MAX_DT_S,
                1.0 if new.device_id == ne else 0.0,
                1.0 if new.class_id == cls else 0.0,
                float(common_arcs(new.arcs, old.arcs)),
                a,
                e,
                ne_count,
                cls_count,
                item_count,
                float(severity),
                density,
                float(max(new.chatter, old.chatter)),
                hub,
                1.0 if (new.source in old.refs or old.source in new.refs) and new.source else 0.0,
                hour,
            )
        )
    return out
