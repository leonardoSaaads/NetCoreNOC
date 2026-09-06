"""The authorization tables — the compiled authority, and all of their prose.

**This module is the single source of truth for authorization.** `policy.py` computes answers from
these tables; `__init__.py` re-exports them **by identity**, not by copy. Nothing else in the
package may bind any of these names at module level, and
`tests/test_rbac.py::test_the_tables_are_re_exported_by_identity_not_by_copy` and its sibling assert
both facts — because a second source of truth for authorization would be worse than the debt the
v0.7.4 split removed (DECISIONS #96).

`PERMISSIONS` maps each capability to the minimum role that holds it; `ROUTE_PERMISSIONS` maps each
registered ``/api`` route (method, templated path) to the capability it requires. Both the FastAPI
security dependency and the authorization-matrix and fail-closed tests read these tables — there is
never a second copy. A route absent from `ROUTE_PERMISSIONS` (and not in `PUBLIC_ROUTES`) fails
closed at runtime and fails CI.

**The compiled map is explicitly the CEILING** (v0.7.0). An admin may store a policy that *narrows*
what a role or principal holds; :func:`netcorenoc.rbac.resolve_capabilities` computes the answer as
``ceiling(role) ∩ granted(role) ∩ granted(principal)``, and an intersection cannot exceed its first
operand. See `policy.py` for the full statement of that guarantee.

**The prose is not decoration.** DECISIONS #87 records that `ROUTE_SCOPE` pushed `rbac.py` past the
400-line guard and that neither the table nor its justifications could be traded away to get under
it — splitting is the fix that keeps both. Every ``"unscoped"`` justification comment travels with
its entry, and this file is read to assert it by
`tests/test_declaration.py::test_every_unscoped_declaration_carries_a_written_justification`.

The three module-level ``assert`` statements below are assertions *about these tables*, evaluated at
import. They live here, with what they constrain: an assertion separated from its table stops
running at the moment the table is defined, which would delete three structural guarantees while
every test stayed green (DECISIONS #96).
"""

from __future__ import annotations

from typing import Literal

ROLE_RANK: dict[str, int] = {"viewer": 0, "editor": 1, "admin": 2}

# Capability -> minimum role that holds it. viewer ⊆ editor ⊆ admin.
PERMISSIONS: dict[str, str] = {
    # any authenticated principal
    "self.read": "viewer",
    # read / stream (viewer+)
    "stats.read": "viewer",
    "graph.read": "viewer",
    "classes.read": "viewer",
    "situations.read": "viewer",
    "timeline.read": "viewer",
    "events.stream": "viewer",
    "entities.read": "viewer",  # v0.3.0: entity tree + varbind profiler (inspectable)
    # v0.6.0: the active scorer id, its parameters, and the per-term contributions EXPLAIN
    # grouping — they are not a secret, so every authenticated role may read them.
    "scorer.read": "viewer",
    # operate (editor+)
    "feedback.write": "editor",
    "label.write": "editor",
    "situation.close": "editor",
    # v0.16.0 (DECISIONS #256). **Four capabilities, not one**, and not `feedback.write` either.
    # `feedback.write` is the power to *record an opinion*; these are the power to *restructure the
    # record*. A merge mutates `situation_alarm` and changes what every later label refers to, and
    # an editor who may say "this grouping is wrong" is not obviously an editor who may rewrite it.
    # `resolve_capabilities` is `ceiling ∩ policy`, so four capabilities let a deployment grant
    # labelling without restructuring — a configuration one capability makes unreachable.
    "situation.move": "editor",
    "situation.merge": "editor",
    "situation.split": "editor",
    # A zombie clear is a fact about an ALARM, not about a grouping, and its capability says so:
    # `alarm.clear`, not `situation.clear` (`PREREGISTRATION-0.16.0.md` §1).
    "alarm.clear": "editor",
    # v0.16.2 (`PREREGISTRATION-0.16.2.md` §2.2, DECISIONS #273). **The weakest write this API
    # has**: it moves a situation from `new` to `open` and changes nothing else — no label, no
    # training row, no membership. Its own capability rather than `feedback.write` for #256's
    # reason exactly: `feedback.write` is the power to record an OPINION, and the whole point of
    # this action is that it records none. Separating them lets a deployment grant "you may pick
    # up work" without "you may teach the correlator", which is a real shift arrangement — a
    # junior triager — that one capability makes unreachable.
    "situation.promote": "editor",
    # administer (admin only)
    "users.manage": "admin",
    "entity.reset": "admin",  # v0.3.0: reset an NE's learned entity key (audited)
    "profile.reset": "admin",  # v0.3.0: wipe an NE's varbind profiler evidence (audited)
    "tokens.manage": "admin",
    "config.read": "admin",  # v0.4.0 (F9): reading runtime config is its own capability…
    "config.write": "admin",  # …distinct from the write that changes it (least privilege)
    "quarantine.read": "admin",
    "audit.read": "admin",
    "audit.export": "admin",
    "audit.prune": "admin",
    # v0.6.0: retuning the link formula is a system-wide logic change, not an operational one.
    # Admin only, with NO editor delegation — deliberately stricter than the "optionally editor"
    # phrasing of the v0.5.0 extensibility draft (SCOPE-0.6 §2, DECISIONS #43).
    "scorer.preview": "admin",  # bounded, read-only what-if over recent alarms
    "scorer.write": "admin",  # append a config, move the active pointer, roll back
    # v0.11.0. Reading what has been proposed and refused is `viewer+` for `scorer.read`'s reason —
    # a refusal EXPLAINS why the correlator is still what it is, and names no network element.
    # PROPOSING a promotion is a system-wide logic change and is admin-only with NO editor
    # delegation, exactly as `scorer.write` is: this release adds no capability beyond these two.
    "promotion.read": "viewer",
    "promotion.write": "admin",
    # v0.7.0: governance. Who may do what, and who may see which NEs, is itself admin-only —
    # `config`-class, no delegation. Under `ceiling ∩ policy` that is structural rather than a
    # convention: no stored policy can move an admin-ceiling capability down to editor or viewer.
    "rbac.read": "admin",  # view the capability policy and each subject's resolved set
    "rbac.write": "admin",  # set, roll back, or clear the capability policy
    "scope.read": "admin",  # view the scoping policy and a principal's resolved NE set
    "scope.write": "admin",  # set, roll back, or clear the scoping policy
}

# Route (METHOD, templated path) -> required capability. THE authorization map.
ROUTE_PERMISSIONS: dict[tuple[str, str], str] = {
    ("POST", "/api/logout"): "self.read",
    ("GET", "/api/me"): "self.read",
    ("POST", "/api/password"): "self.read",
    ("GET", "/api/stats"): "stats.read",
    ("GET", "/api/graph"): "graph.read",
    ("GET", "/api/classes"): "classes.read",
    ("GET", "/api/situations"): "situations.read",
    ("GET", "/api/situations/{sid}"): "situations.read",
    ("GET", "/api/timeline"): "timeline.read",
    ("GET", "/api/events"): "events.stream",
    ("GET", "/api/entities"): "entities.read",
    ("GET", "/api/entities/{ne_id}"): "entities.read",
    ("GET", "/api/state-clears"): "entities.read",
    ("POST", "/api/entities/{ne_id}/reset"): "entity.reset",
    ("POST", "/api/profiles/{ne_id}/reset"): "profile.reset",
    ("POST", "/api/situations/{sid}/feedback"): "feedback.write",
    ("POST", "/api/labels"): "label.write",
    # Withdrawing a declaration is the same power as making one (DECISIONS #284): the derived
    # value it falls back to was never overwritten, so this restores a state the appliance
    # already held rather than destroying one it did not.
    ("DELETE", "/api/labels/{kind}/{target_id}"): "label.write",
    ("POST", "/api/situations/{sid}/close"): "situation.close",
    ("POST", "/api/situations/{sid}/move"): "situation.move",
    ("POST", "/api/situations/{sid}/merge"): "situation.merge",
    ("POST", "/api/situations/{sid}/split"): "situation.split",
    # A rename is a **label**, so it reuses `label.write` rather than inventing a fifth capability
    # (DECISIONS #260): naming a device, naming an alarm class and naming a situation are one power.
    # It gets its own ROUTE because the storage and the scope decision are the situation's.
    ("POST", "/api/situations/{sid}/name"): "label.write",
    ("POST", "/api/alarms/{aid}/clear"): "alarm.clear",
    # v0.16.5: the same gesture over a whole situation, and therefore **the same capability**. A
    # bulk clear is N single clears and nothing else — same event kind, same audit action, same
    # absence from `ASSERTING_KINDS` — so a second capability would let an operator hold one and
    # not the other over an act that has one meaning. It is not under `/api/situations` for the
    # reason `annotate.clear_alarm` states: a clear is a fact about an alarm's lifecycle, and the
    # correlation namespace would say otherwise in the URL.
    ("POST", "/api/alarms/clear"): "alarm.clear",
    # v0.16.2: promotion WITHOUT judging. `PREREGISTRATION-0.16.2.md` §2.2 registers it as one of
    # two distinct actions, and the other one is `POST …/feedback` with `verdict: confirm`, which
    # already existed and already asserts.
    ("POST", "/api/situations/{sid}/promote"): "situation.promote",
    ("GET", "/api/users"): "users.manage",
    ("POST", "/api/users"): "users.manage",
    ("DELETE", "/api/users/{uid}"): "users.manage",
    ("POST", "/api/users/{uid}/role"): "users.manage",
    ("GET", "/api/tokens"): "tokens.manage",
    ("POST", "/api/tokens"): "tokens.manage",
    ("DELETE", "/api/tokens/{tid}"): "tokens.manage",
    ("GET", "/api/config"): "config.read",
    ("POST", "/api/config"): "config.write",
    # v0.8.0. The dataset is a scope bypass by construction (captured engine-side, where visibility
    # scoping does not exist), so every route that touches it is `config`-class and admin-only —
    # and admin is never scoped. `GET` is the PREVIEW: read-only, bounded, and aggregate-only.
    ("GET", "/api/dataset/retention"): "config.read",
    ("POST", "/api/dataset/retention"): "config.write",
    ("GET", "/api/scorer"): "scorer.read",
    ("POST", "/api/scorer/preview"): "scorer.preview",
    ("POST", "/api/scorer"): "scorer.write",
    ("POST", "/api/scorer/rollback"): "scorer.write",
    ("GET", "/api/promotion"): "promotion.read",
    ("POST", "/api/promotion"): "promotion.write",
    ("GET", "/api/rbac"): "rbac.read",
    ("POST", "/api/rbac"): "rbac.write",
    ("GET", "/api/scope"): "scope.read",
    ("POST", "/api/scope"): "scope.write",
    ("GET", "/api/quarantine"): "quarantine.read",
    ("GET", "/api/audit"): "audit.read",
    ("GET", "/api/audit/export"): "audit.export",
    ("POST", "/api/audit/prune"): "audit.prune",
}

# The only /api routes reachable without a resolved identity.
PUBLIC_ROUTES: frozenset[tuple[str, str]] = frozenset({("POST", "/api/login")})

# Route (METHOD, templated path) -> its **visibility-scope posture**. v0.7.2.
#
# F34 existed because a route's scope posture was expressed *nowhere at all*: three editor write
# routes simply did not have one, and no table, test or reviewer could notice the omission. This
# is that missing declaration. It is **descriptive** in v0.7.2 — it records what each route already
# does after v0.7.1 — and `tests/test_declaration.py` asserts every entry against the route's
# observed behaviour. Making the perimeter *inject* the check from this table is a ROADMAP line,
# because injection changes control flow and control flow is behaviour (DECISIONS #80).
#
#   "scoped"      the response depends on the caller's resolved visibility scope: the route
#                 resolves scope and either filters what it returns or denies an out-of-scope
#                 target through the same 404 a nonexistent one would take (DECISIONS #60).
#   "unscoped"    the response does NOT depend on the caller's scope: a scoped and an unscoped
#                 caller of the same role receive the same body. Every entry carries its reason.
#   "admin_only"  the capability's minimum role is `admin`, and **admin is never scoped**
#                 (DECISIONS #58), so the question does not arise. This is a *derived* claim, not
#                 a second authority: the assertion below re-derives it from `PERMISSIONS` in both
#                 directions at import, so the two tables cannot disagree.
#
# `PUBLIC_ROUTES` is exempt from this table exactly as it is from `ROUTE_PERMISSIONS`, and the
# registration gate says so by consulting it rather than by finding nothing here.
ROUTE_SCOPE: dict[tuple[str, str], Literal["scoped", "unscoped", "admin_only"]] = {
    # Acts on the caller's own session; references no network element.
    ("POST", "/api/logout"): "unscoped",
    # Reports the caller's own scope summary (scoped?, ne_count), so the body follows the policy.
    ("GET", "/api/me"): "scoped",
    # Acts on the caller's own password; references no network element.
    ("POST", "/api/password"): "unscoped",
    ("GET", "/api/stats"): "scoped",
    ("GET", "/api/graph"): "scoped",
    # An alarm class is a *kind of trap*, not a network element, and the table carries no NE
    # reference. The count that would leak — "a device you cannot see just emitted a new trap
    # type" — is `stats.classes`, and that one is scoped.
    ("GET", "/api/classes"): "unscoped",
    ("GET", "/api/situations"): "scoped",
    ("GET", "/api/situations/{sid}"): "scoped",
    ("GET", "/api/timeline"): "scoped",
    ("GET", "/api/events"): "scoped",
    ("GET", "/api/entities"): "scoped",
    ("GET", "/api/entities/{ne_id}"): "scoped",
    # Learned state fields are keyed on (class, varbind OID) — a property of a trap type, not of
    # any NE. Same reasoning as `/api/classes`.
    ("GET", "/api/state-clears"): "unscoped",
    ("POST", "/api/entities/{ne_id}/reset"): "admin_only",
    ("POST", "/api/profiles/{ne_id}/reset"): "admin_only",
    ("POST", "/api/situations/{sid}/feedback"): "scoped",
    ("POST", "/api/labels"): "scoped",
    ("DELETE", "/api/labels/{kind}/{target_id}"): "scoped",
    ("POST", "/api/situations/{sid}/close"): "scoped",
    # All five name a network element and all five are below `admin`, so all five are the write
    # perimeter F34 established. Move and merge name **two** situations and check both.
    ("POST", "/api/situations/{sid}/move"): "scoped",
    ("POST", "/api/situations/{sid}/merge"): "scoped",
    ("POST", "/api/situations/{sid}/split"): "scoped",
    ("POST", "/api/situations/{sid}/name"): "scoped",
    ("POST", "/api/alarms/{aid}/clear"): "scoped",
    # Scoped, and the handler derives its whole working set from what the scope permits rather than
    # from the request body: `situation_members` minus `hidden_member_ids`. So the posture is not
    # just declared here, it is the only way the route can reach a row at all.
    ("POST", "/api/alarms/clear"): "scoped",
    ("POST", "/api/situations/{sid}/promote"): "scoped",
    ("GET", "/api/users"): "admin_only",
    ("POST", "/api/users"): "admin_only",
    ("DELETE", "/api/users/{uid}"): "admin_only",
    ("POST", "/api/users/{uid}/role"): "admin_only",
    ("GET", "/api/tokens"): "admin_only",
    ("POST", "/api/tokens"): "admin_only",
    ("DELETE", "/api/tokens/{tid}"): "admin_only",
    ("GET", "/api/config"): "admin_only",
    ("POST", "/api/config"): "admin_only",
    ("GET", "/api/dataset/retention"): "admin_only",
    ("POST", "/api/dataset/retention"): "admin_only",
    # The five parameters of the active scorer and its immutable history. They *explain* every
    # grouping decision and name no network element, so every authenticated role reads the same
    # numbers (SCOPE-0.6 §2).
    ("GET", "/api/scorer"): "unscoped",
    # Same reasoning one release on: a promotion decision is about the SCORER, not about a network
    # element, and its row names no NE. Scoping the READ would be scoping a statement about
    # arithmetic. The WRITE is `admin_only` because its capability's minimum role is `admin` — the
    # posture is DERIVED from PERMISSIONS and never asserted independently (DECISIONS #58, #80).
    ("GET", "/api/promotion"): "unscoped",
    ("POST", "/api/promotion"): "admin_only",
    ("POST", "/api/scorer/preview"): "admin_only",
    ("POST", "/api/scorer"): "admin_only",
    ("POST", "/api/scorer/rollback"): "admin_only",
    ("GET", "/api/rbac"): "admin_only",
    ("POST", "/api/rbac"): "admin_only",
    ("GET", "/api/scope"): "admin_only",
    ("POST", "/api/scope"): "admin_only",
    ("GET", "/api/quarantine"): "admin_only",
    ("GET", "/api/audit"): "admin_only",
    ("GET", "/api/audit/export"): "admin_only",
    ("POST", "/api/audit/prune"): "admin_only",
}

assert set(ROUTE_SCOPE) == set(ROUTE_PERMISSIONS), (
    "ROUTE_SCOPE and ROUTE_PERMISSIONS must declare exactly the same routes: "
    f"only in ROUTE_SCOPE {sorted(set(ROUTE_SCOPE) - set(ROUTE_PERMISSIONS))}, "
    f"only in ROUTE_PERMISSIONS {sorted(set(ROUTE_PERMISSIONS) - set(ROUTE_SCOPE))}"
)
assert not [
    route
    for route, posture in ROUTE_SCOPE.items()
    if (posture == "admin_only") != (PERMISSIONS[ROUTE_PERMISSIONS[route]] == "admin")
], (
    "an `admin_only` posture is derived from PERMISSIONS, never asserted independently: a route "
    "is `admin_only` if and only if its capability's minimum role is `admin` (DECISIONS #58, #80)"
)

# Sensitive capabilities whose *denied* (403) attempts are still audited. THIS is the single
# source of truth for the audited-denied set: ``api.py`` derives its "should this denial be
# audited?" decision from here (mapping each to a representative catalog action), and
# ``tests/test_rbac.py::test_f8_audited_denied_single_source`` fails CI if the two ever diverge.
AUDITED_DENIED_PERMISSIONS: frozenset[str] = frozenset(
    {
        "quarantine.read",
        "audit.read",
        "audit.export",
        "audit.prune",
        "users.manage",
        "tokens.manage",
        "config.read",  # reading the allowlist reveals network-security posture (F9)
        "config.write",
        # v0.6.0 (F21): a denied attempt to retune or preview the correlation formula is an
        # attempted system-wide logic change — worth a row even though it failed. `scorer.read`
        # is deliberately NOT here: it is viewer+, so a denial only means "unauthenticated".
        "scorer.preview",
        "scorer.write",
        # v0.7.0 (F27): a denied attempt to read or rewrite the perimeter itself is an attempted
        # privilege change — worth a row even though it failed, and for the same reason
        # `config.read` is here: knowing the shape of the policy is knowing the shape of the
        # defences.
        "rbac.read",
        "rbac.write",
        "scope.read",
        "scope.write",
    }
)

# v0.7.0 (DECISIONS #64): capabilities an admin can never lose to a stored policy.
#
# `ceiling ∩ policy` otherwise lets a *well-formed* policy remove `rbac.write` from the `admin`
# role, leaving no authenticated path to repair the perimeter — a hard lockout that the
# malformed-policy fallback would never catch, because the policy is not malformed. These are
# unioned back for admin inside `resolve_capabilities`, and because the set is a **subset of
# ceiling("admin")** the union cannot leave the ceiling: `resolved ⊆ ceiling(role)` still holds for
# every input, which is the invariant the property test asserts.
#
# Deliberately tiny, and the *recovery* surface only: governance may still take `users.manage`,
# `audit.read`, `config.write` or `scorer.write` away from an admin. It simply may not brick the
# appliance.
RECOVERY_CAPABILITIES: frozenset[str] = frozenset(
    {"self.read", "rbac.read", "rbac.write", "scope.read", "scope.write"}
)

# Per-role ceilings, computed once from the compiled map. This is exactly v0.6.0's `role_allows`
# expressed as a set, which is what lets the resolver be an intersection.
_CEILINGS: dict[str, frozenset[str]] = {
    role: frozenset(
        capability
        for capability, minimum in PERMISSIONS.items()
        if ROLE_RANK[role] >= ROLE_RANK[minimum]
    )
    for role in ROLE_RANK
}

assert _CEILINGS["admin"] >= RECOVERY_CAPABILITIES, (
    "RECOVERY_CAPABILITIES must be a subset of the admin ceiling, or unioning it back in "
    "resolve_capabilities() would breach the ceiling invariant (DECISIONS #64)"
)
