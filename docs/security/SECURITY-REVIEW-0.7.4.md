# Security review — NetCoreNOC v0.7.4

**Scope of this review: the route-declaration gate, and a structural split of the authorization
authority.** Two confirmed defects, **F40** and **F41**, continuing the F1–F39 series. Both were
found by adversarial probing of `api/declare.py` during the v0.7.2 review, **reproduced by
execution** against the untouched v0.7.3 tree (`docs/gates/v0.7.4-phase-0.md` §2), and turned into
regression tests that **fail on the unmodified tree** before either fix was written
(`docs/gates/v0.7.4-phase-2.md` §3).

Neither was exploited on the v0.7.3 surface. Both were latent holes in a guard whose entire value
is completeness, and this review is as much about why they were fixed the way they were as about
that they were fixed.

This release adds **no route, no capability, no audit action, no migration, no runtime dependency
and no served path**. `PERMISSIONS` (28), `ROUTE_PERMISSIONS` (39), `PUBLIC_ROUTES` (1),
`ROUTE_SCOPE` (39), `AUDITED_DENIED_PERMISSIONS` (14) and `audit.ACTIONS` (30) are unchanged.
`make eval` is byte-identical to v0.7.3 (`sha256 c333ca46…3132`).

---

## 1. Completeness by construction, not by enumeration

Both findings are the same defect at two levels: **a guard that lists the cases it knows about**.

`DeclaredRoutes` listed three verbs and one registration style. `require_declaration` listed one
path prefix. Each was true of the surface in front of it and silent about the next thing anyone
would write — and a guard whose value is *"nothing gets past this"* is worth exactly as much as its
least-covered path.

The fix in both cases replaces a list with a property:

| | v0.7.3 — enumeration | v0.7.4 — construction |
|---|---|---|
| **F40** | wrap `get`, `post`, `delete`; hope nobody registers another way | assert over **`app.routes`** — whatever produced the route, the route is there |
| **F41** | `if not path.startswith("/api"): return` | consult an **allowlist of what is actually served**, asserted against the code that serves it |

The distinction matters more than the two holes do. Wrapping `put` and `patch` would have closed
both reproductions and left the property no stronger; a reviewer would still have to ask "is that
all the ways?" and would still have no way to answer.

---

## 2. Findings — F40, F41

| # | Sev | Area | Finding / property asserted | Fix / control | Test | Status |
|---|-----|------|------------------------------|---------------|------|--------|
| F40 | **Med** | Declaration gate incomplete — registration paths outside the gate | `DeclaredRoutes` wraps `get`, `post` and `delete`, and only the decorator form. A route registered directly on the FastAPI application never calls `require_declaration`. Reproduced: registering `GET /api/undeclared-bypass` directly raised nothing, took the route count 4 → 5, and the route was absent from **both** `ROUTE_PERMISSIONS` and `ROUTE_SCOPE`; the decorator form was refused as designed (the control). `DeclaredRoutes` also has no `put`/`patch`, so those verbs had no gated path at all. Not exploited: v0.7.3 has no `PUT`, no `PATCH`, and exactly one direct registration, confined to the static-asset allowlist. The two existing guards cannot see it — `test_the_gate_is_the_only_registration_path` greps for `@app.<verb>` decorators, and `test_add_api_route_is_confined_to_the_static_asset_allowlist` asserts there is one caller **today**, which is true and does not prevent a second. | `declare.assert_every_route_is_declared(app)` re-checks **every route on the built application** through the same `require_declaration` the decorator calls, and `create_app` invokes it as its **last statement before `return app`** — so a mis-declared route stops the process rather than failing only under test. Complete by construction: the function names no verb and no registration mechanism, so a path nobody has written yet is still caught. The decorator-time refusal is **kept as well** (build prompt directive 4) — two checks, one complete and one with the better error message. | `test_f40_a_route_registered_without_the_decorator_fails_create_app`; `…_by_an_unwrapped_verb_…[put]`, `[patch]`; `test_f40_the_assertion_runs_before_create_app_returns`; `test_f40_the_decorator_time_refusal_is_kept_as_well` | **met** |
| F41 | **Med** | Declaration-gate exemption by path prefix rather than by absence of capability | `require_declaration` returned early for any path not starting with `/api`. True of today's public surface — `/healthz`, `/readyz`, `/`, four static assets — and **accidentally** true of anything else that ever sits outside `/api`. Reproduced: `require_declaration("GET", "/metrics")` and `("GET", "/admin/debug")` both returned without raising, while `("GET", "/api/undeclared")` raised. `/metrics` is already on `docs/ROADMAP.md`, and an authenticated `/metrics` would have been exempt from the gate **by accident** — declaring neither a capability nor a scope posture, and invisible to the route-map completeness tests, which only enumerate `/api`. | The prefix test becomes `declare.UNAUTHENTICATED_PATHS`, an explicit frozenset. Membership is now a **reviewable claim** — "no capability is required to fetch this" — instead of a consequence of four characters. Asserted against what is served from **two independent directions**: against `routes_static.STATIC_ASSETS` plus the health surface (source), and against every non-`/api` route on a **built application** (runtime). The second is not redundant: `/openapi.json` is registered by FastAPI itself and no source-level derivation over `routes_static.py` would ever mention it. | `test_f41_a_non_api_path_outside_the_allowlist_is_refused[/metrics]`, `[/admin/debug]`, `[/status]`, `[/vendor/other.js]`; `test_f41_the_allowlist_matches_what_is_actually_served`; `test_f41_the_allowlist_is_exactly_the_live_non_api_surface`; `test_f41_every_currently_served_public_path_is_still_accepted` ×8 | **met** |
| — | — | Authorization single-sourcing under a package split | `rbac.py` (436) became `rbac/`. A re-export that **copies** (`PERMISSIONS = dict(tables.PERMISSIONS)`) leaves every existing test green and creates a second source of authority that diverges on first mutation — and `tests/test_declaration.py` already mutates `rbac.ROUTE_PERMISSIONS` in a fixture. | Re-export by **identity**. Eight identity assertions plus an AST check that no module under `rbac/` except `tables.py` binds any table at module level. Both were **shown to fail** against a deliberately-copying `__init__.py` and (for the second) against a `policy.py`-local fallback, before being accepted. | `test_the_tables_are_re_exported_by_identity_not_by_copy` ×8; `test_no_module_but_tables_binds_an_authorization_table` | **met** |

---

## 3. The properties this review is required to assess

### 3.1 The gate is stronger and no narrower

**Every route that registered at v0.7.3 still registers.** The eight public paths and all 48
route-table entries are unchanged, in order.

| Assertion | State |
|---|---|
| `test_route_table_order_is_unchanged` — the whole 48-entry table, in order | **passes, unedited** |
| The generated authorization matrix — every route × every role, including anonymous | **passes, unedited** |
| `test_every_api_route_is_in_the_permission_map` | **passes, unedited** |
| `test_every_api_route_declares_a_scope_posture` | **passes, unedited** |
| `test_no_scope_declaration_is_dead` | **passes, unedited** |
| `test_the_three_postures_are_all_populated` (22 admin_only / 5 unscoped / 12 scoped) | **passes, unedited** |
| `test_f41_every_currently_served_public_path_is_still_accepted` ×8 | passes on **both** trees — the direction the fix must not change |

That last row is the one that matters for "no narrower": those eight cases pass against v0.7.3's
source as well as v0.7.4's, so they cannot be satisfied by having removed a public route.

**The new assertion runs before `create_app` returns**, not only under test. This was a deliberate
choice and it is asserted rather than trusted: `test_f40_the_assertion_runs_before_create_app_returns`
parses `create_app`'s own source and requires nothing but `return app` to follow the call. A guard
that runs in CI but not at startup is a guard an operator can ship past — the appliance would boot,
serve, and be wrong.

### 3.2 `rbac` still single-sources authorization

This is the release's highest structural risk, and the honest measurement is the one that makes the
case:

> **With a deliberately-copying `__init__.py` in place, 218 pre-existing tests pass green.**

That is the whole argument, measured rather than asserted. The generated authorization matrix, the
fail-closed tests, `test_f8_audited_denied_single_source`,
`test_admin_only_is_derived_from_permissions_in_both_directions` — none of them can tell a
reference from a copy, because equality holds at import and every one of them compares values.

The tests that prove single-sourcing survived, all **unedited**:

* `test_f8_audited_denied_single_source` — `api.DENIED_ACTION` is only a presentation mapping of
  `rbac.AUDITED_DENIED_PERMISSIONS`;
* `test_admin_only_is_derived_from_permissions_in_both_directions`;
* `test_f28_no_role_comparison_outside_rbac` — no authorization decision by role comparison anywhere
  in the `api` package;
* the generated authorization matrix;
* the three import-time asserts, which moved into `tables.py` **with the tables they constrain**. Had
  they stayed in `policy.py` they would have run after the tables were built rather than as part of
  building them — three structural guarantees silently deleted, with every test still green.

And two new ones hold the line the existing suite could not see (§2, row 3). Neither subsumes the
other: the identity check passes while `policy.py` shadows a table locally; the no-shadowing check
passes while `__init__.py` copies.

One finding from writing the sabotage is worth recording, because it nearly invalidated the proof:
the first attempt used `frozenset(PUBLIC_ROUTES)`, and **three of the eight tests passed** — CPython
returns the same object when `frozenset()` is called on a frozenset, so the "copy" was not a copy.
Corrected to `frozenset(set(...))` before the proof was accepted. A guard proved against a sabotage
that is not the defect proves nothing.

### 3.3 The F35 invariant survived the `shaping/` split

No input to `visible_nes()` is writable by a scopable role. The function moved to
`shaping/scope.py` as identical text, and **both comment blocks that state why moved with it** —
`visible_nes`'s statement that every input is admin-written or engine-written and that anything added
to the signature must satisfy the same test, and `_matches`'s explanation of why the operator label
is not read and what the v0.7.0 escalation was.

```
tests/test_governance.py::test_f35_no_resolver_input_is_writable_by_a_scopable_role   PASSED
tests/test_governance.py::test_f35_an_editor_cannot_widen_their_own_scope_with_a_label PASSED
```

Both **unedited**. `test_f32_scoping_is_not_tenant_isolation_is_documented` also passes; the file it
reads moved from `shaping.py` to `shaping/__init__.py`, and the warning is carried in both the
package docstring and `scope.py`.

### 3.4 No new surface

| | v0.7.3 | v0.7.4 |
|---|--:|--:|
| Routes (`ROUTE_PERMISSIONS`) | 39 | **39** |
| Public routes | 1 | **1** |
| Scope postures (`ROUTE_SCOPE`) | 39 | **39** |
| Capabilities (`PERMISSIONS`) | 28 | **28** |
| Audited-denied capabilities | 14 | **14** |
| Audit actions | 30 | **30** |
| Migrations | 7 | **7** |
| Runtime dependencies | 5 | **5** |
| Served non-`/api` paths | 8 | **8** |

The ingest path gained nothing: `receiver.py`, `events.py`, `correlate.py`, `scoring.py`,
`learn.py` and `engine.py` are **untouched**. The v0.6.0 F24 and v0.7.0 F33 source-level assertions
remain green, and `make eval` is byte-identical.

### 3.5 Upgrade integrity

A database written by **real v0.7.3 code** (extracted from the v0.7.3 commit and run against it),
then opened by the v0.7.4 wheel in a clean virtualenv:

```
written by netcorenoc 0.7.3      opened by netcorenoc 0.7.4
schema_version   7 (latest 7)    schema_version   7 (latest 7)
users 4  ne 3  situations 2      users 4  ne 3  situations 2
audit final hash 3d1bdf8a…79d9   audit final hash 3d1bdf8a…79d9
SNAPSHOT sha256  28f636fd…2e96   SNAPSHOT sha256  28f636fd…2e96

$ python -m netcorenoc audit verify
audit chain OK: 2 entries verified; final hash 3d1bdf8afd419a77ec6f263dddbd8ee2e8f402059c91d99d505710c6ffe179d9
```

No migration ran, the store snapshot is byte-identical, and the audit chain verifies to the same
final hash. **An operator upgrading from v0.7.3 has nothing to do.**

---

## 4. Critical analysis — honest residual risk

### 4.1 The gate is complete for *registration*. That is not correctness of what it guards.

This is the most important sentence in this review, and it is a limitation rather than an
achievement.

F40 and F41 make it impossible for a route to reach a running process without **declaring**
something. They say nothing about whether what it declared is **true**. Every `ROUTE_SCOPE` entry is
a human judgement — `"scoped"`, `"unscoped"` or `"admin_only"` — and the gate does not check it; it
checks that one is present.

The mitigation is real but partial: `tests/test_declaration.py` checks every declared posture
against the route's **observed behaviour** — an `unscoped` route must answer a scoped and an
unscoped caller identically, a `scoped` collection route's body must change under a policy, a
`scoped` targeted route must 404 an out-of-scope target. That is a behavioural check of the claim,
which is much stronger than a comment. But it is checked *after the fact*, on routes that exist, by
tests that must be written; and **`ROUTE_SCOPE` remains descriptive rather than injected**. The
perimeter does not enforce the declared posture; each handler still calls `scope_for` itself. That
was v0.7.2's deliberate choice (DECISIONS #80) because injection changes control flow and control
flow is behaviour — and it is still a `docs/ROADMAP.md` line, not a fix, because it remains true
that a release proving "nothing moved" cannot also move the control flow.

So the honest statement is: **a route can now not be undeclared; it can still be wrongly declared,
and the wrongness is caught by a test rather than by the structure.**

### 4.2 An empty `DEBT_ALLOWLIST` does not mean the codebase is well-factored

It means **no module exceeds a line count**, which is a proxy — and a coarse one. A 399-line module
doing three things passes; a 401-line module doing one thing fails. The guard's job is to force the
*question*, not to answer it (`MODULE-ARCHITECTURE.md` §2 says so explicitly), and this release has
answered the question for the three modules that were over the line, not for the codebase.

**What I would look at next, as an opinion rather than a commitment:**

1. **`api/perimeter.py` (361).** Under the guard and the single most security-relevant file in the
   tree — it is the whole HTTP security boundary, and `MODULE-ARCHITECTURE.md` §3 tells a reviewer
   to read it first. It is also the file whose size is most load-bearing in the *other* direction:
   the argument for keeping it whole is exactly `engine.py`'s, that a reviewer must be able to
   confirm the boundary without following imports. I would not split it. I would consider whether it
   deserves a `COHESION_EXEMPT`-style entry recording *why* it stays whole, so that a future
   contributor at 401 lines argues the invariant rather than reaching for the split.
2. **`scoring.py` (394) and `learn.py` (394).** Both one line under the guard, which is the position
   where the ratchet is about to become an obstacle rather than a signal. Neither is incoherent, so
   the honest response is not to split them pre-emptively — but the next feature that touches either
   will hit the guard, and it should not be that change's job to invent the seam under time
   pressure. Naming the seam now, in `MODULE-ARCHITECTURE.md`, is what v0.7.2 did for the three
   modules this release just closed, and it is why this release could *execute* rather than
   rediscover.
3. **`engine.py` (542).** Explicitly **not** on this list. `COHESION_EXEMPT`, permanently, and the
   empty debt allowlist is not an invitation to finish the job.

### 4.3 `/openapi.json` is served unauthenticated

Found while building F41's allowlist, and listed in `UNAUTHENTICATED_PATHS` because that set states
what **is** served, not what should be. FastAPI registers the schema route itself, with no security
dependency, so the full API surface — every path, every method, every request model — is readable
by anyone who can reach the port. `docs_url` and `redoc_url` are already disabled; `openapi_url` is
not.

This is **pre-existing** (v0.7.2 and v0.7.3 served it identically) and is **not changed here**:
directive 5 makes a defect noticed during a move release a ROADMAP line, and this release's parity
story forbids changing a served path. It is recorded on `docs/ROADMAP.md` and in the threat model.

The honest severity assessment: **low, and not nothing.** It discloses no data and no credential,
and every route it names is enforced by the authorization matrix — so this is not an access-control
bypass. It is reconnaissance value: it hands an unauthenticated attacker the exact shape of the
write surface, including the pydantic models, without a single guess. On an appliance whose
deployment guidance already assumes a trusted management network that is a small increment; on one
exposed more widely it is the first thing an attacker reads.

### 4.4 A guard in this release counts mentions, not calls

`test_add_api_route_is_confined_to_the_static_asset_allowlist` greps the **text** of every module
under `api/` for an identifier and asserts exactly one file contains it. Documenting the F40 fix in
`declare.py`'s docstring made the count three, and the test failed — on prose.

The assertion had to pass unedited, so the prose was reworded. The actual caller count is unchanged
at one. But a guard that cannot distinguish a call from a comment will, sooner or later, either fire
on a correct change (as it did here) or be silenced. An AST-based caller count is a few lines and
would say what the test means. **Not fixed here** — directive 5 — and on `docs/ROADMAP.md`.

### 4.5 What this release did not look at

The threat model's existing entries were re-checked for the two findings and otherwise unexamined.
This was a structural release with a security-adjacent workstream, **not** a full re-review of the
attack surface. The last full pass was v0.7.1's, against the write perimeter; v0.7.2 and v0.7.3
found nothing and looked at what they changed. Nothing here should be read as evidence that
F1–F39's controls were re-tested beyond what CI asserts on every commit.

---

## 5. Mapping to `threat-model.md`

| STRIDE | Threat | Control added or confirmed by v0.7.4 | Check |
|---|---|---|---|
| **Elevation of privilege** | A route reaches production without declaring the capability it requires, and so is not covered by the authorization map — fail-closed catches it at request time, but only if the route is *in* `/api` and only after the appliance is serving | **F40.** `assert_every_route_is_declared` over the built app, inside `create_app`, before it returns. Complete by construction: no registration mechanism is enumerated | `test_f40_*` (5), proven red on the unmodified tree |
| **Elevation of privilege** | An authenticated non-`/api` route (e.g. `/metrics`) is exempt from the gate by accident, declaring neither capability nor scope posture and invisible to the route-map completeness tests | **F41.** `UNAUTHENTICATED_PATHS`, an explicit allowlist asserted against what is served from two directions | `test_f41_*` (14), proven red on the unmodified tree |
| **Elevation of privilege** | A second source of authorization truth appears when `rbac.py` is split, and diverges silently from the first | Identity re-export plus a no-shadowing AST check; both proven against sabotage, under which 218 pre-existing tests stay green | `test_the_tables_are_re_exported_by_identity_not_by_copy`; `test_no_module_but_tables_binds_an_authorization_table` |
| **Elevation of privilege** | An input to the scope decision becomes writable by the role being scoped (F35) | Confirmed intact across the `shaping/` split; the invariant's comments moved with the code | `test_f35_no_resolver_input_is_writable_by_a_scopable_role`, unedited |
| **Information disclosure** | The OpenAPI schema is readable without authentication, disclosing the full API surface | **Not fixed** — pre-existing, recorded. Low severity: no data, no credential, and every route it names is enforced. Reconnaissance value only | `docs/ROADMAP.md`; §4.3 |
| **Tampering** | A declared scope posture is wrong rather than absent | **Unchanged and unresolved.** `ROUTE_SCOPE` remains descriptive; each declaration is checked against observed behaviour by test, not injected by the perimeter | `tests/test_declaration.py` behavioural suite; ROADMAP (DECISIONS #80); §4.1 |

---

## 6. Verdict

**Two findings, F40 and F41, both met.** Both reproduced by execution before being fixed, both fixed
by replacing an enumeration with a property, both covered by regression tests proven to fail on the
unmodified tree. The gate is stronger and no narrower: every route that registered before still
registers, and every completeness test passes unedited.

The authorization authority survived being split into a package, and the proof that it survived is
that a deliberately broken version of the same split passes 218 existing tests and fails the two new
ones.

**The finding series stands at F41.** The next review continues from **F42**.
