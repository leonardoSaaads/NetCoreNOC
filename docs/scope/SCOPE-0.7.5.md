# SCOPE — NetCoreNOC v0.7.5

**Theme: make the operator's click mean what the operator meant, and make the two guards that
protect the next release actually guard.**

Three things are open at v0.7.4, and all three are **prerequisites for v0.8.0** rather than
improvements to v0.7.4.

1. **The declaration gate fails open on any route shape it cannot classify.** v0.7.4's F40 closed
   the *registration paths*; it did not close the *route shapes*.
   `assert_every_route_is_declared` skips every route object it cannot read a `.path` and a
   `.methods` from, and five shapes qualify. Worse, whether it skips one of them **depends on the
   version of an unpinned dependency**: the `include_router` case was refused on
   `fastapi==0.115.0` and is skipped on `0.141.1`, so the gate regressed in CI with no commit and
   no failing test. Reproduced by execution in
   [`../gates/v0.7.5-phase-0.md`](../gates/v0.7.5-phase-0.md) §2. This is **F42**.
2. **The documentation-consistency guard sees 31% of the element tags it exists to check.** Its
   `source_of()` blanks inline code spans; the project's own convention writes the tags *inside*
   backticks, and the comment saying so sits four lines above the code that defeats it. 15 of 48
   tags are visible; a stray `` `v0.8.0: planned` `` left in the half-finished supersession leaves
   the suite green. §3.2 of the Phase 0 gate is the green-then-red proof.
3. **The operator-feedback acquisition path records labels the operator did not evaluate.**
   Diagnosed and specified in
   [`../architecture/FEEDBACK-PATH-0.7.5-DRAFT.md`](../architecture/FEEDBACK-PATH-0.7.5-DRAFT.md).
   The feedback click is the only source of human labels in the system and **v0.8.0 is the dataset
   built from it**, so an unreliable click is an unreliable dataset — discovered, if at all, long
   after a model has trained on it.

The runtime identity is unchanged: one Python 3.12 asyncio process, one SQLite (WAL) file, one
static UI of four files, environment variables only, no build step, **zero new runtime
dependencies** (five, unchanged), **zero new dev dependencies** (eleven, unchanged), **zero new
migrations** (seven, unchanged), **zero new routes, capabilities, audit actions or served paths**.
`make eval` is byte-identical: the engine, the store, correlation and the scoring seam are not
touched at all.

All prior scope documents and their invariants still hold; `docs/security/threat-model.md` keeps the
authority it has held since v0.2.0. On a conflict, this document wins on *scope*, the build prompt
wins on *process and quality*, the threat model wins on *security posture*,
[`../architecture/MODULE-ARCHITECTURE.md`](../architecture/MODULE-ARCHITECTURE.md) wins on
*placement*, and `FEEDBACK-PATH-0.7.5-DRAFT.md` is the binding specification for workstream 2.

**Delivery model (unchanged).** The repository is read-only to automation: the maintainer takes the
resulting archive and pushes it by hand. No step depends on pushing, on CI running, or on any
external account, registration, or dashboard action. Every gate is local and reproducible
(`make qa`, `make eval`, `docker compose config`, a locally built wheel).

---

## 1. In scope — exactly five workstreams, and nothing else

### 1. F42 — the gate refuses what it cannot check

`assert_every_route_is_declared` iterates `app.routes` and fails open twice: `if path is None:
continue`, and an inner loop over a `methods` set that is empty for every shape that has no verbs.
`continue` is fail-open on the unknown, in a project whose stated posture is fail-closed everywhere.

| Shape | Produced by | Which branch it takes |
|---|---|---|
| `fastapi.routing._IncludedRouter` | `app.include_router(...)` | `path is None` — **only on newer FastAPI**; on 0.115.0 the router's routes are flattened to `APIRoute` and *are* checked |
| `starlette.routing.Mount` (sub-app) | `app.mount(...)` | empty `methods` — a whole subtree served unchecked |
| `starlette.routing.Mount` (`StaticFiles`) | `app.mount(...)` | empty `methods` |
| `fastapi.routing.APIWebSocketRoute` | `app.add_api_websocket_route(...)` | empty `methods` |
| an explicitly-registered `HEAD`-only route | `add_api_route(methods=["HEAD"])` | the exemption written for *synthesised* verbs |

None is used by v0.7.4 today, exactly as neither F40 nor F41 was exploited — and the severity class
is the same: a latent hole in a guard whose entire value is completeness.

**The fix: an allowlist of route shapes, and refusal outside it.** Not a traversal taught to walk
each container — recursing into `_IncludedRouter` means reading `include_context` or
`effective_route_contexts`, undocumented FastAPI internals, which reproduces the defect one level
deeper. DECISIONS #98.

* An explicit tuple of the route classes the gate knows how to check —
  `fastapi.routing.APIRoute` and `starlette.routing.Route`. A test asserts that the shapes a real
  `create_app` produces are **exactly** that set, so the allowlist is a fact about the application
  rather than a guess.
* Any object on `app.routes` outside that tuple raises `UndeclaredRouteError`, naming
  `type(route).__module__` and `type(route).__name__`. **An unrecognised shape is refused, not
  skipped**, so a future FastAPI that invents a sixth one is caught on the day it arrives.
* Within a known shape, every method is checked as today, with one narrowed exemption: skip `HEAD`
  **only when `GET` is present on the same route**, because that is the only case Starlette
  synthesises. A `HEAD` that is a route's sole method was asked for explicitly. `OPTIONS` is never
  synthesised into `route.methods` — confirmed by execution — so the `OPTIONS` exemption is
  removed outright.
* The decorator-time refusal in `DeclaredRoutes` is **kept** (v0.7.4 directive 4, still binding),
  and the assertion stays `create_app`'s last statement before `return app`.

**Plus the guard for the class, not the instance (§4.4).** A test asserts the set of route classes
a real `create_app` produces is exactly the known set, so a FastAPI upgrade that changes the
representation fails the suite loudly on the day of the upgrade, naming the new class, instead of
silently widening the hole. The shape allowlist itself is **enumeration**, and the test's docstring
says so.

This costs the project nothing today: `create_app` produces exactly two shapes and all 48
method/path pairs pass. It costs a future contributor the ability to reach for `include_router`
without noticing — the correct price, because `DeclaredRoutes` is *the* registration path by design.

**The gate gets stricter, never looser.** Every route that registers today still registers; the F40
and F41 test sets, the authorization matrix, the route-map completeness tests and the route-order
parity test all pass **unedited**.

### 2. The operator-feedback acquisition path

`FEEDBACK-PATH-0.7.5-DRAFT.md` §2 is the specification; it is implemented, not re-derived. **Three
changes in `ui/app.js` and nothing else.**

* **§5.1 — reconcile instead of clear-and-rebuild.** While a card is expanded, `renderSituations`
  must not destroy it. The **narrow** version the draft prefers: reuse the existing detail node
  only while `expanded.has(s.id)`, rebuild the header, and keep today's clear-and-rebuild for
  collapsed cards. Collapsing resumes normal rebuilding. The general reconciler is a
  UI-framework-shaped answer to a problem with one node in it, and is out of scope by §2.3 below.
* **§5.2 — build into a fragment and swap atomically.** `renderDetail` builds into a
  `DocumentFragment` and clears-and-appends in one synchronous step after the `await` resolves.
  There is then no reachable state in which the container is `display: block` with no children,
  whatever the network does — including the first expansion, which §5.1 does not cover.
* **§5.3 — the held card says it is stale. Required, not optional.** Holding the card trades a
  wrong label for a stale one, and a stale label is only better if the operator knows. A static
  text marker in the header of any currently-expanded card, in the shape of the existing `redacted`
  badge so it costs no new CSS architecture. No controls, no countdown, no refresh, no live diff.
  Without it this release replaces a visible defect with an invisible one.

**How this is verified, and what the verification does not prove** — DECISIONS #99. There is no
JavaScript runtime in this repository and every existing UI assertion is a source-inspection test
(Phase 0 §5). The properties §5.1–§5.3 establish therefore **cannot be proved automatically here**.
Three things are done instead: structural assertions labelled in their own comments as asserting
the *shape of the source* and not the *behaviour of the browser*; a manual verification protocol in
`docs/gates/v0.7.5-manual-verification.md` written to be
executed by the maintainer; and the existing feedback and SSE contract tests passing **unedited**,
which is the strongest thing that *can* be proved automatically and is listed as evidence rather
than as a formality.

### 3. The documentation guard's element-tag blind spot

`source_of` blanks fenced code blocks **and** inline code spans. `_ELEMENT_TAG` matches
`vX.Y.Z: planned`, and the comment four lines above it records that the project's convention writes
that tag **inside backticks**. The guard is not partially blind by accident; it is **inverted**.

**The fix is to drop the inline-code strip**, keeping the fenced-block strip — one regex removed.
Plus the test that was missing: **inject the forgotten tag and observe red.** A guard whose failure
mode has never been demonstrated is not a guard. `source_of`'s docstring, which currently gives the
wrong reason for the strip, is corrected, and `docs/README.md` gains a sentence making the
convention unambiguous so the two rules cannot drift apart again.

This is a **test defect, not a security finding** — no `F` number. DECISIONS #100.

### 4. Security review

`docs/security/SECURITY-REVIEW-0.7.5.md`, continuing from
**F42**, in the established format. It must cover: F42 with its reproduction and fix; **the
correction to v0.7.4's completeness claim**; the **dependency-representation class** of defect and
whether to also pin; that the gate is stricter and no looser; the acquisition path and its
**residual** (the fix narrows the window but does not make the label traceable to what was on
screen — that is the membership fingerprint, and it is v0.8.0); the staleness trade as a
human-factors residual risk; and two critical-analysis notes at minimum, one of them being that the
automated suite does not prove this release's behavioural claims. `threat-model.md` gains F42
mapped to a control and a check.

### 5. Refinement of the v0.8.0 specification — no implementation

`FEEDBACK-DATASET-0.8-DRAFT.md` is refined in place, every element still tagged `v0.8.0: planned`.
It records that v0.7.5 hands v0.8.0 **a click the operator meant** and that this is a precondition
for the dataset, not a substitute for the membership fingerprint; re-verifies each of the four
schema constraints against the **v0.7.5** tree with file and line, correcting any that moved;
reconfirms the scope-fingerprint requirement; and states which dataset columns belong to the
`feedback` write path versus a new table **as a question the v0.8.0 build must answer in its own
Phase 0** — because ambiguity about a v0.8.0 design decision resolves to "the v0.8.0 build
decides", and saying so is the deliverable.

---

## 2. The four intentional behaviour changes, stated once

**This release ships exactly four. Any fifth is a defect in the work, not a feature.**

1. **A route of a shape the gate cannot classify now refuses.** Startup-time, not request-path. An
   application registering an `include_router`, a `Mount`, a websocket route or a `HEAD`-only route
   fails `create_app` instead of serving it unchecked. No such route exists in v0.7.5, so no served
   path changes.
2. **An expanded situation card is no longer destroyed by an SSE update.** Its detail node — and
   the feedback buttons inside it — survive the two-second rebuild while the card is open.
3. **The detail container is never displayed empty.** `renderDetail` swaps content in atomically
   after its round trip resolves.
4. **A held card carries a staleness marker** in its header for as long as it is expanded.

Nothing else moves. `make eval` is byte-identical, and every other test that passed at v0.7.4
passes here.

---

## 3. Out of scope — deferred, each with the reason

1. **The v0.8.0 feedback dataset** — schema, capture, migration, bias report. Workstream 5 refines
   the specification; it builds nothing.
2. **The membership fingerprint** on the feedback record. It is the v0.8.0 primitive, and
   `FEEDBACK-PATH-0.7.5-DRAFT.md` §3.4 records why rejection-on-mismatch is the wrong mechanism.
   No version field, no precondition and no 409 is added to the feedback endpoint.
3. **Any UI remodelling**, `SSE_UPDATE_S` changes, a pause control, a live diff, a refresh button, a
   countdown, or a general DOM reconciler. `FEEDBACK-PATH-0.7.5-DRAFT.md` §3 enumerates these and
   the reason each is excluded. The list is **closed**: ambiguity about whether a UI change is in
   scope resolves to *no*.
4. **A JavaScript test runtime.** No node, npm, jsdom, playwright, headless browser, or embedded JS
   engine as a dev dependency. DECISIONS #99 explains what is done instead, and why the answer is
   *not* "add the tool that would make this provable".
5. **Making `ROUTE_SCOPE` enforcing**, authenticating `/openapi.json`, and the AST-based caller
   count for `add_api_route` — the three v0.7.4 ROADMAP lines. Still ROADMAP lines.
6. **Pinning FastAPI to an upper bound.** §4.4's guard *notices* a representation change, which is
   strictly better than a pin that freezes one. Whether to **also** pin is recorded as a ROADMAP
   line with the reasoning (DECISIONS #101), not a decision this release makes.
7. **Fixes inside the changed code.** If repairing the acquisition path reveals another UI bug it
   is a `docs/ROADMAP.md` line and a note in the security review — not a fix in this release. A fix
   smuggled into a three-line diff is invisible to review, which is the whole reason the diff is
   three lines.
8. SNMPv3, `/metrics`, pcap replay, outbound webhook / `Case` JSON emission — still out.

---

## 4. What v0.7.5 leaves behind

* A declaration gate that **refuses what it cannot classify** rather than skipping it, and a test
  that fails loudly on the day a dependency upgrade changes the route representation — naming the
  new class — instead of silently widening the hole.
* A corrected completeness claim. v0.7.4 said the assertion was "complete by construction"; the
  argument offered for it did not support it, and the project's guards are trusted on the strength
  of these claims. `SECURITY-REVIEW-0.7.4.md` is **not edited** — it is the record of what was
  believed then.
* A documentation guard that sees the tags it was written to check, with its failure mode
  **demonstrated** rather than asserted.
* An acquisition path where the click lands on the card the operator was looking at, and where the
  operator is **told** the card is held.
* An explicit, written statement of which of this release's promises the test suite does **not**
  keep, and the manual protocol that is their actual proof — so that v0.8.0 is briefed by a
  repository that does not overstate what it knows.
