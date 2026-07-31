# Build report — NetCoreNOC v0.7.4

**Theme: close every loose end the v0.7.x series leaves behind, so v0.7.5 and v0.8.0 start from a
repository with no contradictions and no unowned debt.**

Three things were open at v0.7.3. All three are closed.

| | Result |
|---|---|
| Tests | **701 → 754** |
| Coverage | **95.80 % → 95.81–95.85 %** (spread is `receiver.py`'s known timing variance) |
| `make eval` | **byte-identical** (`sha256 c333ca46…3132`) |
| Modules over the 400-line guard | 4 → **1** (`engine.py`, cohesion-exempt by design) |
| `DEBT_ALLOWLIST` | 3 entries → **empty**, and still defended in both directions |
| Security findings | F39 → **F41** (F40 and F41, the first since v0.7.1) |
| Decisions | #92 → **#97** |
| Migrations / routes / capabilities / audit actions / dependencies | **all unchanged** |
| Intentional behaviour changes | **exactly two**, both at startup, neither on the request path |

---

## 1. The two findings, and why the fixes are complete by construction

Both holes were found by adversarial probing of v0.7.2's registration gate, recorded in
`MODULE-ARCHITECTURE.md` §10.1, and **reproduced by execution** in Phase 0 before either was fixed —
line for line matching the output §10.1 recorded a release earlier. That mattered: it meant this
release *executed* a specification rather than re-deriving one.

They are the same defect at two levels: **a guard that lists the cases it knows about.**
`DeclaredRoutes` listed three verbs and one registration style; `require_declaration` listed one path
prefix. Each was true of the surface in front of it and silent about the next thing anyone would
write.

**F40 — the gate covered three verbs and only the decorator form.** A route registered directly on
the FastAPI application reached the route table without ever consulting the gate: registration raised
nothing, the route count went 4 → 5, and the route was in neither declaration table.

The fix is `assert_every_route_is_declared(app)`, called as the last statement of `create_app` before
it returns. It is complete by construction because **it names no verb and no registration
mechanism** — it inspects the *result*. Whatever produced a route, the route is on `app.routes`, and
every entry goes back through the same `require_declaration` the decorator calls. Wrapping `put` and
`patch` would have closed both reproductions and left the property no stronger; a reviewer would
still have had to ask "is that all the ways?" with no way to answer.

Running it inside `create_app` rather than in a test was deliberate, and is itself asserted:
`test_f40_the_assertion_runs_before_create_app_returns` parses `create_app`'s source and requires
nothing but `return app` to follow the call. A guard that runs in CI but not at startup is one an
operator can ship past.

**F41 — the exemption was by path prefix.** `require_declaration` returned early for anything outside
`/api` — true of today's public surface and accidentally true of everything else.
`require_declaration("GET", "/metrics")` returned cleanly, and `/metrics` is already on the ROADMAP.

The fix is `UNAUTHENTICATED_PATHS`, an explicit allowlist. Membership is now a **reviewable claim** —
"no capability is required to fetch this" — rather than a consequence of four characters. It is
asserted against what is served from two independent directions: against `routes_static.py`'s
allowlist plus the health surface, and against the non-`/api` routes of a built application. The
second is not redundant — `/openapi.json` is registered by FastAPI itself, and no source-level
derivation would ever have mentioned it.

**Ten regression tests fail on the unmodified tree.** Nine more pass on *both* trees by design: eight
public-surface cases and the decorator-refusal control, which are the direction the fix must not
change. A regression test that only fails-then-passes proves the hole closed; these prove nothing
else was.

---

## 2. The three splits, and the correction that came out of measuring

`DEBT_ALLOWLIST` reaches **zero**. Every module is at or under 400 lines except `engine.py` (542),
which is `COHESION_EXEMPT` permanently.

| Was | Became | Largest |
|---|---|--:|
| `shaping.py` (476) | `shaping/` — `fields.py` 88, `scope.py` 302, `project.py` 110, `__init__.py` 110 | 302 |
| `rbac.py` (436) | `rbac/` — `tables.py` 277, `policy.py` 197, `__init__.py` 79 | 277 |
| `varbind_profile.py` (417) | `varbind_profile.py` 305 + `varbind_accum.py` 154 | 305 |

**All 56 function bodies moved as identical text**, proved by a `sha256` table taken in Phase 0 and
recomputed in Phase 5. The mechanism is v0.7.3's, and so is the sentence: *the enclosing module
changes, the code does not.*

### `shaping.py` had three parts, not two

`MODULE-ARCHITECTURE.md` §10.2 — a **binding** document — recorded the seam as two axes. Phase 0
classified every top-level symbol from the AST before accepting that, and found three.

The projections (`filter_rows`, `project_graph`, `project_situation_detail`) are not a third *axis*;
they are the **consumer** of the other two. Each takes a `Scope` produced by the scope axis and
returns a response body, which is the field axis's subject. Forcing them into `scope.py` would put
response-body construction in the module that owns the scope decision; forcing them into `fields.py`
would put `Scope` handling in the module that owns field rules. A two-way framing is precisely what
makes one of those look necessary.

Recorded as DECISIONS #95, with §10.2 **superseded in place** — the original text left as written,
because it was right that a measurement was needed and right about the other two modules. A binding
document that turns out to be wrong is worth more corrected than obeyed.

Two smaller corrections came from the same measurement: `rbac.py`'s **three module-level asserts**
travel with the tables they constrain (left behind, they would run *after* the tables were built
rather than as part of building them — three structural guarantees deleted silently), and
`varbind_accum.py` is **`engine` layer**, following its parent, not cross-cutting as the build brief
assumed.

### The riskiest thing in the release, and the measurement that justified the guard

Splitting `rbac.py` could have created a second source of authorization truth. The dangerous form is
not obvious:

```python
PERMISSIONS = dict(tables.PERMISSIONS)  # equality holds; identity does not
```

Every existing test would stay green, because they all compare values. The two objects diverge the
first time either is mutated — and the test suite **already mutates** `rbac.ROUTE_PERMISSIONS` in a
fixture.

So the re-export is by **identity**, and two new tests hold that line: eight identity assertions, and
an AST check that no module under `rbac/` except `tables.py` binds any table at module level. Both
were proved before being accepted — and the proof is the number that matters:

> **With a deliberately-copying `__init__.py` in place, 218 pre-existing tests pass green.**

Neither test subsumes the other. A `policy.py`-local fallback passes all eight identity checks and is
caught only by the no-shadowing check. And the first sabotage attempt was itself wrong:
`frozenset(x)` on a frozenset returns the *same object*, so three tests passed against a "copy" that
was not one. Corrected to `frozenset(set(x))` before the proof was accepted — a guard proved against
a sabotage that is not the defect proves nothing.

The prose travelled with the tables. DECISIONS #87 recorded that neither the table nor its
justifications could be traded away to get under the guard; splitting is the fix that keeps both, so
every `"unscoped"` justification moved with its entry. `tables.py` is 277 lines, of which 241 are
table and prose. A split that had come in under the guard by shortening comments would have solved
the wrong problem.

---

## 3. The decision the project had been acting on for two releases without recording it

This is the most consequential workstream, because the next two builds are briefed from these
documents.

`docs/ROADMAP.md` said **both**:

* line 38 and line 66 — *"Customer-supplied models → v0.8.0"*
* line 114 and line 188 — *"the v0.8.0 feedback dataset"*, *"v0.8.0 is the next feature release — the
  operator-feedback dataset"*

…and the whole of the scorer-plugins draft was tagged `v0.8.0: planned`. Phase 0 enumerated every
occurrence with file and line: **eleven distinct phrasings across six live documents.**

The resequencing that settles it had been *decided and acted on* — v0.7.1 hardened the feedback
path, v0.7.3 named the dataset as what comes next — and was **never written down**. A repository that
cannot say what its next release is cannot brief the build that writes it.

**DECISIONS #93** records it with the reasoning rather than only the outcome:

* **v0.8.0 is the operator-feedback dataset.** The feedback click is the only source of human labels
  in the system, and every later ML step consumes it. Any ordering that puts a model surface first
  builds the consumer before the supply.
* **Customer-supplied models → v0.13.0**, behind the champion/challenger framework they plug into.
  Shipping the riskiest element — a new runtime dependency and a new trust surface — before the
  framework that receives it inverts how this project has sequenced every release since v0.2.0.
* **ONNX only. The Python entry-point escape hatch is rejected, not deferred.** ONNX is *data
  executed by a pinned runtime*; an entry-point scorer is *arbitrary code running as the process*.
  Recorded as a rejection so nobody reintroduces it later as an obvious convenience — the treatment
  DECISIONS #44 gave the external-criterion API.
* **The worker-process preemption harness stays a blocking prerequisite**, including that its
  worker→parent channel must not use `pickle`.

`ROADMAP-0.8-TO-0.13.md` writes the chain down as the project's own document — one screen per
release, and **why the order cannot be permuted**, as a chain of evidence dependencies rather than a
preference: you cannot train a challenger without a label; you cannot trust the label without knowing
it is scarce and biased; you cannot declare a winner without an evaluator that never saw the training
data; you cannot automate the promotion without proof of real agreement with humans.

The scorer-plugins draft was `git mv`-d and **superseded in place** with a dated box. Its analysis is
untouched — only the release changed, and §2 is marked rejected with its reasoning preserved, because
the analysis of what that path would have cost *is* the argument for declining it.

### The guard, and the two times it was not yet the guard

`tests/test_documentation.py` asserts the repository states **exactly one answer** to "what is
release X" (DECISIONS #94). It was installed against the **still-contradictory** tree and run there
first, because a guard installed green has not been shown to work.

Its first version scanned raw lines and caught 11 occurrences in 5 files — and **missed seven of the
eleven enumerated phrasings**, including one wrapping across a line break and one bolded mid-phrase.
Build-prompt Gate 1 rules that a guard catching less than the Phase 0 enumeration is not yet the
guard, so this was treated as a failure of the guard rather than a pass of the tree. Rewritten to
match normalised text — the same normalisation `test_f32_scoping_is_not_tenant_isolation_is_
documented` has used since v0.7.0 — it caught **22 occurrences across exactly the six files** Phase 0
listed.

Then it found two design bugs in itself once the documents were fixed: fenced examples in
`docs/README.md` were parsed as real claims (fixed the way `test_structure.py::_strip_code` already
fixes it for links), and a historical quotation in a then/now supersession row fired the element-tag
check (resolved by a convention: **the marked forms are for live assertions; historical mentions are
prose**).

The guard reads forward-looking documents and deliberately not `scope/`, `adr/`, `gates/`,
`releases/` or the per-release security reviews. `SCOPE-0.6.md` says v0.8.0 is customer models
because that is what v0.6.0 believed, and rewriting a record to agree with a later decision is
falsifying it. That exclusion is the guard's biggest risk, so the excluded set is itself asserted —
including that `architecture/`, where the drafts live, can never join it.

The narrow half — the enumerated forbidden phrasings — is labelled **in the test** as deliberately
not the general guarantee, because the contradiction that actually happened was in untagged prose,
which no claim-form check can see retroactively.

---

## 4. Two specifications, neither implemented

**`FEEDBACK-PATH-0.7.5-DRAFT.md`.** Every two seconds the SSE update rebuilds every situation card,
including the one the operator has expanded, and the rebuilt detail is filled only after a network
round trip — so the card is briefly `display: block` and empty.

The failure that matters is not the flicker. **A click can land on a card rebuilt between the
operator's visual decision and their mouse-down**, recording a verdict against a membership the
operator never evaluated. That is a *silently wrong* label, and it is worse than a missing one: a
missing label is visible as absence and countable in the bias report, while a wrong one is
indistinguishable from a considered one at every layer downstream — and nothing in the system can
detect it.

The four code locations were **verified against the tree, not copied from the brief**: three exact,
one off by one (`clear(sits)` is at `ui/app.js:455`, not 454). One measurement the brief does not
state sharpens the diagnosis: the *expand* path awaits `renderDetail`; the *rebuild* path does not.
The empty window is specific to the SSE rebuild of an already-expanded card, which is why it survives
casual testing.

The fix is specified minimally — reconcile by id instead of clear-and-rebuild, build into a fragment
and swap atomically — plus one **required** addition: the held card's header must say it is stale.
Holding the card trades a wrong label for a stale one, and that trade is only honest if the operator
is told. Without it the release would reintroduce the very problem it exists to fix.

**`FEEDBACK-DATASET-0.8-DRAFT.md`.** Refines rather than re-derives, with each of the four
constraints traced to the code that causes it: `correlate.py:298` discards `considered` and
`:295` truncates `links`, so the dataset is censored on both ends; `scoring.py:257` stores
weight × value, and dividing back out fails when a weight is zero, which is legal;
`learn.py:366–376` expands a split verdict to every pair, which is a sound learning heuristic and
**fabricated negatives** as training data; and `learn.py:65–86` plus `0003_entity.sql:94` mean the
state at decision time is unrecoverable afterwards.

The section that took the most care is why **rejection is the wrong primitive**. "Add a version and
reject on mismatch" is the obvious-looking design. Rejection suits *edits* — "change X from A to B"
is meaningless if X is no longer A — and not *observations*: "when I looked, I saw this" stays true.
A stale label is not invalid; it is a label about a **subset**. And at a two-second update interval,
rejecting on change trades a race for a livelock, making the acquisition path worse than the bug.
So the primitive is a **membership fingerprint captured with the label** — a dataset column, not a
precondition — and the endpoint contract needs no change at all.

---

## 5. Decisions #93–#97

| # | Decision |
|---|---|
| **#93** | v0.8.0 is the operator-feedback dataset; customer models → v0.13.0; the entry-point hatch is **rejected**, not deferred; the preemption harness stays a blocking prerequisite |
| **#94** | The documentation-consistency guard: a claim form, one machine-readable source of truth, and an asserted line between live documents and records |
| **#95** | `shaping.py` is three parts, not two — `MODULE-ARCHITECTURE.md` §10.2 corrected in place |
| **#96** | `rbac/` re-exports by identity, not equality; the three import-time asserts travel with the tables |
| **#97** | `varbind_accum.py` is `engine` layer; the shared constants move with the classes because the import graph forbids the alternative |

---

## 6. Honest caveats

1. **The gate is complete for *registration*. That is not correctness of what it guards.** A route
   can no longer be *undeclared*; it can still be *wrongly* declared. Every `ROUTE_SCOPE` entry is a
   human judgement, and `ROUTE_SCOPE` remains **descriptive rather than injected** — each handler
   still calls `scope_for` itself. The postures are checked against observed behaviour by test, which
   is much stronger than a comment and weaker than a structure. Still a ROADMAP line (DECISIONS #80),
   because injection changes control flow and control flow is behaviour.
2. **An empty `DEBT_ALLOWLIST` does not mean the codebase is well factored.** It means no module
   exceeds a line count, which is a proxy — a 399-line module doing three things passes.
   `SECURITY-REVIEW-0.7.4.md` §4.2 names what a reviewer would look at next (`api/perimeter.py`,
   which I would *not* split; `scoring.py` and `learn.py`, both one line under the guard and
   therefore about to become an obstacle) as an opinion rather than a commitment.
3. **`/openapi.json` is served unauthenticated.** Found while building F41's allowlist and **not
   fixed** — pre-existing, and this release's parity story forbids changing a served path. Low
   severity: no data, no credential, every route it names still enforced. Its value is
   reconnaissance. ROADMAP.
4. **A guard in this suite counts mentions, not calls.**
   `test_add_api_route_is_confined_to_the_static_asset_allowlist` greps for an identifier;
   documenting the F40 fix in a docstring tripped it, and the prose was reworded rather than the
   test. An AST-based caller count would say what the test means. ROADMAP.
5. **No Docker daemon was available**, so the image was not built and the container was not run.
   `docker compose config` validates. The specified equivalent was performed instead: the wheel, in
   a clean virtualenv, serving all eight public paths with the four security headers intact and
   `/api/stats` still refusing anonymously.
6. **This was not a full re-review of the attack surface.** F1–F39's controls were re-checked only to
   the extent CI asserts them. The last full pass was v0.7.1's.
7. **The build brief was wrong in four places, and the tree won each time** — `shaping.py`'s seam,
   `rbac.py`'s asserts, `varbind_accum.py`'s layer, and `MODULE-ARCHITECTURE.md` §11, which does not
   exist. All four are recorded in Phase 0 as found. A baseline that quietly agrees with its own
   brief is not a baseline.

---

## 7. What v0.7.4 leaves behind

* A declaration gate that is complete **by construction** rather than by enumeration, checked at
  startup rather than only in CI.
* No module over the size guard except one that is large by documented design, with an empty debt
  allowlist that is **still defended in both directions** — because an empty allowlist nothing
  defends is a coincidence, not a guarantee.
* An authorization authority split across two files with a runtime assertion that there is still only
  one of it.
* **A repository that states exactly one answer to what v0.8.0 is**, with a test that fails if it
  ever again states two — and a written chain from v0.8.0 to v0.13.0 explaining why the order cannot
  be permuted.
* Two specifications ready to be built, each traced to the code that motivates it.

The most useful of those is the third. The two releases after this one can be briefed from documents
that agree with each other.
