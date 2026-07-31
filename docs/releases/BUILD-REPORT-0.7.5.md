# Build report — NetCoreNOC v0.7.5

**"Make the operator's click mean what the operator meant, and make the two guards that protect the
next release actually guard."**

Five workstreams, seven phases, strict waterfall. Four intentional behaviour changes. Zero new
dependencies, migrations, routes, capabilities, audit actions or served paths. `make eval`
byte-identical.

**The one-paragraph version.** The declaration gate had been failing open on route shapes nobody
thought about, and had *silently stopped covering one of them* when an unpinned dependency changed
its internal representation — no commit, no test failure. The documentation-consistency guard was
seeing 31% of what it exists to check, because it stripped the exact syntax the project's own
convention specifies. And the operator's feedback click — the only source of human labels in the
system, and the entire input to v0.8.0 — could be recorded against a situation membership the
operator never looked at. All three are closed. One of the three is closed by changes this repository
**cannot test**, and this report says so in more than one place, because that is the more useful half
of it.

---

## 1. What shipped

| Workstream | Outcome |
|---|---|
| **1. F42** — the gate refuses what it cannot check | `KNOWN_ROUTE_SHAPES` + refusal; narrowed `HEAD` exemption; the class-level guard. 14 tests, 12 proven red |
| **2. The acquisition path** | three changes in `ui/app.js`; 6 structural assertions; a manual protocol |
| **3. The documentation guard** | one regex removed; 31% → 100% visibility; 3 tests, demonstrated green→red→green |
| **4. Security review** | `SECURITY-REVIEW-0.7.5.md`; threat model updated; **a v0.7.4 claim corrected** |
| **5. v0.8.0 specification** | refined in place, every constraint re-traced; **nothing implemented** |

754 tests → **777**. Coverage **at or above** the v0.7.4 figure. ADRs **#98–#101**.

---

## 2. F42 — and why refusal is complete where shape-enumeration was not

`assert_every_route_is_declared` iterated `app.routes`, skipped anything without a `.path`, and
looped over `.methods`. v0.7.4 called that *"complete by construction… nothing here lists the ways a
route can be registered."*

It listed no **mechanism**. It assumed a **shape** — a flat object exposing `.path` and `.methods` —
and there were **two** fail-open branches, not one. Five shapes walked through, every one of them
serving real traffic, all reproduced by execution with a passing control:

| Shape | Escapes via | Served |
|---|---|---|
| `_IncludedRouter` (`include_router`) | `path is None` | 200 |
| `Mount` (sub-application) | empty `methods` | 200 |
| `Mount` (`StaticFiles`) | empty `methods` | 200, file contents returned |
| `APIWebSocketRoute` | empty `methods` | handshake completed |
| `APIRoute`, `HEAD` only | the `HEAD`/`OPTIONS` exemption | 200 |

**The fix.** An explicit allowlist of route classes; anything else **refuses**. The gate need not
know what a `Mount` is in order to refuse it, and that is the point: every object on `app.routes` now
has an outcome — checked, or refused — and there is no third case. That is a property of the control
flow rather than of the dependency, which is what "complete by construction" should have meant.

**Recursing into each container was rejected** (DECISIONS #98). `dir()` on the `_IncludedRouter`
shows the only ways in are `include_context`, `original_router`, `effective_route_contexts` —
undocumented FastAPI internals. A gate walking them would again be correct only for the versions
whose internals it matched, which is the defect being fixed, rebuilt one level down.

### 2.1 The half that generalises: coverage that changed with no commit

| | `app.routes` contains | Verdict |
|---|---|---|
| `fastapi==0.115.0` (the pin floor) | `APIRoute '/api/undeclared-via-router' {'GET'}` | **REFUSED** |
| `fastapi==0.141.1` (resolved today) | `_IncludedRouter None None` | **skipped** |

`pyproject.toml` says `fastapi>=0.115`, there is no lockfile, and CI runs a bare
`pip install -e .[dev]`. The gate's completeness was a property of whatever pip resolved that
morning, and it regressed **with no commit, no test failure and no signal at all**.

`test_f42_the_live_app_produces_exactly_the_known_shapes` asserts the shape set of a real
`create_app` equals the allowlist, so the next such change fails loudly, naming the new class, on the
day of the upgrade. **Its limit is written into its own docstring**: it detects a new *shape*, not a
changed *meaning*. If a future `APIRoute` carried its verbs somewhere other than `.methods`, the
shape set would be unchanged and the gate would quietly check nothing. Nothing here closes that.

Whether to **also** pin FastAPI is left open with the reasoning (DECISIONS #101): a pin freezes a
representation, the test notices when one changes, and only the second produces information — a
pinned project meets the identical silent widening the day it lifts the pin. It is a supply-chain
policy question about five dependencies, not a route-gate question about one.

### 2.2 The correction to v0.7.4's claim

This matters more than the fix. The project's guards are trusted on the strength of claims like that
one, and a wrong claim in a security review outlives a wrong line of code.

`SECURITY-REVIEW-0.7.5.md` §3 states what was claimed, why the argument given did not support it, and
what is claimed now — that every object on `app.routes` is checked or refused, and that the set of
checkable shapes is **enumeration, labelled as such**. `SECURITY-REVIEW-0.7.4.md` is **not edited**;
it is the record of what was believed then. `MODULE-ARCHITECTURE.md` §10.1 carries a dated correction
beneath the original paragraph, which is left as written.

---

## 3. The acquisition path — what it fixes, and what it does not

`renderSituations` called `clear(sits)` first, destroying every card every two seconds — expanded
ones included, with the feedback buttons inside them. The rebuilt detail was `display: block`
immediately and filled only after an **un-awaited** round trip.

The failure that mattered was never the flicker. **A click could land on a card rebuilt between the
operator's visual decision and their mouse-down**, recording `{"verdict": "confirm"}` against a
membership the operator never evaluated. That is a *silently wrong label*: a missing label is visible
as absence and can be counted; a wrong one is indistinguishable from a considered one at every layer
downstream, and **nothing in the system can detect it**.

Three changes, one commit each:

1. **§5.1** — detail nodes of open cards are harvested before the clear and re-appended, keeping
   their identity and listeners. Collapsed cards are still rebuilt. The narrow fix, not a reconciler.
2. **§5.2** — `renderDetail` builds into a `DocumentFragment` and swaps atomically, so no reachable
   state has the container displayed and empty. Covers the first expansion, which §5.1 does not.
3. **§5.3** — a `held while open` badge, reusing the existing `.badge.redacted` styling so `style.css`
   and `index.html` are untouched.

**What v0.7.5 does not do.** The verdict is still recorded against the *situation*, whose membership
keeps moving. An operator who judged four members produces a row indistinguishable from one who
judged nine. **The label is now deliberate; it is still not traceable to what was on screen.** That
is the membership fingerprint and it is v0.8.0. Nobody should read this release as having solved
label provenance.

**And the marker is a human-factors control, not a technical one.** Holding the card trades a wrong
label for a stale one, which is the right trade *only if the operator knows*. There is no
confirmation step and nothing that fails closed if the badge goes unread. An operator under incident
pressure reading a card they believe is live is the realistic failure, and this design informs rather
than prevents it. Recorded as an accepted residual, not described as a fix.

---

## 4. §5.4 — what the test suite could not prove, and what was done instead

**This is the section to read if you read only one.**

Three of the four intentional behaviour changes are browser behaviour. **There is no JavaScript
runtime in this repository** — no node, no npm, no jsdom, no browser automation, in
`pyproject.toml`, the `Makefile`, `flake.nix` or `.github/workflows/`. Every UI assertion in the
project is a source-inspection test. That is a deliberate consequence of "one static UI, no build
step, no npm", and this release did not overturn it.

The draft asked for tests that "drive `applyUpdate` twice and assert the same DOM node is still in
the document". There is no DOM to drive.

| Claim | Proved by the suite? | Actually proved by |
|---|---|---|
| An expanded card's DOM node survives an SSE update | **No** | manual Test A |
| The feedback buttons keep working across updates | **No** | manual Test A / D |
| No reachable state has the container displayed and empty | **No** | manual Test B (throttled) |
| The marker appears while held and goes on collapse | **No** | manual Test C |
| A click lands on the card the operator was reading | **No** | manual Test D |
| The operator *notices* the marker | **No, and no test can** | nothing — residual risk |
| The source has the shape those behaviours require | **Yes** | 6 structural assertions |
| The API contract and SSE behaviour did not change | **Yes** | contract tests, unedited |

**What was done (DECISIONS #99):**

**(a)** Six structural assertions, each carrying **in the test itself** a sentence saying it asserts
the shape of the source and not the behaviour of the browser, and naming the manual test that does
prove the claim. They are real tripwires — five go red against the v0.7.4 source — and they are not
the assertion the draft asked for.

**(b)** `docs/gates/v0.7.5-manual-verification.md`: exact steps against `docker compose up`, 20
minutes, explicit PASS/FAIL criteria, a sign-off block, and a checkpoint that **stops the protocol**
if the SSE stream is not actually live — because every test after that point would otherwise pass for
the wrong reason. **Written by this build and not executed by it**; there was no browser and no
operator. Its own §9 says so, so a maintainer opening it later cannot mistake it for a completed
record.

**(c)** The contract tests unedited — every feedback test, every SSE test — which is the strongest
thing that *can* be proved automatically here, and is therefore listed as evidence rather than as a
formality.

**Why not just add jsdom?** Two reasons, and the second is the one that would not have been obvious.
It is out of scope, and it would be the largest dependency decision this project has made since
v0.2.0, taken inside a patch release to test three lines. **And** this build container happens to
carry `node` and `bun` on `PATH` while CI, the Nix dev shell and a maintainer's machine do not — so a
test written against them would have been green **only on the machine that wrote it**. A test that
passes for environmental reasons is worse than an acknowledged gap, because it looks like coverage.

`docs/ROADMAP.md` records that the planned UI rebuild is where testability should become a **design
input** — decide the runtime and the reconciliation model together, so the tests come from the
architecture instead of being retrofitted. That is the honest place to reopen this, and not before.

---

## 5. The documentation guard: 31% → 100%

`source_of` blanked fenced code blocks **and inline code spans**, while the element-tag pattern
matches `vX.Y.Z: planned` — which `docs/README.md` specifies as a **backticked** form and which every
draft writes that way. The guard was not partially blind by accident; it was **inverted**. The comment
recording the convention sat four lines above the code that defeated it: the guard written to stop
the repository contradicting itself four lines apart contained that contradiction.

| | before | after |
|---|---:|---:|
| Element tags visible | **15 of 48 (31%)** | **49 of 49 outside fences (100%)** |
| Tag-carrying documents entirely invisible | **5 of 8** | 0 |
| Fenced examples correctly excluded | 1 | 1 |

Among the invisible: `FEEDBACK-DATASET-0.8-DRAFT.md`, the v0.8.0 specification, and
`SCORER-PLUGINS-0.13-DRAFT.md`, the half-finished supersession the guard's own docstring names as its
motivating example.

**Demonstrated, not asserted** — injected and unfixed → green; injected and fixed → red at a true
file:line; injection removed → green across the whole tree. Three tests added that drive the **real**
guard function over a synthetic document.

**Verified rather than assumed.** The build prompt said `docs/README.md` was safe because both its
examples are fenced. True — and incomplete: it also carries **three real un-fenced tags** that become
visible. They are harmless, but not for the stated reason — that file makes **no governed release
claim**, so the test that would care skips it. Right conclusion, different reason.

A **test defect, not a security finding**: no `F` number. The temptation to number it is real — it is
a guard failing open — and the difference is that this guard protects the repository's claims about
itself, not the appliance.

---

## 6. Where the build corrected its own brief

Three times. Recorded because a build that silently conforms to a brief it has disproved is worth
less than its evidence.

1. **The `Mount` shapes evade through the empty-`methods` branch, not a missing `.path`.** The brief
   said otherwise; on starlette 1.3.1 the `Mount` carries a path. There are **two** fail-open
   branches, and a fix written to the brief would have closed **one shape out of five**. This changed
   the fix.
2. **The feedback draft's UI line numbers had not moved.** The brief warned that copying them was a
   Phase 0 failure. All seven were re-verified and all seven were unchanged, because v0.7.4 never
   touched `app.js`. Verified rather than copied — and the answer was "the brief's premise is false".
3. **`tests/test_events.py` contains no SSE tests.** The brief cites it as the SSE evidence; it tests
   trap-event parsing. The real SSE tests are `test_f30_sse_*` in `tests/test_governance.py`. Both
   pass unedited, so the requirement is met — but citing the wrong file would have been citing
   something that proves something else.

---

## 7. Decisions #98–#101

| # | Decision | The rejected option that mattered |
|---|---|---|
| **98** | the gate refuses unknown shapes; it does not learn to walk them | recursing into `_IncludedRouter` — it rebuilds the defect against a private attribute. Also records why **exact-type** rather than `isinstance`: `APIRoute` subclasses `Route` |
| **99** | source inspection **plus** a written manual protocol | not only the JS harness — also **source-inspection-only**, because a green tick on a source scan reads exactly like a green tick on the behavioural assertion nobody made |
| **100** | the inline-code strip is dropped, not narrowed | matching the tag with backticks included — it keeps a rule whose stated justification is the reverse of the truth |
| **101** | no FastAPI upper bound; detect the representation change instead | the pin — with the limit recorded, that the guard detects a new *shape* and not a changed *meaning* |

---

## 8. Verification summary

| | |
|---|---|
| `make eval` | **byte-identical**, `c2e8a0ce…` |
| Tests | 754 → **777**; no test removed, **no assertion weakened** |
| Test diff | 14 deleted lines: one import block, and `source_of` itself (which *is* the fix). Both listed in Gate 5 §2 |
| Upgrade from a **real** v0.7.4 database | no migration, identical schema, identical row counts, `integrity_check ok`, **same audit final hash** verified by both versions |
| Packaging | wheel **and** sdist carry the full UI, 7 migrations, d3 `CHECKSUMS.txt`; `release-check` agrees on 0.7.5 |
| Deployment | `docker compose config` **valid**; **the daemon is unavailable, so the image was not built** — the documented substitute was performed instead (§9 of Gate 5) |
| Toolchain | `ruff`, `ruff format`, `mypy --strict`, `bandit`, `pip-audit`, dead-code, link-check, SHA-pin, and every structural guard clean |

---

## 9. Honest caveats

1. **The automated suite does not prove three of this release's four behaviour changes.** §4. The
   manual protocol is the proof and **this build did not run it**.
2. **The staleness marker informs; it does not enforce.** A marker the operator ignores is a marker
   that did not work. Accepted human-factors residual.
3. **Label provenance is not solved.** v0.7.5 delivers a click the operator meant. Which membership
   they meant it about is still unrecorded until v0.8.0.
4. **The shape allowlist is enumeration.** One test keeps it honest, and that test detects a new
   shape, not a changed meaning.
5. **The documentation guard's forbidden-phrase half is still enumeration** and still
   spelling-sensitive (`->` vs `→`). Its element-tag half went to 100%; those are different halves,
   and "100% of element tags" is not "catches contradictions".
6. **`renderEntityDetail` still has the clear-then-fill shape** repaired in `renderDetail`. Found
   while working, deliberately not fixed — a fix smuggled into a small diff is invisible to review.
   On the ROADMAP.
7. **This was not a full re-review of the attack surface.** F1–F41's controls were re-checked only to
   the extent CI asserts them. The last full pass remains v0.7.1's.
8. **The repository was not pushed.** `git push` returns 403 by design — the repository is read-only
   to automation and the archive is the deliverable. Every gate in this build is local and
   reproducible; nothing depends on the push.

---

## 10. What v0.8.0 inherits

A declaration gate that refuses what it cannot classify, and a test that fails loudly on the day a
dependency changes the route representation. A documentation guard that sees the tags it was written
to check, with its failure mode demonstrated. An acquisition path where the click lands on the card
the operator was reading, and where the operator is told the card is held. A v0.8.0 specification
whose every constraint has been re-read against this tree, with two corrections and four column
questions **left explicitly open** for that release's own Phase 0 to answer from measurement.

And — most usefully — a written statement of which of this release's promises the test suite does
**not** keep, so that v0.8.0 is briefed by a repository that does not overstate what it knows.
