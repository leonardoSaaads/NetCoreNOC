# Module architecture — where code goes

**Status: binding on every release from v0.7.2 onward.** This document decides, once, for the
whole project — including the parts v0.7.2 does not touch — so that a later release *executes* a
decision instead of re-litigating one. It is enforced by
`tests/test_architecture.py` (the module-size guard and its debt allowlist) and by
`tests/test_structure.py` (the `src/` layout and import resolution).

NetCoreNOC is pre-alpha. This is the right — and the last cheap — moment to say where code goes.

Authority order, unchanged: `docs/scope/SCOPE-<version>.md` wins on scope, the release build
prompt wins on process, `docs/security/threat-model.md` wins on security posture, **this document
wins on placement**.

---

## 1. The layers

Five layers. The first four are a stack; the fifth is available to all of them.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  http           api/  (app, perimeter, context, models, routes_*)            │
│                 the delivery layer: HTTP semantics, the security boundary,   │
│                 request/response shape. Owns no domain rule.                 │
├──────────────────────────────────────────────────────────────────────────────┤
│  engine         main, engine, engine_base, maintenance, gaps,                │
│                 scorer_lifecycle, correlate, learn, scoring, rootcause,       │
│                 severity, varbind_profile, preview   (runner -> http)         │
│                 the domain: what a situation is, what links two alarms, what │
│                 an entity is, what the root cause is.                        │
├──────────────────────────────────────────────────────────────────────────────┤
│  data           store, migrations/                                           │
│                 one SQLite connection under one asyncio lock; SQL lives here │
│                 and nowhere else.                                            │
├──────────────────────────────────────────────────────────────────────────────┤
│  ingest         receiver, events, known_oids                                 │
│                 the wire: parse, allowlist, quarantine, the trap vocabulary. │
│                 "Ingestion is sacred" — no lock, no I/O, no await.           │
└──────────────────────────────────────────────────────────────────────────────┘
     ╷
     ╵  importable from any layer above, and only from each other
┌──────────────────────────────────────────────────────────────────────────────┐
│  cross-cutting  rbac, shaping, auth, audit, runtime, logsetup                │
│                 identity, authorization, visibility, attribution, config,    │
│                 logging. No layer's private concern; every layer's concern.  │
└──────────────────────────────────────────────────────────────────────────────┘
```

### The dependency rule

> **A layer may import downward, and may import cross-cutting. Never upward.**

Downward means toward the wire: `http` → `engine` → `data` → `ingest`. Cross-cutting is
importable from anywhere and imports only cross-cutting.

The rule exists because an upward import is what turns a stack into a knot: it makes the lower
layer untestable without the higher one, and it makes "where is this decided?" unanswerable — the
exact property that let F34–F39 hide.

**Enforced since v0.7.3** by `tests/test_layers.py` (DECISIONS #92), which parses every module's
imports and mirrors the table above. Between v0.7.2 and v0.7.3 the rule had a paragraph and no
test, which is exactly why the violation below sat recorded-but-unfixed for a release. Type-only
imports (`if TYPE_CHECKING:`) are excluded — no runtime edge, no cycle — and the exemption list is
**empty**.

### Current violations — named, not fixed

A violation found while writing an architecture document is a `docs/ROADMAP.md` line, not a fix in
the release that found it (v0.7.2 build prompt, directive 3). Both are recorded there.

| Import | Layers | Why it is a violation | Disposition |
|---|---|---|---|
| ~~`main.py` → `netcorenoc.api` (`QuietServer`, `create_app`)~~ | engine → **http** | The one genuine upward import. `main.py` was two things wearing one hat: the `Engine` (domain) **and** the process entry point that builds the HTTP server. The entry point may legitimately reach up; the `Engine` may not, and they shared a module. | **RESOLVED in v0.7.3.** `runner.py` and `main.py` are the entry point and may reach up; `engine.py` may not and does not. Enforced by `tests/test_layers.py`, whose exemption list is empty. |
| `runtime.py` → `receiver.py` (`Network`, `parse_allowlist`) | cross-cutting → ingest | `RuntimeConfig` holds parsed allowlist networks, so it reaches into the ingest layer for the parser. Small, real, and a cycle risk if `receiver` ever needs runtime config. | ROADMAP; the parser is a candidate for cross-cutting, or `RuntimeConfig` could hold strings and let the receiver parse. |
| `audit.py`, `auth.py` → `store.Store` under `if TYPE_CHECKING` | cross-cutting → data | **Tolerated.** Type-only: no runtime edge, no import cycle, and the alternative is a `Protocol` that would restate `Store`'s surface in a second place. Recorded so it is a decision rather than an oversight. | Keep. |

`api.py`'s `if TYPE_CHECKING: from netcorenoc.main import Engine` is http → engine — downward, and
therefore fine.

---

## 2. The placement rule

> **A module owns one noun or one decision. A file over ~250 lines is a smell; over ~400 it is
> debt with a named owner.**

Three clarifications a contributor will need:

* **"One noun"** means a thing the domain talks about — an alarm class, a situation, a scorer
  configuration. **"One decision"** means a question the system answers exactly once —
  *is this principal authorized?*, *which NEs may they see?*, *do these two alarms belong
  together?* A module that owns a decision owns **all** of it: a second implementation of an
  existing decision is a defect regardless of where it lives.
* **The line numbers are a smell threshold, not a target.** Splitting a coherent 260-line module
  into two 130-line modules to satisfy a number makes the code worse. The number's job is to force
  the *question* — "is this still one noun?" — not to answer it.
* **400 is enforced.** `tests/test_architecture.py::test_no_module_exceeds_the_size_guard` fails CI
  above it. See §5.

---

## 3. The `http` layer as built in v0.7.2

`src/netcorenoc/api/` is a package, one level deep and no deeper. Sixteen modules — two more
than the release plan sketched, because `perimeter.py` measured 444 lines before the
registration gate was written and the 400-line guard outranks a planned module list
(DECISIONS #85). Each extraction is justified by the placement rule above, not by arithmetic.

| Module | Owns | May import |
|---|---|---|
| `__init__.py` | the compatibility surface: everything previously reachable as `netcorenoc.api.X` | the siblings |
| `app.py` | **wiring only** — build `Perimeter`, build `AppContext`, register the middleware, call each `register()` in order | all siblings |
| `perimeter.py` | **the security boundary** (§4) | `rbac`, `shaping`, `auth`, `audit` (cross-cutting) |
| `governance_cache.py` | `GovernancePolicies` — the per-request cache of the two stored policy documents. One noun, and not a decision: the decisions are `rbac.resolve_capabilities` and `shaping.visible_nes` | `rbac`, `shaping` |
| `declare.py` | the route-declaration gate: `DeclaredRoutes`, the only registration path | `rbac` |
| `context.py` | `AppContext`, the frozen record every route module receives | `perimeter`, `governance_cache` |
| `models.py` | every pydantic request model, `QuietServer`, the `MAX_*` limits they use | `auth`, `scoring` |
| `routes_static.py` | `/healthz`, `/readyz`, `/`, the static asset allowlist | — |
| `routes_auth.py` | login, logout, `/api/me`, password change | `auth` |
| `routes_read.py` | the viewer read surface: stats, graph, classes, situations, timeline, entities, state-clears | `shaping`, `learn` |
| `routes_operate.py` | the editor write surface plus the two admin resets | — |
| `routes_admin.py` | users, service tokens, runtime config | `auth` |
| `routes_scorer.py` | the v0.6.0 scoring seam's HTTP surface | `scoring`, `preview` |
| `routes_governance.py` | the v0.7.0 capability and scope policy surface | `rbac`, `shaping` |
| `routes_audit.py` | quarantine and the audit read/export/prune surface | `audit` |
| `routes_events.py` | the SSE stream | `shaping`, `rbac`, `learn` |

**Read `perimeter.py` first if you are reviewing security.** It is the whole HTTP security
boundary in one file a reviewer can finish in one sitting. The route modules receive its bound
helpers on `AppContext`; **none of them may implement one.**

### 3.1 The three concepts, and only three

v0.7.2 adds exactly three named abstractions to the HTTP layer. Each answers a defect this project
actually had. Nothing else — no service layer, no repository pattern, no DI container, no base
classes, no plugin registry.

1. **`Perimeter`** — a class, because the closures it replaces capture `store`, `governance`,
   `limiter` and `warnings`, and free functions would have grown a parameter each (which would
   have changed handler call sites, forfeiting the parity proof).
2. **`AppContext`** — a frozen dataclass carrying the fifteen things handlers need. Each
   `register()` **rebinds its fields to local names as its first statement**, so every handler body
   is textually identical to v0.7.1. This rebinding block is mandatory; rewriting call sites to
   `ctx.audit_row(...)` would touch every handler and destroy the proof that nothing moved.
3. **The registration gate** — `declare.DeclaredRoutes` is the object every route module
   registers through. It refuses a route `rbac.py` has not been told about, while the application
   is being built, so an appliance carrying an undeclared route does not start.

---

## 4. What the perimeter owns, and what it does not

The perimeter is a named component, not a folder convention.

**It owns:** the security-headers middleware; the CSRF/origin check; identity resolution (session
cookie or bearer token); the bootstrap gate; capability resolution (delegated to
`rbac.resolve_capabilities`, the single authority); scope resolution (delegated to
`shaping.visible_nes`); the per-client rate limit; the audit-row helper; the write-transaction
boundary; the write-side scope check and its denial audit; the route-declaration gate.

**It does not own:** any handler logic; any response shaping; any SQL; any domain rule. It decides
*whether a request may proceed*, never *what it returns*.

The order its steps run, and the full component description, are in
[`DESIGN.md`](DESIGN.md) § "v0.7.2 — the perimeter as a named component". `api/perimeter.py`'s
module docstring points there rather than restating it.

---

## 5. The module-size guard and the debt allowlist

`tests/test_architecture.py` asserts that no module under `src/netcorenoc/` exceeds **400 lines**,
with an explicit `DEBT_ALLOWLIST` mapping each current offender to its line count and the release
that will fix it.

Two properties make this a ratchet rather than a comment:

* **The allowlist may only shrink.** A new offender fails the guard; it cannot be waived by adding
  a line to the allowlist without that being a visible, arguable diff.
* **An allowlisted module may not grow.** Its recorded count is an upper bound, asserted
  separately. Debt is allowed to exist; it is not allowed to compound.

State at v0.7.2:

| Module | Lines | Owner release | Note |
|---|---:|---|---|
| `store.py` | 1 512 | **v0.7.3** | specified in §6 below |
| `main.py` | 1 079 | **v0.7.3** | specified in §7 below |
| `shaping.py` | 476 | v0.7.4 | two axes in one file (field shaping, NE scoping); the split is along that seam |
| `rbac.py` | 436 | v0.7.4 | **added by v0.7.2**: `ROUTE_SCOPE` — the declaration whose absence was F34 — took it from 348 past the guard. The table belongs here (single source of authority) and every `"unscoped"` entry must carry a written justification, so neither the table nor the prose could be traded away. Seam: the route/capability **tables** on one side, the capability-policy parser and resolver on the other. DECISIONS #87 |
| `varbind_profile.py` | 417 | v0.7.4 | just over; likely one extraction (the accumulator) rather than a package |

This is the single most valuable thing v0.7.2 leaves behind: the debt is in CI instead of in a code
review two years from now.

---

## 6. `store.py` — the target (**v0.7.3: planned**)

**Implement none of this in v0.7.2.**

`store.py` (1 512 lines) splits **by domain, along its own existing section comments**, which
already mark the seams:

| Section (current line) | Target module |
|---|---|
| devices and classes (163) | `store/devices.py` |
| alarms (250) | `store/alarms.py` |
| learned state (352) | `store/learned.py` |
| situations (387) | `store/situations.py` |
| feedback and labels (510) | `store/feedback.py` |
| read models for the API (566) | `store/read_models.py` |
| entity model + varbind profiler (761) | `store/entities.py` |
| state-based clears (907) | `store/state_clears.py` |
| ingest gaps (935) | `store/ingest_gaps.py` |
| scoring configuration (954) | `store/scoring_config.py` |
| governance policy (1051) | `store/governance.py` |
| auth: users / sessions / tokens (1238, 1296, 1357) | `store/auth.py` |
| audit log (1399) | `store/audit_log.py` |
| retention (1471) | `store/retention.py` |

### The invariant v0.7.3 may not break

> **One `Store` class is preserved, with one connection and one `store.lock`.**

This is not a style preference. The single connection and the single lock are load-bearing:
v0.7.1's F39 exists precisely *because* one connection is shared by the engine task and every API
request, and the write-transaction discipline is built on `store.lock` being the one mutual
exclusion. Splitting `Store` into several objects with several connections, or several locks, is a
**behaviour change of the worst kind** — one whose failure mode is data corruption under
concurrency, invisible to every existing test.

### The two candidate mechanisms — and the choice is v0.7.3's to make

**(a) Mixins assembled in `store/__init__.py`.**
`class Store(DeviceMixin, AlarmMixin, …)`, each mixin a plain class holding methods that use
`self._conn` and `self.lock`.
*`mypy --strict` risk:* each mixin's methods reference attributes it does not define
(`self._conn`, `self.lock`), so every mixin needs either a `Protocol` base or duplicated
attribute annotations. Both are real cost, and a `Protocol` restating `Store`'s internals is a
second source of truth for its shape. Method resolution order also becomes something a reader must
reason about.

**(b) Free functions taking the connection, with `Store` as a thin façade.**
`async def list_classes(conn: Connection) -> list[dict[str, Any]]`, and `Store.list_classes`
delegates.
*`mypy --strict` risk:* low — every function is independently and completely typed. The cost is
109 delegating one-line methods on the façade, which is real boilerplate and a real place for a
transcription error, and it makes the method-hash parity proof (below) less direct.

**Neither is chosen here.** Guessing now would be exactly the re-litigation this document exists to
prevent — in reverse. v0.7.3's Phase 1 picks one, having measured the `mypy --strict` cost of each
on two real sections, and records the choice as an ADR.

> **Superseded 2026-07-30 (v0.7.3) — see [`DECISIONS.md` #88](../adr/DECISIONS.md).** The paragraph
> above is left as written; it was right that the choice needed a measurement, and the measurement
> was taken. A **third** mechanism was chosen, which this section did not consider: **mixins over a
> thin base that only *declares* the ten attributes and the `conn` accessor** — neither a `Protocol`
> restating `Store`'s shape nor duplicated annotations, but one declaration site. Option (b) was
> rejected on a ground this section understated: rewriting all 109 bodies as free functions changes
> all 109 hashes, so the method-hash parity proof §8.3 requires becomes impossible to state.
>
> One amendment came out of the measurement rather than the plan. `StoreBase` holding *only* the
> annotations and the accessor produced **4** `mypy --strict` errors, because exactly **6** methods
> are called across a mixin boundary. Where that happens the calling mixin **inherits the sibling
> mixin** — two edges in total, `AlarmMixin(DeviceMixin)` and
> `ReadModelsMixin(GovernanceMixin)` — rather than the five signatures being restated on the base.
> The MRO stays linear and `StoreBase` stays free of behaviour. Evidence:
> [`docs/gates/v0.7.3-phase-1.md`](../gates/v0.7.3-phase-1.md).

---

## 7. `main.py` — the target (**v0.7.3: planned**)

**Implement none of this in v0.7.2.**

`main.py` (1 079 lines) holds four separable things and one that must not be separated.

**May leave `Engine`:**

* **The maintenance loop** — `maintenance()`, `maintenance_loop()`, `_promotion_sweep()`,
  `_maybe_promote()`, `_maybe_confirm_severity()`, `_flush_profiler()`. Periodic, off the ingest
  path, and reasons about elapsed time rather than about a batch.
* **The ingest-gap tracker** — `GapTracker`, `_OpenGap`, `_record_ingest_gaps()`. Already a
  self-contained class with its own state; it is in `main.py` only because that is where it was
  written.
* **The scorer reload point** — `load_scorer_config()`, `_use_default_scorer()`,
  `scorer_warning_list()`, `_audit_scorer_fallback()`. The v0.6.0 seam's *lifecycle*, distinct from
  scoring itself.
* **The process runner** — `Settings`, `Supervisor`, `operator_warnings()`, the bootstrap banner,
  the legacy-env errors, `main()`. This is also what resolves the `main.py` → `api.py` layer
  violation in §1: the runner may reach up into `http`; the `Engine` may not.

**Must not leave `Engine`:**

> **The batch lock and everything that reasons about it.**

`run()`, `_commit_batch()`, `_process()`, `drain()`, `_assign_situation()`, `_handle_clear()`,
`_handle_state_clear()`, `_close_situation()`, `_resolve_entity()`, `_resolve_severity()`,
`FlapDetector`. "Ingestion is sacred" (invariant 2) is only auditable if the whole ingest path can
be read in one place: a reviewer must be able to confirm, without following imports, that nothing
on that path takes a lock, does I/O, or awaits where it must not. Fragmenting it would make the
project's oldest invariant unauditable, which is the exact opposite of this document's purpose.

> **Amended 2026-07-30 (v0.7.3) — see [`DECISIONS.md` #90](../adr/DECISIONS.md).** The may-leave
> list above is left as written, and all of it left **except `maintenance()` and
> `maintenance_loop()`**, which stayed. Measurement: `maintenance` is the only may-leave candidate
> that does `async with self.store.lock:` — the same `asyncio.Lock` object `_commit_batch` takes,
> because there is only one — and the only one that calls a must-stay method (`_close_situation`).
> The v0.7.3 build prompt §5.2 rules that such a method does not leave and that directive 4 outranks
> the module table. `maintenance_loop` is six lines whose whole body calls `maintenance`.
> `_promotion_sweep()`, `_maybe_promote()`, `_maybe_confirm_severity()` and `_flush_profiler()` left
> as planned. Keeping the pair also removed the only mixin→`Engine` call, which is what lets
> `EngineBase` be a pure declaration site.

## 8. The gates v0.7.3 inherits

Non-negotiable, in the same form v0.7.2 used them:

1. **`make eval` byte-identical** to the frozen baseline.
2. **No test assertion edited** — only module lists, moved import paths, and genuinely new
   structural tests.
3. **A method-hash proof for `Store`**, exactly equivalent to v0.7.2's handler hashes:
   `sha256(inspect.getsource(method))` for all 109 `Store` methods, taken in Phase 0 and recomputed
   in Phase 5. A single mismatch is a build failure.
4. **The debt allowlist shrinks, never grows** — `store.py` and `main.py` leave it; nothing joins.

---

## 9. Deliberately not decided here

* **Path normalisation.** `/api/labels` carries a `kind` discriminator instead of being two
  resources; `/api/situations/{sid}/close` and `/api/scorer/rollback` are RPC verbs in a REST
  estate; `/api/users/{uid}/role` is a sub-resource where a `PATCH` would do. These are public
  contract changes, not placement questions. ROADMAP; the v0.7.2 registry makes each one a one-line
  change with the authorization matrix proving the rest.
* **Async/DI frameworks, service layers, repository patterns.** Not present, not planned. The
  project has five runtime dependencies and intends to keep them.
* **A second level of nesting** anywhere under `src/netcorenoc/`. One level, where earned.

---

## 10. The v0.7.4 targets (**v0.7.4: planned**)

**Implement none of this in v0.7.3.** Specified here so v0.7.4 *executes* rather than rediscovers,
which is the same service §6 and §7 performed for v0.7.3.

### 10.1 The declaration gate has two holes — **v0.7.4: planned**

Found by adversarial probing of `api/declare.py` while reviewing v0.7.2, and **confirmed by
execution**, not by reading. Neither is exploited today — the current surface has no `PUT`, no
`PATCH`, one `add_api_route` caller, and no authenticated non-`/api` route — but both are latent
holes in a guard whose entire value is completeness.

They were **not** fixed in v0.7.3 because fixing a security-adjacent guard inside a move release
forfeits the parity story for a latent, unexploited gap, and a fix buried in a 2 600-line diff is a
fix nobody can review. That is the same reasoning that kept v0.7.2 from fixing what it found.

**Gap 1 — the gate covers three verbs and only the decorator form.**

```
DeclaredRoutes methods: ['delete', 'get', 'post']
  has .put():   False
  has .patch(): False

app.add_api_route("/api/undeclared-bypass", handler, methods=["GET"])
  registration raised:            False
  route now in the table:         [(['GET'], '/api/undeclared-bypass')]
  declared in ROUTE_PERMISSIONS:  False
  declared in ROUTE_SCOPE:        False
  (control) the decorator form:   refused, as designed
```

`tests/test_declaration.py::test_the_gate_is_the_only_registration_path` greps for `@app.<verb>`
decorators, so it cannot see the non-decorator form; and
`test_add_api_route_is_confined_to_the_static_asset_allowlist` asserts there is exactly **one**
caller today, which is true and does not prevent a second.

**The fix, specified: assert *after* `create_app` returns that every `/api` route on the built app
is declared.** Wrapping the remaining verbs and gating `add_api_route` would work, but only by
enumeration — it closes the paths that exist today and stays silent about the next one. A post-hoc
assertion over `app.routes` is **complete by construction**: it catches any registration path,
including ones nobody has written yet, because it inspects the *result* rather than the route in.
Prefer it. Keep the decorator-time refusal as well, because failing at the point of registration
gives a much better error than failing at the end of `create_app`.

**Gap 2 — the exemption is by path prefix, not by absence of capability.**

```python
def require_declaration(method: str, path: str) -> None:
    if not path.startswith("/api"):
        return  # static / health surface: no identity, no capability, nothing to declare
```

True of today's surface — `/healthz`, `/readyz`, `/`, the four static assets — and **accidentally**
true of anything else that ever sits outside `/api`. `require_declaration("GET", "/metrics")`
returns without raising, and `/metrics` is already on the ROADMAP.

**The fix, specified:** replace the prefix test with an explicit allowlist of unauthenticated paths,
so adding an authenticated non-`/api` route fails the gate instead of slipping past it. The
allowlist should be derived from, or asserted against, `STATIC_ASSETS` plus the health surface, so
it cannot drift from what is actually served.

### 10.2 Three oversized modules — **v0.7.4: planned**

The `DEBT_ALLOWLIST` v0.7.3 leaves behind, with the seams §5 already names:

| Module | Lines | Seam |
|---|--:|---|
| `shaping.py` | 476 | two axes in one file: **field** shaping by role, and **NE scoping** by policy |
| `rbac.py` | 436 | the route/capability **tables** on one side, the capability-policy parser and resolver on the other |
| `varbind_profile.py` | 417 | one extraction — the accumulator — **not** a package |

All three are small enough that the v0.7.3 mechanism (mixins over a thin annotated base) is
probably overkill; `varbind_profile.py` in particular wants a single class moved out, not a package.
Take the measurement before choosing, as §6 required of v0.7.3.

> **Superseded 2026-07-31 (v0.7.4) — see [`DECISIONS.md` #95](../adr/DECISIONS.md).** The table
> above is left as written. It was right that the measurement had to be taken before choosing, and
> right about `rbac.py` and `varbind_profile.py`. It was **wrong about `shaping.py`**: the seam is
> not two axes but **three parts**, confirmed from the AST in
> [`../gates/v0.7.4-phase-0.md`](../gates/v0.7.4-phase-0.md) §4.1 before the correction was
> accepted.
>
> | Part | Lines (v0.7.3) | Target |
> |---|--:|---|
> | field shaping | 50–108 | `shaping/fields.py` |
> | scope resolution | 114–377 | `shaping/scope.py` |
> | **projections** | 383–476 | `shaping/project.py` |
>
> The projections — `filter_rows`, `_as_int`, `project_graph`, `project_situation_detail` — are not
> a third *axis*. They are the **consumer** of the other two: each takes a `Scope` produced by the
> scope axis and returns a response body, which is the field axis's subject. Forcing them into
> either side would be arbitrary, and this section's two-way framing is exactly what makes one of
> those choices look necessary.
>
> Two further corrections from the same measurement:
>
> * **`rbac.py`'s three module-level `assert` statements** (lines 200–212 and 271–274) travel into
>   `rbac/tables.py` with the tables they assert. An assertion about a table that no longer runs
>   where the table is defined is a structural guarantee silently deleted (DECISIONS #96).
> * **`varbind_accum.py` is `engine` layer**, following `varbind_profile.py`, not cross-cutting
>   (DECISIONS #97). `rbac/` and `shaping/` need no `LAYER_OF` entries at all: `tests/test_layers.py`
>   keys a packaged module by its package name, which the table already carries.

### 10.3 Also recorded

* **Four redundant `# nosec B608` markers**, now at `store/retention.py:23,27,31,35`. `bandit`
  reports "nosec encountered, but no failed test". Untouched in v0.7.3 because changing a `# nosec`
  on a SQL string is a change to SQL handling, and that release changed no SQL.
* **`receiver.py`'s coverage is timing-dependent** (87–91 % across runs of identical code, including
  at the v0.7.2 baseline). Harmless in itself, but it puts noise in the one gate that detects a test
  going quiet.
* **`engine.py` is `COHESION_EXEMPT`, permanently.** v0.7.4 must not "finish the job" by splitting
  it. There is no job to finish: the entry has no owner and no date because the invariant it cites
  has no expiry.

### 10.4 And after that

<!-- release-claim: v0.8.0 = operator-feedback-dataset -->

**v0.8.0 is the next feature release** — the operator-feedback dataset. The v0.7.x series is not
open-ended: v0.7.3 was the last structural release, and once §10.2 lands the project has no module
over the size guard except `engine.py`, which is large by documented design.

> **Amended 2026-07-31 (v0.7.4) — see [`DECISIONS.md` #93](../adr/DECISIONS.md).** The paragraph
> above was right and is now the *only* answer the repository gives. When it was written,
> `docs/ROADMAP.md` and the draft then named SCORER-PLUGINS-0.8-DRAFT simultaneously said v0.8.0 was
> customer-supplied models — this section was one of two camps, not the settled position, and
> nothing recorded which was which. v0.7.4 records the resequencing (models → **v0.13.0**, ONNX
> only, the Python entry-point hatch **rejected**), writes the whole chain down in
> [`ROADMAP-0.8-TO-0.13.md`](ROADMAP-0.8-TO-0.13.md), and installs
> `tests/test_documentation.py` so a second answer fails CI.
>
> One correction to §10.2 also came out of v0.7.4's Phase 0 and is recorded in **DECISIONS #95**:
> `shaping.py` is **three** parts, not two. See the note in §10.2.
