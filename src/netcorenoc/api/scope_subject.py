"""Which customers one person serves: their entry in the visibility policy (ADR #499).

A person limited to Claro sees Claro's brokers — every element Claro owns, in every broker
(`org:<id>`) — and Claro's side of the NOC: its book, cases, routines and credentials. This module
writes that one entry: a new version of the **whole** scope policy, appended and activated the way
`POST /api/scope` does it, so it is versioned, audited by the caller and reversible from the
policy's history like any other change.

It runs **inside the caller's write transaction**, which is the point: creating a person and
limiting them is one commit, so there is no moment at which "Claro - viewer" exists and sees every
customer. A subject's other selectors (an address, a CIDR, `broker:<id>`) are kept: this edits the
customers, not the whole entry, and the screen shows what else the entry holds.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from fastapi import HTTPException

from netcorenoc.crosscutting import shaping
from netcorenoc.store import Store

PREFIX = "org:"
#: ADR #508: said whenever a write would limit the super admin, who reaches everything, always.
OWNER_REFUSAL = (
    "the super admin reaches every broker and every customer, always: their access cannot be "
    "limited, by anyone, themselves included (ADR #508)"
)
#: ADR #508: what this module writes for the two planes — the Broker's and the NOC's.
BROKER = "broker:"
NOC = "noc:"


def canonical(document: dict[str, Any]) -> tuple[str, str]:
    """Canonical JSON and its sha256 — the bytes `doc_hash` commits to (as `/api/scope`)."""
    text = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


async def active_document(store: Store) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The active scope document (empty when none) and its row."""
    policy_id = (await store.active_governance_ids()).get("scope")
    row = await store.get_governance_policy(policy_id) if policy_id is not None else None
    if row is None:
        return {}, None
    if shaping.parse_scope_policy(row["document"]).malformed:
        raise HTTPException(
            status_code=409, detail="the stored visibility policy is malformed; repair it first"
        )
    return json.loads(row["document"]), row


def organizations_of(document: dict[str, Any], subject: str) -> list[int] | None:
    """The customers a subject's entry names; None when it names none (all, or other selectors)."""
    entry = (document.get("principals") or {}).get(subject)
    if entry is None:
        return None
    named = [int(s[len(PREFIX) :]) for s in entry if s.startswith(PREFIX) and s[4:].isdigit()]
    return named or None


async def set_organizations(
    store: Store, subject: str, organizations: list[int] | None, actor: str, note: str
) -> dict[str, Any]:
    """Replace `subject`'s `org:` selectors; None removes them. Returns the audit's before/after.

    Every id must be a customer the NOC serves (not the default organization, not one that ended).
    An entry left with no selector is removed, so the subject inherits again — never an empty list,
    which would mean "sees nothing".
    """
    wanted: list[int] = []
    if organizations is not None:
        wanted = sorted(set(organizations))
        customers = {int(c["id"]) for c in await store.noc_customers()}
        unknown = [o for o in wanted if o not in customers]
        if unknown or not wanted:
            raise HTTPException(
                status_code=400, detail=f"organizations: not customers the NOC serves: {unknown}"
            )
    document, before = await active_document(store)
    principals = dict(document.get("principals") or {})
    kept = [s for s in principals.get(subject, []) if not s.startswith(PREFIX)]
    selectors = kept + [f"{PREFIX}{o}" for o in wanted]
    if selectors:
        principals[subject] = selectors
    else:
        principals.pop(subject, None)
    document = {**document, "version": 1, "principals": principals}
    text, doc_hash = canonical(document)
    problems = shaping.scope_policy_errors(shaping.parse_scope_policy(text))
    if problems:
        raise HTTPException(status_code=400, detail="; ".join(problems))
    now = time.time()
    policy_id = await store.insert_governance_policy("scope", text, doc_hash, actor, now, note)
    await store.set_active_governance_policy("scope", policy_id, actor, now)
    return {
        "kind": "scope",
        "action": "applied",
        "subject": subject,
        "organizations": None if organizations is None else wanted,
        "before": {"policy_id": int(before["id"]), "doc_hash": before["doc_hash"]}
        if before
        else None,
        "after": {"policy_id": policy_id, "doc_hash": doc_hash},
    }


def access_of(document: dict[str, Any], subject: str) -> dict[str, list[int] | None]:
    """ADR #508: the brokers and NOC customers a subject reaches — None for every one.

    An entry with no opinion reaches everything. A legacy `org:<id>` entry (ADR #499) reads as those
    customers, on both planes; the screen writes it back as `broker:`/`noc:` selectors.
    """
    entry = (document.get("principals") or {}).get(subject)
    if entry is None:
        return {"brokers": None, "customers": None}

    def ids(prefix: str) -> list[int]:
        return sorted(
            {
                int(s[len(prefix) :])
                for s in entry
                if s.startswith(prefix) and s[len(prefix) :].isdigit()
            }
        )

    legacy = ids(PREFIX)
    brokers: list[int] | None = None if shaping.BROKER_ALL in entry else ids(BROKER)
    if shaping.NOC_ALL in entry:
        customers: list[int] | None = None
    elif any(s.startswith(NOC) for s in entry):
        customers = sorted(set(ids(NOC)) | set(legacy))
    else:
        customers = legacy
    return {"brokers": brokers, "customers": customers, "organizations": legacy or None}


async def set_access(
    store: Store,
    subject: str,
    brokers: list[int] | None,
    customers: list[int] | None,
    actor: str,
    note: str,
) -> dict[str, Any]:
    """Replace `subject`'s brokers and NOC customers (ADR #508); None is every one, [] none.

    Every id must be a broker or a served customer. Both None removes the entry's plane selectors,
    so the subject inherits again; anything narrower writes `broker:` and `noc:` selectors — and a
    legacy `org:` one goes, since the two lists now say all of it. Other selectors (an address, a
    CIDR) are kept, as `set_organizations` keeps them.
    """
    known_brokers = {int(b["id"]) for b in await store.brokers()}
    known_customers = {int(c["id"]) for c in await store.noc_customers()}
    if brokers is not None:
        brokers = sorted(set(brokers))
        if unknown := [b for b in brokers if b not in known_brokers]:
            raise HTTPException(status_code=400, detail=f"brokers: no such broker: {unknown}")
    if customers is not None:
        customers = sorted(set(customers))
        if unknown := [c for c in customers if c not in known_customers]:
            raise HTTPException(
                status_code=400, detail=f"customers: not customers the NOC serves: {unknown}"
            )
    document, before = await active_document(store)
    principals = dict(document.get("principals") or {})
    kept = [s for s in principals.get(subject, []) if not s.startswith((PREFIX, BROKER, NOC))]
    plane: list[str] = []
    if brokers is not None or customers is not None or kept:
        plane += [shaping.BROKER_ALL] if brokers is None else [f"{BROKER}{b}" for b in brokers]
        if customers is None:
            plane.append(shaping.NOC_ALL)
        elif customers:
            plane += [f"{NOC}{c}" for c in customers]
        else:
            plane.append(shaping.NOC_NONE)
    selectors = kept + plane
    if brokers is None and customers is None and not kept:
        principals.pop(subject, None)
    else:
        principals[subject] = selectors
    document = {**document, "version": 1, "principals": principals}
    text, doc_hash = canonical(document)
    problems = shaping.scope_policy_errors(shaping.parse_scope_policy(text))
    if problems:
        raise HTTPException(status_code=400, detail="; ".join(problems))
    now = time.time()
    policy_id = await store.insert_governance_policy("scope", text, doc_hash, actor, now, note)
    await store.set_active_governance_policy("scope", policy_id, actor, now)
    return {
        "kind": "scope",
        "action": "applied",
        "subject": subject,
        "brokers": brokers,
        "customers": customers,
        "before": {"policy_id": int(before["id"]), "doc_hash": before["doc_hash"]}
        if before
        else None,
        "after": {"policy_id": policy_id, "doc_hash": doc_hash},
    }
