# Changelog

All notable changes to this project are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.13.0] - 2026-08-15 — "the UI"

**The largest single change in this project's history, and the first release whose deliverable is
something a human looks at.** v0.12.0 built an instrument and changed no pixel. This changes every
pixel, against that instrument, with every screen rendered and driven as every role while it was
being built.

### The headline

```
make dom  ->  24 passed          (executed, never a collected count; was 18)
make qa   ->  1428 passed        (was 1339)
make eval ->  byte-identical: c2e8a0ce…8b9b6f26
ui/app.js ->  52 738 bytes in one file  ->  an entry point plus 34 ESM modules
/api      ->  44 route/method pairs, order byte-identical to the v0.7.1 baseline
migrations -> zero
runtime deps -> five
```

### F53 is closed, and the mechanism is a decision rather than an exception

The finding v0.12.0 issued and could not fix: the panel loaders had no capability check, and a
viewer's zero-request property held only because `clear(null)` threw a `TypeError`. **Routing is
exactly what arms that**, so the repair is structural: `router.resolve()` returns a *decision*, and
one call site turns only a `view` decision into a mounted component. A principal without the
capability gets a refusal — the real component is never constructed, so there is no request to
suppress.

Reproduced before (7 panels, 7 `TypeError`s, 0 requests) and measured after (7 refusals, 0
requests, **0 exceptions**). The test that asserted the `TypeError` now asserts its absence.

### Eight routes that had no screen now have one

Re-derived by driving every control in Gate 0, not copied from the design draft:

| Route | What an operator could not do |
|---|---|
| `GET /api/promotion` | see what the gate decided and **why it refused** |
| `POST /api/promotion` | approve a promotion |
| `GET`/`POST /api/dataset/retention` | see and change the three retention tiers |
| `POST /api/audit/prune` | prune the audit log |
| `POST /api/users/{uid}/role` | change a role without deleting the account |
| `POST /api/password` | **change their own password while signed in** |
| `GET /api/classes` | browse learned alarm classes |

### Added

- **A three-region console**: grouped sidebar, work area, context panel. Hash routing, so a
  situation is a shareable deep link during an incident.
- **The per-term contributions on every situation**, with each term's number beside its bar —
  the screen that answers *"why did the system group these alarms?"* without leaving it.
- **Seventeen screens**, each with explicit loading, empty, error and partial states. The empty
  state says what will fill the screen and what to do meanwhile, because a zero-config product's
  first screen is always empty.
- **The settings surface in three visible classes** — mechanism, hardening-only, structural — with
  three-column precedence (environment default, database override, effective) and an impact
  statement per setting. A structural value is a fact with no control, never a greyed-out input.
- **Themes** (dark / light / system) and **density** (compact / comfortable), persisted in a cookie
  carrying a theme name from a closed set and nothing else.
- **Keyboard navigation**: the sidebar is one tab stop with roving `tabindex`; focus moves to the
  work-area heading on every route change.
- Preact 10.29.8 + htm 3.1.1, vendored as two import-free ESM assets — 12 900 bytes against d3's
  279 706 — each with its version in the filename, its SHA-256 pinned and its licence beside it.

### Fixed

- **F54** — the member-marking checkbox's accessible name embedded operator-supplied text. Not an
  XSS (the attribute is set safely), but an inert string is not therefore appropriate to read
  aloud: a screen-reader user would have had a hostile payload announced as a checkbox's name.
- **F55** — Governance offered `rbac.write` controls to a principal gated only on `rbac.read`, and
  the scorer offered Preview without `scorer.preview`. Granting one capability without the other is
  the entire point of the governance feature.
- The sidebar's arrow keys were inert; the Overview sat on a spinner forever when `/api/stats`
  failed. Both were found by **driving** the UI, not by reading it.
- `tests/test_supply_chain.py` verified only what `CHECKSUMS.txt` already named, so an asset
  dropped into `vendor/` with no pin was invisible to it.

### Changed

- `GET /api/config` gained `precedence` and `startup`, additively. TLS is reported as one boolean
  and the removed shared token is not reported at all.
- The 400-line module guard now applies to JavaScript. Largest module: 388 lines.
- `test_ui_invariants.py` grew from 33 assertions to 65 — the draft's rule was that a selector
  rename may not reduce the count.

### Unchanged

`engine.py`, the correlation path, the capture path, the ingest path, every migration, the CSP and
the security headers, `rbac.PERMISSIONS`, `rbac.ROUTE_PERMISSIONS`, the audit catalog, the seal's
query count (0), and the five runtime dependencies.

### Honest limits

**The visual layer is outside the instrument, and so is the graph.** The harness has no layout, no
cascade and no accessibility tree; d3 is a recording double, so `app/views/graph.js` is executed by
no assertion at all. No screen-reader testing was performed. These are stated in
[`docs/security/SECURITY-REVIEW-0.13.0.md`](docs/security/SECURITY-REVIEW-0.13.0.md) §5 rather than
implied away by a green suite.

## [0.12.0] - 2026-08-15 — "the instrument and the shape"

**Before rewriting fifty-two kilobytes that no test executes, build the thing that would notice —
and decide the shape of what replaces it. This release changes no pixel of the UI.**

`src/netcorenoc/ui/app.js` is 52 738 bytes and 55 top-level functions. Until now **no test executed
it**, and that was demonstrated rather than assumed: with the file made unparseable by any
JavaScript engine, the full suite reported **1302 passed**
([`docs/gates/v0.12.0-phase-0.md`](docs/gates/v0.12.0-phase-0.md) §2.3).

**18 DOM tests now execute it.** The number was zero.

### The headline

```
make dom  ->  18 passed          (executed, never a collected count)
make qa   ->  1339 passed, coverage 96.13 %
make eval ->  byte-identical: c2e8a0ce…8b9b6f26
ui/       ->  byte-identical, all four files, by SHA-256
src/      ->  unchanged since v0.11.0, not one byte
```

### The five invariants now under guard

Each captured against the current UI so the replacement has something to honour; each with a
control; each demonstrated **red** under an injected defect
([`docs/gates/v0.12.0-guard-demonstrations.md`](docs/gates/v0.12.0-guard-demonstrations.md)).

1. **A role never renders a panel requiring a capability it lacks.** Viewer and editor get three
   panels; admin gets ten. The seven admin panels are *absent* from a viewer's document.
2. **A partial split sends exactly the ids the operator marked, and no others** — the contract the
   whole v0.9.1 → v0.9.2 evidence chain rests on.
3. **A server-sent update mid-gesture does not destroy the click target.** *The v0.7.5 defect,
   machine-checked for the first time.* `FEEDBACK-PATH-0.7.5-DRAFT.md` §5 asked for exactly this;
   DECISIONS #99 recorded that there was no DOM to drive.
4. **No render path writes unescaped data into the document** (F1), asserted by counting *elements*
   rather than serialised text.
5. **A capability the client lacks produces no request** — not a request the server refuses.

### Added

- **`tests/domharness/`** — a DOM harness that evaluates `ui/app.js` under `node:vm`. Stdlib-only
  Node: **no npm, no `package.json`, no `node_modules`, no lockfile, no network, no install step.**
  Not jsdom, and ADR #167 is why: both jsdom and Playwright need `npm install`, and introducing a
  package manifest to build the guard against package manifests is incoherent.
- **`tests/test_ui_invariants.py`** — the five invariants, with fixtures **captured from the real
  server** at the exact URLs `app.js` requests.
- **`tests/test_build_step.py`** — **principle 6 finally has a test.** Before this, a tracked
  `package.json`, three lockfiles, a `vite.config.js` and a tracked `node_modules/` passed all 1302
  tests. The file list comes from `git ls-files`, never a directory walk with a skip-list — that is
  F51 in the mirror.
- **`make dom`** — reports DOM tests **executed**. On a machine without Node it prints `18 skipped`,
  and that difference is the point.
- **[`docs/architecture/UI-0.13-DRAFT.md`](docs/architecture/UI-0.13-DRAFT.md)** — the release's most
  durable output. Sidebar navigation with room for Phase 2 and Phase 3 **and no empty placeholders
  for them**; per-role dashboards; the enumeration of every capability with no UI surface; the three
  parameter classes; the env-then-database precedent `runtime.py` already proves; the framework
  recommendation; twelve things v0.13.0 must not do.

### Changed

- **The `split("const TABS")` guard is replaced by rendering.** The panel-to-capability mapping is
  now *discovered by execution* and cross-checked against `rbac.ROUTE_PERMISSIONS`. The text-level
  guard is **kept** (ADR #168): it is the only one of the pair still watching on a machine without
  Node, and its docstring now says so.
- **The chain is resequenced** (ADR #170). Archetypes move to **v0.15.0**, the external cartridge to
  **v0.14.0**; both drafts are retagged in place, not deleted. A corpus that cannot decide one
  comparison cannot decide `k` of them.
- **`tests/test_security_ui.py`'s v0.7.5 comment block is corrected in place.** It said *"there is no
  JavaScript runtime anywhere in this repository"*. That is no longer true, and the correction says
  what changed and what did not.

### Found and deliberately not fixed — **F53**

**The panel loaders have no capability check of their own.** A direct `renderPanel("audit")` as a
viewer issues no request — but only because `prunePanels` removed the container and `clear(null)`
throws. Not exploitable today (no path reaches a loader) and the server enforces regardless.
**v0.13.0 introduces routing, which is exactly when an accidental defence stops being available.**
Recorded, constrained in the draft, and asserted with its mechanism named so the note cannot rot.

### Unchanged

**Zero** migrations, **zero** new routes, **zero** new capabilities, **zero** new audit actions,
**zero** new runtime dependencies (five since v0.2.0), **zero** intentional behaviour changes. An
operator upgrading has nothing to do.

## [0.11.0] - 2026-08-14 — "champion/challenger"

**Promotion becomes possible, auditable, and refusable — and on this corpus it refuses.**

The slow loop can now propose a scorer swap, an admin can approve it, and the evidence survives the
swap. Against the real corpus the gate returns **`INSUFFICIENT_EVIDENCE`**, the sealed holdout is
**not read**, and its query count remains **0**.
[`docs/analysis/PREREGISTRATION-0.11.0.md`](docs/analysis/PREREGISTRATION-0.11.0.md) §6.1 predicted
that outcome **before any of this release's code existed**, and the census in
[`docs/gates/v0.11.0-phase-1.md`](docs/gates/v0.11.0-phase-1.md) predicted it again from real data
before the gate was built. **That is the expected result and it is a successful release.**

### The verdict, on real data

```
verdict          : INSUFFICIENT_EVIDENCE
triggers         : FLOOR_UNMET, HOLDOUT_UNSPENT, THIN_SPLIT, POWER
seal query count : 0
promotions applied / refused : 0 / 1
```

`asserting_bags` is **0** against a registered floor of **50**. The detection threshold at
n = 37 incidents is **0.2384**, reproducing ADR #154's corrected closed form. The projection is
`undefined`, because this repository has measured that it has no labelling-rate data at all.

### Added

- **Migration `0013`** — one, additive, forward-only, seeding no rows. `model_version` (the
  artefact), `promotion` (the event, **refusals included**), `evaluation_fold` (the assignment a
  citation points at), a rebuilt `scorer_active` with a `CHECK` admitting **exactly one** pointer,
  and three hash-chain columns on `holdout_access`.
- **`model_version.py`** — canonical JSON parameter documents with a **per-kind validator**. The
  logistic kind's five degeneracy rules were registered in the plan §5 **before any fit existed**.
- **Dispatch by kind in `scorer_lifecycle`**, which it had never had.
- **`promotion.py`** — the gate. Floors, then the power condition, then the seal, in that registered
  order; two refusals by **different code paths**, each of which raises if handed the other's
  verdict; and a third for an artefact whose coefficients cannot be traced to a run.
- **`evaluation_folds.py`** — fold materialisation, so a promotion cites stored rows.
- **`POST` / `GET /api/promotion`** and `netcorenoc promotion list|register`.
- **Four audit actions**: `promotion.applied`, `promotion.refused`, `seal.construct`, `seal.spend`.
- **`make census`** — the corpus census as a command, byte-identical across two processes.

### Fixed

- **F52 — the asserting-bag predicate was unguarded.** Widening
  `excluded_reconciled >= 1` to `>= 0` in `asserting_bag_rows` left **all 1 296 tests green**, while
  making bags that assert nothing count toward `asserting_bags` — the **primary** registered floor.
  Found by re-running F48's injection in Phase 6, and closed in the same release.

### Not in this release, deliberately

- **No UI change** — not a button, not a field, not a string (ADR #163).
- **No automatic promotion**, and **no `auto_promote` flag, not even defaulted off**.
- **No composite quality score.** Four named quantities, never composed.
- **No plugin surface**: no adapter column, no registry, no entry point. ONNX is v0.13.0.
- **No re-cutting of the seal**, and no change to its construction rule.
- The v0.9.2 reconciliation-drift audit gap stays open; nothing here makes it newly reachable.

### Unchanged

`make eval` is byte-identical (`c2e8a0ce…8b9b6f26`), `scoring.py` is byte-identical, `engine.py` is
still exactly 569 lines, and there are still **five** runtime dependencies. An appliance with no
`model_version` row behaves exactly as v0.10.1 did.

## [0.10.1] - 2026-08-13 — "the corrections v0.10.0 earned"

**A guard that was not merely untested but wrong, a conclusion about a statistic that ran backwards,
and a reported number that did not reproduce — all three fixed without moving a single line of the
plan those numbers were measured against.**

A patch release inside the v0.10.0 line. **No new capability, no migration, no schema change, no new
route, no new dependency, and one declared behaviour change.** `make eval` is byte-identical, the
database schema is byte-identical, and the full HTTP surface answers exactly as v0.10.0 did across
39 measured probes.

### The three corrections

- **F50 — `incidents.resolve` took the minimum over the walk, not over the cycle.** Its docstring
  states that a cycle resolves to *"the minimum id in the cycle"* and argues that this is what stops
  two bags entering the same cycle at different points from being assigned different incidents. The
  code did `min(seen)`, and `seen` is the **whole walk including the tail**. `{1: 7, 7: 8, 8: 7}`
  reported **two** incidents where there is one — the exact inflation the transitive resolution
  exists to remove, produced by the line that claimed to prevent it. The minimum is now over the
  cycle members. Unreachable through the write path today (`engine.py` merges into `min(sids)`, so
  `merged_into` always points downward) and **wrong in the guard that exists for when that stops
  holding**. Four tests, two of them controls; three injections recorded red.
- **The minimum detectable difference was pessimistic, and DECISIONS #142's conclusion ran
  backwards.** The closed form gave **both** arms the base rate's variance `p(1−p)`; the second arm
  sits at `p + delta` with a far smaller one — at `n = 37`, 0.210 against **0.058** — so the form
  demanded a **larger** delta than reality, increasingly so as `n` shrank. Replaced with the
  variance-correct fixed point (no dependency added). The ratified §3.1 table now reproduces:
  0.238 / 0.149 / 0.099 against a registered 0.25 / 0.16 / 0.10 and an independent Monte-Carlo's
  0.240 / 0.150 / 0.099. **The plan was never optimistic; the formula was pessimistic.**
  DECISIONS **#154** supersedes #142; #142, the ratified pre-registration and both its hash guards
  are untouched.
- **The coverage figure did not reproduce, and the cause was arithmetic.** Every count reproduced
  exactly and the percentage did not, because **96.20 % was never a coverage.py output** — it is the
  four printed columns computed by hand, and `BrPart` (partially-covered *statements*) is not
  `missing_branches` (missing *arcs*). coverage.py's own figure for the v0.10.0 tree is **95.95 %**.

### Added

- **`make coverage`** — the one true coverage command, with the rule that matters in a comment
  above it: quote the tool's own `Total coverage:` line and never recompute it from the columns.
- **`tests/test_shadow_cv_power.py`** — the detection threshold pinned against a Monte-Carlo that
  shares **no arithmetic** with the closed form: exact inverse-CDF binomial draws, a pooled z-test,
  a fixed absolute grid, and its own generator. Tolerance derived from three named terms.
  **The next disagreement is detected, not argued.**
- **A structural guard forbidding the one-hop expression.** No module may compute incident identity
  in SQL at all, so a fifth consumer cannot be written rather than having to be found. `ast`, not
  `grep` — six modules name the expression in prose — with its **own vacuity check**, because a
  broken extractor reports every module clean.
- **`src/netcorenoc/agreement_bags.py`** — what a labelled bag *is* and how one is read, split from
  `agreement.py` when routing identity through `netcorenoc.incidents` took it to 402 of a 400-line
  ceiling. **Split, never exempted**: `DEBT_ALLOWLIST` is still empty and no ceiling moved.
- **Three architecture documents that specify and implement nothing** —
  `DATA-LINEAGE.md`, `OBSERVABILITY-DRAFT.md`, `STORAGE-PORTABILITY-DRAFT.md`. They exist so
  v0.11.0, the UI work and the storage work can be scoped rather than discovered.

### Fixed

- **The last two consumers of incident identity** — `agreement.py` and `bias.py` — now resolve
  through `incidents.resolve_all` like `store/shadow.py` and `census.py`. Four consumers, one
  implementation. Both frozen reports are byte-identical: on this corpus every merge chain is one
  hop, so the two readings coincide — **which is the hazard rather than the reassurance**.
- **F49, both repairs.** The pinned backfill expression is tied to `0011_evidence_boundary.sql` by
  normalised substring, and `test_upgrade.py`'s v0.9.1 fixture now writes the client's reported bag
  containing the ghost id, so the `source = 'server'` predicate has something to exclude. Deleting
  the predicate from the migration fails **three** tests, against v0.10.0's measured 1093 passing.
- **`_cycle_members` is bounded.** The mutation ledger seeded a one-token mistake in F50's own repair
  and the run **hung** rather than failing — the walk's stopping condition was an invariant. Bounded
  by `MAX_CHAIN_DEPTH`, returning what it collected: a wrong number is visible where a hang is a
  support ticket. DECISIONS #158.
- **`census.first_label_per_incident`'s discarded computation** (`_ = identity`) removed.

### Changed

- **One reported quantity, declared and counted.** The shadow report's printed
  `minimum detectable difference` moves **0.182 → 0.161** at its fixture's `n = 100`. The verdict,
  its four trigger lines, every floor and every other number in the report are byte-identical.
  **This is the release's only behaviour change.**

### Known limitations

- **The coverage figure is not reproducible**, and pinning the command was necessary but not
  sufficient: two runs of `make coverage` on the same tree give **96.21 %** and **96.10 %**.
  `receiver.py` carries the entire 0.11-point band, because `tests/test_receiver.py` fuzzes the BER
  decoder with `@given(st.binary(…))` and Hypothesis generates different examples each run.
  **Deliberately not derandomised** — that trades a real fuzzer for a tidy number (DECISIONS #159).
  Every coverage figure in this project's history is one sample from a distribution, and a release
  gate of the form *"coverage at or above X"* is not well-defined on this suite.
- **Neither byte-frozen corpus contains a single merge edge**, so the strongest gate this project
  has **cannot discriminate** a one-hop reading from a transitive one. That is measured, not
  assumed: restoring either `COALESCE` leaves both reports byte-identical and only the structural
  guard goes red.

## [0.10.0] - 2026-08-12 — "the honest judge"

**An evaluation whose verdict cannot be produced by the thing being evaluated, and a holdout that is
built and deliberately not spent.**

This release does **not** produce a better model. It produces the machinery that could one day tell
whether a model is better — and the verdict that machinery returns on this corpus is
`INSUFFICIENT_EVIDENCE`. **That is the pre-registered expected outcome and it is a successful
release.** `docs/analysis/PREREGISTRATION-0.10.0.md` §7.1 says so in advance, before any result
existed, which is the entire point of having written it first.

### The headline, in the order it should be read

```
verdict                                INSUFFICIENT_EVIDENCE
holdout queries                                            0
sealed incidents                          12 of a corpus of 37
asserting bags                                    0  (floor 50)
asserting incidents                               0  (floor 30)
minimum detectable difference at n=37                  0.298
```

**The corpus cannot support an evaluation. The judge exists and is demonstrated on fixtures. The
seal is intact at query count 0.** What would have to change is stated in
`docs/releases/BUILD-REPORT-0.10.0.md`.

### Added

- **Incident identity that follows the merge chain.** `netcorenoc/incidents.py` resolves
  `situation.merged_into` transitively to a fixed point, with a **cycle guard** and a **depth
  guard** reported separately and never collapsed. One implementation; the SQL no longer computes
  identity at all. A cycle resolves to `min(cycle)`, so two bags entering it at different points get
  the same incident rather than silently inflating the count.
- **`netcorenoc/census.py`** — what the labelled corpus contains, including the one-hop count the
  merge-aware count replaces, the reduction between them (**0** on this corpus), unsound chains, and
  pre-v0.8.0 merges **counted rather than assumed absent**.
- **The sealed holdout** — migration `0012`, `netcorenoc/seal.py`, `store/seal.py`. Constructed
  once (a `UNIQUE` constant column, so a second construction fails at SQLite); an explicit ordered
  immutable list rather than a predicate; append-only access log recording refusals as well as
  reads; a read requires a ratified plan hash already on record.
- **`netcorenoc/shadow_cv.py`** — grouped repeated cross-validation over merge-aware incidents, a
  cluster bootstrap over incidents, and the closed-form power condition.
- **`netcorenoc/shadow_assertions.py`** — `asserted_negative_respected_rate`, the **fourth** named
  quantity: per bag, aggregated as the mean over bags, **never pooled over pairs**, excluding
  `coverage IN ('none','empty')` bags, and computed for the champion by the same code path.
- **`netcorenoc/judge.py`** — a three-valued verdict. `BETTER`, `NOT_BETTER`,
  `INSUFFICIENT_EVIDENCE`, with every §6.2 trigger a named enum member and each individually fired
  by a fixture that fires it and no other.
- The shadow report gains a **verdict** section, a **sealed holdout** section carrying the query
  count, and four census lines.

### Changed

- `shadow_eval.py` **split twice** rather than exempted: `shadow_admission.py` (may this model
  compete) and `shadow_assertions.py` (the fourth metric). `shadow.py` and `training.py` likewise
  split into `census.py`. No module is over 400 lines; `DEBT_ALLOWLIST` is still empty.
- `store/shadow.py`'s two training joins return `situation_id` and the merge **edges**; the
  `COALESCE` that computed one-hop identity is gone.

### Fixed

- **A bag with no partition scored 1.0** on the new metric — every asserted negative pair
  "respected" — because an empty component mapping makes every pair look separated. Found by the
  mutation ledger (M3), not by review.
- **The bootstrap's PRNG used an LCG's low bits**, so `% 2` alternated with period 2 and a
  two-cluster corpus produced a **zero-width interval**. Found by a test (A11).

### Security

- **F48** (issued in Gate 0, against the v0.9.2 tree): the `source = 'server'` reconciliation
  predicate was adversarially tested in one of the three places it appears. No production change —
  a missing demonstration, not a missing fix (DECISIONS #140).
- **F49**: nothing ties a migration's pinned SQL constant to the migration file's own text, and
  `test_upgrade.py` does not catch the drift. Measured, open, and a ROADMAP line.

### Not in this release, deliberately

**No promotion mechanism, no pointer move, no new route, no new capability, no UI change.** The
champion decides everything in production, exactly as before. The seal is **constructed and not
spent**: reserving later is impossible, spending later is always possible, and adaptive selection
over 12 queries on 37 incidents inflates a reported rate by a median **+11.1 points** when every
candidate is equally good.

### Migration

`0012` is additive and forward-only, applies to a populated v0.9.2 database with every row intact
and the audit chain verifying to the same final hash, and **seeds nothing**. See `MIGRATION.md`.

## [0.9.2] - 2026-08-10 — "the evidence boundary"

**A number that describes the evidence is derived by the server; a number that describes the client
may be derived from the client; and the place where the two meet is a named, stored, auditable act
rather than an arithmetic accident.**

A **corrective** release. No new capability, no new route, no new model, no product movement. It
exists because the quantity v0.10.0 is most likely to promote into a pre-registered sufficiency
floor — the count of asserted negative pairs — was being computed from unvalidated client input,
and a floor computed from something the subject controls is not a floor.

### Why

`labels.py` recorded `excluded_count` as the raw length of a client-supplied list, never intersected
with the server's own bag, and three reports multiplied it. Measured over HTTP as an ordinary
`editor`, against an honest corpus:

```
8 honest labels (9-member bags, 2 marked each)      total =     112
+ ONE label: bag of 4, 600 ids sent (truncated 512) total = -259,984   delta = -260,096
+ ONE label: bag of 60, 30 GHOST ids marked         total = -259,084   delta =    +900
```

The third line is the release. `-260,096` is loud and any `>= N` floor fails on it. `+900` is
positive, plausible, and composed of **zero true assertions** — the same label read through the path
`learn.penalize` actually uses resolves 0 marks and moves not one matrix cell.

### Security

- **F46 — the asserted-negative count is the client's list length.** High for evidence integrity;
  nil for confidentiality and availability; not a privilege escalation. Repaired by
  `feedback.excluded_reconciled`, computed server-side at the verdict.
- **F47 — the assertion does not record whether it could have been made.** A scoped editor may mark
  members they were never shown, and nothing recorded that. Repaired by
  `feedback.excluded_reconciled_out_of_scope`. **It makes the assertion legible; it does not prevent
  it** — preventing it would mean rejecting client ids and reintroducing the existence oracle F34
  closed.

Both are reproduced with controls in `docs/gates/v0.9.2-phase-0.md`; the full analysis is
`docs/security/SECURITY-REVIEW-0.9.2.md`.

### Added

- **Migration `0011`** — three nullable columns on `feedback`: `excluded_reconciled` (the reported
  marks intersected with the server's own bag, distinct by alarm id), `excluded_reconciled_source`
  (`live` or `backfill`), and `excluded_reconciled_out_of_scope` (how many reconciled marks were
  about members the labeller could not observe; `NULL` means unknown, for ever). Two enforced
  `CHECK` constraints.
- **`docs/architecture/EVIDENCE-BOUNDARY-0.9.2.md`** — the three-tier model, every client-controlled
  write-path input classified against it, and what each consumer is entitled to read.
- **A maintenance drift check** that recomputes the reconciled count from the child tables and
  **reports** disagreement as an operator warning. It never corrects: a disagreement is evidence
  that a write path is broken, and repairing the row would destroy it.
- **`docs/gates/v0.9.2-guard-demonstrations.md`** — every guard this release ships, with the exact
  defect injected as a diff, the verbatim red, the verbatim green, and a named control that passed
  under the injection. Thirteen mandatory injections, all caught; a fifteen-mutant ledger whose
  first run caught nine, with all six survivors named and repaired.

### Changed

Four intentional behaviour changes, and any change not on this list is a defect.

1. `make bias-report` leads with the **disagreement between the reported and reconciled counts**,
   then prints both, then the three scope populations (**clean / checked / unknown**) separately and
   never averaged.
2. `make shadow-report` labels its asserted-negative line `(server)` and prints the client's figure
   beside it as `(client)`.
3. `make dataset-stats` splits `feedback_member` into `.server` and `.client`; the total is still
   printed.
4. `make migrate` derives `excluded_reconciled` on labels that already carry a marking, marked as
   `backfill`. Nothing from the scope column is seeded.

**On any corpus written by the shipped UI, no number moves.** The reports say more about the same
figures.

### Unchanged, and asserted by test

`make eval` is byte-identical (`c2e8a0ce…b9b6f26`, since v0.7.0). `learn.penalize` is byte-identical.
Every response is unchanged in status, body **and timing**. No route, capability, audit action,
served path or runtime dependency is added. `engine.py` is untouched at 569 lines.

### Internal

- `shadow_report.py` reached its 400-line guard and was split into `shadow_report.py` (the reader)
  and `shadow_render.py` (the renderer); `bias.py` was split into `bias.py` (what capture cost) and
  `bias_labels.py` (what an operator said). **Neither guard was raised** (DECISIONS #139).
- `Perimeter.redacted_member_count` became `Perimeter.hidden_member_ids`, returning the set rather
  than its size, so both scope facts on a label come from **one** read (DECISIONS #137).
- The perimeter's fail-open branch has a test for the first time, and
  `tests/test_structure.py`'s virtualenv exclusion is now `pyvenv.cfg`-aware rather than a literal
  name.
- ADRs #131–#139.

## [0.9.1] - 2026-08-08 — "the partial split"

**The operator can say *which* members do not belong — and the project audits whether its own tests
would notice if they were wrong.**

A patch-shaped release in the v0.7.5 mould: a small, auditable diff that improves the evidence the
product gathers, and turns the same scepticism on the test suite that guards everything else. It is
the second release inserted for label integrity, and the first one bought the whole v0.8.0 dataset.

### Why

Two releases in a row reported insufficiency. The binding constraint on v0.10.0 through v0.13.0 is
not a missing evaluator — it is a missing **label**. A `split` verdict asserted *"these members are
at least two situations"* **without saying which**, so it supported no pairwise claim at all: the
minority class, the only source of negative evidence in the entire system, was also the least
informative label the product knew how to collect.

Gate 0 §1 demonstrates it by query and by arithmetic. The complete recorded evidence of a `split` was
a verdict and an ordered member list, with no column, table or row anywhere in the schema asserting
one member separate from another — while `learn.penalize()` responded by halving **every** matrix
cell the bag spanned: on a nine-member bag, all 36 member pairs driving 72 cells, from an assertion
that named none of them.

### Added

* **The partial split.** `POST /api/situations/{sid}/feedback` accepts an optional `excluded_ids` —
  the members the operator marked as not belonging — and an optional `remainder_together`. The
  assertion is recorded **exactly as made**: `marked` × `rest` is asserted negative and **nothing
  else**, with the pairs inside the remainder and inside the marked set left **unknown**. On a
  nine-member bag with two marked, one gesture now yields **fourteen asserted negative pairs** where
  it previously yielded none.
* **The close channel.** `POST /api/situations/{sid}/close` accepts an optional verdict, recording
  the same label the feedback endpoint would — same bag, same fingerprint discipline, same scope
  fingerprint — with `acquisition_channel = 'close'` rather than `'organic'`, because closing selects
  for *resolved* incidents and that is a different population.
* **Informativeness in all three reports.** Asserted negative pairs, the plain-versus-partial split
  breakdown, `|marked|` against bag size, the remainder-assertion rate, closes without a verdict, a
  per-channel section in the bias report, every existing conditioning repeated per channel in the
  agreement report, and `split ∧ mixed` and asserted negatives as observations beside the shadow
  report's floors.
* **A seeded-defect audit of the test suite**, `docs/gates/v0.9.1-test-audit.md`.
* **Migration `0010`** — `feedback_exclusion`, and `excluded_count`, `excluded_truncated` and
  `remainder_together` on `feedback`. Additive; seeds nothing; **backfills nothing**.

### Changed

* **`learn.penalize()` uses the assertion when there is one.** Given an exclusion it halves only the
  asserted `marked` × `rest` pairs and leaves the unasserted remainder alone. **Without one it is
  v0.9.0 byte for byte**, proved against a restatement of the v0.9.0 body.
* The label row's children moved from `store/dataset.py` to `store/feedback.py`, where the label row
  already lives (DECISIONS #128) — a pure move, no body edited.
* `engine.apply_feedback` takes one `LabelContext` in place of `scope=` and `client=`
  (DECISIONS #129), which is what kept `engine.py` inside its 580-line cohesion ceiling.

### Not changed, deliberately

* **`make eval` is byte-identical**: `c2e8a0ced29d9edf986279d41089ddb68e18da65a46bdc7e9f04811e8b9b6f26`.
* **Closing without judging** is exactly as easy as it was: no body, `{}` and `{"verdict": null}` all
  behave as at v0.9.0 and write no label.
* **No new capability, audit action, route, runtime dependency or served path.** Five runtime
  dependencies, unchanged for nine releases.
* **The UI stays four files**, with no panel, modal or restyling. The exclusion gesture is a checkbox
  cell in the member table already on screen; the v0.7.5 held-card tests pass unedited.
* **The v0.9.0 pre-registration is untouched**, hash `bb5bff85…2cbaef`, and no floor was moved.

### What this release does not claim

* **It raises informativeness, not rate.** The close gesture is deferred (DECISIONS #130), so the
  shipped UI writes `organic` on every row: the channel has its mechanism and none of its volume.
* **The exclusion set cannot be demonstrated on the corpus.** Eleven of the thirteen `split` bags in
  the fullest corpus this repository can construct have fewer than two members; the other two are
  storms of 240 and 501. The semantics are proved on a purpose-built fixture, and no projection of
  the gain is supportable from the data available.
* **The prize is unmeasurable here.** The corpus labels every situation by construction and none of
  its closes came from an operator, so closes-without-a-verdict is counted from the day this release
  ships rather than estimated now.

## [0.9.0] - 2026-08-03 — "shadow mode"

**A challenger runs beside the champion and writes its opinion where nobody acts on it. The
built-in scorer decides everything.**

The most valuable output of this release is not a model. It is two numbers — **how well the
champion already agrees with the operators**, and **whether there is enough signal in the data to
learn anything at all** — and the second one came back *no*, which is the release succeeding rather
than failing.

`make eval` is byte-identical (`c2e8a0ce…`), rows captured per trap are unchanged at **82.830601**
on the same four scenarios, **five** runtime dependencies, **one** migration, **no route**, **no
capability**, and no promotion mechanism anywhere.

### Added

- **The champion-agreement report** — `python -m netcorenoc dataset agreement`
  (`make agreement-report`). How well the built-in scorer already agrees with the operators, at bag
  level, conditioned six ways: by mixed-versus-uniform bag, bag size, storm, visibility scope,
  operator and capture provenance. **It needs no model**, which is why it shipped first and why a
  test parses its imports to keep it that way.

  The headline is cheap; the conditioning is the deliverable. **A uniform bag contained no
  decision** — every pair fell on the same side of the threshold, so confirming it says nothing
  about the scorer's judgement. On the fullest corpus this repository can build, **five of
  forty-one bags are mixed**, and that proportion is structural rather than a property of who did
  the labelling. Intervals are a cluster bootstrap over **incidents**, refused below ten of them
  rather than printed narrow. Operators are anonymised, structurally (DECISIONS #120).

- **The shadow-mode report** — `python -m netcorenoc dataset shadow` (`make shadow-report`). Leads
  with the **sufficiency verdict**, then both label-derivation policies, partition-level over-merge
  and under-merge against the human verdicts, bag-level calibration, the admission filter run
  against the champion too, and the training/serving skew rate. It re-derives offline and **fits
  nothing**.

- **The challenger** — a deterministic, dependency-free logistic `LinkScorer` satisfying the v0.6.0
  Protocol **structurally**: no base class, no registry, and `scoring.py` gains nothing. Its
  `score()` returns the **pre-link** score so the terms sum to it exactly, the same property
  `AdditiveScorer` has; the probability is a separate method. Per-term explainability and
  `SafeScorer`'s fail-safe are inherited by contract rather than promised (DECISIONS #116).

- **Training in a slow loop this release had to build.** Phase 0 proved by parsing the code that
  **no point inside `maintenance()` runs outside `store.lock`** — one `async with` block, zero
  statements after it. So training runs in `maintenance_loop` *after* `maintenance()` returns and
  releases the lock; the lock is taken only to read rows and to write the result, and **the fit
  holds nothing**. It yields to the event loop between iterations, because a fit that never yielded
  would stall ingestion by an indirect route (DECISIONS #118, #121).

- **Both label-derivation policies, A and B**, fitted and reported — never one with its number.
- **Migration `0009`**: `challenger_run` and `shadow_opinion`. Seeds no rows; a run row is written
  **even when nothing is fitted**, because insufficiency is a result and a schema that could only
  record a successful fit would make it invisible.
- **The pre-registered analysis plan**, `docs/analysis/PREREGISTRATION-0.9.0.md`, written before any
  model existed, stating what will be concluded under **ten** outcomes including insufficiency —
  and hash-guarded, so editing it after seeing a result turns the suite red.

### Findings

- **The corpus is insufficient, and the release says when.** Against pre-registered floors: **13
  `split` bags of 50 required**, **5 mixed bags of 20**, and **exactly one bag that is both `split`
  and mixed**. Where the floors are unmet the release fits nothing and publishes a projection in
  months at the measured labelling rate — or `undefined` where no rate exists, because an
  extrapolation from a single instant is a fabricated number.

- **Policy B derives only one class.** The shadow-mode draft called it *"throws away the minority
  class"*; measured, on bag-level labels it throws away **the only source of negatives**, so the
  target is constant and the best achievable model predicts "link" unconditionally. On the fixture
  it scores 0.0000 on both headline rates **and buries all sixty split bags** — which is why
  `split_bag_intact_rate` is reported as a separate, named third quantity.

- **Training/serving skew: 0.0000 %** over 2 000 sampled opinions from four corpus scenarios,
  compared with `==` on the float rather than a tolerance. Both shadow mechanisms ship because
  offline reconstruction **cannot** measure skew by construction, and their divergence is the test
  (DECISIONS #119).

- **A full-rate sample is silently truncated — found by measuring, fixed here.** At
  `sample_rate = 1.0` the in-memory buffer discards 43 474 of 45 474 opinions. The bound is correct;
  what was missing is that nothing told the operator, who would read a truncated prefix as a census.
  It now raises an operator warning. No F-number: the F-series tracks defects in *shipped* code.

### Changed

- `over_merge_rate` and `under_merge_rate` moved into `netcorenoc.shadow_eval`; `eval/metrics.py`
  imports them back (DECISIONS #122). They became a runtime metric and `src/` never imports `eval/`;
  two copies of one metric agree until one is tuned. `make eval` byte-identical across the move.
- `maintenance_loop` moved from `engine.py` to `maintenance.py` (DECISIONS #121). `engine.py` stays
  at exactly its 580-line cohesion ceiling.
- `docs/analysis/` is a new documentation area for **pre-registered analysis plans** — immutable
  once written, hash-guarded, and disagreed with in the *next* release's review rather than by
  editing.

### Unchanged, and verified so

`correlate.py`, `capture.py`, `receiver.py`, `learn.py`, `rbac/`, `shaping/`, `scoring.py` and every
existing migration: **zero-byte diff**. Routes, capabilities, audit actions, served paths and
runtime dependencies: unchanged. F34–F44 regression tests: unedited. Coverage **96 %**, equal to
v0.8.1, after 2 836 new source lines. Tests **855 → 923**.

## [0.8.1] - 2026-08-02 — "the dataset has a governed lifecycle"

**v0.8.0 designed a lifecycle for the rows it created and did not check the lifecycle the
repository already had.** In a default deployment the consequence was that the release's own
deliverable evaporated: the human verdict — the least reconstructible asset in the system — was
deleted seven days after its situation closed, by a maintenance loop that predates the feature.

A patch release in the v0.7.1 mould: **no schema change, no migration, no new route, no new
dependency, no new capability.** `make eval` is byte-identical (`c2e8a0ce…`), the correlation and
capture write paths are untouched, and rows captured per trap are unchanged on every fixture.

### Fixed

- **F44 — the operational prune deleted human labels.** `store/retention.py::prune()` deleted
  `feedback` for every situation closed or merged longer than `NETCORENOC_RETENTION_DAYS`
  (**default 7.0**), taking `feedback_member` with it by `ON DELETE CASCADE`, while the
  `dataset_pair` features it justified survived — `dataset_pair` deliberately carries no foreign key
  to `alarm` or `situation`. Silent and asymmetric: the corpus grew, the labels evaporated, and the
  bias report's label count only ever reflected the last seven days. Before v0.8.0 the line was
  correct — feedback was a transient learning signal, applied at click time and then disposable.
  v0.8.0 made that row the dataset's **label** and did not revisit it.

  A label is not operational data and is no longer governed by the operational retention. The
  labelled `situation` **row** is retained with it — and only that row — because
  `feedback.situation_id` is a restricting foreign key under `PRAGMA foreign_keys=ON`, so the
  dangling reference the obvious fix assumes is one SQLite refuses; without the retention the sweep
  would raise on every pass. Its `situation_alarm` and `link` rows are still collected on the
  operational schedule. DECISIONS #109.

- **The retention policy did not survive a restart.** An admin set a policy, the route answered
  `"saved"`, the change was audited as `retention.change` — and the next restart silently returned
  the shipped defaults. The asymmetry was the serious part: *the destruction an admin asked for was
  permanent and the configuration they asked for was not.* Now persisted as one `meta` value
  (`config.dataset_retention`), written in the same transaction as the audit row and the deletion,
  and read at the documented configuration reload point. **No migration** — `meta` is where this
  product has always kept operator configuration. DECISIONS #111.

- **The coverage rate could exceed 100%.** `bias.py` divided labelled situations by
  `COUNT(*) FROM situation`, a table the operational prune collects while labels outlive it;
  measured at **300.0%**. The denominator is now the population the database has evidence of —
  surviving situations plus situations named by a surviving label — so the numerator is a subset by
  construction and no clamp is needed. The report names the population. DECISIONS #112.

### Changed

- **The three retention tiers have meanings that are true.** v0.8.0 defined three and enforced one:
  `training_days` was only the cutoff of an explicit admin reduction, and `audit_days` was
  validated, recorded and reported but read by **no deletion path at all**.

  | Tier | Meaning | Mechanism |
  |---|---|---|
  | sink | pairs awaiting a verdict | **deletes**, under its dual bound — unchanged |
  | training | what a model may *read* | **selects** — a `WHERE` clause. Nothing is deleted here. |
  | audit | the outer bound of the data's life | **deletes** — the only background path that may reach a label |

  A training-retention *delete* destroys evidence in order to express a modelling preference;
  wanting to train on the last twelve months is a statement about **selection**, and keeping the
  rows keeps the choice revisable for the four releases that will disagree about how to use them.
  v0.8.0's directive that the loop must never *silently* destroy labels is satisfied rather than
  repealed: the audit sweep enforces a bound the operator configured, and every deletion is counted
  and reported. DECISIONS #110.

- **The explicit admin reduction now cuts on `audit_days`, not `training_days`**, and both
  responses carry `"bound": "audit"` and `"training_deletes": 0`. v0.8.0's preview reported a
  `labels` figure the apply never deleted; that discrepancy is resolved in the direction of honesty
  about which tier destroys. Additive response fields only — the API contract is otherwise
  unchanged.

- `store.prune_dataset` → **`store.prune_dataset_audit`**, and it now deletes labels as well as
  pairs and observations, in the order pairs → observations → labels so a crash mid-sweep leaves
  features without a label rather than a label whose evidence is gone. (The obvious name,
  `prune_audit`, is already the audit-**log** deleter; `mypy --strict` caught the collision, which
  would have silently shadowed audit-log retention.)

- `RetentionPolicy` moves to `netcorenoc/retention_policy.py`. `capture.py` was at 374 of its
  400-line budget and re-exports every name, so **no import site anywhere else changed**.
  DECISIONS #113.

### Added

- **The bias report counts orphaned promoted pairs** — promoted pairs whose label no longer exists,
  features nothing can interpret. Counted, **never collected**: a corpus with orphans is not
  corrupt, it is one whose *usable* size is smaller than its row count. No cleanup job.
- **The report distinguishes what the corpus holds from what a model may read**, applying the
  training window as a selection clause anchored on the newest promoted row rather than the clock.
- **`MODULE-ARCHITECTURE.md` and `repo-map.md` describe `capture.py`, `labels.py`, `bias.py` and
  `bias_report.py`** — four modules of a major feature that existed in the tree and not in the
  architecture documents `docs/` calls binding.
- **`DESIGN.md` records that the sink's row cap, not its age limit, is what governs** at any
  realistic traffic rate — previously stated only in `MIGRATION.md` and a commit message.

### Upgrading

**Labels already lost to F44 cannot be recovered**, and any retention policy set through the route
before this release was never persisted and must be set again. `MIGRATION.md` says so plainly. No
migration runs: a v0.8.0 database opens with an identical schema, identical row counts, and an audit
chain verifying to the same final hash. Pre-existing orphans are **reported, not collected**.

## [0.8.0] - 2026-08-01 — "the scoreboard"

**Capture the operator feedback as a durable dataset, and measure its bias. Trains nothing.**

Every ML release from v0.9.0 to v0.13.0 consumes what this one captures, and capture is
irreversible: `A` and `E` decay continuously, `alarm` is deduplicated and mutated on re-fire, and
situations are merged and lose their membership. A field not captured at the moment of decision is
not captured late — it is captured never.

### Added

- **The feedback dataset** (migration `0008`). Four tables: `capture_run` (the constants a period
  of capture shares), `dataset_observation` (one immutable row per activation, carrying the raw
  material `alarm` overwrites on re-fire), `dataset_pair` (one row per **evaluated** pair — linked
  and rejected alike, before `MAX_LINKS_PER_ALARM` truncation), and `feedback_member` (the
  membership record). Fifteen columns on `feedback`, one on `situation`.
- **The server-side membership record.** At the moment of a verdict the server writes the ordered
  member alarm ids from its own state. A merged situation's label previously lost its referent
  entirely — `feedback ⋈ situation_alarm` returns nothing and the surviving situation holds the
  union of both bags — and the bag is now recoverable regardless.
- **`situation.merged_into`.** The merge chain was unrecoverable: the merge marked the source
  `merged` and never recorded the destination. Lineage, and v0.10.0's split-by-incident, depend on
  it.
- **The optional client fingerprint** on `POST /api/situations/{sid}/feedback` — what the UI
  rendered, plus the situation's `updated_at` at render time. Additive, optional, bounded, never
  rejected, and never used to validate the existence of anything. The divergence between it and the
  server's own bag is a metric, not an error.
- **Three retention tiers**, admin-configurable, `sink < training ≤ audit` enforced fail-closed with
  a precise reason. `GET`/`POST /api/dataset/retention`, both admin-only.
- **Preview before destruction.** Lowering retention deletes rows and there is no rollback for a
  `DELETE`. Applying requires `preview=false` sent deliberately, after the count has been seen;
  both the preview and the change are audited (`retention.preview`, `retention.change`).
- **The bias report** — `python -m netcorenoc dataset bias`, and `make bias-report`. Deterministic,
  aggregates only, and a **gate**: `make qa` compares it byte-for-byte against a frozen fixture, so
  it goes red the day capture changes shape. Reports effective sample size as **bags, not pairs**.
- **`python -m netcorenoc dataset stats`** / `make dataset-stats` — what capture costs in rows, and
  the **observed** sink window in days, which is not the configured one (see Known limits).

### Fixed

- **F43 — the declaration gate's residual fail-open.** `assert_every_route_is_declared` refused
  unknown route *shapes* (F42) but, within a known shape, iterated `route.methods` — and an empty
  set produced zero iterations, so the route was neither checked nor refused while Starlette served
  every verb on it. An empty method set is now refused, by the same logic that refuses an unknown
  shape. Latent; no route in this repository was registered either way.

### Changed

- `Correlator.process()` returns the evaluated pairs it already computed. **`correlate.py`'s only
  change, and it is additive on the return value**: `make eval` is byte-identical, `score_link`'s
  function body hashes identically, and the `links` list is unchanged member for member.
- Pre-v0.7.5 `feedback` rows are marked `legacy_capture` and **excluded from training by default**.
  They are not known to be bad — they are of *unknown quality*, which is a weaker and different
  claim — so they are marked rather than deleted.

### Known limits

- **The sink's row cap binds long before its 21-day age limit at any realistic traffic rate.**
  Measured at 62 pair rows per trap on the storm-heavy eval corpus, the 2 000 000-row default is
  exhausted after ~3.7 days at 0.1 traps/s and ~9 hours at 1 traps/s. The cap is a **disk budget**;
  the age limit is a ceiling most deployments never reach. `dataset stats` reports the *observed*
  window so an operator is not misled by the configured one.
- **Capture costs 62 rows and 6.9 kB per trap on the eval corpus**, a 9.9× database growth. That
  corpus is 86 % storm by construction and is the worst case, not the typical one.
- The merge chain is recorded from v0.8.0 **forward**. Merges that happened before the upgrade are
  gone; no migration can reconstruct a destination that was never written.
- **Nothing trains.** No model, no fit, no train/test split, no `numpy`, no `scikit-learn`.
  Runtime dependencies: **five, unchanged**.

## [0.7.5] - 2026-07-31 — "the click means what the operator meant"

The release that makes the operator's click mean what the operator meant, and makes the two guards
that protect v0.8.0 actually guard. Runtime dependencies stay at **five** and dev dependencies at
**eleven**; **zero** new migrations (still `0001`–`0007`); **zero** new routes, capabilities, audit
actions or served paths; `make eval` is **byte-identical** to v0.7.4. **An operator upgrading from
v0.7.4 has nothing to do** — see [MIGRATION.md](MIGRATION.md).

**Exactly four intentional behaviour changes.** One is at startup — a route of a shape the
declaration gate cannot classify now refuses. Three are in the browser: an expanded situation card
is no longer destroyed by an SSE update, the detail container is never displayed empty, and a held
card carries a staleness marker. Nothing else in the appliance's behaviour moves.

> **Read this if you read nothing else.** Three of those four changes are browser behaviour, and
> **the test suite does not prove them.** There is no JavaScript runtime in this repository, by
> design, so the UI tests assert the *shape of the source* and say so in their own comments. The
> behavioural proof is [docs/gates/v0.7.5-manual-verification.md](docs/gates/v0.7.5-manual-verification.md),
> which this build **wrote and did not execute**. DECISIONS #99.

### Security — F42, and a correction to a claim v0.7.4 made

See [SECURITY-REVIEW-0.7.5.md](docs/security/SECURITY-REVIEW-0.7.5.md).

- **F42 — the declaration gate failed open on route shapes it could not classify.** v0.7.4's F40
  closed the *registration paths*; it did not close the *route shapes*.
  `assert_every_route_is_declared` failed open twice — `if path is None: continue`, and an inner
  loop over a `methods` set that is empty for every shape carrying no verbs. **Five shapes evaded
  it while serving real traffic**, all reproduced by execution with a passing control and a
  served-200 confirmation: `include_router`, `mount()` with a sub-application, `mount()` with
  `StaticFiles`, `add_api_websocket_route`, and an explicitly-registered `HEAD`-only route.
  **Behaviour change:** an unrecognised route shape now refuses instead of being skipped.
  Fixed with `declare.KNOWN_ROUTE_SHAPES` and a refusal outside it, so every object on `app.routes`
  is either checked or refused and none is skipped. Recursing into each container was rejected —
  every attribute it would need is an undocumented FastAPI internal, which rebuilds the defect one
  level down (DECISIONS #98). `HEAD` is now skipped only when `GET` is present on the same route;
  the `OPTIONS` exemption fired on nothing and is gone.
- **The gate's coverage had regressed with no commit and no failing test.** The `include_router`
  shape was **refused** on `fastapi==0.115.0`, the floor of this project's own pin, and is
  **skipped** on `0.141.1`. `pyproject.toml` carries no upper bound and CI has no lockfile, so the
  gate's completeness was a property of whatever pip resolved that morning. A new test asserts the
  route-class set a real `create_app` produces equals the known set, so a future upgrade fails
  loudly — naming the new class — on the day it happens. Its limit is recorded: it detects a new
  *shape*, not a changed *meaning*.
- **v0.7.4's completeness claim is corrected.** That release called the traversal "complete by
  construction… nothing here lists the ways a route can be registered." The second clause is true
  and the first does not follow from it: the traversal enumerated no *mechanism* but assumed a
  *shape*. `SECURITY-REVIEW-0.7.4.md` is **not edited** — it is the record of what was believed
  then; `MODULE-ARCHITECTURE.md` §10.1 carries a dated correction beneath the original paragraph.
- **Whether to also pin FastAPI is left open**, with the reasoning, as a supply-chain policy
  question about five dependencies rather than a route-gate question about one (DECISIONS #101).

Fourteen `test_f42_*` regression tests, **twelve proven to fail** on the unmodified tree. The two
that stay green are the control and the no-looser guard, and both must pass on either tree. The F40
and F41 sets, the authorization matrix, the route-map completeness tests and the 48-entry
route-order table all pass **unedited**.

### Fixed — the operator-feedback acquisition path

Three changes in `ui/app.js` and nothing else; specified in
[FEEDBACK-PATH-0.7.5-DRAFT.md](docs/architecture/FEEDBACK-PATH-0.7.5-DRAFT.md). The failure that
mattered was never the flicker: a click could land on a card rebuilt between the operator's visual
decision and their mouse-down, recording a verdict against a membership they never evaluated. That
is a **silently wrong label**, and it is worse than a missing one — a missing label is visible as
absence and can be counted, while a wrong one is indistinguishable from a considered one at every
layer downstream. The feedback click is the only source of human labels in the system and **v0.8.0
is the dataset built from it**.

- **An expanded card survives the SSE update.** `clear(sits)` was the first statement of
  `renderSituations` and destroyed every card every two seconds, expanded ones included, along with
  the feedback buttons inside them. Detail nodes of open cards are now harvested before the clear
  and re-appended, keeping their identity and their listeners. Collapsed cards are still rebuilt —
  the narrow fix the draft prefers, not a general reconciler.
- **The detail container is never displayed empty.** `renderDetail` builds into a
  `DocumentFragment` and swaps it in one synchronous step, so no reachable state has the container
  displayed with no children. This also covers the first expansion, which the change above does not.
- **A held card says it is stale.** Holding the card trades a wrong label for a stale one, and a
  stale label is only better if the operator knows. A `held while open` badge, reusing the existing
  `.badge.redacted` styling so it costs no new CSS — `style.css` and `index.html` are untouched and
  the UI is still four files.

**What this does *not* fix, said plainly:** the label is now *deliberate*, but it is still not
*traceable to what was on screen*. Recovering which membership a verdict was about is the membership
fingerprint, and that is **v0.8.0**. Nobody should read v0.7.5 as having solved label provenance.

### Fixed — the documentation-consistency guard saw 31% of what it checks

`test_documentation.py::source_of` blanked fenced code blocks **and** inline code spans, while the
element-tag pattern matches `vX.Y.Z: planned` — which `docs/README.md` specifies as a **backticked**
form and which every draft in the repository writes that way. The guard was not partially blind by
accident; it was **inverted**, catching the form nobody writes and missing the form everybody
writes, with the comment recording the convention sitting four lines above the code that defeated
it.

**15 of 48 element tags were visible (31%)**, and five of the eight tag-carrying documents were
invisible entirely — including the v0.8.0 specification and the half-finished supersession the
guard's own docstring names as its motivating example. **Now 49 of 49 outside fenced blocks (100%)**,
with the one fenced example correctly still excluded.

Demonstrated in three runs rather than asserted: injected and unfixed → green; injected and fixed →
red; injection removed → green across the whole `docs/` tree. Three tests added, each driving the
real guard function over a synthetic document. A **test defect, not a security finding** — no `F`
number. DECISIONS #100.

### Changed

- `docs/architecture/FEEDBACK-DATASET-0.8-DRAFT.md` refined in place, every element still tagged
  `v0.8.0: planned` and **nothing implemented**. All eleven cited code locations re-read against the
  v0.7.5 tree; two corrected with dated notes (the `alarm` uniqueness is an index rather than a
  table constraint; `project_situation_detail` moved in the v0.7.4 package split). **No constraint
  changed in substance.** New §0 records what v0.7.5 hands over and what it does not; new §6a leaves
  four column questions explicitly open for v0.8.0's own Phase 0.
- `docs/README.md` states that backticks are the tag convention **and** that the guard reads them,
  so the two rules cannot drift apart again.

### Decisions

**#98** the gate refuses unknown route shapes rather than learning to walk them · **#99** the UI is
verified by source inspection plus a written manual protocol, and why not a JS harness · **#100**
the documentation guard's inline-code strip is dropped, not narrowed · **#101** FastAPI is not
pinned; the representation change is detected instead.

## [0.7.4] - 2026-07-31 — "no contradictions, no unowned debt"

The release that closes every loose end the v0.7.x series leaves behind, so that v0.7.5 and v0.8.0
start from a repository that agrees with itself. Runtime dependencies stay at **five**; **zero** new
migrations (still `0001`–`0007`); **zero** new routes, capabilities, audit actions or served paths;
`make eval` is **byte-identical** to v0.7.3. **An operator upgrading from v0.7.3 has nothing to do**
— see [MIGRATION.md](MIGRATION.md).

**Exactly two intentional behaviour changes**, both in the route-declaration gate, both at import or
startup time and neither on the request path. They are listed under *Security* below. Nothing else
in the appliance's behaviour moves.

### Security — F40 and F41, the first findings since F39 (v0.7.1)

Both were found by adversarial probing of `api/declare.py` during the v0.7.2 review, **reproduced by
execution** rather than by reading, and deferred from v0.7.3 because fixing a security-adjacent
guard inside a move release forfeits the parity story. Neither was exploited on the v0.7.3 surface;
both were latent holes in a guard whose entire value is completeness. See
[SECURITY-REVIEW-0.7.4.md](docs/security/SECURITY-REVIEW-0.7.4.md).

- **F40 — the gate covered three verbs and only the decorator form.** `DeclaredRoutes` wraps `get`,
  `post` and `delete`, so a route registered directly on the FastAPI application reached the route
  table without ever consulting `require_declaration`. **Behaviour change:** a route registered by
  *any* path without a declaration now fails. Fixed by `assert_every_route_is_declared(app)`, called
  as the last statement of `create_app` before it returns — so a mis-declared route stops the
  process rather than failing only under test. It inspects the *result* rather than the route in,
  which makes it complete **by construction** rather than by enumeration: a registration mechanism
  nobody has written yet still produces a route. The decorator-time refusal is kept as well, because
  failing where the route is written gives a far better error.
- **F41 — the exemption was by path prefix, not by absence of capability.** `require_declaration`
  returned early for anything not starting with `/api`, which is true of today's public surface and
  **accidentally** true of everything else outside it — `require_declaration("GET", "/metrics")`
  returned cleanly, and `/metrics` is on the ROADMAP. **Behaviour change:** a non-`/api` path outside
  the unauthenticated allowlist now fails. Fixed with an explicit `UNAUTHENTICATED_PATHS` allowlist,
  asserted against what `routes_static.py` registers **and** against the non-`/api` routes of a built
  application.

Ten regression tests, each proved to fail on the unmodified tree. Route order, the generated
authorization matrix and both route-map completeness tests pass **unedited**.

### Changed — the last three modules split; `DEBT_ALLOWLIST` reaches zero

Every module under `src/netcorenoc/` is now at or under the 400-line guard **except `engine.py`
(542), which is `COHESION_EXEMPT` permanently**. All **56** function bodies moved as identical text,
proved by a `sha256` table taken before the move and recomputed after it.

- **`shaping.py` (476) → the `shaping/` package** — `fields.py` (88), `scope.py` (302),
  `project.py` (110), `__init__.py` (110). `MODULE-ARCHITECTURE.md` §10.2 recorded the seam as two
  axes; the AST showed **three** parts, and the projections turned out to be the *consumer* of the
  other two rather than a third axis. §10.2 is superseded in place (DECISIONS #95). The F35
  invariant travels with `scope.py`, comments included.
- **`rbac.py` (436) → the `rbac/` package** — `tables.py` (277), `policy.py` (197), `__init__.py`
  (79). **`rbac` remains the single source of authority for authorization**: the tables are
  re-exported by **identity, never by copy**, and two new tests assert it. Both were shown to fail
  against a deliberately-copying `__init__.py` — against which **218 pre-existing tests pass green**,
  which is precisely why they exist. Every `"unscoped"` justification comment travels with its entry,
  and the three import-time asserts travel with the tables they constrain (DECISIONS #96).
- **`varbind_profile.py` (417) → 305 + `varbind_accum.py` (154)** — one extraction, not a package.
  `engine` layer, following its parent (DECISIONS #97).

`from netcorenoc.rbac import …`, `from netcorenoc.shaping import …` and
`from netcorenoc.varbind_profile import …` keep working for **every** symbol; the packages are
invisible to every caller.

### Changed — the roadmap says one thing about v0.8.0

The repository stated **both** that v0.8.0 was customer-supplied models and that it was the
operator-feedback dataset — twice each, four lines apart in `docs/ROADMAP.md`. The resequencing that
settles it had been decided and acted on for two releases and was **never recorded**.

- **DECISIONS #93** records it with the reasoning: **v0.8.0 is the operator-feedback dataset**;
  customer-supplied models move to **v0.13.0**, behind the champion/challenger framework they plug
  into; **ONNX only** — the Python entry-point escape hatch is **rejected, not deferred**; and the
  worker-process preemption harness remains a blocking prerequisite.
- **`docs/architecture/ROADMAP-0.8-TO-0.13.md`** is new: one screen per release, recording the chain
  and **why the order cannot be permuted**.
- The scorer-plugins draft was `git mv`-d to `SCORER-PLUGINS-0.13-DRAFT.md` and **superseded in
  place** with a dated note; its analysis is untouched. `EXTENSIBILITY-0.6-DRAFT.md`,
  `MODULE-ARCHITECTURE.md`, `repo-map.md` and `DESIGN.md` are amended where they name the sequence.

### Added — a documentation-consistency guard

`tests/test_documentation.py` asserts that the repository states **exactly one answer** to "what is
release X" (DECISIONS #94). Installed against the still-contradictory tree and **observed red before
green**; its first version caught too little and was widened until its catch set covered the Phase 0
enumeration in full. Records and forward-looking documents are distinguished, and that distinction
is itself asserted — `SCOPE-0.6.md` says what v0.6.0 believed, and rewriting a record to agree with
a later decision would be falsifying it.

### Added — two specifications, neither implemented

- **`FEEDBACK-PATH-0.7.5-DRAFT.md`** — the operator-feedback acquisition path. Every two seconds the
  SSE update rebuilds every situation card, including the expanded one, and the rebuilt detail is
  filled only after a network round trip. The failure that matters is not the flicker: a click can
  be recorded against a membership the operator never evaluated, which is a **silently wrong label**,
  and nothing downstream can detect one.
- **`FEEDBACK-DATASET-0.8-DRAFT.md`** — the v0.8.0 dataset, refined, with each of its four
  constraints traced to the code that causes it.

### Notes

- Decisions **#93–#97**. Security findings **F40** and **F41**.
- Test count **701 → 754**. Coverage **95.81–95.85 %** across runs, at or above the v0.7.3 figure of
  95.80 %. The spread is `receiver.py`'s known timing-dependent branches, on the ROADMAP since
  v0.7.3 and unchanged here.
- `docs/scope/SCOPE-0.7.4.md`, `docs/releases/BUILD-REPORT-0.7.4.md`, and gate evidence
  `docs/gates/v0.7.4-phase-0.md` … `-phase-6.md`.

## [0.7.3] - 2026-07-30 — "the data and engine layers" (internal structure only)

**Internal structure only. No behaviour change of any kind.** Not one route path, method, status
code, response field, database row or number moves. Runtime dependencies stay at **five**; **zero**
new migrations (still `0001`–`0007`); **zero** new routes, capabilities or audit actions;
`make eval` is **byte-identical** to v0.7.2. **An operator upgrading from v0.7.2 has nothing to do**
— see [MIGRATION.md](MIGRATION.md).

This is the **last structural release**. v0.8.0 resumes the feature line.

### Changed — `store.py` is now the `store/` package

`src/netcorenoc/store.py` (1 512 lines, 109 methods on one class) becomes eighteen modules split
along its own section comments, largest **213** lines, one level deep. `from netcorenoc.store
import Store, EdgeRow, FeedbackResult, MIGRATIONS_DIR` keeps working verbatim.

The mechanism is mixins over a thin annotated `StoreBase` that declares the ten attributes and the
`conn` accessor and holds no behaviour (DECISIONS #88). **All 109 method bodies are unchanged
text** — the enclosing class header is the only edit — proved by a `sha256` table taken before the
move and recomputed after it, through `inspect.getsource` on the live attribute resolved via
`Store.__mro__`.

**The invariant this protects: one `Store`, one connection, one `store.lock`.** The single
connection and the single lock are load-bearing — F39 exists precisely because one connection is
shared by the engine task and every API request, and v0.7.1's `write_txn` discipline is built on
`store.lock` being the one mutual exclusion. `tests/test_store_concurrency.py` is the control, and
it was written and mutation-tested against the pre-split tree.

### Changed — `main.py` is now the engine, the runner, and five siblings

`main.py` (1 079 lines) becomes `engine.py` (542), `runner.py` (227), `maintenance.py` (113),
`scorer_lifecycle.py` (112), `settings.py` (106), `gaps.py` (94), `engine_base.py` (61) — and
`main.py` itself at **79** lines: `main()`, the `__main__` guard, and the re-exports.
**`python -m netcorenoc.main` is unchanged**, which is why `main.py` stays a module and not a
package (DECISIONS #89).

The ingest path does not fragment. `run`, `_commit_batch`, `_process`, `drain`,
`_assign_situation`, `_handle_clear`, `_handle_state_clear`, `_close_situation`, `_resolve_entity`,
`_resolve_severity`, `_seed_clear_pair`, `_is_flapping`, `apply_feedback` and `FlapDetector` stay
together, because "ingestion is sacred" is only auditable if a reviewer can confirm — without
following imports — that nothing on that path takes a lock, does I/O, or awaits where it must not.
`maintenance()` and `maintenance_loop()` stayed with them, against both module tables, because
`maintenance` takes `store.lock` and calls `_close_situation` (DECISIONS #90).

### Fixed — the project's one recorded layer violation

`main.py` → `netcorenoc.api` (engine → http), recorded in `MODULE-ARCHITECTURE.md` §1 since v0.7.2,
is **resolved**: `runner.py` and `main.py` are the process entry point and may reach up into
`http`; `engine.py` may not, and does not.

### Added — three guards, and one hole closed in an old one

* **`tests/test_layers.py`** — the dependency rule has had a paragraph since v0.7.2 and **no test**,
  which is why its one violation went unfixed for a release. It now parses every module's imports
  and fails on any upward edge. Exemption list **empty** (DECISIONS #92).
* **`tests/test_store_concurrency.py`** — one connection, one lock, concurrent writes from three
  domain modules, the audit chain under 24 concurrent appends, and `write_txn`'s rollback contract.
* **`COHESION_EXEMPT`** — for a module that is large because an **invariant** forbids splitting it,
  as distinct from debt. `engine.py` is its one entry, citing "ingestion is sacred", with **no owner
  and no fix date** — that absence is the semantic difference and a test asserts it (DECISIONS #91).
* **`test_no_module_may_join_the_allowlist`** — "the debt allowlist may only shrink" was asserted in
  one direction only: a *stale* entry failed, but a **newly added** module would have passed green.

`DEBT_ALLOWLIST` is down from five entries to **three** (`rbac.py`, `shaping.py`,
`varbind_profile.py`, all owned by v0.7.4). `store.py` and `main.py` are gone from it.

### Security

`docs/security/SECURITY-REVIEW-0.7.3.md` — **zero findings**. The series stays at F39; F40 is
unused. The review states plainly that this release does **not** make the data layer more correct,
and names the cost it does carry: a mixin split makes it easier for a future contributor to add a
method that forgets the lock, because the neighbouring methods that would have shown the pattern
now live in another file.

## [0.7.2] - 2026-07-30 — "the HTTP package" (internal structure only)

**Internal structure only. No behaviour change of any kind.** Not one route path, method, status
code, response field or capability moves. `PERMISSIONS`, `ROUTE_PERMISSIONS`, `PUBLIC_ROUTES`,
`AUDITED_DENIED_PERMISSIONS` and the audit action catalog are unchanged; runtime dependencies stay
at **five**; **zero** new migrations; `make eval` is **byte-identical** to v0.7.1. An operator
upgrading from v0.7.1 has nothing to do — see [`MIGRATION.md`](MIGRATION.md).

v0.7.1 closed six findings; four of them lived in `api.py`, and they hid for one structural reason:
a single 1 752-line file held the CSRF gate, identity resolution, the governance policy cache,
capability resolution, scope resolution, the audit helper, the rate limiter, the transaction
discipline **and** forty route handlers. This release splits that file into a package, and replaces
the string-joined route/permission convention with a declaration that fails before the process can
serve — so the class of defect F34 belongs to becomes structurally impossible rather than merely
tested for.

> **Declare now, prove the declaration is true now, enforce mechanically later — and move the code
> without moving a single decision.**

### Changed — internal structure

- **`src/netcorenoc/api.py` is now the package `src/netcorenoc/api/`** — sixteen modules, largest
  361 lines, one level deep (DECISIONS #79, #85). `api/__init__.py` re-exports every symbol
  previously reachable as `netcorenoc.api.X`, so `import netcorenoc.api` and
  `from netcorenoc.api import create_app` behave exactly as before and `create_app`'s signature is
  unchanged. **Read `api/perimeter.py` first if you are reviewing security.**
- **The HTTP security boundary is one file.** `api/perimeter.py` holds everything that decides
  *whether a request may proceed* — CSRF, identity resolution, the bootstrap gate, capability
  resolution, scope resolution, the rate limit, the audit-row helper, the write-transaction
  boundary, the write-side scope check and its denial audit, `DENIED_ACTION` **with its import-time
  assert against `rbac.AUDITED_DENIED_PERMISSIONS`**, and the security-headers middleware. It owns
  no handler logic and no response shaping (DECISIONS #76, #77).
- **Every handler body is textually unchanged**, proved hash by hash: all 43 decorator-registered
  handlers hash identically to v0.7.1 from their `def` line down, and all 43 decorators differ by
  exactly `@app.` → `@route.` and nothing else. The mechanism is a mandatory local-rebinding block
  at the top of each route module's `register()` (DECISIONS #78).
- **The route table is identical, in order.** FastAPI resolves the first matching route, so the
  48-entry ordering is behaviour; a test pins it against the v0.7.1 baseline.

### Added — the declaration discipline

- **`rbac.ROUTE_SCOPE`** — one entry per non-public route, `scoped` | `unscoped` | `admin_only`,
  with a written justification on every `unscoped` (asserted by test). `admin_only` is *derived*
  from `PERMISSIONS` at import in both directions, so the two tables cannot disagree. It is
  **descriptive** this release: nothing reads it at request time, because injection would change
  control flow and control flow is behaviour (DECISIONS #80).
- **`api/declare.py`** — the registration gate. A route absent from `ROUTE_PERMISSIONS` or
  `ROUTE_SCOPE` raises while the application is being built, so an appliance carrying an undeclared
  route does not start. `PUBLIC_ROUTES` and non-`/api` paths are exempt **by explicit
  consultation**, never by omission. A test asserts no raw `@app.<verb>` decorator survives anywhere
  in the package.
- **`docs/architecture/MODULE-ARCHITECTURE.md`** — the target module map for the whole project,
  including `store.py` and `main.py`, which this release does not touch. Five layers, the
  dependency rule, the placement rule, and the v0.7.3 specification.
- **A module-size guard with a shrink-only debt allowlist** (`tests/test_architecture.py`): no
  module over 400 lines, an allowlisted module may not grow, and an entry that drops within the
  limit must be deleted. Installed in Phase 2 against the *unmodified* tree, so every step of the
  split was measured by a rule that predated it.

### Known debt, added by this release

- **`rbac.py` grew from 348 to 436 lines** and joins the debt allowlist with **v0.7.4** as its
  owner. `ROUTE_SCOPE` — the declaration whose absence *was* F34 — belongs in the single source of
  authority, and every `unscoped` justification is required by test, so neither the table nor the
  prose could be traded away for a line count. Split seam recorded: the route/capability tables on
  one side, the capability-policy parser and resolver on the other (DECISIONS #87).

### Security

**No findings.** A move-only release should produce none, and this one did.
`docs/security/SECURITY-REVIEW-0.7.2.md` says so plainly, and says equally plainly that this
release does **not** make the perimeter more correct: the same code in different files has the same
behaviour, and every caveat in SECURITY-REVIEW-0.7.1 §4 stands unchanged. What it buys is a
perimeter a reviewer can read in one sitting and a registration discipline under which F34's class
cannot recur silently.

## [0.7.1] - 2026-07-29 — "the write perimeter" (security patch)

**A security patch, not a feature release.** Six confirmed defects (F34–F39) in which a v0.7.0
guarantee was enforced on reads and not on writes. No new capability, no new route, no new
configurability, no restructuring. `PERMISSIONS`, `ROUTE_PERMISSIONS`, `PUBLIC_ROUTES`,
`AUDITED_DENIED_PERMISSIONS` and the audit action catalog are unchanged; runtime dependencies stay
at **five**; `make eval` is **byte-identical** to v0.7.0.

v0.7.0's review declared, under F32, that scoping is enforced by "one filter applied to every
NE-bearing read". That sentence is true, and it is the defect: the perimeter was designed as a
*read* projection and the three editor-level write routes were never brought inside it. Worse, one
of the resolver's own inputs — the operator label — was writable by the very role the scope
constrains. This release closes the class, not the six instances.

> **Authorization never reads data the constrained party can write, and a write is inside the
> perimeter or it is a defect.**

### Security

- **F34 (High) — scope is now enforced on the three `editor` write routes.** `POST
  /api/situations/{sid}/feedback`, `POST /api/situations/{sid}/close` and `POST /api/labels`
  resolve scope through the **same** `scope_for` the reads use, and deny through each handler's
  **existing** 404 branch, so out-of-scope and nonexistent stay indistinguishable in status, body
  and timing. Denials are audited. *Impact: a scoped editor could previously mutate global learned
  state for network elements they cannot see, and the 200-vs-404 split was an existence oracle.*
- **F35 (Critical) — an editor can no longer widen their own scope by writing a label.** Scope
  selectors resolve against **NE identity and address only**; the operator label is gone from the
  resolver, and the timeline filters on `ne_id` rather than on a rendered display string. *Impact:
  with a policy of `{"editor": ["core-*"]}`, labelling an out-of-scope device `core-pwned` used to
  add it to the editor's own visible set; a colliding label leaked an out-of-scope element's alarm
  timing and classes.*
- **F36 (High) — operator feedback is idempotent and bounded, and no longer ages global state.**
  At most one effect per `(situation, verdict)`, and the forgetting epoch advances only when a
  situation **closes**. Feedback rows now record `principal_ref` and `role`. *Impact: 60 confirms
  and 20 splits previously drove one pair's learned mass from 1.000000 to 1.824e-05, and the author
  was unrecorded.*
- **F37 (Medium) — a label write to a target that does not exist now returns 404.** Migration
  `0007` removes existing orphans. *Impact: every editor previously held an unbounded,
  never-reclaimed write primitive against the database file.*
- **F38 (Medium) — list endpoints apply their `LIMIT` after scope filtering.** *Impact: a scoped
  viewer's own open incidents used to vanish from their list when a noisy neighbour they cannot see
  was busy, and the returned count varied with out-of-scope volume.*
- **F39 (Medium) — every API write is one transaction: mutate → audit → commit, or nothing.** A
  single `write_txn()` helper rolls back on any exception. *Impact: a handler that raised after
  mutating previously left the statement pending on the shared connection, and the next commit from
  an unrelated caller adopted it — the change landing with no audit row.*

### Changed

Three deliberate behaviour changes at empty policy, and no others (`docs/scope/SCOPE-0.7.1.md` §2):

1. a label write to a target that does not exist returns **404** (was 200) — F37;
2. a repeated **identical** feedback verdict is a **no-op** (was applied and recorded each time) —
   F36;
3. list endpoints truncate after filtering — invisible at empty policy, and the unrestricted result
   set is asserted byte-identical — F38.

**Breaking for one configuration only:** a scope selector that relied on matching an operator
**label** (`core-*`) now matches by address or not at all, and is rejected at write time with a
message pointing at `MIGRATION.md`. Review any stored scope policy that uses one. Selectors by NE
id, exact address, CIDR and **address** glob (`10.0.*`) are unchanged.

### Added

- Migration `0007_write_perimeter.sql` — two nullable attribution columns on `feedback`, a
  `UNIQUE (situation_id, verdict)` index with prior de-duplication (earliest row by `created_at`
  kept), and the F37 orphan cleanup. **Seeds nothing; changes no behaviour by itself.**
- A **generated** write-perimeter test over `ROUTE_PERMISSIONS`: every mutating route below `admin`
  must resolve scope, so a route added in any future release fails CI until it is inside the
  perimeter.
- A **resolver-input invariant** test: no input to the scope decision may be writable by a scopable
  role.
- A transaction-discipline test and a feedback-boundedness test.
- `docs/security/SECURITY-REVIEW-0.7.1.md`, `docs/scope/SCOPE-0.7.1.md`, decisions **#65–#74**, and
  a `v0.7.1` threat-model extension. `SECURITY-REVIEW-0.7.md`'s F32 row is **superseded in place**
  with a dated note pointing to F34 and F38 — the published claim is left intact rather than
  rewritten.

### Fixed

- The UI now surfaces a failed feedback, close, or rename instead of silently swallowing the
  rejection.

## [0.7.0] - 2026-07-25 — "governance"

An admin can define what each role and principal may **do** and may **see**. Both are stored,
audited policy read through the **existing** single decision points — no new authorization
mechanism, no second decision site, no new runtime dependency, and nothing whatsoever on the ingest
path.

**With no stored policy, v0.7.0 is byte-identical to v0.6.0.** The compiled permission map and full
visibility are simultaneously the *default* and the *ceiling*. Migration `0006` seeds **no** rows,
so a fresh install and an upgraded one behave exactly as v0.6.0 did, and most operators never open
the Governance panel. That parity is a release gate, not a claim.

### Added

- **Admin-configurable RBAC.** A stored policy narrows what a role — or an individual principal —
  holds. The resolved set is `ceiling(role) ∩ granted(role) ∩ granted(principal)`, computed by one
  function (`rbac.resolve_capabilities`) that every caller reads. **Escalation is structurally
  impossible, not merely forbidden**: an intersection cannot exceed its first operand, so a policy
  naming a capability above a role's ceiling is *inert* — however the row arrived, including a
  direct `sqlite3` write to a stolen or restored database. Proven property-based over generated and
  adversarial policies (DECISIONS #53).
- **Per-role / per-principal visibility scoping.** Which NEs a viewer or editor may see, by NE id,
  exact address, CIDR, or name glob, resolved against the live inventory on every request — so an
  NE discovered after the policy was written is covered by a CIDR that plainly matches it
  (DECISIONS #57). One filter at every NE-bearing read; a graph edge survives only when **both**
  ends are in scope.
- **404, not 403, for an out-of-scope resource**, produced by the projection returning nothing so
  the handler's *existing* not-found branch fires. "Not yours" and "does not exist" are one code
  path — same status, same body, same timing — rather than two that happen to agree (DECISIONS #60).
- **Honest redaction.** A situation is listed if any member is in scope; out-of-scope members become
  a **count and their alarm classes** — never an id, address, entity key, or varbind — and links to
  them are withheld. Silent omission was rejected: an operator shown "3 alarms" for a 40-alarm
  cross-boundary fibre cut would be *confidently wrong* (DECISIONS #59).
- **Four capabilities** — `rbac.read`, `rbac.write`, `scope.read`, `scope.write` — admin-only,
  `config`-class, no delegation, all four audited when denied. **Two audit actions**
  (`rbac.policy.update`, `scope.policy.update`) with before/after, plus `governance.fallback` for a
  policy that will not parse.
- **Migration `0006_governance.sql`** (`user_version` 5 → 6): an append-only `governance_policy`
  history with `RAISE(ABORT)` triggers and no sanctioned deleter, plus a per-kind `governance_active`
  pointer. Apply, roll back, and **clear** are one call each; clearing removes the pointer, never
  the history.
- **A Governance panel** in the UI, and tabs/affordances now gated on the **resolved capability set**
  from `/api/me` rather than on role rank — a UI deriving permissions from rank would be a second
  decision site that silently disagrees with the server once a policy exists.

### Changed

- **One candidate-selection rule for the engine and preview** (the v0.6.0 close-out).
  `preview.partition()` was a second implementation of the engine's windowing with its own copies of
  the window length and the cap; a change to `correlate.WINDOW_S` alone would have left the what-if
  replaying a different window from the engine it claims to predict. `correlate.select_candidates()`
  is now the single implementation and preview's bounds are **aliases** of the engine's constants.
  A test asserts preview reproduces the engine's *actual situation partition*, member for member;
  another asserts the two callers cannot drift again (DECISIONS #61).
- `rbac.role_allows` is reimplemented on top of the new `rbac.ceiling` and is no longer called from
  `src/` — it answers the *ceiling* question and is kept as the independent oracle the parity gate
  compares the resolver against.
- `auth.Principal` gains `token_id` and a `ref` property (`user:<id>` / `token:<id>`), so a
  per-principal policy keys on row identity rather than on a display name that is not unique across
  users and tokens (DECISIONS #62).
- SSE re-resolves capability **and** scope on **every event**, not at connection time, and ends the
  stream if `events.stream` is revoked mid-connection.

### Security

- **F27–F33** in [`docs/security/SECURITY-REVIEW-0.7.md`](docs/security/SECURITY-REVIEW-0.7.md),
  each with a passing regression test: escalation via a stored policy, a second decision site,
  fail-safe and lockout, session staleness, provenance integrity, scope bypass and existence
  disclosure, and the hot-path surface.
- **Fail-safe in both directions, deliberately asymmetric.** A malformed *capability* policy falls
  back to the compiled ceiling (the shipped v0.6.0 baseline — nobody gains anything); a malformed
  *scope* policy denies viewer and editor (nobody sees anything new). Both raise an operator warning
  and write an audit row. The asymmetry is safe **only because admins are never scoped**
  (DECISIONS #55, #58).
- **An admin can never be locked out.** A *well-formed* policy could otherwise remove `rbac.write`
  from the admin role, leaving no authenticated path to repair the perimeter. A small compiled
  recovery set is unioned back inside the resolver; it is a subset of the admin ceiling, so the
  escalation invariant is untouched (DECISIONS #64).

### ⚠ Visibility scoping is a presentation control and is **not tenant isolation**

Correlation still learns across **all** network elements, and a situation may still *form* across a
boundary a principal cannot see — its members are hidden from them, not prevented from correlating.
A scoped operator therefore sees a partial picture, which is exactly why the redacted count is
shown rather than the members silently dropped. True multi-tenant isolation is a separate, larger
feature on [`docs/ROADMAP.md`](docs/ROADMAP.md); v0.7.0 does not provide it, and says so in the
docs, in the API responses, and in the UI, with a documentation test asserting the statement cannot
be quietly dropped.

### Quality

426 → **499 tests**; coverage **95.43 %** (up from 95.24 %); `make eval` **byte-identical**;
`ruff`, `ruff format`, `mypy --strict`, `vulture`, `bandit`, `pip-audit`, structure guard, link
check, SHA-pin lint and the d3 checksum all clean. Runtime dependencies unchanged at **five**.

## [0.6.0] - 2026-07-25 — "the scoring seam"

The correlation formula stops being a hard-coded expression and becomes the **default
implementation of a versioned, swappable, explainable interface** — plus admin-tunable
parameters with safe preview and one-click rollback. **Grouping behaviour does not change**: at
the default parameters v0.6.0 produces byte-identical output to v0.5.0 on every fixture and a
byte-identical `make eval` delta table. One process, one SQLite file, one static UI, **zero new
runtime dependencies**, and not one byte of new work on the ingest path.

### Added

- **The `LinkScorer` seam** (`src/netcorenoc/scoring.py`): a `Protocol` with `scorer_id`,
  `contract_version`, `score(LinkFeatures) -> LinkScore` (pure, deterministic, side-effect-free,
  inference-only) and `params_fingerprint()`. `LinkFeatures` carries exactly what the current
  computation uses plus **reserved optional slots** (`severity_i/j`, `topo_distance`,
  `probable_cause_i/j`, `event_type_i/j`, all `None` in 0.6) so X.733/3GPP features and future
  scorers are a *minor* contract bump, never a breaking one. `LinkScore.terms` makes a per-term
  breakdown **contractual**, so "why did it decide that?" can never regress.
- **`AdditiveScorer`** — the built-in three-term score as the default and the always-available
  safe fallback, with the five parameters as dataclass fields (completing the v0.5.0 P2 tidy).
- **Tier A: admin-configurable scoring parameters.** `GET /api/scorer` (viewer+),
  `POST /api/scorer` and `POST /api/scorer/rollback` (admin), backed by an **append-only,
  immutable** `scorer_config` table and a one-row active pointer — apply and rollback are the
  same operation, and history is never edited or deleted.
- **Read-only preview** (`POST /api/scorer/preview`, admin): re-partitions a bounded snapshot of
  recent alarms under the candidate parameters and returns the structural delta (what merges,
  what splits, links gained/lost). Deterministic, bounded by an alarm cap *and* a hard timeout,
  rate-limited by its own tight bucket, writes nothing, off the ingest path, and imports nothing
  from `eval/`. It says plainly that it is directional, not exhaustive.
- **Validation that rejects the degenerate, not merely the out-of-range** — a threshold at zero
  merges every alarm into one situation; a threshold at the weight sum links nothing, ever.
  Neither can be stored.
- **Decision provenance**: every situation records the `scorer_config_id` in effect when it was
  opened, so a historical grouping stays explainable months later.
- **Fail-safe execution**: any scorer exception, contract violation, or budget overrun degrades
  to the coded defaults, audits `scorer.fallback`, and raises an operator warning. The engine can
  never run scorer-less.
- **UI**: an admin-only **Scorer** panel (active parameters, preview with its caveat, immutable
  history with one-click rollback), pruned from a non-admin DOM entirely.
- New capabilities `scorer.read` (viewer+), `scorer.preview` / `scorer.write` (**admin only, no
  editor delegation**); new audit actions `scorer.config.update`, `scorer.preview`,
  `scorer.fallback`.
- **v0.7.0 and v0.8.0 specifications** (spec only, built later):
  `docs/architecture/GOVERNANCE-0.7-DRAFT.md` (admin RBAC + visibility scoping, stating
  explicitly that scoping is **not** tenant isolation) and
  `docs/architecture/SCORER-PLUGINS-0.8-DRAFT.md` (blessed ONNX adapter + Python entry-point
  escape hatch under this release's contract).

### Removed

- **The legacy `OPTICORR_*` environment aliases**, as promised in v0.4.0 and v0.5.0
  (DECISIONS #34, #39, #45). Setting any of them is now a **hard startup error** naming each
  variable and its `NETCORENOC_*` replacement — never a silent no-op, because an ignored
  `OPTICORR_ALLOWLIST` would mean every trap source is accepted. See `MIGRATION.md`.

### Changed

- `correlate.py` selects candidates and applies the verdict; it no longer inlines the arithmetic.
  `link` objects in `GET /api/situations/{sid}` gain an additive `terms` list alongside the
  unchanged `term_t`/`term_a`/`term_e`.
- The external-criterion API specified in `EXTENSIBILITY-0.6-DRAFT.md` was **rejected**, not
  deferred (DECISIONS #44): `score()` is typed pure and inference-only, so no outbound call can
  decide a link. That draft is superseded in place with a disposition table.

### Security

- **`docs/security/SECURITY-REVIEW-0.6.md`** — findings F20–F26 (parameter poisoning, privilege
  boundary, preview as a DoS/exfiltration surface, provenance integrity, hot-path surface,
  fail-safe execution, removed-knob misconfiguration), each with a control and a regression test,
  plus an honest critical analysis of residual risk. Threat model extended.

### Migration

- One forward-only migration, `0005_scorer_config.sql` (`user_version` 4 → 5), applying to a
  populated v0.5.0 database: additive tables, a nullable provenance column, a seed equal to the
  coded defaults, and a backfill. Grouping is unchanged, the audit chain still verifies.
  **One-time action required:** rename any `OPTICORR_*` environment variables. See `MIGRATION.md`.

## [0.5.0] - 2026-07-24 — "legible, installable, contributable"

An organization/structure release: it makes the project legible, installable, and contributable,
and prepares the ground for v0.6.0 — **without changing the running correlator at all.** No engine,
schema, API, or UI-behaviour change; the `make eval` metrics are byte-identical to v0.4.0. One
process, one SQLite file, one static UI, **zero new runtime dependencies**. 320 tests, 95 %
coverage.

### Changed

- **Repository adopts the PyPA `src/` layout** (`netcorenoc/` → `src/netcorenoc/`, history
  preserved). The import path stays `netcorenoc` — no public change. Tests now run against the
  installed package, the standing guard against the F12 class of bug. All packaging/tooling paths
  updated (`pyproject`, `Dockerfile`, `Makefile`).
- **Documentation reorganised into a navigable tree** with an index (`docs/README.md`):
  `architecture/`, `adr/`, `security/`, `scope/`, `releases/`, `gates/`, plus a newcomer
  `architecture/repo-map.md`. The decision log stays one append-only file under `adr/`.
- **`SECURITY.md` restructured** so a coordinated **vulnerability disclosure policy** is what a
  reporter finds first; the operator hardening guide moved to `docs/security/operations.md`.
- **Quickstart is now `docker compose up`.**
- The legacy `OPTICORR_*` environment-alias deprecation window was **extended one version to
  v0.6.0** (DECISIONS #39) — the only behaviour-adjacent change, and a non-removal.

### Added

- **Self-contained deployment**: a hardened `docker-compose.yml` (read-only rootfs, `cap_drop:
  [ALL]` + `CAP_NET_BIND_SERVICE`, `no-new-privileges`, `tmpfs /tmp`, named DB volume, `/healthz`
  healthcheck) with a committed `.env.example`; a hardened example `deploy/netcorenoc.service`
  systemd unit; `.dockerignore`/`MANIFEST.in`; `make dist`/`make release-check` and
  `tools/release_check.py`.
- **Open-source scaffolding**: `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` (Contributor Covenant
  v2.1), GitHub issue/PR templates (security → private advisories), `NOTICE` and
  `ui/vendor/d3.LICENSE`, `.editorconfig`, README badges.
- **`/.well-known/security.txt`** (RFC 9116), shipped in the package and served by the app from
  the static allowlist under the existing CSP/security headers.
- **v0.6.0 specification** (spec only): `docs/architecture/EXTENSIBILITY-0.6-DRAFT.md` — admin
  RBAC, per-role/per-principal visibility scoping, and a configurable/pluggable match formula,
  each with its security framing; every element `v0.6.0: planned`, implemented none.
- **Dormant, opt-in CI**: a SHA-pinned least-privilege `release.yml` (built-in token only;
  publish/sign steps commented) and `dependabot.yml`; `ci.yml` actions SHA-pinned.
- **New guard tests**: structure + documentation link check (`test_structure.py`), GitHub-Actions
  SHA-pin lint (`test_workflows.py`), deployment-hardening assertions (`test_deploy.py`), and the
  RFC 9116 `security.txt` tests (`test_security_txt.py`).

### Security

- **`docs/security/SECURITY-REVIEW-0.5.md`** — findings F15–F19 (compose/systemd hardening,
  `security.txt`/disclosure policy, packaging integrity, SHA-pinned least-privilege workflows),
  each with an assertion test, plus an honest critical-analysis of residual risk. No exploitable
  runtime weakness was found; the runtime attack surface gains only the static `security.txt`
  path. Threat model extended with a v0.5.0 note.

### Migration

- No schema change; a live v0.4.0 database upgrades in place. The `netcorenoc` import path and all
  `NETCORENOC_*`/legacy `OPTICORR_*` env names are unchanged (the alias removal is now v0.6.0).
  See `MIGRATION.md`.

## [0.4.0] - 2026-07-23 — "trustworthy by construction"

Security- and reliability-hardening release under a new identity. **No new inference features.**
One process, one SQLite file, zero new runtime dependencies. 283 tests, 95 % coverage, eval delta
byte-identical against the frozen baseline.

### Changed

- **Project renamed** from *OptiCorr* / *NewProjectNetworj* to **NetCoreNOC**
  (`github.com/leonardoSaaads/NetCoreNOC`). Import package `netcorenoc`, env prefix `NETCORENOC_*`,
  session cookie `netcorenoc_session`, CSRF header `X-NetCoreNOC-Client`. Legacy `OPTICORR_*`
  environment names are honoured for this one version with a deprecation warning (removed in
  v0.5.0); the cookie rename forces a one-time re-login. (DECISIONS #34)
- `GET /api/config` now requires a dedicated least-privilege `config.read` capability instead of
  the write capability. (F9)
- Response bodies are shaped by role: viewers receive coarsened device IPs (/24, /48) and never
  `source_ip` or `community_tag` — deny-by-default extended from routes to fields. (F7)
- Admin screens are pruned from a non-admin DOM (absent, not merely hidden); the UI gained a
  design-token refresh (light variant, focus states, AA contrast, responsive) — still four files.

### Added

- **Reliability**: supervised background tasks with backoff-restart and operator warnings (F10);
  startup `PRAGMA integrity_check`/`foreign_key_check` (F11); `/readyz` readiness endpoint (DB
  reachable + migrations applied + queue headroom, ok/not-ok only); graceful queue drain on SIGTERM.
- **Supply chain**: the vendored d3 is SHA-256-pinned (`ui/vendor/CHECKSUMS.txt`) with a CI job;
  the container is documented with a hardened run recipe and base-image digest pinning.
- **Standards**: `docs/SECURITY-REVIEW-0.4.md` — OWASP ASVS 4.0.3 L2 / NIST SP 800-63B / RFC /
  CIS compliance mapping.
- **Corpus/tooling**: a declarative scenario DSL (`eval/scenario_dsl.py`) + trap simulator
  (`tools/trap_sim.py`, `make sim`); security-event correlation and network-fault-breadth
  scenarios as engine-driven tests; a consolidated abuse suite.
- CI gains a dead-code gate (`vulture` + committed allowlist) and a d3-checksum job.

### Fixed

- **A built wheel shipped only `index.html`**, so `pip install .` (the Dockerfile path) served a
  UI whose `app.js`, `style.css`, and vendored d3 all 404'd. The whole UI now ships. (F12)
- The orphaned second audited-denied table (`rbac.AUDITED_DENIED_PERMISSIONS` vs
  `api.DENIED_ACTION`) is collapsed to one source with a divergence test. (F8)
- Removed confirmed dead code (`auth.ROLES`, `auth.now_s`, `store.set_user_disabled`,
  `VarbindProfiler.role_of`).

### Security

- CSRF enforcement now has regression tests (missing/renamed `X-NetCoreNOC-Client`, origin/host
  mismatch → 403); the rename could otherwise have silently broken it. (F14)
- All abuse-suite properties (CSP + headers on new routes, shaped-viewer injection inert,
  entity-key-forgery bound, append-only audit) confirmed to hold.

## [0.3.0] - 2026-07-23

Entity identity — learning *what* is alarmed, not merely *who* reported it. A network element
starts as a single entity and is subdivided only when the trap stream proves, statistically,
which varbind names the alarmed sub-object. Nothing about the ingestion path or the v0.2.0
grouping changes until something is learned: cold start is byte-identical to v0.2.0 on every
fixture, and all 171 v0.2.0 tests still pass. One process, one SQLite file, zero new runtime
dependencies.

### Added

- **Learned entity model**: each device becomes an `ne` plus a level-0 `entity`; a bounded,
  in-engine **varbind profiler** scores every varbind by three explainable terms
  (`S = 0.35·R + 0.45·X + 0.20·D`) and promotes the discriminator only when the evidence clears
  conservative floors (score ≥ 0.60, ≥ 200 obs, ≥ 2 distinct, cardinality ratio ≤ 0.50, and a
  1.25× margin over the runner-up). Promotion is **forward-only** — history is never
  reinterpreted.
- **Containment hierarchy**: a classical functional-dependency test recovers PON port → ONU,
  chassis → card → port, and NVR → camera without a MIB or inventory. Depth is capped.
- **Learned severity, honest fallback**: a small-ordinal cross-class varbind (bundled severity
  tokens or integers) becomes the severity field only when its ordering is *validated* against
  observed alarm lifetimes; otherwise severity stays **unknown** and is rendered as unknown.
- **State-based clear**: a varbind that strictly alternates between exactly two values on a
  `(device, instance, class)` is learned as that class's state field, its terminating value the
  clear — for platforms that carry raise and clear in one trap OID.
- **Entity affinity**: `device_affinity` becomes `entity_affinity`, kept at NE level (same
  entity → 1.0, same NE → 0.8, else the learned NE×NE affinity); reduces to v0.2.0 exactly
  before any promotion.
- **SNMPv1 (RFC 3584)**: v1 traps are mapped into the pipeline (NE = the UDP source, not the
  spoofable agent address, which is exposed as a varbind); no configuration.
- **Durable ingest gaps** (§5.6): queue-full and window-overflow drops are recorded as
  `ingest_gap` rows and surfaced in `/api/stats` and a UI banner — "events lost between t1 and
  t2" as first-class NOC information.
- **Inspectable UI + admin recourse**: a viewer **Entities** tab shows the entity tree
  (`key_source`, `confidence`) and the profiler evidence behind every decision, plus learned
  state fields; situation detail gains a **severity** column; admins can reset a poisoned
  identity (`entity.reset`) or wipe the evidence (`profile.reset`) — both audited and
  forward-only-safe.

### Changed

- **Performance**: the 120 s sliding-window scan is made non-quadratic — an O(1) removal index,
  bounded candidate iteration, and an absolute `MAX_WINDOW_ALARMS` cap with oldest-first
  eviction.
- The alarm uniqueness constraint becomes `UNIQUE (entity_id, class_id, instance)`, equivalent
  at level 0 to the v0.2.0 constraint (the mechanical basis of the parity gate). `device_id` is
  retained and kept in sync for one more version.

### Removed

- **`OPTICORR_API_TOKEN`** (deprecated in v0.2.0): setting it is now a hard startup error that
  names the service-token migration path; the `legacy_token.used` audit action is retired from
  the catalog (historical rows still verify).

### Migration

- Additive migrations `0003_entity.sql` (ne/entity model, profiler, ingest-gap tables; alarm
  gains `ne_id`/`entity_id`/`severity`) and `0004_state_clear.sql`, applied automatically at
  startup (`PRAGMA user_version` → 4). Populated v0.1.0/v0.2.0 databases upgrade in place with
  all data, the append-only audit triggers, and the hash chain intact. See `MIGRATION.md`.

## [0.2.0] - 2026-07-20

Identity, role-based authorization, and a tamper-evident audit log — plus remediation of
six findings from the independent v0.1.0 security review. The ingestion path is unchanged
and still lossless; all v0.2.0 security controls live on the HTTP side.

### Added

- **Accounts and roles**: viewer / editor / admin with a single deny-by-default permission
  map (`opticorr/rbac.py`); `401` unauthenticated, `403` insufficient, `404` only after
  authorization.
- **Authentication**: `scrypt` password hashing (n=2¹⁷, upgradeable) with NIST SP 800-63B
  length policy; server-side sessions with SHA-256-stored ids, 30-minute sliding idle and
  12-hour absolute timeouts; per-username/per-IP exponential login lockout with no user
  enumeration; a bootstrap admin printed once at first start; forced password change.
- **Service tokens**: admin-created, per-identity, per-role, revocable bearer tokens
  (stored as SHA-256), shown once at creation; replace the shared static token.
- **Tamper-evident audit log**: append-only (SQLite triggers) and hash-chained; covers
  authentication, management, operator actions, and sensitive reads, including denied
  attempts; `python -m opticorr audit verify|export` tooling; dedicated 365-day retention.
- **UI v0.2.0**: login page; role-aware navigation (viewers see no mutating controls);
  situation timeline; root-cause confidence; filters/search; Server-Sent Events at
  `/api/events` (heartbeat every 15 s) as the primary live-update path with polling
  fallback; admin screens for users, tokens, config, quarantine, and the audit log. Still
  four static files, no build step.
- **Optional built-in TLS** (`OPTICORR_TLS_CERT`/`OPTICORR_TLS_KEY`) with an auto-`Secure`
  cookie; reverse-proxy TLS documented in `SECURITY.md`.
- **CSRF protection** for cookie-authenticated mutations (`Origin`/`Host` match plus an
  `X-OptiCorr-Client` header; `SameSite=Strict` cookie).
- **Operator warning banners** for an empty allowlist or a non-TLS non-loopback bind.
- **Config via the UI** (allowlist, retention) with audited changes; env defaults are
  overridden by admin-saved values.
- `docs/threat-model.md`, `docs/SCOPE-0.2.md`, `docs/SECURITY-REVIEW-0.2.md`, `SECURITY.md`,
  `MIGRATION.md`; `make migrate` and `make audit-verify` targets; a CI secret-leak scan.

### Fixed (v0.1.0 security review)

- **F1 (High)** Stored XSS: all externally sourced strings now reach the DOM via
  `textContent`/`createElement`; strict CSP with locally vendored d3; security headers.
- **F2 (High)** Shared static token and `localStorage` storage removed in favour of
  sessions and per-identity service tokens.
- **F3 (Med)** No secret is written to logs; a root-logger redaction filter and a
  secret-leak test enforce it; the bootstrap banner is the one sanctioned exception.
- **F4 (Med)** The SNMPv2c community string is never persisted or logged; an HMAC
  `community_tag` is kept for grouping and quarantine blanks or omits the community.
- **F5 (Med)** Optional TLS with an automatic `Secure` cookie.
- **F6 (Low)** Insecure-default deployment now surfaces a persistent admin banner.

### Changed

- `create_app` takes a `legacy_token` (the deprecated `OPTICORR_API_TOKEN`) mapped to a
  synthetic admin identity `legacy-token`, with a startup deprecation warning and a
  one-time audit event. **Removal is scheduled for v0.3.0.**
- Schema migrated to v2 (`0002_auth_audit.sql`): `user`, `session`, `api_token`,
  `audit_log`, plus F4 quarantine columns and an alarm `community_tag`. Forward-only;
  applies to a populated v0.1.0 database.

## [0.1.0] - 2026-07-19

First release: a zero-configuration SNMP trap correlator in one Python process, one
SQLite file, and one web UI.

### Added

- SNMPv2c trap receiver (UDP 162) with source-IP allowlist, defensive parsing, and raw
  quarantine for malformed packets — nothing can crash or block ingestion.
- Zero-config discovery: devices from source IPs, alarm classes from trap OIDs, vendor
  names from a bundled IANA enterprise-number table, standard SNMPv2/MIB-II trap
  semantics built in.
- Deduplication by (device, class, instance) fingerprint with a periodic-flapping
  detector that demotes noisy fingerprints.
- Incremental learning: class-affinity matrix A and device-affinity matrix E via
  evidence-discounted normalized PMI with exponential forgetting (λ = 0.05 per closed
  situation), an n ≥ 5 trust threshold on device edges, and 10× damped updates during
  mass storms. The learned device graph is the living topology.
- Correlation: three-term link score (temporal decay + class affinity + device
  affinity) over a 120 s sliding window; situations as connected components; the three
  terms stored on every link for auditability.
- Probable-root hints from learned temporal precedence (class- and device-level
  lead/lag statistics).
- Raise/clear pairs learned from strict alternation per (device, instance), seeded with
  linkDown/linkUp; fully cleared situations auto-close and reinforce the matrices.
- Operator feedback: confirm reinforces a grouping, split penalizes it; cosmetic,
  persisted renames for devices and classes.
- FastAPI HTTP API with static bearer-token auth (autogenerated when unset) and
  per-client rate limiting; single-file d3-force web UI with the living graph,
  situation explanations, renames, and feedback buttons.
- SQLite (WAL) storage with plain-SQL migrations, versioned learned-edge persistence,
  and retention pruning; state survives restarts.
- Tooling: real-PDU trap replay (fixtures and synthetic load), Makefile
  (qa/security/run/replay/loadtest), CI with ruff, mypy --strict, pytest + coverage,
  bandit, and pip-audit; Dockerfile (non-root) and flake.nix.
