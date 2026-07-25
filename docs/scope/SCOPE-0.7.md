# SCOPE — NetCoreNOC v0.7.0

**Theme: governance — an admin defines what each role and principal may *do* and may *see*.**

v0.6.0 made the *link formula* configurable. v0.7.0 makes the *perimeter* configurable: which
capabilities a role or an individual principal actually holds, and which network elements they are
shown at all. Both are **stored, audited policy read through the existing single decision
points** — `rbac.py` for routes, `shaping.py` for bodies. Neither is a new authorization
mechanism, and neither can widen anything.

The one sentence that governs the whole release:

> **With no stored governance policy, v0.7.0 behaves byte-identically to v0.6.0.** The shipped
> static authorization map and full visibility are simultaneously the **default** and the
> **ceiling**.

A fresh database and a migrated v0.6.0 database both carry **zero** governance rows, so an
operator who never opens the governance panel cannot tell v0.7.0 from v0.6.0 by observation. Most
operators never will — that is the intended experience, not a fallback.

The runtime identity is unchanged: one Python 3.12 asyncio process, one SQLite (WAL) file, one
static UI, environment variables only, no build step, no npm, **zero new runtime dependencies**.
All prior scope documents and their invariants still hold; `docs/security/threat-model.md` keeps
the authority it has held since v0.2.0. On a conflict, this document wins on *scope*, the build
prompt wins on *process and quality*, the threat model wins on *security posture*.

**Delivery model (unchanged).** The repository is read-only to automation: the maintainer takes
the resulting archive and pushes it by hand. No step depends on pushing, on CI running, or on any
external account, registration, or dashboard action. Every gate is local and reproducible on the
maintainer's machine (`make qa`, `make eval`, `docker compose config`, a locally built wheel).
Committed CI/release workflows are ready-to-use artifacts, never prerequisites.

---

## In scope — exactly three items, plus a review and a specification refinement

### 0. Close-out carried out of v0.6.0 — one candidate selection, proven (P0, lands first)

v0.6.0 shipped `preview.partition()` as a *second* implementation of the engine's
windowing/candidate selection, with its own copies of the window length and the candidate cap.
Nothing pinned the two together: changing `correlate.WINDOW_S` alone would have made the what-if
quietly lie, and a what-if that lies is worse than no what-if.

v0.7.0 extracts **one shared helper** used by both `Correlator._recent_live()` and
`preview.partition()`, and adds a **preview↔engine partition-parity test** asserting that
`preview.partition()` reproduces the engine's actual situation partition over the same alarms.

This is a correctness guarantee, not a security finding — but it is also the **prerequisite** for
workstream 2: the scoping read-filter is built on top of these read paths, and layering a
disclosure control over two implementations that may disagree is how an existence oracle gets
built by accident. It therefore lands **before** any scoping code (Phase 3, S0).

### 1. Admin-configurable RBAC — capability restriction within a fixed ceiling

The per-role (and per-principal) capability set becomes stored, audited policy that can only ever
**narrow** the compiled ceiling.

- **The ceiling.** The compiled `PERMISSIONS` map in `src/netcorenoc/rbac.py` is reinterpreted, in
  place, as the **hard maximum**:
  `ceiling(role) = { capability : ROLE_RANK[role] >= ROLE_RANK[PERMISSIONS[capability]] }`.
  That is *exactly* today's behaviour, expressed as a set.
- **The resolved set is an intersection**, and that is the whole escalation argument:
  ```
  resolved(principal) = ceiling(role) ∩ granted(role) ∩ granted(principal)
  ```
  An intersection **cannot** exceed its first operand. No stored policy — malformed, adversarial,
  or crafted — can put a capability into a principal's resolved set that the compiled map does not
  already grant their role. Escalation is impossible **by construction**, not by a validation
  check that could be bypassed or forgotten.
- **Unset means "the whole ceiling".** No policy row for a role ⇒ `granted(role)` is treated as
  the ceiling itself, so the intersection is the ceiling — parity. Same for a principal.
- **P0: per-role.** **P1: per-principal**, a further restriction that never widens.
- **One resolver.** `rbac.resolve_capabilities(...)` is the only function that computes a
  capability set. The `api.py` security dependency, the UI-affordance gate (`/api/me`), and the
  tests all read that one answer. `ROUTE_PERMISSIONS` and the 401/403/404 semantics are unchanged.
- **Fail-safe to the ceiling.** A missing, unreadable, or malformed capability policy falls back
  to the compiled ceiling — the shipped safe baseline, i.e. today's behaviour — raises an operator
  warning through `operator_warnings()`, and audits the event. It never falls to a state that
  grants above the ceiling, and it never hard-locks the admin.
- **Live sessions.** The resolved set is computed **per request** from live policy, so a change
  takes effect on the next request without a restart. This is a property the code already has —
  authorization was never cached.
- **New capabilities** (added to the single map, both admin-only, `config`-class, both in
  `AUDITED_DENIED_PERMISSIONS`): `rbac.read`, `rbac.write`.
- **New audit action:** `rbac.policy.update` (admin actor, before/after captured).

### 2. Per-role / per-principal visibility scoping — presentation, not isolation

Which NEs a principal may see becomes stored, audited policy generalising `shaping.py` from
*fields* to *resources*.

- **The scope model.** A scope is a set of **NE selectors** — NE id, exact IP, CIDR, or host-glob
  — resolved to a set of NE ids at read time. A principal's scope is `role_scope ∪
  principal_scope`, where each layer is a *restriction*: **unset ⇒ all NEs** (parity), **set ⇒
  only these**, fail-closed.
- **Admin is never scoped.** This single rule is what makes fail-closed safe everywhere else: a
  malformed scope policy can deny for viewer/editor without risk, because the admin who must fix
  it can never be locked out of the data or of `scope.write`.
- **One filter, every read.** A single resolver (`shaping.visible_nes(...)`) applied uniformly to
  every endpoint returning NE/entity/situation/alarm/graph/timeline data. No scattered `if ne in`
  checks.
  - Lists return only in-scope NEs/entities/alarms.
  - A **situation** is listed iff it contains **at least one in-scope member**; its out-of-scope
    members are **redacted to a coarse count and type** — no NE id, IP, entity key, or varbind — so
    operational usefulness survives without disclosing out-of-scope identifiers.
  - A directly-requested resource **entirely** out of scope returns **404, not 403**, past
    authorization: existence is not disclosed.
  - Aggregate counters that would let a scoped principal infer out-of-scope volume are computed
    over the in-scope set only.
- **Fail-closed.** A malformed or unreadable scope policy **denies** for viewer/editor (they see
  nothing new), with an operator warning and an audit row. It never fails *open*.
- **Live sessions.** Scope is resolved per request, including on every SSE event.
- **New capabilities:** `scope.read`, `scope.write` — admin-only, `config`-class, in
  `AUDITED_DENIED_PERMISSIONS`.
- **New audit action:** `scope.policy.update` (admin actor, before/after).

#### ⚠ Scoping is a presentation control and is **NOT** tenant isolation

Stating this is part of the deliverable, in this document, in `DESIGN.md`, in `README.md`, in
`MIGRATION.md`, and **in the UI**, with a documentation test asserting the statement is present so
it cannot be quietly dropped.

Scoping decides **what a principal is shown**. It does **not** partition what NetCoreNOC *learns*,
*correlates*, or *groups*:

- **Correlation still learns across all NEs.** The class×class `A` and NE×NE `E` matrices are
  global. A storm on one operator's NEs still shapes the matrices that influence how another's
  alarms group, and a `confirm`/`split` from one operator still moves the shared matrices.
- **Situations may span scope boundaries.** A situation is a connected component of a link graph
  computed *before* any scoping is applied. A scoped viewer can therefore see a situation whose
  size and root-cause hint are influenced by alarms they cannot see. The redaction count
  ("N members outside your scope") is the **honest signal** that this happened — silent omission
  would be the dishonest alternative, and is rejected.
- **Side channels remain by construction.** Situation ids are global and monotonic; timing is
  shared; learned edge weights are global. Scoping is not designed to defeat inference from them.

**True multi-tenant isolation** — per-tenant learning, per-tenant situation boundaries, per-tenant
retention and audit segmentation — is a separate, larger, later feature that would change the
engine, the schema, and the eval methodology. It is explicitly **not** what v0.7.0 delivers.
Selling scoping as isolation would be the most damaging thing this project could claim.

### 3. Security review — `docs/security/SECURITY-REVIEW-0.7.md`

A dedicated adversarial review of items 0–2, continuing the finding series from **F27** (the tree
already uses F26 for the v0.6.0 `OPTICORR_*` removal — see `docs/gates/v0.7-phase-0.md` §1).
Each finding carries a severity, a precise location, a fix, a regression test `test_f<N>_*`, and a
mapping row. `threat-model.md` gains the new threats, each mapped to a control and a check.

### 4. Terrain-preparation for v0.8.0 — **specification refinement only, nothing built**

`SCORER-PLUGINS-0.8-DRAFT.md` is refined now that governance and the scoring seam exist: the
`LinkScorer` contract is re-confirmed against the ONNX adapter and the entry-point scorer, the
**worker-process preemption harness** (`resource.setrlimit` + a real wall-clock kill) is recorded
as a **blocking prerequisite**, and the reconciliation with governance is stated — activating a
customer scorer is `scorer.write`, admin-only, **never scoped and never delegated**, because a
scorer is a system-wide logic change and not a per-resource view. Every element stays tagged
`v0.8.0: planned`.

---

## Out of scope — deferred, in this order

Each is a `docs/ROADMAP.md` line; those that resolved an ambiguity also carry a `DECISIONS.md`
entry.

1. **Customer-supplied models → v0.8.0.** The blessed ONNX adapter and the Python entry-point
   escape hatch per `SCORER-PLUGINS-0.8-DRAFT.md`, including the worker-process preemption harness
   that v0.6.0's `SafeScorer` names as a blocking prerequisite (SECURITY-REVIEW-0.6 F25, listed
   **partial**). Refined here, built there. **Resist implementing the plugin draft.**
2. **True multi-tenant isolation** — per-tenant learning, per-tenant situation boundaries, and the
   cardinality/quota accounting that goes with it. Scoping is presentation only; isolation is a
   distinct, larger feature. Named in the governance docs as the thing scoping is *not*.
3. **External identity providers / SSO / SCIM / MFA / group-based provisioning.** Principals stay
   locally managed; a stored policy references existing local principals and the three fixed roles.
4. **Dynamic *roles*** — new role names beyond viewer/editor/admin, or a role-authoring UI. The
   three roles and their ceiling stay compiled in; only the *restriction* of what each role or
   principal may do and see is data-driven (DECISIONS #56).
5. **Per-field scoping policies** — an admin choosing which *fields*, not which *NEs*, a role sees.
   Field shaping stays the compiled `shaping.py` policy; scoping restricts *which resources* are
   visible, not *which fields* (DECISIONS #59).
6. **SNMPv3, `/metrics`, pcap replay, outbound webhook / `Case` JSON emission** — still out, as in
   every release since v0.2.0.

---

## Anti-overengineering rules (a violation is a build failure)

1. **Zero new runtime dependencies.** Dev/CI tooling only, justified in `DECISIONS.md`.
   Expectation: the runtime dependency list ends this release exactly as it started, at five.
2. **Empty-policy parity is inviolable.** No stored policy ⇒ byte-identical to v0.6.0. The parity
   and eval gates may never be weakened to accommodate a feature.
3. **Ingestion is sacred.** Governance is HTTP-side only. No change to the engine, the learning,
   the scoring seam, or `datagram_received`. (The S0 unification is a structural extraction inside
   the correlator's *candidate selection*, gated by byte-identical `make eval`.)
4. **The ceiling is the hard maximum; the stored policy is a subset within it.** Escalation must be
   structurally impossible — an intersection — not merely checked.
5. **One resolver for authorization, one for scope.** No second decision site; layer on
   `rbac.py`/`shaping.py`; no scattered role/NE checks.
6. **Scoping is presentation, not tenant isolation.** Do not build per-tenant learning or situation
   isolation; do not touch the learned matrices.
7. **One process, one SQLite file, one static UI, env vars only, no build step; admin is never
   scoped** (never a lockout).
8. Preserve git history on every move (`git mv`); never renumber existing ADR/finding entries; no
   feature outside the workstreams above.

## Definition of done

- `make qa` green; **all 426 pre-existing tests still pass**; `make eval` byte-identical.
- Empty-policy parity proven by an explicit governance-parity test, not merely asserted.
- A property-based ceiling-invariant test over generated policies (including adversarial and
  malformed ones) finds no resolved capability outside the role's ceiling.
- The preview↔engine partition-parity test passes.
- Every `F…` finding in `SECURITY-REVIEW-0.7.md` has a passing `test_f<N>_*` regression test.
- Migration `0006` applies to a populated v0.6.0 DB with data intact, the audit chain verifying,
  **no governance rows seeded**, and ships in a freshly built wheel *and* sdist (F12).
- Coverage ≥ **92.24 %** (the v0.6.0 figure, 95.24 %, minus three points).
- A documentation test asserts the "scoping is not tenant isolation" statement is present in the
  governance docs **and** in the UI copy.
