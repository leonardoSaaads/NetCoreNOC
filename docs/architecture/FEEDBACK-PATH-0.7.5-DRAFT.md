# The operator-feedback acquisition path — v0.7.5 draft (specification only, not implemented in v0.7.4)

**Implement none of this in v0.7.4.** Every element below is tagged **`v0.7.5: planned`**. v0.7.4 is
a structural release whose whole value is a parity story — two package splits and one extraction,
proved by function hashes — and a three-line change to `ui/app.js` is a *runtime behaviour change on
the operator's path*. Landing it inside a move release would mean that if something broke, nobody
could tell the move from the behaviour change. It is three lines, it is correct, and it belongs
where a reviewer can see it.

**Why this release exists at all.** The feedback click is the only source of human labels in
NetCoreNOC. **v0.8.0 is the operator-feedback dataset**
([`ROADMAP-0.8-TO-0.13.md`](ROADMAP-0.8-TO-0.13.md), DECISIONS #93), and every ML step after it
consumes those labels. An unreliable click produces an unreliable dataset — so the acquisition path
is fixed *before* anything starts recording from it, not after.

---

## 1. The defect (`v0.7.5: planned`)

Diagnosed from the v0.7.3 tree and **verified line by line** in
[`../gates/v0.7.4-phase-0.md`](../gates/v0.7.4-phase-0.md) §8, not copied from a brief.

| Location | Code | What it does |
|---|---|---|
| `api/routes_events.py:27` | `SSE_UPDATE_S = 2.0` | the server pushes a full update every 2 s |
| `ui/app.js:928` | `if (u.situations && activePanel === "situations") renderSituations(...)` | every update re-renders the whole list |
| `ui/app.js:455` | `clear(sits)` | `renderSituations` **tears down every card**, expanded ones included |
| `ui/app.js:387–390` | `const d = await api(...)` … `clear(container)` | `renderDetail` clears **only after** the round trip |
| `ui/app.js:481` | `if (expanded.has(s.id)) renderDetail(detail, s.id)` | the rebuilt detail is filled **fire-and-forget — not awaited** |

Composed, that is a defect with a 2-second period:

1. The SSE update arrives. `renderSituations` runs and `clear(sits)` (455) destroys every card —
   including the one the operator has open, and the feedback buttons inside it, whose `onclick`
   closures were created by `renderDetail` at `ui/app.js:446–447`.
2. A fresh card is built. Its detail div is set to `display: block` immediately (`ui/app.js:459`,
   from `expanded.has(s.id)`), so it is **visible**.
3. It is **empty**, because line 481 calls `renderDetail` without `await`, and `renderDetail` does
   not clear-and-fill until its `GET /api/situations/{sid}` returns (387 → 390).
4. The card visibly collapses and reopens on every update.

One measurement sharpens the diagnosis and is not obvious from the line list: the **expand** path at
`ui/app.js:477` *does* await —
`expanded.add(s.id); detail.style.display = "block"; await renderDetail(detail, s.id);` — while the
**rebuild** path at 481 does not. The empty-and-visible window is therefore specific to the
SSE-driven rebuild of an *already-expanded* card. A first expansion never shows it, which is exactly
why it survives casual testing.

### 1.1 The failure that matters is not the flicker (`v0.7.5: planned`)

A click landing inside the window hits a node already detached from the document and does nothing.
That is annoying and it is **not** why this release exists.

The failure that matters is this: **a click can land on a card that was rebuilt between the
operator's visual decision and their mouse-down.** The operator reads a four-alarm grouping, decides
"these belong together", and moves the mouse. Two seconds elapse. The list is rebuilt; the situation
now has nine members, or a different situation occupies that position in the list. The mouse-down
lands on a button from the *new* render, and `POST /api/situations/{sid}/feedback` records
`{"verdict": "confirm"}` against a membership the operator never evaluated.

That is a **silently wrong label**, and it is worse than a missing one:

* a missing label is visible as absence, and the bias report v0.8.0 must produce can count it;
* a wrong label is indistinguishable from a considered one at every layer downstream — `learn.
  penalize()` acts on it, the dataset records it, and a v0.9.0 model trains on it — and **nothing in
  the system can detect it**, because the record carries no evidence of what was on screen.

This is the label-integrity problem, and it is the reason v0.7.5 precedes v0.8.0 rather than
following it.

---

## 2. The fix, specified — deliberately minimal (`v0.7.5: planned`)

Two changes in `ui/app.js`, and one addition. No new endpoint, no schema change, no API contract
change, no new dependency.

### 2.1 Reconcile instead of clear-and-rebuild (`v0.7.5: planned`)

> **While a card is expanded, `renderSituations` must not destroy it.**

Reconcile by situation id: for each situation in the incoming list, reuse the existing card node if
one is present for that id, updating the header in place, and build a new node only for ids that
were not on screen. Remove nodes whose id has left the list. `clear(sits)` at `ui/app.js:455`
therefore stops being the first thing the function does.

The narrowest correct version reuses a node **only while `expanded.has(s.id)`** and keeps today's
clear-and-rebuild for collapsed cards, which are cheap and carry no click target the operator is
aiming at. Collapsing a card resumes normal rebuilding. Preferring the narrow version is deliberate:
it keeps the diff small enough to read, and the general reconciler is a UI-framework-shaped answer to
a problem that has one node in it.

### 2.2 Build into a fragment and swap atomically (`v0.7.5: planned`)

> **`renderDetail` must never leave the container visible and empty.**

Build the detail content into a `DocumentFragment`, then clear and append in one synchronous step
after the `await` resolves — rather than `clear(container)` at `ui/app.js:390` followed by
incremental appends. There is then no reachable state in which the container is displayed with no
content, whatever the network does.

This also fixes the case §2.1 does not: the very first expansion of a card, where `renderDetail` is
awaited but the container is still briefly empty between the click and the response.

### 2.3 The held card must say it is stale (`v0.7.5: planned`) — **required, not optional**

Holding the card fixes the race by **freezing what the operator is looking at**. That trades a wrong
label for a stale one, and a stale label is only better if the operator knows it is stale.

> **The held card's header carries a static text marker saying so.**

Three lines of code. No controls, no countdown, no refresh button, no live diff. Something to the
effect of *"held while open — may not reflect the last few seconds"*, rendered in the header of any
card that is currently expanded and therefore not being rebuilt.

Without it, this release replaces a visible defect with an invisible one: the operator confirms a
four-alarm grouping that now has nine and is **never told** — which is precisely the label-integrity
problem §1.1 says the release exists to fix, reintroduced by its own fix. The marker is what makes
the trade honest.

---

## 3. Explicitly not in v0.7.5

Each of these is a reasonable idea and each would make the release bigger than the defect.

1. **Any UI remodelling.** The UI is to be rebuilt later; this release must not invest in it. Fix
   the acquisition path, change nothing else.
2. **Changing `SSE_UPDATE_S`.** Slowing the stream would narrow the window without closing it, and
   it would change what every other panel does to fix what one panel does wrong.
3. **A pause control.** A control the operator must remember to use is not a fix for a race the
   operator cannot see. §2.1 holds the card automatically, for exactly as long as it is open.
4. **Optimistic-concurrency rejection on the feedback endpoint.** Sending a version with the verdict
   and rejecting on mismatch is the obvious-looking design and it is the **wrong primitive here** —
   see [`FEEDBACK-DATASET-0.8-DRAFT.md`](FEEDBACK-DATASET-0.8-DRAFT.md) §2, which records why
   rejection suits *edits* and not *observations*, and why the right answer is a membership
   fingerprint captured **with** the label rather than a precondition on the endpoint. In a system
   that updates every two seconds, rejecting on change would trade a race for a livelock.
5. **Any change to `POST /api/situations/{sid}/feedback`.** The contract stays `{verdict}`. The
   fingerprint of §4 is additive and belongs to v0.8.0.
6. **Touching the engine, the store, or correlation.** This is a UI defect with a UI fix.

---

## 4. What v0.7.5 hands to v0.8.0

A click the operator meant. That is all, and it is the whole point: v0.8.0's dataset columns are
only worth recording if the verdict in them was a considered judgement about the membership shown.
The membership fingerprint that makes *which* membership recoverable is v0.8.0's, specified in
[`FEEDBACK-DATASET-0.8-DRAFT.md`](FEEDBACK-DATASET-0.8-DRAFT.md).

## 5. Tests v0.7.5 must add

Named here so the release does not have to invent them, in the shape this project already uses:

* **An expanded card survives an SSE update** — drive `applyUpdate` twice with an expanded id and
  assert the same DOM node is still in the document, with the same feedback buttons attached.
* **The detail container is never both displayed and empty** — assert across a simulated
  slow-response render that no state exists with `display: block` and no children.
* **The stale marker is present exactly while a card is held** and absent once collapsed.
* **The endpoint contract is unchanged** — the existing feedback tests pass **unedited**, which is
  what proves this release touched the acquisition path and nothing else.
