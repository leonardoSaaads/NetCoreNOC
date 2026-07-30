# SCOPE — NetCoreNOC v0.7.2

**Theme: the HTTP layer becomes a legible, declarative, growth-ready package — with zero
behavioural change.**

This is a **structural release**. It ships no feature, no fix, no configurability, and **no
behaviour**. Not one status code, not one path, not one field, not one number.

v0.7.1 closed six findings; four of them lived in `api.py`, and they hid for one structural reason:
a single 1 752-line file holds the CSRF gate, identity resolution, the governance policy cache,
capability resolution, scope resolution, the audit helper, the rate limiter, the transaction
discipline, **and** forty route handlers. v0.7.2 splits that file into a package of small,
single-responsibility modules and — more importantly — replaces the string-joined route/permission
convention with a **declaration that fails before the process can serve**, so the class of defect
F34 belongs to becomes structurally impossible rather than merely tested for.

The one sentence that governs the whole release:

> **Declare now, prove the declaration is true now, enforce mechanically later — and move the code
> without moving a single decision.**

The runtime identity is unchanged: one Python 3.12 asyncio process, one SQLite (WAL) file, one
static UI, environment variables only, no build step, **zero new runtime dependencies** (five,
unchanged), **zero new migrations**, **zero new routes, capabilities or audit actions**. The import
path stays `netcorenoc`, and `netcorenoc.api.create_app` remains importable with an unchanged
signature — `api` becoming a package is an internal change and is invisible to every caller.

All prior scope documents and their invariants still hold; `docs/security/threat-model.md` keeps the
authority it has held since v0.2.0. On a conflict, this document wins on *scope*, the build prompt
wins on *process and quality*, the threat model wins on *security posture*, and
`docs/architecture/MODULE-ARCHITECTURE.md` wins on *placement*.

**Delivery model (unchanged).** The repository is read-only to automation: the maintainer takes the
resulting archive and pushes it by hand. No step depends on pushing, on CI running, or on any
external account, registration, or dashboard action. Every gate is local and reproducible on the
maintainer's machine (`make qa`, `make eval`, `docker compose config`, a locally built wheel).

---

## 1. In scope — exactly five workstreams, and nothing else

### 1. The target architecture, for the whole project

`docs/architecture/MODULE-ARCHITECTURE.md`: five layers, the dependency rule, the placement rule
(*a module owns one noun or one decision; over ~250 lines is a smell, over ~400 is debt with a named
owner*), and the target for `store.py` and `main.py` **which this release does not touch**. Backed
by a module-size guard in CI with a shrink-only `DEBT_ALLOWLIST`, installed against the *old* tree
in Phase 2 so every later step is measured by a rule that predates it. Current layer violations are
named in the document and recorded on the ROADMAP — a violation found is not a violation fixed here.

### 2. The perimeter extraction

Everything that decides *whether a request may proceed* — as opposed to *what it returns* — moves
into `src/netcorenoc/api/perimeter.py`, one file a reviewer can read end to end: `RateLimiter`,
`GovernancePolicies`, `DENIED_ACTION` **and its import-time assert against
`rbac.AUDITED_DENIED_PERMISSIONS`**, `BOOTSTRAP_ALLOWED`, `MUTATING`, `CSP`, `SECURITY_HEADERS`, the
four rate constants, `_client_ip`, `_route_path`, and the eleven `create_app` closures that form the
security boundary (`audit_row`, `resolve_identity`, `csrf_ok`, `flush_governance_fallbacks`,
`security`, `scope_for`, `all_warnings`, `write_txn`, `situation_in_scope`, `audit_scope_denial`,
and the `security_headers` middleware body).

The closures become methods of one class, `Perimeter`, because they capture `store`, `governance`,
`limiter` and `warnings` and free functions would have grown a parameter each. The **only** edit
permitted to moved perimeter code is the mechanical substitution `store` → `self._store`,
`governance` → `self.governance`, `limiter` → `self._limiter`, `warnings` → `self._warnings`.

### 3. The route-declaration discipline

`rbac.py` remains the single source of authority and gains one table:

```python
ROUTE_SCOPE: dict[tuple[str, str], Literal["scoped", "unscoped", "admin_only"]]
```

one entry per non-public route, with a one-line justification on every `"unscoped"`. `"admin_only"`
is a **derived assertion**, not a second authority: an entry claiming it must have a capability
whose minimum role is `admin` in `PERMISSIONS`, asserted at import so the two tables cannot
disagree.

A registration decorator in `perimeter.py` wraps `app.get`/`app.post`/`app.delete` and raises if the
`(method, path)` is absent from `ROUTE_PERMISSIONS` or from `ROUTE_SCOPE` — **before the process can
serve**, not per request and not only in CI. It is the only registration path, asserted by a test.
`PUBLIC_ROUTES` keeps its current meaning and is exempt from both tables **by explicit
consultation**, never by omission.

`ROUTE_SCOPE` is **descriptive this release, enforcing later**: it records what each route already
does after v0.7.1, and a test asserts the recorded posture matches observed behaviour for every
route. Wiring the posture so the perimeter *injects* the scope check is a ROADMAP line — injection
changes control flow, and control flow is behaviour.

### 4. The `api.py` split

`src/netcorenoc/api.py` becomes `src/netcorenoc/api/`: `__init__`, `app`, `perimeter`, `context`,
`models`, and nine `routes_*` modules. None over ~400 lines; no second level of nesting. Every
handler body is textually unchanged, proved hash by hash. `register()` functions are called in the
order the routes are declared today, so FastAPI's path-matching precedence is identical, proved by a
route-order test. `api/__init__.py` re-exports every symbol previously reachable as
`netcorenoc.api.X`.

### 5. A short security review

`docs/security/SECURITY-REVIEW-0.7.2.md`, continuing the finding series **only if the
reorganisation actually reveals something**. A move-only release should produce **zero** findings,
and a review that manufactures one to look diligent is worse than one that says "none".

---

## 2. Behavioural changes shipped

**None.** This is the release's defining property, so it is stated as an empty list rather than
omitted:

* No route path, method, status code, response field, or capability changes.
* `ROUTE_PERMISSIONS` gains no entry and loses none.
* No migration, no dependency, no audit action, no served path.
* `make eval` is byte-identical to the v0.7.1 baseline.
* Every test that passed at v0.7.1 passes here, with no assertion edited.

A refactor that "probably didn't change anything" is worth less than no refactor, because it
silently invalidates the security review of the release it follows: v0.7.1's six findings were
reviewed against *this* code, and a reorganisation that quietly moves behaviour makes that review
describe a program that no longer exists. The gates make drift **detectable**, and that — not care,
not review, not confidence — is what makes a large reorganisation safe.

---

## 3. Explicitly out of scope (deferred, each with its owner)

1. **Splitting `store.py` and `main.py` → v0.7.3.** Specified in
   `MODULE-ARCHITECTURE.md` §6–§8, built there. They are the data/engine layer: independent of the
   HTTP layer, independently gateable, and putting five large moves in one autonomous run risks a
   half-moved tree with no clean stopping point.
2. **Normalising route paths.** The estate is inconsistent — `/api/labels` carries a `kind`
   discriminator in the body rather than being two resources; `/api/situations/{sid}/close` and
   `/api/scorer/rollback` are RPC verbs in a REST estate; `/api/users/{uid}/role` is a sub-resource
   where a `PATCH` would do. Renaming any of them is a public contract change touching
   `ROUTE_PERMISSIONS`, the generated authorization matrix, `ui/app.js` and every test — in the same
   release that moves forty handlers between files. If the matrix then broke you could not tell
   which change did it. ROADMAP, with the specific inconsistencies named so the work is already
   scoped.
3. **Any fix, however small, revealed while reading the code.** A fix hidden inside a move is
   exactly how F34–F39 stayed invisible for a release. ROADMAP line and a note in the security
   review; the next release fixes it where a reviewer can see the fix.
4. **Splitting the pydantic request models per route group.** All eleven go to one `models.py`;
   fragmenting them across nine modules would make the request surface *harder* to audit, not
   easier.
5. **Async/DI framework changes**, dependency-injection containers, service layers, repository
   patterns, or any other abstraction not already present. This is a *move*, not a redesign. Exactly
   three new named concepts — `Perimeter`, `AppContext`, the registration decorator — each justified
   by a defect this project actually had.
6. **Making `ROUTE_SCOPE` enforcing** (the perimeter injecting the scope check). Control flow is
   behaviour. ROADMAP.
7. **Fixing the two named layer violations** (`main.py` → `api.py`; `runtime.py` → `receiver.py`).
   Named in `MODULE-ARCHITECTURE.md` §1, recorded on the ROADMAP; the first is resolved naturally by
   v0.7.3's `main.py` split.
8. **Anything from the v0.8.0 feedback-dataset roadmap.**
9. SNMPv3, `/metrics`, pcap replay, outbound webhook / `Case` JSON emission — still out.

---

## 4. Hard constraints (violating any is a build failure)

1. No behavioural change of any kind. Packaging, docs, declarations, and file moves.
2. No test assertion is edited — only `test_structure.py`'s module lists, import paths that name a
   moved symbol, and the genuinely new structural tests.
3. No fixes, no tidies, no renames inside moved code. The only permitted edits are the two
   mechanical substitutions above (`self._*` in the perimeter; local rebinding in each `register()`).
4. No new runtime dependencies, migrations, routes, paths, capabilities, or audit actions.
5. No new abstractions beyond `Perimeter`, `AppContext`, and the registration decorator.
6. No module under `src/netcorenoc/api/` over ~400 lines, and no second level of nesting.
7. `correlate.py`, `scoring.py`, `learn.py`, `receiver.py`, `preview.py`, `rootcause.py`,
   `severity.py`, `varbind_profile.py`, `store.py` and `main.py` are not touched — not one
   character. (`store.py` and `main.py` may be *read* for the v0.7.3 specification.)
8. `src/netcorenoc/ui/` is not touched. The UI consumes paths, and no path changes.
9. Git history preserved: `git mv` for the file that becomes a package; one commit per step so
   `git log -C -M` follows the moved lines; existing ADR and finding entries are never renumbered.
10. One process, one SQLite file, one static UI, environment variables only, no build step, no npm.

---

## 5. Definition of done

* `make qa` green; `make eval` byte-identical to the frozen baseline.
* Every `(method, path)` in the Phase 0 handler hash table hashes identically after the move.
* The ordered `(method, path)` list on the built app equals the Phase 0 baseline.
* An undeclared route raises before the app is built; `ROUTE_SCOPE` is complete; no raw
  `@app.<method>` decorator outside `perimeter.py`; every route's declared posture matches its
  observed behaviour.
* `netcorenoc.api.create_app` has an unchanged signature; every symbol in the Phase 0 reference
  inventory still resolves from `netcorenoc.api`; every new module resolves from the **installed**
  package.
* A freshly built wheel **and** sdist contain the whole `netcorenoc/api/` package, the full UI, the
  migrations and the d3 `CHECKSUMS.txt`; `make release-check` agrees on `0.7.2`.
* `docker compose config` validates; the image builds and serves the full UI.
* No module over 400 lines except the allowlisted `store.py`, `main.py`, `shaping.py` and
  `varbind_profile.py`, none of which grew.
* Coverage at or above the v0.7.1 figure (95.46 %) — a pure move cannot lower it, and a drop means
  something is no longer being exercised.
* Upgrade from a live v0.7.1 database verified: no migration runs, learned state, scorer config,
  governance policy, provenance and audit chain intact, every route answering exactly as before.
* **Zero new findings.**
