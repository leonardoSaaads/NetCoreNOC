# Extensibility — v0.6.0 draft (specification only, not implemented in v0.5.0)

> ## ⚠ Superseded in place by v0.6.0 — read this box first
>
> This document was written in v0.5.0 and specified **three** surfaces for v0.6.0. v0.6.0 built
> **one** of them and resequenced the other two (**DECISIONS #43**). The text below is preserved
> unchanged as the historical record; where it conflicts with the disposition table, the table
> wins.
>
> | § | Surface | Disposition |
> |---|---|---|
> | 1 | Admin-configurable RBAC | **→ v0.7.0.** Specified in [`GOVERNANCE-0.7-DRAFT.md`](GOVERNANCE-0.7-DRAFT.md) |
> | 2 | Per-role / per-principal visibility scoping | **→ v0.7.0.** Same spec, which additionally states the mandatory limit: scoping is a *presentation* control and **not** tenant isolation |
> | 3 Tier A | Configurable scoring parameters | **BUILT IN v0.6.0** — see [`DESIGN.md`](DESIGN.md) "v0.6.0 — the scoring seam" and [`../scope/SCOPE-0.6.md`](../scope/SCOPE-0.6.md) |
> | 3 Tier B | External API supplying the linking criterion | **REJECTED** on the correlation hot path (**DECISIONS #44**). Never authoritative in `score()`; a ROADMAP line and a threat-model note, not a plan |
> | — | Customer-supplied models (ONNX / Python plugins) | **→ v0.8.0.** Specified in [`SCORER-PLUGINS-0.8-DRAFT.md`](SCORER-PLUGINS-0.8-DRAFT.md) |
>
> Three corrections the reader should carry into the text below:
>
> 1. **§3 Tier A says parameters are "editable by admin (optionally editor, if the admin
>    delegates)". v0.6.0 deliberately did not do that.** `scorer.preview` and `scorer.write` are
>    **admin-only with no editor delegation**: retuning the formula is a system-wide logic change,
>    and security-relevant ambiguity resolves toward the stricter option. `scorer.read` is
>    viewer+, because the active parameters *explain* grouping and are not a secret.
> 2. **The "P2 tidy" (moving `W_T`/`W_A`/`W_E` into the dataclass) was indeed the first step of
>    v0.6.0** — confirmed not-applied in `../gates/v0.6-phase-0.md` §5, then completed as part of
>    extracting `src/netcorenoc/scoring.py`, proven byte-identical.
> 3. **v0.6.0 went further than "make the parameters configurable"**: the formula became the
>    default implementation of a versioned `LinkScorer` interface, with contractual per-term
>    explainability, persisted decision provenance, read-only preview, and one-click rollback.
>    That interface is what v0.8.0's customer models plug into.

This document specifies what **v0.6.0** will make configurable, and confirms that the ground is
already clean for it. **It implements nothing.** It is written now, in v0.5.0 (the
organization/structure release), following the project's proven "spec-now-implement-later"
pattern (`docs/architecture/CASE-SCHEMA-DRAFT.md`): specifying the extension points before
touching them turns v0.6.0 into "make these three surfaces data-driven" rather than "find
them, then redesign them."

Every element below is tagged **`v0.6.0: planned`**. v0.5.0 changes none of the three surfaces'
behaviour; the correlation engine, the RBAC map, and the response serializer are byte-for-byte
what v0.4.0 shipped.

## What v0.6.0 will let an admin do

1. **Define what viewers and editors may do and see** — admin-configurable per-role
   *capabilities*, and per-role/per-principal *visibility scoping* (which hosts / IPs / NEs a
   viewer or editor may see).
2. **Change the correlation match formula** — move away from the fixed
   `s = 0.3·e^(−Δt/30) + 0.35·A + 0.35·E > 0.5`, with bounded/validated parameters, and
   (optionally, if the admin enables it) an **external API** that supplies the linking
   criterion. The admin always holds this; the editor holds it only if the admin delegates it.

## The three surfaces are already well-placed

v0.6.0 does **not** need to find or refactor these; they are single, named sources today. The
work is "make each data-driven," not "locate it."

| Surface | File | What is there today | v0.6.0 makes it |
|---|---|---|---|
| RBAC permission map | `netcorenoc/rbac.py` | `PERMISSIONS` (capability → min role), `ROUTE_PERMISSIONS` (route → capability), `PUBLIC_ROUTES`, `AUDITED_DENIED_PERMISSIONS`; deny-by-default; single source read by `api.security` | admin-configurable per-role capabilities, within a fixed role ceiling |
| Visibility serializer | `netcorenoc/shaping.py` | `FIELD_RULES` (field → (min role, coarsen\|drop)) driving `shape(obj, role)`, the one place a response body is projected by role | a stored per-role/per-principal *resource-scope* policy generalising the field rules |
| Scoring parameters | `netcorenoc/correlate.py` | named constants `W_T`, `W_A`, `W_E`, `TAU_S`, `LINK_THRESHOLD`; `tau` and `threshold` are already `Correlator` dataclass instance fields read from `self` in `score()`/`process()` | a validated, audited, hot-path-safe configurable formula, with an optional pluggable external criterion |

**P2 tidy (not done in v0.5.0):** the one code change v0.6.0's parameter work needs is to move
`W_T`/`W_A`/`W_E` into the `Correlator` dataclass exactly as `tau`/`threshold` already are, so
`score()` reads `self.w_t` etc. v0.5.0 deliberately leaves `correlate.py` untouched (no
behaviour change is the rule; if there is any doubt, do not touch the scoring path) — the surface
is documented here and the tidy is the first, byte-identical step of v0.6.0.

---

## 1. Admin-configurable RBAC (`v0.6.0: planned`)

Today a capability's minimum role is fixed in `PERMISSIONS`. v0.6.0 lets an admin adjust, per
role, which capabilities a role holds — **without weakening any invariant**.

### Invariants preserved (non-negotiable)

- **Single source of truth.** The stored capability grants are read through the *same*
  `rbac.role_allows` / `permission_for` path; there is never a second authorization table. The
  in-code `PERMISSIONS` becomes the **default seed** and the **role ceiling**, not a bypass.
- **Deny-by-default.** An unmapped route still fails closed (the existing
  `test_every_api_route_has_permission` guarantee). A capability with no stored grant falls back
  to its coded default; a role with no grant for a capability is denied.
- **401 / 403 / 404 semantics unchanged.** Missing identity → 401; authenticated-but-insufficient
  → 403; resource lookup happens only *after* authorization, so 403 never leaks existence.
- **Every capability change is audited.** A new audit action (e.g. `rbac.grant` / `rbac.revoke`,
  system/admin actor, before/after in `details` with no secret) writes one append-only row per
  change, exactly as `config.write` does today.

### The fixed role ceiling (the escalation guard)

A stored grant can only ever **narrow or widen within the fixed role ceiling** — it can never let
a config write silently escalate privilege beyond what the role model already permits, and it can
never grant a capability above the role's ceiling. Concretely:

- The coded `PERMISSIONS` (viewer ⊆ editor ⊆ admin) defines, per capability, the **highest role**
  that a grant may assign it to and the set of capabilities a role may ever hold. A stored grant
  that tried to give `viewer` an `admin`-ceiling capability (e.g. `users.manage`, `audit.*`,
  `config.write`) is **rejected at write time** and audited as denied.
- **Sessions re-evaluate; a config write never mutates a live session's authority in flight.**
  Authorization is computed per request from the current stored policy, so a narrowing takes
  effect on the next request. Where a change amounts to a role change for a principal, the
  existing revocation applies — **role changes already revoke that user's sessions**
  (`revoke_user_sessions`), so a de-privileged principal cannot ride an old session.
- The **audit log capabilities** (`audit.read/export/prune`) and **user/token management**
  (`users.manage`, `tokens.manage`) remain admin-ceiling and cannot be delegated downward by a
  grant — an admin who could delegate audit-tamper visibility or user creation would defeat the
  accountability model. (Security-relevant ambiguity resolves toward the stricter option.)

### Threat-model entries v0.6.0 must add

- **Privilege escalation via a stored grant** — control: ceiling validation at write time +
  audited change + per-request re-evaluation + session revocation on effective role change;
  test: a grant above the ceiling is rejected and audited; a narrowed principal is denied on the
  next request and its sessions are revoked.

---

## 2. Per-role / per-principal visibility scoping (`v0.6.0: planned`)

Today `shaping.py` coarsens or drops *fields* by role. v0.6.0 generalises it to a stored policy
that scopes *resources*: which NEs / IPs / hosts a given role (or an individual principal) may
see at all.

### Shape of the stored policy

A stored, admin-managed policy mapping a **subject** (a role, or a specific principal) to an
allowed **resource set** (CIDRs / NE ids / host globs). It is read on every resource-listing and
resource-detail path and composed with the existing field-level `shape()` — field coarsening and
resource scoping are two layers of the same deny-by-default serializer, not scattered checks.

### Invariants (fail closed, never an existence oracle)

- **Fail closed by default.** An **unscoped principal sees nothing new** — i.e. the *current*
  v0.4.0 behaviour is exactly the "no scope policy configured" case: every viewer/editor sees the
  full set the route already allows. Introducing a scope can only *restrict*. A principal that a
  policy references but grants no resources to sees an **empty** result set, never an error that
  reveals the resource count.
- **Never leak existence of out-of-scope resources.** A request for a specific out-of-scope NE
  returns **404, not 403** — consistent with "authorization precedes resource lookup," so scoping
  never becomes an oracle for which NEs exist. Listing endpoints omit out-of-scope resources
  entirely (they are not rendered-then-hidden).
- **Audited on change.** Every scope-policy edit writes an append-only audit row (subject,
  before/after resource set summary — counts/CIDRs, never trap payloads).
- **Full fidelity preserved server-side.** Like field shaping today, the engine, store, and audit
  log keep every NE; scoping is presentation-time projection only. Correlation still learns across
  all NEs — scoping is a *visibility* control, not an ingestion or learning filter (the hot path
  is untouched).

### Threat-model entries v0.6.0 must add

- **Existence oracle via resource scoping** — control: 404 (not 403) for an out-of-scope
  resource, list-omission for collections; test: an out-of-scope detail fetch is indistinguishable
  from a nonexistent one.
- **Fail-open scope misconfiguration** — control: no policy ⇒ current behaviour; a referenced-but-
  empty scope ⇒ empty set, not full set; test: default-deny on an empty scope.

---

## 3. Configurable / pluggable match formula (`v0.6.0: planned`) — hard security framing

The linking score is
`s = w_t·e^(−Δt/τ) + w_A·A[c_i,c_j] + w_E·E[e_i,e_j]`, accepted when `s > threshold`. v0.6.0
makes this configurable in two tiers of increasing risk. **The built-in formula stays the
default and the always-available safe fallback** at every tier.

### Tier A — parameters (weights, τ, threshold): a logic-change surface

- **Editable by admin (optionally editor, if the admin delegates).** These are not cosmetic —
  changing them changes which alarms group — so they are treated as a **logic-change surface**:
  bounded and validated ranges (e.g. `0 ≤ w_* ≤ 1` with a bounded sum, `τ > 0` within a sane
  window, `0 < threshold < 1`), rejected outside the bounds, and **audited on every change**
  (before/after in `details`).
- **The ingestion hot path stays untouched.** Parameters are read exactly where `tau` and
  `threshold` already are — instance fields on the `Correlator`, read in `score()`/`process()`,
  which run in the engine batch, **not** in `receiver.datagram_received`. Applying a new parameter
  set is a maintenance-side swap of the values the engine already reads; the datagram callback
  gains no lock, no I/O, no config read (invariant 2). `W_T`/`W_A`/`W_E` become instance fields in
  the P2 tidy above so all five parameters live in one validated place.
- **Fallback.** An invalid or absent parameter set falls back to the coded defaults
  (`W_T`, `W_A`, `W_E`, `TAU_S`, `LINK_THRESHOLD`) — the engine never runs with an unvalidated
  formula.

### Tier B — an external API supplying the criterion: the riskiest idea in the roadmap

An external service that supplies (or overrides) the linking criterion is a
**code-execution / SSRF / trust surface** and is specified with the **strictest controls**. It is
explicitly the riskiest element of the v0.6.0 roadmap and gets treated as such.

- **Opt-in, off by default.** Absent configuration ⇒ the built-in formula, unchanged. Enabling it
  is a deliberate, audited admin action.
- **Allowlisted destinations only.** Outbound calls go only to an admin-configured allowlist of
  destinations (host + port + scheme); anything else is refused before a socket is opened. This is
  the SSRF control — no attacker-influenced URL, no metadata-endpoint reach, no internal-network
  pivot.
- **Hard timeouts and bounds.** Every call has a hard timeout and a bounded, **validated response
  contract** (a typed, size-limited score/verdict; anything malformed, oversized, or late is
  rejected). No arbitrary code, no deserialization of executable content — JSON only, validated
  like every other input.
- **Fail-safe fallback.** On **any** error, timeout, refusal, or contract violation, the engine
  **falls back to the built-in formula** for that decision and records the failure. The external
  criterion can never stall or break correlation; the safe default is always reachable.
- **Never on the datagram path.** The external call is **never** made in
  `receiver.datagram_received` and never under the trap-ingestion critical section. It lives on the
  engine/maintenance side where a bounded wait is tolerable; ingestion stays lossless and
  lock-free (invariant 2). A per-decision network call at trap rate would itself be a DoS — so it
  is bounded, cached where valid, and off the hot path by construction.
- **Fully audited.** Enablement, the allowlist, each configuration change, and each fallback event
  are audited.

### Threat-model entries v0.6.0 must add

- **SSRF via the external criterion API** — control: destination allowlist enforced before
  connect; test: a non-allowlisted destination is refused and audited.
- **DoS / stall via the external criterion** — control: hard timeout + fail-safe fallback + off
  the datagram path; test: a slow/erroring endpoint never blocks ingestion and correlation
  proceeds on the built-in formula.
- **Untrusted response injection** — control: bounded/validated JSON contract, no executable
  deserialization; test: a malformed/oversized response is rejected and falls back.
- **Silent logic change via parameters** — control: bounded validation + audit on change +
  hot-path-safe application; test: out-of-bounds parameters rejected; a change is audited; the
  datagram path is unaffected.

---

## Summary — what v0.5.0 confirms, and what v0.6.0 builds

- v0.5.0 **confirms** the three surfaces are single, named, and clean, and **specifies** (here)
  how each becomes configurable with its invariants and its new threat-model entries. It builds
  none of it and changes none of their behaviour.
- v0.6.0 **builds** admin-configurable RBAC (within the fixed role ceiling, sessions
  re-evaluate, every change audited), per-role/per-principal visibility scoping (fail-closed,
  audited, 404-not-403), and the configurable formula (validated parameters off the hot path;
  the external criterion API opt-in, allowlisted, hard-timeout, fail-safe, off the datagram path,
  fully audited), and adds the threat-model entries listed under each section.

---

## What actually happened (added in v0.6.0 — supersedes the summary above)

The summary above is the v0.5.0 *plan*. The v0.6.0 *outcome*, per DECISIONS #43 and #44:

- v0.6.0 built **the scoring surface only**, and built more of it than this draft asked for: the
  formula became the default implementation of a versioned `LinkScorer` **interface**
  (`src/netcorenoc/scoring.py`) with contractual per-term explainability, a stable
  `params_fingerprint`, persisted decision provenance (`situation.scorer_config_id` into an
  append-only `scorer_config` table), an admin-only read-only **preview** with a structural
  partition diff, instant **rollback** by moving a one-row active pointer, and a fail-safe
  fallback to the coded defaults audited as `scorer.fallback`. At the default parameters the
  output is byte-identical to v0.5.0 — that parity is the gate the whole release rests on.
- v0.6.0 also removed the legacy `OPTICORR_*` environment aliases (DECISIONS #45), the removal
  promised two versions earlier.
- **Admin-configurable RBAC and visibility scoping did not ship**; they are v0.7.0 and are fully
  specified in [`GOVERNANCE-0.7-DRAFT.md`](GOVERNANCE-0.7-DRAFT.md), which also names the limit
  this draft did not: scoping is a presentation control, **not** tenant isolation.
- **The Tier B external criterion API was rejected outright** (DECISIONS #44) rather than
  deferred. `LinkScorer.score` is specified pure, deterministic, side-effect-free and
  inference-only, which forecloses an outbound call at the type level. Customer-supplied models
  reach the same goal without a socket and are specified for v0.8.0 in
  [`SCORER-PLUGINS-0.8-DRAFT.md`](SCORER-PLUGINS-0.8-DRAFT.md).

The threat-model entries this draft listed for §1 and §2 move with them to v0.7.0. The §3 Tier A
entry ("silent logic change via parameters") was added to
[`../security/threat-model.md`](../security/threat-model.md) in v0.6.0; the Tier B entries (SSRF,
DoS via the external criterion, untrusted response injection) are recorded there as
**rejected-by-design**, which is a stronger statement than a control.
