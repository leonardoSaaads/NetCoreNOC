# SCOPE — v0.13.0, the UI

**Theme: the UI a NOC would recognise, built against a harness that watches every step.**

This is the largest single change in the project's history and the first release whose deliverable
is something a human *looks at*. Two consequences shaped the method, and they are worth stating
before the scope:

1. **Every previous release could be verified by running a suite. This one cannot.** A UI that
   passes every assertion and is unusable is a failed release. Every screen in this release was
   rendered in the harness, dumped, and driven as each of the three roles **while it was being
   built**, and three defects were found that way that no assertion about markup would have caught
   (§5).
2. **The visual layer is outside the instrument, and this document does not pretend otherwise.**
   `docs/gates/v0.12.0-phase-6.md` §8 established that; it is inherited unchanged.

---

## 1. In scope

| | |
|---|---|
| The shell | sidebar, work area, context panel; hash routing with deep links; the `#sidebar` collision resolved |
| **F53** | repaired **structurally** — every view resolves its capability before anything is constructed |
| Operations | Situations (with the per-term contributions), Network graph, Timeline, Entities, Alarm classes |
| Evidence | Labelling, Corpus, Judge & promotion |
| Administer | Users, Service tokens, Settings, Link scorer, Governance, Quarantine, Audit |
| Elsewhere | Overview (per-role), Your account (reachable by address, not offered in navigation) |
| The framework | Preact 10.29.8 + htm 3.1.1, vendored as two pinned ESM assets |
| Themes | dark / light / system, with a cookie; density compact / comfortable |
| Accessibility | the floor of `UI-0.13-DRAFT.md` §12.6, met — and what was not attempted, named |
| The instrument | the harness extended to link and evaluate an ES module graph |

**Eight routes that had no UI surface now have one.** Re-derived by execution in Gate 0, not copied
from the draft: `GET /api/classes`, `GET`/`POST /api/promotion`, `GET`/`POST
/api/dataset/retention`, `POST /api/audit/prune`, `POST /api/users/{uid}/role`, and
`POST /api/password` — a signed-in operator previously had no way to change their own password.

## 2. Out of scope, and why

| Not done | Why |
|---|---|
| A real reconciler for the situation card | ADR #173. The renderer gives node identity; the *payload* is still held deliberately, because node identity is not meaning identity. The reconciler is a ROADMAP line. |
| Moving the graph off d3 | Part III, closing draft §12.2. It would mean writing a force layout inside the release that rewrites everything else. Its cost — 279 706 bytes for one screen, and no test executes it — is recorded rather than absorbed. |
| Growing `RuntimeConfig` | Part III, closing §12.5. Each addition is a live-reload path in a running receiver. `GET /api/config` reports precedence instead (ADR #179). |
| A write path for the pre-registered sufficiency floors | ADR #178. Persisting them changes the promotion gate's inputs — evidence-chain work, not console work. |
| HTTP routes for the four CLI reports | Draft §4 and §11.11: *no second implementation of a number a CLI report already computes*. The console names the command and says what it answers. |
| Any Phase 2 or Phase 3 placeholder | Draft §2. Not disabled, not greyed, not "coming soon". **Absent.** |
| A migration | None was needed. `meta` already holds operator configuration; the theme is a cookie. **Zero migrations.** |

## 3. The intentional behaviour changes, enumerated

Part VII.9: *declare them and count them.* **Any change not on this list is a defect in this work.**

1. Navigation is a grouped sidebar, not ten horizontal tabs.
2. Screens are **rendered on demand**, not declared in `index.html` and pruned. Absence is the
   default; presence is a decision.
3. The address bar means something: `#/situations/12` is a shareable deep link.
4. A principal without a view's capability sees a **refusal naming the capability**, where
   v0.12.0 silently had no tab.
5. An unknown address renders "no such screen" instead of falling through to a default.
6. The situation card holds its **payload** rather than its DOM subtree; the staleness marker now
   states how many updates are being withheld.
7. The graph, timeline and entities are separate screens rather than one always-visible panel plus
   tabs.
8. The Overview is a per-role landing screen that did not exist.
9. Heavy reads on the Overview are **on demand with a timestamp**, never on load (§12.4).
10. Every screen has explicit loading / empty / error / partial states; the empty state says what
    will fill it.
11. Destructive actions go through one preview component; the apply control does not exist until
    the preview has been seen.
12. The scorer form **refuses an out-of-bounds value before sending it**, showing the four things
    draft §6.2 requires.
13. Settings shows three-column precedence and what needs a restart.
14. Theme and density are explicit, persisted in a cookie.
15. The sidebar is keyboard-operable; focus moves to the work-area heading on route change.
16. Sign-out clears the fragment, so the next sign-in does not land on a refusal.
17. A render error no longer sends the operator to the sign-in card (the boot `try` covers the
    request only).
18. `GET /api/config` gained `precedence` and `startup`; its existing keys are unchanged.
19. The member-marking checkbox's accessible name is positional, not the device and class names
    (F54).
20. Governance, Scorer and Settings gate each write on its own capability (F55).

**Twenty.** Six of them (14–19) are things v0.12.0 could not do at all rather than things it did
differently.

## 4. What did not change

`engine.py`, `correlate.py`, `receiver.py`, `learn.py`, `capture.py`, `labels.py`, `scoring.py`,
`challenger.py`, `promotion.py`, `seal.py`, `judge.py`, `shaping/`, every migration, the CSP, the
security headers, `rbac.PERMISSIONS`, `rbac.ROUTE_PERMISSIONS`, the `/api` route order, the audit
catalog, and the five runtime dependencies. `make eval` is byte-identical.

## 5. Three defects found by driving the UI rather than by reading it

Recorded because they are the argument for Part I.1's working loop, and because **in all three
cases the markup was correct and every structural assertion was green**:

1. **The sidebar's arrow keys were inert.** `render` recomputed the roving `tabindex` from the
   active route on every pass, discarding what the keyboard had set. The keyboard trace reported
   `at: 0` after every keypress. A screen-reader user would have found the navigation unusable.
2. **The Overview sat on a spinner forever** when `/api/stats` failed. On screen, a console that
   cannot reach its own API and a network that is merely quiet looked identical — the worst
   behaviour a monitoring product can have during an incident.
3. **Governance offered `rbac.write` controls to a principal gated on `rbac.read`** (F55). Found by
   a guard written in this release, not by looking.

## 6. Anti-overengineering compliance

| Rule | Status |
|---|---|
| No new runtime dependency | **5**, unchanged. The framework is a vendored asset. |
| At most one migration, prefer zero | **Zero.** |
| `make eval` byte-identical | ✅ `c2e8a0ce…8b9b6f26` |
| No empty Phase 2/3 placeholders | ✅ none |
| No feature behind a screen that does not exist | ✅ every view is backed by a route that exists today |
| No second source of truth for authorisation | ✅ `rbac.tables`; the sidebar and the router read one table |
| No module over 400 lines, JavaScript included | ✅ the guard now covers both; largest module 388 lines |
| No fix inside a move | ✅ F54 and F55 are named findings with their own tests, not silent repairs |
| Behaviour changes enumerated | ✅ §3, twenty of them |
| Coverage band registered before measuring | ✅ ADR #181 |
| History preserved, Conventional Commits | ✅ |
