"""The acts on a case, once each, for every caller: the API, the case loop, automation (ADR #459).

Each act checks its transition (`cases.transition`), writes the case and its cycle, and posts an
`internal` event to the feed saying what happened and who did it — so the feed is the case's
history as well as its conversation, as a case management system's is. The caller holds
``store.lock`` (or the API's write transaction) and commits; nothing here commits or audits, so
an act and its audit row land together or not at all.

**What is not here**: closing the situation and clearing its alarms when a case is resolved. Those
are the broker's gestures, with the broker's evidence (`engine/dataset/gestures.py`) and its
in-memory state; the NOC domain may not import them (`tests/repo/test_layers.py`), so the API does
that half (`api/case_close.py`) in the same transaction.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from netcorenoc.engine.noc import cases
from netcorenoc.engine.noc.cases import CaseError

if TYPE_CHECKING:
    from netcorenoc.store import Store

__all__ = [
    "SYSTEM",
    "Actor",
    "auto_pend",
    "auto_resolve",
    "close",
    "dispatch",
    "event",
    "pend",
    "release",
    "reprioritize",
    "requeue",
    "resolve",
    "take",
    "update",
    "validate",
]


@dataclass(frozen=True)
class Actor:
    """Who acts: a person, an agent (a service token), or the appliance itself."""

    ref: str
    name: str
    kind: str  # person | agent | system
    role: str | None

    def author(self) -> dict[str, Any]:
        return {"ref": self.ref, "name": self.name, "kind": self.kind, "role": self.role}


SYSTEM = Actor("system", "NetCoreNOC", "system", None)


async def event(store: Store, sid: int, body: str, now: float, actor: Actor = SYSTEM) -> None:
    """An `internal` event in the feed: the case's own history."""
    await store.noc_post(
        sid, actor.author(), visibility="internal", kind="event", body=body[:8000], now=now
    )


def _owns(case: Mapping[str, Any], actor: Actor, *, admin: bool) -> None:
    if admin or case.get("owner_ref") == actor.ref:
        return
    raise CaseError("take the case first: only its owner may do this", 403)


# -- a person's or an agent's acts -------------------------------------------------------------


async def take(store: Store, case: Mapping[str, Any], actor: Actor, now: float) -> None:
    sid, status = int(case["situation_id"]), str(case["status"])
    if case.get("owner_ref") == actor.ref and status == "working":
        return  # already theirs: a second take is not a second event
    cases.transition("take", status)
    if status in ("new", "auto"):
        # Taken before it reached the queue: the cycle it would have opened, taken at once.
        await reprioritize(store, sid, now)
        await store.noc_case_enqueue(sid, "opened" if status == "new" else "automation", now)
    await store.noc_case_move(
        sid, "working", now, actor.ref, owner=(actor.ref, actor.name, actor.kind)
    )
    if status in cases.UNOWNED:
        await store.noc_cycle_taken(sid, now, actor.ref)
    before = case.get("owner_name")
    took = f"{actor.name} took the case" + (
        f" from {before}" if before and status == "working" else ""
    )
    await event(store, sid, f"{took}.", now, actor)


async def release(
    store: Store, case: Mapping[str, Any], actor: Actor, now: float, *, admin: bool, note: str = ""
) -> None:
    sid = int(case["situation_id"])
    _owns(case, actor, admin=admin)
    cases.transition("release", str(case["status"]))
    await store.noc_cycle_left(sid, now)
    await store.noc_case_enqueue(sid, "released", now)
    await event(
        store, sid, f"{actor.name} put the case back in the NOC queue. {note}".strip(), now, actor
    )


async def update(
    store: Store,
    case: Mapping[str, Any],
    actor: Actor,
    now: float,
    *,
    fields: Mapping[str, Any],
    summaries: Mapping[int, str],
    organizations: frozenset[int],
    admin: bool,
) -> dict[str, Any]:
    """The customer update: any subset of the fields, and per-organization summaries. Returns the
    case as it now reads (fields merged), for the caller's documentation check.

    Not only the owner's (ADR #470): the update follows the facts, so whoever works cases — the
    agent it is routed to, the one it climbed to, a person helping — keeps it current, each edit
    in the feed under their name. ``admin`` is kept for the callers; it no longer decides."""
    del admin
    sid = int(case["situation_id"])
    if str(case["status"]) in ("resolved", "closed"):
        raise CaseError("a resolved case is not edited; its feed still takes posts")
    for name, value in fields.items():
        if name in cases.FIELD_LIMITS and len(value) > cases.FIELD_LIMITS[name][1]:
            raise CaseError(f"{name}: at most {cases.FIELD_LIMITS[name][1]} characters", 400)
    if "priority" in fields and fields["priority"] not in cases.PRIORITIES:
        raise CaseError(f"priority: one of {', '.join(cases.PRIORITIES)}", 400)
    when = fields.get("next_update_at")
    if when is not None and float(when) <= now:
        raise CaseError("next_update_at: must be in the future", 400)
    unknown = sorted(set(summaries) - organizations)
    if unknown:
        raise CaseError(
            f"summaries: organization {unknown[0]} has no element in this situation", 400
        )
    if any(len(text) > cases.FIELD_LIMITS["executive_summary"][1] for text in summaries.values()):
        raise CaseError("summaries: at most 200 characters each", 400)
    await store.noc_case_set_fields(sid, fields, now, actor.ref)
    for org_id, text in sorted(summaries.items()):
        await store.noc_case_set_summary(sid, org_id, text.strip(), now, actor.ref)
    merged = {**case, **fields}
    if case["status"] == "working" and cases.documented(merged, now=now):
        await store.noc_cycle_documented(sid, now)
    named = {
        "next_update_at": "next update",
        "priority": f"priority to {cases.PRIORITIES.get(str(fields.get('priority')), '')}",
    }
    changed = [
        cases.FIELD_LIMITS[f][0].lower() if f in cases.FIELD_LIMITS else named.get(f, f)
        for f in sorted(fields)
    ]
    changed += ["an organization's executive summary"] * bool(summaries)
    await event(store, sid, f"{actor.name} updated the {', '.join(changed)}.", now, actor)
    return merged


async def pend(
    store: Store,
    case: Mapping[str, Any],
    actor: Actor,
    now: float,
    *,
    status: str,
    admin: bool,
    note: str = "",
) -> None:
    """Working (or pending) → a pending status. The customer update must be complete, and the next
    update in the future: it is when the NOC looks again."""
    sid = int(case["situation_id"])
    _owns(case, actor, admin=admin)
    to = cases.transition("pend", str(case["status"]), to=status)
    lacking = cases.missing(case, to, now=now)
    if lacking:
        raise CaseError(f"complete the customer update first: {', '.join(lacking)}")
    if case["status"] == "working":
        await store.noc_cycle_documented(sid, now)
        await store.noc_cycle_left(sid, now)
    await store.noc_case_move(sid, to, now, actor.ref)
    text = f"{actor.name} set the case to {cases.STATUSES[to]} until its next update."
    await event(store, sid, f"{text} {note}".strip(), now, actor)


async def resolve(
    store: Store,
    case: Mapping[str, Any],
    actor: Actor,
    now: float,
    *,
    cause: str,
    solution: str,
    alarms_cleared: bool,
    admin: bool,
    resolution: str = "noc",
) -> None:
    """The case row's half of a resolution (the situation's is the API's, module docstring)."""
    sid = int(case["situation_id"])
    if resolution == "noc":
        _owns(case, actor, admin=admin)
        cases.transition("resolve", str(case["status"]))
    else:
        cases.transition("auto_resolve", str(case["status"]))
    if cause not in cases.CAUSES:
        raise CaseError(f"cause: one of {', '.join(cases.CAUSES)}", 400)
    if len(solution) > cases.FIELD_LIMITS["solution"][1]:
        raise CaseError("solution: at most 8000 characters", 400)
    lacking = cases.missing({**case, "cause": cause, "solution": solution}, "resolved", now=now)
    if lacking and resolution == "noc":
        raise CaseError(f"complete the case first: {', '.join(lacking)}")
    if case["status"] == "working":
        await store.noc_cycle_documented(sid, now)
        await store.noc_cycle_left(sid, now)
    await store.noc_cycle_withdrawn(sid, now)
    await store.noc_case_resolve(
        sid,
        now,
        actor.ref,
        resolution=resolution,
        cause=cause,
        solution=solution.strip(),
        alarms_cleared=alarms_cleared,
    )
    cleared = " Its alarms were verified clear on the device and cleared." if alarms_cleared else ""
    await event(
        store, sid, f"{actor.name} resolved the case: {cases.CAUSES[cause]}.{cleared}", now, actor
    )


# -- the case loop's and automation's acts -----------------------------------------------------


async def auto_pend(
    store: Store,
    case: Mapping[str, Any],
    actor: Actor,
    now: float,
    *,
    status: str,
    update: Mapping[str, str],
    wait_s: int,
    note: str = "",
) -> bool:
    """A playbook's `pending` leaf: its customer update written, the case waiting until
    ``wait_s`` from now. When the update it carries is incomplete, the case goes to the NOC queue
    instead, and this answers False. Nobody owns the case and no take is counted."""
    sid = int(case["situation_id"])
    to = cases.transition("auto_pend", str(case["status"]), to=status)
    fields: dict[str, Any] = {k: v for k, v in update.items() if k in cases.TEXT_FIELDS}
    fields["next_update_at"] = now + wait_s
    merged = {**case, **fields}
    lacking = cases.missing(merged, to, now=now)
    if lacking:
        await requeue(
            store, sid, "automation", now, note=f"Its update lacked {', '.join(lacking)}."
        )
        return False
    await store.noc_case_set_fields(sid, fields, now, actor.ref)
    await store.noc_case_move(sid, to, now, actor.ref)
    text = f"{actor.name} set the case to {cases.STATUSES[to]} until its next update."
    await event(store, sid, f"{text} {note}".strip(), now, actor)
    return True


#: Worst first: a lower number is a higher priority.
_RANK = {"critical": 0, "major": 1, "minor": 2}


async def reprioritize(store: Store, sid: int, now: float) -> str | None:
    """While the priority is still the appliance's (`priority_by` NULL), the worst effective
    severity among the situation's alarms decides it — raised, never lowered: lowering is a
    person's call, and a clock relaxed after the fact is a clock gamed (ADR #464). Returns the
    new priority, or None when it stays."""
    case = await store.noc_case(sid)
    if case is None or case.get("priority_by") is not None:
        return None
    worst = await store.noc_case_severity(sid)
    derived = cases.priority_of(worst)
    if _RANK[derived] >= _RANK[str(case["priority"])]:
        return None
    await store.noc_case_raise_priority(sid, derived, now)
    await event(
        store,
        sid,
        f"Priority raised to {cases.PRIORITIES[derived]}: the situation is {worst} on the Broker.",
        now,
    )
    return derived


async def dispatch(store: Store, sid: int, now: float, *, automate: bool = False) -> None:
    """A `new` case goes to automation or to the NOC queue, its take clock counted from opening —
    at the priority its alarms give it (`reprioritize`)."""
    await reprioritize(store, sid, now)
    if automate:
        await store.noc_case_move(sid, "auto", now, "system")
        await event(store, sid, "Automated checks started.", now)
        return
    priority = await store.noc_case_enqueue(sid, "opened", now)
    await event(store, sid, f"Entered the NOC queue ({cases.PRIORITIES[priority]}).", now)


async def requeue(store: Store, sid: int, reason: str, now: float, *, note: str = "") -> None:
    """Back to the NOC queue — a pending case's next update came, a person posted on it, or
    automation did not conclude."""
    await reprioritize(store, sid, now)
    await store.noc_case_enqueue(sid, reason, now)
    await event(
        store, sid, f"Back in the NOC queue: {cases.CYCLE_REASONS[reason]}. {note}".strip(), now
    )


async def validate(store: Store, sid: int, now: float, why: str) -> None:
    """The situation resolved while someone owned the case: they confirm it is over."""
    await store.noc_cycle_left(sid, now)
    await store.noc_case_move(sid, "validate", now, "system")
    await event(store, sid, f"The situation resolved ({why}): validate the resolution.", now)


async def auto_resolve(store: Store, sid: int, now: float, resolution: str, why: str) -> None:
    """Over without the NOC resolving it — the situation cleared before anyone took the case, or
    was merged into another (owned or not: the work continues on the other one)."""
    await store.noc_cycle_withdrawn(sid, now)
    await store.noc_cycle_left(sid, now)
    cause = "duplicate" if resolution == "merged" else "cleared_before_isolation"
    await store.noc_case_resolve(
        sid, now, "system", resolution=resolution, cause=cause, solution=why, alarms_cleared=False
    )
    await event(store, sid, f"Resolved without the NOC: {why}.", now)


async def close(store: Store, sid: int, now: float) -> None:
    await store.noc_case_move(sid, "closed", now, "system")
    await event(store, sid, "Closed: resolved a day ago with nothing since.", now)
