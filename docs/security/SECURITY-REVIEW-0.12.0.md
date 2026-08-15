# Security review — v0.12.0 (the instrument and the shape)

**This release adds no route, no capability, no audit action, no migration and no runtime
dependency, and the only line of `src/` it changes is the `__version__` string.** The whole diff is
`tests/`, `docs/`, ten lines of `Makefile`, six of `pyproject.toml`, and that one version line —
which `tools/release_check.py` reads and no execution path does.

That makes the security question unusual, and narrower than usual: **not "is the new code safe" but
"did the new instrument reach anywhere it should not, and does the guard it replaces still hold".**
§1 answers the four assessments Phase 7 requires. §2 records the one finding. §3 is the critical
analysis. §4 carries the open questions forward.

---

## 1. The four assessments

### 1.1 The harness cannot reach production code paths

**It cannot, and the reason is structural rather than conventional.**

The harness is `tests/domharness/*.mjs` plus `tests/domdriver.py`. Nothing under `src/netcorenoc/`
imports, references or names either — asserted by
`test_the_harness_is_a_test_tool_and_never_a_runtime_dependency`, which walks every module under
`src/` and greps for both names. There is no import to break; the appliance has no idea the harness
exists.

In the other direction the harness reads exactly three things from the tree — `ui/index.html`,
`ui/app.js` and `ui/vendor/` — and writes nothing.
`test_the_harness_never_writes_into_the_repository` captures `git status --porcelain` before and
after a scenario and asserts equality, so a harness that started leaving artefacts would fail rather
than quietly put files in front of the principle-6 guard.

**What executes `app.js` is a `node:vm` context with an explicit global table.** There is no
`require`, no `process`, no `fs` in it — the sandbox is built from a literal object in `env.mjs`, so
reachability is a property of that object rather than of an exclusion list. `app.js` therefore cannot
touch the filesystem from inside the harness even if it tried.

One honest qualification: **`node:vm` is not a security boundary and is not being used as one.** V8
contexts share a heap and are escapable; Node's own documentation says so. That is acceptable here
because the code being evaluated is *this repository's own `ui/app.js`*, read from the tree, at test
time — not untrusted input. If a future release ever evaluates a customer-supplied asset in this
harness, the boundary argument changes completely and this paragraph is where to start.

### 1.2 No test dependency entered `dependencies`

```toml
dependencies = [
    "pysnmp>=7.1", "aiosqlite>=0.20", "fastapi>=0.115", "uvicorn>=0.30", "pydantic>=2.7",
]
```

**Five, unchanged since v0.2.0.** Asserted by counting the quote characters in that block, so an
addition fails even if it is formatted unusually.

The stronger fact: **the harness adds nothing to the `dev` extra either.** It is stdlib-only Node
plus stdlib-only Python. `test_the_harness_tree_holds_no_installed_dependency` asserts every `import`
in every `.mjs` file resolves to `node:*` or to a sibling file — there is no third-party JavaScript
anywhere in it, and no `package.json` to hold one.

That was the deciding factor in ADR #167 and it is worth restating as a security property rather
than an aesthetic one: **jsdom or Playwright would have added a transitive npm dependency tree,
fetched over the network at test time, to a project whose supply-chain posture is one vendored file
with a pinned SHA-256.** The harness's answer to "what is its supply chain" is *nothing*.

Packaging: `package-data` names `migrations/*.sql`, `ui/*`, `ui/.well-known/*` and `py.typed`. No
`.mjs`, no `domharness`. Nothing from this release ships in a wheel.

### 1.3 The CSP and the security headers are unchanged

```python
assert "style-src 'self'" in CSP and "script-src 'self'" in CSP
assert "'unsafe-inline'" not in CSP and "default-src 'none'" in CSP
```

`src/netcorenoc/api/perimeter.py` is byte-identical to v0.11.0 — as is every module under `src/`
except `__init__.py`'s version string — so the CSP,
`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` and the cookie flags are the ones
reviewed in earlier releases and re-asserted, unchanged, by `test_csp_is_unchanged_and_forbids_inline`
and `test_security_headers_on_every_route_class`.

Worth stating because it is the obvious place a DOM harness could have caused damage: **the harness
does not run under the CSP and does not model it.** It evaluates `app.js` directly rather than
loading a page. So a violation of `script-src 'self'` — an inline handler, a CDN reference — is
**not** something these tests would catch; `test_ui_source_has_no_f1_antipatterns` and
`test_the_ui_is_still_loaded_directly_by_the_browser` are, and they are source-level. The two layers
do not overlap, and neither is a substitute for the other.

### 1.4 The client-side capability guard now mirrors the server rather than paraphrasing it

**This is the assessment with the most changed.**

Before: `app_js.split("const TABS", 1)[1]` plus a regex, compared against `rbac.PERMISSIONS`. It
mirrored the server in the sense that both names appeared in one file — but the *client* half was a
text fragment, and Phase 0 §1.2 established that a shape change makes the regex match nothing while
`test_admin_panels_are_gated_to_admin` stays green against a map it failed to read.

After: the mapping is **discovered by execution**. Each role boots with the capability set the real
server returned in `/api/me`; each rendered tab is clicked; each resulting request is resolved
against `rbac.ROUTE_PERMISSIONS`; the required capability is compared against what the server said
the principal holds. **The client's claim and the server's table are compared as data, on the same
run**, and there is no text fragment in between.

Three properties follow that the old form did not have:

* it cannot pass by matching nothing — a panel issuing no request is visible, and the admin control
  asserts the same gestures *do* produce admin-capability requests;
* it survives a rewrite — nothing in it depends on how the map is spelled;
* it caught the injection at §1 of the guard demonstrations **twice over**, on two independent tests.

And one it did *not* have until the injections were run — see §3.1, which is not a small caveat.

---

## 2. F53 — the client's least-privilege property holds by accident

**Severity: none today. Latent, with a named trigger.**

### 2.1 What was found

The harness drove `renderPanel(id)` directly — bypassing the tab gesture, which is what a deep link
or a client-side router does — as a viewer, for all seven admin panels. **No request was issued.**

The mechanism is not a capability check:

```
outcomes: {users: "threw: TypeError", tokens: "threw: TypeError", …}
requestsAfterBypass: []
```

`prunePanels` has removed each panel's container from the DOM, so the loader's first statement —
`clear($("usersView"))` — dereferences `null` and throws before `api(...)` is reached. **There is no
capability check inside any loader.** `loadUsers`, `loadAudit`, `loadQuarantine` and the rest call
`api()` unconditionally.

### 2.2 Why it is not exploitable today

Nothing can call a loader except `renderPanel`, which is called only by `selectPanel`, which is
called only by a tab button that `buildTabs` did not create for a principal lacking the capability.
There is no router, no hash handler, no `postMessage` listener, no deep link. The path does not
exist.

And the server enforces regardless: every one of those routes is admin-only in `ROUTE_PERMISSIONS`
and the perimeter is deny-by-default. The worst outcome, were the path to exist, is a viewer's
browser collecting 403s — an information-shape leak into an access log, not an authorization bypass.

### 2.3 Why it is a finding anyway

**Because v0.13.0 introduces routing, and routing is exactly the thing that makes a URL able to call
a loader.** The property that holds today holds for a reason nobody chose, in a file about to be
rewritten by someone who will reasonably assume the least-privilege behaviour they measured is
designed.

This is the same class as F34, which existed because a route's scope posture *was expressed nowhere
at all* — not wrong, absent, and therefore invisible to every table, test and reviewer.

### 2.4 What was done, and what deliberately was not

**Not fixed.** Anti-overengineering rule 9 — *no fix inside a move* — names this case explicitly:
*"including anything the harness reveals about the current UI, which is the most likely place this
rule is tested."* Adding a capability check to nine loaders would change `ui/app.js`, and a
characterisation test written against a UI the test itself modified characterises nothing.

Recorded three ways instead, one of them load-bearing:

1. `docs/ROADMAP.md`, under *Found while building v0.12.0*;
2. `UI-0.13-DRAFT.md` §3 and §11 constraint 10 — *no route may be fetched before its capability is
   resolved*;
3. **the test asserts both halves.** `test_a_panel_reached_without_its_capability_still_issues_no_request`
   asserts that no request is issued *and* that every loader threw a `TypeError`. If a later change
   made a loader return normally — including a well-meant partial fix — the test fails and the
   docstring cannot quietly become false.

Item 3 is the difference between a note and a guard. The note would have rotted.

---

## 3. Critical analysis — four honest notes

### 3.1 Whether the five captured invariants are the right five, and which sixth I would add

Four of the five earn their place on evidence rather than judgement: each corresponds to a defect
this project has actually had (F1, the v0.7.5 click gesture, the v0.9.1→v0.9.2 evidence chain) or to
a boundary the server also enforces. Invariant 5 is the weakest of the five on that test — least
privilege at the client has never failed here — but it is the one that guards against a *regression
in reasoning* ("the server will catch it"), and the §5 injection shows a one-line change makes it
real.

**The sixth I would add: that the login overlay cannot be bypassed, and that an unauthenticated boot
renders nothing.** It is absent, and its absence is not a considered omission — I did not think of
it until writing this section. Every scenario in the harness boots a *successful* `/api/me`. Nothing
executes the path where `/api/me` returns 401 and `showLogin()` runs, which means nothing asserts
that the app container stays hidden, that no panel is rendered, and — the part that would actually
matter — that a failed resume issues no further requests. That is a security invariant of the same
class as the other five, it survives a rewrite, and the harness could assert it in about fifteen
lines. It is a ROADMAP line rather than a late addition here, because adding a sixth invariant after
the guard demonstrations were run would give it no red beside its green.

A seventh candidate I considered and **reject**: "the session cookie is never read by JavaScript".
It is `HttpOnly`, so it is a server-side property already asserted server-side, and a client test
would be asserting the absence of a thing rather than a behaviour.

### 3.2 Whether the harness could pass while executing nothing

**It could not pass silently. It could pass loudly, and the difference is the whole design.**

Three independent mechanisms, and they fail for different reasons:

* `run_scenario` refuses a result whose `proof.escaped` is not the exact string `app.js`'s own
  `esc()` produces. A harness that stubbed everything cannot fabricate it. **This raises, it does not
  skip.**
* `availability()` is a pure function of `PATH`, driven into its unavailable branch by a test that
  asserts the reason *names the requirement*. Injection 8 shows the failure it catches.
* `make dom` reports executed, never collected.

**But here is the honest residual, and it is real**: on a machine with no Node, all eighteen DOM
tests skip, `make qa` reports green, and **nothing fails**. That is correct behaviour — the
alternative is a suite that cannot run without Node, which ADR #166 argues against — but it means the
anti-skip argument ultimately rests on *a human reading the number*. Injection 8 measured exactly
what that looks like: `18 skipped, 1321 deselected`, with no reason attached, inside an otherwise
green suite.

The gap is closable and was not closed: a CI step that fails when the count of *executed* DOM tests
is zero. It is one line of workflow and it is on the ROADMAP. I would not describe the current state
as safe from a maintainer who does not read the output.

### 3.3 What the characterisation does not cover

**The entire visual layer, and saying so is the point.**

* **No layout, at all.** `getBoundingClientRect()` returns zeroes. No CSS is parsed, no cascade
  computed, no paint occurs. Nothing in this release can detect that the UI renders as an unstyled
  column, that a panel is invisible, that text is white on white, or that a control is off-screen.
* **The graph and the timeline are unexecuted.** d3 is a recording double, so `updateGraph` and
  `loadTimeline` — two of the largest render paths in a 52 KB file — run against a stub. A defect
  introduced into either is caught by **nothing**, today.
* **The second of v0.7.5's three claims is still not machine-checked.** "The detail container is
  never displayed and empty" is about paint. The harness has no renderer. Test B of the manual
  protocol remains the only evidence, and `tests/test_security_ui.py`'s corrected comment block now
  says so in place.
* **Accessibility is untouched**, here and in the product: keyboard navigation, focus management,
  screen-reader semantics for the graph. `UI-0.13-DRAFT.md` §12.6 names it and is explicit that
  naming is not specifying.
* **The harness DOM is not a browser.** Seventeen semantics are conformance-tested because an
  invariant depends on each. Every other divergence is unguarded and unmeasured — two known ones are
  listed as survivors S3.

A blunt way to put the total: **this release makes it possible to notice a class of logic defect in
the UI, and does nothing whatever about whether the UI looks right or works for a human.** The
v0.7.5 defect would now be caught. A v0.7.5-shaped defect in the graph would not.

### 3.4 The guard I am least confident in

**`test_a_role_never_renders_a_panel_whose_capability_it_lacks` — and specifically my confidence in
having repaired it correctly.**

It is the guard that failed its own injection (guard demonstrations §1.1). The set-difference form
was unfalsifiable by the defect it exists to catch, and I did not notice while writing it, while
writing its docstring, or while writing the gate document that described it as *"the replacement for
the `split(\"const TABS\")` guard"*. It was found because the build prompt **mandated** that
particular injection.

The repair derives the expected admin-panel set from `rbac.PERMISSIONS` via the routes each panel
requests. I believe it is correct. What I cannot claim is that it is *sufficient*, for a specific
reason: **the repair and the test still share a run.** `_admin_ceiling_panels()` executes the same
harness, boots the same admin fixture, and reads the same `perTab` structure the assertion later
compares against. The authority for *which capability* is `rbac`, which the injection cannot touch —
that is the part that works. But the authority for *which panel issued which request* is still the
harness observing itself, and I have not constructed the injection that would separate those.

Appendix B's rule applies and I am applying it to my own repair: **before trusting an invariant, ask
what input would make it false.** For this one I can answer that for the capability half and not for
the panel-attribution half.

The second thing I would flag, more briefly: **the ledger is manual and I chose the mutations.**
§1.1 is now the second measured instance in two releases of a mandated injection finding something
none of the chosen ones did — the first was F48's re-run in v0.11.0. Two instances is a pattern, and
the honest reading is that the survivor list in §9 of the demonstrations is **certainly incomplete**,
not merely possibly so.

---

## 4. Findings ledger

| Finding | State |
|---|---|
| F1 (stored XSS) | closed; now **also** asserted behaviourally — invariant 4, demonstrated red |
| F2 (`localStorage` token) | closed; the guard is untouched and ADR #172 keeps it exception-free |
| F12 (wheel dropped UI files) | closed; `package-data` unchanged, re-asserted |
| F34–F52 | unedited and green; `git diff` over their test files against v0.11.0 is empty |
| **F53** (this release) | **open, latent** — §2. Not exploitable today; trigger is v0.13.0's routing |

## 5. Open questions carried to v0.13.0

1. **F53's repair**, which is v0.13.0's to make: every view resolves its capability before it
   fetches.
2. **The sixth invariant** — the unauthenticated boot path (§3.1). ROADMAP line.
3. **A CI step that fails when zero DOM tests execute** (§3.2). One line of workflow; the last gap in
   the anti-skip argument.
4. **The graph and the timeline are covered by nothing** (§3.3, survivor S2), and `UI-0.13-DRAFT.md`
   §12.2 leaves open whether d3 stays at all. Whichever way that goes, it is 279 706 bytes serving
   one screen with no behavioural test.
5. **No shape assertion on the captured fixtures** (survivor S4): a route that dropped a field would
   render `undefined` and every invariant would still pass.
6. **Carried unchanged from `SECURITY-REVIEW-0.11.0.md` §4**: the fourth verdict state for a
   non-computable quantity (1); the `MAX_ABS_COEFFICIENT` reading (2); snapshotting the merge edges
   (3); retention not knowing what a citation is (4); the v0.9.2 reconciliation-drift audit gap (5).
   **Item 6 of that list — a UI for promotion, gated on a test that executes `ui/app.js` in a real
   DOM — is closed by this release**: the prerequisite exists, and the screen is
   `UI-0.13-DRAFT.md` §5.3.
