# Security review — NetCoreNOC v0.7.2

**Findings: none.**

v0.7.2 moved 1 752 lines of the HTTP layer into sixteen modules and added a route-declaration
gate. It changed no behaviour, and this review found nothing to number. A move-only release should
produce zero findings, and a review that manufactures one to look diligent is worse than one that
says "none" — so this one says none, and then spends the rest of its length on the two things that
actually matter: **proving the claim**, and **saying what the release does not buy.**

The finding series therefore stays at **F39** (v0.7.1). F40 is unused.

---

## 1. What this release is, in security terms

Four of v0.7.1's six findings lived in `api.py`. They hid for one structural reason: a single file
held the CSRF gate, identity resolution, the governance policy cache, capability resolution, scope
resolution, the audit helper, the rate limiter, the transaction discipline **and** forty route
handlers. Nobody could read the perimeter, because there was no perimeter to read — only a file
that happened to contain one.

v0.7.2 makes the perimeter a thing: `src/netcorenoc/api/perimeter.py`, 361 lines, everything that
decides *whether a request may proceed* and nothing that decides *what it returns*. And it makes
the declaration F34's absence exposed into something the process refuses to start without.

That is the whole release. It moves code and it adds a gate. It fixes nothing, because there was
nothing left to fix — and it was forbidden from fixing anything, for a reason worth restating: a
fix hidden inside a move is invisible to review, which is precisely how F34–F39 stayed invisible
for a release.

---

## 2. No new surface

| Surface | v0.7.1 | v0.7.2 | Evidence |
|---|---:|---:|---|
| Routes | 48 (incl. `/openapi.json` and 4 static assets) | **48** | `test_route_table_order_is_unchanged` — identical **and in order** |
| Capabilities (`PERMISSIONS`) | 25 | **25** | file unchanged in that region |
| `ROUTE_PERMISSIONS` entries | 39 | **39** | unchanged; `test_authorization_matrix` regenerates from it |
| `PUBLIC_ROUTES` | 1 | **1** | unchanged |
| `AUDITED_DENIED_PERMISSIONS` | 15 | **15** | unchanged; `test_f8_audited_denied_single_source` |
| Audit actions | unchanged | unchanged | `test_audit_catalog_completeness` |
| Migrations | 7 | **7** | none added; `schema_version` stays 7 |
| Runtime dependencies | 5 | **5** | `pyproject.toml` |
| Served static paths | 7 | **7** | `STATIC_ASSETS` moved verbatim to `routes_static.py` |
| Environment variables | unchanged | unchanged | `main.py` untouched |

`rbac.ROUTE_SCOPE` is new, and it is not surface: nothing reads it at request time. It is a table
plus two import-time assertions plus a registration check. Wiring it into the request path would
change control flow, and control flow is behaviour — so it is deferred to a ROADMAP line
(DECISIONS #80).

---

## 3. No decision moved, and none was duplicated

This is the claim that matters, so it is evidenced rather than asserted.

### 3.1 Every handler body is byte-identical

All 43 decorator-registered handlers hash identically to their v0.7.1 text from the `def` line
down — signature, docstring, body. All 43 decorators differ from v0.7.1 by exactly `@app.` →
`@route.` and by nothing else. Measured against the pristine v0.7.1 `api.py` at commit `9cb2086`,
recomputed at every step of the split (`docs/gates/v0.7.2-phase-5.md` §1).

A handler whose text has not changed cannot have changed its decision.

### 3.2 The perimeter's own code moved under one mechanical rule

The eleven `create_app` closures became `Perimeter` methods under exactly one substitution, applied
to NAME **tokens** only — never inside a string, a docstring, a comment, or an attribute position:

> a name that was a captured free variable of `create_app`'s scope becomes an attribute of `self`

`store` → `self._store`, `governance` → `self.governance`, `limiter` → `self._limiter`,
`warnings` → `self._warnings`, sibling closure → `self.<name>`. A first regex-based attempt
substituted inside string literals (`"no-store"` → `"no-self._store"`) and was discarded and
rewritten with `tokenize`. That mistake is recorded in `docs/gates/v0.7.2-phase-3.md` §3 because it
is exactly the class of error a "mechanical" transformation is assumed not to make.

### 3.3 The single-decision-site guarantee still holds, and the tests that prove it

| Decision | One site | Test |
|---|---|---|
| Authorization | `rbac.resolve_capabilities` | `test_f28_no_role_comparison_outside_rbac` — no role comparison anywhere in the package |
| Scope (read) | `Perimeter.scope_for` | `test_f34_every_mutating_route_below_admin_resolves_scope` |
| Scope (write) | `Perimeter.situation_in_scope`, `scope.allows_ne` | same |
| Transaction boundary | `Perimeter.write_txn` | `test_f39_every_mutating_handler_uses_the_transaction_helper` |
| Audited-denied set | `rbac.AUDITED_DENIED_PERMISSIONS` | `test_f8_audited_denied_single_source` + the import-time assert |

All four source-scanning guards survived the split with their assertions intact; only the source
they read and three token spellings changed, both enumerated in `docs/gates/v0.7.2-phase-5.md` §3.
Without `tests/apisource.py` they would have kept passing against `__init__.py` alone — which is
the failure mode this review would otherwise be reporting. `api_source()` refuses a module it has
not been told where to place and asserts a floor on the concatenated length, so it cannot go
vacuous.

**No route module implements a perimeter decision.** Each receives the perimeter's bound methods on
`AppContext`, and `AppContext` is a frozen dataclass, so a route module cannot swap one for its own
— the guarantee is structural rather than conventional.

### 3.4 The import-time invariants survived the move

`DENIED_ACTION` moved to `perimeter.py` **with its assert**, which is the point: the F8 guarantee is
that the presentation mapping cannot drift from `rbac.AUDITED_DENIED_PERMISSIONS`, and an assert
left behind in a file that no longer holds the table would guarantee nothing. Demonstrated live:

```
>>> rbac.AUDITED_DENIED_PERMISSIONS |= {"drift.injected"}
>>> importlib.reload(netcorenoc.api.perimeter)
AssertionError: DENIED_ACTION keys must equal rbac.AUDITED_DENIED_PERMISSIONS (single source of truth)
```

`rbac.py` gained two more import-time assertions of the same kind: `ROUTE_SCOPE` and
`ROUTE_PERMISSIONS` must declare the same routes, and `admin_only` must hold **if and only if** the
capability's minimum role is `admin`.

### 3.5 The middleware still runs on every response

`create_app` registers `perimeter.security_headers` via `app.middleware("http")(...)` rather than a
decorator, which is the same registration by a different spelling. Verified end to end against the
**installed wheel run as a real process**: every one of the seven public paths returns
`Content-Security-Policy` and `X-Frame-Options: DENY`, and `/api/*` returns `Cache-Control:
no-store`. The 46-route cross-version comparison (§4) checks both headers on every route.

### 3.6 Packaging integrity

Wheel and sdist both carry all sixteen `netcorenoc/api/` modules, the full UI, the seven
migrations, and the vendored d3 with its `CHECKSUMS.txt` — verified from freshly built artefacts,
not from reading `pyproject.toml`. The F12/F13 class of defect (tests passing against a source tree
a wheel would not reproduce) is closed one level deeper than before: `tests/test_structure.py`
now proves all fifteen `api.*` submodules resolve from the **installed** package.

---

## 4. The strongest evidence: the same database, both versions

A live v0.7.1 database was produced by installing the real v0.7.1 tree into its own virtualenv and
driving it — 40 traps through the engine, a maintenance pass, four users, two service tokens, four
scorer configurations with history, both governance policy kinds with history, labels, feedback,
runtime config, and a six-row audit chain. Two copies were then queried by v0.7.1 and v0.7.2 code
respectively.

```
store snapshot diff  : EMPTY  (schema SQL hash, 24 tables, every row count, every read model)
schema_version       : 7 -> 7   no migration ran
audit chain          : verifies, same final hash 046a933d…
routes compared      : 46   response diff: EMPTY
```

Every status code, every response field, every header, and every link's three scored terms to six
decimal places are identical. The only values excluded are wall-clock timestamps written *by the
probe itself*; the stored ones are compared and match.

---

## 5. Critical analysis — what this release does **not** buy

### 5.1 It does not make the perimeter more correct

The same code in different files has the same behaviour. That is the release's central claim and it
is proved hash by hash — which means it is also a limit. **Every caveat in
`SECURITY-REVIEW-0.7.1.md` §4 stands unchanged**, without exception:

* visibility scoping is still a presentation control and **not tenant isolation** — correlation
  still learns across every network element, and a situation may still form across a boundary a
  principal cannot see;
* the rate limiter is still per-client-address, in-memory, and resets on restart;
* `SafeScorer` still cannot preempt a synchronous in-process call that never returns (F25, partial);
* the audit chain is still append-only-by-convention within one SQLite file, not externally
  witnessed;
* `label` still has no foreign key.

If a reviewer read v0.7.1 and concluded the perimeter was sound, v0.7.2 gives them no new reason to
believe it. If they concluded it was not, v0.7.2 gives them no reason to stop worrying. What it
gives them is the ability to *check* in one sitting instead of three.

### 5.2 What it does buy, stated precisely

Two things, and only two:

1. **A perimeter a reviewer can read end to end.** 361 lines, one file, containing every decision
   about whether a request may proceed and nothing else. Before, that reviewer had to find eleven
   closures scattered through 1 752 lines and satisfy themselves that nothing between them was also
   making a decision.
2. **A discipline under which F34's class cannot recur silently.** F34 existed because a route's
   scope posture was expressed nowhere. Now every non-public route declares one; a route missing
   either declaration cannot be registered, so the process does not start; and a test checks every
   declaration against the route's observed behaviour. A future contributor adding a route gets a
   failure at the point of registration, not a defect found two releases later.

Note the honest gap in (2): `ROUTE_SCOPE` is **descriptive**. It records what each route does; it
does not make the route do it. A contributor could declare `"scoped"` and write a handler that
never resolves scope — the posture test would catch that, but a *test* is what F34 already had.
Making the perimeter inject the check from the table is what would make it structural, and that is
deferred because injection changes control flow (DECISIONS #80). This release moves the guarantee
from "nothing" to "declared and tested"; the step from "tested" to "structural" is v0.7.3+'s.

### 5.3 `Perimeter` is now constructible outside `create_app` — named, not discovered

Extracting the closures into a class means the authorization machinery has a second construction
path:

```python
Perimeter(store, rate_capacity=…, rate_refill=…, warnings=…)
```

This is convenient for tests and it is also, plainly, a second way to instantiate the object that
resolves capabilities and scope. It is **harmless**: `Perimeter` holds no state a caller could not
already reach through `store` — the same `GovernancePolicies` reads the same two policy rows, and
`resolve_capabilities`/`visible_nes` are pure functions of `(role, ref, policy)`. A caller who can
construct one already holds the `Store`, and holding the `Store` is strictly more power than
holding a `Perimeter` built from it.

It is named here rather than left to be discovered because "harmless" is a judgement, and a
judgement recorded in a review is one a later reader can disagree with. Two consequences follow if
that judgement ever stops holding — if `Perimeter` gains state a caller could not otherwise reach,
or if construction acquires a side effect — and both belong in a future review, not in a
constructor comment.

### 5.4 `rbac.py` got worse, and this release did that

`rbac.py` went from 348 to 436 lines and joined the module-size debt allowlist. `ROUTE_SCOPE` is
what pushed it over: 88 lines of table, posture definitions and per-entry justification.

Every alternative was worse. Moving the table out of `rbac.py` would break the single-source-of-
authority property that makes the whole authorization model checkable. Trimming the prose would
delete the per-entry justification on every `"unscoped"` — which is *required by test*, precisely
because an unexplained `"unscoped"` is an assertion nobody has had to defend, and that is how F34
happened. Splitting `rbac.py` would be a second structural change to the authorization authority in
a release that ships no behaviour.

So the guard noticed, and the answer is a dated entry with an owner (v0.7.4) and a named split seam
— the route/capability tables on one side, the capability-policy parser and resolver on the other.
Saying "this release added debt" is more useful than quietly raising a threshold, and a guard that
gets weakened the first time it says something inconvenient is not a guard.

### 5.5 The declaration gate fires at application construction, not at module import

The build plan called this an import-time failure. It is more precise to say it fires when
`create_app` runs, because that is when `register()` executes the decorators. The practical
guarantee is the same and is what matters — `main.py` calls `create_app` during startup, so an
appliance carrying an undeclared route **does not start** — but "import time" would overstate it,
and a security document that overstates its own mechanism is the wrong kind of document.

Two things *are* strictly import-time, in `rbac.py`: `set(ROUTE_SCOPE) == set(ROUTE_PERMISSIONS)`,
and the `admin_only` ⟺ `PERMISSIONS[...] == "admin"` derivation. Those fire on `import netcorenoc.rbac`,
before any application exists.

### 5.6 One registration is not behind the gate

`_asset_route` registers the four `STATIC_ASSETS` entries with `app.add_api_route`, not through
`DeclaredRoutes`. That is deliberate — a static file has no capability and no scope to declare, and
the paths come from a compile-time allowlist, not from a request — but it is a second registration
path, so it is pinned by a test: `add_api_route` must have exactly one caller, and that caller must
be the module owning `STATIC_ASSETS`. A future contributor cannot use it to slip an `/api` route
past the gate without deleting a test that says why they should not.

---

## 6. Mapping to `threat-model.md`

**Unchanged. No update needed, and that is the correct answer.**

The threat model describes trust boundaries, assets, adversaries and controls. This release moved
no boundary, exposed no asset, admitted no adversary and altered no control. Four sentences in it
name `api.py` as the file holding a control; those are now imprecise about *where* the control
lives, and they are corrected in this release's documentation pass — a file reference is not a
posture.

The one thing worth adding for a future reader is a pointer rather than a change: the file to read
when auditing the HTTP boundary is now `src/netcorenoc/api/perimeter.py`, and
`docs/architecture/MODULE-ARCHITECTURE.md` §4 states what that component owns and what it does not.

| Threat-model control | Status after v0.7.2 |
|---|---|
| T1 spoofed identity — session/bearer resolution | unchanged (`Perimeter.resolve_identity`) |
| T2 cross-site request forgery | unchanged (`Perimeter.csrf_ok`) |
| T3 privilege escalation via policy | unchanged (`rbac.resolve_capabilities`, ceiling ∩ policy) |
| T4 information disclosure across scope | unchanged (`Perimeter.scope_for`, `shaping`) |
| T5 unattributable change | unchanged (`Perimeter.audit_row` + `write_txn`) |
| T6 resource exhaustion | unchanged (`Perimeter._limiter`, the preview bucket) |
| T7 stored XSS / CSP | unchanged (`SECURITY_HEADERS`, same middleware) |
| T8 supply chain | unchanged (d3 checksum, SHA-pinned actions, `pip-audit`) |

---

## 7. Verdict

**Zero findings. Zero new surface. Zero behavioural change, proved rather than asserted.**

The honest summary is this: v0.7.2 did not make NetCoreNOC safer. It made NetCoreNOC's safety
*checkable* — by one person, in one sitting, from one file — and it made one specific class of
future mistake fail loudly at startup instead of quietly in production. Those are worth a release.
Neither is a fix, and this review does not present them as one.
