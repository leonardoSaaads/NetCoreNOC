# Security Review — NetCoreNOC v0.7.0

> **Status: closed at Gate 4.** Every row below names the regression test that proves it and the
> status it earned. The skeleton was written in Phase 1 against the design; nothing in it was
> relaxed to make a test pass, and where a control is met with a caveat the row says so and the
> caveat carries a `docs/ROADMAP.md` line.

An adversarial review of everything v0.7.0 adds: the stored **capability policy** and its resolver
(`src/netcorenoc/rbac.py`), the stored **visibility scope** and its read filter
(`src/netcorenoc/shaping.py` + every NE-bearing read path in `src/netcorenoc/api.py`), the
`0006_governance.sql` policy store, and the **preview↔engine candidate-selection unification**
carried out of v0.6.0.

The release is deliberately narrow, and that shapes the review. v0.7.0 adds **no outbound call, no
dynamic code loading, no new runtime dependency, and no change to `receiver.datagram_received`, the
engine, the learning, or the scoring seam**. What it does add is **two stored inputs that are read
on the authorization path** and **a resource-level projection over every read that names a network
element**. Those two are what this review attacks.

The failure mode is worth naming up front, because it differs from v0.6.0's. A bad scoring
parameter degrades grouping *visibly*. A bad governance decision is **silent**: either a principal
quietly holds a capability the code reserves, or a principal quietly learns that a network element
exists. Silence is why every control below is structural — an intersection, a filtered query —
rather than a validation check that has to be remembered on every future write path.

Findings continue the series from **F27**. F1–F14 are v0.1–v0.4, F15–F19 v0.5.0, **F20–F26 v0.6.0**
— note that the v0.6.0 series ran to F26 (the `OPTICORR_*` removal), so F27 is the next unused
number; see `docs/gates/v0.7-phase-0.md` §1. **No existing finding is renumbered.**

Status legend: **met** (file + test prove it) · **planned** · **N/A** (one-line reason) ·
**partial** (met with a documented gap → ROADMAP).

## 1. Standards anchor (continued from v0.4.0–v0.6.0)

- **Application**: OWASP ASVS 4.0.3 Level 2 — re-verified for the four new routes, the two stored
  policies, and the changed read paths. Most relevant this release: **V1.4** (access-control
  architecture — "a single, well-vetted access control mechanism"), **V4.1/V4.2/V4.3**
  (general/operation-level/other access control, including *fail securely* and *deny by default*),
  **V5.1** (input validation of selectors), **V7.1/V7.2** (log content and integrity), **V8.3**
  (sensitive private data — the NE inventory becomes such data for a scoped principal), **V13.1**
  (generic API security, including "no enumeration").
- **Access-control model**: NIST SP 800-162 (ABAC) terminology is deliberately *not* adopted. The
  model stays RBAC with a compiled ceiling plus a stored restriction, because an attribute-based
  policy engine would make the first operand of the escalation-proof intersection into data.
- **Change management / integrity**: the append-only + hash-chain discipline already proven for
  `audit_log` and `scorer_config` is reused verbatim for the policy tables — same trigger pattern,
  same tamper-evidence argument, same one-row active pointer, same rollback-by-pointer.
- **Supply chain**: unchanged. **Zero** new runtime dependencies is itself the control; the release
  is set operations, bounded queries, and one SQL migration.

## 2. Findings — F27…F33 (continuing the F1–F26 series)

| # | Sev | Area | Finding / property asserted | Fix / control | Test | Status |
|---|-----|------|------------------------------|---------------|------|--------|
| F27 | **High** | Privilege escalation via stored policy | A stored policy that could *add* a capability to a role would be an escalation primitive reachable by anyone who can write the table — through the API, a future second write path, a bad migration, or direct `sqlite3` access to a stolen or restored DB file. Write-time validation protects only the paths it is on. | Resolution is `ceiling(role) ∩ granted(role) ∩ granted(principal)` with the **compiled `PERMISSIONS` map as the first operand** (DECISIONS #53). An intersection cannot exceed its first operand, so an above-ceiling row is **inert**, not merely rejected. Write-time 400 is a usability affordance only. `rbac.read`/`rbac.write` admin-only, no delegation, in `AUDITED_DENIED_PERMISSIONS`. | `test_governance.py::test_f27_no_generated_policy_resolves_above_the_ceiling` (hypothesis, 250 examples, strategies including above-ceiling capabilities, unknown roles/capabilities and empty strings); `…_a_policy_naming_an_above_ceiling_capability_is_inert`; `…_a_policy_can_only_narrow`; `…_policy_written_directly_to_the_db_cannot_escalate`; `…_rbac_and_scope_write_are_admin_only`; `…_denied_governance_attempts_are_audited`; `…_recovery_capabilities_stay_within_the_admin_ceiling`; `test_per_principal_capability_restriction_never_widens` | **met** |
| F28 | **High** | Second decision site | Two places computing "may this principal do X" drift silently and one-directionally. The UI-affordance gate and the authorization matrix are the two most likely divergences. | One resolver, `rbac.resolve_capabilities()`, read by the `api.py` security dependency, by `GET /api/me`, and by the generated matrix. A source-level assertion forbids a role comparison outside `rbac.py`. Scope is a *separate* resolver composed after authorization and is never an authorization input. | `test_governance.py::test_f28_no_role_comparison_outside_rbac`; `…_scope_policy_grants_no_capability`; `test_rbac.py::test_authorization_matrix_with_a_policy_active` (expectations regenerated from the resolver) and `…_a_policy_never_makes_a_route_reachable_that_the_ceiling_forbids` | **met** |
| F29 | **Med** | Fail-safe and lockout | "Fail closed" means opposite things for the two policies: closing capabilities denies the admin the ability to repair; failing scope open over-discloses. Getting either backwards is unrecoverable or a disclosure bug. | Malformed **capability** policy ⇒ fall back to the compiled ceiling (the shipped v0.6.0 baseline) + `operator_warnings()` + audit row. Malformed **scope** policy ⇒ deny for viewer/editor + warning + audit row. Safe in both directions **only because admin is never scoped** (DECISIONS #55, #58). | `test_governance.py::test_f29_malformed_capability_policy_falls_back_to_the_ceiling` (also asserts the warning and the `governance.fallback` row); `…_malformed_scope_policy_denies_viewer_and_editor`; `…_admin_cannot_be_locked_out_by_a_well_formed_policy`; `test_f32_admin_is_never_scoped`; plus `test_malformed_capability_documents_never_raise_and_fall_back` and `test_malformed_scope_documents_never_raise_and_deny` (11 + 6 malformed shapes) | **met** |
| F30 | **Med** | Session staleness | A capability or scope revoked while a session is open must not survive in that session — including on an SSE stream opened before the change, which captures its principal once at connect time. | Both resolvers run **per request**; nothing is cached on the session or the principal. The SSE generator re-resolves capability **and** scope on **every event**, not at connection. | `test_governance.py::test_f30_revoked_capability_does_not_survive_an_open_session`; `…_scope_change_takes_effect_on_the_next_request`; `…_sse_reevaluates_scope_on_every_event`; `…_sse_ends_when_the_streaming_capability_is_revoked` | **met** |
| F31 | **Med** | Provenance and audit integrity | If the policy history behind a past authorization decision can be edited, an incident review cannot answer "who could see and do what, on the day?" — and an attacker could rewrite the apparent perimeter. | Policy tables are append-only at the storage layer (`BEFORE UPDATE`/`BEFORE DELETE` → `RAISE(ABORT)`) with a one-row active pointer; rollback moves the pointer, rows are never mutated; the retention prune does not touch them. Every change also writes a hash-chained `rbac.policy.update` / `scope.policy.update` row with before/after, so tampering must defeat both. | `test_governance.py::test_f31_policy_history_is_append_only_and_survives_a_clear`; `…_prune_does_not_touch_the_policy_tables`; `…_every_policy_change_is_attributable`; `test_audit.py::test_audit_catalog_completeness` | **met** |
| F32 | **High** | Scope bypass and existence disclosure | Scoping is the first control in this project whose failure discloses *the existence of a network element*. A 403, a leaked identifier inside a mixed situation, a global aggregate, or one unfiltered read path is an enumeration primitive for an authenticated viewer. | **404, not 403**, produced by filtering the lookup itself so "out of scope" and "absent" are indistinguishable by construction (DECISIONS #60). One filter applied to **every** NE-bearing read (list, detail, graph, timeline, entities, stats, SSE). Out-of-scope situation members redacted to a **count and type only** — no NE id, IP, entity key, or varbind; links to redacted members withheld; root hint suppressed when the root is out of scope. Enumerating aggregates computed over the in-scope set only. | `test_governance.py::test_f32_out_of_scope_detail_is_indistinguishable_from_nonexistent`; `…_redacted_members_disclose_no_identifier`; `…_aggregates_are_computed_over_the_in_scope_set`; `…_every_ne_bearing_read_is_filtered`; `…_admin_is_never_scoped`; `…_scoping_is_not_tenant_isolation_is_documented`; plus `test_scope_selector_forms` (9 cases) and `test_scope_layers_union_and_an_unset_layer_expresses_no_opinion` | **met** |
| F33 | **Low** | Hot-path surface | The prime directive is that ingestion gains nothing. A policy read in `datagram_received`, in the engine batch, or in the learning would violate it silently — and a policy read holds a lock and touches the DB, which is exactly what the trap path may not do. | Both policies are read HTTP-side, per request, and nowhere else. `receiver.py` imports neither `rbac` nor `shaping`. The v0.6.0 F24 source-level assertions over `datagram_received` remain in force, extended with the governance identifiers. The engine, `learn.py`, and `scoring.py` are unchanged. | `test_governance.py::test_f33_datagram_received_gained_nothing`; `…_receiver_does_not_import_the_governance_modules`; `…_engine_and_learning_are_untouched_by_governance`; unchanged `test_scoring.py::test_f24_*` and `test_perf.py` | **met** |
| — | — | Migration integrity (F12 class) | The new migration must ship in the wheel **and** sdist, apply to a populated v0.6.0 DB with data intact and the audit chain verifying, and seed **no** governance rows so the result is byte-identical. | `0006_governance.sql` under the existing `migrations/*.sql` package-data glob; forward-only, additive; **zero** seeded rows, because "no policy" resolves to the ceiling and to full visibility (DECISIONS #54). | `test_migration.py::test_migrate_populated_v060_database_seeds_no_governance_rows`; `test_upgrade.py::test_v070_upgrade_changes_no_behaviour`; `test_supply_chain.py`; the Gate 4 built-wheel/sdist install check (schema 6, zero policy rows) | **met** |
| — | — | Correctness carried from v0.6.0 (not a security finding) | `preview.partition()` was a second implementation of the engine's windowing/candidate selection with its own copies of the window and cap. A what-if that silently diverges from the engine lies to the operator — and the scoping read-filter is built on these same paths. | One shared selection helper used by both `Correlator._recent_live()` and `preview.partition()`; preview's bounds become aliases of the engine's constants (DECISIONS #61). A partition-parity test asserts preview reproduces the engine's actual situation partition over the same alarms. | `test_correlate.py::test_preview_reproduces_the_engine_partition`; `…_over_generated_streams` (hypothesis); `…_preview_and_engine_share_one_selection_implementation`; `test_select_candidates_skips_tombstones_and_honours_the_window_and_cap` | **met** |

## 3. Attack narratives worked through (not a table)

Four attackers from the threat model, each walked end to end through the new surface, naming where
they stop and — where it matters — what they still get.

### A2, a malicious viewer, wants an admin capability

Sees `/api/rbac` in the UI's absence and calls it directly → **403**, audited. Cannot write a
policy. Suppose an admin is socially engineered into pasting a document granting
`viewer: [users.manage, audit.read, rbac.write]` — the API rejects it with a 400 naming each
above-ceiling line, but even if it had not (a future second write path, a bad migration), the
resolver intersects with `ceiling("viewer")` and the entries are **inert**. Verified by
`test_f27_a_policy_naming_an_above_ceiling_capability_is_inert` and, from the other direction, by
`test_a_policy_never_makes_a_route_reachable_that_the_ceiling_forbids`, which grants every role
every capability and sweeps every route.

**Stops at:** the intersection. There is no ordering of API calls that reaches an above-ceiling
capability, because no code path adds to the resolved set.

### A5, holding a copy of `netcorenoc.db`, edits it and restores it

The strongest version of the escalation attempt, and the one a write-time check cannot see. They
`INSERT` a policy row and point `governance_active` at it, entirely outside the API.
`test_f27_policy_written_directly_to_the_db_cannot_escalate` does exactly this: the viewer still
gets 403 on `/api/users`, `/api/audit` and `/api/rbac`. They can also *narrow* — a denial-of-service
by policy — but that is visible (operator warning), attributable, and repairable, and the admin's
recovery capabilities cannot be removed.

**Stops at:** the intersection again, for the same reason. **Still gets:** tampering with the
policy history is detectable (append-only triggers plus the hash chain), but a restored *whole*
database is a restored whole database — offline DB integrity was never in scope for any release,
and `SECURITY.md`'s operator guidance on protecting the file still applies.

### A2, a scoped viewer, wants to enumerate the network

Asks for a situation id they were not shown → **404**, byte-identical to a genuinely missing id
(status, body, headers — asserted). Watches `/api/stats` for a counter that moves when nothing
visible changed → every enumerating counter is computed over their own NE set. Reads a
mixed-membership situation they *can* see → the out-of-scope members are a count and a list of
alarm classes; the response contains no out-of-scope NE id, address, or entity key at any depth,
and the links referencing hidden members are withheld so no alarm id survives as a dangling
reference. Opens an SSE stream and waits, hoping the connect-time grant outlives a policy change →
every event re-resolves.

**Stops at:** the filtered lookup — "out of scope" and "absent" are the same code path. **Still
gets:** the *cardinality* of what they cannot see, deliberately (§4), and inference from global
situation ids and timing, which scoping was never designed to defeat.

### A compromised admin

Can do everything an admin can do, by definition — including narrowing the perimeter maliciously.
The controls are not prevention: every change is bounded by the compiled ceiling, appended
immutably, attributed in the hash-chained audit log, reversible by pointer, and visible as an
operator warning when it degrades. They **cannot** brick the appliance (the recovery capabilities
survive any policy), and they cannot grant themselves or anyone else a capability the code reserves.

**Stops at:** nothing, and the review says so plainly. An admin governs. What the design buys is
that the governing is visible, bounded, and undoable.

## 4. Critical analysis — honest residual risk

**Scoping is presentation, not isolation, and the gap is real.** The learned matrices are global,
situation ids are global and monotonic, timing is shared. A scoped viewer who watches situation ids
advance faster than their own visible activity can infer that *something* is happening beyond their
boundary. Nothing in this release closes that, and closing it means per-tenant learning — a
different engine, a different schema, and a different eval methodology. What this release owes is
not a fix but an accurate claim, which is why the limit is stated in five documents and the UI and
asserted by a test.

**A scoped operator sees a partial picture, and that is an operational hazard.** During a
cross-boundary fibre cut, the operator who can see three of forty alarms is at risk of triaging a
major incident as a minor one. The design's answer is the redacted count and the alarm classes —
"there are 37 more members you cannot see, of these classes". That is a mitigation, not a solution.
The alternative, silent omission, was rejected (DECISIONS #59) precisely because it would make the
operator *confidently* wrong rather than *aware* they are looking at an edge.

**The redaction discloses cardinality, and cardinality is information.** A viewer learns how many
alarms exist beyond their boundary in situations they can partly see. This is strictly less than the
situation id and `updated_at` they can already read, and it is the minimum that keeps them honest —
but it is not zero, and an operator who genuinely needs zero cross-boundary inference needs
isolation, not scoping.

**The write-time validator can lull.** `capability_policy_errors` reports above-ceiling entries as
having "no effect", which is true — but an admin who ignores the warning and stores the policy
anyway gets a policy that silently does less than it reads as doing. The mitigation is that the
`/api/rbac` response shows each role's **resolved** set beside its ceiling, so "what did I just do?"
is answerable without reasoning about the document.

**One malformed-policy asymmetry is deliberate and worth restating.** A bad *capability* policy is
more permissive than the author intended (it falls back to the ceiling); a bad *scope* policy is
more restrictive (it denies). Both raise a warning and an audit row, so neither is silent — but an
operator who writes a capability policy to *restrict* and mistypes it gets the restriction silently
not applied, protected only by the warning they must read. Failing closed instead would deny the
admin the ability to repair it, which is the worse trade (DECISIONS #55), but the asymmetry is a
real sharp edge and is why the warning text says explicitly that nobody has gained anything.

**Policy history grows one row per change and is never pruned.** Bounded in practice (a handful of
rows per year) and immutable by design; the same trade already accepted for `scorer_config`.

**Per-request policy reads add a query to every authenticated request.** One SELECT over a two-row
table, plus one NE listing when a scope policy is active and the caller is not an admin. Measured as
noise against the existing per-request session lookup, and the alternative — caching with explicit
invalidation — trades a staleness window on the *authorization* path for a saving that does not
matter at this scale (DECISIONS #57, and a ROADMAP line if an NE table ever grows past it).

Every follow-up above is a `docs/ROADMAP.md` line; none is silent scope.

## 5. Mapping to `threat-model.md`

| Threat (v0.7.0 section) | Finding | Proving test |
|---|---|---|
| Escalation via a stored grant | F27 | `test_f27_no_generated_policy_resolves_above_the_ceiling`, `…_policy_written_directly_to_the_db_cannot_escalate` |
| Escalation via a second policy source | F28 | `test_f28_no_role_comparison_outside_rbac`, `test_authorization_matrix_with_a_policy_active` |
| `rbac.write` as the escalation | F27 | `test_f27_rbac_and_scope_write_are_admin_only`, `…_denied_governance_attempts_are_audited` |
| Malformed policy locks the admin out | F29 | `test_f29_admin_cannot_be_locked_out_by_a_well_formed_policy`, `…_malformed_capability_policy_falls_back_to_the_ceiling` |
| Untraceable perimeter change | F31 | `test_f31_every_policy_change_is_attributable`, `test_audit_catalog_completeness` |
| Policy change riding an open session | F30 | `test_f30_revoked_capability_does_not_survive_an_open_session` |
| Existence oracle via a scoped resource | F32 | `test_f32_out_of_scope_detail_is_indistinguishable_from_nonexistent` |
| Leakage through aggregates | F32 | `test_f32_aggregates_are_computed_over_the_in_scope_set` |
| Leakage through a mixed-membership situation | F32 | `test_f32_redacted_members_disclose_no_identifier` |
| The live stream forgetting the policy | F30 | `test_f30_sse_reevaluates_scope_on_every_event`, `…_sse_ends_when_the_streaming_capability_is_revoked` |
| Fail-open *or* lockout on a malformed scope | F29 | `test_f29_malformed_scope_policy_denies_viewer_and_editor`, `test_f32_admin_is_never_scoped` |
| Scope confused with authorization | F28 | `test_f28_scope_policy_grants_no_capability` |
| A policy read on the trap path | F33 | `test_f33_datagram_received_gained_nothing`, `…_engine_and_learning_are_untouched_by_governance` |
| A migration that changes behaviour | — (F12 class) | `test_migrate_populated_v060_database_seeds_no_governance_rows`, `test_v070_upgrade_changes_no_behaviour` |
| Isolation over-claim | F32 | `test_f32_scoping_is_not_tenant_isolation_is_documented` |
