"""**Why the census reads what it reads.** A shortfall with no diagnosis is a number, not a result.

`PREREGISTRATION-0.14.0.md` §5.3 requires the loop to report, per unmet floor, the shortfall. It
does not require it to explain it, and a release that stopped at *"asserting_bags: 10, floor 50"*
would be telling the truth and saying nothing. This module is the explanation, and every quantity in
it is an **additional observation** under §9:

> A quantity not in `PREREGISTRATION-0.10.0.md` §5 may be measured and reported under *"additional
> observations"* and may never support a conclusion in §8.

So: nothing here enters a metric, a verdict or a floor. It reads the store after the fact and says
what the appliance did. The conclusion in §8 is still §8.3's, reached from the census alone.

## What it measures, and why each one

* **The bag census** — per formed bag, its size and how many distinct ground-truth situation keys
  its members carry. An *asserting* bag is a `split` with `excluded_reconciled >= 1`, which under
  the labelling rule needs **two or more truth keys in one bag**. So the count of mixed bags is an
  upper bound on the asserting bags, and if it is 1 per increment the floor is arithmetic.
* **The cross-incident links** — every link whose two alarms belong to different generated
  incidents, with the three score terms the correlator wrote at decision time. The three `link`
  columns are persisted for exactly this reason: v0.2.0's explainability requirement is what makes
  a collapse diagnosable three releases later.
* **The learned NE mass** — `Learner.E`'s pair mass between the storm's OLT and every other NE.
  `learn.MIN_EDGE_N` (5.0) is the *only* guard that stops an unlearned NE pair from contributing
  entity affinity, so the mass against that number is the whole question.

**This reads ground truth** and that is legitimate here: §1 permits ground truth to measure the
*simulator*, and `tests/test_simulation.py` asserts by parsing that no runtime module can reach this
package at all.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simulation.generator import devices_of, shape_of

if TYPE_CHECKING:  # pragma: no cover - type-only
    from netcorenoc.store import Store

__all__ = ["BagCensus", "bag_census", "cross_incident_links", "render", "storm_mass"]

# `learn.MIN_EDGE_N`, transcribed by hand rather than imported. A diagnosis that read the constant
# it is checking would agree with itself whatever that constant became — `test_simulation.py` makes
# the same choice about the registered proportions, for the same reason.
MIN_EDGE_N = 5.0


class BagCensus:
    """Per formed bag: its size, and how many ground-truth situation keys it holds."""

    __slots__ = ("largest", "mixed", "pure", "sizes", "truth_keys")

    def __init__(self) -> None:
        self.sizes: list[int] = []
        self.truth_keys: list[int] = []
        self.pure = 0
        self.mixed = 0
        self.largest = 0

    def add(self, size: int, keys: int) -> None:
        self.sizes.append(size)
        self.truth_keys.append(keys)
        self.largest = max(self.largest, size)
        if keys > 1:
            self.mixed += 1
        else:
            self.pure += 1


async def bag_census(store: Store, truth: dict[tuple[str, str], str]) -> BagCensus:
    """Every unmerged situation, with the number of distinct truth keys its members carry.

    A key that does not resolve counts as its own key rather than being dropped: an unresolved
    lookup would otherwise make a mixed bag look pure, which is the direction that hides a problem.
    """
    census = BagCensus()
    cursor = await store.conn.execute(
        "SELECT s.id FROM situation s WHERE s.merged_into IS NULL ORDER BY s.id"
    )
    for (situation,) in await cursor.fetchall():
        rows = await store.conn.execute(
            "SELECT d.ip, c.oid FROM situation_alarm sa "
            "JOIN alarm a ON a.id = sa.alarm_id "
            "JOIN device d ON d.id = a.device_id "
            "JOIN alarm_class c ON c.id = a.class_id "
            "WHERE sa.situation_id = ?",
            (int(situation),),
        )
        members = [(str(r[0]), str(r[1])) for r in await rows.fetchall()]
        if not members:
            continue
        keys = {truth.get(member) or f"unresolved-{member[0]}-{member[1]}" for member in members}
        census.add(len(members), len(keys))
    return census


async def cross_incident_links(store: Store, increments: int) -> dict[str, Any]:
    """Every link joining two **different** generated incidents, with the terms that produced it.

    The three terms are read from the `link` row rather than recomputed. Recomputing them would read
    a learner whose masses have moved since the decision, which is the same reason `capture`
    persists what the champion saw instead of re-deriving it.
    """
    device_incident: dict[str, int] = {}
    for increment in range(increments):
        device_incident.update(devices_of(increment))

    cursor = await store.conn.execute(
        "SELECT l.score, l.term_t, l.term_a, l.term_e, da.ip, db.ip, ca.oid, cb.oid "
        "FROM link l "
        "JOIN alarm a ON a.id = l.alarm_a JOIN device da ON da.id = a.device_id "
        "JOIN alarm_class ca ON ca.id = a.class_id "
        "JOIN alarm b ON b.id = l.alarm_b JOIN device db ON db.id = b.device_id "
        "JOIN alarm_class cb ON cb.id = b.class_id "
        "ORDER BY l.id"
    )
    total = 0
    cross: list[tuple[float, float, float, float, str, str]] = []
    shapes: Counter[tuple[str, str]] = Counter()
    for score, term_t, term_a, term_e, ip_a, ip_b, oid_a, oid_b in await cursor.fetchall():
        total += 1
        incident_a = device_incident.get(str(ip_a))
        incident_b = device_incident.get(str(ip_b))
        if incident_a is None or incident_b is None or incident_a == incident_b:
            continue
        cross.append(
            (float(score), float(term_t), float(term_a), float(term_e), str(oid_a), str(oid_b))
        )
        shapes[tuple(sorted((shape_of(incident_a), shape_of(incident_b))))] += 1  # type: ignore[index]
    return {
        "links": total,
        "cross_incident": len(cross),
        "by_shape_pair": dict(shapes.most_common(8)),
        "max_term_e": max((row[3] for row in cross), default=0.0),
        "max_term_a": max((row[2] for row in cross), default=0.0),
        "examples": cross[:6],
    }


async def storm_mass(store: Store) -> dict[str, Any]:
    """`Learner.E`'s pair mass distribution, against the one guard that gates it.

    `learn.entity_affinity` returns the learned NPMI for a cross-NE pair **only** once the pair's
    mass reaches `MIN_EDGE_N`; below it the pair contributes exactly zero. So the question a
    collapse poses is not *"what did the learner believe"* but *"how many NE pairs got past 5.0 at
    all"*, and that is what this counts. The edge rows are the learner's own flushed state
    (`kind = 'device'`), not a re-derivation.
    """
    cursor = await store.conn.execute(
        "SELECT n, weight FROM edge WHERE kind = 'device' ORDER BY n DESC"
    )
    rows = [(float(r[0]), float(r[1])) for r in await cursor.fetchall()]
    trusted = [row for row in rows if row[0] >= MIN_EDGE_N]
    return {
        "ne_pairs": len(rows),
        "past_min_edge_n": len(trusted),
        "max_mass": max((mass for mass, _w in rows), default=0.0),
        "max_npmi_trusted": max((w for _n, w in trusted), default=0.0),
        "median_mass": sorted(mass for mass, _w in rows)[len(rows) // 2] if rows else 0.0,
    }


def render(census: BagCensus, links: dict[str, Any], mass: dict[str, Any]) -> str:
    """The diagnosis as the gate quotes it. Counts only; nothing here is an average of rates."""
    lines = [
        "===== additional observations (PREREGISTRATION-0.14.0.md §9) =====",
        "  -- bags formed, and how many ground-truth situations each holds --",
        f"    bags (unmerged situations) : {len(census.sizes)}",
        f"    of which PURE  (1 key)     : {census.pure}",
        f"    of which MIXED (>= 2 keys) : {census.mixed}   <- the upper bound on asserting bags",
        f"    largest bag                : {census.largest} members, "
        f"{max(census.truth_keys, default=0)} truth keys",
        "",
        "  -- links joining two DIFFERENT generated incidents --",
        f"    links written              : {links['links']}",
        f"    CROSS-incident             : {links['cross_incident']}",
        f"    largest entity term seen   : {links['max_term_e']:.4f}  (weight 0.35, so E up to "
        f"{links['max_term_e'] / 0.35:.3f})",
        f"    largest class  term seen   : {links['max_term_a']:.4f}  (weight 0.35, so A up to "
        f"{links['max_term_a'] / 0.35:.3f})",
    ]
    for pair, count in links["by_shape_pair"].items():
        lines.append(f"      {pair[0]:<22} x {pair[1]:<22} {count}")
    lines += [
        "",
        f"  -- learned NE-pair mass against MIN_EDGE_N = {MIN_EDGE_N} --",
        f"    NE pairs with any mass     : {mass['ne_pairs']}",
        f"    past MIN_EDGE_N (trusted)  : {mass['past_min_edge_n']}",
        f"    largest pair mass          : {mass['max_mass']:.3f}",
        f"    largest trusted NPMI       : {mass['max_npmi_trusted']:.4f}",
        f"    median pair mass           : {mass['median_mass']:.3f}",
    ]
    return "\n".join(lines)
