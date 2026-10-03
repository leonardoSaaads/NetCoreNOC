"""A model-decided link's decomposition, in the shape `/api/situations/{sid}` serves (v0.27.0).

The `link` table's `terms` column (`0026`) holds a trained model's whole explanation as canonical
JSON — its basis, base value and threshold, and ``[name, value, contribution]`` per term — because a
model has one term per feature and the three v0.2.0 columns hold only the formula's three
(DECISIONS #50). Until v0.27.0 no route read that column: a link a model decided reached the console
as three formula columns that did not sum to its score. `tests/ops/test_operation.py` found it on
the first build that packaged a model, end to end over a real socket.

This serves it, compactly. F145 measured what restating a term list on every link costs on a storm
(993 KiB of a 1 844 KiB response), so the names travel **once per situation** — a small table — and
each link carries only an index into it and its contributions. The formula's links are untouched:
their three columns already are the decomposition.

The contract a link then satisfies is **principle 2, per basis**: for the formula, the three
columns sum to the score; for a model, ``base + sum(phi)`` is the score (to the six decimals the
wire carries).
"""

from __future__ import annotations

import json
import math
from typing import Any

__all__ = ["DECIMALS", "compact", "scale_of", "threshold_of"]

#: Contributions are rounded to this many decimals on the wire: enough that ten of them sum to the
#: score within 1e-5, and short enough that a storm's links stay a fraction of what F145 removed.
DECIMALS = 6


def compact(links: list[dict[str, Any]]) -> list[list[str]]:
    """Turn each link's stored ``terms`` JSON into ``basis``, ``base``, ``threshold``, ``names``
    (an index into the returned table) and ``phi``. A link without one — the formula's — keeps its
    three columns only. An explanation that cannot be read is dropped, never guessed at: the link
    then shows its three columns, which is what it showed before this module existed."""
    table: list[list[str]] = []
    index: dict[tuple[str, ...], int] = {}
    for link in links:
        raw = link.pop("terms", None)
        if not raw:
            continue
        try:
            doc = json.loads(str(raw))
            terms = doc["terms"]
            names = tuple(str(t[0]) for t in terms)
            phi = [round(float(t[2]), DECIMALS) for t in terms]
            base = round(float(doc["base"]), DECIMALS)
            threshold = float(doc["threshold"])
            basis = str(doc["basis"])
        except (ValueError, KeyError, TypeError, IndexError):
            continue
        if not all(math.isfinite(v) for v in (*phi, base, threshold)):
            continue
        if names not in index:
            index[names] = len(table)
            table.append(list(names))
        link.update(basis=basis, base=base, threshold=threshold, names=index[names], phi=phi)
    return table


def threshold_of(links: list[dict[str, Any]], formula: float | None) -> float | None:
    """The threshold the console should measure the weakest link against: the model's when every
    link a model decided agrees on one, the formula's when no model decided any, and ``None`` —
    *not reported* — when the two kinds are mixed, because a margin against the wrong one would be
    a confident wrong number."""
    model = {float(link["threshold"]) for link in links if "phi" in link}
    if not model:
        return formula
    if len(model) == 1 and all("phi" in link for link in links):
        return model.pop()
    return None


def scale_of(links: list[dict[str, Any]]) -> str:
    """``"logit"`` when a model decided every link (scores are log-odds), else ``"additive"``."""
    return "logit" if links and all("phi" in link for link in links) else "additive"
