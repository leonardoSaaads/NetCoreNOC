"""Projections: applying a resolved scope to a response body.

Not a third axis but the **consumer** of the other two (v0.7.4, DECISIONS #95). Each function here
takes a :class:`~netcorenoc.shaping.scope.Scope` — produced by the scope axis — and returns a
response body, which is the field axis's subject. That is why forcing these four functions into
either `fields.py` or `scope.py` would have been arbitrary, and why `MODULE-ARCHITECTURE.md` §10.2's
two-way framing made one of those choices look necessary.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.crosscutting.shaping.naming import derive_situation_name
from netcorenoc.crosscutting.shaping.scope import Scope


def filter_rows(rows: list[dict[str, Any]], scope: Scope, *, ne_key: str) -> list[dict[str, Any]]:
    """Keep only the rows whose NE is in scope. ``ne_key`` names the id column on the row."""
    if scope.unrestricted:
        return rows
    return [row for row in rows if scope.allows_ne(_as_int(row.get(ne_key)))]


def project_situation_row(
    row: dict[str, Any], member_nes: list[int | None], scope: Scope
) -> dict[str, Any] | None:
    """One row of the situations LIST for a scoped reader, or **None** if none of it is visible.

    Both callers of the list — `GET /api/situations` and the SSE stream — had this expression
    written out separately and identically; it is here once because v0.16.0 gave it a third
    responsibility and two copies of a redaction rule is one copy too many.

    **`derived_name` is dropped when anything is redacted, not recomputed.** The name is built from
    the addresses of the members, and this row carries no membership to rebuild it from — the list
    query returns a count, not a bag. Recomputing would mean a second query per list to fetch
    addresses the reader may see; dropping says exactly as much as `redacted_count` already says,
    and the console falls back to `#id`, which is the situation's identity anyway (DECISIONS #59:
    redact to a count, never to a different identity).

    A row with **no** redacted members keeps its name: every address in it belongs to a device this
    reader can already see, and the field axis coarsens it for a role below `editor`.
    """
    shown = sum(1 for ne_id in member_nes if scope.allows_ne(ne_id))
    if not shown:
        return None  # nothing of this situation is yours: it is not listed at all
    redacted = len(member_nes) - shown
    out = {**row, "alarm_count": shown, "redacted_count": redacted}
    if redacted:
        out["derived_name"] = None
    return out


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def project_graph(snapshot: dict[str, Any], scope: Scope) -> dict[str, Any]:
    """Graph nodes restricted to in-scope devices; an edge survives only if **both** ends do.

    Keeping a half-visible edge would disclose the existence of a neighbour the caller may not see
    — the graph would draw a line to nothing — so an edge with one end out of scope is dropped
    entirely rather than truncated.
    """
    if scope.unrestricted:
        return snapshot
    nodes = [node for node in snapshot.get("nodes", []) if scope.allows_ip(node.get("ip"))]
    visible_ids = {_as_int(node.get("id")) for node in nodes}
    edges = [
        edge
        for edge in snapshot.get("edges", [])
        if _as_int(edge.get("a_id")) in visible_ids and _as_int(edge.get("b_id")) in visible_ids
    ]
    return {**snapshot, "nodes": nodes, "edges": edges}


# v0.7.1 (F35 + F38): there is deliberately **no** `project_timeline` here any more. v0.7.0
# filtered timeline marks in Python by comparing the rendered ``device`` string —
# ``COALESCE(label, ip)`` — against the scope's address and label sets. Labels are not unique, so
# an editor who copied an in-scope NE's label onto an out-of-scope one inherited its alarm timing
# and classes: a display string had become an authorization key. The filter now lives in
# `store.timeline_marks()`, keyed on `ne_id`, which fixes the identity defect and the
# truncate-before-filter defect in the same move (DECISIONS #67, #72).


def project_situation_detail(
    detail: dict[str, Any], scope: Scope, *, member_ne_ids: dict[int, int | None]
) -> dict[str, Any] | None:
    """Project one situation for a scoped reader, or **None** if none of it is visible.

    Returning ``None`` is how the 404 happens: the caller hands it to the handler's existing
    ``if detail is None: raise HTTPException(404)`` branch, so "out of your scope" and "no such
    situation" are the same code path, the same body, and the same timing — indistinguishable by
    construction rather than by two branches that happen to agree (DECISIONS #60).

    A situation survives if **at least one** member is in scope. Out-of-scope members are not
    deleted but **redacted to a count and a type** (DECISIONS #59): silently showing "3 alarms" for
    a 40-alarm cross-boundary incident would leave an operator confidently wrong about what they
    are looking at, which is the failure mode this project refuses elsewhere too. The redaction
    carries no NE id, address, entity key, or varbind — only how many members lie outside, and
    which alarm classes they belong to, which is strictly less than the situation id and
    ``updated_at`` the reader can already see.
    """
    if scope.unrestricted:
        return detail
    alarms = detail.get("alarms", [])
    visible: list[dict[str, Any]] = []
    hidden: list[dict[str, Any]] = []
    for alarm in alarms:
        ne_id = member_ne_ids.get(_as_int(alarm.get("id")) or -1)
        (visible if scope.allows_ne(ne_id) else hidden).append(alarm)
    if not visible:
        return None  # nothing of this situation is yours: indistinguishable from "no such id"
    visible_ids = {_as_int(alarm.get("id")) for alarm in visible}
    links = [
        link
        for link in detail.get("links", [])
        if _as_int(link.get("alarm_a")) in visible_ids
        and _as_int(link.get("alarm_b")) in visible_ids
    ]
    out = {**detail, "alarms": visible, "links": links}
    if hidden:
        # **Recomputed, not dropped** — here, unlike on the list, the visible membership is in
        # hand, so the reader gets a true name for the part of the situation that is theirs
        # instead of a name built partly from devices they may not see. One function, a different
        # input (DECISIONS #257): a second implementation is how the two would come to disagree.
        out["derived_name"] = derive_situation_name(
            [str(alarm.get("device_ip") or "") for alarm in visible], len(visible)
        )
    # The root hint names an alarm; suppress it when that alarm is one the reader may not see.
    if _as_int(out.get("root_alarm_id")) not in visible_ids:
        out["root_alarm_id"] = None
    if hidden:
        out["redacted_members"] = {
            "count": len(hidden),
            "classes": sorted(
                {str(a.get("class_name") or a.get("class_oid") or "") for a in hidden}
            ),
            "note": (
                f"{len(hidden)} further member(s) of this situation are outside your visibility "
                "scope and are not shown. Scoping hides them from you; it does not stop them "
                "correlating."
            ),
        }
    return out
