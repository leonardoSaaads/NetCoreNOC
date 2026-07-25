# Governance — v0.7.0 draft (specification only, not implemented in v0.6.0)

This document specifies what **v0.7.0** will make configurable on the **HTTP security perimeter**:
admin-configurable per-role capabilities, and per-role/per-principal *visibility scoping*. **It
implements nothing.** Every element below is tagged **`v0.7.0: planned`**.

It supersedes sections 1 and 2 of `EXTENSIBILITY-0.6-DRAFT.md`, which specified these two surfaces
for v0.6.0. They were resequenced to v0.7.0 by **DECISIONS #43**: the scoring seam changes the
*engine* and is gated by exact parity against a frozen baseline, whereas these two change the
*authorization and disclosure perimeter*, where the failure mode is silent privilege escalation or
an existence oracle. Three different risk profiles must not share a release, and each review must
be able to be honest about its own surface.

v0.6.0 changes neither `rbac.py`'s authorization model nor `shaping.py`'s field rules beyond
adding three capabilities (`scorer.read`, `scorer.preview`, `scorer.write`) to the existing map.

## What v0.7.0 will let an admin do

1. **Adjust, per role, which capabilities a role holds** — within a fixed ceiling that a stored
   grant can never exceed.
2. **Scope what a role or an individual principal may see** — which NEs / IPs / hosts appear in
   listings and detail views at all, generalising today's field-level coarsening to
   resource-level projection.

Neither is a new authorization *mechanism*. Both are stored *policy* read through the existing
single decision points: `rbac.role_allows` / `rbac.permission_for` for routes, and
`shaping.shape` for bodies.

---

## 1. Admin-configurable RBAC (`v0.7.0: planned`)

Today a capability's minimum role is fixed in `PERMISSIONS` (`src/netcorenoc/rbac.py`). v0.7.0
lets an admin adjust, per role, which capabilities a role holds — **without weakening any
invariant**.

### Shape of the stored policy

An append-only `rbac_grant` table (`role`, `permission`, `granted` boolean, `created_by`,
`created_at`, `note`) with a one-row active pointer, mirroring v0.6.0's `scorer_config` /
`scorer_active` pattern so rollback is a pointer move and history is tamper-evident. The coded
`PERMISSIONS` map remains the **seed** and the **ceiling**; a capability with no stored grant
falls back to its coded default.

### Invariants preserved (non-negotiable)

- **Single source of truth.** Stored grants are read *through* `rbac.role_allows` /
  `rbac.permission_for`. There is never a second authorization table and never an `if role ==`
  check outside `rbac.py`. `tests/test_rbac.py::test_authorization_matrix` continues to generate
  its expectations from that one source, so the test cannot drift from the enforcement.
- **Deny-by-default.** An unmapped route still fails closed
  (`test_every_api_route_is_in_the_permission_map`). A role with no grant for a capability is
  denied.
- **401 / 403 / 404 semantics unchanged.** Missing identity → 401; authenticated-but-insufficient
  → 403; resource lookup happens only *after* authorization, so 403 never leaks existence
  (`test_403_precedes_404_no_existence_oracle`).
- **The fixed role ceiling is the escalation guard.** The coded `PERMISSIONS` defines, per
  capability, the **highest role** a grant may assign it to. A grant that tried to give `viewer`
  an admin-ceiling capability is **rejected at write time** and audited as denied.
- **Audit-log and account-management capabilities are undelegable.** `audit.read`, `audit.export`,
  `audit.prune`, `users.manage`, `tokens.manage`, `config.write`, and v0.6.0's `scorer.write` /
  `scorer.preview` stay admin-ceiling and cannot be moved downward by any grant. An admin who
  could delegate audit visibility or user creation would defeat the accountability model.
  (Security-relevant ambiguity resolves toward the stricter option.)
- **Grants only narrow or widen *within* the ceiling; they never silently escalate a live
  session.** Authorization is computed per request from the current stored policy, so a narrowing
  takes effect on the next request. Where a change amounts to an effective role change for a
  principal, the existing revocation applies — role changes already revoke that user's sessions
  (`store.revoke_user_sessions`), so a de-privileged principal cannot ride an old session.
- **Every change is audited.** New actions `rbac.grant` / `rbac.revoke` (admin actor, before/after
  in `details`, no secret), one append-only row per change, covered by the audit-catalog
  completeness test exactly as existing actions are.

### Threat-model entries v0.7.0 must add

- **Privilege escalation via a stored grant** — *control*: ceiling validation at write time +
  audited change + per-request re-evaluation + session revocation on effective role change;
  *test*: a grant above the ceiling is rejected and audited; a narrowed principal is denied on the
  next request and its sessions are revoked.
- **Authorization bypass via a second policy source** — *control*: grants are read only through
  `rbac.role_allows`; *test*: the generated authorization matrix passes with grants active, and a
  static check finds no authorization decision outside `rbac.py`.

---

## 2. Per-role / per-principal visibility scoping (`v0.7.0: planned`)

Today `shaping.py` coarsens or drops *fields* by role. v0.7.0 generalises it to a stored policy
that scopes *resources*: which NEs / IPs / hosts a given role — or an individual principal — may
see at all.

### Shape of the stored policy

An append-only `visibility_scope` table mapping a **subject** (a role, or a specific principal) to
an allowed **resource set** (CIDRs / NE ids / host globs), with the same append-only + active
pointer discipline. It is read on every resource-listing and resource-detail path and composed
with the existing field-level `shape()`: resource scoping and field coarsening are two layers of
the same deny-by-default serializer, never scattered checks.

### Invariants (fail closed, never an existence oracle)

- **Fail closed by default, and "no policy" means today's behaviour.** The *current* v0.6.0
  behaviour is exactly the "no scope policy configured" case: every viewer/editor sees the full
  set the route already allows. Introducing a scope can only *restrict*.
- **A referenced-but-empty scope yields an empty set, not the full set.** A principal a policy
  names but grants no resources to sees zero rows — never an error that reveals the resource
  count, and never a fail-open to everything.
- **Never leak existence of out-of-scope resources.** A request for a specific out-of-scope NE
  returns **404, not 403** — consistent with "authorization precedes resource lookup", so scoping
  never becomes an oracle for which NEs exist. Listing endpoints omit out-of-scope resources
  entirely; they are not rendered-then-hidden.
- **Audited on change.** Every scope-policy edit writes an append-only audit row (subject,
  before/after resource-set summary — counts and CIDRs, never trap payloads).
- **Full fidelity preserved server-side.** Like field shaping today, the engine, store, and audit
  log keep every NE. Scoping is presentation-time projection only; the ingest path is untouched.

### ⚠ Visibility scoping is a presentation control and is **NOT** tenant isolation

This limit is mandatory to state, and stating it is part of the specification.

Scoping decides **what a principal is shown**. It does **not** partition what NetCoreNOC
*learns*, *correlates*, or *groups*:

- **Correlation still learns across all NEs.** The `A` (class × class) and `E` (NE × NE) matrices
  are global. A storm on customer X's NEs still shapes the learned matrices that influence how
  customer Y's alarms group, and a `split`/`confirm` feedback from one operator still moves the
  shared matrices.
- **Situations may span scope boundaries.** A situation is a connected component of the link
  graph; that graph is computed before any scoping is applied. A scoped viewer can therefore see a
  situation whose membership count or root-cause hint is influenced by alarms they cannot see, and
  the honest presentation of that (an "N members outside your scope" affordance versus silent
  omission) is itself a v0.7.0 design decision, not a detail.
- **Side channels remain.** Aggregate counters (`/api/stats`), learned-edge weights, timing, and
  situation ids are global by construction. Scoping is not designed to defeat inference from them.

**True multi-tenant isolation** — per-tenant learning, per-tenant situation boundaries, per-tenant
retention and audit segmentation — is a separate, larger, later feature that would change the
engine, the schema, and the eval methodology. It is explicitly **not** what v0.7.0 delivers, and
the v0.7.0 documentation must say so in the operator-facing text, not only here. Selling scoping
as isolation would be the most damaging thing this project could claim.

### Threat-model entries v0.7.0 must add

- **Existence oracle via resource scoping** — *control*: 404 (not 403) for an out-of-scope
  resource, list-omission for collections; *test*: an out-of-scope detail fetch is
  indistinguishable from a nonexistent one.
- **Fail-open scope misconfiguration** — *control*: no policy ⇒ current behaviour; a
  referenced-but-empty scope ⇒ empty set; *test*: default-deny on an empty scope.
- **Isolation over-claim** — *control*: documented limit (above), operator-facing wording, and a
  documentation test that the "not tenant isolation" statement is present; *test*: the phrase is
  asserted in the shipped docs so it cannot be quietly dropped.

---

## Relationship to the v0.6.0 scoring seam

The two releases touch disjoint surfaces, deliberately:

| | v0.6.0 (built) | v0.7.0 (planned here) |
|---|---|---|
| Surface | engine scoring (`correlate.py` → `scoring.py`) | HTTP perimeter (`rbac.py`, `shaping.py`) |
| Gate | byte-identical parity vs the frozen eval baseline | authorization matrix + 404-not-403 evidence |
| Failure mode | degraded grouping (visible, previewable, reversible) | silent escalation / existence oracle |
| Stored policy | `scorer_config` + `scorer_active` (append-only + pointer) | `rbac_grant`, `visibility_scope` (same pattern) |

v0.7.0 inherits v0.6.0's storage discipline wholesale — append-only rows, a one-row active
pointer, rollback as a pointer move, provenance by reference, and a fail-safe fallback to the
coded default — because that pattern is now proven in the tree. It adds no new mechanism of its
own beyond the two policies above.
