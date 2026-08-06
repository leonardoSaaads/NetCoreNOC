# Changelog

All notable changes to this project are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
