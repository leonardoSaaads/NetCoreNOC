"""What a caller may see of a maintenance window, and the two clock-driven statuses.

Split from `api/routes/maintenance.py` at the 400-line guard, on the seam that matters most in
this feature: **the routes decide what happens; this decides what is shown.** Prime directive 4
and prime directive 6 both live in one function here — :func:`may_see_details` — and a reader
asking *"who sees a window's name?"* should find one answer rather than searching nine handlers.

⚠ **Nothing here is the redaction.** :func:`shape` returns less for a caller who may not see the
details, and that is the *presentation* of a decision already made. The claim prime directive 6
makes — *"scope filters in the query, never the render"* — is satisfied one layer down: every role
reads a window's existence from `store/mw_reads.py::window_markers`, which is a different
statement selecting different columns, and no caller is handed a full row to have fields removed
from it. `tests/test_maintenance_api.py` asserts that against the SQL rather than against this
module, in `test_the_visibility_filter_is_in_the_query_and_not_in_the_render`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from netcorenoc.crosscutting import auth, shaping
from netcorenoc.store.maintenance_windows import CONFIRMATION_THRESHOLD_S, STATUSES


@dataclass(frozen=True)
class WindowAccess:
    """The three questions both route modules ask before they touch a window.

    A small object rather than five free functions taking `store` and `scope_for`, because the
    two route modules would otherwise each thread the same three dependencies through nine
    handlers — and the one that forgot would be the one that answered without checking.

    Built once per `register()` from the `AppContext`, which is the same shape every route module
    already uses for `audit_row` and `write_txn`.
    """

    store: Any
    scope_for: Any
    audit_scope_denial: Any

    async def scope_ne_ids(self, principal: auth.Principal) -> frozenset[int] | None:
        """The caller's visible elements, or None for an unrestricted scope.

        `None` rather than "every id" so the store's parity path — the unmodified query an
        unscoped appliance runs — stays reachable without materialising an inventory.
        """
        scope = await self.scope_for(principal)
        return None if scope.unrestricted else scope.ne_ids

    async def in_scope(self, window: dict[str, Any], principal: auth.Principal) -> bool:
        """Whether this caller's visibility scope reaches any element the window names.

        A window naming **no** element is in every scope: it belongs to no device, so there is no
        device a scope could withhold. That is the reading `list_windows` and `visible_or_404`
        have always had; it is a method now because :meth:`visible_or_404` is not the only route
        that must ask — the idempotency replay in `create_window` asks too, and did not (F152).
        """
        targets = await self.store.window_targets(int(window["id"]))
        scope = await self.scope_for(principal)
        if scope.unrestricted or not targets:
            return True
        return any(scope.allows_ne(t["ne_id"]) for t in targets)

    async def visible_or_404(
        self, wid: int, principal: auth.Principal, request: Any, action: str
    ) -> dict[str, Any]:
        """One window the caller may see in full, or the 404 a nonexistent one takes.

        **Out of scope and nonexistent are one code path** — same status, same body, same timing
        (DECISIONS #60, #65). A viewer looking at an `editors`-visibility window gets the 404, and
        learns the window exists only from the marker on the device, which carries no details.

        `action` is what the caller was attempting, and it is a parameter because the perimeter's
        contract is that a denial is *"audited under the action the caller attempted"*. Every
        window route passed the literal `maintenance.window.update` until v0.21.1, so an auditor
        reading the log saw a scoped principal probing the write surface when what they had
        actually done was read a card or confirm a window (F153).
        """
        window: dict[str, Any] | None = await self.store.maintenance_window(wid)
        if window is None:
            raise HTTPException(status_code=404, detail="no such maintenance window")
        if not may_see_details(window, principal) or not await self.in_scope(window, principal):
            await self.audit_scope_denial(
                request, principal, action, "maintenance_window", str(wid)
            )
            raise HTTPException(status_code=404, detail="no such maintenance window")
        return window

    async def permitted_targets(
        self, body_targets: list[int], principal: auth.Principal
    ) -> list[int]:
        """The named elements the caller may actually see. **Silently narrowed, never reported.**

        This is the no-existence-oracle rule in one method. A caller naming an element outside
        their scope gets a window over the elements they can see, with no error and no differing
        count — indistinguishable from naming an element that does not exist. Telling them *"you
        may not see 17"* would confirm that 17 exists.
        """
        permitted = await self.scope_ne_ids(principal)
        known = await self.store.ne_organizations(sorted(set(body_targets)))
        return [
            ne
            for ne in sorted(set(body_targets))
            if ne in known and (permitted is None or ne in permitted)
        ]


#: The rule-row fields compared when deciding whether an edit changed what a window collects.
_RULE_KEYS = (
    "ne_id",
    "kind",
    "severity_rank",
    "oid_root",
    "match_on",
    "slot_starts_at",
    "slot_ends_at",
)


def frozen_while_active(
    window: dict[str, Any],
    *,
    starts_at: float,
    tz: str,
    patch_s: float,
    ledger_enabled: bool,
    targets: list[int],
    rules: list[dict[str, Any]],
    current_targets: list[int],
    current_rules: list[dict[str, Any]],
) -> str | None:
    """**What an edit may not change once a window is running** (v0.22.0, item 19, ADR #389).

    Returns the first frozen field the edit changes, or None. Shortening and ending are safe — the
    appliance starts collecting sooner — and so is extending, which `extend` already offers. What
    is not safe is anything that rewrites the part already in force: moving the start, the zone or
    the patch band, which targets it covers, or what it lets through. Those would make the ledger
    and the alarms of the minutes already past answer to a window that was not the one in force.
    Name, description and visibility are words, and they may change.
    """
    if window["status"] != "active":
        return None

    def rule_key(rule: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(rule.get(k) for k in _RULE_KEYS)

    checks = (
        ("starts_at", abs(float(window["starts_at"]) - starts_at) > 0.5),
        ("tz", str(window["tz"]) != tz),
        ("patch_s", abs(float(window["patch_s"]) - patch_s) > 0.5),
        ("ledger_enabled", bool(window["ledger_enabled"]) != ledger_enabled),
        ("targets", sorted(current_targets) != sorted(targets)),
        ("rules", sorted(map(rule_key, current_rules)) != sorted(map(rule_key, rules))),
    )
    return next((name for name, changed in checks if changed), None)


def needs_confirmation(starts_at: float, ends_at: float) -> bool:
    """D6, in one expression. Up to six hours no human agrees; over six hours one must."""
    return (ends_at - starts_at) > CONFIRMATION_THRESHOLD_S


def initial_status(starts_at: float, now: float) -> str:
    """A window that needs no confirmation is `scheduled`, or `active` if it has already begun."""
    return "active" if starts_at <= now else "scheduled"


def restatus(window: dict[str, Any], needs: bool, starts_at: float, now: float) -> str:
    """The status an edited window takes. **A re-armed gate is never silent.**

    A window that now needs confirmation and does not have one goes back to
    `pending_confirmation` — which means it stops being in force, and the response says so. The
    alternative is a window an operator believes is running that is not, which is the one failure
    mode a maintenance feature must never have.
    """
    if needs and window["confirmed_at"] is None:
        return "pending_confirmation"
    return initial_status(starts_at, now)


def parse_statuses(raw: str | None) -> tuple[str, ...]:
    """`?status=scheduled,active` to a validated tuple. An unknown name is refused by name.

    Refused rather than ignored: an agent filtering on a status this appliance does not have has
    misunderstood the resource, and an empty list would look like *"there are none"*.
    """
    if not raw:
        return ()
    names = tuple(part.strip() for part in raw.split(",") if part.strip())
    unknown = [name for name in names if name not in STATUSES]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"unknown status {unknown}; the statuses are {list(STATUSES)}",
        )
    return names


def may_see_details(window: dict[str, Any], principal: auth.Principal) -> bool:
    """Who sees a window's name, description, owner and rules.

    **Not who sees that it exists** — that is `window_markers`, which every role reads and which
    returns different columns from a different query. Prime directive 4 and prime directive 6 are
    both in this one distinction: the existence is public, the details are scoped, and the scoping
    is a choice of statement rather than a filter over a row that was already assembled.
    """
    if principal.role in ("editor", "admin"):
        return True
    if str(window["visibility"]) == "everyone":
        return True
    return str(window["owner_ref"]) == (principal.ref or "\x00")


def shape(
    window: dict[str, Any], principal: auth.Principal, now: float, *, in_scope: bool = True
) -> dict[str, Any]:
    """One window as this caller may see it.

    The public half is every field prime directive 4 requires every role to have: that it exists,
    what state it is in, when it runs, and **how long until it starts as a number** — D7's *"in 2 h
    15 min"*, computed here so the console and an agent read the same figure rather than each
    subtracting for themselves.

    `in_scope=False` forces the public half whatever the role. It is how the idempotency replay
    answers a key that belongs to a window over elements the caller cannot see: the key is taken
    and the window exists — both of which prime directive 4 already makes public — and the name,
    the owner and the organization are not disclosed to a caller `GET /{wid}` would 404 (F152).
    """
    public = {
        "id": window["id"],
        "status": window["status"],
        "starts_at": window["starts_at"],
        "ends_at": window["ends_at"],
        "patch_s": window["patch_s"],
        "starts_in_s": float(window["starts_at"]) - now,
        "ends_in_s": float(window["ends_at"]) - now,
        "target_count": window["target_count"],
        "tz": window["tz"],
        "site_time": shaping.wall_clock(str(window["tz"]), float(window["starts_at"])),
        "site_offset": shaping.offset_label(str(window["tz"]), float(window["starts_at"])),
        "redacted": True,
    }
    if not in_scope or not may_see_details(window, principal):
        return public
    return {
        **public,
        "redacted": False,
        "name": window["name"],
        "description": window["description"],
        "organization_id": window["organization_id"],
        "organization_name": window["organization_name"],
        "all_day": window["all_day"],
        "ledger_enabled": window["ledger_enabled"],
        "visibility": window["visibility"],
        "owner_ref": window["owner_ref"],
        "owner_role": window["owner_role"],
        # **Marked in the API and on screen** (Part III). An operator reading a list of planned
        # work is entitled to know which entries a human wrote.
        "created_by_agent": window["created_by_agent"],
        "needs_confirmation": window["needs_confirmation"],
        "confirmed_at": window["confirmed_at"],
        "confirmed_by": window["confirmed_by"],
        "created_at": window["created_at"],
        "updated_at": window["updated_at"],
        "cancelled_at": window["cancelled_at"],
        "ended_at": window["ended_at"],
    }


def situation_marker(
    row: dict[str, Any],
    situation_nes: dict[int, list[int | None]],
    markers: dict[int, dict[str, Any]],
) -> dict[str, Any] | None:
    """The maintenance marker for one situation row, or `None` (IV.3).

    A situation is marked when **any** of its member elements is under a window. That is the
    honest reading rather than a convenient one: the operator opens the situation, and one
    suppressed element is enough to make its alarm counts incomplete — telling them only when
    *every* member is suppressed would withhold the marker in exactly the mixed case where it
    matters most.

    The **earliest-ending** window wins when several members are under different ones, because
    the question the badge answers is *"when does this stop being incomplete?"* and the first
    answer an operator can act on is the soonest.

    Returns the marker `store.window_markers` built and nothing of this route's own: a name or an
    owner reaching a viewer through here would be `visibility` bypassed on a route nobody thought
    to check.
    """
    if not markers:
        return None
    found = [
        markers[ne]
        for ne in situation_nes.get(int(row["id"]), [])
        if ne is not None and ne in markers
    ]
    return min(found, key=lambda m: float(m["ends_at_effective"])) if found else None
