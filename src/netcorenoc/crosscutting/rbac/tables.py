"""The authorization tables — the compiled authority, and all of their prose.

**This module is the single source of truth for authorization.** `policy.py` computes answers from
these tables; `__init__.py` re-exports them **by identity**, not by copy. Nothing else in the
package may bind any of these names at module level, and
`tests/test_rbac.py::test_the_tables_are_re_exported_by_identity_not_by_copy` and its sibling assert
both facts — because a second source of truth for authorization would be worse than the debt the
v0.7.4 split removed (DECISIONS #96).

**v0.21.0 splits this file in two, at the 400-line guard and on the seam it already had.**
`tables.py` is *what a role may ever hold*; `route_map.py` is *which capability each route
requires*. DECISIONS #87 recorded that neither the table nor its justifications could be traded
away to get under the guard and that splitting is the fix — this is that fix applied a second
time, and the two module-level assertions about the route tables moved with the tables they
constrain, which is the rule this file states about its own assertions.

`__init__.py` re-exports both halves **by identity**, so `rbac.ROUTE_PERMISSIONS` resolves exactly
as it did and the single-source-of-truth guard is unchanged.

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

The module-level ``assert`` statements below are assertions *about these tables*, evaluated at
import. They live here, with what they constrain: an assertion separated from its table stops
running at the moment the table is defined, which would delete three structural guarantees while
every test stayed green (DECISIONS #96).
"""

from __future__ import annotations

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
    # v0.18.0 (Part II): how the RUNNING scorer is behaving — accept rate, score distribution,
    # which term is carrying the links. Same class and same reasoning as `scorer.read`: it
    # explains grouping, it is aggregate, and it names no network element. An operator who may
    # see the formula may see whether it is working.
    "correlation.read": "viewer",
    # Whether the models are learning, and how far the evidence is from the registered
    # floors. Same class again: counts of the operator's own judgements and a loss curve
    # over them, naming no network element. Registering one of this appliance's own fits is
    # a different act and is admin — it creates an artefact a promotion could later name.
    "model.read": "viewer",
    "model.register": "admin",
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
    # v0.21.0 — maintenance windows.
    #
    # **`mw.read` is `viewer`, and that is prime directive 4 rather than a convenience.** A host
    # that goes quiet with no marker reads as healthy, which is how maintenance windows hide
    # outages. So every authenticated role may learn that a window exists, what state it is in and
    # when it ends. What they may *not* see is its name, its owner, its description and its rules,
    # and that is `visibility` — enforced in the query, never in the render.
    "mw.read": "viewer",
    # Declaring that an estate will stop reporting is an operational act, so it is `editor` — the
    # same rank as closing a situation or clearing an alarm. It is deliberately NOT admin: the
    # engineer who is about to do the work is the person who knows when it starts.
    "mw.write": "editor",
    # **A separate capability from `mw.write`, and #256's reasoning exactly.** `mw.write` is the
    # power to *declare* planned work; this is the power to *agree* that a window longer than six
    # hours may take effect. `resolve_capabilities` is `ceiling ∩ policy`, so two capabilities let
    # a deployment grant "you may schedule" without "you may approve your own" — four-eyes as a
    # configuration rather than as a rule invented here (ADR #370). One capability makes that
    # arrangement unreachable.
    "mw.confirm": "editor",
    # Reading a window's **ledger counts** — how many fingerprints it saw raise, how many it saw
    # clear. `admin`, and **its own capability rather than a role test in the handler**, which is
    # F28's rule: authorization goes through `resolve_capabilities`, never through a comparison a
    # reader has to find. The ledger is not a view of the network and this is the one place it can
    # be read at all, so a deployment that wants nobody reading it withholds this and keeps the
    # rest of the resource working.
    "mw.ledger": "admin",
    # Which provider an element belongs to. `viewer` for the read because a window's card names
    # an organization and a screen showing a name nobody can look up is a dead end; admin for the
    # write because it is inventory structure rather than operation.
    "organizations.read": "viewer",
    "organizations.write": "admin",
    # The zones this host can resolve. `viewer`, and it names no network element at all — it is a
    # property of the operating system's `tzdata`, which is public information about a public
    # database.
    "timezones.read": "viewer",
    # v0.22.0. **Snoozing a warning for oneself** (ADR #387). `viewer`, because every role is
    # shown the warnings and a snooze is per user — it changes one operator's bell and nothing
    # anyone else sees. Its own capability so a deployment can refuse snoozing outright.
    "notice.snooze": "viewer",
    # v0.22.0. **Acknowledging the marker on a fault that outlived a maintenance window** (#388).
    # `editor`, the rank that clears an alarm: it stops a marker being drawn for everybody, so it
    # is an operational act on the alarm, and weaker than a clear — the alarm stays active.
    "alarm.acknowledge": "editor",
    # v0.22.0. **Declaring and importing trap-catalogue rules** (#384, #385). A rule names and
    # grades a trap OID or a whole branch — `label.write`'s power over a class, generalised — and an
    # import is many of them at once, so it is a capability of its own a deployment can withhold.
    "catalogue.write": "editor",
    "catalogue.import": "editor",
    # **The poller's two capabilities are NOT here**, and their absence is the decision rather
    # than an oversight. D3 slips to v0.21.1 (ADR #373, HANDOFF §1), and a capability with no
    # route behind it is a placeholder — which `ui/app/registry.js` already refuses, on the ground
    # that a promise of a feature nobody has built teaches an operator that parts of the product
    # do not work. They arrive with the routes that need them.
}

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
