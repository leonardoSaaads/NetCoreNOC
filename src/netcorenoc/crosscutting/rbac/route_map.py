"""The route map: which capability each ``/api`` route requires, and its scope posture.

Split from :mod:`netcorenoc.crosscutting.rbac.tables` in v0.21.0, at the 400-line guard and on the
seam that file already had. `tables.py` answers *"what may this role ever hold?"*; this answers
*"what does this route need, and does its answer depend on who is asking?"*. DECISIONS #87
recorded that neither the table nor its justifications could be traded away to get under the guard
and that splitting is the fix; this is that fix applied a second time.

**Nothing here decides anything.** `policy.py` computes capability answers, the perimeter enforces
them, and this is the authority they read. A route absent from :data:`ROUTE_PERMISSIONS` (and not
in :data:`PUBLIC_ROUTES` below) fails closed at runtime and fails CI.

**The prose is not decoration.** Every ``"unscoped"`` justification comment travels with its entry,
and this file is read to assert it by
`tests/test_declaration.py::test_every_unscoped_declaration_carries_a_written_justification`.

The two module-level ``assert`` statements moved here with the tables they constrain, which is the
rule `tables.py` states about its own: an assertion separated from its table stops running at the
moment the table is defined, which would delete a structural guarantee while every test stayed
green.
"""

from __future__ import annotations

from typing import Literal

from netcorenoc.crosscutting.rbac.tables import PERMISSIONS

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
    ("GET", "/api/correlation"): "correlation.read",
    ("GET", "/api/models"): "model.read",
    ("POST", "/api/models/register"): "model.register",
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
    # v0.21.0 — maintenance windows. `preview` is `mw.read` rather than `mw.write`: it is a dry
    # run that writes nothing, and an operator who may see planned work may ask what a plan would
    # cover. It is bounded by the caller's own scope, so it is not an existence oracle.
    ("GET", "/api/maintenance-windows"): "mw.read",
    ("POST", "/api/maintenance-windows/preview"): "mw.read",
    ("POST", "/api/maintenance-windows"): "mw.write",
    ("GET", "/api/maintenance-windows/{wid}"): "mw.read",
    ("POST", "/api/maintenance-windows/{wid}"): "mw.write",
    ("POST", "/api/maintenance-windows/{wid}/confirm"): "mw.confirm",
    ("POST", "/api/maintenance-windows/{wid}/cancel"): "mw.write",
    ("POST", "/api/maintenance-windows/{wid}/end"): "mw.write",
    ("POST", "/api/maintenance-windows/{wid}/extend"): "mw.write",
    ("GET", "/api/organizations"): "organizations.read",
    ("POST", "/api/organizations"): "organizations.write",
    # Which provider owns one element. **The same capability as creating an organization**,
    # because a feature whose only reachable state is its seeded default is not a feature:
    # without this route D1 is a column nobody can set. Not `label.write` — a rename is an
    # operator describing a thing, and this is a statement about who owns it.
    ("POST", "/api/entities/{ne_id}/organization"): "organizations.write",
    ("GET", "/api/timezones"): "timezones.read",
    # v0.22.0 (ADR #380): the host's readings over a window. The same capability as the current
    # reading, which `/api/stats` has served to every role since v0.16.5.
    ("GET", "/api/resources"): "stats.read",
    # v0.22.0 (ADR #381): alarm activity over a window, for the Timeline and the Overview. The
    # same capability as the marks they replace on screen, because they are the same rows counted.
    ("GET", "/api/activity/severity"): "timeline.read",
    ("GET", "/api/activity/lanes"): "timeline.read",
    ("GET", "/api/activity/groups"): "timeline.read",
    # v0.23.0 (#393): active alarms over time, overall and for the busiest elements.
    ("GET", "/api/activity/active"): "timeline.read",
    ("GET", "/api/activity/top"): "timeline.read",
    # v0.23.0 (#395): the Entities screen's inventory, and one element's learned components.
    ("GET", "/api/inventory"): "entities.read",
    ("GET", "/api/elements/{ne_id}/components"): "entities.read",
    # v0.22.0 (item 11): one element for the graph's selection panel. The Entities screen's
    # capability, because it is the same element seen from the graph.
    ("GET", "/api/elements/{ne_id}"): "entities.read",
    # v0.22.0 (items 1, 8): what is shown, per user or per alarm; never what is known.
    ("GET", "/api/notices"): "stats.read",
    ("POST", "/api/notices/snooze"): "notice.snooze",
    ("DELETE", "/api/notices/snooze/{digest}"): "notice.snooze",
    ("POST", "/api/alarms/{aid}/outlived/ack"): "alarm.acknowledge",
    # v0.22.0 (items 16-18): the trap catalogue. Reading it is reading the classes; a rule is a
    # declaration about a trap OID or a branch; an import is many at once.
    ("GET", "/api/catalogue"): "classes.read",
    ("GET", "/api/catalogue/tree"): "classes.read",
    ("GET", "/api/catalogue/rules"): "classes.read",
    ("POST", "/api/catalogue/rules"): "catalogue.write",
    ("DELETE", "/api/catalogue/rules/{rule_id}"): "catalogue.write",
    ("POST", "/api/catalogue/import"): "catalogue.import",
    ("DELETE", "/api/catalogue/imported"): "catalogue.import",
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
    # Counters over the scorer's own decisions. Aggregate over the whole estate by construction —
    # correlation learns across it — and naming no element, so scoping it would be scoping a
    # statement about arithmetic, exactly as for `/api/scorer` above. **It is an observability
    # surface and never evidence**: it is read-only, it is in memory, and no promotion path
    # reads it.
    ("GET", "/api/correlation"): "unscoped",
    # The models: floors, counts and a loss curve. Unscoped for the third time for the same
    # reason — arithmetic and tallies of judgements, no element named. The register POST is
    # `admin_only` because its capability's minimum role is `admin`; the posture is DERIVED
    # from PERMISSIONS above and never asserted here independently (DECISIONS #58, #80).
    ("GET", "/api/models"): "unscoped",
    ("POST", "/api/models/register"): "admin_only",
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
    # v0.21.0 — maintenance windows. **Every one of these is `scoped`**, and it is the strongest
    # scoping in the API rather than the weakest: a window names network elements, so every route
    # here resolves the caller's visibility and narrows what it reads, what it writes and what it
    # counts. A window naming an element outside the caller's scope is created over the elements
    # they can see, with no error and no differing count — indistinguishable from naming one that
    # does not exist (the no-existence-oracle rule, `api/routes/maintenance.py`).
    ("GET", "/api/maintenance-windows"): "scoped",
    ("POST", "/api/maintenance-windows/preview"): "scoped",
    ("POST", "/api/maintenance-windows"): "scoped",
    ("GET", "/api/maintenance-windows/{wid}"): "scoped",
    ("POST", "/api/maintenance-windows/{wid}"): "scoped",
    ("POST", "/api/maintenance-windows/{wid}/confirm"): "scoped",
    ("POST", "/api/maintenance-windows/{wid}/cancel"): "scoped",
    ("POST", "/api/maintenance-windows/{wid}/end"): "scoped",
    ("POST", "/api/maintenance-windows/{wid}/extend"): "scoped",
    # An organization is a row naming a provider, and the table carries no NE reference at all.
    # The count that would leak — *"a provider you cannot see has 40 elements"* — is `ne_count`,
    # and that one is the reason this is worth a sentence rather than a shrug: the count is over
    # the whole estate. It is admitted here as **unscoped and aggregate**, on the same terms as
    # `/api/classes`: it names no element, and the per-element question is `/api/entities`, which
    # is scoped. A deployment that considers the provider list itself sensitive withholds
    # `organizations.read` from the role, which `ceiling ∩ policy` makes a one-line policy.
    ("GET", "/api/organizations"): "unscoped",
    ("POST", "/api/organizations"): "admin_only",
    ("POST", "/api/entities/{ne_id}/organization"): "admin_only",
    # A property of the host's `tzdata`. Public information about a public database, naming no
    # element and no principal.
    ("GET", "/api/timezones"): "unscoped",
    # A CPU percentage and a queue depth over time. They describe the appliance's own host, name no
    # network element and no principal, and every role already reads the current value of each.
    ("GET", "/api/resources"): "unscoped",
    ("GET", "/api/activity/severity"): "scoped",
    ("GET", "/api/activity/lanes"): "scoped",
    ("GET", "/api/activity/groups"): "scoped",
    ("GET", "/api/activity/active"): "scoped",
    ("GET", "/api/activity/top"): "scoped",
    ("GET", "/api/inventory"): "scoped",
    ("GET", "/api/elements/{ne_id}/components"): "scoped",
    ("GET", "/api/elements/{ne_id}"): "scoped",
    # The warnings are the appliance's about itself — the same list `/api/stats` has always served
    # every role unscoped (F107 took the addresses out of them) — and a snooze is the caller's own.
    ("GET", "/api/notices"): "unscoped",
    # Writes a row keyed on the caller's own user id and a warning's text; names no element.
    ("POST", "/api/notices/snooze"): "unscoped",
    # Deletes only the caller's own snooze row; another user's digest answers the same 404.
    ("DELETE", "/api/notices/snooze/{digest}"): "unscoped",
    ("POST", "/api/alarms/{aid}/outlived/ack"): "scoped",
    # A trap OID is a kind of trap, not a network element, and no catalogue row names one — the
    # same reasoning as `/api/classes`. The per-class active count is over the whole estate, as
    # `/api/classes`' existence of a class already is.
    ("GET", "/api/catalogue"): "unscoped",
    # The OID branches under a node and the vendors IANA assigned; a property of trap types.
    ("GET", "/api/catalogue/tree"): "unscoped",
    # The rules themselves: an OID, a name, a severity, a source. No element column exists.
    ("GET", "/api/catalogue/rules"): "unscoped",
    # Names or grades a trap type for the whole estate; it can name no element, so there is no
    # element for a scope to narrow. The capability is `catalogue.write` (editor).
    ("POST", "/api/catalogue/rules"): "unscoped",
    # Withdraws one such rule; same reasoning as the write it reverts.
    ("DELETE", "/api/catalogue/rules/{rule_id}"): "unscoped",
    # A file of OID → name/severity rows. Every row is validated to name a trap type only, and
    # the import is bounded, all-or-nothing and audited (ADR #385).
    ("POST", "/api/catalogue/import"): "unscoped",
    # The undo for every import; touches `source='imported'` rows only, never a declared rule.
    ("DELETE", "/api/catalogue/imported"): "unscoped",
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
