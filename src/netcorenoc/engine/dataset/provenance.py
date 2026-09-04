"""**How a bag was formed** — recorded beside every training row, and consumed by nothing.

`PREREGISTRATION-0.16.0.md` §5, and the plan is explicit about the second half:

> **Registered: neither enters any model, any metric that decides, or any floor, in v0.16.0.**
> They are recorded because they cannot be recomputed later — the scores decay, membership mutates,
> and `0008`'s first rule applies. They are **reported** in the census, stratified.
>
> A build that supplies either to a scorer, a promotion input, or a verdict trigger has violated
> this plan.

Two quantities, and each says something a score alone does not:

* **the weakest link's margin over the threshold.** A grouping whose weakest pair cleared by 0.01
  is one scorer nudge from falling apart; one whose weakest cleared by 0.3 is not.
  `ui/app/views/parts/why.js` has shown an operator this number since #245; this is the server's own
  reading of it, taken at the instant of the gesture.
* **the bridge.** Situations are **connected components** while scoring is **pairwise**: A links B,
  B links C, and A and C share a situation even though A-C scored below the threshold. One weak
  bridge merges two incidents, which is F76 measured at the product. A bag held together by one
  frail edge is epistemically different from a densely-linked one, and today both enter training
  indistinguishable.

**Why deferred rather than used.** That difference is plausible, and plausibility is exactly the
kind of thing that looks right and has never been measured. Using it now would encode an unmeasured
belief as a weight, which is the error §2.1(c) of the v0.10.0 plan rejects under a different name.

**This module is the one a guard can name.** `tests/test_evidence_boundary.py` asserts that nothing
under `engine/correlate/`, `engine/model/` or `engine/evaluation/` imports it, so "recorded and not
consumed" is a property of the import graph rather than a promise in a docstring.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

__all__ = ["MIN_BRIDGE_SIDE", "BagProvenance", "bridge_min_side", "provenance"]

#: The smallest a bridge's smaller side may be for `bag_has_bridge` to read 1.
#:
#: **The plan registers "two parts each above a registered minimum size" and does not fix the
#: size**, so this constant is a build decision and is treated as one: it is recorded, it enters
#: nothing that decides, and the raw measurement is stored beside the boolean it produces
#: (`situation_event.bag_bridge_min_side`) so a later release that registers a different minimum can
#: recompute the answer instead of being stuck with this one. Whether 2 is right is an open question
#: for v0.16.1, written down rather than settled here — Part VIII resolves an analytical ambiguity
#: the plan does not answer to *report it*, never to decide it during implementation.
#:
#: 2 is the weakest possible claim: it excludes only the bridge that detaches a single leaf alarm,
#: which is every pendant edge in every situation and would make the flag true almost everywhere.
MIN_BRIDGE_SIDE = 2


@dataclass(frozen=True)
class BagProvenance:
    """How the bag this gesture asserts about was held together. **Four facts, no judgement.**"""

    link_count: int
    #: `min(score) - threshold` over the links inside the bag. `None` when the bag has no link (a
    #: one-member situation has no weakest pair) or when the threshold was not recorded — never
    #: 0.0, which would say something false about both.
    weakest_margin: float | None
    #: Members on the smaller side of the best bridge; `None` when the graph has no bridge.
    bridge_min_side: int | None

    @property
    def has_bridge(self) -> bool:
        """The plan's registered form: a bridge splitting the bag into two parts each big enough."""
        return self.bridge_min_side is not None and self.bridge_min_side >= MIN_BRIDGE_SIDE

    def as_columns(self) -> dict[str, float | int | None]:
        """The four `situation_event` columns, named.

        Written here; read by the census and by nothing else.
        """
        return {
            "bag_link_count": self.link_count,
            "bag_weakest_margin": self.weakest_margin,
            "bag_bridge_min_side": self.bridge_min_side,
            "bag_has_bridge": int(self.has_bridge),
        }


def bridge_min_side(members: Iterable[int], edges: Iterable[tuple[int, int]]) -> int | None:
    """The size of the smaller component the **best** bridge would produce, or `None`.

    A bridge is an edge whose removal increases the number of connected components. "Best" is the
    one whose smaller side is largest, because that is the bridge whose removal says the most: a
    pendant edge detaching one alarm is a bridge in every real situation and tells a reader nothing,
    while an edge holding two halves of a storm together is the thing F76 is about.

    Tarjan's low-link bridge search, iterative rather than recursive: a 1051-member storm is a real
    bag in this repository's own corpus and Python's recursion limit is 1000. Linear in nodes plus
    edges, and it runs once per gesture on the HTTP write path — thousands of times rarer than the
    ingest path it never touches.

    Returns `None` for a graph with no bridge, which includes every graph with fewer than two nodes
    and every 2-edge-connected one. `None` is not zero: *"there is no bridge"* and *"the bridge
    detaches nothing"* are different statements and only the first can be true.

    Self-loops and repeated edges are handled the way the graph means them: a repeated edge makes
    its endpoints 2-edge-connected, so it is **not** a bridge, and that is why edges are counted by
    multiplicity rather than collapsed into a set.
    """
    adjacency: dict[int, list[tuple[int, int]]] = {node: [] for node in members}
    edge_id = 0
    for a, b in edges:
        if a not in adjacency or b not in adjacency or a == b:
            continue  # an endpoint outside the bag says nothing about how the bag is held together
        adjacency[a].append((b, edge_id))
        adjacency[b].append((a, edge_id))
        edge_id += 1
    order: dict[int, int] = {}
    low: dict[int, int] = {}
    subtree: dict[int, int] = {}
    best: int | None = None
    counter = 0
    for root in adjacency:
        if root in order:
            continue
        # Each component is searched on its own, and its bridges are collected here rather than
        # compared as they are found: a bridge's two sides are `subtree[node]` and *the component
        # minus it*, and the component's size is not known until its root pops. So the sides are
        # compared after the `while` loop, against a number the loop's last iteration produced.
        found: list[int] = []
        # (node, parent edge id, index into that node's adjacency list). The third element is what
        # makes this iterative: it is where the neighbour loop resumes after a child returns.
        # Iterative because a 1051-member storm is a real bag in this repository's own corpus and
        # Python's default recursion limit is 1000.
        stack: list[list[int]] = [[root, -1, 0]]
        order[root] = low[root] = counter
        counter += 1
        subtree[root] = 1
        component = 1
        while stack:
            node, parent_edge, index = stack[-1]
            if index < len(adjacency[node]):
                stack[-1][2] += 1
                neighbour, via = adjacency[node][index]
                if via == parent_edge:
                    continue  # the edge we arrived by; a *repeated* edge has its own id and counts
                if neighbour in order:
                    low[node] = min(low[node], order[neighbour])
                    continue
                order[neighbour] = low[neighbour] = counter
                counter += 1
                subtree[neighbour] = 1
                stack.append([neighbour, via, 0])
                continue
            stack.pop()
            if not stack:
                component = subtree[node]  # the root popped: this is the component's size
                continue
            parent = stack[-1][0]
            low[parent] = min(low[parent], low[node])
            subtree[parent] += subtree[node]
            if low[node] > order[parent]:
                # `node`'s subtree hangs off the edge to `parent`, so removing that edge splits the
                # component into `subtree[node]` and everything else.
                found.append(subtree[node])
        for side in found:
            smaller = min(side, component - side)
            best = smaller if best is None else max(best, smaller)
    return best


def provenance(
    members: Iterable[int],
    edges: Iterable[tuple[int, int]],
    scores: Iterable[float],
    threshold: float | None,
) -> BagProvenance:
    """The four facts, from the bag's members and the links inside it. **Arithmetic only.**"""
    edge_list = list(edges)
    score_list = list(scores)
    return BagProvenance(
        link_count=len(edge_list),
        weakest_margin=(
            min(score_list) - threshold if score_list and threshold is not None else None
        ),
        bridge_min_side=bridge_min_side(members, edge_list),
    )
