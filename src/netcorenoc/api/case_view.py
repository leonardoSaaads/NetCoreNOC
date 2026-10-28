"""How a case and its feed read, per reader; and who the caller is, as an actor (ADRs #459-#461).

Shared by `routes/cases.py` and `routes/case_feed.py`, which may not import each other
(`routes/__init__.py`). Two rules from the rest of the product apply to a reader below editor
(`crosscutting/shaping/fields.py`), and both are applied here rather than by key-name shaping,
because a case is free text a person wrote:

* **addresses** in any text are shown as their network (`feed.mask_addresses`);
* **people** are not named: an owner or an author reads as "NOC engineer", "NOC agent" or
  "Operator", never as a username, and their reference is withheld.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import HTTPException, Request

from netcorenoc.crosscutting import auth, shaping
from netcorenoc.engine.noc import agents, cases, feed, kpi
from netcorenoc.engine.noc.case_ops import Actor
from netcorenoc.engine.noc.cases import CaseError

__all__ = ["actions", "actor_of", "case_view", "fail", "manages", "post_view", "reader_of"]


def actor_of(principal: auth.Principal) -> Actor:
    return Actor(
        ref=principal.ref or principal.actor,
        name=principal.actor,
        kind="agent" if principal.is_token else "person",
        role=principal.role,
    )


def manages(request: Request) -> bool:
    """`cases.manage`: act on any case as its owner would, and read every `specified` post."""
    return "cases.manage" in request.state.capabilities


def reader_of(request: Request, principal: auth.Principal) -> feed.Reader:
    return feed.Reader(
        ref=principal.ref,
        user_id=principal.user_id,
        role=principal.role,
        works="cases.work" in request.state.capabilities,
        admin=manages(request),
    )


def fail(exc: CaseError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


def _text(reader: feed.Reader) -> Any:
    return (lambda s: s) if shaping.sees_raw_addresses(reader.role) else feed.mask_addresses


def _who(
    ref: str | None, name: str | None, kind: str | None, role: str | None, reader: feed.Reader
) -> dict[str, Any] | None:
    if ref is None or kind is None:
        return None
    if shaping.sees_people(reader.role) or ref == reader.ref:
        return {"ref": ref, "name": name, "kind": kind, "you": ref == reader.ref}
    return {"ref": None, "name": feed.author_label(kind, role), "kind": kind, "you": False}


def actions(case: Mapping[str, Any], reader: feed.Reader) -> list[str]:
    """What this reader may do to this case now — the server's answer, so neither the console nor
    an agent has to work it out from a role. Each is still checked again when it is asked for."""
    if not reader.works:
        return []
    status = str(case["status"])
    yours = reader.ref is not None and case.get("owner_ref") == reader.ref
    out = []
    for act in ("take", "release", "pend", "resolve"):
        try:
            cases.transition(act, status, to=next(iter(sorted(cases.PENDING))))
        except CaseError:
            continue
        if act == "take" and yours and status == "working":
            continue
        if act != "take" and not (yours or reader.admin):
            continue
        out.append(act)
    if status not in ("resolved", "closed") and (yours or reader.admin):
        out.append("edit")
    # ADR #467: routing to an agent and climbing the chain. The handler decides who may: an
    # agent only within its own reports, a person on a case they own or that nobody has.
    if status in cases.LIVE and (yours or reader.admin or status in cases.UNOWNED):
        out.append("assign")
    if status in cases.LIVE and (yours or reader.admin or case.get("agent_id") is not None):
        out.append("escalate")
    return out


#: The statuses a routed case climbs from (`store.noc_cases_routed`): not while it waits on someone.
CLIMBS = frozenset({"new", "auto", "queue", "working"})


def _escalates_at(case: Mapping[str, Any]) -> float | None:
    """When a routed case climbs to the next tier unless its agent concludes it first (ADR #467);
    None while it waits on someone, once a person took it, or past the agents."""
    if case.get("agent_id") is None or case.get("assigned_at") is None:
        return None
    if str(case["status"]) not in CLIMBS or str(case.get("owner_ref") or "").startswith("user:"):
        return None
    limit = agents.escalate_after(
        {"escalate_after_s": case.get("agent_escalate_after_s")}, str(case["priority"])
    )
    return float(case["assigned_at"]) + limit


def case_view(
    case: Mapping[str, Any],
    reader: feed.Reader,
    *,
    now: float,
    cycles: Sequence[Mapping[str, Any]] = (),
    organizations: Sequence[Mapping[str, Any]] | None = None,
    whole: bool = True,
) -> dict[str, Any]:
    """``whole`` is False for a scoped reader: the situation's derived name is built from every
    member's address, in scope or not, so it is withheld and the case reads by its own title."""
    text = _text(reader)
    status = str(case["status"])
    current = cycles[-1] if cycles else None
    owner_role = "editor" if case.get("owner_kind") == "person" else None
    out: dict[str, Any] = {
        "id": case["situation_id"],
        "situation_id": case["situation_id"],
        "status": status,
        "status_label": cases.STATUSES[status],
        "super_state": cases.super_state(status),
        "priority": case["priority"],
        "priority_label": cases.PRIORITIES[str(case["priority"])],
        # True while the appliance decides it from the alarms (ADR #464); a person's is theirs.
        "priority_auto": case.get("priority_by") is None,
        **{name: text(case[name]) for name in cases.TEXT_FIELDS if name != "solution"},
        "next_update_at": case["next_update_at"],
        "cadence": kpi.cadence(case, now),
        # ADR #467: the agent the case is routed to, the tier it has climbed to, and whether it is
        # past the agents, with the NOC team.
        "agent": None
        if case.get("agent_id") is None
        else (
            {"id": case["agent_id"], "name": case.get("agent_name")}
            if shaping.sees_people(reader.role)
            else {"id": None, "name": "NOC agent"}
        ),
        "assigned_at": case.get("assigned_at"),
        "tier": case.get("tier", 1),
        "with_people": bool(case.get("with_people")),
        "escalated_at": case.get("escalated_at"),
        "escalates_at": _escalates_at(case),
        "level_label": "With the NOC team"
        if case.get("with_people")
        else f"With NOC Tier {case.get('tier') or 1}",
        "owner": _who(
            case.get("owner_ref"),
            case.get("owner_name"),
            case.get("owner_kind"),
            owner_role,
            reader,
        ),
        "opened_at": case["opened_at"],
        "status_at": case["status_at"],
        "age_s": round(now - float(case["opened_at"]), 1),
        "version": case["version"],
        "updated_at": case["updated_at"],
        "situation": {
            "status": case["situation_status"],
            "severity": case["severity"],
            "name": text(case["situation_name"] or "") if whole else "",
            "members": case["members"],
        },
        "clocks": {
            **(
                {}
                if current is None
                else {"take": kpi.take(current, now), "document": kpi.document(current, now)}
            ),
            "restore": kpi.restore(case, now),
        },
        "missing": cases.missing(case, "pending_customer", now=now),
        "actions": actions(case, reader),
        "resolution": None
        if case["resolution"] is None
        else {
            "how": case["resolution"],
            "how_label": cases.RESOLUTIONS[str(case["resolution"])],
            "cause": case["cause"],
            "cause_label": cases.CAUSES.get(str(case["cause"]), case["cause"]),
            "solution": text(case["solution"]),
            "alarms_cleared": case["alarms_cleared"],
            "at": case["resolved_at"],
        },
    }
    if cycles:
        out["cycles"] = [
            {
                "reason": c["reason"],
                "reason_label": cases.CYCLE_REASONS[str(c["reason"])],
                "priority": c["priority"],
                "entered_at": c["entered_at"],
                "taken_at": c["taken_at"],
                "documented_at": c["documented_at"],
                "take": kpi.take(c, now),
                "document": kpi.document(c, now),
            }
            for c in cycles
        ]
    if organizations is not None:
        default = text(case["executive_summary"])
        out["organizations"] = [
            {
                "id": o["id"],
                "name": o["name"],
                "summary": text(o["summary"]) if o["summary"] else None,
                "reads": text(o["summary"]) if o["summary"] else default,
            }
            for o in organizations
        ]
    return out


def post_view(post: Mapping[str, Any], reader: feed.Reader) -> dict[str, Any]:
    text = _text(reader)
    author = _who(
        str(post["author_ref"]),
        str(post["author_name"]),
        str(post["author_kind"]),
        post["author_role"],
        reader,
    )
    out = {
        "id": post["id"],
        "kind": post["kind"],
        "visibility": post["visibility"],
        "author": author,
        "body": text(post["body"]),
        "created_at": post["created_at"],
    }
    if post["visibility"] == "specified" and (
        reader.admin or (author is not None and author["you"])
    ):
        out["readers"] = list(post["readers"])
    return out
