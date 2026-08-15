# Build report — v0.12.0, "the instrument and the shape"

## 18 DOM tests execute `ui/app.js`. The number was zero.

Five invariants of a 52 738-byte file that no test had ever run are now under guard, each
demonstrated **red** under an injected defect:

| # | Invariant | Why it survives the rewrite |
|---|---|---|
| 1 | a role never renders a panel requiring a capability it lacks | security |
| 2 | a partial split sends **exactly** the ids the operator marked, and no others | the contract the v0.9.1 → v0.9.2 evidence chain rests on |
| 3 | a server-sent update mid-gesture does not destroy the click target | **the v0.7.5 defect, by name** |
| 4 | no render path writes unescaped data into the document | the reason `esc()` exists (F1) |
| 5 | a capability the client lacks produces **no request**, not a refused one | least privilege at the client |

**Invariant 3 is the one this project has been unable to assert for five releases.**
`FEEDBACK-PATH-0.7.5-DRAFT.md` §5 asked for it in these words — *"drive `applyUpdate` twice and
assert the same DOM node is still in the document"* — and DECISIONS #99 recorded why it could not be
done: *there is no DOM to drive.* There is now.

```
$ make dom
18 passed, 1321 deselected in 3.51s
```

**Executed, not collected.** On a machine without Node that line reads `18 skipped`, and the whole
harness is built so that the difference cannot pass unnoticed.

---

## What the release did not do

**Not one byte of `ui/`.** All four files byte-identical by SHA-256, asserted by a test.

**One line of `src/`, and it is the version string.** `git diff` against v0.11.0 over `src/` and
`eval/` is `__version__ = "0.11.0"` → `"0.12.0"` and nothing else. Behaviour parity across the HTTP
surface — including timing — follows from identity, not from measurement. (An earlier draft said
*"not one byte of `src/`"*; that was true before the Phase 7 bump and false at tag time, and it is
corrected in place rather than defended — `gates/v0.12.0-phase-6.md` §4.)

Zero migrations. Zero new routes, capabilities or audit actions. Zero new runtime dependencies.
**Zero intentional behaviour changes**, and the count is asserted rather than claimed.

---

## The measurement this release exists because of

Phase 0 did not assert that no test executes `app.js`. It demonstrated it, twice, with controls.

**Probe A** — instrument the file so any execution leaves a marker on disk; run the full suite:

```
1302 passed in 214.24s        marker: ABSENT
```

**The control**, because a negative from an uncontrolled probe is worthless — the *same* instrumented
file, handed to Node:

```
marker: PRESENT
```

**Probe C** — a defect no executing test could survive:

```
$ node --check src/netcorenoc/ui/app.js
function ( { { syntax error not valid javascript ===;
^^^^^^^^
$ pytest -q
1302 passed in 211.34s
```

**1302 passed against an `app.js` that no JavaScript engine can parse.**

And the second premise, the same way: a tracked `package.json`, three lockfiles, a `vite.config.js`
and a tracked `node_modules/` — **1302 passed**. The constitution's most structural clause had no
test.

---

## The instrument

`tests/domharness/` — 1 341 lines of stdlib-only JavaScript plus 326 of Python. `node:vm` evaluates
`ui/app.js` against a purpose-built DOM, so all 52 top-level functions become reachable and a test
drives `renderSituations`, `applyUpdate` and `renderDetail` directly.

**No npm. No `package.json`, no `node_modules`, no lockfile, no network, no install step.** Not
jsdom, and ADR #167 is the reason: jsdom and Playwright both need `npm install`, and introducing a
package manifest in order to build the guard against package manifests is incoherent — as well as
turning the harness into one that skips on a machine with no network.

**The fixtures are the real server.** `uifixtures.py` boots the real engine over the real fiber-cut
corpus, logs in as each real role, and captures what the real routes return at the exact URLs
`app.js` requests. Two of the five invariants are statements about the client's contract with the
server; a paraphrased fixture would make them worthless.

### Three mechanisms against the failure that mattered

A green suite over zero executed DOM tests is worse than no harness, because it reads like coverage.

* `run_scenario` **refuses** a result whose proof-of-execution — the output of `app.js`'s own
  `esc()` — is absent or wrong. It raises; it does not skip.
* `availability()` is a pure function of `PATH`, so the skip branch is **driven by a test** rather
  than assumed reachable.
* `make dom` reports executed.

**The skip path was broken, and its own test found it.** `path or os.environ["PATH"]` collapsed *"not
provided"* with *"search nowhere"*, making the unavailable branch unreachable from a test — F51's
class, caught by the very test written to exercise it.

---

## Principle 6 finally has a guard

`tests/test_build_step.py`. The file list comes from **`git ls-files`**, never a directory walk with
a skip-list: v0.10.1's F51 was a skip-list scoped by the literal string `.venv`, and a build-step
guard with one fails the same way in the *mirror* — an artefact under a skipped directory stops being
**found**, and the guard goes quiet without going red.

The vacuity check builds a **real git repository** and adds each of seven artefact classes with a
real `git add`, one at a time — because v0.9.2's ledger entry L2 records a guard test that called its
helper directly and stayed green when the caller was reverted.

`node_modules` is deliberately **not** added to `.gitignore`, with a test asserting it stays out
(ADR #169): the guard is scoped to the tracked set by design, so a dirty `git status` is the only
remaining signal, and ignoring it would remove that *and* blind the guard by construction.

---

## The shape

[`UI-0.13-DRAFT.md`](../architecture/UI-0.13-DRAFT.md) — sidebar navigation, per-role dashboards, the
three parameter classes, the framework recommendation, twelve prohibitions.

Two parts are worth naming here.

**The capability-without-surface enumeration, derived by execution**: 30 capabilities, 43 routes, of
which **8 routes and 4 capabilities are unreachable from any screen**, plus six CLI-only reports.
The one that stands out: `POST /api/password` has no surface, so **a signed-in operator cannot change
their own password** — a missing security affordance in a product that ships password policy. Nobody
had noticed, because nothing had enumerated the surface.

**No Phase 2 or Phase 3 placeholders.** The shape accommodates them; the UI does not announce them.
This project was right to criticise "mechanism with no volume" when v0.9.1's `close` channel shipped
unused, and a greyed-out *Troubleshooting* item is that error with worse ergonomics.

---

## Eight guards demonstrated red — and one of them was wrong

[`v0.12.0-guard-demonstrations.md`](../gates/v0.12.0-guard-demonstrations.md): eight injections,
eight reds, eight controls that passed in both states, **five survivors named individually**.

**Entry 1 is the one to read.** Lowering the Audit tab's required capability so a viewer sees it was
**not caught** by the guard as first written. `admin_only = seen["admin"] - seen["viewer"]` is
unfalsifiable by that defect — the panel simply leaves the difference set. Measured rather than
reasoned: the original form was restored under the injection and passed.

That is Appendix B's *"a test that kills the obvious mutant and misses the real one"*, and as in
v0.10.0's cycle test **the fixture was part of the guard**. The repair derives the expected set from
`rbac.PERMISSIONS`. Two other guards caught the injection either way, so the invariant was never
unguarded — but *"another test would have caught it"* is not a reason to keep a guard that cannot
fail for its own subject.

**Entry 7** is the other one: with the principle-6 extractor returning `[]`, the principle-6 guard
itself stays **green**. It would report a tree containing a bundler, three lockfiles and
`node_modules/` as clean. The vacuity check is the only thing that notices.

**Entry 8** injects this release's own worst outcome. `make dom` printed `18 skipped`, no reason
attached, every other test green. A reader of `1339 passed` would have seen nothing.

---

## F53 — found by the harness, deliberately not fixed

**The panel loaders have no capability check of their own.** A direct `renderPanel("audit")` as a
viewer issues no request — but only because `prunePanels` removed the container and `clear(null)`
throws a `TypeError` before `api(...)` is reached.

Not exploitable today: nothing can call a loader except a tab that was not rendered, and the server
enforces regardless. **But v0.13.0 introduces routing, and routing is exactly what lets a URL call a
loader.**

Rule 9 — *no fix inside a move* — names this case: *"including anything the harness reveals about the
current UI, which is the most likely place this rule is tested."* It was tested immediately, and the
answer was a ROADMAP line, a constraint in the draft, and **a test that asserts both the absent
request and the `TypeError`** — so if a later change made a loader return normally, the test fails
and the note cannot quietly become false.

---

## Verification

| | v0.11.0 | v0.12.0 |
|---|---:|---:|
| tests | 1302 | **1339** |
| **DOM tests executed** | **0** | **18** |
| `mypy --strict` | 172 files, clean | **177 files, clean** |
| coverage | 96.02 % | **96.02 – 96.13 %** (two runs) |
| `eval` hash | `c2e8a0ce…` | **`c2e8a0ce…`** identical |
| migrations | 0001–0013 | **0001–0013** |
| runtime dependencies | 5 | **5** |
| `engine.py` | 569 / 580 | **569 / 580** |
| seal query count | 0 | **0** |
| ADRs / findings | #165 / F52 | **#172 / F53** |

**Two runs of `make coverage` on the same tree gave 96.13 % and 96.02 %.** The 0.11-point spread is
exactly the drift ADR #159 attributes to two underandomised `hypothesis` properties, so the mechanism
is confirmed — but the range **straddles the band's lower edge** (96.10 – 96.21 %), and the honest
statement is *"coverage on this tree is 96.02 – 96.13 %"*, not *"coverage is inside the band"*. An
earlier draft of the Gate 6 document made the second claim from a single sample; the second run
falsified it and the correction is recorded in place. Nothing here is attributable to this release —
the v0.11.0 baseline was also 96.02 %. The band was not widened, the properties were not
derandomised, and the measurement was not replaced by the band.

`F34`–`F52` unedited; all three pre-registration hash guards green; the declaration gate, route order,
handler hashes, `Store` method hashes, one-connection-one-lock, the layer test, the module-size guard
and the documentation guard all green. `DEBT_ALLOWLIST` empty, `COHESION_EXEMPT` one entry at 580.

---

## What this release cannot tell you

Stated here rather than only in the security review, because a build report that leads with a number
should say what the number does not mean.

* **The entire visual layer is uncovered.** No layout, no CSS, no paint. Nothing here detects an
  unstyled column, an invisible panel, or a control off-screen.
* **The graph and the timeline are unexecuted.** d3 is a recording double, so two of the largest
  render paths in the file run against a stub. A defect introduced into either is caught by nothing.
* **The second of v0.7.5's three claims is still not machine-checked.** *"Never displayed and empty"*
  is about paint; the harness has no renderer. The manual protocol remains the only evidence, and
  `tests/test_security_ui.py` now says so in place of the sentence that claimed no runtime existed.
* **The mutation ledger is manual and its author chose the mutations.** §1.1 of the demonstrations is
  the second measured instance in two releases of a *mandated* injection finding what none of the
  chosen ones did. The honest reading is that the survivor list is **certainly** incomplete.
* **On a machine without Node, everything skips and `make qa` is green.** Correct behaviour, and the
  anti-skip argument ultimately rests on a human reading the number. A CI step that fails when zero
  DOM tests execute is one line of workflow and is on the ROADMAP.

---

## The pattern this release kept

**The riskiest thing comes *after* the safety net that protects it.** v0.7.5 fixed the feedback
acquisition path before v0.8.0 built the dataset. v0.9.2 fixed the evidence boundary before v0.10.0
built the judge. Rewriting the UI before it could be tested would have inverted that for the first
time.

The release succeeds by changing nothing and making the next one safe.
