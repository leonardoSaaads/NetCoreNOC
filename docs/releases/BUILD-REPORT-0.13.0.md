# Build report — v0.13.0, "the UI"

## What an operator can now do that they could not before

Nine things. None of them is "the console uses a framework".

1. **Answer "why did the system group these alarms?" without leaving the screen.** Every expanded
   situation shows one row per link with the three named terms and **each term's number beside its
   bar** — temporal, class affinity, entity affinity — and the threshold the sum had to clear. It
   was reachable in v0.12.0 as three coloured bars with the numbers in a tooltip.
2. **Send someone a link to one situation.** `#/situations/12`. The old console had no addresses at
   all, so during an incident the only way to point at a grouping was to describe it.
3. **Change their own password while signed in.** There was no way to do this. The login overlay
   handled the forced first-sign-in change; after that the capability existed and nothing offered
   it.
4. **See what the promotion gate decided and why it refused** — the verdict, the triggers, the
   refusal reason, and the sealed holdout's query count. A refusal is an explanation, and it had no
   screen.
5. **See what the feedback corpus costs, in rows, and change its retention with a real count of
   what would be deleted** — from the console rather than from a shell on the appliance.
6. **Change a user's role** without deleting and recreating the account, and be told beforehand
   that it revokes their sessions.
7. **Prune the audit log**, and read on the same screen that verification runs offline and that
   there is no repair for a broken chain.
8. **Browse the alarm classes the appliance has learned** — the clearest demonstration the product
   has of its own claim, since that table was empty at install and nobody loaded a MIB.
9. **Use the console from the keyboard, in the theme and density they chose.** One tab stop into
   navigation, arrow keys within it, focus moved into the work area when they navigate.

And one thing an operator can now *understand* that they could not: **why a setting has the value it
has.** Every live setting shows three columns — environment default, database override, effective.

---

## The numbers

| | v0.12.0 | v0.13.0 |
|---|---|---|
| tests | 1339 | **1428** |
| DOM tests **executed** | 18 | **24** |
| `ui/app.js` | 52 738 bytes, one file, 55 top-level functions | **an entry point plus 34 ESM modules**, largest 388 lines |
| screens | 10 tabs | **17 views in three groups** |
| declared routes with no screen | 8 | **0** |
| `mypy --strict` | clean, 177 files | **clean, 180 files** — the three added files are the evidence commands under `tools/evidence/`, which `[tool.mypy].files` covers |
| `make eval` | `c2e8a0ce…8b9b6f26` | **byte-identical** |
| migrations | `0001`–`0013` | **unchanged** |
| runtime dependencies | 5 | **5** |
| vendored bytes | 279 706 (d3) | **292 606** (d3 + 12 900 of framework) |
| `/api` route/method pairs | 44 | **44, order byte-identical to the v0.7.1 baseline** |
| seal query count | 0 | **0** |

---

## What was hard, and what it cost

### The harness had to learn to link a module graph

v0.12.0's harness evaluated one classic script with `vm.runInContext`, which cannot see an
`import`. v0.13.0's UI is a module graph, so the harness now links it with `vm.SourceTextModule`
and a resolver that reads the real files. Two consequences worth stating:

* **the vendored Preact and htm bytes that `CHECKSUMS.txt` pins are the bytes the tests execute.**
  Not a stub, not a shim, not a second copy.
* the proof of execution moved and got stronger: it is `esc()` called on the **evaluated namespace
  of `app/dom.js`**, which cannot be produced without linking and evaluating the whole graph.

The DOM double needed five additions, and `dom.mjs` names each with the reason its absence produced
a *wrong* result rather than an obvious one. **The dangerous one was the `on<type>` properties**:
Preact decides a listener's type by asking `"onclick" in node`, and without the property it would
have registered `"Click"` — a UI that renders perfectly, responds to nothing, and passes every
assertion about rendered markup. `addEventListener` now refuses a capitalised type, so that failure
cannot come back quietly.

### Three defects were found by driving the UI, not by reading it

This is the argument for Part I.1's working loop, and it is worth being concrete: **in all three
cases the markup was correct and every structural assertion was green.**

1. **The sidebar's arrow keys were inert.** `render` recomputed the roving `tabindex` from the
   active route on every pass and discarded what the keyboard had set. The keyboard trace reported
   `at: 0` after every keypress.
2. **The Overview sat on a spinner forever** when `/api/stats` failed — on screen, a console that
   cannot reach its own API and a quiet network looked identical.
3. **Governance offered `rbac.write` controls to a principal gated only on `rbac.read`** (F55).

### And two probes were measuring themselves

Recorded because Appendix B says they are the trap this repository keeps falling into, and because
both would have produced a *wrong document* rather than a wrong build.

* Gate 0's first reachability drive reported **14 unreachable routes**. Four were unreachable
  because the corpus had no token to revoke and no second scorer configuration to roll back to; two
  more because `uifixtures` never captured `/api/entities/{ne_id}`, so the reset buttons were never
  rendered. With both controls added: **eight**, which independently reproduces the design draft's
  figure. **Without them this gate would have overstated the missing-surface list by 75 %.**
* Gate 6's first empty-state sweep reported the Corpus screen as having no empty state. The "empty"
  fixture had reused admin's populated payload. The screen was already right.

### The mutation ledger earned its keep

Nineteen mutants, seventeen killed, one probe failure — **and one genuine survivor**: nothing
asserted that the precedence table's "environment default" column reported the *environment*. It
could have shown the database override in both columns, reading as *"the environment was already
set to this"*, with the entire suite green.

Closing it needed a named test, and running the mutant again found the same defect in the adjacent
field. Then the new test found a **third** thing: with no `RuntimeConfig`, the route answered
`retention_days: 0.0` while reporting an environment default of `7.0` — an effective value
contradicting the environment column printed beside it, which is precisely the confusion the
three-column table exists to remove. Fixed in `routes_admin.py`.

**Final ledger: 21 mutants, 21 killed, 0 survivors.** The value was never the number.

### Then the finished tree was opened in a browser, and six more defects were on screen

Every figure above comes from the harness, and the harness has no layout, no cascade and no paint.
So the finished console was driven in a real Chromium against a real appliance — empty SQLite, real
SNMPv2c traps over UDP, four correlated situations, both judgement paths — and **six defects were
found by looking, with all 1428 tests green.**

Five were the same defect five times. `htm` collapses a newline **adjacent to a tag boundary to
nothing**, not to a space, so

```js
html`<p class="root">Probable root:
  <b>${alarmName(root)}</b>`
```

painted `Probable root:1.3.6.1.4.1.1271.2.1.1`. The sixth was worse in kind: `NETCORENOC_ALLOWLIST`
is unset, unset means *accept every source*, and the settings precedence table rendered that as a
**blank cell** — in the one table whose entire purpose is to explain why a setting has the value it
has. It now reads `(empty — every source accepted)`.

The harness dumps an identical, correct tree in all six cases. It cannot see whitespace and it
cannot see emptiness, because both are properties of what is **painted** and `textContent` is what
was **written**. That gap does not close by adding assertions; closing it needs a layout engine.
Hence ADR #182: one browser pass per UI release, recorded with what it did **not** verify, and
deliberately **not** added to `make qa`, CI or `pyproject.toml`. The drive's own probe carried
three defects of exactly the shape Appendix B names — a literal that CSS uppercases, a selector
matching zero cells, a control addressed by text it never had — and all three are recorded beside
the six.

Full record: [`docs/gates/v0.13.0-live-verification.md`](../gates/v0.13.0-live-verification.md).

---

## Honest notes

Four are required. There are five.

### 1. What the harness still cannot see — and the answer includes the whole visual layer

The harness has **no layout, no CSS cascade, no `getComputedStyle`, no paint, and no accessibility
tree.** `style.css` is 560 lines and no test executes any of it beyond substring checks on a handful
of tokens. Every judgement in this release about spacing, contrast, visual hierarchy, or whether two
severity colours are distinguishable is **unmeasured**. I chose them by reading hex values.

The browser pass above changes what *I have seen* and changes nothing about what is *tested*. Both
themes were rendered and read by eye at one viewport in one browser; **no contrast ratio was
computed against WCAG AA**, no second browser was driven, no narrow viewport was exercised, and
nothing was heard by a screen reader. The pass is not in the suite and will not protect the next
release. Six defects it found are the measure of how much this note was understating the gap.

**And the graph is unexecuted in every sense.** d3 is a recording double, so `app/views/graph.js` —
172 lines of force simulation, drag, zoom and edge rendering — is executed by no assertion at all.
The double throws on an unknown d3 API, which is a drift alarm, not coverage. Calling it anything
else would be the one dishonesty this project has never committed.

### 2. The screen I am least confident an operator can use

**Settings**, and not because of the parameters — because of its length. It stacks the class legend,
the live configuration with its precedence table, the corpus retention tiers with their destructive
preview, the hardening-only floors, the structural facts, and the restart-required table. On a
1080p screen that is a lot of scrolling to reach the thing you came for, and I have no way to check
whether the three parameter classes actually *read* as three classes or just as three border
colours nobody notices.

Second place: **Judge & promotion**, which is dense with vocabulary — verdict, trigger, plan hash,
seal query count — that is precise and is not self-explanatory to someone who has not read
`PREREGISTRATION-0.10.0.md`.

### 3. Whether the accessibility floor was met or approximated

**Met for the markup; approximated for everything else, and the gap is not small.**

Really met: one tab stop into navigation with roving `tabindex` (measured), focus moved to the
work-area heading on route change (measured), landmarks with accessible names, `aria-label` on every
icon-only control stating its current value, labels on every form control, severity encoded three
ways, `prefers-reduced-motion` honoured.

Not met, and named rather than glossed: **the graph is not keyboard-operable and has no
screen-reader semantics.** It has a label and a pointer to the Entities screen, which carries the
same facts as text — that is a mitigation, not a fix. **No screen reader was run at all**, so every
claim above is about markup and none is about what is announced. **Contrast ratios are not
measured.** ADR #180 lists all four.

### 4. The guard I am least confident in

`test_mutating_controls_are_behind_capability_guards`. It is the newest, it found the most (F55),
and it is the one whose *extraction* I trust least: it resolves `post()`/`del()` call sites by
regex, and although it raises on a path it cannot resolve — which is how the composed
`` `/api/${kind}` `` was caught — a write issued through a helper it does not recognise would
simply not be found. It asserts `len(writes) >= 6` as a vacuity check, and six is a number I chose,
not a number I derived.

Runner-up, for a different reason: `test_security_headers_on_every_route_class` **cannot** catch a
change to the CSP constant, because it compares against that constant. Demonstration §12 shows it
passing while its sibling fails. It is kept, and the demonstrations document says plainly what its
green does not cover.

### 5. The thing I would undo if I could

**Seventeen screens is more than one release should ship.** Every one is backed by a real route and
none is a placeholder, so nothing here violates the anti-overengineering rules — but breadth is
itself a risk, and the screens I drove least (Quarantine, Alarm classes, Timeline) are the ones most
likely to be subtly wrong in a way no assertion covers. A narrower release with the same rigour
would have been a better release; the eight missing surfaces were the specification, and I took
them all in one go.

---

## Where the next release should start

`docs/architecture/CARTRIDGE-0.14-DRAFT.md`, specified here and implemented nowhere. Before that,
two things this release leaves on the floor:

* the **ROADMAP lines** it created: a true reconciler for the situation card (ADR #173), a write
  path for the pre-registered sufficiency floors (ADR #178), and HTTP routes for the four CLI
  reports so the console can display verdicts it currently only names.
* **F54's residual**: no operator-supplied text in an accessible name is asserted only where the
  escaping scenario walks. A future screen can reintroduce it elsewhere.
