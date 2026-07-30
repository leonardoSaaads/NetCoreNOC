# Build report — NetCoreNOC v0.7.2

**Theme: the HTTP layer becomes a legible, declarative, growth-ready package — with zero
behavioural change.**

One sentence, and it is the whole release:

> **1 752 lines became sixteen modules, a route cannot be registered unless it declares what it may
> do and what it may see, and nothing else changed — proved hash by hash, route by route, and
> database by database.**

| | |
|---|---|
| Version | **0.7.2** (`pyproject.toml`, `src/netcorenoc/__init__.py`, `CHANGELOG.md` agree) |
| Findings | **none** — the series stays at F39 |
| Migrations | **0 added** — `user_version` stays 7 |
| Runtime dependencies | **5**, unchanged |
| Routes / capabilities / audit actions | **unchanged**, all three |
| Tests | 524 → **646** (+122, all structural) |
| Coverage | 95.46 % → **95.65–95.87 %** (run-to-run spread; every run above the baseline) |
| `make eval` | **byte-identical** |
| Largest module in `api/` | **361 lines** (`perimeter.py`), from 1 752 |

---

## 1. The new module map

`src/netcorenoc/api/` — sixteen modules, one level deep, largest 361 lines.

| Module | Lines | Cov. | What it owns |
|---|---:|---:|---|
| `perimeter.py` | 361 | 99 % | **The HTTP security boundary.** Read this first if you are reviewing security |
| `routes_scorer.py` | 271 | 98 % | the v0.6.0 scoring seam's HTTP surface |
| `routes_governance.py` | 217 | 88 % | the v0.7.0 capability and scope policy surface |
| `routes_read.py` | 203 | 98 % | the nine viewer read handlers |
| `routes_admin.py` | 180 | 89 % | users, service tokens, runtime config |
| `routes_auth.py` | 170 | 92 % | login, logout, `/api/me`, password change |
| `routes_operate.py` | 161 | 95 % | the two admin resets and the three editor writes |
| `routes_events.py` | 117 | 90 % | the SSE stream |
| `models.py` | 111 | — | eleven pydantic models, `QuietServer`, two `MAX_*` limits |
| `app.py` | 107 | — | `create_app`, and nothing else |
| `__init__.py` | 100 | — | the compatibility surface: 38 re-exported names |
| `governance_cache.py` | 97 | — | the per-request cache of the two stored policies |
| `declare.py` | 91 | — | the registration gate |
| `routes_audit.py` | 89 | 100 % | quarantine, audit read/export/prune |
| `routes_static.py` | 80 | 96 % | health, readiness, the UI, the asset allowlist |
| `context.py` | 71 | — | `AppContext` |

Sixteen rather than the fourteen originally sketched: `perimeter.py` measured 444 lines before the
registration gate was written and about 510 after, and the 400-line guard is a hard constraint that
this very release installs. `governance_cache.py` (a cache is not a decision) and `declare.py` (one
decision: *is this route declared?*) came out on the placement rule, not on arithmetic
(DECISIONS #85).

### What moved, and what did not

**Moved:** thirteen module-level symbols and eleven `create_app` closures into `perimeter.py`;
eleven pydantic models and `QuietServer` into `models.py`; forty handlers and their twelve
group-private helpers into nine `routes_*` modules.

**Not touched, not one character:** `correlate.py`, `scoring.py`, `learn.py`, `receiver.py`,
`preview.py`, `rootcause.py`, `severity.py`, `varbind_profile.py`, `store.py`, `main.py`,
`src/netcorenoc/ui/`, `Dockerfile`, `docker-compose.yml`, `MANIFEST.in`, every migration.

`rbac.py` changed in exactly one way: it gained `ROUTE_SCOPE` and two import-time assertions.
`PERMISSIONS`, `ROUTE_PERMISSIONS`, `PUBLIC_ROUTES`, `AUDITED_DENIED_PERMISSIONS`,
`RECOVERY_CAPABILITIES` and every function are untouched.

---

## 2. The parity proof, and its numbers

```
BODY PARITY OK      43/43 handler bodies hash identically to v0.7.1
DECORATOR PARITY OK 43/43 decorators differ only by @app. -> @route.
ROUTE TABLE         48/48 entries identical, in order
EVAL                byte-identical
```

The Phase 0 baseline hashed `inspect.getsource(endpoint)`, decorator line included. Workstream 3
rewrites that line on every handler **by design**, so during Phase 3 the baseline was split in two
— a **body hash** (signature + docstring + body, which must not move) and a **decorator delta**
(which must be exactly `@app.` → `@route.` and nothing else). Both are computed from the pristine
v0.7.1 `api.py` at commit `9cb2086`. The pair is strictly stronger than the single hash it
replaces: the first proves nothing in the handler moved, the second proves the only thing that did
is the one token the release exists to change.

### The guards earned their place

Four real defects were caught during the build, each by a gate that existed before the step that
broke it:

1. **A deleted decorator.** Removing the `situation_in_scope`/`audit_scope_denial` block took
   `@app.post("/api/situations/{sid}/feedback")` with it, and the route silently vanished. No
   handler text changed, so no body hash could see it — the Phase 2 route-order test named the
   exact index on the next run.
2. **A regex that substituted inside strings.** The first mechanical pass turned `"no-store"` into
   `"no-self._store"` and `"governance.fallback"` into `"self.governance.fallback"`. Discarded and
   rewritten with `tokenize`, which touches NAME tokens only. Recorded because it is exactly the
   class of error a "mechanical" transformation is assumed not to make.
3. **A scanning guard that would have gone vacuous.** `inspect.getsource()` on a *package* returns
   only `__init__.py`, so four v0.7.1 text guards would have kept passing against almost no source.
   `tests/apisource.py` gives them their corpus back and refuses a module nobody has placed in it.
4. **A dropped `import time`.** Caught by `ruff` and `mypy` before the tests ran.

---

## 3. The declaration discipline, and the defect class it closes

F34 existed because a route's **scope posture was expressed nowhere at all**. Three editor write
routes did not have one, and no table, no test and no reviewer could notice the omission — there
was nothing to notice.

`rbac.py` now carries two declarations per route:

| | `ROUTE_PERMISSIONS` | `ROUTE_SCOPE` |
|---|---|---|
| Answers | what capability does this route require? | does the caller's visibility scope reach it? |
| Entries | 39, unchanged | **39, new** |
| Missing entry fails | at registration | at registration |

`ROUTE_SCOPE` at v0.7.2: **12 `scoped`**, **5 `unscoped`** (each with a written justification,
required by test), **22 `admin_only`** (derived from `PERMISSIONS` at import, in both directions, so
the two tables cannot disagree).

`api/declare.py::DeclaredRoutes` is the only registration path. A route absent from either table
raises while `create_app` builds the application — so an appliance carrying an undeclared route
does not start. `PUBLIC_ROUTES` and non-`/api` paths are exempt **by explicit consultation**, never
by omission. A test asserts no raw `@app.<verb>` decorator survives anywhere in the package, and a
second confines `add_api_route` to the module owning the static-asset allowlist.

**Honest about what this is.** `ROUTE_SCOPE` is *descriptive*: nothing reads it at request time, and
41 parametrised tests check every declaration against the route's observed behaviour. That moves the
guarantee from **nothing** to **declared and tested** — it does not reach **structural**. Injection
would change control flow, and control flow is behaviour, which this release ships none of
(DECISIONS #80). The step from tested to structural belongs to a later version and is on the
ROADMAP.

---

## 4. The debt allowlist and its schedule

Installed in **Phase 2, against the unmodified tree**, so every later step was measured by a rule
that predated it. Two properties make it a ratchet: an allowlisted module may not grow, and an entry
that drops within the limit must be deleted.

| Module | Lines | Owner | Note |
|---|---:|---|---|
| `store.py` | 1 512 | **v0.7.3** | specified in `MODULE-ARCHITECTURE.md` §6 |
| `main.py` | 1 079 | **v0.7.3** | specified in `MODULE-ARCHITECTURE.md` §7 |
| `shaping.py` | 476 | v0.7.4 | two axes in one file; split along that seam |
| `rbac.py` | **436** | v0.7.4 | **added by this release** |
| `varbind_profile.py` | 417 | v0.7.4 | one extraction, not a package |

`api.py`'s entry **left** the list during Phase 4 — `test_allowlist_only_shrinks` failed the build
until it was deleted. That obligation was taken on in Phase 2 and discharged by the guard, not by a
checklist.

`rbac.py` is the one honest cost. `ROUTE_SCOPE` pushed it from 348 to 436 lines, and every
alternative was worse: moving the table out breaks the single-source-of-authority property; trimming
the prose deletes the per-entry justifications a test requires; splitting `rbac.py` is a second
structural change to the authorization authority in a release that ships no behaviour. So it is a
dated entry with an owner and a named split seam — the declaration tables on one side, the
capability-policy parser and resolver on the other. A guard that gets weakened the first time it
says something inconvenient is not a guard.

---

## 5. Decisions (#75–#87)

| # | Decision |
|---|---|
| 75 | The layer model and placement rule, decided once for the whole project |
| 76 | The perimeter boundary is *"may this request proceed?"*, not *"which region of the file?"* |
| 77 | `Perimeter` is a class — free functions would have grown a parameter each and edited every call site |
| 78 | `AppContext` + a **mandatory** local-rebinding block, so handler text never changes |
| 79 | The package is `api/`, one level deep — **explicitly superseding** SCOPE-0.5's "prefer flat" and ADR #74's flat `perimeter.py`, for this subtree only |
| 80 | `ROUTE_SCOPE` is descriptive now, enforcing later |
| 81 | The module-size guard ships with a shrink-only allowlist, installed **before** the move |
| 82 | Path normalisation deferred, with the three inconsistencies named |
| 83 | `store.py`/`main.py` specified here, built in v0.7.3; the `Store` mechanism deliberately left open |
| 84 | The four source-reading tests get a new **source**, not new assertions |
| 85 | Sixteen modules, not fourteen: the 400-line guard outranks the planned module list |
| 86 | `audit_row` reached through `ctx.perimeter`, to keep `mypy --strict` checking its arguments |
| 87 | `rbac.py` joins the debt allowlist rather than losing the table or the prose |

---

## 6. Deferred work, each with its reason

| Deferred | Owner | Why not here |
|---|---|---|
| Splitting `store.py`, `main.py` | **v0.7.3** | Independent of the HTTP layer and independently gateable; five large moves in one autonomous run risks a half-moved tree with no clean stopping point. Fully specified in `MODULE-ARCHITECTURE.md` §6–§8, including the one-`Store`/one-connection/one-lock invariant, the two candidate mechanisms with their `mypy --strict` costs, and the gates v0.7.3 inherits |
| Making `ROUTE_SCOPE` enforcing | ROADMAP | Injection changes control flow; control flow is behaviour |
| Route-path normalisation | ROADMAP | Three named inconsistencies. A public contract change in the same release that moves forty handlers means a broken matrix cannot be attributed |
| The two layer violations (`main.py` → `api`, `runtime.py` → `receiver`) | ROADMAP | A violation found while writing an architecture document is a line, not a fix in the release that found it |
| `shaping.py`, `varbind_profile.py` | v0.7.4 | On the allowlist with named owners |
| Splitting `models.py` per route group | **rejected** | Eleven fragments across nine modules make the request surface harder to audit, not easier |
| DI containers, service layers, repository patterns | **rejected** | Three new concepts, each justified by a defect this project actually had, and no fourth |

Nothing was fixed that was noticed while reading the code. That was the rule, and it held: a fix
hidden inside a move is invisible to review, which is exactly how F34–F39 stayed invisible for a
release.

---

## 7. Verification summary

```
ruff check / format     clean            mypy --strict     88 files, clean
vulture (dead code)     clean            bandit            clean
pip-audit               no vulnerabilities                 release-check     0.7.2 everywhere
pytest                  646 passed       coverage          95.65-95.87 % (was 95.46 %)
eval                    byte-identical   route order       48/48, in order
handler bodies          43/43            decorators        43/43
wheel + sdist           16/16 api modules, full UI, 7 migrations, d3 CHECKSUMS.txt
docker compose config   valid
```

**The upgrade, verified rather than assumed.** A live v0.7.1 database — produced by installing the
real v0.7.1 tree in its own virtualenv and driving 40 traps through the engine, plus users, tokens,
four scorer configurations, both governance policy kinds with history, labels, feedback and a
hash-chained audit log — was opened by v0.7.2. No migration ran. The whole store snapshot compared
identical (schema SQL hash, 24 tables, every row count, every read model). The audit chain verified
with the same final hash. All **46 routes returned byte-identical responses** under both versions on
the same data, headers included.

**Honest caveats.**

* The Docker **daemon** is unavailable in this build environment, so the image was not built and
  `docker compose up` was not run. `docker compose config` validates, the Dockerfile is unchanged,
  and the equivalent was exercised in its place: the wheel installed into a clean virtualenv and run
  as a real process via the Dockerfile's own `CMD`, serving all seven public paths with CSP and
  `X-Frame-Options` intact and 401-ing every `/api` route unauthenticated. The image build remains
  for the maintainer.
* Five dotfile paths present in the v0.7.1 source archive (`.github/`, `.gitignore`,
  `.env.example`, `.editorconfig`, `.dockerignore`) were missing from the working clone this build
  started from. They were restored **byte-identical from the archive** in a separate first commit,
  because `tests/test_workflows.py` — the GitHub-Actions SHA-pin lint — asserts at least one
  workflow exists. No content changed.
* `tests/` gained three files and eight changed lines outside them. Every one of those lines is
  enumerated in `docs/gates/v0.7.2-phase-5.md` §3: four source-acquisition lines, three scanned
  decorator tokens, one scanned `store.rollback()` token, and `test_structure.py`'s module list. No
  assertion's meaning changed.

---

## 8. What this release does not buy

Said plainly, because a build report that only lists wins is not a build report.

**v0.7.2 did not make NetCoreNOC safer.** The same code in different files has the same behaviour —
that is the release's central proved claim, and it is therefore also its limit. Every caveat in
`SECURITY-REVIEW-0.7.1.md` §4 stands unchanged: scoping is still a presentation control and not
tenant isolation; the rate limiter is still in-memory and per-address; `SafeScorer` still cannot
preempt a call that never returns; the audit chain is still append-only by convention within one
SQLite file; `label` still has no foreign key.

What it buys is two things. A perimeter one person can read end to end in one sitting, instead of
eleven closures scattered through 1 752 lines. And a registration discipline under which the class
of defect F34 belongs to fails at startup rather than surviving to production. Neither is a fix, and
this report does not present them as one.

The most valuable thing left behind is probably none of that: it is the **debt allowlist**, which
put five oversized modules and their owning releases into CI instead of into a code review two years
from now — and which, on its first outing, made this very release write down that it had made one of
them worse.
