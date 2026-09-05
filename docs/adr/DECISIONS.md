# Decisions

Every scope-ambiguity resolution and notable engineering choice, numbered, with the reason. The
format and the rules that govern this file — about six lines an entry, numbered in sequence and
never renumbered, superseded by reference rather than by edit — are in [`README.md`](README.md).

**v0.15.0 removed the 50 entries that no code and no live document cited, and condensed the 146 that
survive** (#201). Nothing was renumbered: `src/` and `tests/` cite **130 distinct numbers in 295
places**, several as *"argued in #N rather than asserted"*, and
`tests/test_documentation.py::test_every_decision_number_cited_in_the_tree_resolves_to_an_entry`
fails if one of them stops resolving. The full original text of every entry — deleted or
condensed — is at commit `3ecf237`; [`../record.md`](../record.md) has the command, and it is worth
using when an entry below reads as an assertion rather than an argument: the argument is there.

# v0.1.0 – v0.4.0 — the correlator, identity, the entity model, hardening

## 3. Alarm instance heuristic

- **Decision**: `ifIndex` when present, else the first payload varbind's value, else empty; capped
  at 120 characters. (v0.1.0)
- **Reason**: `ifIndex` is the standard instance for the built-in link traps, and for vendor traps
  the first payload varbind is in practice the entity identifier. A volatile one degrades dedup
  gracefully rather than breaking it.

## 15. Auth throttle and login lockout are in-memory, single process

- **Decision**: in memory, matching the existing rate limiter; lockouts still audited durably.
  (v0.2.0)
- **Reason**: a single-node NOC tool restarts rarely, so the reset-on-restart window is acceptable
  and recorded as residual risk. Durable lockout is a roadmap item if this ever runs multi-node.

## 21. SNMPv1 `ne.ip` is the UDP source, not the trap's agent-address

- **Decision**: identity is the UDP source; `agent-addr` stays visible as a varbind and is never
  trusted as identity. (v0.3.0)
- **Reason**: the UDP source is the same identity v2c uses and is not spoofable at the application
  layer. Security-relevant ambiguity resolves toward the stricter option.

## 22. Migration `0003` is additive; the v0.2.0 alarm UNIQUE is kept for one version

- **Decision**: add the entity-based unique index beside the device-based one rather than replacing
  it. (v0.3.0)
- **Reason**: the additive path applies cleanly onto a populated database and avoids toggling
  foreign keys inside a migration. The two keys never conflict, because an alarm's instance always
  carries its entity's discriminator.

## 29. `OPTICORR_API_TOKEN` is removed, and its tests move to service tokens

- **Decision**: setting it is a hard startup error naming the migration path; the retained tests
  mint a real admin service token. (v0.3.0)
- **Reason**: a deliberate behaviour change, and the replacements are stronger — they pin the
  hard-error behaviour *and* prove the sanctioned replacement grants the same access.

## 34. Rebrand to NetCoreNOC — a gated rename

- **Decision**: package, environment prefix, cookie and CSRF header renamed, with the legacy
  variables accepted for one version. (v0.4.0)
- **Reason**: the rename changes names, never behaviour, and the gate proves it: 226 tests green
  under the new package name and a **byte-identical** `make eval` delta against the frozen baseline.
  Only the printed header brand changed.

## 35. Re-defer the `device_id` → `entity_id`/`ne_id` cutover

- **Decision**: not in v0.4.0; recorded in the roadmap rather than left silently half-done. (v0.4.0)
- **Reason**: a hardening release, and the cutover is behaviour-adjacent churn on the hottest path
  with no safety benefit. Still open.

## 36. Re-defer typed relations and device-archetype clustering

- **Decision**: not in v0.4.0. (v0.4.0)
- **Reason**: both are new inference in a release that adds none. Archetype clustering is what
  v0.17.0 would need and still does not have.

## 37. New fault and abuse scenarios are engine-driven tests, not scored corpus additions

- **Decision**: author them through the declarative DSL and assert the outcome; the scored corpus
  stays frozen. (v0.4.0)
- **Reason**: adding scored scenarios changes the denominators and moves the gated metrics off the
  frozen baseline, for no benefit — a scenario's *correctness* is exactly what a targeted assertion
  checks.

## 38. Role-aware UI: admin screens are pruned from the DOM, gating verified statically

- **Decision**: remove every panel whose role the caller lacks; verify by static analysis rather
  than a browser. (v0.4.0)
- **Reason**: a browser dependency for a P1 concern was outside the dev whitelist, and the
  security-relevant properties are assertable without one. **Superseded in v0.12.0 by #167.**

# v0.5.0 — structure and growth readiness

## 39. Extend the legacy env-alias deprecation window by one release

- **Decision**: keep the aliases and their warning; move the removal target. (v0.5.0)
- **Reason**: removing a still-warned compatibility path inside an organisation release is exactly
  the unrelated breaking change that release exists to avoid. It is a non-removal — strictly more
  compatible than the alternative.

# v0.6.0 — the scoring seam

## 43. The three configurability surfaces are resequenced across three releases

- **Decision**: v0.6.0 builds **only** the scoring seam; RBAC and scoping go to v0.7.0, customer
  models to what is now v0.16.0. (v0.6.0)
- **Reason**: three different risk profiles must not share a release. Scoring changes the *engine*
  and is gated by exact parity — a gate that only means something when nothing else can move a
  number. RBAC changes the *security perimeter*, where the failure is silent escalation. Customer
  models add a *new dependency and a new trust surface*. Reviewing them separately is the only way
  each review can be honest.

## 44. The external-API scoring criterion is rejected on the correlation hot path

- **Decision**: no outbound call ever decides a link; an external signal, if ever wanted, is
  advisory or offline. (v0.6.0)
- **Reason**: a per-decision network call at trap rate is a self-inflicted denial of service and an
  SSRF surface on the one path this project treats as sacred. The right answer to *"the formula is
  too rigid"* is a swappable **local** scorer. The strictest option is not having the socket, and
  `score()` is typed pure so the type system forecloses it.

## 45. The legacy `OPTICORR_*` environment aliases are removed

- **Decision**: any legacy variable present raises at startup, naming its replacement. (v0.6.0)
- **Reason**: a removed knob that silently no-ops is the worst outcome — an operator still setting
  the legacy allowlist would believe traps are filtered while every source is accepted. That is a
  *security* regression dressed as a compatibility one, and extending the window twice would make
  the deprecation meaningless.

## 46. Parameter bounds reject the degenerate, not merely the out-of-range

- **Decision**: named constants with a rationale each, plus a **reachability rule** — the threshold
  must sit a margin below the maximum achievable score and strictly above zero. One validation
  function; an invalid set is a 4xx that is never stored. (v0.6.0)
- **Reason**: a too-tight bound costs a little tuning range; a too-loose one lets an admin shatter
  or collapse every incident on a production NOC. Preview is a *warning* control, so the store must
  refuse the shape that cannot be right.

## 47. Provenance by reference: `situation.scorer_config_id`, not a copy of the parameters

- **Decision**: immutable append-only configuration rows, a one-row active pointer, and the id
  stored on each situation. Rollback re-points and never mutates. (v0.6.0)
- **Reason**: copying the parameters creates a second source of truth that can disagree with the
  table, and deriving *"what was active at time T"* from the audit log is archaeology a
  post-incident review cannot afford to get wrong. A foreign key answers the question by lookup.

## 48. Preview is a bounded in-memory re-partition of recent alarms, not an `eval/` run

- **Decision**: at most 5 000 recent alarms through the engine's own correlator in read-only mode,
  bounded by an alarm cap **and** a wall-clock budget, importing nothing from `eval/`. (v0.6.0)
- **Reason**: running the corpus harness would make a dev harness a runtime dependency of the HTTP
  surface and answer the wrong question — corpus behaviour, not *this operator's*. The learned
  matrices are held fixed because they are an input to a what-if, not an output. Preview reflects a
  recent window, so it is directional rather than exhaustive, and says so.

## 49. `LinkFeatures` reserves optional slots now, so later features are a minor bump

- **Decision**: carry seven `| None` fields, unused today. **Adding an optional field is a minor
  bump; changing or removing one is a major bump**, and a configuration whose declared major version
  the running code does not support is refused at activation. (v0.6.0)
- **Reason**: without the reservation the first real extension is a breaking change that would
  strand exactly the customer-supplied scorers the seam exists to enable.

## 50. Explainability becomes contractual: `LinkScore.terms`, not three fixed columns

- **Decision**: a sequence of named contributions; the store keeps writing the three columns, which
  is what keeps the schema, the API, the UI and the tests byte-identical. (v0.6.0)
- **Reason**: three fixed columns would force a future scorer to fabricate a class affinity it does
  not compute, or to lose its explanation — and the project's whole claim is that a grouping is
  auditable by inspection. General *requirement*, specific *storage* is the only combination that
  preserves today's bytes and leaves room for a scorer with five terms.

## 51. The ingest-latency envelope is skipped under a tracer, not widened

- **Decision**: skip the wall-clock bound when a line tracer is active, with the measurement written
  into the test. (v0.6.0)
- **Reason**: the **unmodified previous tree** breached the same bound under coverage and passed
  without it, so the breach is a property of the tracer. Widening a threshold to match a number
  rather than because the system changed silently weakens it for the untraced runs where it measures
  something. Zero trap loss stays unconditional.

## 52. The scoring contract types are `NamedTuple`s, not frozen dataclasses

- **Decision**: named tuples, with the terms a `tuple`. Measured **4.7×** faster than the dataclass
  and **+3.5 %** end-to-end on a full corpus replay — scoring was never the bottleneck. (v0.6.0)
- **Reason**: the rare case where the faster option is also the *stricter* one. A named tuple is
  genuinely immutable rather than raising on assignment, and returning a tuple means a scorer hands
  out its explanation rather than lending a mutable list. A "skip the guard for the built-in scorer"
  fast path was **rejected**: complicating the fail-safe wrapper to recover a fraction of 3.5 % is a
  bad trade.

# v0.7.0 — governance

## 53. The stored capability policy is an **intersection** with the ceiling, not a grant table

- **Decision**: resolved capabilities are `ceiling ∩ policy`; write-time rejection is a usability
  affordance and never the security control. (v0.7.0)
- **Reason**: an intersection cannot exceed its first operand, so an above-ceiling policy row is not
  *rejected* but **inert** — however it got there: a bypassed endpoint, a direct `sqlite3` write, a
  bad migration, a future second write path. That is the difference between *escalation is
  forbidden* and *escalation is impossible*. It also makes the property testable **as a property**,
  over hostile inputs rather than over the inputs a test happens to send.

## 54. An unset policy means the whole ceiling; a set-but-empty policy means nothing

- **Decision**: the absence of a subject row and a subject row with an empty set are different
  statements, stored differently. (v0.7.0)
- **Reason**: this is what makes empty-policy parity **structural** rather than a special case
  sprinkled through the resolver. An admin who deliberately grants a principal nothing must get
  nothing, not everything.

## 55. A malformed **capability** policy falls back to the ceiling; a malformed **scope** policy denies

- **Decision**: fail-open for capabilities, fail-closed for scope, with a warning and an audit row
  in both directions. (v0.7.0)
- **Reason**: failing closed on capabilities is a denial of service with no recovery path — the
  admin cannot reach the write capability to repair the policy that locked them out. Failing open on
  scope is a disclosure bug. Safe in both directions **only because admin is never scoped** (#58).
  The capability fallback is the shipped compiled baseline: the strictest state that is also
  recoverable.

## 56. The three roles stay compiled in; only their *restriction* is data-driven

- **Decision**: no custom roles. (v0.7.0)
- **Reason**: the ceiling model draws its whole strength from the permission map being *code*,
  reviewed as code. A runtime-defined role has no compiled ceiling, so the first operand of the
  intersection would become attacker-influenced data and the guarantee would collapse into a
  validation check. The role ranking's total order is also assumed by field shaping and the UI
  affordance gate.

## 57. Scope selectors resolve to element ids at read time, and the scope is a union of two layers

- **Decision**: store selectors, resolve them on every request; the two layers union. (v0.7.0)
- **Reason**: the appliance *discovers* elements continuously — that is the product — so resolving
  at write time would make an element that first reports after the policy was written invisible to a
  scope whose CIDR plainly covers it, and the failure would look like a correlation bug. They union
  because each is independently a restriction, and a per-principal scope is meant to *add* a named
  exception, which an intersection makes inexpressible.

## 58. Admin is never scoped

- **Decision**: the exemption is in the resolver, before any policy is read. (v0.7.0)
- **Reason**: this one rule is what makes every fail-closed branch in the release safe. If a
  malformed scope policy could hide elements from an admin, they could not see them, could not
  diagnose why, and would be one bad row from an appliance whose only fix is `sqlite3` on the host.
  Scoping an admin's *view* while leaving their *authority* intact would protect nothing anyway.

## 59. An out-of-scope situation member is redacted to a count and a type, never omitted silently

- **Decision**: list a situation if **at least one** member is in scope; show the rest as a count
  and an alarm class. (v0.7.0)
- **Reason**: omitting them turns a presentation control into a lie — an operator reading *"3
  alarms"* would triage an incident that spans 40 across a boundary they cannot see, and be
  confidently wrong. Hiding the whole situation fails the other way: a cross-boundary fibre cut is
  precisely what a scoped operator most needs to know about. Cardinality and type is strictly less
  than the situation id they already see.

## 60. Out-of-scope resources return **404**, produced by the same lookup that would find them

- **Decision**: the scope is a predicate *inside* the read, not a pre-check in front of it. (v0.7.0)
- **Reason**: 403 is an existence oracle. A separate pre-check is a **second decision site** that
  can drift from the query it guards, silently and one-directionally. As a predicate inside the
  read, *out of scope* and *absent* are indistinguishable by construction — including in timing,
  because it is the same query.

## 61. One shared candidate-selection helper, and preview keeps its bounds as arguments

- **Decision**: one selection function; preview's bounds become aliases of the correlator's.
  (v0.7.0)
- **Reason**: two copies with a test proving they agree *today* is the debt being closed. Sharing
  the engine's mutable window and storm accounting instead would drag engine state onto an HTTP path
  for a read-only question. The alias removed the second copy of the numbers, which was the actual
  drift risk.

## 62. Per-principal policy is keyed on the principal's identity, not its display name

- **Decision**: key on the primary key that already exists. (v0.7.0)
- **Reason**: keying on a name silently applies one operator's restriction to an unrelated service
  token that shares it — a cross-principal authorization bug appearing only once somebody picks a
  colliding name. The identity is what session revocation and the audit log already use, so a policy
  row, an audit row and a revocation all name the same thing.

## 63. An unset scope layer expresses no opinion; the visible set is the union of the layers that do

- **Decision**: generalising #54 — **unset** means *no opinion*, **set** (even to empty) means
  *exactly these*; visible is the union over the layers that express one. (v0.7.0)
- **Reason**: it satisfies every clause at once and adds the case a plain override gets wrong — role
  unset with the principal set gives the principal's set, so a per-principal restriction actually
  restricts. Intersecting would break the additive case that motivated the union in #57. It is also
  the stricter reading.

## 64. The admin's recovery capabilities are unremovable — structurally, not by validation

- **Decision**: a tiny recovery set re-added for the admin role inside the resolver. (v0.7.0)
- **Reason**: it preserves the central invariant *exactly*, because the set is a subset of the admin
  ceiling — a union with a subset cannot leave it. No stored policy, by any path, can produce an
  appliance an admin cannot repair. A validation check would guard only the paths it sits on. The
  set is the *recovery* surface only: governance can still restrict day-to-day authority.

# v0.7.1 — the write perimeter

## 65. A write is inside the perimeter or it is a defect, and it denies through the *existing* 404

- **Decision**: reuse the listing predicate rather than restating it. (v0.7.1, F34)
- **Reason**: one scope decision site. Reusing the listing predicate makes it impossible for the
  read and the write to disagree about what *"yours"* means; a second copy would drift
  one-directionally and silently. Denying through the existing 404 gives *out of scope* and *no such
  thing* the same status, body and timing by construction — #60 applied to writes.

## 66. Scope selectors resolve against element identity and address only, never operator-writable data

- **Decision**: remove operator-writable labels from the authorization decision entirely. (v0.7.1,
  F35)
- **Reason**: guarding the one write path that reaches editor-writable data leaves the escalation
  reachable by a future second path, and nothing would fail a test when it opened. Removing the
  input makes it **unexpressible**. The cost is real and stated: a label glob in an existing policy
  now matches by address or not at all.

## 67. The timeline filters on element identity, not on a rendered display string

- **Decision**: filter on the identity column. (v0.7.1, F35)
- **Reason**: **a display string must never be an authorization key.** Enforcing label uniqueness
  treats a symptom an operator can be talked out of, and the decision would still be keyed on a
  mutable string. One extra column in a query whose result the UI already renders identically is #66
  applied to the second path.

## 68. Feedback is idempotent per `(situation, verdict)`

- **Decision**: a *changed* verdict is a legitimate correction and applies once. (v0.7.1, F36)
- **Reason**: the effect is bounded by the **shape of the data** rather than by a policy someone can
  tune wrong — two possible verdicts, so total influence is bounded at two applications whatever
  anyone posts. A rate limiter only paces an attack. The cost is stated: an operator who genuinely
  wants to reinforce the same verdict twice cannot, and that is correct, because the second carries
  no new information.

## 69. The learning epoch belongs to a closed situation, not to feedback

- **Decision**: a parameter on the learning call, so the close path is untouched. (v0.7.1, F36)
- **Reason**: it restores what the learner's own docstring has said since v0.1.0. Global forgetting
  is a property of the correlation lifecycle, not of an operator's opinion about one grouping, and
  letting a write route drive it is the category error behind the finding.

## 70. A label write to a nonexistent target is a 404, and the affected tests are repaired, not weakened

- **Decision**: 404, plus an orphan cleanup; the three tests it broke are repaired by **giving them
  a real device**. (v0.7.1, F37)
- **Reason**: 404 is the only status keeping *does not exist* and *not yours* indistinguishable — a
  400 would be an existence oracle re-introduced by the fix for a different finding. The project
  rule is *"the change is the path only, never an assertion"*; this is an explicit exception,
  recorded because a test change inside a security patch must not pass unremarked.

## 71. No foreign key on `label` in a patch release

- **Decision**: close the write primitive, remove the orphans, leave the structural fix to a release
  that can carry a table rebuild. (v0.7.1, F37)
- **Reason**: a rebuild inside a security patch runs against every operator's populated database, is
  the one class of migration that can lose rows, and would make the diff unreviewable — which is the
  whole value of a security patch. Still open.

## 72. Truncation applies to the filtered set: the scope predicate moves into the SQL

- **Decision**: bind the element ids in the query, with the parameter count bounded. (v0.7.1, F38)
- **Reason**: filtering after truncation makes the defect rarer and therefore harder to find — the
  count still depends on out-of-scope volume, which is the worst property a disclosure control can
  have. It also gives parity **by construction**: the unrestricted branch runs byte-identical SQL.

## 73. One transaction discipline, implemented once

- **Decision**: one helper beside the perimeter closures, with the engine's internal commit removed
  so the API owns the boundary. (v0.7.1, F39)
- **Reason**: twenty copies of a commit-and-rollback block is twenty chances to forget, and the one
  forgotten is invisible until exploited. Middleware cannot work: by the time it sees the exception
  the lock is released and another caller may have committed. Removing the engine's commit makes
  feedback a single transaction in the order mutate → audit → commit.

## 74. The perimeter extraction is the next release's theme; this one records its shape and builds none

- **Decision**: name the extraction, leave every route handler textually unchanged. (v0.7.1)
- **Reason**: a security patch's value is a **reviewable diff**, and moving the files the fixes
  touch makes every fix hunk look like a move. One theme per version, and the risky thing *after*
  the safety net.

# v0.7.2 — the HTTP package

## 76. The perimeter boundary is "may this request proceed?", not "which file is it in?"

- **Decision**: classify by what a helper decides, not by where it sits. A scope check 400 lines
  away is perimeter; a parameter formatter beside the perimeter is not. (v0.7.2)
- **Reason**: classifying by location would split the write-side scope check from the read-side one,
  which is precisely the mistake F34 was. It is the only criterion under which *"authorization,
  scope and the transaction boundary keep exactly one implementation"* is checkable.

## 77. `Perimeter` is a class, because the alternative would edit the handlers

- **Decision**: a class holding the four pieces of state, with attribute rebinding as the only
  permitted edit. (v0.7.2)
- **Reason**: free functions change every call site, touching all forty handlers and forfeiting the
  parity proof that is this release's most valuable artefact. The cost is named honestly:
  `Perimeter` is now constructible outside the app factory.

## 78. A context object plus a mandatory local-rebinding block, so handler text never changes

- **Decision**: fifteen fields, confirmed empirically from the closure dependency graph, and the
  rebinding block is **mandatory** rather than stylistic. (v0.7.2)
- **Reason**: attribute access at each call site is tidier and the wrong trade — it edits all forty
  handlers, so the hash table proves nothing and *"behaviour did not move"* becomes an assertion
  instead of a measurement. With the block the diff of a 1 752-line split is *entirely* moves, and
  the linter catches a name bound but unused so it cannot rot.

## 79. The package is `api/`, one level deep — "prefer flat" is superseded for this subtree only

- **Decision**: an explicit supersession, for this subtree and no other. (v0.7.2)
- **Reason**: the subtree earned it — fourteen cohesive modules, none over 400 lines. Naming it
  `http` would shadow a stdlib package, and every existing import already says `api`. Superseding a
  standing preference quietly is how a project's rules stop meaning anything.

## 80. `ROUTE_SCOPE` is descriptive now and enforcing later

- **Decision**: one entry per non-public route with a justification on every `unscoped`, and
  `admin_only` asserted at import against the permission map so it is derived rather than a second
  authority. (v0.7.2)
- **Reason**: injecting the check changes control flow, and control flow is behaviour — in a release
  whose entire value is that behaviour did not change, that one decision would make every other gate
  unfalsifiable. Declare now, prove the declaration, enforce later. Still descriptive.

## 81. The module-size guard ships with a shrink-only debt allowlist, installed before the move

- **Decision**: add the guard **against the unmodified tree**, before anything moves. (v0.7.2)
- **Reason**: installing it first matters as much as its content — every later step is then measured
  by a rule that predates it, so the guard cannot have been shaped to fit the outcome. The *may not
  grow* clause stops an allowlisted module absorbing new code because it is already exempt.

## 84. The four source-reading tests get a new source, not new assertions

- **Decision**: point them at the concatenated package; two scanned tokens change spelling because
  that is the decorator object's literal new name. (v0.7.2)
- **Reason**: relaxing them to make a refactor look cleaner is the trade this release exists to
  refuse. A silently-vacuous guard is worse than a deleted one, so the helper **refuses a module it
  has not been told where to place** and asserts a floor on the concatenated length.

## 85. Sixteen modules, not fourteen: the 400-line guard outranks the planned module list

- **Decision**: two extra modules, leaving the perimeter at 361 lines. (v0.7.2)
- **Reason**: raising the threshold would gut the flagship artefact of the release in the release
  that installs it; a guard whose threshold moves to accommodate its author is not a guard. Headroom
  matters too — a file six lines under its limit is one where the next legitimate comment fails CI.

## 86. `audit_row` is reached through the context, to keep `mypy --strict` checking it

- **Decision**: bind it through the perimeter object rather than as a bare callable. (v0.7.2)
- **Reason**: a bare callable silently stops the type checker verifying the arguments at all
  twenty-five audit call sites — in the one helper where a wrong keyword is least acceptable, since
  it writes the attribution trail. A release claiming to change nothing must not quietly delete type
  coverage.

## 87. `rbac.py` joins the debt allowlist rather than losing the table or the prose

- **Decision**: 436 lines, owner named, seam recorded. (v0.7.2)
- **Reason**: splitting the single-source table to satisfy a line count is the worst available
  trade, and deleting the prose removes exactly what a contributor adding a route needs at the point
  of the table. The guard did its job — it noticed — and debt that is written down and dated is not
  the same failure as debt that is invisible.

# v0.7.3 — the store and engine split

## 88. Mixins over a thin annotated base, with sibling inheritance where a mixin calls a sibling

- **Decision**: mixins on an annotation-only base; a mixin calling a sibling **inherits the
  sibling**. Measured: type checker clean, and all nine moved bodies hash **identically**. (v0.7.3)
- **Reason**: free functions plus delegating one-liners would rewrite all 109 bodies and make the
  method-hash parity proof impossible to state. The amendment is forced by measurement — an
  annotation-only base produced exactly four errors and only six methods cross a mixin boundary, so
  it costs **two** edges. Declaring those signatures on the base needs stub bodies, and a stub that
  silently resolves instead of the real method is a defect whose failure mode is a no-op write.

## 89. `main.py` stays a module; the engine gets the same mechanism

- **Decision**: a base declaring the attributes the mixins touch. Measured: clean, with **no**
  method declaration needed at all. (v0.7.3)
- **Reason**: `python -m netcorenoc.main` is the documented way to run the correlator — the README,
  Makefile, Dockerfile and compose file all print it. A package would change the semantics of the
  one command every operator types, which is a behaviour change wearing a structural hat.

## 90. `maintenance()` does not leave the engine, against both documents' module tables

- **Decision**: it stays, at 28 lines. (v0.7.3)
- **Reason**: a method touching the batch lock or the ingest path does not leave, and that outranks
  the module table. Both triggers fire on this one and nothing else: it takes the *same* lock object
  the batch commit takes, and calls a must-stay method. A reviewer asking *"what closes a situation,
  and under which lock?"* must not have to follow an import. Keeping it also removes the only call
  pointing from a mixin back into the engine, which is what lets the base stay a pure declaration
  site.

## 91. `COHESION_EXEMPT` — "cohesive by design" is not "unfinished"

- **Decision**: a second list, module → the invariant that forbids splitting it, with five
  constraints each enforced by its own test — the reason must cite an invariant **by name**, a
  module may be in one list or the other, entries carry **no owner and no date**, an exempt module
  may not grow, and at most two entries may exist. (v0.7.3)
- **Reason**: the debt allowlist means *"too big, will be fixed by release N"*. `engine.py` will
  never be fixed, because there is nothing to fix. Filing it as debt would put a promise in CI
  nobody intends to keep, and the first time the date slipped the honest response would be to move
  it — which is how a ratchet becomes a comment. The absence of an owner is the semantic difference,
  and a test asserts it.

## 92. The layer rule gets a test, seven releases after it got a paragraph

- **Decision**: parse every module's imports and enforce the map, with the exemption list **empty**.
  (v0.7.3)
- **Reason**: a rule with no test is a rule that gets noticed two releases late — which is exactly
  what happened. The exemption list exists so a future upward import is a *visible, arguable diff*
  rather than a silent regression, and it is empty on arrival, which is the only state that makes
  the guard mean what it says.

# v0.7.4 — the resequencing and the documentation guard

## 93. v0.8.0 is the operator-feedback dataset; customer models move down the chain

- **Decision**: v0.8.0 is the dataset; customer models move behind the framework they plug into —
  v0.16.0 after #184 and #202. (v0.7.4)
- **Reason**: the feedback click is the only source of human labels, and every later step consumes
  it. Nothing downstream can be built — or honestly evaluated — before the labels exist and their
  bias is measured. An unnamed *"later"* is not a decision, and the status quo was the defect.

## 94. The documentation-consistency guard: a claim form, and a line between live docs and records

- **Decision**: one release table as the single source of truth; a machine-readable marker for what
  a release *is*; the existing tag convention for what an *element* is planned for; and a document
  making a claim may only tag elements for its own release. (v0.7.4)
- **Reason**: a prose-scanning guard fires on prose and misses claims, so it gets deleted. A marked
  form is checkable without reading English, and putting the truth in one table makes a disagreement
  arithmetic rather than interpretation. A narrow enumerated check is kept as the **belt** and
  labelled as such, because the failure that actually happened was in untagged prose.
- **The exclusion of records is the guard's biggest risk**, so the excluded set is itself asserted
  by a test rather than merely defined.
- **The first version was wrong, and that is recorded rather than fixed silently**: scanning raw
  lines caught 11 occurrences in 5 files and **missed 7 of the 11 enumerated phrasings**. A guard
  that catches less than the enumeration it was built from is not yet the guard.

## 95. `shaping.py` is three parts, not two — the binding document is corrected in place

- **Decision**: fields, scope, and projections, with the architecture document superseded **in
  place** by a dated note. (v0.7.4)
- **Reason**: the projections are not a third *axis*; they are the **consumer** of the other two.
  Either two-way split would put response-body construction inside the module that owns the scope
  decision, or scope handling inside the module that owns field rules. A binding document that turns
  out to be wrong is worth more corrected than obeyed.

## 96. `rbac/` re-exports by identity, not by equality

- **Decision**: assert identity for eight tables, plus a second test that no other module under the
  package binds those names. (v0.7.4)
- **Reason**: equality is not the property that matters; *identity* is. Only one object can be the
  authority, and only an identity assertion says so. Both tests were **shown to fail against a
  deliberately-copying `__init__.py`** before being accepted — a guard installed green has not been
  shown to work.

## 97. `varbind_accum.py` is engine layer, and the shared constants move with the classes

- **Decision**: engine, not cross-cutting; four constants move, and the old module imports them
  back. (v0.7.4)
- **Reason**: cross-cutting means *no layer's private concern; every layer's concern*, and an
  accumulator for varbind statistics is a domain concept with one consumer — classifying it
  cross-cutting would have failed the *cross-cutting imports only cross-cutting* check, which is
  what the guard is for. The constants move because leaving them would create a circular import:
  **the import graph decides rather than taste.**

# v0.7.5 — the feedback acquisition path

## 98. The declaration gate refuses unknown route shapes; it does not learn to walk them

- **Decision**: an exact-type allowlist of the two known shapes; anything else raises, naming its
  module and class. (v0.7.5)
- **Reason**: teaching the gate to walk new shapes rebuilds the defect one level down — every
  attribute it needs is an undocumented framework internal, so the guard's correctness would again
  rest on a dependency's private representation. A fix written to the reported mechanism would have
  left four shapes open. Refusing needs to know nothing about what the new object *is*; exact-type
  rather than `isinstance`, which would admit any future subclass unexamined.

## 99. The UI changes are verified by source inspection plus a written manual protocol

- **Decision**: no JavaScript runtime; a written, executed manual verification instead. (v0.7.5)
- **Reason**: adding one would be the largest dependency decision since v0.2.0, taken inside a patch
  release, to test three lines — and the build container happened to carry `node` while CI and a
  maintainer's machine did not, so a test written against it would be green only on the machine it
  ran on, which is worse than no test. **Honesty about what a test proves outranks a green suite.**
  Superseded in v0.12.0 by #167.

## 100. The documentation guard's inline-code strip is dropped, not narrowed

- **Decision**: remove the regex. (v0.7.5)
- **Reason**: the docstring claimed the strip was what made the convention work. The convention is
  the **opposite**: the backticked form is *the* way to tag an element, so the strip did not filter
  historical mentions, it filtered live ones. **Measured: 15 of 48 tags visible, 31 %**, with five
  of the eight tag-carrying documents entirely invisible — including the release's own
  specification. The guard written to stop the repository contradicting itself contained that
  contradiction.

## 101. The framework is not pinned to an upper bound; the representation change is detected instead

- **Decision**: detect, do not pin. (v0.7.5)
- **Reason**: a pin freezes a representation; the test **notices** when it changes, and that is the
  guarantee that was missing — a pinned project upgrading a year later meets the identical silent
  widening the moment it lifts the pin. Detection also fails on the day of the upgrade, naming the
  new class, which is when the information is worth most. **The honest limit**: it detects a change
  in the *shapes* produced, not in what an existing shape *means*. Still open.

# v0.8.0 — the operator-feedback dataset

## 103. The imitation-trap invariant is expressed structurally, not only in prose

- **Decision**: capture the machine's decision, and make reaching for it as a training target a
  deliberate act rather than an autocomplete. (v0.8.0)
- **Reason**: dropping it destroys real information the promotion comparison needs, and would be
  capture-side censoring in the release built to end censoring. Prose alone had already failed once.
  **The friction is the mechanism.**

## 104. The server-side membership record is a child table, written from the server's own state

- **Decision**: a child table, written from the server's state at verdict time, always.
  Client-reported membership is a **separate, optional, untrusted** half. (v0.8.0)
- **Reason**: a digest proves that something changed and cannot say what it was — and after a merge
  there is nothing left to compare it *against*. A list in a column makes the only useful query a
  string scan. The divergence between the two halves is a metric, not an error.

## 105. The alarm-observation row is written per activation, not per trap

- **Decision**: per activation. (v0.8.0)
- **Reason**: measured — 3 159 traps produced 2 256 activations, a ratio of **0.7142**, agreeing
  with the harness's independently computed 0.7156. The 903 extra rows would be re-fires that **by
  construction never reached the scorer**, so no pair row could reference them: a 40 % increase in
  write volume on the ingest path, inside the batch lock, buying rows nothing can join to.

## 106. An empty method set is refused, not defaulted and not skipped

- **Decision**: refuse. (v0.8.0)
- **Reason**: defaulting to all verbs is superficially more precise and worse — it invents a
  declaration requirement for seven verbs nobody wrote, so the natural fix is to declare all seven,
  turning a mistake into seven authorizations. It also encodes an assumption about the framework's
  dispatch, the unpinned-internal dependency #101 recorded as the mechanism by which this gate
  regressed once. Refusing assumes nothing.

## 107. One pair table with a lifecycle column, not two tables — chosen against the measurement

- **Measured**: two tables won every operation — 21.52 vs 23.92 MB, promotion 12.57 vs 18.42 ms,
  query 0.06 vs 0.44 ms, deletion 297 vs 326 ms.
- **Decision**: **one table**, against the measurement. (v0.8.0)
- **Reason**: a pair row references two observation rows. With two tables, promotion must either
  preserve ids across four independently-autoincrementing tables or **rewrite every reference as it
  moves** — a correctness hazard on the per-label path whose failure mode is a dataset row silently
  pointing at the wrong observation. With one table **nothing moves**. A correctness hazard on the
  path that produces the release's entire output is not worth 29 ms.

## 108. The engine's cohesion ceiling rises 542 → 580, and a new guard pays for it

- **Decision**: raise it, and add two tests — that **no SQL statement and no dataset table** appears
  in the engine, and that the capture module is the one that grew. (v0.8.0)
- **Reason**: shrinking the call site to satisfy a number is the inverse of what the guard is for.
  Before: *the engine may not exceed 542 lines.* After: *it may not exceed 580 **and may contain no
  persistence logic at all***. The second is strictly stronger — the first was satisfiable by a file
  full of SQL — and was verified non-vacuous by injecting an `INSERT` and watching the guard go red.
  A ceiling that rises with a new structural guard attached is a ratchet moving forward.

# v0.8.1 — the dataset's lifecycle

## 109. The F44 fix retains the labelled `situation` row, because the foreign key is restricting

- **Decision**: retain the row itself, not its members and not its links. (v0.8.1)
- **Reason**: a migration is forbidden in a patch and would weaken the constraint that made the
  defect *detectable*. Retaining everything keeps a labelled storm's 501 member rows forever,
  turning a data-loss fix into a disk-growth bug. **One row per label** is bounded by the rarest
  event in the system and keeps the label interpretable.

## 110. The training tier selects; it does not delete

- **Decision**: the training window is a `WHERE` clause, not a `DELETE`. (v0.8.1)
- **Reason**: a training-retention *deletion* destroys evidence to express a **modelling
  preference**. Wanting to train on the last twelve months is a statement about *selection*, and
  nothing has to die for a model to ignore a row. It also keeps the choice **revisable** for a
  corpus four later releases will disagree about how to use.
- **Supersedes, in place and dated**, the earlier claim that the maintenance loop bounds *"the sink
  and nothing else"*.

## 111. Retention is persisted as one JSON value, not four keys

- **Decision**: one key holding the whole policy. (v0.8.1)
- **Reason**: the four values are **one policy with an invariant between them**. Per-key storage
  forces a choice, when one is unreadable, between mixing stored and default values — which can
  synthesise a policy **no operator ever set**, possibly one that deletes more than either — or
  discarding three valid values for one bad one. One key makes the unit of parsing the unit of
  policy.

## 112. The coverage denominator is the population the report can see evidence of

- **Decision**: the union of the situations the report can see evidence of. (v0.8.1)
- **Reason**: clamping hides the inconsistency, and a clamped 100 % is indistinguishable from a real
  one. Shrinking the numerator discards evidence to make an arithmetic property hold. With the union
  the numerator is a subset of the denominator **by construction**, so no clamp is needed.

## 113. `RetentionPolicy` moves to its own module, because the size guard required it

- **Decision**: a new module; every name re-exported, so **no import site anywhere else changed**.
  (v0.8.1)
- **Reason**: cutting the reasoning to fit is what #108 rejected, and the reasoning being cut is
  what a reviewer of a data-destroying policy most needs. The allowlist must stay empty and this is
  not debt. Raising a second ceiling in two releases is how a ratchet becomes a comment. The seam is
  real: a retention policy participates in no capture decision; it is passed *to* one.

# v0.9.0 — shadow mode

## 114. The evidentiary standard has a floor a deployment may raise and can never lower

- **Decision**: floors resolve as `max(project floor, deployment policy)`, absent by default.
  (v0.9.0)
- **Reason**: **the asymmetry is the whole argument** — *softening admits a bad model; hardening
  rejects a good one.* A rejected good model costs a release; an admitted bad one costs the
  operator's trust in every grouping the product makes afterwards, and no later fix recovers it. So
  the monotone direction is toward evidence, always. This is `ceiling ∩ granted` applied to evidence
  instead of authority.

## 115. The champion-agreement report is a sibling subcommand, not a section of the bias report

- **Decision**: its own subcommand and its own frozen expectation. (v0.9.0)
- **Reason**: the bias report is compared **byte-for-byte** against a frozen fixture, so adding a
  section would re-cut *both* fixtures on every future change to *either* and couple two independent
  gates into one. They answer different questions — the whole dataset versus the *labelled* subset —
  and a gate should go red for one reason. A route is refused because it would add HTTP surface to a
  scope bypass and could never be a byte-for-byte gate.

## 116. The challenger satisfies `LinkScorer` structurally, and lives in its own module

- **Decision**: a new module implementing the protocol; the default scorer gains nothing. (v0.9.0)
- **Reason**: subclassing inherits five parameters the challenger does not have and an arithmetic it
  must not reproduce; a registry is the plugin surface specified for a later release and would
  destroy what v0.6.0 paid for — that a second implementation needs **no edit to the package**.
  Implementing the protocol gets explainability, the fail-safe wrapper and a future pointer-move
  promotion for free rather than by promise.

## 118. Training runs in the maintenance loop, because no unlocked slow-loop point existed

- **Decision**: reuse the existing periodic loop; take the lock only to read the rows and to write
  the result, never for the fit. (v0.9.0)
- **Reason**: fitting under the store lock is forbidden — it is the *same* lock object the batch
  commit takes, so a two-second fit is a two-second ingestion stall. A separate task is a third
  supervisor entry and a second cadence for work that is already periodic. The maintenance loop is
  the *only* place in the periodic path where the lock is provably not held — a property a reviewer
  checks by reading twelve lines rather than by tracing a scheduler.

## 119. Both shadow mechanisms ship, because their disagreement is the measurement

- **Decision**: offline reconstruction **and** online shadow, and they are not alternatives.
  (v0.9.0)
- **Reason**: offline measures quality at no ingest cost and **cannot measure skew by construction**
  — recomputing from the stored features is tautologically consistent with them. Online measures
  real behaviour and says nothing about quality. **The divergence between them is the skew test**,
  and a non-zero rate means the quality figures in the same report describe features that were never
  served.

## 120. The per-operator cut is anonymised, and the identity never leaves the module

- **Decision**: aliases assigned inside the measuring module, so the reference never reaches the
  renderer. (v0.9.0)
- **Reason**: naming operators turns a bias measurement into a **per-employee performance report**,
  generated by a tool nobody was told would generate it, from data collected for another purpose.
  Dropping the cut discards the measurement the workstream exists to make — the *spread* across
  operators — which survives anonymisation intact.

## 121. `maintenance_loop` moves out of the engine; the base gains one declaration

- **Decision**: move the loop, keep `maintenance()` itself. (v0.9.0)
- **Reason**: raising the ceiling a second time in three releases is how a ratchet becomes a
  comment. This does not re-litigate #90 — that measurement was about `maintenance()`, and **it does
  stay**. The loop was kept beside it on the weaker ground of being *"six lines whose whole body
  calls maintenance"*, and that is no longer true: its body now sequences two activities with
  **different lock disciplines**.

## 122. The partition metrics move into the package; the harness re-exports them

- **Decision**: move them. Verified byte-identical `make eval` output before and after. (v0.9.0)
- **Reason**: a copy in each place is a **second implementation of one decision**, and the failure
  mode is specific — the two would agree until one was tuned, and the release's headline metric
  would then differ between the harness and the report with nothing to notice.

# v0.9.1 — the partial split

## 123. The operator marks *which* members do not belong: the exclusion set

- **Decision**: an exclusion set, not pairwise marking and not a full partition. (v0.9.1)
- **Reason**: measured against what one gesture yields. Pairwise yields **one** negative pair and is
  dominated at every bag size ≥ 3. A full partition asks a question the operator usually **cannot
  answer**, and an affordance demanding the unknown gets guessed at — **a guess recorded as an
  assertion is worse than no assertion**. On a nine-member bag with two marked, one click yields
  **fourteen** asserted negatives where today it yields none.

## 124. A partial split asserts `marked × rest` and **nothing else**

- **Decision**: `marked × rest` negatives, with the remainder recordable as a separate nullable
  assertion in which `NULL` means *not asserted*. (v0.9.1)
- **Reason**: inferring more is **fabrication**. An operator pulling two alarms out of a bag has not
  said the other seven belong together, nor that the two belong to each other, and either inference
  would be invisible in the data. A fourth verdict value is refused because **a partial split *is* a
  split**.
- **The arithmetic that makes "and nothing else" checkable**: for n = 9, m = 2 — 14 asserted, 22
  unasserted, 36 total, exactly `n(n−1)/2`. A test asserts it.

## 125. `penalize` acts on the assertion when there is one

- **Decision**: penalise the asserted pairs, not the whole bag. (v0.9.1)
- **Reason**: leaving it alone is the smaller diff and still wrong — the product would **collect a
  better signal and knowingly act on the worse one**, un-learning thirty-six pairs when the operator
  contradicted fourteen.

## 126. A verdict recorded through `close` is a **second acquisition channel**

- **Decision**: record the channel, and it is not optional; report per channel, never averaged.
  (v0.9.1)
- **Reason**: closing a situation **selects for resolved incidents** — a different population from
  spontaneous browsing. The failure mode is **retroactive**: two populations in one undifferentiated
  column destroy the bias characterisation for rows *already written*. A release that raises the
  labelling rate without recording which population it raised has **damaged the corpus while
  appearing to improve it**.

## 127. The gesture ships; the remainder assertion does not

- **Decision**: ship the exclusion checkboxes; leave the remainder assertion to the API and the
  schema. (v0.9.1)
- **Reason**: the card already renders the member table, so the exclusion is a selection on rows
  already on screen — no panel, no modal, no new file. The remainder assertion is not a property of
  a row, so it needs a control of its own, and that is a **second gesture**, which must not be
  manufactured to save a click.

## 128. The label row's children move to `store/feedback.py`

- **Decision**: move the four label-child methods to the module that already holds the label row.
  (v0.9.1)
- **Reason**: **the seam is one this codebase has already cut, one layer up** — capture runs per
  activation under the batch lock, the label runs per operator verdict on the HTTP write path. The
  store layer had the same two paths in one file.

## 129. `LabelContext`, and the bag resolution leaves the engine

- **Decision**: a frozen context collapsing four arguments, and the bag resolution moves with it.
  Net: the engine 580 → **569**. (v0.9.1)
- **Reason**: the context gives the two rules about a label's *contents* a place to live — dropping
  an exclusion that arrived on a `confirm`, and returning nothing when nothing was asserted. Both
  are decisions about what a label records, which is the label module's subject. The engine's
  exemption covers the *ingest path*, not code that happens to sit near it.

## 130. The close gesture is deferred; the contract and the channel ship

- **Decision**: ship the endpoint and the channel column; do not add the buttons. (v0.9.1)
- **Reason**: two more buttons would put **five** near-identical click targets in one row, two
  differing only in a trailing word, on the one path where a mis-click is not an annoyance but a
  **silently wrong label**. Repurposing an existing control is worse: an operator who has clicked
  Confirm a thousand times would start closing situations by doing it.

# v0.9.2 — the evidence boundary

## 131. Three tiers, and the sentence that decides which one a consumer may read

- **Decision**: *a quantity that describes the client may be derived from the client; a quantity
  that describes the evidence must be derived from the server's reconciliation; the reported set
  never reaches an arithmetic operator whose result is reported as a property of the network.*
  (v0.9.2)
- **Reason**: clamping is the fix that looks like the fix and is not — it turns `−60` into a number
  in range and leaves `+900`: thirty ghost marks on a sixty-member bag produce a plausible total
  from zero real assertions, and a clamp accepts every digit. Rejecting at the boundary would
  reintroduce the existence oracle F34 closed. The sentence also explains why `client_diverged` is
  *correct*: its subject is the client.

## 132. Both quantities are computed at write time and stored

- **Decision**: store both; reuse the scope object the perimeter already resolved. (v0.9.2)
- **Reason**: deriving one at read time gives two quantities belonging to **the same assertion**
  different availability profiles, and every consumer for the rest of the project's life would have
  to know which is which. A second scope resolution is a second answer that can drift.

## 133. What may be backfilled, and what is `NULL` forever

- **Decision**: **derivation may be backfilled; fabrication may not.** Recomputing from evidence
  already stored is derivation; inventing a quantity whose input is gone is fabrication, and must be
  `NULL` — counted and reported as unknown, **never assumed zero**. (v0.9.2)

## 134. The drift check reports; it does not correct

- **Decision**: report the stored value, the recomputed value and the difference. (v0.9.2)
- **Reason**: a disagreement means a **write path is broken**, and correcting it destroys the
  evidence of that. The counterfactual settles it: had this existed a release earlier as a
  corrector, F46 would have been *invisible* — every hostile row quietly repaired, the reports
  looking right, the write path staying broken indefinitely.

## 135. A truncated assertion is its own population, not a denominator

- **Decision**: report it separately. (v0.9.2)
- **Reason**: discarding it destroys evidence; folding it in lets a known-incomplete count
  contribute to a quantity that may become a threshold — the whole failure this release repairs, in
  a smaller font. Separate reporting lets the next release decide, in advance, whether a floor is
  expressed over all rows or over untruncated ones.

## 136. The identity test becomes a property test, because the old one could not fail

- **Decision**: a **deterministic** property test over an enumerated range reaching the degenerate
  cases, asserting the bounds, **every component ≥ 0**, and the sum. No RNG, so a failure names one
  case. (v0.9.2)
- **The half that matters**: component non-negativity is what discriminates the impossible case;
  **the sum alone cannot fail**, and the docstring says so in those words. But non-negativity is
  **necessary and not sufficient** — at `n = 60, m = 30` every component is non-negative and the
  identity closes while the label asserts nothing at all. Only the intersection discriminates that.

## 137. `redacted_member_count` becomes `hidden_member_ids` — one read, one answer

- **Decision**: return the set once and derive both facts from it. (v0.9.2)
- **Reason**: two reads are two answers that can drift. A label whose two scope facts disagreed
  would be worse than one recording neither, because a reader would have no way to tell which half
  was wrong.

## 138. The drift check reports through the warning channel, and writes no audit row

- **Decision**: the operator-warning channel plus the bias report. **No new action in the frozen
  catalog.** (v0.9.2)
- **Reason**: the catalog records **events** — something a principal did, or a behaviour that
  changed. A drift *detection* changes no behaviour and no principal did it. Durability, the only
  thing an audit row would add, is better provided by the report, which re-derives the count on
  every run.

## 139. Two modules split at the 400-line guard, rather than the guard raised

- **Decision**: split on the reader/renderer seam and on the **subject** seam — what capture *cost*
  versus what an operator *said*. (v0.9.2)

# v0.10.0 — the honest judge

## 140. F48 is issued without a corrective release, and the argument is written down

- **Decision**: issue the finding; cut no patch release. (v0.10.0)
- **Reason**: **F48 requires no production change** — the shipped code carries the predicate
  everywhere and every number is correct; what is missing is a *demonstration*, not a *fix*. A
  release exists so an operator can *obtain* something, and there is nothing here to obtain. Cutting
  one would ship an unchanged appliance under a new number and devalue the four insertions that
  carried a repair.

## 142. The plan's detection threshold is not reproduced, and neither side is adjusted

- **Decision**: record the disagreement, adjust neither. (v0.10.0)
- **Reason**: fitting the closed form to the table is the same error as fitting a model to a test
  set, one level down, and would destroy the form's only value — that it was derived independently.
  The plan is hash-guarded so it could not be edited in any case, and **that constraint is the
  mechanism working rather than an obstacle**.

## 145. The seal is designed before the estimator, and its unreadability is structural

- **Decision**: build the seal first, the estimator second — the phase ordering is part of the
  decision. (v0.10.0)
- **Reason**: retrofitting isolation onto an estimator that already holds a store handle is how a
  structural guarantee decays into a convention. Building the seal first means the estimator is
  written against an interface that never offered the sealed ids, so reading them is not a rule it
  must obey but a thing it **cannot express**.

## 147. The seal refuses a second construction at the schema, not at the caller

- **Decision**: a constant column with `UNIQUE` and a `CHECK`, and a constructor that **inserts
  without checking first**. (v0.10.0)
- **Reason**: check-then-act is a race, and more importantly a rule living in whichever caller
  remembered it. The refusal must hold against a method written next year and against somebody at a
  `sqlite3` prompt, because the failure mode is not an accident: **a seal that can be rebuilt is a
  seal that can be rebuilt *after* seeing a result.** The test re-cuts against a **larger** corpus,
  so a silent re-derivation shows as a changed membership rather than as a no-op.

## 148. The seal's access log is not the audit log

- **Decision**: a separate table with its own append-only triggers, logging **refusals too**.
  (v0.10.0)
- **Reason**: the audit log records **operator** actions reachable from HTTP; nothing about the seal
  is, and no principal performs it. The two answer different questions: *who touched this appliance*
  versus *how many times has this analysis looked at its own answer*. A log of successful reads
  answers *"how often was the holdout spent"* and not *"how often did somebody try"* — and the
  second is what a reviewer of a four-release tuning loop needs.

## 149. The isolation guard forbids reaching the membership, not importing the module

- **The first version failed, and the failure was correct.** Every holdout number printed must carry
  its query count, so the report must reach the seal's *state*; forbidding the import would have
  moved the query count out of the report — a weaker outcome wearing a stronger rule.
- **Decision**: the estimator may not import the module at all, and **exactly one function in the
  package may call the one expression that returns the membership**, asserted by AST. (v0.10.0)
- **Reason**: the summary object *cannot* carry the membership. The report therefore holds the
  module, prints the count, and still has no way to obtain the ids — **stronger than an import
  ban**, because it constrains what can be *obtained* rather than what can be *named*. A third
  guard's first draft counted the module's own **docstring** as a read; a scan that cannot tell
  prose from SQL reports the module that is correct.

## 150. `shadow_eval.py` is split on the admission seam, not at the line counter

- **Decision**: a new module carrying admission and its verdict. (v0.10.0)
- **Reason**: it is the seam that already existed. One module answers *how good is this model*;
  admission answers *is it allowed to be measured at all* — asked **first**, with a different
  consequence: a model that fails admission does not get a bad score, it gets **no score**.

# v0.10.1 — the corrections

## 154. The detection threshold was pessimistic, not the plan optimistic — superseding #142

- **Supersedes #142**, which stays exactly as written: a correction to an append-only ledger is an
  addition, and a project whose entries can be edited has entries nobody can cite. (v0.10.1)
- **What the measurement shows**: the closed form gave **both** arms the base rate's variance, where
  the second arm has far smaller variance exactly in the small-`n` regime — so it was
  **pessimistic**, the opposite of #142's conclusion. At `n = 37` the correct value is 0.238, not
  0.298. #142's Monte-Carlo agreed with the error because it shared the assumption: **two methods
  that share an assumption are not two methods.**
- **Decision**: repair the implementation, leave the plan, and **replace an argument with a test** —
  a search that simulates two binomials and shares no arithmetic with the closed form. **The next
  disagreement is detected, not argued.** A *lower* threshold makes the comparison easier, so no
  verdict changes.

## 155. `agreement.py` is split rather than exempted, and the seam a finding created

- **Decision**: split into what is measured over a set of bags, and what a bag *is*. (v0.10.1)
- **Reason**: raising a guard to fit a corrective release is something this project has never done,
  and a release about repairing guards is the worst place to start. **The finding is what made the
  seam load-bearing**: before it, incident identity was a `COALESCE` in a select list and there was
  nothing to own. Three names stay importable from the old module, because a split is not a reason
  to move a caller's import.

## 158. The cycle walk is bounded, and the mutation ledger found it by hanging

- **Decision**: bound the walk, with a direct test and a control at the bound. (v0.10.1)
- **Reason**: trusting the invariant is the reading this project's own guidance warns against — *ask
  what value of its inputs would make it false*. Here there **is** one, a single token away, in the
  module whose entire subject is merge chains the schema does not forbid. Raising instead
  contradicts the module's stated posture that a corrupt chain is a fact to report, not an error
  that stops an offline report.

## 159. The coverage figure has a band, and it is reported rather than removed

- **The cause is exact**: the receiver's fuzz properties feed random bytes to the BER decoder with
  no derandomisation, so which malformed-datagram branches get exercised varies. **Nothing else in
  the suite drifts at all.**
- **Decision**: report the band — `96.10 %–96.21 %` over two runs, ±0.11 points, with the receiver
  named as the whole of it. (v0.10.1)
- **Reason**: derandomising trades **a real fuzzer for a stable number** — the value of random bytes
  against a BER decoder is that it tries inputs nobody thought of. Excluding the module hides the
  variance instead of measuring it, and the receiver is the ingest path.

# v0.11.0 — champion/challenger

## 160. Three tables, three responsibilities: the artefact, the event, and the retune

- **The three refutations of reusing the existing table, independent of each other**: its columns
  are the additive scorer's, all `NOT NULL`; a fitted challenger is **rejected by the validator** in
  four places, and the threshold cannot be negotiated because `0.0` means *"link everything"* to one
  kind and *"the neutral point"* to the other — **the same number means opposite things**; and a
  provenance column would be `NULL` on every manual retune, meaning both *"a human retuned this"*
  and *"the provenance is missing"*.
- **Decision**: three tables. (v0.11.0)
- **Reason**: the artefact and the event have different cardinalities and lifetimes — one model
  version may be **proposed many times and refused many times**, and a refused promotion must leave
  a row.

## 161. The parameters are a canonical JSON document with a per-kind validator, not typed columns

- **Decision**: one document column, one hash, one validator per kind. (v0.11.0)
- **Reason for rejecting typed columns**: **one migration per scorer kind, forever** — a schema
  requiring a migration to learn a new kind has put the release cadence inside the database.
- **Reason for rejecting "the fail-safe wrapper will catch it", the part a build gets wrong**: the
  wrapper catches an **exception at score time**, not a **parameter set that scores without raising
  and destroys grouping** — an all-zero weight vector raises nothing, returns a finite score and a
  full term breakdown, and gives every pair the identical logit while every guard stays green. **A
  payload validator without degeneracy rules is a type check wearing a safety check's name.** The
  document is **not** a plugin surface: no adapter column, no registry, no entry point.

## 162. The audit catalog reopens for exactly the actions this release makes possible

- **Decision**: promotion applied, promotion refused, and — because this release makes the seal
  **spendable for the first time** — seal construction and seal spend. (v0.11.0)
- **Reason**: a promotion is a governed operator act with a before and an after; recording it
  elsewhere would make the swap the one admin action not hash-chained. Opening it wider is refused
  on the project's own rule — **ambiguity about scope resolves to the smaller theme** — and a
  release that closed unrelated gaps because it had the file open would be sizing its work by
  convenience.

## 163. No UI in v0.11.0, and the reason is a measured defect rather than a preference

- **Decision**: not a button, not a field, not a string. (v0.11.0)
- **Reason, and the halves compound**: the audit catalog is reopening in the same release, so
  changing the frozen record *and* the surface a human acts through means a defect in either is
  diagnosed against a moved reference. And **the v0.7.5 defect is the most expensive in this
  project's history — a click gesture in a UI no test executes.** A mis-click that swaps the
  correlator is that failure with a larger consequence: the earlier one lost annotations, this one
  changes every subsequent grouping.

## 164. The magnitude bound is 25.0, and here is the arithmetic that chose it

- **The arithmetic, computed rather than recalled**: every feature lives in a range of width at most
  1, so a coefficient of magnitude `c` moves the logit by at most `c`. At `|z| ≈ 9.2` the
  probability is within `1e-4` of its limit and the slope has fallen three orders of magnitude — one
  feature moving across its full range has taken the decision from *certain* to *certain the other
  way*. **That is where a term stops meaning *this much evidence* and starts meaning *this is a
  switch*.**
- **Decision**: 25.0. (v0.11.0)
- **Reason**: a bound at the floating-point limit refuses nothing a fit would plausibly produce. A
  tight bound is worse: a genuinely strong learned effect *should* saturate, and refusing it would
  encode a prior about the network this project has not measured. What 25.0 refuses is the
  **runaway** regime — unpenalised logistic regression on separable data has no finite solution, so
  a diverged fit does not arrive at 30, it arrives at 400.

## 165. `evaluation_folds.py` is split out, by size, onto a real seam

- **Decision**: split. (v0.11.0)
- **Reason**: trimming docstrings was tried first and abandoned honestly — the module reached 415,
  still over, and going further would have meant deleting *claims*. **Deleting the reasoning to
  satisfy the size guard is the size guard doing harm.** The two halves answer to different
  specifications and were built in different phases against different gates. **Size first, and the
  seam it landed on happened to be real.** No abstraction is introduced: the same two functions, one
  import, no registry.

# v0.12.0 — the instrument

## 166. The harness's Node requirement is a rule, not a pinned version

- **Decision**: a floor, with the reference target being *the Active LTS line at the time of the
  release*. (v0.12.0)
- **Reason**: three runtimes must all work — CI, the flake's dev shell, and a maintainer's machine —
  and #99 recorded the failure mode of writing a test against whatever the build container happened
  to have. A harness pinned to an exact version is one the build environment cannot run, and its
  failure would be a **skip**, the most dangerous outcome available to this release.

## 167. A JavaScript runtime enters the test tree, and it brings no npm — superseding #99

- **Decision**: a hand-written DOM under `node:vm`. #99 is superseded **only** on whether a
  JavaScript runtime may exist in the *test* tree. (v0.12.0)
- **Reason**: source inspection is what #99 itself scheduled for replacement, and the cost was
  measured — an `app.js` no JavaScript engine can parse left all 1302 tests green. **Every
  off-the-shelf harness requires `npm install`**, and that is the whole argument: a harness needing
  a package manager puts a `package.json` and a `node_modules/` in a tree whose constitution forbids
  them, and makes the harness depend on a network fetch. A harness that cannot install is a harness
  that **skips**.

## 168. The characterisation boundary: invariants only, and the text guard is kept beside the behavioural one

- **Decision**: capture five properties a replacement must honour, and no layout assertion; keep the
  older text guard beside the new behavioural one. (v0.12.0)
- **Reason**: a test asserting a CSS class describes what is about to be deleted, and would have to
  be *edited* during the rewrite — when a guard is least trustworthy. The behavioural guard is
  **stronger** (it cannot pass by matching nothing) but **skippable**, because it needs Node;
  deleting the text guard would mean that on a machine without Node the capability map has no guard
  at all. They fail for different reasons, which is the reason to keep both.

## 170. Archetypes are deferred and the draft is retagged, not deleted

- **Decision**: archetypes move down the chain. (v0.12.0)
- **Reason**: per-archetype weights mean splitting an already-insufficient corpus `k` ways. **A
  corpus that cannot decide one comparison cannot decide `k` of them, and dividing it makes every
  arm worse.** That is the measurement four consecutive releases returned, not a scheduling
  preference. Deleting the draft is refused because its analysis is *correct* and starts from
  exactly this fact.

## 171. The UI release vendors one ESM micro-framework, and the recommendation is Preact + htm

- **Decision**: recommend it; **this release vendors nothing and writes no framework code.**
  (v0.12.0)
- **Reason**: a build step would require reopening the constitution, which a UI release does not get
  to do, and continuing by hand produces another 50 KB without structure. Vendoring asks for
  **nothing new** — the existing vendored asset is five times the console's size and already ships
  under a pinned digest. **htm needs no compile step**, so the source a maintainer edits is the
  source the browser runs, which is the actual content of principle 6.

## 172. Theme persistence goes to a cookie, not `localStorage`

- **Decision**: a cookie carrying a theme name from a closed set and nothing else. (v0.12.0)
- **Reason**: `localStorage` is not available — the F2 guard's value comes from being an absolute,
  and that shape stops working the moment it acquires a carve-out. A server-side preference is
  correct and too expensive for a display setting. An unrecognised value falls back to the system
  preference, and the cookie is **not** `HttpOnly`, which is why it may never carry anything whose
  disclosure matters.

# v0.13.0 — the UI

## 173. The situation card keeps the held-card behaviour; the reconciler is a roadmap line

- **What measurement changed**: the renderer's diff **already** gives the property the gesture
  invariant asserts — a button node is object-identical across two re-renders and stays connected.
- **Decision**: hold the **payload** of an expanded card, count the updates withheld, release both
  on collapse. (v0.13.0)
- **Reason**: relying on node identity is the trap. **Node identity is not meaning identity.** An
  operator who has ticked members 2 and 4 is asserting something about *those two alarms*; if an
  update re-members the situation underneath them, every DOM node survives and the marks now refer
  to alarms the operator never saw. Freezing the payload freezes the thing the operator is actually
  judging.

## 174. The framework is vendored as two assets, not one

- **Decision**: two upstream files, each with its version in the filename and its own digest.
  (v0.13.0)
- **Reason**: a combined bundle is two packages at versions the filename cannot state — the
  standalone build pins a renderer four years older than its own version number. A locally-built
  bundle matches no upstream release, so the checksum would pin only *our* build.

## 175. The UI becomes an ES module graph, and the static allowlist enumerates every module

- **Decision**: keep the compile-time allowlist; add a test that the set on disk and the set in the
  allowlist are **equal in both directions**. (v0.13.0)
- **Reason**: serving a directory is a path-traversal surface and makes *"what does this appliance
  serve?"* unanswerable from the code — deleting a deny-by-default property to save typing.

## 176. Routing resolves capability before construction, which is F53's structural repair

- **Decision**: the router returns a **decision**, and only a `view` decision is ever turned into a
  mounted component — rejecting both a check at the top of each of the 17 view loaders and a single
  check in the router before dispatch. (v0.13.0)
- **Reason**: a check is 17 chances to forget, or one thing a later refactor can reorder; a decision
  makes the refusal a *different component*, so the real one is never constructed, its
  `componentDidMount` never runs, and there is no request to suppress. **The zero is a decision, not
  a dereference.** There is exactly one call site that mounts a decision (`shell.js`); a second
  would be a second authorisation surface, which is the shape of F53.
- **Restored in v0.15.1** (#215): v0.15.0 deleted this entry on a measurement of what the tree
  cites, and the instrument could not see the one citation — an f-string in
  `tests/test_security_ui.py`. Deleting it was therefore outside #201's own rule.

## 178. The hardening-only class is enforced where a write path exists, and named where none does

- **What measurement changed**: **most hardening-only values have no write path at all** — they are
  module constants nothing in the API can set. The scorer's degeneracy bounds are the exception.
- **Decision**: enforce where a write path exists; render the rest as facts with the reason.
  (v0.13.0)
- **Reason**: persisting the sufficiency floors changes the promotion gate's inputs — evidence-chain
  work needing a route, a capability and an audit decision a UI release has no reason to add. A
  control that *appears* to harden the appliance and does nothing is the empty placeholder in its
  most damaging form.

## 179. `RuntimeConfig` does not grow; the config route reports precedence instead

- **Decision**: report the three columns from the existing route. (v0.13.0)
- **Reason**: every addition to the live-reloadable holder is a live-reload path in a running
  receiver. Reporting adds no route, no capability, no audit action and no migration.

## 180. The accessibility floor is met and the graph is explicitly excluded

- **Decision**: meet the floor for the whole console; give the graph and the timeline a label and a
  **text equivalent** rather than ARIA semantics. (v0.13.0)
- **Reason for the exclusion**: an accessible force-directed graph is genuine work, and doing it
  badly produces a worse experience than a clear pointer to the text equivalent. **An accessibility
  claim that overstates is worse than none.**

## 182. A UI release gets one pass with eyes on it, and that pass is not automated

- **What happened**: with all 1428 tests green, a real-browser pass found **six defects on screen**
  — five template whitespace collapses and one blank cell in the table whose entire purpose is to
  explain why a setting has the value it has. **The harness dumps an identical, correct tree in all
  six cases.**
- **The class**: *the harness cannot see whitespace, and it cannot see emptiness.* Both are
  properties of what is **painted**; a test reading `textContent` reads what was **written**.
- **Decision**: a release that changes the UI gets **one pass in a real browser before it ships**,
  recorded with what was driven **and what was not**. Explicitly not in `make qa`, not in CI; the
  driver lives outside the repository. (v0.13.0)
- **Reason**: a browser in the test suite is a build-adjacent dependency principle 6 forbids, and it
  is the wrong instrument — what caught these six was *reading the screen*, and an automated pass
  asserts only what someone already thought to assert.

# v0.14.0 — the model family

## 183. Tree ensembles run in process; the ONNX-door sentence is superseded, not edited

- **What measurement changed**: nothing, because nothing needed to be. The premise is true and **the
  conclusion does not follow from it.** Principle 5 forbids *dependencies*, not *implementations* —
  and a CART over three continuous features is arithmetic, exactly as v0.9.0 argued of logistic
  regression. The sentence read a *packaging* constraint out of a premise about *packages*.
- **Decision**: supersede by reference; do not edit the original. (v0.14.0)
- **Reason**: editing falsifies the record. A reader who finds the original claim cited in a
  document from three releases ago must be able to find the original claim.

## 184. The chain is resequenced: the model family first, the cartridge after

- **What measurement changed**: `seal.spend()` has existed since v0.10.0 with a query count of **0**
  — not by discipline, but because the branch that calls it is unreachable on this corpus.
  Reproduced with a control corpus returning non-zero through the same code, so the zeros are
  properties of the corpus rather than of the query. **The project has never observed a promotion
  happen.**
- **Decision**: the model family first. (v0.14.0)
- **Reason**: shipping the cartridge alongside four new model kinds puts two unrelated risks in one
  release, where a failure in either contaminates the evidence for the other. **Driving the chain
  end to end costs no new process, no new dependency and no new trust surface**, and it means the
  cartridge arrives at a gate that has been watched to open.

## 185. Three model kinds ship in process, in pure Python; linear regression is refused

- **Decision**: `tree`, `forest` and `gradient_boosting`. (v0.14.0)
- **Reason**: one tree alone would leave the interesting half of #183's claim untested — the point
  is that an *ensemble* needs no cartridge, and a single tree exercises neither the ensemble path,
  nor the seed, nor the shrinkage.

## 186. `LinkScore` gains `basis` and `base_value`: a minor contract bump

- **Decision**: two optional, defaulted fields. (v0.14.0)
- **Reason**: writing a Shapley value into a field called `weight` is a lie in the field's own name,
  and synthesising one is worse — a *plausible* number nobody can detect as wrong. Changing the
  field's type is a **major** bump under #49. Two optional fields make *"does `weight` mean anything
  here?"* a question the data answers rather than a convention a reader has to know.

## 187. Every kind owns its degeneracy rules; the dispatch module owns the bounds

- **Decision**: rules beside their model, bounds passed in as arguments. (v0.14.0)
- **Reason**: keeping every rule in the dispatch module recreates the problem one file over. Moving
  them keeps the validation entry point the **single validation point** — a rule is unreachable
  except through it, which is the property that mattered, and it was never *"the rules are textually
  inside that function"*.

## 188. `forest` carries a seed, and the seed is inside `params_document`

- **Decision**: a seed in the document (and therefore in the fingerprint), with the draw a **pure
  function** rather than a stream. (v0.14.0)
- **Reason, in three parts**: two forests from the same rows with different seeds are different
  models, so a seed outside the document would let them share a fingerprint; a function has no state
  to advance, so a bag cannot depend on how many calls preceded it, which is why a resumed fit
  cannot silently differ; and **both halves are tested** — same seed byte-identical, *different*
  seeds differ — because a test of the first alone passes against an implementation that ignores the
  seed entirely.

## 189. The boosted kind is named `gradient_boosting`, never `xgboost`

- **Decision**: `gradient_boosting`. (v0.14.0)
- **Reason**: XGBoost is a **specific algorithm** and also a project name; what ships is first-order
  boosting with squared loss and constant shrinkage. A row whose kind read `xgboost` would put a
  false claim in the model registry and, through the promotion action, in the **audit log** — whose
  entire value is that what it says happened, happened. A naming convenience is not worth spending
  that.

## 190. Attribution is exact marginal Shapley, tabulated per cell at registration

- **What the plan's cost sentence gets wrong**: it says marginal values *"cost eight evaluations"*.
  Against a 256-row background they cost **1 536**, which at up to a hundred pairs per activation is
  tens of milliseconds per trap on the ingest path. The plan is ratified and **is not edited**; the
  discrepancy is carried forward as an opinion for the next release.
- **Decision**: precompute, do not approximate. (v0.14.0)
- **Reason**: approximating changes the registered method to fit an implementation budget — the
  analysis-plan analogue of moving a floor after seeing the data. Precomputing changes nothing about
  the numbers: Shapley is **linear in the model**, and a tree is **constant on the cells its own
  thresholds cut**, so each cell's contributions are computed once and scoring is three binary
  searches.

## 191. `scoring.py` splits: the contract moves to `scorer_contract.py`

- **Decision**: split. (v0.14.0)
- **Reason**: trimming buys one release and pays for it by deleting the reasoning that makes the
  module readable; exempting spends the cohesion escape hatch on a module that has an obvious split
  — no invariant requires the contract and the default implementation to share a file. The
  dependency runs one way and cannot cycle.

## 192. The feature vocabulary moves out of `challenger.py` into the contract

- **Decision**: move the three names; re-export them. (v0.14.0)
- **Reason**: the guard exists to keep a **shadow model** off the champion path, and it fired on a
  module that wanted two constants and a pure function. Widening its allowlist would say
  *"attribution is part of shadow mode"*, which is false, and a guard whose allowlist grows whenever
  it inconveniences someone stops meaning anything. **These names were never the challenger's**, so
  the move makes the guard *stronger* rather than wider.

## 193. The lower bound of the admission band is discrimination, not the clock

- **What was measured**: comparing like with like, the degenerate-versus-working gap is **0.019 µs**
  in one shape and **0.001 µs** in the other, against **0.095 µs** of run-to-run spread on a single
  unchanged arm. Over the same corpus the two produce **10 976 links and 0**.
- **Decision**: **spread** over the fixed probe set, **and decision** (at least one probe linked and
  at least one not). Both hardening-only; an optional wall-clock floor exists as a mechanism-class
  setting defaulting to zero, and the surface says it is a proxy. (v0.14.0)
- **Reason**: the plan calls the degenerate model *"the fastest available"*; measured, it is neither
  faster nor slower, so **the clock carries no signal about this failure in either direction** — a
  stronger argument for the floor than the plan's. The discrimination checks have no off switch, so
  a wall-clock floor **adds** to them. For a model whose parameters cannot be inspected — the
  cartridge — **a behavioural floor is the only form threshold-reachability can take**; see
  [F62](../findings.md) for the measured limit of its decision half.

## 195. The promotion gate measures the candidate, not whatever is in shadow

- **Decision**: the challenger arm is the scorer the candidate row describes, built by **the**
  dispatch — the same function that activates a row. (v0.14.0, F59)

## 196. The console reports what is DECIDING, not what is configured

- **What it meant for an operator**: a promoted champion, and five weights on screen that decide
  nothing, under a heading asserting that they do. It predates the tree kinds and survived two
  releases, one of them a console rewrite, because nothing had ever been promoted.
- **Decision**: split the one function into two, because it had two meanings — what the form edits,
  and what the engine is scoring with. The console leads with **"What is deciding"** and marks the
  configured weights inactive when they are. (v0.14.0, F60)
- **Reason**: the weights are still shown deliberately — an admin may retune and roll back — so the
  defect was not showing them, it was calling them active. `kind` is derived from the running scorer
  and never from the artefact row: reporting the intent of a load that failed is how a fail-safe
  becomes a second lie.

# v0.15.0 — the repository

*Ten entries, and every one of them changes a rule this project had written down as permanent. They
are here rather than in a gate document because #197 is the entry that abolishes gate documents.
From this release an entry is about six lines: decision, reason, release.*

## 197. A release writes no gate document, no scope document, no build report and no security review (v0.15.0)

- **Measured**: v0.14.0 alone produced 3 638 lines of record — 2 579 of gates across eight files,
  plus a scope document, a build report, a security review and a pre-registration. Fourteen releases
  of that is the 58 000 lines this release deletes. Without a change of convention the pile is back
  in four releases.
- **Decision**: findings go to `docs/findings.md`, five lines each; decisions go here, six lines
  each; everything else is a commit message and a `CHANGELOG` line. Working notes during a build are
  scratch files **outside** the repository.
- **Reason**: the evidence chain lives in `tests/`, not in prose about `tests/`. A guard still goes
  red when the defect is injected, and the demonstration can be re-run in thirty seconds; a document
  describing that demonstration is commentary, and commentary is what accumulated.

## 198. `docs/` is organised by what the reader is doing, not by which release produced it (v0.15.0)

- **Decision**: one file per reader task at the top of `docs/` — install, configure, operate,
  console, correlation, security, troubleshoot, architecture — plus an index, the open findings, the
  decision log, the pre-registered analysis plans, and `docs/plans/` for releases that do not exist
  yet.
- **Reason**: it is the shape Zabbix, Grafana and Prometheus use, and the reason they use it is that
  a reader knows what they are trying to do and does not know which release did it.

## 199. The historical-document taxonomy is retired, and the guard that pinned it is rewritten (v0.15.0)

- **Decision**: the live-document set becomes **all of `docs/` except the decision log**, and
  `test_the_historical_exclusion_is_exactly_the_record_taxonomy` is rewritten to assert the new,
  smaller exclusion rather than the old one.
- **Reason**: the exclusion was the guard's biggest risk when it named four directories; it is a
  much smaller risk naming one. `DECISIONS.md` stays excluded for the original reason — it is the
  record, and an entry that said what v0.6.0 believed is not a claim about what is true today.

## 200. Principle 8 is replaced: the instrument precedes the change it measures (v0.15.0)

- **Reason**: the foresight was real and is this project's best pattern — v0.7.5 fixed the feedback
  path before v0.8.0 built a dataset on it, v0.9.2 fixed the evidence boundary before v0.10.0 built
  a judge over it, v0.12.0 built a DOM harness before v0.13.0 rewrote the UI. **None of that value
  came from the specification documents; it came from the ordering.** What the documents produced
  was volume.

## 201. The decision log drops the entries no shipped code cites, and renumbers nothing (v0.15.0)

- **Measured**: 196 entries; 113 cited from `src/`, 73 from `tests/`, **129 from either**, 67 from
  neither. (The build brief predicted 118/76/138/58; the difference is reported in the release notes
  and this measurement is the one acted on.)
- **Decision**: delete the 67 no code cites; condense the 129 that survive to about six lines each —
  decision, reason, release. **Renumber nothing.** Gaps in the sequence are free.
- **Reason for not renumbering**: 129 citations in shipped `src/` and `tests/` name these numbers,
  several of them in the form *"argued in #N rather than asserted"* where the argument is what the
  docstring depends on. Renumbering means editing 129 references to fix a problem that does not
  exist.

## 202. The chain is resequenced: v0.15.0 is the repository (v0.15.0)

- **Decision**: v0.15.0 is **the repository**; v0.15.1 the package tree; v0.15.2 the console's
  defects; v0.15.3 the console designed; **v0.16.0** the external cartridge; **v0.17.0** archetypes.
  Thirteen rows.
- **Reason**: the cartridge is the project's riskiest step — a second process, a preemption harness,
  an amendment to *"ingestion is sacred"* — and taking it while a stranger cannot find the install
  instructions is the wrong order. Nothing in the cartridge's own argument (#93, #183, #184) moves.

## 203. Forward specifications move to `docs/plans/` (v0.15.0)

- **Decision**: `docs/architecture/` becomes `docs/plans/`, and `docs/architecture.md` is the
  reader-facing description of what exists.
- **Reason**: the two are different jobs and the old name was doing both. `docs/architecture.md`
  beside a `docs/architecture/` directory is a tree a reader has to guess at; "what is built" and
  "what is specified but not built" is a distinction worth putting in the path.

## 204. The pre-registration hashes' second home moves to `docs/record.md` (v0.15.0)

- **Decision**: the second home becomes `docs/record.md`. The four hashes are **copied, not
  recomputed** — from
  `docs/gates/{v0.9.0-phase-1,v0.10.0-phase-0,v0.11.0-phase-0,v0.14.0-phase-0}.md` at `3ecf237`, and
  each entry names the commit and the file it came from.
- **Reason**: the two-sided discipline is what the guard is for — one hash alone could be edited
  quietly in the same commit as the plan, two in different files make that an obviously deliberate
  diff. The discipline survives the move; only the second file changes.

## 205. The four derived fixtures become a loader (v0.15.0)

- **Measured**: the four fixture JSONs are their `eval/corpus/` namesakes with `description` removed
  and `truth` dropped from every event — 97 065 bytes nothing derives and nothing keeps in step.
  **The relation is exact in CONTENT and not in BYTES**: three re-render byte-for-byte at `indent=2`
  and `olt_storm.json` is the same 501 events serialised compactly, so a byte-level check would have
  failed on one file and a build trusting the prediction would have replaced it with the wrong
  bytes.
- **Measured, and it corrects the premise**: the derivation runs the *other* way at build time.
  `eval/corpus_gen.py` reads the four fixtures and writes the corpus, and `make corpus` reproduces
  the committed corpus byte-for-byte — so the fixtures are an input, not an output. Both copies are
  the same stream in opposite roles, **with nothing guarding them against divergence**, and they are
  replayed by different gates: the suite replays the fixture, `make eval` replays the corpus.
- **Decision**: the loader returns **parsed events**, which is what every consumer wanted anyway —
  `util.fixture_events` re-encodes them to the wire and `test_api.py` iterates them — so rendering
  never enters it. The four files are deleted, and `corpus_gen`'s four relabel functions read the
  corpus and recompute the labels over it. `eval/corpus/` is untouched: `eval/harness.py` reads
  `sorted(CORPUS_DIR.glob("*.json"))` and the frozen `c2e8a0ce…` depends on that directory's exact
  contents.
- **Reason**: a copy that drifts is worse than a derivation that cannot. Verified by execution
  against the four files **before** deleting them, with a control proving each half of the strip is
  load-bearing — stripping only `description`, or only `truth`, reproduces none of them.
- **The cost, stated**: those four relabel functions become a fixed point rather than a derivation
  from an independent source. That is a smaller loss than it sounds, because the independent source
  was a second copy of the same bytes that nothing compared; `make corpus` still rewrites the corpus
  deterministically and still fails if the labelling logic changes.

## 206. Tags are kept, completed, and gate nothing (v0.15.0)

- **Decision**: keep every existing tag, ship the three missing ones in the delivered `.git`, and
  stop treating a tag as a precondition for anything. The recovery procedure becomes three commands
  in `CONTRIBUTING.md`.
- **Reason**: a tag is a convenience for finding a release. Nothing in `make qa`, the promotion gate
  or the pre-registration guards reads one, and the one that carried real evidence —
  `v0.14.0-gate0`'s message — carries it in the annotation, which travels with the tag object.

# v0.15.1 — the package tree

## 207. Layer at the top, domains inside `engine/`

- **Decision**: the top level of the package is the **layer**; the 46-module `engine` layer is
  divided into six **domains** inside it. Rejected: layer-only, which moves the bucket into a folder
  and renames the problem; domain-only, which abandons the one structural rule this project has
  enforced since v0.7.3 and has a test for. (v0.15.1)
- **Reason**: the rule survives *and* the bucket is broken, and the guard gets stronger rather than
  weaker — `test_layers.py` stops being a 62-name dictionary somebody has to remember to edit and
  becomes an observation about where a file was saved (#212). The cost accepted is depth: a module
  path is now two components (`engine/model/attribution.py`) where it was one.
- **Measured**: 46 of 62 mapped modules were `engine` — a layer holding 74 % of the tree describes
  none of it. After the split the largest domain is nine modules.

## 208. The six domains are derived from the import graph, and the derivation is what chose them

- **Decision**: `correlate/` (9), `dataset/` (6), `model/` (9), `evaluation/` (9), `report/` (8),
  `operate/` (5). Departures from the shape the brief sketched: `scorer_contract` joins `scoring` in
  `correlate/`; `retention_policy` joins `capture` in `dataset/`; `scorer_lifecycle` is `operate/`,
  not `model/`, because it is an `EngineBase` mixin; `shadow_render` and `shadow_report` join
  `report/`, which is what the brief's own suspicion about `shadow_report` versus `agreement_report`
  was pointing at; and the brief's `shadow/` and the promotion half of its `evidence/` are one
  domain, `evaluation/`. (v0.15.1)
- **Reason**: a domain boundary that the imports cross in both directions is a boundary the code
  does not have. Grouping by name would have kept `shadow_report` away from the two report modules
  it is a sibling of, and split the judge from the promotion gate that reads its verdict.
- **Measured**: over the same 190 edges, the chosen partition has **zero** cycles between domains
  and yields a strict order — `correlate` imports no other domain, then `dataset`, `model`,
  `evaluation`, then `report` and `operate`, which nothing but the process entry points import. The
  brief's sketch has **nine** cycles across five of its six groups.

## 209. What is a layer directory, what stays at the package root, and what keeps its name

- **Decision**: three layer directories are created — `engine/`, `ingest/`, `crosscutting/` — and
  two existing packages **are** their layer and keep their names: `api/` is http, `store/` is data.
  The package root holds exactly four modules, and they are the process entry surface:
  `__init__.py`, `__main__.py`, `main.py`, `runner.py`. Rejected: `http/api/` and `data/store/`.
  (v0.15.1)
- **Reason**: `python -m netcorenoc.main` is a **public interface** — the `Dockerfile`,
  `deploy/netcorenoc.service`, `flake.nix`, `docker-compose.yml`, `README.md` and the bug-report
  template all print it — so moving `main.py` is a behaviour change, which this release makes none
  of. And `api/routes/static.py` and `store/types.py` locate `ui/` and `migrations/` as
  `Path(__file__).parent.parent / …`: moving either package changes a line that is not an import,
  and the alternative — moving the 47 UI files and 13 migrations too — is churn that buys no layer.
- **The cost, stated**: `api/` and `store/` are directories not named for their layer, so two of
  the guard's five rows stay declarations. Five, not 62 — and every row names a directory, so the
  property the release is for still holds for every module in the tree.

## 210. Two levels of nesting, and the guard says two rather than one

- **Decision**: `MODULE-ARCHITECTURE` §9's *"one level of nesting, where earned. Never two"* becomes
  **two, where earned; never three**, and `test_the_package_is_at_most_one_level_deep` is renamed and
  re-pinned to match. (v0.15.1)
- **Reason**: the second level is exactly what #207 buys, and it is bounded by the same word —
  *earned*: level one is the layer, level two is the domain, and there is no third thing a path is
  allowed to say. Leaving the guard at one and exempting the tree would make the rule a comment.
- **Note**: v0.15.1's brief states no such guard was found. It exists, at
  `tests/test_architecture.py`; the rule it enforces is what made this a decision rather than an
  oversight.

## 211. The behaviour-identity harness records 90 routes against four principals, and pins the bytes

- **Decision**: `tests/behaviour_identity.py`, driven by `tests/test_behaviour_identity.py`. It seeds
  one database per principal from `eval/corpus/fiber_cut.json` through the real ingest path at a
  fixed epoch, drives every route the app registers in registration order — reads and writes, the
  static surface included — as anonymous, viewer, editor and admin, and emits one canonical document
  whose SHA-256 is the gate figure. (v0.15.1)
- **Reason**: in a release that is entirely moves, *"the tests pass"* is weaker than *"the HTTP
  surface is unchanged"*, because the assertions were written against the code that produces the
  shape. A role is included because a principal that renders differently is a behaviour.
- **Canonicalisation is an enumerated substitution list, never a pattern**: an over-broad one
  passes the diff by deleting the evidence. Completeness is asserted by two runs in separate
  processes; the ability to fail, by a deliberate response change.

## 212. `test_layers.py` becomes a directory check, and it changes last

- **Decision**: the 62-entry `LAYER_OF` dictionary is replaced by a five-entry directory table, and
  the replacement lands **after** every module has moved, in its own commit. No compatibility shim:
  `LAYER_OF` is kept correct through every move commit and deleted in one. (v0.15.1)
- **Reason**: changing a guard in the same commit as the thing it guards is the shape that hides a
  mistake, and a shim would be a third state that nothing measures. Keeping the old dictionary
  honest through 12 move commits costs one edited line per commit and means the layer rule is
  enforced by a guard that predates the tree at every point in the release.

## 213. The import paths break, and nothing re-exports the old ones

- **Decision**: `from netcorenoc.correlate import …` becomes
  `from netcorenoc.engine.correlate.correlate import …`, with no compatibility re-export in any
  `__init__.py`. `netcorenoc`, `netcorenoc.main`, `netcorenoc.api` and `netcorenoc.store` are
  unchanged, so every documented entry point still resolves. (v0.15.1)
- **Reason**: pre-alpha, zero users, and every importer is in this repository, so the whole cost
  is one mechanical rewrite `mypy --strict` and the suite verify. A re-export would make the old
  path work forever and the tree's truth optional — the defect this release exists to remove.
  `test_structure.py::SUBMODULES` enumerates the new paths, so one that does not resolve from the
  **installed** package is a red test rather than a runtime error.

## 214. The `src/` byte-pin is recomputed in every move commit, and is renamed to stop naming v0.14.0

- **Decision**: `SRC_TREE_AT_V0_14_0` becomes `SRC_TREE_DIGEST` and is recomputed in each of the 12
  move commits rather than once at the end. The digest hashes each path alongside its contents, so a
  move already moves it. (v0.15.1)
- **Reason**: recomputing once at the end would leave the strongest whole-tree guard checking a tree
  that no longer exists for 11 commits — the `TRAP_PATH_HASHES` failure mode the brief names, at the
  scale of the whole package. The rename is because the constant would otherwise assert a claim
  about v0.14.0 that v0.15.1 has stopped making.

## 215. The citation reader is widened to f-strings, and #176 is restored (F64)

- **Decision**: `_python_citations` filters on `FSTRING_START` / `FSTRING_MIDDLE` / `FSTRING_END` as
  well as `COMMENT` and `STRING`, and decision #176 — deleted by v0.15.0 — is restored, condensed.
  (v0.15.1)
- **Reason**: PEP 701 moved f-strings out of `tokenize.STRING` in Python 3.12, so a guard written
  before that has been silently partial ever since. #201's rule is that an entry may be removed only
  when nothing cites it; something did cite #176, in an f-string, and the instrument could not see
  it. The deletion was therefore outside the rule that authorised it, and restoring the entry is the
  repair — repointing the citation would delete the argument instead.
- **Measured**: with the filter widened, the tree yields exactly **one** citation that was invisible
  before, `#176` in `tests/test_security_ui.py`, and the resolve-guard goes red on it. Controls: the
  same citation in a plain string and in a comment was seen both before and after.

## 216. The smallest agent is nine modules, and eight of them are one subtree

- **Decision**: named, not built. A `zabbix-agent`-shaped collector would be
  `ingest/{receiver,events,known_oids}.py` plus `crosscutting/{settings,logsetup,runtime}.py`, a
  transport, and — if it is to say anything about what it collected —
  `engine/correlate/{varbind_profile,varbind_accum}.py`. (v0.15.1)
- **Reason**: the release owes the agent one thing — not to make it awkward — and #207 answers it
  for free: `ingest/` imports nothing above it, so *"just the wire parser"* is a directory.
- **The finding, for whoever builds it**: `store.py` imports `events` and `known_oids`, so the
  vocabulary an agent would share is **already below the data layer** — a shared package boundary
  rather than a copied protocol, and that choice is therefore still open. The two profiler modules
  are the part that is *not* a subtree: they sit in `correlate/`, which an agent would not have.

## 217. `engine.py` moves in the first `engine/` commit, so the trap path cannot be last

- **Decision**: the move order is `crosscutting/`, `engine/operate/`, `engine/report/`,
  `engine/evaluation/`, `engine/model/`, `engine/dataset/`, `engine/correlate/`, `ingest/` — not
  the risk order the brief specifies, which puts every trap-path module last. (v0.15.1)
- **Reason**: **a package and a module of the same name cannot coexist.** The moment
  `src/netcorenoc/engine/` acquires an `__init__.py`, `import netcorenoc.engine` resolves to the
  package and `engine.py` becomes unreachable — so it moves in whichever commit creates `engine/`,
  and `operate/` is that commit. A namespace package does not help: `netcorenoc.engine` would
  resolve to the module and `netcorenoc.engine.report` to nothing.
- **What actually protects the trap path is the pin, not the ordering**: `TRAP_PATH_HASHES` moves
  with each file in the same commit, and its five values are unchanged for the whole release. The
  brief's ordering is a proxy for that discipline; where the two conflict, the discipline wins.
- **Measured**: three of the five pinned modules — `capture`, `correlate`, `learn` — still move in
  the last three commits. `engine` moves second and `receiver` last.

## 218. The module-size guard counts a module's body, not its imports

- **Decision**: `test_architecture._modules` measures lines that are **not import statements**.
  `COHESION_EXEMPT_CEILING` for `engine.py` moves 580 -> 545, and it falls because the metric
  changed rather than because the file did. Rejected: raising `MAX_MODULE_LINES`, which is changing
  a rule to fit an outcome, and `DEBT_ALLOWLIST`, which must stay empty. (v0.15.1)
- **Reason**: every moved module's import path gained two components, `ruff format` wraps what no
  longer fits in 100 characters, and `capture.py` went from 398 lines to **402** — over a guard
  about *"one noun or one decision"* — without a line of its substance changing. An import
  statement cannot hold logic, so measuring the body loses nothing and stops a package
  reorganisation from consuming a module's budget.
- **Measured**: on the moved tree, exactly one module exceeds 400 body lines and it is `engine.py`,
  which is permanently exempt. `learn.py` (393 body, 400 total) and `promotion.py` (391, 400) sit
  on the line, so this is structural rather than one awkward file.

## 219. The detail panel is removed, because no view populates it (v0.15.2)

- **Decision**: `app/context.js`, the `#context` region, its stylesheet rules and the unused import
  in `situations.js` are deleted. The shell becomes two regions. Rejected: populating it from
  Situations, which would build a second home for facts the expanded card already shows in place.
- **Reason**: the brief's reading is *"it belongs to Situations, and the shell should not render the
  region on views that do not use it."* Measured, **no** view uses it, so that reading reduces to
  not rendering it. `registry.js` states the rule this breaks — *"nothing here is a placeholder"* —
  and a permanently empty third of the screen is one. Removal was chosen over completion because
  nothing in phase 1 has a second thing to say about a selection.
- **Measured**: driven in Chromium as admin, editor and viewer over all 17 views, `#context` carries
  `context-idle` and the text *"Select something to see its detail here."* on **17 of 17** at
  1440 px, at a computed width of 320 px. Expanding a situation card does not change it.
- **Note**: the served surface goes from 90 method/path pairs to **89**, so #211's figure is a
  statement about v0.15.1's tree rather than a standing one. Removing one module is a deliberate
  diff in five places — `routes_static.STATIC_ASSETS`, `declare.UNAUTHENTICATED_PATHS`,
  `ROUTE_ORDER_BASELINE`, `UI_HASHES`/`UI_SIZES`, and the behaviour record — which is the
  deny-by-default machinery working rather than five chores.

## 220. Below 760 px the repair is the link row, not the panel (v0.15.2)

- **Decision**: the narrow-viewport rule that hid `.context` goes with the panel (#219), and the
  per-term contributions are made readable at 390 px by letting `.linkrow` wrap. No drawer, no
  second route, no inline expansion.
- **Reason**: the constraint is that the per-term contributions must be reachable on any device the
  operator has. They already were — they render in `#work`, not in the panel — so the drawer the
  brief anticipated would have solved a problem this console does not have. What is actually broken
  is narrower and cheaper: the row they sit in overflows and clips.
- **Measured**: at 390x844 the *"Why these were grouped"* section exists, is visible, is inside
  `#work`, and shows all 90 term numbers. Every one of the 30 `.linkrow` boxes overflows by 51 px
  and all 30 `.linkpair` labels fall outside the viewport with no scrollable ancestor (F67). At
  1440x900 the same measurement is 0 px and 0 labels.

## 221. There is no icon system in this release, and the glyph set is stated rather than replaced (v0.15.2)

- **Decision**: the 17 Unicode glyphs in `registry.js` stay, and no inline-SVG module is added.
- **Reason**: choosing marks that read as one family is choosing what the product looks like, which
  #223 gives to v0.15.3. Replacing 17 glyphs with 17 hand-drawn paths inside a repair release would
  spend the release's whole visual budget on the half of the problem that is not broken: a glyph
  renders, is announced correctly (each is `aria-hidden` beside a text label), and is legible.
  Inline SVG in one module remains the right answer; it is the *when* that moves.
- **Measured**: 2 `<svg>` elements in the console, both data surfaces. The glyphs are decorative in
  every one of their 17 call sites — `nav-glyph` is `aria-hidden="true"` and carries a `nav-label`
  beside it — so none of them is load-bearing for a screen reader today.

## 222. System health renders what `/api/stats` already serves, and the route does not change (v0.15.2)

- **Decision**: `queue_depth` and the five `receiver.*` counters go on the Overview. Trap rate is
  derived in the client from `receiver.received` between two polls and is labelled with the window
  it covers. `/api/stats` gains no key, so no declaration and no capability move.
- **Reason**: the appliance already measures itself and the console throws the measurements away.
  Rendering what is served is most of the value at none of the cost of a new route surface. CPU,
  memory and uptime stay absent and are recorded as absent rather than added.
- **Measured**: `/api/stats` returns 11 keys against a running appliance; the console rendered 7 of
  them and `queue_depth` plus `receiver.{received,accepted,denied,quarantined,dropped}` reached no
  screen. `receiver.denied` is the only signal an operator has that their allowlist is wrong (F68).

## 223. v0.15.3 chooses what the product looks like; this release only fixes what is broken (v0.15.2)

- **Decision**: no type scale, no spacing rhythm, no palette, no icon family, no rewritten prose.
  This release changes presentation only where the current presentation loses information or
  misstates a fact — a clipped label, a caption describing an encoding that is not there.
- **Reason**: repairing four defects and choosing an identity are different jobs and doing both
  means neither is reviewable. The rule that decides the boundary in a hard case: **ambiguity about
  whether a change is presentation or identity resolves to identity**, and it waits.
- **Measured**: `style.css` is 544 lines with **three** `@media` blocks, not the four
  `plans/v0.15.2-console.md` §2 states — one system-preference, one layout breakpoint, one
  reduced-motion. The fourth occurrence is inside a comment.

## 224. An operation test boots a real appliance, and it lives in `tests/` (v0.15.2)

- **Decision**: `tests/test_operation.py` drives a real `python -m netcorenoc.main` process — real
  SNMPv2c PDUs over a real UDP socket, arriving over time; situations asserted against the
  generator's ground truth; the console read over HTTP as a signed-in principal. It reuses
  `eval/simulation/`'s appliance host and network generator rather than reimplementing them, and
  `eval/simulation/` keeps them. Determinism is asserted on a **clock-free canonical projection**
  of the outcome, never on wall-clock output.
- **Reason**: `eval/simulation/drive_http.py` is already this test and nothing runs it; its
  ten-increment loop is half an hour of wall clock, which is why. The repair is a bounded drive in
  the suite, not a smaller reimplementation of a harness that works. `eval/` holds the frozen corpus
  and the deterministic offline harness; a live drive of the appliance is a test, so it goes where
  tests go.
- **Measured**: `eval/simulation/` is 9 modules and 2 254 lines; `tests/test_simulation.py` is 308
  lines and boots no appliance. `drive_http.py` (295 lines) and `measure.py` (278) are imported by
  nothing in the tree.

## 225. A failed startup exits; the store is closed on every path (v0.15.2)

- **Decision**: `runner.run` opens the store inside a `try:` whose cleanup always closes it, and the
  cleanup's `await asyncio.gather(*tasks)` no longer lets a failed task's exception skip the drain,
  the final maintenance pass and `store.close()`. The original exception still propagates, so the
  process still fails — it fails *and exits*.
- **Reason**: an appliance that hangs is worse than one that crashes, because every restart policy
  written for it (`Restart=on-failure`, `restart: unless-stopped`) is keyed on exit. Rejected:
  making the `aiosqlite` thread a daemon, which would be reaching into a dependency to paper over a
  cleanup path this code owns.
- **Measured**: F66 — five treatments needed `SIGKILL` after 32.0 s; two controls exited in 0.5 s
  and 12.1 s. After the repair, the same five exit on their own.

## 226. An unreadable environment variable names itself (v0.15.2)

- **Decision**: `Settings.from_env` reads each numeric variable through one helper that catches the
  conversion error and re-raises a `SettingsError` naming the variable, the value it was given and
  what it expects; `parse_allowlist` does the same for the entry it could not parse. One message
  shape, the one `legacy_env_error` already uses.
- **Reason**: the project's own rule is that a setting must never fail silently, and it built two
  careful messages to honour it. Five variables were never given one, and an operator who blanks a
  line in `.env` gets a 20-line traceback naming `''`.
- **Measured**: F69. Five variables, five bare `ValueError`s; the two documented refusals name the
  variable and its replacement.

## 227. A denied trap reaches the operator as a counter and a warning, never as a log line (v0.15.2)

- **Decision**: `receiver.denied > 0` raises an entry in `operator_warnings()` naming the count and
  the configured allowlist, evaluated where the warnings already are — per request, off the trap
  path. The receiver counters and `queue_depth` also render on the Overview (#222). No logging is
  added to `datagram_received`.
- **Reason**: principle 4. A counter incremented and reported is the right answer for anything that
  happens per trap, and the console already renders `warnings` as a banner above the work area on
  every screen — so the cheapest correct channel is the one that exists.
- **Measured**: F68 — an allowlist that refuses every source produced 0 log lines, `warnings: []`,
  and 0 rendered counters, in an arm whose control accepted all 8 traps.

## 228. d3 loads when the graph does (v0.15.2)

- **Decision**: the classic `<script src="/vendor/d3.v7.min.js">` leaves `index.html`; the two views
  that use d3 request it on mount by appending a same-origin `<script>` and awaiting it. The asset
  is still vendored, still checksummed, still licensed, still loaded from `'self'`.
- **Reason**: the standing v0.13.0 decision was *keep d3*, and that stands — writing a force layout
  is not this release's work. What was never decided is that **every** screen should pay for it.
  This is the third option the brief offers: make the cost visible by making it fall where it is
  incurred. The CSP is untouched: `script-src 'self'` permits a same-origin element, and no inline
  script is introduced.
- **Measured**: 279 706 bytes, 22x the two framework assets combined, on every cold load of all 17
  screens; `typeof globalThis.d3 === "object"` on every view in the browser drive. Two of the 17
  use it.

## 229. F65 gets a reading rule, not a guard (v0.15.2)

- **Decision**: `record.md` gains one sentence — a `netcorenoc.<module>` path in prose that does not
  resolve is a pre-v0.15.1 path, and `git log --follow` resolves it — beside the rule it already
  states for `docs/gates/…`. No guard is added.
- **Reason**: a guard that read module paths out of docstrings would have to be kept green through
  every future move, which is a standing cost paid to fix references that are already resolvable,
  and it would be a second reason to edit a docstring during a refactor. The precedent is one file
  up: the same shape, the same defence, the same answer.
- **Measured**: 50 occurrences (F70), 44 of them in `src/`. Zero are imports — `mypy --strict`
  passes over 214 files.

## 230. `flake.nix` joins the version check (v0.15.2)

- **Decision**: `tools/release_check.py` reads a fourth file, and `tests/test_structure.py` asserts
  that every place in the tree declaring a project version is one the check reads.
- **Reason**: the check exists so a release cannot be tagged with a mismatched version, and it was
  blind to a quarter of the declarations for fifteen releases. Adding the file without the test
  would leave the next declaration equally invisible; the test is the instrument, and it precedes
  the fifth file rather than following it.
- **Measured**: F73 — `flake.nix` said `0.1.0` while the check printed *"all sources agree on
  version 0.15.1"*.

## 231. What counts as broken presentation, and what waits for v0.15.3 (v0.15.2)

- **Decision**: this release changes a rendered thing only when the current rendering **loses
  information or states something false**. It fixed: a link row that clipped the pair it was about,
  a caption promising two encodings the timeline does not have, a citation on screen to a document
  deleted three releases ago, a node radius that grew past the layout's own collision radius, a
  force graph with no centring force, and a spinner with no animation. It chose nothing about type,
  spacing, colour or iconography.
- **Reason**: #223 gives the identity to v0.15.3, and the hard cases needed a rule rather than a
  feeling. *"Would an operator be misled, or unable to reach a fact?"* separates a clipped label
  from a font size, and it is the same question the rest of this project asks about a number with
  no window or a bar with no figure beside it.
- **Measured**: the graph's four nodes went from **1 of 4** on canvas to **4 of 4**, and its largest
  circle from 3.79 % of the panel to 0.34 % (F77). Nothing in the palette or the type scale moved.

## 232. `drive_http.py` and `measure.py` are removed, not completed (v0.15.2)

- **Decision**: delete `eval/simulation/drive_http.py` (295 lines) and `eval/simulation/measure.py`
  (278). The rest of `eval/simulation/` stays — `generator`, `shapes`, `labelling`, `diagnose`,
  `drive` and `appliance` are all reachable, and `appliance` is what `tests/test_operation.py`
  boots.
- **Reason**: **removal was chosen over completion, and the reason is that completing them is not
  worth it.** `drive_http.py` is the end-to-end HTTP drive this release rebuilt as
  `tests/test_operation.py`; keeping both leaves two implementations of one idea, one of which
  nobody runs and which takes half an hour when they do. `measure.py` reported the generated
  network's near-threshold pair distribution once, for a v0.14.0 gate document that v0.15.0
  deleted; nothing consumes its output and no release is going to.
- **Measured**: both are imported by **nothing** in the tree — not by `tests/`, not by `tools/`, not
  by the other seven simulation modules. `eval/simulation/` goes from 9 modules and 2 254 lines to
  7 and 1 681. `make eval` is byte-identical at `c2e8a0ce…`: neither file is on its path.

## 233. The last enabled admin is an invariant of the store, refused at the route (v0.15.3)

- **Decision**: one predicate — *"at least one enabled admin exists"* — lives in
  `crosscutting/auth.py` beside the password policy, is answered by one query
  (`store.count_enabled_admins()`), and is enforced by every route that could falsify it: role
  change and deletion today, disabling the day a disable route exists. The console also hides the
  control, and that is an affordance, never the control itself (principle 6).
- **Reason**: the alternative — a check written inline in each route — is how the two halves of F79
  came to disagree in the first place. Putting the predicate in the store and the refusal at the
  route keeps the HTTP shape (a 400 with a sentence) where HTTP concerns already live.
- **Measured**: **there is no disable route and no `set_user_disabled` in the tree** — `disabled`
  is read by `perform_login` and `get_session` and written by nothing. The brief asked for three
  injections; two are transitions that exist. Adding a disable route to make the third injectable
  would be adding a feature to test a guard, so the guard takes the transition as a parameter and
  `test_auth_invariants` asserts by execution that no route can reach `user.disabled` at all.

## 234. `bootstrap_admin` re-bootstraps when there is no admin, rather than when there are no users (v0.15.3)

- **Decision**: guard on `count_enabled_admins() == 0`, not `count_users() > 0`, and give the
  recovered account a free username (`admin`, else `recovery-admin`, else `recovery-admin-2`…) so
  it cannot collide with the demoted one. A CLI recovery command was considered and rejected.
- **Reason**: the function's own docstring said *"create the initial admin"* and its guard asked a
  different question; this is that guard corrected, not a new mechanism. A CLI command needs the
  locked-out operator to have shell access **and** to know the command exists, which is exactly what
  the person in F79 does not have — they have a browser and a restart. Security cost, stated: an
  attacker who can restart the process and read its log already owns the appliance, so minting an
  admin on a database with none lowers no barrier that was still standing.
- **Measured**: on the F79 database — two users, zero enabled admins — the shipped guard returns
  `None` and the corrected one mints a password. **The operator who is locked out today restarts
  the appliance and reads the new password from the log**; `docs/troubleshoot.md` says so.

## 235. Density is removed with its mechanism, not just its button (v0.15.3)

- **Decision**: delete the control (`shell.js`), the cookie and both accessors (`theme.js`), the
  `:root[data-density="comfortable"]` block (`style.css`), the harness's three reports of it, and
  the assertion that called it *"a first-class choice"*. One type scale, one spacing scale.
- **Reason**: removal beat completion. `comfortable` moved four tokens and **one step of the type
  ramp** (`--fs-md` 13→14), so the ratio to `--fs-lg` closed from 1.23 to 1.14 in one density and
  nothing else moved with it — two different sets of relationships, one of them chosen by nobody.
  DECISIONS #45 is why the mechanism goes too: a knob removed while its mechanism stays is worse
  than either.
- **Measured**: every reference, found by grep rather than memory — `style.css` (2), `theme.js`
  (6), `shell.js` (5), `tests/domharness/run.mjs` (3), `tests/test_security_ui.py` (1),
  `docs/console.md` (1). Nothing else in the tree reads `density()` or `data-density`.

## 236. Icons are generated inline SVG in one module, and the set is exactly what renders (v0.15.3)

- **Decision**: `ui/app/icons.js` exports one `Icon` component over a table of path data — 24x24
  viewBox, 1.5 px stroke, `currentColor`, `stroke-linecap="round"`, no fill. Seventeen view icons,
  the actions the console actually has, and nothing else. `aria-hidden` beside a text label at
  every call site, exactly as the glyphs were.
- **Reason**: `tests/test_build_step.py` rules out an icon package and `test_supply_chain.py` would
  demand a checksum and a licence for a vendored set — which is the argument for *drawing* them
  rather than acquiring them. The seventeen Unicode glyphs came from four blocks and rendered at
  whatever weight the operator's font stack chose; they were never a family (#221).
- **Measured**: `registry.js` had **zero `icon:` entries** and seventeen `glyph:` ones. A test walks
  the module and the call sites and fails on an icon nobody renders, so the set cannot grow past
  what is used (VII.3).

## 237. Three widths, and dense tables scroll with the first column frozen (v0.15.3)

- **Decision**: breakpoints at **1100 px** (below it the sidebar is a horizontal strip) and
  **720 px** (below it single-column, cards for the widest tables). Dense tables get horizontal
  scroll inside their own container with the first column sticky. Column-dropping and a card layout
  for *every* table were both rejected.
- **Reason**: a table that drops columns silently is worse than one that scrolls, because the
  operator cannot tell a missing column from an empty one; and a card layout applied to all of them
  destroys the vertical scan that is the whole reason these tables are dense. The frozen first
  column keeps the row's identity — device, or id — beside whatever the operator scrolled to.
- **Measured**: at 820 px the shipped console rendered **byte-identically to 1440 px** on every
  view of every role, because `max-width: 760px` was the only width the stylesheet reasoned about.
  Thirty-seven elements were outside the viewport at 390 px across three views (F80).

## 238. Two-factor and recovery are declarations with a release named, and no mechanism (v0.15.3)

- **Decision**: the sign-in card and the account screen carry a short, permanently visible
  statement that two-factor authentication is not available, that **v0.17.0** brings it, and that it
  will be **required for admins**. Recovery says what an operator does today: restart the appliance
  and read the new password from its log (#234). No TOTP, no secret, no email, no schema, no
  disabled control.
- **Reason**: v0.13.0 refused *"coming soon"* panels and the detail panel then spent seventeen
  screens holding a placeholder for a feature one view used (#219). A sentence that is true is not a
  placeholder; a greyed-out button promising a mechanism that does not exist is. Naming the release
  is what makes it a commitment rather than an apology.
- **Measured**: the statement is two lines on two screens and reserves **no region on the other
  fifteen**, which is the specific failure #219 recorded.

## 239. `views/` holds views; the four modules in it that are not views move to `views/parts/` (v0.15.3)

- **Decision**: move `facts.js`, `retention.js`, `model.js` and `verdict.js` into
  `ui/app/views/parts/`. **Nothing else moves.** The other fifteen modules under `ui/app/` stay one
  level deep.
- **Reason**: derived from the import graph, not from a preference for folders. `registry.js`
  imports seventeen modules from `views/` and the directory holds twenty-one; the other four are
  imported by *sibling views* (`settings.js` takes `facts` and `retention`, `scorer.js` takes
  `model`, `promotion.js` takes `verdict`) and are components, not screens. That is the one thing
  the directory currently says that is false.
- **Measured**: in-degree over the whole console. The fifteen top-level modules already separate
  cleanly by it — `dom` 27, `widgets` 23, `format` 21, `api` 19, `session` 14 are the foundation;
  `destructive` 8, `store` 6, `parameters` 4 the next tier; `shell`, `sidebar`, `login`,
  `registry`, `router`, `theme`, `vendor` are the frame at 1–3 — and **a directory per tier would
  move 15 files, rename 15 static routes and rewrite 37 import statements to encode a fact the
  graph already states.** Rejected on that measurement (VII, "do not distribute files into pretty
  folders").

## 240. The password policy is served, not restated in JavaScript (v0.15.3)

- **Decision**: `auth.password_policy()` returns `{min, max, rule}` from the same constants
  `validate_password` reads, and it travels on `/api/me` **and** on the login route's
  `must_change_password` response. The console renders nothing at all until the server has said.
  No new route.
- **Reason**: V.2 asks for an indicator that reflects the policy the server actually enforces, and
  a `12` written into `password.js` would be a second source of truth about what a valid password
  is — the day `MIN_PASSWORD` moved, the sign-in card would confidently show the old bound. It goes
  on the login response as well because the forced first change happens **before a session exists**,
  so `/api/me` is not reachable at the one moment the console needs the numbers. Adding a route for
  it was rejected: the client already makes this round trip, and VII.5 asks for a reason the
  alternative was worse rather than a third endpoint.
- **Measured**: `rule` is `"length"`, and that is the honest part — this project follows NIST
  SP 800-63B and has **no composition requirement at all**. A meter asking for a digit and a symbol
  would invent a rule the appliance does not enforce, which is the same defect as showing the wrong
  minimum with the sign reversed. The served surface is unchanged at 44 `/api` pairs; two response
  bodies changed shape and the behaviour record is where that is pinned.

## 241. `GET /api/users` says which account is the sole admin; the console never works it out (v0.15.3)

- **Decision**: each row carries `sole_admin`. The console reads it and renders a locked role and a
  reason; it does not filter the list for `role === "admin"`.
- **Reason**: the first version of the console change did filter, and
  `test_security_ui.py::test_no_module_re_derives_permissions_from_role_rank` refused it. **The
  guard was right and the code was wrong** — a console working out from a role something the
  appliance already knows is F28's shape, and the fact that this particular derivation was only
  cosmetic is exactly the argument that turns an absolute into a judgement call on every later
  diff. The predicate now exists once, on the server, beside the refusal that enforces it.
- **Measured**: one call site, one field, and the console module contains no comparison of a role
  to a literal. The guard stays an absolute with no carve-out.

## 242. `administration.py` — who may administer this appliance, in one module (v0.15.3)

- **Decision**: split `crosscutting/administration.py` out of `auth.py`: the bootstrap, the recovery
  naming, and `would_remove_last_admin`. Not re-exported from `auth` — call sites import the concern
  they are using. `auth.py` keeps scrypt, the policy, the throttle, `Principal`, sessions, tokens
  and the login flow.
- **Reason**: the size guard fired at **409 lines** against a 400 ceiling, and `DEBT_ALLOWLIST` is
  empty and stays empty (prime directive 10) — so the choice was to split or to argue for an
  exemption, and there was no invariant to cite for one. The seam is the concept this release
  created rather than an arbitrary cut: the bootstrap and the invariant are each other's
  counterweight (one makes the lost state unreachable through the product, the other makes it
  survivable when something outside the product creates it), and reading either alone leaves the
  reasoning half-stated. A re-export in the `rbac/` style was rejected because the dependency runs
  the other way — `administration` needs `auth.hash_password` — so it would be an import cycle.
- **Measured**: `auth.py` 409 → 275 body lines, `administration.py` 118. The move is 9 files' import
  lines and no behaviour: the record's only auth diffs are the two response shapes #240 and #241
  name. `BOOTSTRAP_PASSWORD_CHARS` moved with it, because its name says which module owns it.

## 243. A 1.2 type scale from a 13 px body, and a 4 px spacing grid (v0.15.3)

- **Decision**: five type steps at 11/13/16/19/23 px plus a shared 13 px body, and spacing at
  4/8/12/16/24/32. 13 px stays the body size; what changed is the relationships between the sizes,
  not the density.
- **Reason**: the previous five were each chosen for one thing and their ratios were 1.09, 1.08,
  1.23 and 1.31 — a caption one pixel from the body it labels, and a heading three ratios away from
  the text under it. Spacing had the same problem in the other direction: 4, 8, 12, **18, 28** put
  two of five steps off the 4 px grid, so components at two removes never lined up. Zabbix density
  is about how much fits on a screen, not about how arbitrary the steps are.
- **Measured**: new ratios 11/13 = 0.85, 13/16 = 1.23, 16/19 = 1.19, 19/23 = 1.21 — one scale
  rather than four accidents. `style.css` goes from 41 custom properties to 43 (the brief's figure
  of 30 was not reproducible). `--tap: 28px` is new and is the pointer floor F81 measured against:
  the theme button was 29x23 and the situation permalink 18x17. **Not** the 44 px mobile figure —
  this is a dense operations console and 44 px rows would halve what fits on a screen.

## 244. The prose that left the console is the prose that was documentation (v0.15.3)

- **Decision**: delete the paragraphs that were explaining the project to itself, keep the ones an
  operator acts on. The largest single removal is the Overview's *"Reports that are not on any
  screen"* panel — a five-row table of shell commands on the first screen an operator opens.
- **Reason**: `docs/operate.md` lines 174–179 already list all five commands, and the Makefile
  documents each at length, so this was a third copy on a dashboard; it is **deleted rather than
  relocated**, which VII.6 asks for a reason for and that is the reason. What stayed is what the
  design doc defends: an empty state that says what will fill the screen, the graph's statement
  that it is not keyboard-operable, and *"a rate with no window is a number nobody can act on"*.
  A release that deleted all of it would be worse than the current state.
- **Measured**: **2 275 → 2 004 words** of rendered English, −12 %, by the method in the commit
  message rather than an unstated one. `overview.js` 310 → 216, `promotion.js` 201 → 157,
  `facts.js` 186 → 137. The v0.15.2 figure did not reproduce because its method was never stated;
  this one is a script, and the first version of it **under-counted** — it matched `hint="…"` but
  not the `hint=${"…" + "…"}` form this codebase also uses, so rewriting a concatenated hint as a
  plain one made the count rise while the prose fell. Both baseline and result above are measured
  with the corrected version, on both trees.

## 245. "Why these were grouped" answers the storm question first (v0.15.3)

- **Decision**: a summary computed from **every** link — the weakest link and its margin over the
  threshold, the strongest, the count, and which of the three named terms is carrying the grouping
  — with the per-link decomposition behind one interaction and **complete** when opened. The
  section moves to `views/parts/why.js`; `situations.js` was 356 body lines against a 400 guard.
- **Reason**: it rendered `links.slice(0, 30)`. In a four-alarm situation that is the right screen;
  in a 400-trap storm it is thirty rows chosen by insertion order, and an operator asking *"can I
  trust this grouping?"* had to answer it from a sample nobody selected. The margin is the number
  that answers it and it was not on the screen: a grouping whose weakest pair cleared by 0.01 is
  one scorer nudge from falling apart, and one that cleared by 0.3 is not. **No verdict is
  computed** — every figure is min, max or a mean over numbers the server already sent, because a
  console that scored its own groupings would be a second scorer.
- **Measured**: the completeness assertion is red under injection at **30 of 32 links** and green
  after — but only after the fixture was grown. Driven against the corpus's own **16** links it
  passed *with the thirty-row cap deliberately reinstated*, because 16 < 30 made the slice a no-op:
  a guard that could not fail for the defect it names, found by injecting the defect rather than by
  reading the test.

## 246. The timeline's y axis had 30 px for a device name (F83, v0.15.3)

- **Decision**: `PAD_LEFT = 82` as its own constant, and the tick text clipped to what it holds;
  the full name stays in the table below, which the caption already points at.
- **Reason**: `PAD = 30` was used for all four sides, and `d3.axisLeft` draws its labels to the
  *left* of the axis. A pad alone would not have been enough either — a device label is operator
  text of any length — so the pad fits an address and the label is clipped to it.
- **Measured**: `127.0.0.2` rendered at **x = −9** at every width, six elements outside the
  viewport at 390x844 with `document.scrollWidth == clientWidth`, so nothing scrolled and the axis
  was unreadable. Zero after. **The second defect this release found on a d3 screen that no
  assertion executes**, after v0.15.2's three — the pattern is now four for four, and both times a
  browser is what saw it.

## 247. The situation detail carries the threshold its links had to clear (F84, v0.15.3)

- **Decision**: `GET /api/situations/{sid}` returns `threshold`, read from the scorer configuration
  the situation names in `scorer_config_id` — **not** from the active one — and `None` when that
  row is gone. No new route; the same additive shape as #240 and #241.
- **Reason**: the console has been passing `detail.threshold` to "Why these were grouped" since
  v0.13.0 and the route has never sent it, so the sentence *"every pair scored above the link
  threshold of X"* has always printed without the X. A score with nothing to compare it against is
  not a decomposition that can be checked, which is the whole of principle 2 — and the margin over
  the threshold is the number #245's summary is built on, so the redesign would have shipped with
  its best figure permanently absent. Reading the situation's OWN configuration rather than the
  active one matters because an admin may have retuned or rolled back since: the threshold this
  grouping cleared is a fact about when it was decided.
- **Measured**: `curl /api/situations/1` on a live appliance returned nineteen keys and no
  `threshold`; the console rendered "the threshold was not reported". Found by looking at the
  screen, not by reading the route — the old copy degraded to a grammatical sentence with the
  number missing, so nothing looked wrong.

## 248. Docker's "Enable Watch" is answered in documentation, not with four lines of compose (v0.15.3)

- **Decision**: no `develop:`/`watch:` section. `docker-compose.yml`'s header says why the button
  does nothing and what to do instead — run the process directly, because there is no build step.
- **Reason**: the finding is confirmed exactly as the brief stated it — 82 lines, no `develop:`,
  no `watch:`, so the prompt is Docker Desktop's generic offer rather than a project feature. Given
  the choice the brief offers, documentation wins on the merits and not on effort: this file is the
  **hardened production recipe** (`read_only: true`, every capability dropped, no privilege
  escalation), and `watch` is a development loop that syncs source into a running container —
  which a read-only root filesystem exists to prevent. Adding it would mean either a second compose
  file or relaxing the hardening in the one an operator deploys.
- **Measured**: the console is static ES modules served from disk and the package installs with
  `pip install -e`, so the edit loop is already *edit, reload* with **nothing to compile and no
  container to rebuild**. `watch` would add machinery to reach a state the project is already in.

## 249. v0.16.0 is the situation lifecycle; the cartridge and archetypes each slip one (v0.15.3)

- **Decision**: the release table becomes `v0.16.0 = situation-lifecycle`,
  `v0.16.1 = visualisation-search`, `v0.17.0 = external-cartridge`, `v0.18.0 = archetypes`. The two
  briefs that claimed the old numbers are re-tagged in the same commit, and the table's size guard
  moves with them.
- **Reason**: the table said v0.16.0 was the external cartridge and the maintainer's v0.15.3 brief
  states, in its own release table, that v0.16.0 is the situation lifecycle and v0.16.1 is
  visualisation and search. **The contradiction was found by the guard**, not by reading: writing
  `docs/plans/v0.16.0-situation-lifecycle.md` — which Part IX of the brief requires — made
  `test_every_release_claim_agrees_with_the_roadmap_table` go red immediately, which is the fourth
  time that guard has caught a resequencing (#202 was the third). `cartridge.md` already argues it
  should slip, so slipping it is the documented direction rather than a new opinion.
- **Measured**: thirteen rows to fifteen. The resequencing changes no code and no schedule anyone
  had committed to — the table's own §"What this document does not decide" says none of these
  releases has a date.

## 250. Two-factor is a roadmap item, and the console names the roadmap rather than a version (v0.15.3)

- **Decision**: `docs/ROADMAP.md` gains 2FA as an unsequenced item; the sign-in card and the account
  screen say it is not available and that it will be **required for admins**, without naming a
  release. This **amends #238**, which named v0.17.0.
- **Reason**: #238 argued that naming a release makes a declaration a commitment rather than an
  apology, and that is right — but the release it named was `archetypes` in the table and became
  `external-cartridge` under #249, so the console would have promised a second factor in a release
  about ONNX. A version number nothing schedules is not a commitment; it is the empty placeholder
  (#219) wearing a date. The roadmap is where this project keeps unsequenced commitments, and the
  entry there carries the constraint the release will have to satisfy.
- **Measured**: the roadmap entry names F79's shape as the design constraint — a second factor an
  admin can lose is a second way to lock the appliance out of itself, so enrolment and recovery are
  one question. The console's two declarations are unchanged in every other respect.

## 251. One recursive package-data glob, and a guard that builds the artefact (F85, v0.15.4)

- **Decision**: `package-data` becomes `ui/**/*` plus `ui/.well-known/*`, replacing six per-level
  globs. Package-data patterns are expanded by one function, `_covered_by`, which uses `glob`
  rather than `fnmatch`; both the coverage test and the guard on that test call it. A new test
  builds a wheel from a context **derived from the Dockerfile's own `COPY` lines** and stripped as
  `.dockerignore` strips it, and asserts every `STATIC_ASSETS` entry — and every UI file on disk —
  is inside.
- **Reason**: the per-level list is a rule that has to be re-obeyed every time the console grows a
  directory, and v0.15.3 grew one and did not obey it — which is the same shape as F12, the defect
  the list was introduced to fix. `**` cannot miss a level, so adding a directory stops being a
  packaging decision. The second glob stays because `**` does not match a path component beginning
  with a dot, and that was found by building a wheel and looking for `security.txt`, not by
  reading the pattern.
- **Measured**: with the per-level globs restored, the coverage guard and the artefact guard go red
  naming exactly the five files, and the artefact guard quotes the contents of a real wheel; green
  after. Reverting the matcher to `fnmatch` turns the coverage guard **and** its guard red — before
  this release the latter restated a property of the standard library and would have stayed green
  through exactly the state that shipped F85.
- **What the first draft of this decision got wrong.** It named `src/netcorenoc.egg-info/` as *the*
  masking mechanism. A 2x2 over the two files that can complete a wheel from `SOURCES.txt` shows
  **either one alone is sufficient**, and the one that matters is `MANIFEST.in`: its `graft src` is
  present in a clean clone with no editable install, so CI's wheels were complete too. The
  Dockerfile does not copy it. `MANIFEST.in` keeps `graft src` — an sdist that cannot rebuild the
  wheel is the worse defect — so the guard is what must build without it, and it now asserts the
  absence of both files rather than merely arranging it.
- `setuptools` moves into the dev extra because the artefact guard builds with `--no-isolation`, so
  it needs no network; **runtime dependencies are unchanged at five**.

## 252. The theme control is a toggle, and a card styles only its own controls (F86, F87, F88, v0.15.5)

- **Decision**: three repairs to the console, all of them where what the source said and what the
  browser did had come apart.
  1. `.login-card` styles its **direct children** (`>`), not every descendant element.
  2. The theme control returns whichever appearance the operator is not looking at, and holds its
     own state in a class component. `forceRepaint()` and its `data-theme-tick` attribute are
     deleted.
  3. `spellcheck` is passed as the boolean `false`, not the string `"false"`.
- **Reason**:
  - **F86.** `.login-card button { width: 100% }` was true of the card that existed when it was
    written. A composed `PasswordInput` later put a second button inside, and `width: 100%` on a
    `flex: none` item made it claim the row and refuse to shrink — the password field rendered
    18 px wide. A selector that acquires new subjects as the tree grows is F85's shape in CSS;
    `>` states the rule's actual meaning and keeps stating it.
  - **F87.** Three states over two appearances guarantee a dead click, whatever the ordering — so
    the ring becomes a toggle. `system` stays the default an absent cookie means and keeps its
    icon until the first click, but stops being a stop on the ring. **This is a deliberate loss:**
    the operator can no longer return to "follow the system" from the control, and doing that
    properly needs a menu, which is a design decision and not a bug fix. Separately, the control
    was rendering from a cookie the framework cannot observe, with a repaint helper that passed
    `setConnection` its own current value — a setter that returns early on an unchanged value. It
    published nothing, and the label was frozen through every click.
  - **F88.** `spellcheck` is an IDL boolean; the truthy string `"false"` set it **true**, so the
    appliance asked the browser to spell-check a password.
- **Measured** in Chromium against a real appliance, before and after: the password field 18 px →
  298 px at 1440 and 276 px at 390, the reveal button 330 px → 28 px, vertical misalignment 8 px →
  0, `spellcheck` `"true"` → `"false"`; six theme clicks with one dead one and a frozen label →
  eight clicks across both OS preferences, every one of them changing the appearance and the label
  naming the state. Focus order is unchanged — username → password field → reveal → submit — which
  is why the icon moves left by `order` and not by reordering the markup.
- **Guarded**, each demonstrated red under injection with a passing control:
  `test_no_card_styles_a_bare_element_it_does_not_own`,
  `test_every_click_of_the_theme_control_changes_what_is_on_the_screen` (which asserts on the
  per-click **trail**, because an endpoint assertion can see neither defect — the existing theme
  test read only the endpoint and saw neither), and
  `test_no_enumerated_dom_attribute_is_passed_as_the_string_false`.
- No new dependency, no migration, no server change: `src/netcorenoc/` moves only under `ui/`.

## 253. An unattributable historical `closed` becomes `resolved` / `unattributed` (v0.16.0)

- **Decision**: `0014` maps `status='open'` → `open`; `status='merged'` → `resolved` with
  `resolution='merged'`; `status='closed'` → `resolved` with **`resolution='unattributed'`**, a
  value that means *"this situation left before v0.16.0 and nothing recorded why"*.
- **Reason**: today `closed` conflates operator-closed and idle-swept, and no column distinguishes
  them. Writing `idle` would be a **guess about content** where `0008`'s one permitted data write is
  explicitly a **marker about provenance** — the same distinction that made `legacy_capture` a
  marker rather than a verdict on a label's quality. `merged` is different in kind: it is knowable
  exactly, because `merge_situations` itself wrote it.
- **Trade-off**: a sixth `resolution` value that every reader must handle, forever, for rows nobody
  will write again. Accepted, because an operator auditing two months later must be able to tell
  *"nobody looked at it"* from *"we do not know"*, and a value that says "unknown" is the only
  honest way to say so.
- **Correction to the brief**: Part IV.3 asks for *"a fifth value meaning 'before this release,
  unknown'"* while Part V.1 already registers five (`operator | self_cleared | idle | merged |
  manual_clear`). It is the **sixth**. The count is the brief's; the decision is unaffected.
- **Measured** (Phase 1, on a populated v0.15.5 database driven through the eval corpus): the
  counts of each `status` before the migration and of each `(status, resolution)` after it are
  recorded by `test_upgrade.py::test_v0155_upgrade_applies_0014_and_attributes_nothing_it_cannot`,
  which asserts the two agree row for row and that no row gained a `resolution` the old schema
  could have justified.

## 254. The correlator creates `new`; the first operator gesture makes it `open` (v0.16.0)

- **Decision**: `create_situation` writes `status='new'`. A situation becomes `open` on the **first
  operator gesture that names it** — a verdict, a close that carries one, a move, a merge, an
  operator split, a manual clear or a rename. No timer, no member count, no age.
- **Reason**: `new` means *"nobody has looked at this"* and `open` means *"an operator is working
  it"*, which is what the three tabs are for. Every alternative criterion is a threshold — five
  members, ten minutes — that nothing in this repository has measured, and a threshold chosen to
  look reasonable is the placeholder rule (#219) wearing a number.
- **Trade-off**: on a busy appliance almost every situation is `new` and the `Open` tab is small.
  That is the true statement about a NOC that has not triaged, and a tab that filled itself would
  be reporting the passage of time as operator attention.
- **Consequence, declared**: `stats.open_situations` now counts `status IN ('new','open')` — the
  live population, which is what the number has always meant. Counting `open` alone would have
  reported zero on a working appliance the moment this release shipped. Every other reader of
  `status='open'` (the idle sweep, engine-state reload, scope resolution) is widened the same way
  and for the same reason.
- **Measured** (Phase 2): the eval corpus forms 41 situations under the census's replay, and the
  count of each `status` after the replay, after one verdict, and after the idle sweep is asserted
  by `test_store.py::test_the_three_states_are_each_reachable_and_each_reached_once`.

## 255. Four routes, one per gesture, rather than one overloaded restructure route (v0.16.0)

- **Decision**: `POST /api/situations/{sid}/move`, `POST /api/situations/{sid}/merge`,
  `POST /api/situations/{sid}/split`, `POST /api/alarms/{aid}/clear`. A self-clear is not an
  operator gesture and gets no route.
- **Reason**: the alternative — one `POST …/{sid}/restructure` carrying an `operation` field —
  needs one declaration, one capability and one `ROUTE_SCOPE` entry, and that is exactly its
  defect. These four differ in **who may do them** (#256), in **what they assert**
  (`PREREGISTRATION-0.16.0.md` §2 gives each its own row), in **which objects they name** (move and
  merge name *two* situations, so the scope decision is two `situation_in_scope` calls, not one),
  and in **what they audit**. One route would give one answer to four different questions and the
  authorization table would say `situation.restructure` where four powers live.
- **Trade-off**: four route declarations, four capabilities, four audit actions and four
  behaviour-record entries instead of one. Accepted: every one of those is a line a reviewer can
  read, and the overloaded route's saving is exactly the review it avoids.
- **`/api/alarms/{aid}/clear` is not under `/api/situations`** because a zombie clear is a fact
  about an **alarm**, not about a grouping — the distinction `PREREGISTRATION-0.16.0.md` §1 turns
  into a prohibition. Putting it under a situation would put the alarm-lifecycle gesture in the
  correlation namespace, which is the misreading the plan exists to prevent, expressed as a URL.

## 256. Each restructuring gesture gets its own capability (v0.16.0)

- **Decision**: four new capabilities, all `editor`: `situation.move`, `situation.merge`,
  `situation.split`, `alarm.clear`. **30 → 34.**
- **Reason**: `feedback.write` is the power to *record an opinion*; these are the power to
  *restructure the record*. A merge mutates `situation_alarm` and changes what every later label
  refers to; an editor who may say "this grouping is wrong" is not obviously an editor who may
  rewrite it. Part VIII resolves the ambiguity to *"it does"*, and `resolve_capabilities` is
  `ceiling ∩ policy`, so four capabilities let a deployment grant labelling without restructuring —
  a configuration one capability makes unreachable.
- **Trade-off**: four rows in three tables rather than one, and an admin has more to read. Accepted
  for the reason `situation.close` was already distinct from `feedback.write` (#126's neighbour):
  the split is what makes least privilege expressible at all.
- **Measured** (Phase 3): the authorization matrix's capability and route counts before and after
  are asserted by `test_rbac.py`, whose fail-closed sweep already covers every registered route as
  each of the four principals — so the four new routes are covered by construction rather than by a
  test somebody remembered to add.

## 257. The derived name is a projection of MEMBERSHIP alone, refreshed where membership changes
     (v0.16.0)

- **Decision**: `situation.derived_name` is stored, and it is written by the same statement group
  that changes `situation_alarm` — `create_situation`, `add_alarm_to_situation`, `merge_situations`
  and the three restructuring operations, and nowhere else. Its value is a function of the member
  count and the **distinct device addresses** of the members, and of nothing else:

  ```
  0 members                         -> "(no members)"
  1 device,  1 member               -> "10.0.0.1"
  1 device,  n > 1                  -> "Storm -> 10.0.0.1"
  2 devices, 2 members              -> "10.0.0.1 <-> 10.0.0.2"
  otherwise                         -> "Storm -> 10.0.0.1 and 3 more"
  ```
- **Reason**: *"a stored name that goes stale is worse than none"*, so staleness has to be
  structurally impossible rather than promised. Deriving on read would satisfy that too, but the
  column is required (Part V.1) and a name that survives its members being pruned is the reason —
  a resolved situation keeps its name after retention has collected the alarms it was derived from,
  and at that point it can no longer be recomputed, which is `0008`'s rule 1 exactly.
- **Why not the root alarm.** `set_root` runs on **every activation** and the root moves, so a name
  that named the root would either go stale or cost a rewrite per trap. Membership is the coarser
  input and it is the one that changes rarely.
- **Why the address and never an operator label.** A device label is free text an operator typed;
  putting it inside a server-derived name would put operator-influenced text into a field the
  console renders beside an operator's *own* name, and the two would be indistinguishable. The
  address is validated at ingest.
- **Trade-off**: one aggregate query per membership change, on the batch lock. Its cost is
  **measured in Phase 2** against the two largest corpus scenarios and against the 100 000-trap
  burst guard, and reported with the release; a name that cost the ingest path more than the
  budget allows would have to become a read-time derivation instead.
- **`operator_name` is a separate column and is never written by the server.** A model does not
  propose one in this release (Part I.2), and the guard that says so is
  `test_no_model_output_reaches_operator_name`.

## 258. A card that self-cleared while held shows the change, does not apply it, and records the
     verdict against the membership the operator saw (v0.16.0)

- **Decision**: the answer Part IV.3 §6 settles, implemented by **reuse and not by a second
  mechanism**: the held card keeps its frozen payload (#173), a badge says the situation resolved
  underneath it, and any verdict it sends is recorded against `feedback_member` with
  `source='server'` taken at the instant of the gesture — F46's repair, F48's demonstration,
  unchanged.
- **Reason**: the alternative is a second reconciliation path for "the state changed while you were
  deciding", and two paths that must agree about what the operator saw are two chances to disagree.
  `record_label` already writes the server's own bag at verdict time; `situation_event` writes the
  same ordered, positional, server-authoritative shape for the gestures that have no `feedback`
  row. There is one snapshot rule in the tree and it now has two writers, not two rules.
- **Trade-off**: the operator can act on a card that is up to one poll interval behind, and the
  badge is the whole of the mitigation. That is the trade #173 already accepted; nothing here makes
  it worse, and the new gestures fail loudly rather than silently when the situation has resolved —
  a move out of a resolved situation is a 409, not a silent no-op.

## 259. `engine.py` is byte-identical; the close derives its own reason (v0.16.0)

- **Decision**: `engine/operate/engine.py` is not edited by this release. `TRAP_PATH_HASHES` and
  `TRAP_PATH_BODY_HASHES` are unchanged. Everything the state machine needs on the engine side is
  reached without touching it:
  - `store.create_situation` writes `status='new'` (the engine calls it unchanged);
  - `store.close_situation` **derives** `self_cleared` or `idle` from whether the bag still holds an
    active member, instead of taking the reason as a parameter;
  - the in-memory membership fix-ups the four operator gestures need are free functions in
    `engine/operate/membership.py` taking the engine, not a new mixin on it;
  - `new` -> `open` promotion happens in the routes, which already hold the store.
- **Reason**: the brief's Part V.6 names five modules the trap path must keep byte-identical and
  `engine.py` is not among them — but `TRAP_PATH_BODY_HASHES` names five and `engine.py` **is**, in
  place of `scoring.py`. The two lists disagree. Holding the stricter reading satisfies both, and it
  buys the strongest claim this release can make: a release that added a state machine, an append-only
  event log, five operator operations and four capabilities changed **no byte** of the file that
  carries *"ingestion is sacred"*.
- **Why deriving is not a compromise.** `_close_situation` calls the store on both paths with
  `(sid, ts)`, and what tells the two apart — whether any member is still active — is in the database
  at the instant of the call, which is the only instant at which it is still true. Passing it in
  would have made the ingest path *say* what it already *demonstrates*.
- **Trade-off accepted**: an empty bag would answer `all_cleared` True, so the derivation guards on
  the member count and an empty situation resolves as `idle`. That is Appendix B's *"an invariant
  that cannot fail"* met head-on: `SUM(status='active') = 0` is true of every empty set, and the
  guard is what stops it meaning *"the network fixed itself"* about a situation with nothing in it.
- **Honest limit**: a situation the idle sweep reaches whose members all happen to be cleared is
  recorded `self_cleared`. It is the right answer — the alarms did clear — and it is reachable only
  when a clear did not travel through `_handle_clear`.

## 260. A rename is a label, so it reuses `label.write` (v0.16.0)

- **Decision**: `POST /api/situations/{sid}/name` is a **fifth route** and does **not** add a fifth
  capability: it requires `label.write`, the capability that already governs naming a device and
  naming an alarm class. Supersedes nothing in #255 and #256; it extends the route count from four
  to five and leaves the capability count at 34.
- **Reason**: naming a device, naming an alarm class and naming a situation are one power — an
  operator who may put a word on a network element may put one on an incident. Part VIII's rule that
  ambiguity about whether a gesture needs its own capability resolves to *"it does"* is about
  distinct **powers**, and a fifth capability here would express a distinction nobody would ever
  configure. The three restructuring capabilities exist because a merge changes what every later
  label refers to; a name changes nothing but a heading.
- **Why not extend `POST /api/labels` with `kind: "situation"`.** That was the closer alternative
  and it was rejected on storage and on scope: `label` is keyed `(kind, target_id)` and the name
  belongs in `situation.operator_name`, beside `derived_name`, which is where the two-column
  distinction lives; and the scope decision for a situation is `situation_in_scope` — a membership
  test — rather than `scope.allows_ne`. One route with two storage backends and two scope rules is
  the overloaded route #255 refuses, one level down.
- **Trade-off**: an operator granted `label.write` for device names also gains the power to name
  situations, and a deployment cannot separate them. Accepted: the alternative is a capability whose
  only purpose is to be separable.

## 261. A bag's identity is its member SET; its order is the record (v0.16.1)

- **Decision**: `feedback` is keyed `(situation_id, verdict, bag_key)`, where `bag_key` is a
  SHA-256 over the situation's member ids **sorted and deduplicated** at the instant of the label.
  `member_digest` — the digest over the *ordered* bag — is unchanged and keeps its own meaning.
  Registered in `PREREGISTRATION-0.16.1.md` §2 before any code was written; migration `0015`
  implements it. Widens #68's key rather than replacing it.
- **Reason**: F89 lost the second correction of one situation, and the second correction asserts
  about a bag the first one changed. Order is what the operator *saw* and belongs to the record;
  the same alarms in a different order are the same *grouping*, so a key over the ordered digest
  would let a correlator that merely re-ordered a bag manufacture a second assertion out of one
  human decision — F36's defect in this release's clothes.
- **Trade-off accepted**: the cap on one situation's influence on learned state moves from **two
  applications** to **one per verdict per distinct membership**. Still bounded and still monotone
  in operator acts — each increment needs a membership change *and* a further POST — but no longer
  a constant. A release that finds it too loose bounds memberships; it does not drop the identity.
- **Measured**: two moves out of one situation recorded `feedback +1` then `feedback +0` before;
  `+1` then `+1` after. The control — a third post with the bag unchanged — still records `+0`.

## 262. The bag key is derived by the store, and a test is what makes that safe (v0.16.1)

- **Decision**: `store.add_feedback` computes `bag_key` itself from `situation_alarm` rather than
  taking it from the caller, so `engine/operate/engine.py` is **byte-identical** and its
  `COHESION_EXEMPT_CEILING` of 545 is untouched.
- **Reason**: the alternative was to read the bag in `apply_feedback` before the insert and pass
  the digest down, which costs two lines in the one file this project has agreed to keep readable
  in a single pass (#121, #108) and would have needed a ceiling raise. The identity of a bag is a
  fact about stored membership, which is this layer's own subject; the *observation* of a bag —
  order included — stays the engine's.
- **The compensating control, because a ratchet without one is a comment**: `feedback_member
  (source='server')` is written by the engine from `capture.server_bag`, which prefers **engine
  state** for a live situation, while the key comes from the store's own table. "They can never
  disagree" is therefore a claim, and `tests/test_bag_identity.py::test_every_labels_key_is_the_
  set_digest_of_its_own_recorded_snapshot` asserts it over the real HTTP write path on bags the
  correlator formed. If the two readings ever diverge, that test — not a production number — is
  what says so.
- **Trade-off**: one extra `SELECT` per verdict on the HTTP write path. Accepted: it is thousands
  of times rarer than the ingest path, and the alternative was inferring the schema from an
  exception on the same path (see #263).

## 263. A schema probe at `open()`, not an `OperationalError` per write (v0.16.1)

- **Decision**: `Store._has_bag_key` is answered once by `PRAGMA table_info(feedback)` after the
  migrations, exactly as `_has_lifecycle` is (#250). On a database frozen below `0015`,
  `add_feedback` issues the v0.16.0 statement byte-for-byte.
- **Reason**: `tests/test_upgrade.py` runs the **current** store against a frozen migration
  directory, which is what makes *"the migration changes behaviour and the code does not"*
  checkable at all. Without the probe that fixture does not fail — it **hangs**, because the
  `OperationalError` escapes into a started engine whose task nobody cancels, which is Appendix B's
  *"a red that was a hang"* and cost this release ten minutes of a `SIGKILL` bisect.
- **Trade-off**: one branch on the busiest write path in the API layer, and a second probe query at
  startup. Accepted for the reason #250 accepted the first: a caught exception per call infers the
  schema from a failure instead of asking once.
- **Measured**: six of twelve `test_upgrade.py` tests hung before the probe; twelve pass after it,
  and the six pinned literals moved from `latest_schema_version() == 14` to `== 15`, which is this
  release's only intended diff in that file.

## 264. The promotion path is derived from the import graph, bounded by one package (v0.16.1)

- **Decision**: `test_no_promotion_path_module_mentions_a_ground_truth_field` walks out from
  `api/routes/promotion.py` and scans every `engine/evaluation/` module the import graph reaches,
  instead of the four-name tuple it carried since v0.14.0. Four became seven (F92).
- **Reason**: a guard that lists what it checks stops checking whatever is added next. v0.14.0's
  list called itself *"the four modules the gate actually reads"* and `promotion_metrics.py` —
  which computes **all four** of the named quantities the gate reads — was not among them.
  Appendix B names this shape first, and v0.15.1 found three more instances of it.
- **Why the walk is bounded at `engine/evaluation/` rather than unbounded**: **measured** — the
  unrestricted closure from the entry point is 112 modules, four of which mention `entity_key` as
  a legitimate domain term (`store/entities.py` and three under `engine/operate/`). An unbounded
  guard could never be green, so it would be deleted or exempted, which is worse than a bounded
  one that is honest about its edge. The boundary is a named constant with that number beside it.
- **Trade-off accepted**: a promotion-path module placed **outside** `engine/evaluation/` still
  escapes this guard, exactly as it escaped the list. Its companion,
  `test_no_runtime_module_can_reach_the_simulator`, parses the whole tree and catches any *import*
  everywhere; what remains uncovered is a truth field copy-pasted into a module that imports
  nothing and lives elsewhere. Named here rather than left to be discovered a third time.

## 265. The situation card is a `views/parts/` module; the screen is the list (v0.16.1)

- **Decision**: `SituationCard`, `Detail` and `situationName` move from `views/situations.js` to
  `views/parts/card.js`. The screen keeps the list, the three tabs and the search box.
- **Reason**: the server-side search took `situations.js` to 411 lines and 20 224 bytes, over the
  400-line rule *and* over the module-graph guard's ceiling (a third of the 52 738-byte file
  v0.13.0 replaced). The honest repair to a module that is over budget is not to write less prose
  in it — it is to notice it had been two things for a while. The seam is the one the file already
  had: one half **finds** a situation, the other **judges** one. `views/parts/members.js` was cut
  out of the same file for the same reason a release earlier (#239's seam, applied again).
- **Trade-off**: one more static asset — the allowlist, the route-order baseline, the hash table,
  the size table and four lines of the behaviour record all move with it. Accepted: that ripple is
  the cost of the guard being real, and it is the same ripple `members.js` paid.
- **Measured**: 411 lines / 20 224 bytes → 213 / 10 465 and 234 / 11 473. Both halves under both
  ceilings, and no module in the console is now the old single file renamed.

## 266. The search is a parameter on the route that already lists situations (v0.16.1)

- **Decision**: `GET /api/situations?q=` rather than a `/api/search`. The match is a **query**
  filter in `store._search_clause`, over the operator's name, the derived name, the device
  address, the device label, the trap OID, the class name and the alarm instance.
- **Reason**: the answer is a list of situations, scoped and shaped by rules this handler already
  applies. A second route would have had to restate the scope predicate, the redaction, the
  `LIMIT`-after-filter rule and the shaping pass — four chances to disagree with the listing about
  what a principal may see. A client-side filter was the other alternative and cannot work: the
  device, the OID and the instance are on the **detail** payload, and what is not loaded cannot be
  searched however clever the predicate.
- **A field is matched only where the requester would be shown it**, and the gate is derived:
  `shaping.sees_raw_addresses` reads `FIELD_RULES["ip"]`, so below editor the raw address and the
  derived name are not in the predicate at all. Matching them would let a viewer confirm by typing
  what the console coarsens on the way out — the shaping axis undone through a text box, which is
  a different failure from the scope one and needs its own guard.
- **Trade-off accepted**: a viewer cannot search by address, and there is no message on screen
  saying why. Stated in the empty state's *"only the ones your account is shown"* rather than as a
  per-field explanation, because naming which fields were withheld would itself describe the
  policy to someone the policy applies to.
- **Measured**: the two refusals are demonstrated red with controls in `tests/test_search.py` —
  a scoped editor finding a hidden member's device, and a viewer finding a coarsened address —
  each beside a principal who legitimately finds the same situation.

## 267. The tab does not follow the card; the card stays while it is open (v0.16.1)

- **Decision**: a card the operator has gestured on remains in the list they are looking at until
  they collapse it, even after the gesture moves it out of that tab. The **tab does not change**,
  the badge still says `open`, and the pin lives in the screen's own state for the length of the
  visit — nowhere else, and never on the server.
- **Reason**: DECISIONS #254 makes the first gesture promote `new` → `open`, which is right and
  means the card leaves the default tab an untriaged appliance is full of. Measured in v0.16.0's
  live pass and recorded in that release's brief §5: *"the card an operator is working on
  disappears from the default tab"*. Moving the **tab** instead would be the same surprise wearing
  the other hat — the tab is what the operator chose.
- **Trade-off accepted**: for as long as the card is open, the list shows one row the tab's own
  predicate excludes. It is visibly `open`, it is the row the operator is reading, and collapsing
  releases both the pin and the held payload together — so the two notions of "I am working on
  this" have one lifetime rather than two.
- **Why not "let the card linger for N seconds"**: a timer is a second, invisible piece of state
  whose expiry an operator cannot predict, and the card would then vanish mid-sentence. Collapse
  is a thing they do.
- **Amended by its own live pass, before it shipped**: choosing a tab releases every pin too. The
  first implementation kept them, and Chromium showed a card pinned in "New" still on screen after
  a switch to "Open" — present in both lists at once. Picking a tab is the operator asking for a
  different list, which is not the act the pin is a courtesy about. A courtesy that outlives its
  reason is the second, private notion of state this entry refused to build.

## 268. The timeline gains two filters and no gesture (v0.16.1)

- **Decision**: `GET /api/timeline` takes `ne_id`, `since` and `until`, all applied in SQL. The
  screen gains **no gesture at all**, and that is the registered answer rather than an omission.
- **Reason (the gesture)**: `PREREGISTRATION-0.16.0.md` §1 extends the `incumbent_linked`
  prohibition to *any signal that is not an assertion about a grouping*, naming the alarm lifecycle
  explicitly. A raise and a clear are facts about an **alarm**. A "this is wrong" control on a
  timeline mark would be exactly the prohibited signal in a new shape, and Part VII rule 5's
  *"none, and here is why"* is the honest answer.
- **Reason (the key)**: the element filter names an element by `ne_id` — the same key the scope
  predicate uses — and **not** by the rendered `device` string the marks carry. Two elements can
  share a label, so filtering on the display string would be v0.7.0's defect asked for on purpose
  (F35, DECISIONS #67). That is why a mark now carries `ne_id`: the console cannot filter by a key
  it is not given, and an NE id discloses nothing new — `/api/entities` has served it to viewers
  since v0.5.0.
- **Trade-off**: the timeline response shape changes for the first time since v0.7.1, and
  `tests/test_upgrade.py`'s "three documented changes" became four. The count left that test's
  name in the same commit, which is F92's lesson applied to a test name.
- **Measured**: with a busy neighbour and `limit=1`, a query filter returns the asked-for element's
  marks and a render filter returns **nothing** — which reads on screen as *"this element is
  quiet"*. `tests/test_timeline.py` pins both directions.

## 269. `by user:2` becomes `by alice`, and `FIELD_RULES` decides who is told (v0.16.1)

- **Decision**: `situation_events` returns `actor_name` beside `actor` — the username if that
  account still exists, `NULL` if it does not or if the actor was a service token. `actor` is
  unchanged and remains the record. The name carries a `FIELD_RULES` entry at `editor`, so a viewer
  receives the reference.
- **Reason**: the reference is correct, unforgeable and useless to a human reading their own work
  (v0.16.1 brief §5). Resolving it raises two questions and both are answered rather than avoided:
  a **deleted account** falls back to the reference and never to an invented name — `0011`'s rule
  that unknown is not a value you may fill in, applied to a person — and **who may read it** is
  decided by the one module that owns field-level authorization, not by a condition in a query.
- **Why `editor` and not `admin`**: an editor is who makes gestures and reads their own history,
  which is the entire reason the name exists. `admin` would have resolved the question by deleting
  it.
- **Why dropped rather than coarsened**: half a username is not a weaker fact, it is a puzzle. The
  reference is already beside it and is a complete answer to *who*.
- **Trade-off accepted**: an editor learns the usernames of other operators who have acted on
  situations they can see. That is narrower than the roster (`GET /api/users` stays admin-only) and
  is the minimum an incident history has to disclose to be an incident history.

## 270. The graph's analytics were already on the wire (v0.16.1)

- **Decision**: "which elements alarm most" and "which relationships are strongest" are rendered
  from `active_alarms`, `weight` and `n` — three numbers `/api/graph` has served since v0.13.0 —
  computed in the client. **No route was added, and no charting library** (Part VII rules 1 and 2).
- **Reason**: v0.15.2 found `/api/stats` serving eleven keys while the console rendered five, and
  this is the same finding on a different route: the drawing encoded `active_alarms` as a radius
  that **saturates at 24 px** (F77) and `weight` as an opacity, so the screen answered both
  questions in quantities that cannot be read off. The numbers were being thrown away.
- **`n` is shown beside `weight` and that is not decoration**: a pair seen six times can already
  reach an affinity of 0.83 (F61, measured), so a table printing the score alone would present a
  claim from six observations and one from hundreds as the same fact.
- **The graph gains no new gesture kind.** An assertion that two *elements* are unrelated is not in
  `PREREGISTRATION-0.16.0.md` §2's registered map, and inventing one to satisfy decision 1 is what
  Part VII rule 5 forbids. What it gains is that its **existing** gesture — renaming a device,
  `label.write`, double-click only since v0.13.0 — is now a button in a table, so it is reachable
  from a keyboard for the first time on a screen whose own caption said it was not.
- **Measured**: the tables are ordinary DOM, so the harness executes them —
  `tests/test_ui_invariants.py` now asserts both, with a control on an empty graph. The drawing
  itself is still not covered and the file still says so.

## 271. Entities and alarm classes both survive, and one of them was missing its verb (v0.16.1)

- **Decision**: neither screen is deleted or merged (decision 4). `views/classes.js` gains the
  control its own caption has promised since v0.13.0; `views/entities.js` is unchanged.
- **Reason — entities**: the brief's §12 calls it a duplicate of the graph. It is the graph's
  **only** keyboard-operable form — the drawing says so itself — and it carries the varbind
  profiler, which is the product's explainability claim one level below correlation and appears
  nowhere else. Deleting it would delete the only accessible path to the network view.
- **Reason — alarm classes**: *"a list with no statistics"* was the charge, and the answer is not
  statistics. The screen said *"a label you set here is cosmetic"* and **there was no control to
  set one**: `POST /api/labels {kind: "class"}` was reachable from no screen in this console. A
  screen that describes an action it does not offer is worse than one that offers nothing.
- **No statistics were added**, and that is Part VII rule 4 rather than an oversight: nothing
  serves a per-class alarm count, and the choice was between inventing a route for a number nobody
  named a question for and leaving it. Recorded as an open question rather than built.
- **No new capability**: naming a class is `label.write`, the same power that renames a device and
  names a situation (DECISIONS #260), and the screen gates its own control with `can()`.

# v0.16.2 — the critical repairs

## 272. The release chain lives in the table, and five rows join it without one moving (v0.16.2)

- **Decision**: `docs/plans/releases.md` gains rows for v0.16.2 through v0.16.6 and no existing row
  moves. The five are described there in one line each plus a dependency argument; none gets a
  brief, and the detail of an unbuilt release stays out of this document exactly as that document's
  own §"What this document does not decide" requires.
- **Reason**: the maintainer planned five releases and they existed only in a prompt. A chain whose
  next five links live outside the repository is a chain `tests/test_documentation.py` cannot check,
  and the guard it enforces — one answer to *"what is release X"* — is worth nothing over releases
  the table does not name.
- **Why an extension rather than a resequence**: the four previous edits to that table each moved a
  release already in it (#170, #184, #202, #249). This one adds releases that were never in it, so
  no claim key changes and no shipped row is touched. `test_the_release_table_parses` records the
  difference in the same comment that records the four permutations.
- **Trade-off accepted**: the table is now twenty rows and reads as a schedule rather than a chain.
  The alternative — five brief documents for five unbuilt releases — is the pile #197 deleted 57 000
  lines to prevent, and a brief for a release nobody has started is a draft that goes stale before it
  is read.

## 273. Six of the seven promotions survive; the move's DESTINATION is the one that goes (v0.16.2)

- **Decision** (`PREREGISTRATION-0.16.2.md` §2.2, decision 1): `store.promote_situation` keeps six of
  its seven call sites — the verdict, the move's **subject**, the merge's subject, the split's
  subject, the rename and the manual clear — and loses `routes/lifecycle.py`'s second call, which
  promoted the situation an alarm was moved **into**.
- **Reason**: `open` means *"an operator is working it"* (#254). The other six name a situation the
  operator was **looking at**; the destination of a move is an **id they typed**. `lifecycle.js`
  offers no picker deliberately — the id is what an operator pastes from a chat during an incident —
  so the destination is the one situation in the estate that a gesture promotes without anybody
  having read it.
- **The harm is concrete, not theoretical**: promotion moves a card out of the **New** tab, which is
  where the operator who has *not* looked at it would find it. The gesture was therefore hiding a
  situation from the queue that exists to surface it — a smaller relative of the defect this release
  is named for.
- **Why the merge is not symmetrical**: `operator_merge` **resolves** the peer (`resolution='merged'`),
  so a merge has no live second situation to promote and no second call site to decide about.
- **Not changed**: the new situation an `operator_split` creates stays `new`. Adding a promotion
  there would be adding an implicit one in the release that removes one, and §2.2 is explicit that a
  restructuring gesture says the *previous* grouping was wrong and not that the result is right.
- **Measured, before the change**: `tools/corpus_census.py --gestures` reports `asserting_bags 10`,
  `asserting_incidents 10`, `asserted_negative_pairs 2 222` (1 050 from one bag) — the plan's §0
  figures, reproduced on this tree. A bare promotion writes no label, so removing one is predicted
  to move none of them: `PREREGISTRATION-0.16.2.md` §4.1, the expected branch, and §4.3 says what to
  do if the number rises instead.

## 274. The idle-but-active situation is DERIVED, and a stored flag would be a cached clock (v0.16.2)

- **Decision** (decision 2): a situation that is live, untouched since the cutoff and still holding an
  active alarm stays `status='open'`/`'new'` and is **derived** at read time. No fourth status, no
  column, **no migration `0016`**.
- **Reason**: staleness is a function of `now`. A stored flag is a cache of a clock reading and is
  wrong between every pair of sweeps — it would say `stale=0` for the 3 599 seconds after a pass and
  be a lie for most of them. Deriving it makes the answer correct at the instant it is asked, which
  is the only instant it is asked.
- **Why not a fourth status**: Part VII's rule, and the arithmetic behind it — `LIVE` is read by the
  sweep, the engine's state reload, the scope resolution, the `open_situations` stat and three
  console tabs. A fourth value is six readers' worth of work to express a fact that `updated_at` and
  `situation_alarm.status` already carry between them.
- **Reachability, which is the risk this decision takes on**: a derived state nothing computes does
  not exist. `store.idle_active_situations` is the one expression, it is served through
  `/api/stats`, and `test_lifecycle.py::test_the_sweep_partitions_the_idle_population` asserts the
  two halves are disjoint and that **both are non-empty** on the same fixture — a partition with an
  empty arm is the failure this guard is written against.
- **Trade-off accepted**: two `SELECT id` statements per maintenance pass where there was one, over
  the population the sweep already scanned and under the lock it already holds. The pass runs every
  five seconds and off the ingest path, so the cost is paid where this project has always been
  willing to pay it.

## 275. The sweep raises the warning it already has a channel for, and writes no audit row (v0.16.2)

- **Decision** (decision 3): after each maintenance pass, `MaintenanceMixin` counts the live
  situations that are idle **and** still hold an active alarm and exposes them through
  `engine.stale_situation_warnings()`, which `runner.py` composes into the operator-warning list
  beside the seven that are already there. No audit row, and no new mechanism.
- **Reason**: *"nobody has touched this in an hour and an alarm is still on"* is the most actionable
  sentence this appliance can produce, and the channel that carries *"the trap allowlist is empty"*
  and *"shadow training failed"* to `/api/stats` is exactly the right one. Building a second
  mechanism for the most important message would be the argument against the first one.
- **Why not an audit row**: the pass runs every five seconds for the life of the appliance. A row per
  pass is 17 280 rows a day recording that nothing changed, and the audit chain is a record of
  **actions**, not of observations — `entity.promote` and `severity.confirm` are written when the
  appliance *decides* something, which is the discriminator.
- **Why not in `engine.py`**: prime directive 4 — the trap path's hashes are unchanged, so the count
  is taken in `maintenance_loop`, which left `engine.py` in v0.9.0 (#121) and is the point that
  already runs after the pass has released the lock. `_idle_active_count` is a class-level default on
  `EngineBase` for the reason `scorer_model_version_id` is (#254's neighbour): `Engine.__init__` is
  in the file that may not change.
- **Measured**: the warning is derived from the same expression the sweep partitions on, so the
  number in the warning and the situations the console badges cannot disagree.

## 276. `warning` is LOW, `indeterminate` is UNKNOWN, and MEDIUM is not invented (v0.16.2)

- **Decision** (decision 4): five vocabulary ranks map to **five** rendered bands, not four.
  `critical`→CRITICAL, `major`→MAJOR, `minor`→MINOR, `warning` (rank 3)→LOW, and **rank 4**
  (`indeterminate`, `cleared`)→UNKNOWN. An alarm with no learned severity is UNKNOWN too, and the two
  are distinguished by their text, which is the element's own word or the absence of one.
- **Reason — the collapse that was wrong.** `format.js` folded ranks 3 and 4 into LOW, so an element
  that reported `indeterminate` — the vocabulary's own word for *"I do not know how serious this
  is"* — was rendered as *"low"*. That is a claim about seriousness the element never made, and it
  is the same defect the module's own docstring already names for the blank cell: *"a blank would
  read as 'no severity' rather than 'not learned yet'."* Applied consistently, it condemns the
  collapse.
- **MEDIUM is not introduced.** The maintainer's scale names one and `SEVERITY_VOCAB` has no token
  that maps to it. The two ways to produce one are to rename `minor` — inventing a word no NE emits
  and no MIB carries — or to add a band nothing can reach. A badge that can display a level the
  appliance cannot produce is worse than one that displays four.
- **What an operator sees**, stated because the question was asked that way: `warning` renders as a
  **LOW** band carrying the word *warning*; `indeterminate` renders as an **UNKNOWN** band carrying
  the word *indeterminate*, visibly the same band as a never-learned severity and visibly a different
  cell from it.
- **Measured**: on the whole labelled corpus, 0 of 2 252 alarms resolve a severity at all
  (`tools/evidence/severity_census.py`), so every band above is reachable today only through the
  console's own fixtures. That is the measurement, not an excuse: it is why v0.16.3 exists.
- **Honest limit, recorded rather than fixed**: for a `kind='int'` severity candidate the rank is the
  raw integer, so ranks above 4 exist and fall into LOW with no upper bound. Nothing in the corpus
  produces one; F99.

## 277. Filled pills, ordered by luminosity, and the glyphs stop repeating (v0.16.2)

- **Decision** (decision 5): `SeverityCell` renders a filled, rounded badge. Five bands, each with
  its own fill, its own ink, its own **distinct** glyph and its text — the rule `format.js` has
  documented since v0.13.0, now true of the glyph as well.
- **Reason the glyphs changed**: `critical` and `major` both rendered `▲`. Two adjacent bands that
  share a glyph AND sit two hue-steps apart on the red-orange axis are one encoding, not three, and
  they are the exact pair that collides under deuteranopia. They are now `▲` and `◆`, with `●` for
  minor, `▬` for low and `?` for unknown — five shapes no two of which are confusable at 11 px.
- **The luminosity ladder, which is the accessibility rule made mechanical**: the fills descend in
  weight from a deep saturated red at CRITICAL to a light neutral at UNKNOWN, so the ordering
  survives when hue does not. Colour, glyph and text are three encodings and any one of them alone
  carries the rank.
- **Why the fill is a token pair and not a filter over the existing colour**: the existing `--alarm`,
  `--major`, `--minor`, `--low` and `--unknown` are **text** colours chosen to be legible ON the
  background. Reusing one as a fill would put ink on a colour that was never picked to hold it, and
  the two themes would fail in opposite directions.
- **Trade-off accepted**: ten new custom properties per theme block, which is the one exception Part
  VII rule 3 allows. No icon set, no design tokens beyond the severity colours.

## 278. `api/routes/`, derived from the import graph, and the guard that would have gone quiet (v0.16.2)

- **Decision** (decision 6): the twelve `routes_*.py` modules move to `api/routes/` and drop the
  prefix — `api/routes/read.py`, `api/routes/lifecycle.py`. The seven that remain at `api/` are the
  machinery every route consumes: `app`, `context`, `declare`, `models`, `perimeter`,
  `governance_cache`, `__init__`. The HTTP surface is unchanged and the behaviour record is the gate.
- **Reason, derived rather than chosen**: v0.15.1's method is to read the import graph, and it gives
  exactly **one** boundary here. Every route module imports `context`, `declare` and `models`; the
  machinery imports no route module except `app.py`, which assembles them; and **no route module
  imports another** — measured, twelve of twelve. A functional five-way split (read / operate /
  model / administer / public) would be a grouping the code does not exhibit, and five directories
  averaging 2.4 files is worse navigation than one list of twelve.
- **What the move found, which is the reason it earned its place**: `tests/apisource.py` — the corpus
  four text-scanning guards read (F28, F34, F39, and the scorer caveat) — globs `PKG_DIR.glob("*.py")`,
  **non-recursively**, and asserts the concatenation exceeds `MIN_SOURCE_CHARS = 60 000`. The seven
  machinery modules alone are 74 640 characters, so a move into a subdirectory would have dropped
  every route module from the corpus and left all four guards passing over source that contains no
  routes. Both halves are repaired in the move's own commit: the walk is recursive, and the floor is
  raised to a figure a subset cannot clear. F98.
- **Trade-off accepted**: twelve renames and every path-pinning reference updated in the same commit.
  Nothing else changes; `MODULE_ORDER` keeps registration order, which is what makes the scanning
  guards' slices mean what they meant.
