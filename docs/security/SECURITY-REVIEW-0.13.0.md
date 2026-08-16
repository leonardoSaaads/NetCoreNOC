# Security review — v0.13.0

**The release that replaced the UI.** Every byte a browser executes is new, so the review's first
question is not "what did this release add" but "what did the rewrite lose". This document answers
that, closes **F53**, and issues **F54** and **F55**.

Findings continue from **F53**. Nothing before it is renumbered.

---

## 1. F53 — closed, and the mechanism named

**The finding** (`SECURITY-REVIEW-0.12.0.md` §2): the panel loaders had no capability check. A
viewer calling `renderPanel("audit")` issued no request — but only because `prunePanels` had
removed the container and `clear(null)` threw a `TypeError` before `api(...)` was reached.

> Before trusting a behaviour, ask which line of code is responsible for it — and if the answer is
> "an exception", it is not a guarantee.

**Reproduced before it was repaired.** `docs/gates/v0.13.0-phase-0.md` §3 drove `renderPanel(id)`
as a viewer against the unmodified v0.12.0 tree, for all seven admin panels:

```
users … audit   TypeError: Cannot read properties of null (reading 'firstChild')
requestsAfterBypass: []
```

Seven panels, seven `TypeError`s, zero requests. `'firstChild'` is `clear()`'s first statement.

**How it is closed.** Not with a check inside a loader — that would be seventeen checks, which is
seventeen chances to forget, and the one that is forgotten is invisible until it is exploited.
`router.resolve()` returns a **decision**, and `shell.js` turns only a `view` decision into a
mounted component (ADR #176). A principal without the capability receives a `Refused` component:
the real one is never constructed, `componentDidMount` never runs, and there is no request to
suppress. **The zero is a decision, not a dereference.**

**Measured after.** A viewer navigating to every admin address:

| | v0.12.0 | v0.13.0 |
|---|---|---|
| requests issued | 0 | **0** |
| how the call ended | `TypeError` | **rendered a refusal** |
| what the operator sees | nothing | the view's name, the missing capability, and that no request was sent |

**Verdict: CLOSED.** Two things make that a measurement rather than a claim:

* `test_a_view_reached_by_address_without_its_capability_refuses_by_decision` asserts all three —
  `paths == []`, `refused is True`, **`threw is None`**. The third assertion is the inversion of
  v0.12.0's, whose docstring said *"when the mechanism becomes deliberate the test fails and has to
  be updated deliberately"*. This is that deliberate update.
* Reverting the repair is demonstration §5 in `v0.13.0-guard-demonstrations.md`: three guards go
  red, the control holds.

**What is NOT claimed**: that the client's refusal protects anything. It does not. The appliance
refuses the same request and `tests/test_rbac.py` proves it. The client's refusal keeps the shape of
the estate's administration out of every viewer's access log; the server's refusal is the control.

---

## 2. F54 — the console's accessible names carried operator-supplied text (fixed in this release)

**Severity: low. Status: fixed before release.**

The member-marking checkbox on the situation card was first written with

```js
aria-label=${`Mark ${alarmName(a)} on ${deviceName(a)} as not belonging`}
```

An operator label is attacker-influenced text: it arrives in a trap, or is typed by an editor, and
`tests/test_security_ui.py` has pushed a hostile payload through that exact path since v0.2.0.

**This is not an XSS.** Preact sets the attribute through `setAttribute`, so the value is inert as
markup, and invariant 4's structural assertion (no dangerous elements introduced) was green
throughout. What went red was the *other* half of that invariant —
`payloadInAttributeValues == 0` — which v0.12.0 wrote with this reasoning: *"`el` sets attributes
through setAttribute, so this would mean a new path composed markup"*.

**That reasoning no longer holds, and the honest response was to fix the code rather than the
assertion.** Under a framework that sets every attribute safely, a payload in an attribute no
longer implies composed markup — so the assertion could have been "corrected" to permit it. It was
not, because the property it now protects is a real and different one: **an inert string is not
therefore an appropriate string to read aloud.** A screen-reader user would have had a
200-character hostile payload announced to them as the name of a checkbox.

**Fix**: the accessible name is positional — *"Mark member 3 of 8 as not belonging"* — which is
also simply clearer. `payloadInAttributeValues == 0` stands at full strength.

**Residual**: this was found because one guard happened to cover it. The general property — *no
operator-supplied text reaches an accessible name* — is asserted only where the escaping scenario
walks, which is the situation card, the entities screen and the alarm classes screen. A future
screen could reintroduce it elsewhere. **ROADMAP line**, not closed.

---

## 3. F55 — a governance principal could be offered controls the appliance refuses (fixed)

**Severity: low. Status: fixed before release.**

The Governance screen was gated on `rbac.read` and offered Apply, Clear and Roll back, which
require `rbac.write` and `scope.write`. The Link scorer screen was gated on `scorer.write` and
offered Preview, which requires `scorer.preview`. Settings was gated on `config.read` and offered
the write forms.

`rbac.read` without `rbac.write` is not hypothetical — **granting one without the other is the
entire point of the governance feature**. Such a principal would have been offered controls the
appliance refuses, producing a denied-and-audited row for a click the console should never have
drawn, and teaching an operator that the console does not know its own rules.

**Found by a guard written in this release**,
`test_mutating_controls_are_behind_capability_guards`, which resolves every `post()`/`del()` call
site onto its `rbac.ROUTE_PERMISSIONS` entry and requires that either the router already resolved
that capability or the screen gates the control itself.

**Fix**: Governance declares `["rbac.read", "scope.read"]` (it reads both on mount, and declaring
one would have leaked a 403 for the other) and gates each write on its own capability; the scorer
gates Preview on `can("scorer.preview")`; Settings renders a read-only precedence view for
`config.read` without `config.write`.

**A side effect worth naming**: writing that guard required making one route literal.
`governance.js` composed its path as `` post(`/api/${kind}`) ``, which no static reader can
attribute to a capability. It is now `POLICY_PATH[kind]` with both literals spelled out. **A route
assembled from a variable is a route no guard can check.**

---

## 4. The rewrite's own surface — what was assessed and what was found

### 4.1 No client-side check replaced a server-side one

Every `can()` in this console hides something; none of them protects anything. `session.js` says so
at the point of definition and `test_the_client_refusal_is_never_the_only_refusal` asserts the one
place it could have gone wrong: the hardening-only refusal reads **the bounds the server
published** (`GET /api/scorer` returns them) and holds no client-side copy of any floor. A test
asserts that no literal matching a project bound appears in `parameters.js`.

No route gained a client-side-only rule. No route lost a server-side one.

### 4.2 The CSP and the security headers are unchanged

Byte-for-byte:

```
default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self';
connect-src 'self'; base-uri 'none'; frame-ancestors 'none'
```

`test_csp_is_unchanged_and_forbids_inline` now compares the **whole string** rather than a
substring, and demonstration §12 shows it red under `'unsafe-inline'`.

**The module graph did not require a relaxation, and the reason is worth recording**: both vendored
ESM assets are import-free, so the console resolves every import by relative path. A bare specifier
would have needed an import map, an import map is an inline `<script type="importmap">`, and
`script-src 'self'` forbids it. **That constraint is why this UI uses Preact's core and no hooks
module** — `hooks.module.js` imports `"preact"`. ADR #174 records it; a supply-chain test asserts
it.

### 4.3 The vendored assets are pinned, licensed and attributed

| Asset | Bytes | Licence | Provenance |
|---|---|---|---|
| `d3.v7.min.js` | 279 706 | ISC | unchanged since v0.2.0 |
| `preact-10.29.8.module.js` | 11 693 | MIT | npm tarball, sha1 `fc85b82d…` verified against the registry |
| `htm-3.1.1.module.js` | 1 207 | Apache-2.0 | npm tarball, sha1 `49266582…` verified against the registry |

Each tarball was verified against the registry's own published `dist.shasum` **before a file was
extracted**, so the chain is registry → tarball → file → the pin in `CHECKSUMS.txt`.

**A gap in the existing guard was found and closed.**
`test_vendored_assets_match_pinned_checksums` iterates `CHECKSUMS.txt` and verifies what it finds —
so it could only ever check what the file already named. **An asset dropped into `vendor/` with no
pin was invisible to it.** `UI-0.13-DRAFT.md` §10.1(4) said this had to be *"verified rather than
assumed"*; it was assumed. `test_every_vendored_asset_is_pinned_by_name` now asserts the opposite
direction, and demonstration §11 shows it red under an unpinned asset.

### 4.4 No new capability, no new audit action, no migration

`rbac.PERMISSIONS` is unchanged: 30 capabilities. `rbac.ROUTE_PERMISSIONS` is unchanged: 43 routes,
and `test_the_api_route_order_is_unchanged_by_the_ui_rewrite` asserts the `/api` order is
byte-identical to the v0.7.1 baseline. No audit action was added. **Zero migrations** — the theme
preference is a cookie (ADR #172), not a per-user row.

### 4.5 The one response shape that widened, and what was deliberately left out

`GET /api/config` gained `precedence` and `startup` (ADR #179), because draft §7.1 requires three
columns and `RuntimeConfig` holds only the merged value.

**`tls_cert` and `tls_key` are reported as a single `tls_enabled` boolean, and `api_token` is not
reported at all.** `config.read` is in `AUDITED_DENIED_PERMISSIONS` precisely because *"reading the
allowlist reveals network-security posture"* (F9); adding filesystem paths to that response would
widen what one audited capability discloses, for no operator benefit. The route remains admin-only
and admin is never scoped.

### 4.6 The theme cookie

`ncn_theme`, `SameSite=Strict`, `Path=/`, one year, **not `HttpOnly`** (the client must read it).
It carries a theme name from a closed set of three and nothing else — never a user id, never a
token. **A value outside the set is discarded**, so a hostile cookie can at worst select a
supported theme; demonstration §9 shows the validation red when removed.

**No `Secure` flag**, deliberately: this appliance is routinely deployed on plain HTTP inside a
management network, and a cookie that silently failed to persist there would be worse than one that
carries a theme name in the clear. It carries nothing whose disclosure matters, which is what makes
that trade acceptable rather than merely convenient.

### 4.7 The static surface grew by 36 paths

Every one is a UI module or a vendored asset, enumerated in `routes_static.STATIC_ASSETS` **and**
in `declare.UNAUTHENTICATED_PATHS` — the second being the reviewable claim that fetching it needs
no capability. A directory route was rejected (ADR #175): it is a path-traversal surface, and it
would make "what does this appliance serve?" unanswerable from the code. Demonstration §14 shows
the equality guard red when a module leaves the allowlist.

---

## 5. What the instrument still cannot see

Stated first, because a green suite over a rewritten UI is the most misleading artefact this
release could produce.

1. **The entire visual layer.** The harness DOM has no layout, no cascade, no `getComputedStyle`,
   no paint and no accessibility tree. Every judgement in this release about spacing, contrast,
   hierarchy, or whether two colours are distinguishable is **unmeasured**. `style.css` is 560
   lines that no test executes beyond substring checks on a handful of tokens.
2. **The force-directed graph.** d3 is a recording double. `app/views/graph.js` is 172 lines of
   which none is executed by any assertion — node placement, edge rendering, zoom, drag and the
   simulation are entirely uncovered. The double throws on an unknown d3 API, which is a drift
   alarm, not coverage. **This is the largest single uncovered surface in the release** and the
   file says so at the top.
3. **The timeline SVG**, for the same reason, mitigated by a text table beside it that *is*
   rendered and asserted.
4. **What a screen reader announces.** There is no assistive technology in this environment. Every
   accessibility claim in ADR #180 is a claim about **markup** — a label exists, a landmark is
   named, one tab stop rather than sixteen — and none of them is a claim about what is announced.
5. **Real browser behaviour under a live SSE stream.** The invariant-3 tests drive one update
   synchronously in a DOM with no renderer.

---

## 6. Residual risks carried forward

| Risk | Why it is accepted |
|---|---|
| The graph is unexecuted | Replacing d3 means writing a force layout inside the release that rewrites everything else (Part III, closing §12.2). Recorded, not hidden. |
| ~30 module requests on a cold load | The honest cost of no bundler (ADR #175). Dominant only over a slow WAN. |
| A flash of the wrong theme | The fix is an inline script in `<head>`; the CSP forbids inline scripts, correctly. Visible, one frame, only when the cookie disagrees with the system setting. |
| Operator text in a future accessible name | F54's residual. Covered where the escaping scenario walks, not in general. |
| `test_security_headers_on_every_route_class` cannot catch a change to the CSP constant | It compares against the same constant. Recorded in the demonstrations doc §12 rather than mistaken for coverage. |
| No hardening-only value has a write path except the scorer bounds | ADR #178. Adding one for the sufficiency floors is evidence-chain work, not console work. |

---

## 7. Regression posture

| | |
|---|---|
| F1–F52 | unedited and green |
| **F53** | **CLOSED** — §1 |
| **F54** | **fixed in this release** — §2, residual recorded |
| **F55** | **fixed in this release** — §3 |
| tests | 1428 passing, up from 1339 |
| DOM tests executed | 24, up from 18 |
| `make eval` | byte-identical: `c2e8a0ce…8b9b6f26` |
| seal query count | 0 |
| migrations | `0001`–`0013`, unchanged |
| runtime dependencies | 5 |
