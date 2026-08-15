# The UI — v0.13.0 draft (specification only, not implemented in v0.12.0)

<!-- release-claim: v0.13.0 = ui -->

**Implement none of this in v0.12.0.** Every element below is tagged **`v0.13.0: planned`**.

v0.12.0 built the instrument and changed no pixel. This is the shape of what replaces the current
UI, decided **now**, in a document, rather than under deadline while someone is writing CSS.

Its parent is [`ROADMAP-0.8-TO-0.13.md`](ROADMAP-0.8-TO-0.13.md). The decisions it implements are
[`../adr/DECISIONS.md`](../adr/DECISIONS.md) **#168** (the characterisation boundary), **#171** (the
framework) and **#172** (theme persistence). The invariants it must honour are
`tests/test_ui_invariants.py`, and the measurements it starts from are
[`../gates/v0.12.0-phase-0.md`](../gates/v0.12.0-phase-0.md).

---

## 0. The three facts this draft starts from (`v0.13.0: planned`)

**Ten horizontal tabs are saturated.** `Situations, Timeline, Entities, Users, Tokens, Config,
Scorer, Governance, Quarantine, Audit`. Promotion would be the eleventh, and it was deferred in
v0.11.0 for an unrelated reason (#163) — but there was nowhere to put it either.

**Phase 2 and Phase 3 do not fit at all.** LLM-assisted troubleshooting, per-equipment test
templates, and automated NOC ticket emission are not one tab each; they are sections with their own
sub-navigation. A horizontal bar cannot absorb them.

**Four capabilities and eight routes have no UI surface today** (§4). The admin cannot reach, from
any screen, things this appliance already does.

---

## 1. Navigation (`v0.13.0: planned`)

**A sidebar, a main work area, and a context panel.** Zabbix, Grafana and ScienceLogic converged on
this independently, and for a reason that is about growth rather than taste: it is the shape that
**absorbs new sections without a redesign**.

```
┌──────────────┬────────────────────────────────────────┬──────────────────┐
│  SIDEBAR     │  MAIN WORK AREA                        │  CONTEXT PANEL   │
│              │                                        │                  │
│  Operations  │  the current view: the situation list, │  the selected    │
│    Situations│  the graph, a table, a form            │  thing's detail  │
│    Timeline  │                                        │                  │
│    Graph     │                                        │  collapses on a  │
│    Entities  │                                        │  narrow viewport │
│              │                                        │                  │
│  Evidence    │                                        │                  │
│    Labelling │                                        │                  │
│    Corpus    │                                        │                  │
│    Judge     │                                        │                  │
│    Promotion │                                        │                  │
│              │                                        │                  │
│  Administer  │                                        │                  │
│    Users     │                                        │                  │
│    Tokens    │                                        │                  │
│    Settings  │                                        │                  │
│    Governance│                                        │                  │
│    Audit     │                                        │                  │
│              │                                        │                  │
│  ── reserved ────────────────────────────────────     │                  │
│  (Phase 2/3 sections attach here — see §2)            │                  │
└──────────────┴────────────────────────────────────────┴──────────────────┘
```

**Three groups, and the grouping is the argument**: *Operations* is what is broken now, *Evidence* is
what the system has learned and what it refuses to conclude, *Administer* is the machine itself.
That division already exists in the product; today it is flattened into one row of ten tabs.

### 1.1 The `#sidebar` collision, which must not silently break a guard (`v0.13.0: planned`)

`index.html` **already has `<div id="sidebar">`, and it is the detail panel beside the graph — not
navigation.** A rewrite that introduces sidebar navigation must rename one of the two.

This is exactly the kind of rename that breaks a guard quietly, so:

* `tests/test_ui_invariants.py` reaches `#sits`, `#tabs`, `#login`, `#app`, `#fltStatus`, `#fltText`
  and `.panel[data-panel=…]`. **Every one of those is a selector v0.13.0 will change.** The tests are
  written about *behaviour*, so they will need updating; the update must be a **rewrite of the
  selector, never a weakening of the assertion**.
* `tests/domharness/selftest.mjs` asserts `index.html` parses into ten panels and the eight ids
  above. It will go red on the rename, which is correct and is the point.
* The harness DOM's selector engine **throws** on a selector outside its grammar. A new selector
  form fails loudly rather than matching nothing.

**Rule for v0.13.0**: when a selector changes, the assertion count in
`tests/test_ui_invariants.py` may not go down. A guard rewritten during the change it guards is at
its least trustworthy, and the count is the cheapest thing to check.

## 2. Where Phase 2 and Phase 3 live — **and no empty placeholders** (`v0.13.0: planned`)

The shape accommodates; **the UI does not announce.**

This project was right to criticise "mechanism with no volume" when v0.9.1's `close` channel shipped
unused, and a greyed-out *Troubleshooting* item promising a feature nobody has built is the same
error with worse ergonomics — it teaches an operator that parts of the product do not work.

**So: no Phase 2 or Phase 3 item appears in v0.13.0's sidebar. Not disabled, not greyed, not
"coming soon". Absent.**

What v0.13.0 owes them is only that adding them later is **not a redesign**:

| Future section | Where it attaches | What it needs that v0.13.0 must not preclude |
|---|---|---|
| **Phase 2 — troubleshooting** | a fourth sidebar group, *Diagnose* | a situation-scoped view with its own sub-navigation; the context panel showing a running transcript |
| **Phase 2 — test templates** | under *Administer*, beside Governance | a per-equipment-class editor; the same three-class parameter discipline as §6 |
| **Phase 3 — tickets** | a fifth group, *Escalate* | an outbox with per-ticket state; a link from a situation to the ticket it produced |

The concrete obligations that follow — and these are the whole of §2's cost:

1. **Sidebar groups are data, not markup.** Adding a group is adding an entry, not editing a layout.
2. **A section owns its own sub-navigation.** No section may assume it is a single flat view.
3. **The context panel is addressable by any section**, not owned by the situation list.
4. **A situation is linkable.** Phase 2 and Phase 3 both need to point at one from elsewhere; §3
   makes routing a requirement anyway.

## 3. Routing, and the defence it removes (`v0.13.0: planned`)

v0.13.0 needs deep links: a situation must be shareable during an incident, and §2's future sections
need to point at one.

**Routing removes a defence that currently works by accident**, and this is the single most
important operational note in this document. v0.12.0's harness established it by execution:

> Calling `renderPanel("audit")` directly as a viewer issues **no request** — but only because
> `prunePanels` removed the panel's container, so `clear(null)` throws a `TypeError` before
> `api(...)` is reached. **There is no capability check inside the loaders.**

Today nothing can call a loader except a tab that was not rendered. **With routing, a URL can.**

**Requirement**: every view resolves its capability **before it fetches**, and a route the caller
may not hold renders a not-authorised view without issuing a request. `hash`-based routing (no
server change, no new route, no history API dependency) satisfies this; the mechanism is v0.13.0's
choice, the check is not.

The corresponding test already exists —
`test_a_panel_reached_without_its_capability_still_issues_no_request` — and it asserts *both* the
absent request and the `TypeError`, so when the mechanism becomes deliberate the test fails and has
to be updated deliberately.

## 4. Every capability with no UI surface today (`v0.13.0: planned`)

**This list is the specification.** Enumerated by execution against `rbac.ROUTE_PERMISSIONS`, not by
reading the UI: **30 capabilities and 43 routes; 4 capabilities and 8 routes are unreachable from
any screen.**

| Route | Capability | Min role | What an operator cannot do today |
|---|---|---|---|
| `GET /api/promotion` | `promotion.read` | viewer | see what the gate decided and **why it refused** |
| `POST /api/promotion` | `promotion.write` | admin | approve a promotion (deliberate in v0.11.0, #163 — the prerequisite is now met) |
| `GET /api/dataset/retention` | `config.read` | admin | see the three retention tiers |
| `POST /api/dataset/retention` | `config.write` | admin | change them — **the destructive one**, §7 |
| `POST /api/audit/prune` | `audit.prune` | admin | prune the audit log |
| `POST /api/users/{uid}/role` | `users.manage` | admin | change a user's role without deleting and recreating them |
| `POST /api/password` | `self.read` | viewer | **change their own password while signed in** |
| `GET /api/classes` | `classes.read` | viewer | browse learned alarm classes |

`POST /api/password` is worth naming separately: the login overlay handles a *forced* change on
first sign-in, so a signed-in operator has no way to change their password at all. That is a
security affordance missing from a product that ships password policy.

**And the reports, which are CLI-only** — an admin who does not have shell access to the appliance
cannot see any of them:

| Command | What it says |
|---|---|
| `python -m netcorenoc dataset stats` | what capture costs, in rows, and the window you actually have |
| `make bias-report` | confirms vs splits, operator concentration, label latency, **effective sample size in bags** |
| `make agreement-report` | how well the built-in scorer already agrees with your operators, **conditioned** |
| `make shadow-report` | the sufficiency **verdict**, the minimum detectable difference, the sealed holdout and **its read count** |
| `make census` | what the promotion gate decides on this corpus, stated in advance |
| `python -m netcorenoc audit verify` | walks the hash chain and reports the first broken link |

**The requirement**: the admin dashboard (§5.3) surfaces the *verdict and the headline* of each,
with the full report available. These are deterministic offline reports over frozen inputs and
several are **byte-compared by tests** — so the UI **displays** them and never recomputes them. A
second implementation of the shadow verdict would be a second source of truth for the number four
releases of evidence discipline rest on.

## 5. Per-role dashboards (`v0.13.0: planned`)

A landing view per role, **built from what exists** rather than from what would be nice.

### 5.1 Viewer — what is broken now (`v0.13.0: planned`)

Open situations by severity; the network graph; the timeline; active alarm count and ingest-gap
banner. The scope badge stays exactly as it is: a scoped operator must know their picture is
partial, and the redacted-member count must remain visible. Nothing here is new — it is today's
Situations tab with room to breathe.

### 5.2 Editor — the above, plus what their feedback produced (`v0.13.0: planned`)

The labelling surface (confirm / split / partial split, unchanged in **contract** — see §11), plus
the thing an editor has never been shown: **what their labels have done.** Labels contributed,
confirm/split ratio, how many of their bags were *mixed* (the only ones that contained a decision),
and where the corpus stands against the pre-registered floors.

That last is the honest one. An editor labelling into `INSUFFICIENT_EVIDENCE` deserves to see the
gap and the projection — `make shadow-report` already computes both, and the number is *"13 split
bags against 50 required"*, not a progress bar.

### 5.3 Admin — everything the system has built (`v0.13.0: planned`)

Everything above, plus: users, tokens, service-token issuance, quarantine, the audit log and its
verification state, the scorer with preview/apply/rollback, **promotions and refusals**, the corpus
census, the bias report, shadow mode, the judge's verdict, and **the seal's state and query count**.

Three of these are new surfaces for things that already exist, and each has a specific requirement:

* **Promotion.** Show the verdict, the refusal *reason*, the triggers, the detection threshold, and
  what would have to change. A refusal is an explanation, not an error state, and the route already
  returns all of it.
* **The seal.** Displayed, never actionable. §6's third class.
* **Audit chain verification.** `audit verify` walks the chain and reports the first broken link. The
  UI shows *intact / broken at row N*, and offers no repair, because there is none.

## 6. The parameter surface, in three visible classes (`v0.13.0: planned`)

**This section protects principle 9, and it must exist before anyone builds a settings screen.**

An "all the parameters" screen shows a sufficiency floor beside a sampling rate. An admin lowers the
floor because the field allowed it, and nine releases of evidence discipline die to an input box.

> `resolved = max(project floor, deployment policy)` — **harden always, soften never.**

| Class | Examples from this tree | What the UI permits |
|---|---|---|
| **Mechanism** | shadow sampling rate; the three retention tiers; session idle/absolute TTL; display limits (`limit=50`, `limit=200`, the 30-link cap); training hyperparameters (§8) | **freely settable, with the cost shown** |
| **Hardening-only** | the pre-registered sufficiency floors (`asserting_bags ≥ 50`); the detection-threshold condition; the visibility-scope policy; the scorer's degeneracy bounds | **raise only.** The UI shows the project floor, accepts stricter values, and **refuses looser ones with the reason** |
| **Structural, read-only** | the `eval` output hash; the seal and its query count; the `incumbent_linked` invariant; the audit hash chain; `params_hash`; the capability **ceiling** | **displayed for transparency, no edit control** |

### 6.1 Why the third class is what makes the screen educate (`v0.13.0: planned`)

An admin who sees *"seal: intact, 0 queries"* and finds no control to change it learns something
true: it is a **guarantee**, not a preference.

That is principle 1 honoured precisely. The product never says *"you can't do that"* — it says
*"you can, and here is the risk"*. But it is also honest that some things are **properties of the
system rather than risks the operator is taking**. A read-only row with an explanation is not a
refusal; it is the difference between a setting and a fact.

**Presentation requirement**: a structural value is rendered *visibly differently* from a settable
one — not a disabled input. A greyed-out field says "you may not"; a fact says "this is what is
true". They are different sentences and the UI must not conflate them.

### 6.2 The hardening-only refusal, in full (`v0.13.0: planned`)

When an admin submits a looser value, the UI must show **four things**:

1. the project floor, and that it cannot be lowered here;
2. the value they submitted, and that it was not applied;
3. **why the floor exists** — a pointer to the pre-registration that set it;
4. **the stricter direction is available**, with the field still usable.

A bare *"rejected"* teaches nothing and invites a workaround. `SECURITY-REVIEW-0.6` §4 already
treats wording as a control for the scorer preview; the same standard applies here.

## 7. Editing what is an environment variable today — **the precedent already exists** (`v0.13.0: planned`)

The requirement is that an admin may change anything, with the impact shown. **The mechanism is
already in the tree and already audited.** From `runtime.py`, verbatim:

> *"Config precedence (DESIGN v0.2): environment variables are the defaults; an admin saving a value
> from the UI writes a `meta` row that then takes precedence. This small holder is the in-memory
> view both sides read, refreshed from `meta` at startup and on every audited change so the running
> receiver (allowlist) and maintenance loop (retention) pick it up without a restart."*

It works today for exactly two values — `allowlist` and `retention_days` — through `/api/config`,
with an audit row and no restart. **v0.13.0 generalises a proven pattern; it does not invent one.**

### 7.1 Three columns, always (`v0.13.0: planned`)

| Setting | Environment default | Database override | **Effective** |
|---|---|---|---|
| `NETCORENOC_ALLOWLIST` | *(unset — allow all)* | `10.0.0.0/8` | `10.0.0.0/8` |
| `NETCORENOC_RETENTION_DAYS` | `7` | *(none)* | `7` |
| `NETCORENOC_HTTP_PORT` | `8080` | *(not overridable — §7.3)* | `8080` |

An operator's real question during an incident is *"why is this value what it is?"* Two sources with
one displayed number cannot answer it, and a two-source configuration that hides its precedence is
more dangerous than a one-source one.

### 7.2 The impact statement, per setting (`v0.13.0: planned`)

Every setting carries: **what changes, when it takes effect, and whether it is destructive.**

`routes_admin.py` already reasons about this for retention, and the reasoning is the model:

> *"lowering retention deletes rows and there is no rollback"* — so the endpoint **previews by
> default**, `preview=True` must be deliberately overridden, and both the preview and the change are
> audited.

**Settings that need preview-before-destroy**, named so v0.13.0 does not have to rediscover them:

| Setting | Why |
|---|---|
| `retention_days` (operational) | deletes cleared/closed history; no rollback |
| the three **dataset** retention tiers | deletes the corpus every ML release is built on; **no rollback**, and six months not captured cannot be reconstructed |
| `audit.prune` | deletes audit rows; the chain is hash-linked, so a prune is visible but not reversible |
| an **entity reset** / **profiler wipe** | already confirms; keep it, and show what will be forgotten |

Everything else is reversible and needs no preview. Scorer configuration is explicitly **not** in
this table: `scorer_config` is append-only and rollback is a pointer move.

### 7.3 What needs a restart, said honestly (`v0.13.0: planned`)

`Settings` is **read once at startup and never mutated**. So, today:

| | Settings |
|---|---|
| **live** — no restart | `allowlist`, `retention_days` (the two `RuntimeConfig` holds) |
| **restart required** | `db_path`, `trap_host`, `trap_port`, `http_host`, `http_port`, `audit_retention_days`, `tls_cert`, `tls_key`, `log_json` |

**Saying so is more useful than a screen that pretends otherwise.** A settings page that accepts a
new `trap_port` and appears to succeed, while the receiver keeps listening on the old one, is worse
than no settings page: the operator believes traps are arriving.

**Requirement**: a restart-required setting shows that it requires a restart **before** it is saved,
and shows *pending* afterwards until the process restarts. Extending `RuntimeConfig` to more values
is a legitimate v0.13.0 choice — but each addition is a live-reload path in a running receiver or
maintenance loop, which is engine work, and it may not be done silently.

### 7.4 A hardening-only setting can never be lowered through this surface (`v0.13.0: planned`)

§6's rule meeting §7's mechanism. `meta` overrides are **max-composed, not last-write-wins**, for
hardening-only values: writing a looser value is refused at the API, not filtered in the client.
The client's refusal is an affordance; the server's is the control.

## 8. Training hyperparameters, and the honest placeholder (`v0.13.0: planned`)

The expectation is that tree ensembles — XGBoost, random forest, decision trees — compete, with a
hyperparameter surface for the admin.

**State plainly, on the screen**: those models cannot run in the core. Principle 5 forbids the
dependencies, and `ROADMAP-0.8-TO-0.13.md` already records why — *"tree ensembles cannot be champion
before the external cartridge. Not on merit — on plumbing."* That is **v0.14.0**, out of process,
behind a worker harness.

**The placeholder's honesty requirement**: not a greyed-out field. A **statement naming the release
that enables it and the reason** — *"Tree ensembles run out of process behind the worker harness,
which is v0.14.0. Not a limitation of the model: a limitation of what may run inside this
process."* A disabled input says *"you may not"*; this says *"not yet, and here is what has to
exist first"*.

Two things anchoring this section are **not** speculative:

* **`challenger_run` already stores `iterations`, `learning_rate` and `fit_seconds`**
  (`0009_shadow_mode.sql`). Hyperparameters are already first-class in provenance; they simply have
  no surface. **Exposing what is already recorded is a far stronger design than inventing fields.**
* **A hyperparameter that changes the trained model must appear in
  `model_version.params_document`, and therefore in `params_hash`.** Otherwise two models with the
  same hash and different hyperparameters are indistinguishable, and the provenance v0.11.0 built
  becomes fiction. **Registered here as a constraint on both v0.13.0 and v0.14.0.**

## 9. Theming (`v0.13.0: planned`)

**The groundwork exists**: `style.css` carries **30 custom properties**, a `:root` block and a
`@media (prefers-color-scheme: light)` override. What is missing is an explicit toggle and its
persistence.

**`localStorage` is forbidden by an existing test** — `assert "localStorage" not in app_js`, which
is F2's remediation. That guard's value is that it is an absolute; a first carve-out turns it into
a judgement call on every future diff.

**Decision (ADR #172): a cookie.** `SameSite=Strict`, **not** `HttpOnly` (the client must read it to
avoid a flash of the wrong theme), carrying a **theme name from a closed set and nothing else** —
never a user id, never a token, never a preference blob. An unrecognised value falls back to
`prefers-color-scheme`, so a hostile cookie can at worst select a supported theme.

Rejected: a server-side preference (a route, a capability, a migration and an audit decision for a
display setting that is not a security boundary), and `localStorage` (not available). The honest
fallback, if v0.13.0 runs out of room, is **no toggle at all** — `prefers-color-scheme` alone, which
is what ships today and is not broken.

## 10. The framework (`v0.13.0: planned`)

**Recommended: Preact + htm, vendored as one ESM asset with pinned bytes.** ADR #171 has the full
argument; the short form:

| Option | Consequence |
|---|---|
| **Vanilla, modern** (ESM, Web Components, custom properties) | preserves everything; produces another hand-written 50 KB without structure — today's problem, deferred, with the rewrite budget already spent |
| **Micro-framework via ESM, vendored** (Preact + htm, or Lit) | **one vendored asset with pinned bytes — the door `d3.v7.min.js` already uses** |
| **Bundler / npm** | **breaks principle 6**; requires reopening the constitution |

The middle option asks the project for **nothing new**. `d3.v7.min.js` is **279 706 bytes — five
times `app.js`** — and already ships under `CHECKSUMS.txt` with its licence beside it, guarded by
`tests/test_supply_chain.py` and permitted by `script-src 'self'`. A second vendored asset walks a
path that already has an instrument on it.

**Preact + htm over Lit** on one ground specific to this project: **htm needs no compile step.**
Lit's ergonomic form is decorators plus TypeScript — a build step wearing a different hat. htm is
tagged template literals evaluated by the browser, so the source a maintainer edits is the source
the browser runs, which is the actual content of principle 6.

**And it makes the capability guard structural rather than textual.** A component that declares the
capability it requires, with a test that renders each role, is far stronger than
`split("const TABS")` — and v0.12.0's Phase 0 measured exactly how weak the textual form is.

### 10.1 The vendoring procedure, inherited exactly (`v0.13.0: planned`)

1. the asset lands in `src/netcorenoc/ui/vendor/` with its **version in the filename**;
2. its SHA-256 goes into `CHECKSUMS.txt` **in the same commit**;
3. its licence ships beside it, as `d3.LICENSE` does;
4. `tests/test_supply_chain.py` covers it — the file lists no assets by name, so this must be
   verified rather than assumed;
5. `package-data` already globs `ui/vendor/*`, so nothing there changes;
6. the CSP is **not** touched. `script-src 'self'` already permits it.

**The version is chosen at build time** against the then-current release and pinned by hash in the
same commit. This document does not name a version, because a version named a release early is a
version nobody re-checked.

## 11. What v0.13.0 must not do (`v0.13.0: planned`)

Enumerated so the next release **inherits** limits rather than discovering them.

1. **No build step.** No `package.json`, no lockfile, no bundler, no transpiler, no minifier, no
   `dist/`. `tests/test_build_step.py` is the guard and it was demonstrated red.
2. **No new runtime dependency.** Five, since v0.2.0.
3. **No client-side authorisation the server does not also enforce.** The client's check is an
   affordance; the server's is the control. Neither may be the only one.
4. **No `localStorage`.** §9. The F2 guard is untouched.
5. **No CDN.** Vendored, pinned, licensed.
6. **No relaxation of the CSP.** `default-src 'none'; script-src 'self'`, no `'unsafe-inline'`.
7. **No empty Phase 2/3 placeholders.** §2. The shape accommodates; the UI does not announce.
8. **No settings control that can lower a hardening-only value.** §6, §7.4.
9. **No weakening of an invariant during a selector rename.** §1.1. The assertion count in
   `tests/test_ui_invariants.py` may not go down.
10. **No route may be fetched before its capability is resolved.** §3 — routing removes an accidental
    defence and must replace it with a deliberate one.
11. **No second implementation of a number a CLI report already computes.** §4. Display it.
12. **No change to the labelling contract.** `excluded_ids` asserts *marked-by-rest negative and
    nothing else* (DECISIONS #127); omitting it means "the operator marked nothing", never an empty
    list. The **gesture** may change; the **payload** may not.

## 12. The questions this document deliberately leaves open (`v0.13.0: planned`)

Listed because a decision recorded as open is honest, and a decision quietly assumed is not.

1. **Whether the situation card keeps the v0.7.5 held-card behaviour, or gets a real reconciler.**
   Holding the card freezes what the operator is judging and marks it stale. A reconciler that
   updated everything *except* the gesture in flight would be better and is a much larger change.
   The invariant is the constraint — the click target survives the update — and both designs can
   satisfy it. **Not decided here.**
2. **Whether the graph moves off d3.** It is 279 706 bytes for one view, and it is the one part of
   the UI v0.12.0's harness does not execute at all (d3 is a recording double). Replacing it would
   mean writing a force layout; keeping it means the largest vendored asset serves one screen.
   **No opinion registered**, but v0.13.0 must not decide it silently.
3. **Whether the context panel is one panel or per-section.** §2 requires only that any section can
   address it.
4. **How much of the admin dashboard is live versus on-demand.** The reports are deterministic and
   some are byte-compared; running them on every dashboard load is a cost nobody has measured.
5. **Whether `RuntimeConfig` grows.** §7.3. Each addition is a live-reload path in a running
   receiver or maintenance loop — engine work, with its own risk, and it may not be done silently.
6. **Accessibility beyond what exists.** `style.css` has `:focus-visible` and a narrow-viewport
   breakpoint. Keyboard navigation of a sidebar, focus management across route changes, and screen
   reader semantics for the graph are all unaddressed, here and today. **Naming them is not the same
   as specifying them**, and this document does not pretend otherwise.
