# Build report — NetCoreNOC v0.7.0 ("governance")

**One sentence:** an admin can now define what each role and principal may *do* and may *see*, as
stored and audited policy read through the perimeter's existing single decision points — and with
no policy stored, the appliance is byte-identically v0.6.0.

## What changed

| Workstream | Priority | State |
|---|---|---|
| **0. Close-out from v0.6.0** — one candidate-selection rule for engine and preview | P0 | ✅ shipped **first** |
| **1. Admin-configurable RBAC** — capability restriction within a fixed ceiling | P0 (+P1 per-principal) | ✅ shipped, both layers |
| **2. Visibility scoping** — which NEs a viewer/editor may see | P0 (+P1 per-principal) | ✅ shipped, both layers |
| **3. Security review** — F27–F33 | P0 | ✅ closed, all **met** |
| **4. v0.8.0 terrain preparation** — specification refinement only | P0 | ✅ §R1–R5, nothing built |

Nothing outside these. Everything else is a `docs/ROADMAP.md` line.

## The ceiling model, and why it is a proof rather than a check

`GOVERNANCE-0.7-DRAFT.md` specified an `rbac_grant` table whose above-ceiling rows are "**rejected
at write time**". That is a *validation check*: correct only while the check is present, reachable,
and applied to every write path — including ones that do not exist yet.

v0.7.0 resolves capabilities as an **intersection** instead (DECISIONS #53):

```
resolved(principal) = ceiling(role) ∩ granted(role) ∩ granted(principal)
```

with the compiled `PERMISSIONS` map as the **first operand**. An intersection cannot exceed its
first operand. A policy row naming an above-ceiling capability is therefore **inert** — not
"rejected", *inert* — regardless of how it entered the table: through the API, through a future
second write path, through a bad migration, or through `sqlite3` on a stolen or restored database
file. The API still returns a 400 naming each above-ceiling line, but that is a **usability
affordance so an admin learns immediately**, not the security control.

The difference is testable, and that is the point. "Escalation is forbidden" can only be checked
against the inputs a test happens to send. "Escalation is impossible" is a **property**:

```python
resolve_capabilities(role, ref, policy) ⊆ ceiling(role)     # for every input
```

asserted property-based over 250 generated policies whose strategies deliberately include
above-ceiling capabilities, unknown roles, unknown capabilities, empty strings and a
wildcard-looking principal — none special-cased anywhere — and separately against a policy inserted
straight into the database, bypassing the API entirely.

Every stricter clause of the draft survives unchanged: an "undelegable" capability is simply one
whose ceiling is `admin`, and no intersection can move it down.

**One escape hatch was needed, and it does not weaken the bound.** A *well-formed* policy could
otherwise remove `rbac.write` from the `admin` role, leaving no authenticated path to repair the
perimeter — a hard lockout the malformed-policy fallback would never catch, because the policy is
not malformed. A small compiled `RECOVERY_CAPABILITIES` set is unioned back for admin **inside the
resolver** (DECISIONS #64). Since it is a subset of `ceiling("admin")` — asserted at import — the
union stays inside the ceiling and the invariant is untouched. Governance can still take
`users.manage`, `audit.read`, `config.write` or `scorer.write` away from an admin; it simply cannot
brick the appliance.

## The scope model, and the limit it does not cross

A scope is a set of selectors (`ne:<id>`, exact address, CIDR, name glob) resolved to NE ids **on
every request** — NetCoreNOC discovers NEs continuously, so a write-time snapshot would silently
hide an NE whose address a CIDR plainly covers, and the failure would look like a correlation bug
(DECISIONS #57).

Three rules carry the whole design:

1. **Admin is never scoped** (DECISIONS #58), checked before any policy is read. This is what makes
   every fail-closed branch in the release recoverable rather than terminal.
2. **Unset means "no opinion"; set — even to nothing — means "exactly these"** (DECISIONS #54, #63).
   Both layers unset ⇒ every NE ⇒ parity.
3. **Fail closed, never fail open.** An unreadable scope shows viewers and editors *nothing*.

Enforcement is one filter composed at every read as **authorize → read → scope-project →
field-shape**. An out-of-scope resource returns **404 produced by the projection returning
nothing**, so the handler's *existing* not-found branch fires: "not yours" and "does not exist" are
one code path — same status, same body, same timing — rather than two that happen to agree today
(DECISIONS #60).

### ⚠ Scoping is a presentation control and is **not tenant isolation**

Stating this is part of the deliverable, and a documentation test asserts it in seven places
(`SCOPE-0.7.md`, `DESIGN.md`, `threat-model.md`, `README.md`, `MIGRATION.md`, the shipped
`ui/index.html`, and `shaping.py`) so it cannot be quietly dropped.

Correlation still learns across **all** NEs — the class and NE affinity matrices are global, and
feedback from any operator still moves them. A situation may still **form** across a boundary a
principal cannot see; scoping hides its members after the fact, it does not prevent it. Situation
ids, timing, and learned edge weights are global by construction.

The consequence is operational, not just theoretical: **a scoped operator sees a partial picture and
could mis-size a cross-boundary incident.** That is precisely why out-of-scope members are redacted
to a **count and their alarm classes** rather than silently omitted (DECISIONS #59). Showing "3
alarms" for a 40-alarm fibre cut would leave an operator *confidently wrong*; showing "3 alarms,
+37 outside your scope" leaves them aware they are looking at an edge. The residual — cardinality is
itself information — is recorded in the review rather than pretended away.

## The v0.6.0 close-out, and what it actually removed

The debt was **not** the duplicated loop. It was the duplicated *numbers*:
`preview.PREVIEW_WINDOW_S` and `PREVIEW_MAX_CANDIDATES` were independent literals that *happened*
to equal the engine's, maintained by hand. Changing `correlate.WINDOW_S` alone would have left the
what-if replaying a different window from the engine it claims to predict, and a preview that lies
is worse than no preview.

`correlate.select_candidates()` is now the single implementation, and preview's bounds are
**aliases** (`is`-identical, asserted). The one genuine difference between the callers is named as a
parameter rather than papered over: the engine's deque carries **tombstones** (alarms cleared or
re-activated, dropped from `index` in O(1) but still physically present), so it passes that index as
the liveness set; preview replays an immutable snapshot where every entry is live.

Two tests, at two levels: **behavioural** — preview reproduces the engine's *actual situation
partition*, member for member, on a stream straddling the ~21 s cold-start link radius and the 120 s
window edge, and over hypothesis-generated streams; and **structural** — both callers go through the
shared helper, asserted over their source. A parity test alone is what v0.6.0 had; it proves
agreement *today*. This proves they cannot drift.

It landed **first**, before any scoping code, because layering a disclosure control on two
implementations that may disagree is how an existence oracle gets built by accident.

## Numbers

| | v0.6.0 | v0.7.0 |
|---|---|---|
| Tests | 426 | **499** (+73) |
| Coverage | 95.24 % | **95.43 %** (floor was 92.24 %) |
| `make eval` | baseline | **byte-identical** — `pairwise_f1 1.0000`, `ari 0.9999`, `root_top1 1.0000`, no gated regressions |
| Runtime dependencies | 5 | **5** |
| Capabilities | 24 | 28 |
| Routes in the authorization map | 35 | 39 |
| Audit actions | 27 | 30 |
| Migrations | 5 | 6 |
| `rbac.py` coverage | — | **100 %** |

`ruff`, `ruff format`, `mypy --strict` (70 files), `vulture`, `bandit`, `pip-audit`, structure
guard, link check, SHA-pin lint, d3 checksum: all clean.

## Decisions (#53–#64)

| # | Decision |
|---|---|
| 53 | The stored capability policy is an **intersection** with the compiled ceiling, not a validated grant table |
| 54 | **Unset** ⇒ the whole ceiling (parity); **set-but-empty** ⇒ nothing |
| 55 | Malformed **capability** policy ⇒ fall back to the ceiling; malformed **scope** policy ⇒ deny |
| 56 | The three roles stay compiled in; only their *restriction* is data-driven |
| 57 | Scope selectors resolve to NE ids **at read time**; the two layers union |
| 58 | **Admin is never scoped** |
| 59 | Out-of-scope situation members are **redacted to a count and type**, never silently omitted |
| 60 | Out-of-scope ⇒ **404**, produced by filtering the lookup itself |
| 61 | One shared candidate-selection helper; preview's bounds become **aliases** |
| 62 | Per-principal policy keys on `user:<id>` / `token:<id>`, not on a display name |
| 63 | An **unset** scope layer expresses no opinion; visible = the union of the layers that do |
| 64 | The admin's recovery capabilities are unremovable — structurally, inside the resolver |

## What v0.8.0 will build (specified here, not built)

`SCORER-PLUGINS-0.8-DRAFT.md` §R1–R5, refined now that governance and the scoring seam exist:

- **R1** — the shipped v0.6.0 `LinkScorer` contract re-checked element by element against the code:
  **no breaking change needed** for either the ONNX adapter or the entry-point scorer.
- **R2 — ⛔ the worker-process preemption harness is a blocking prerequisite.** `SafeScorer` is a
  *post-hoc* guard: it measures a call after it returns and degrades the **next** one. Against five
  floating-point operations that is right; against untrusted code that never returns there is no
  next call, the engine batch loop blocks, and the ingest path — the one thing this project promises
  is lossless — starts dropping traps. `signal.alarm` is explicitly rejected as a fix (main-thread
  only, cannot interrupt a C extension, which `onnxruntime` is). Recorded with the constraints the
  harness must satisfy so it does not become its own hazard, and with the standing statement that it
  is **not** a sandbox: a plugin is as trusted as the operator who installed it.
- **R3** — governance reconciliation: activating a customer scorer stays `scorer.write`, admin-only,
  and v0.7.0 makes that survive **structurally** (no `ceiling ∩ policy` result can move an
  admin-ceiling capability down). A customer scorer is a system-wide logic change and is therefore
  **never scoped and never delegated**.
- **R4** — `onnxruntime` stays an optional extra; the base list has been five since v0.2.0.
- **R5** — sequencing: harness first, then the attribution store, then ONNX, then the entry-point
  hatch. Its review opens at **F34**.

## Deferred (ROADMAP lines, not silent scope)

Customer-supplied models → v0.8.0 · true multi-tenant isolation · custom roles · per-field scoping
policies · SSO/SCIM/MFA · scope-aware notifications · materialised scope resolution with
invalidation · carrying a per-principal policy across a token rotation.

## Honest caveats

- **Scoping is presentation, not isolation** (above). Global learned state, global situation ids and
  shared timing mean a determined observer can still infer *that* activity exists beyond their
  boundary from aggregate behaviour. Only true isolation closes this.
- **A scoped operator sees a partial picture.** The redacted count and classes are a mitigation of a
  real hazard, not its removal.
- **Cardinality is information.** The redaction discloses *how many* members are out of scope — less
  than the situation id and `updated_at` a viewer already sees, but not zero.
- **The two fail-safes are deliberately asymmetric.** A bad capability policy is *more permissive*
  than intended (falls back to the ceiling); a bad scope policy is *more restrictive* (denies). Both
  warn and audit, so neither is silent — but an operator who writes a capability policy to restrict
  and mistypes it gets the restriction silently not applied, protected only by the warning they must
  read. Failing closed instead would deny the admin the ability to repair it, which is the worse
  trade (DECISIONS #55) — but it is a real sharp edge.
- **The write-time validator can lull.** It reports above-ceiling entries as having "no effect",
  which is true, but a policy can read as doing more than it does. Mitigated by `/api/rbac`
  returning each role's **resolved** set beside its ceiling.
- **A compromised admin can rewrite the perimeter.** Bounded by the compiled ceiling, append-only,
  attributable, reversible by pointer, and audited — but not prevented, because an admin governs by
  definition.
- **Policy history grows one row per change and is never pruned.** Bounded and immutable, the same
  trade already accepted for `scorer_config`.
- **Per-request policy reads add a query to every authenticated request** (one SELECT over a two-row
  table, plus an NE listing when a scope policy is active and the caller is not an admin). Noise at
  this scale; a ROADMAP line if an NE table ever grows past it.

## Three things the gates caught in my own work

Recorded because a build report that only lists successes is not evidence of anything.

1. **A second decision site, in code I had just written.** `scope_for()` in `api.py` began as
   `if principal.role == "admin"` — the admin-never-scoped short-circuit, and exactly the role
   comparison F28 exists to forbid: an authorization-relevant rule outside the resolver, invisible
   to the generated matrix. The source-level assertion failed on it. Moved to
   `shaping.is_scopable()`; `api.py` now contains no role literal at all.
2. **A claimed control with no test.** The Phase-1 review skeleton named an SSE re-evaluation test
   that did not exist — the behaviour was implemented but unproven. The test was written (driving
   the ASGI app directly, since httpx cannot deliver partial bodies of an infinite response); the
   claim was not softened to match the evidence.
3. **A per-row database lock inside a listing.** The first scoped `/api/situations` took
   `store.lock` once per returned row. Replaced with one query for the whole page, returning a list
   rather than a set so the honest visible/redacted counts survive.

Two pre-existing tests were also corrected rather than worked around: one hard-coded
`user_version == 5`, and one froze the migration directory by *name prefix*, so `0006` leaked into
what it calls "a genuine v0.5.0 database". Both now track the migration **number**, so a future
`0007` cannot un-freeze them either.

## Delivery

The repository is read-only to automation. Every gate is local and reproducible on the maintainer's
machine: `make qa`, `make eval`, `docker compose config`, and a locally built wheel and sdist (both
verified to carry `0006_governance.sql`, with the wheel installed into a clean venv and confirmed to
migrate to schema 6 and seed zero policy rows). No step depended on pushing, on CI, or on any
external account. `tools/release_check.py` confirms `pyproject.toml`, `src/netcorenoc/__init__.py`
and `CHANGELOG.md` all agree on **0.7.0**.
