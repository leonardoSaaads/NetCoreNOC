# Security review — NetCoreNOC v0.7.5

**Scope of this review.** One confirmed finding, **F42**, continuing the F1–F41 series; a
**correction to a completeness claim v0.7.4 made and this project has been trusting**; and an
assessment of the operator-feedback acquisition path, which this release repairs without closing the
problem it belongs to.

The correction matters more than the fix. F42 is fifteen lines of guard. The claim that guard was
described by — *"complete by construction"* — is the kind of statement the project's whole
verification posture rests on, and it was not supported by the argument given for it.

---

## 1. What this release changed, and what it did not

| | |
|---|---|
| Changed | `api/declare.py` (the gate), `ui/app.js` (three places), `tests/test_documentation.py` (one regex) |
| New routes, capabilities, audit actions, migrations, served paths | **none** |
| New dependencies, runtime or dev | **none** (five runtime, eleven dev, unchanged) |
| `make eval` | **byte-identical** to v0.7.4 — the engine, store, correlation and scoring seam are untouched |
| The UI | still four files; CSP unchanged; no `innerHTML` |
| API contract | `POST /api/situations/{sid}/feedback` still takes `{verdict}` and nothing else |

Four intentional behaviour changes, all listed in `SCOPE-0.7.5.md` §2. One is at startup (the gate);
three are in the browser.

---

## 2. Findings — F42

| ID | Severity | Title |
|---|---|---|
| **F42** | **Med** | The declaration gate fails open on route shapes it cannot classify, and its coverage depends on an unpinned dependency's internal representation |

### F42 — the gate skips what it cannot read

**Location.** `src/netcorenoc/api/declare.py`, `assert_every_route_is_declared`, v0.7.4 form:

```python
for route in app.routes:
    path = getattr(route, "path", None)
    if path is None:
        continue
    for method in sorted(getattr(route, "methods", set()) or set()):
        if method not in ("HEAD", "OPTIONS"):
            require_declaration(method, path)
```

**Threat (A3 malicious/careless contributor, A6 operator error).** A route reaches a running
appliance without declaring the capability it requires or its visibility-scope posture, and is
therefore outside the authorization map. This is the same threat F40 addressed; F40 closed the
*registration paths* and left the *route shapes* open.

**Reproduced by execution**, not by reading, with a passing control and a served-200 confirmation
for every shape — full transcript in `../gates/v0.7.5-phase-0.md` §2. Every probe application was
built `docs_url=None, redoc_url=None` to match `create_app`; a bare `FastAPI()` carries an
undeclared `/docs` and would have made every probe look like a catch.

| Shape | Registered by | Branch it escapes through | Served |
|---|---|---|---|
| `fastapi.routing._IncludedRouter` | `app.include_router(...)` | `path is None` | `GET /api/undeclared-via-router` → **200** |
| `starlette.routing.Mount` (sub-app) | `app.mount(...)` | empty `methods` | `GET /api/sub/...` → **200** |
| `starlette.routing.Mount` (`StaticFiles`) | `app.mount(...)` | empty `methods` | `GET /api/static/leak.txt` → **200**, file contents returned |
| `fastapi.routing.APIWebSocketRoute` | `app.add_api_websocket_route(...)` | empty `methods` | websocket handshake completed |
| `fastapi.routing.APIRoute`, `HEAD` only | `add_api_route(methods=["HEAD"])` | the `HEAD`/`OPTIONS` exemption | `HEAD /api/undeclared-head` → **200** |

**There are two fail-open branches, not one.** The finding's brief described the `Mount` cases as
evading through a missing `.path`; on starlette 1.3.1 the `Mount` **does** carry a path, and evades
because `methods` is `None`, so `sorted(... or set())` is empty and the inner loop never runs. This
is recorded because it changes the fix: patching only the `path is None` branch — the obvious
reading of the brief — would have closed **one shape out of five**.

**Not exploited.** v0.7.5 uses none of these shapes: `create_app` produces exactly two route classes
and 48 method/path pairs, all declared. Exactly as with F40 and F41, the finding is a latent hole in
a guard whose entire value is completeness.

### 2.1 The part that generalises: coverage that changed without a commit

The same probe on **`fastapi==0.115.0`, the floor of this project's own pin**, in a throwaway
virtualenv:

| | `app.routes` contains | Gate verdict |
|---|---|---|
| **fastapi 0.115.0** | `APIRoute path='/api/undeclared-via-router' methods={'GET'}` | **REFUSED** |
| **fastapi 0.141.1** | `_IncludedRouter path=None methods=None` | **skipped** |

`pyproject.toml` says `fastapi>=0.115` with no upper bound, there is no lockfile and no constraints
file, and `.github/workflows/ci.yml` runs a bare `pip install -e .[dev]`. So the gate's completeness
was a property of whatever pip resolved on the morning of the build. **It regressed in CI with no
commit, no test failure, and nobody touching `declare.py`** — FastAPI changed how `include_router`
is represented on `app.routes`, and a guard that assumed a flat `.path`/`.methods` shape silently
stopped covering an entire registration path.

Shapes 2–5 evade on **both** versions and are not version-dependent.

### 2.2 The fix — refusal on the unknown, not recursion into it

`declare.KNOWN_ROUTE_SHAPES = (APIRoute, Route)`. Any object on `app.routes` whose type is not
exactly one of them raises `UndeclaredRouteError` naming `type(route).__module__`,
`type(route).__name__`, and where registration belongs. Within a known shape every method is
checked, with `HEAD` skipped **only when `GET` is present** — the sole case Starlette synthesises,
confirmed by execution — and the `OPTIONS` exemption removed outright, because `OPTIONS` is never
synthesised into `route.methods` and the exemption therefore fired on nothing.

**Why refusal is complete by construction where shape-enumeration was not.** The v0.7.4 traversal
answered *"is this route declared?"* for objects it could parse and **silently answered nothing** for
the rest. The set of objects it could parse was an unstated assumption about a third-party library.
The new traversal answers one of two things for **every** object on `app.routes`: either it is a
shape the gate can check, and it is checked; or it is not, and it is refused. There is no third
outcome and no object for which the loop is a no-op. That is a property of the control flow, not of
the dependency — a future FastAPI inventing a sixth shape produces a refusal, not a gap.

**Teaching the traversal to walk each container was rejected** (DECISIONS #98). `dir()` on the
`_IncludedRouter` shows the only ways in are `include_context`, `original_router`,
`effective_route_contexts`, `effective_candidates` — undocumented FastAPI internals, most
underscore-prefixed. A gate walking them would again be correct only for the dependency versions
whose internals it happened to match, which is precisely the defect. Confirmed by execution in
Phase 0 §2.5, not assumed.

Matched on **exact type**, not `isinstance`: `APIRoute` subclasses `Route`, so an `isinstance` test
would admit any future subclass of either unexamined — the same fail-open, one inheritance level
down.

**Regression tests.** `test_f42_*` ×14, of which **12 were proven to fail** by stashing `declare.py`
alone (transcript in `../gates/v0.7.5-phase-2.md` §2). The two that stay green on the unmodified tree
are `test_f42_an_untouched_app_is_not_refused` — the control, whose job is to pass on both trees —
and `test_f42_every_path_served_today_still_registers`, which asserts the gate got no *looser* and
must therefore hold before and after.

**Status: met.**

---

## 3. The correction to v0.7.4's completeness claim

This section exists because the project's guards are trusted on the strength of claims like this one,
and a wrong claim in a security review is a more durable defect than a wrong line of code.

**What was claimed** (`SECURITY-REVIEW-0.7.4.md` §2, and `MODULE-ARCHITECTURE.md` §10.1):

> `assert_every_route_is_declared` … is complete *by construction*… **nothing here lists the ways a
> route can be registered.**

**Why the argument does not support the claim.** The second clause is true and the first does not
follow from it. The traversal enumerates no *registration mechanism* — correct — but it assumes a
*shape*: a flat object exposing `.path` and `.methods`. Declining to enumerate mechanisms while
silently enumerating shapes is still enumeration; §2 is the demonstration, since five shapes walked
through it and a dependency upgrade removed a whole registration path from its view without a commit.

This is exactly the distinction v0.7.4's own build prompt (§6.5) demanded be stated out loud about
the documentation guard — and it was, there. It was not applied to the gate in the same release.

**What is claimed now.** `assert_every_route_is_declared` is complete **by construction** in a
narrower and checkable sense: *every object on `app.routes` is either checked or refused; none is
skipped.* The set of checkable shapes, `KNOWN_ROUTE_SHAPES`, is **enumeration and is labelled as
such** in the code and in its test's docstring. What makes it maintained rather than merely written
down is `test_f42_the_live_app_produces_exactly_the_known_shapes`, which fails on the day a
dependency changes the representation, naming the new class.

`SECURITY-REVIEW-0.7.4.md` is **not edited.** It is the record of what was believed then.
`MODULE-ARCHITECTURE.md` §10.1 carries a dated correction note appended below the original paragraph,
which is left as written — "supersede in place, never rewrite".

---

## 4. The dependency-representation class of defect

Named as a class because F42 is one instance and the next one will not be in `declare.py`.

**The class.** A guard whose behaviour depends on an unpinned dependency's internal representation
can regress with **no commit, no test failure and no signal of any kind**. Nothing in the project's
CI would have reported it; the gate simply began covering less.

**What §4.4's guard covers.** `test_f42_the_live_app_produces_exactly_the_known_shapes` asserts the
set of route classes a real `create_app` produces equals `KNOWN_ROUTE_SHAPES`. A FastAPI upgrade
that changes the representation fails the suite loudly, naming the new class, on the day of the
upgrade.

**What it does not cover, stated plainly.** It detects a **new shape**. It does not detect a
**changed meaning**. If a future `APIRoute` carried its verbs somewhere other than `.methods`, the
shape set would be unchanged, the test would pass, and the gate would quietly check nothing — the
same failure mode, undetected. No test in this release closes that, and it is on `docs/ROADMAP.md`.

**Would I also pin? — an opinion, with the trade, not a commitment.**

Yes, eventually, and **not as a substitute for the guard**. A pin freezes a representation; the
guard notices when one changes. Only the second produces information. A pinned project meets the
identical silent widening the day it lifts the pin, with no signal — so a pin without this test is
strictly worse than this test without a pin.

The argument *for* also pinning is reproducibility rather than correctness: today a CI run and a
maintainer's run a month apart can resolve different FastAPI versions and therefore exercise
different code, which makes "it passed in CI" a weaker statement than it looks. The argument against
is that a floor-only constraint is what has kept this project's five runtime dependencies unpinned
and its upgrade cost near zero, and an upper bound on one of five is an inconsistent posture that
mostly generates dependabot noise.

The honest answer is that this is a **supply-chain policy question about five dependencies**, not a
route-gate question about one, and the decision protocol forbids resolving ambiguity by adding
scope. Recorded as open: DECISIONS #101, `docs/ROADMAP.md`.

---

## 5. The gate is stricter and no looser

A guard that refuses more must be shown not to refuse anything it previously served.

| Check | Result |
|---|---|
| Every path served at v0.7.4 still registers | **yes** — 48 method/path pairs, asserted by `test_f42_every_path_served_today_still_registers` |
| The live app's shape set equals the allowlist | `APIRoute` + `Route`, exactly |
| F40 test set (5) | **passes unedited** |
| F41 test set (14) | **passes unedited** |
| Authorization matrix, route-map completeness (`tests/test_rbac.py`) | **passes unedited** |
| Route-order parity table (48 entries) | **passes unedited** |
| `create_app`'s last statement before `return app` | still the assertion; `test_f40_the_assertion_runs_before_create_app_returns` still parses the source to prove it |

No test assertion was edited. The changes to `tests/test_declaration.py` are additive.

---

## 6. The operator-feedback acquisition path

### 6.1 What was wrong

`renderSituations` called `clear(sits)` as its first statement, destroying every situation card every
two seconds (`SSE_UPDATE_S = 2.0`) — including the one the operator had open, and the feedback
buttons inside it. The rebuilt detail was set to `display: block` immediately and filled only after a
network round trip, from an **un-awaited** call.

The security-relevant failure is **not** the flicker. It is that a click can land on a card rebuilt
between the operator's visual decision and their mouse-down, recording
`POST /api/situations/{sid}/feedback {"verdict": "confirm"}` against a membership the operator never
evaluated. That is a **silently wrong label**, and it is worse than a missing one: a missing label is
visible as absence and can be counted; a wrong one is indistinguishable from a considered one at
every layer downstream — `learn.penalize()` acts on it, the v0.8.0 dataset records it, a v0.9.0 model
trains on it — and **nothing in the system can detect it**, because the record carries no evidence of
what was on screen.

This is an **integrity** finding in STRIDE terms, against the learned state and against a dataset
that does not exist yet. It is not assigned an `F` number: it is a UI defect on the operator's own
path with no attacker in the model — no privilege is crossed and no attacker-controlled input is
involved — and it was already specified and scheduled in `FEEDBACK-PATH-0.7.5-DRAFT.md`. It is
recorded here because its consequence is a data-integrity one and the review is where that belongs.

### 6.2 What v0.7.5 closes

An expanded card's detail node survives the rebuild; the detail container is never displayed empty;
a held card carries a staleness marker. The click now lands on the card the operator was reading.

### 6.3 The residual — **v0.7.5 does not make the label traceable to what was on screen**

Stated explicitly so nobody reads this release as having solved label provenance.

The verdict is recorded against the *situation*, whose membership continues to change. An operator
who judged four members and clicked confirm produces a row indistinguishable from one who judged
nine. v0.7.5 makes the click **deliberate**; it does not make it **interpretable**.

Recovering *which* membership a verdict was about is the **membership fingerprint**, and it is
**v0.8.0** (`FEEDBACK-DATASET-0.8-DRAFT.md` §2.2) — captured *with* the label as evidence, explicitly
**not** as an optimistic-concurrency precondition, because rejecting an observation discards a
statement that was true, and in a system that updates every two seconds it would trade a race for a
livelock.

One consequence for v0.8.0, recorded in that draft's new §0: `feedback` rows written **before**
v0.7.5 were acquired over the defective path, so an unknowable fraction are clicks the operator did
not mean. If they are carried into the dataset the bias report must count them **separately** — they
are the one population whose noise is known to be non-random.

### 6.4 The staleness trade — a human-factors residual, accepted

Holding the card fixes the race by **freezing what the operator is looking at**. That trades a wrong
label for a **stale** one: the operator may confirm a four-member grouping that now has nine.

A stale label is a label *about a subset*, which is recoverable information; a wrong label is not.
The trade is correct. But it is only correct **if the operator knows**, and the whole of the
mitigation is a static text badge reading `held while open`.

**A marker the operator ignores is a marker that did not work.** There is no technical control behind
it: no confirmation step, no forced acknowledgement, nothing that fails closed if it goes unread. In
an incident, an operator under time pressure reading a card they believe is live is the realistic
failure, and this design does not prevent it — it only informs. **Accepted as a residual risk**, and
recorded as one rather than described as a fix. Reducing it further means capturing what was on
screen rather than warning about it, which is again the v0.8.0 fingerprint.

### 6.5 No new surface

No route, capability, audit action, migration, dependency (runtime or dev) or served path. The UI is
four files. The CSP is unchanged and still forbids inline script and style. No string reaches the DOM
other than through `textContent`/`createTextNode` — the marker's text is a module constant and its
explanation is set through the `title` property, never markup. `test_ui_source_has_no_f1_antipatterns`
passes unedited.

---

## 7. Critical analysis

### 7.1 The automated suite does not prove this release's behavioural claims

Three of the four intentional behaviour changes are browser behaviour, and **there is no JavaScript
runtime in this repository** — evidenced across `pyproject.toml`, the `Makefile`, `flake.nix` and
`.github/workflows/` in `../gates/v0.7.5-phase-0.md` §5. Every UI test is a source-inspection test.

Which claims rest on the manual protocol (`../gates/v0.7.5-manual-verification.md`) rather than on
CI:

| Claim | Proved by |
|---|---|
| An expanded card's DOM node survives an SSE update | **manual Test A** |
| The feedback buttons keep working across updates | **manual Test A / D** |
| No reachable state has the detail container displayed and empty | **manual Test B** (requires throttling) |
| The marker appears while held and disappears on collapse | **manual Test C** |
| A click lands on the card the operator was reading | **manual Test D** |
| The operator *notices* the marker | **nothing — §6.4, residual** |

**That protocol was written by the v0.7.5 build and was not executed by it**; there was no browser and
no operator. It is recorded as unexecuted in the gate evidence, in the build report, and in §9 of the
protocol itself.

The alternative — adding a JS harness — was rejected (DECISIONS #99) for two reasons, the second of
which is not obvious: it is out of scope, **and** the build container happens to carry `node` and
`bun` on `PATH` while CI, the Nix dev shell and a maintainer's machine do not, so a test written
against them would have been green only on the machine that wrote it. A test that passes for
environmental reasons is worse than an acknowledged gap, because it looks like coverage.

The six structural assertions are real tripwires — five go red against the v0.7.4 source — and each
says, in its own docstring, that it asserts the shape of the source and not the behaviour of the
browser, naming the manual test that does prove the claim. **A green suite here must never be
reported as having verified the acquisition path.**

### 7.2 The documentation guard is near-complete on element tags — and its other half is not

The element-tag half went from **15 of 48 tags visible (31%)** to **49 of 49 outside fenced blocks
(100%)**, with the failure mode demonstrated green→red→green rather than asserted.

**Its forbidden-phrase half remains enumeration, and remains spelling-sensitive.** It is a literal
list of eleven strings the repository actually carried, matched over normalised text. It does not
generalise and was never meant to: deciding whether an arbitrary English sentence asserts a release
theme is not something a test can do honestly. It is also sensitive to spelling — `->` and `→` are
different strings, and a rephrasing defeats it entirely.

That is **correct by design** and is restated here so that nobody reads "the documentation guard now
sees 100% of element tags" as "the documentation guard now catches contradictions". It catches
*marked* claims completely, and *prose* claims only in the eleven specific forms that already
happened once.

### 7.3 What was found and deliberately not fixed

`renderEntityDetail` (`ui/app.js:583`) has the same clear-then-fill shape §5.2 fixed in
`renderDetail`, so the entities panel retains a displayed-and-empty window. **Not fixed**: it is not
on the label path and carries no label-integrity consequence, and a fix smuggled into a small diff is
invisible to review — which is the whole reason the diff is small. On `docs/ROADMAP.md`.

### 7.4 The limits of this review

It examined what this release changed: the declaration gate, the acquisition path, and one test
helper. It is **not** a re-review of the whole attack surface. F1–F41's controls were not re-tested
beyond what CI asserts on every commit. The last full pass remains v0.7.1's, against the write
perimeter.

---

## 8. Mapping to `threat-model.md`

| STRIDE | Threat | Control added or confirmed by v0.7.5 | Check |
|---|---|---|---|
| **Elevation of privilege** | A route of a shape the gate cannot classify — an included router, a mount, a websocket route, a `HEAD`-only route — reaches a running appliance undeclared, outside the authorization map | **F42.** `KNOWN_ROUTE_SHAPES` plus refusal on anything outside it. Every object on `app.routes` is checked or refused; none is skipped | `test_f42_*` (14), 12 proven red on the unmodified tree |
| **Elevation of privilege** | The gate's coverage silently narrows when an unpinned dependency changes its route representation — no commit, no failing test | The live app's shape set is asserted to equal the allowlist, so the change fails loudly on the day of the upgrade, naming the new class | `test_f42_the_live_app_produces_exactly_the_known_shapes` |
| **Elevation of privilege** | A `HEAD` or `OPTIONS` route registered deliberately is exempted by a rule written for *synthesised* verbs | `HEAD` skipped only when `GET` is present; the `OPTIONS` exemption removed | `test_f42_a_head_only_route_is_refused…`, `…a_get_plus_head_pair_is_not_double_checked`, `…options_is_never_synthesised…` |
| **Tampering / integrity** | A feedback verdict is recorded against a situation membership the operator never evaluated — a silently wrong training label that nothing downstream can detect | The expanded card is held across SSE updates; the detail container is never displayed empty. **Partial:** the label is deliberate, not traceable | manual protocol Tests A/B/D; six structural assertions; feedback and SSE contract tests unedited |
| **Tampering / integrity** | The operator judges a **stale** membership without knowing it is stale | A `held while open` marker on every held card. **Human-factors residual — informs, does not enforce** | manual protocol Test C; `test_the_staleness_marker_exists_and_is_guarded_by_expanded` |
| **Repudiation / integrity** | The recorded verdict cannot be tied to what was on screen | **Not closed — v0.8.0.** The membership fingerprint, captured with the label as evidence, not as a precondition | `FEEDBACK-DATASET-0.8-DRAFT.md` §2.2; §6.3 above |
| **Information disclosure** | The OpenAPI schema is readable without authentication | **Unchanged, still not fixed.** Pre-existing, recorded at v0.7.4. Reconnaissance value only | `docs/ROADMAP.md` |
| **Tampering** | A declared scope posture is wrong rather than absent | **Unchanged and unresolved.** `ROUTE_SCOPE` remains descriptive, not injected by the perimeter | `tests/test_declaration.py` behavioural suite; ROADMAP (DECISIONS #80) |

---

## 9. Verdict

**One finding, F42, met.** Reproduced by execution in five shapes with a passing control and a
served-200 for each, plus a two-version dependency comparison showing the gate's coverage changing
without a commit. Fixed by replacing a silent skip with a refusal, so that every object on
`app.routes` has an outcome. Covered by 14 regression tests, 12 proven to fail on the unmodified
tree. The gate is stricter and no looser: every path served before is served now, and the F40, F41,
authorization-matrix, route-map and route-order suites all pass unedited.

**One completeness claim corrected.** v0.7.4 called the traversal complete by construction on an
argument that established something narrower. The claim is restated in a form that is checkable, the
enumeration inside it is labelled as enumeration, and the record of what was believed then is left
untouched.

**One integrity defect closed, one left open on purpose.** The operator's click now lands on the card
the operator was reading. What the operator was reading is still not recorded, and that is v0.8.0's
membership fingerprint — said here so that nobody reads v0.7.5 as having solved label provenance.

**And one thing this review will not claim.** The behavioural half of this release is verified by a
manual protocol that this build wrote and did not run. The suite is green; the suite does not prove
it.

**The finding series stands at F42.** The next review continues from **F43**.
