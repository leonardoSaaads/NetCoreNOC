# Roadmap (post-MVP, ordered)

From `docs/SCOPE.md`:

1. SNMPv3 support (requires credentials, hence configuration — deliberately post-MVP).
2. Export to ticketing systems.
3. Automatic MIB enrichment (readable names without user obligation).
4. Precursor/gauge layer (performance-monitoring signals ahead of alarms).
5. PostgreSQL / NATS if scale demands it (queue and storage already sit behind
   interfaces).

Ideas noted during the build (one line each, per the anti-overengineering rules):

- Unlearning/expiry for learned raise/clear pairs (currently permanent once promoted).
- Second read-only SQLite connection for the API if UI latency under storms matters.
- WebSocket push for the UI instead of 2.5 s polling.
- Replay tool: read pcap captures of real trap traffic, not only JSON fixtures.
- Root-cause hint confidence score surfaced in the UI (precedence margin).
- Situation timeline view (alarms on a time axis) in the UI.

From v0.4.0 (deferred here to keep the hardening release focused):

- ~~**Remove the legacy `OPTICORR_*` environment aliases**~~ — **done in v0.6.0** (DECISIONS #45).
  Setting any `OPTICORR_*` variable is now a hard startup error naming the `NETCORENOC_*`
  replacement and `MIGRATION.md`.
- **Complete the `device_id` → `entity_id`/`ne_id` cutover** — remove the `learn.device_affinity`
  shim and the `alarm.device_id` column with a forward-only migration and a parity re-run.
  DECISIONS #35.
- **Typed relations** (physical adjacency / containment / common-cause-of-site) and **device
  archetype clustering** by emitted-class vector — re-deferred from SCOPE-0.3. DECISIONS #36.
- Situation subsumption, impact scope, situation fingerprint / recurrence.

From v0.6.0 (the scoring seam — deferred to keep the release to the scoring surface only):

- **Admin-configurable RBAC and per-role/per-principal visibility scoping → v0.7.0.** Specified in
  `docs/architecture/GOVERNANCE-0.7-DRAFT.md`. Scoping is a *presentation* control and **not**
  tenant isolation. DECISIONS #43.
- **Customer-supplied models → v0.13.0** — a blessed ONNX adapter under the v0.6.0 `LinkScorer`
  contract; `onnxruntime` an optional extra, never a base dependency. Specified in
  `docs/architecture/SCORER-PLUGINS-0.13-DRAFT.md`. DECISIONS #43, **resequenced from v0.8.0 by
  DECISIONS #93**; the Python entry-point escape hatch that line originally carried is **rejected,
  not deferred**. Chain: `docs/architecture/ROADMAP-0.8-TO-0.13.md`.
- **External-API / sidecar scoring criterion — rejected on the correlation hot path**
  (DECISIONS #44). If it ever exists it is advisory/offline only, never authoritative in
  `score()`. Recorded here as a rejection, not a plan.
- **Per-archetype weight profiles** (distinct parameter sets for PON/access vs. transport/DWDM vs.
  IP core). The `LinkScorer` seam accommodates them; they depend on device-archetype clustering
  (DECISIONS #36) and are not built.
- **X.733 / 3GPP TS 32.111 scoring features** (`probableCause`, `eventType`, `perceivedSeverity`).
  `LinkFeatures` already reserves optional `None` slots so adding them is a minor contract bump,
  not a breaking change (DECISIONS #49); populating them depends on MIB enrichment.
- **Generalised per-link attribution storage** — `link.term_t/term_a/term_e` are the *default*
  scorer's three contributions (DECISIONS #50); a scorer with a different term set needs a
  `link_term` child table or a JSON column. Additive, forward-only, and a prerequisite for the
  customer-model release (**v0.13.0**, DECISIONS #93).
- **Per-link scorer provenance.** `situation.scorer_config_id` records the configuration a
  situation was *opened* under; a long-lived situation spanning a parameter change carries its
  original id. Per-link provenance would answer the finer question.
- **"Effect of the last parameter change" report** derived from the provenance column — an
  after-the-fact companion to the before-the-fact preview.
- **Real scorer preemption** — `SafeScorer` degrades on an *over-budget* call but cannot interrupt
  a synchronous in-process call that never returns (SECURITY-REVIEW-0.6 F25, listed **partial**).
  Harmless while the only scorer is five floating-point operations; a **blocking prerequisite** for
  the customer-supplied code of **v0.13.0**, where `SCORER-PLUGINS-0.13-DRAFT.md` §R2 specifies a
  worker process with `resource.setrlimit` — and requires that its worker→parent channel not use
  `pickle`. DECISIONS #93.

From v0.7.0 (governance — deferred to keep the release to the authorization perimeter only):

- **Customer-supplied models → v0.13.0** — unchanged in substance from the v0.6.0 line above, with
  one addition recorded during v0.7.0: the **worker-process preemption harness**
  (`resource.setrlimit` + a real wall-clock kill, batch-oriented IPC) is a **blocking
  prerequisite**, not a nice-to-have. `SafeScorer` degrades the *next* call, which cannot fire on a
  plugin that never returns. See `SCORER-PLUGINS-0.13-DRAFT.md` §R2 and SECURITY-REVIEW-0.6 F25.
  Resequenced from v0.8.0 by DECISIONS #93.
- **True multi-tenant isolation** — per-tenant learning, per-tenant situation boundaries, per-tenant
  retention and audit segmentation, and the cardinality/quota accounting that goes with it. This is
  the thing v0.7.0 visibility scoping is explicitly **not**: scoping is a presentation projection
  over reads and does not partition the learned matrices or prevent a situation forming across a
  boundary. A distinct, larger feature that would change the engine, the schema, and the eval
  methodology. DECISIONS #59, SCOPE-0.7 §out-of-scope.
- **Custom roles** — admin-defined role names beyond viewer/editor/admin, and a role-authoring UI.
  Deliberately out (DECISIONS #56): a runtime-defined role has no compiled ceiling, so the first
  operand of v0.7.0's escalation-proof intersection would become stored data and the guarantee
  would collapse into a validation check. `ROLE_RANK`'s total order is also assumed by `shaping.py`
  and the UI affordance gate.
- **Per-field scoping policies** — an admin choosing which *fields* (not which NEs) a role sees.
  Field shaping stays the compiled `shaping.py` policy; v0.7.0 scoping restricts *which resources*
  are visible. DECISIONS #59.
- **External identity providers / SSO / SCIM / MFA / group-based provisioning.** Principals remain
  locally managed; a stored policy references existing local principals and the three fixed roles.
- **Scope-aware situation notifications** — if outbound emission is ever built, "who may be told
  about this situation" is a scoping question distinct from "who may read it", and the redaction
  rule (DECISIONS #59) would need an equivalent for pushed payloads.
- **Materialised scope resolution with invalidation** — v0.7.0 resolves selectors to NE ids on
  every request (DECISIONS #57), which is correct for a continuously-discovering inventory and
  cheap at this scale. If an NE table ever grows past what a per-request set operation should
  touch, a cached resolution with explicit invalidation on NE creation is the next step.
- **Per-principal scope for service tokens by token id** — implemented in v0.7.0 keyed on
  `token:<token_id>` (DECISIONS #62); a token *rotation* currently starts from an unset policy
  because the new token is a new row. Carrying a policy across a deliberate rotation would need an
  explicit "replace token, keep policy" operation.

From v0.7.1 (the write perimeter — deferred, each with the version that owns it):

- **Extract the perimeter into `src/netcorenoc/perimeter.py` — the theme of v0.7.2.** The security
  dependency, `GovernancePolicies`, `resolve_identity`, `csrf_ok`, `scope_for`, `audit_row`,
  `RateLimiter`, `DENIED_ACTION` and `write_txn` move to one flat module, leaving every route
  handler in `api.py` textually unchanged so the move is provable. Four of v0.7.1's six findings
  lived in `api.py` and were hard to find because that file is 1 700+ lines. DECISIONS #74.
- **A foreign key on `label`.** SQLite needs a table rebuild to add one, which is disproportionate
  to a patch release; the application-level existence check plus the `0007` orphan cleanup close the
  write primitive in the meantime. DECISIONS #71.
- ~~**Split `store.py` by domain and `api.py` by route group.**~~ **Both done.** `api.py` became
  the package `api/` in v0.7.2; `store.py` became `store/` in v0.7.3. Written here as "larger,
  weaker arguments than the perimeter extraction" — which was right at the time, and each
  eventually earned a version theme of its own once the perimeter work proved the parity
  discipline scaled.
- **The v0.8.0 feedback dataset** — schema, capture, and bias reporting. v0.7.1 made the *existing*
  feedback path trustworthy (idempotent, bounded, attributed); it deliberately built no part of the
  dataset. Specified in `docs/architecture/FEEDBACK-DATASET-0.8-DRAFT.md`.

From v0.7.2 (the HTTP package — deferred, each with the reason it is not in that release):

- **Make `ROUTE_SCOPE` enforcing** — have the perimeter *inject* the scope check from the declared
  posture rather than each handler calling `scope_for` itself. v0.7.2 declares the posture and
  proves every declaration matches observed behaviour; injection changes control flow, and control
  flow is behaviour, which v0.7.2 ships none of. DECISIONS #80.
- **Normalise the route paths.** Three named inconsistencies: `/api/labels` carries a `kind`
  discriminator in the body instead of being two resources; `POST /api/situations/{sid}/close` and
  `POST /api/scorer/rollback` are RPC verbs in a REST estate; `POST /api/users/{uid}/role` is a
  sub-resource where a `PATCH` on the user would do. Each is a public contract change touching
  `ROUTE_PERMISSIONS`, the generated authorization matrix, `ui/app.js` and every test. The v0.7.2
  declarative registry makes each rename a one-line change with the matrix proving the rest.
  DECISIONS #82.
- ~~**Split `store.py` and `main.py` → v0.7.3.**~~ **Done in v0.7.3.** `store/` is eighteen
  modules (largest 213 lines); `main.py` is 79 lines and the `Engine` lives in `engine.py`. One
  `Store`, one connection, one `store.lock` preserved and guarded by
  `tests/test_store_concurrency.py`; all 141 method bodies proved unchanged by hash.
  DECISIONS #83, #88–#92.
- **Split `rbac.py` (436) → v0.7.4.** v0.7.2 pushed it past the module-size guard by adding
  `ROUTE_SCOPE`, the declaration whose absence was F34. It is on the `DEBT_ALLOWLIST` with a
  named owner and a named seam: the route/capability **tables** on one side, the
  capability-policy parser and resolver on the other. DECISIONS #87.
- **Split `shaping.py` (476) and `varbind_profile.py` (417) → v0.7.4.** On the module-size guard's
  `DEBT_ALLOWLIST` with a named owner. `shaping.py` holds two axes in one file — field shaping by
  role, and NE scoping by policy — and the split is along that seam. DECISIONS #81.
- **One layer-rule violation left.** `runtime.py` imports `receiver.Network`/`parse_allowlist`
  (cross-cutting → ingest): `RuntimeConfig` holds parsed allowlist networks, so it reaches into the
  ingest layer for the parser. The fix is either moving the parser to cross-cutting, or having
  `RuntimeConfig` hold strings and letting the receiver parse. `MODULE-ARCHITECTURE.md` §1.
  ~~`main.py` → `netcorenoc.api`~~ was **resolved in v0.7.3** by separating `runner.py` from
  `engine.py`, and the rule is now enforced by `tests/test_layers.py` with an empty exemption list
  — it had a paragraph and no test for a whole release, which is why it went unfixed that long.
  DECISIONS #92.
- **`api/models.py` per route group.** Deliberately *not* done: all eleven pydantic request models
  stay in one file because fragmenting them across nine modules would make the request surface
  harder to audit, not easier. Recorded as a rejection, not a plan.
- **`receiver.py`'s coverage is timing-dependent (87–91 % across runs) → v0.7.4.** Noticed in
  v0.7.3's Gate 5 while comparing coverage between two runs of identical code: the only per-module
  line that differs is `receiver.py`. Its socket-error and backpressure branches depend on datagram
  timing, so the number moves run to run — including at the v0.7.2 baseline, before this release
  changed anything. Harmless today, but it puts noise in the one gate that is supposed to detect a
  test going quiet, which is exactly the signal Gate 3 §4.3 relied on. Make those branches
  deterministically exercised.
- **Four redundant `# nosec B608` markers in `store/retention.py` → v0.7.4.** `bandit` reports
  "nosec encountered, but no failed test" for lines 23, 27, 31 and 35. Not touched in v0.7.3:
  changing a `# nosec` on a SQL string is a change to SQL handling, and this release changed no SQL
  (SCOPE-0.7.3 §2.4). The split at least made them easy to find — four lines in a 48-line module
  instead of four lines in a 1 512-line one.

## v0.7.4 — specified in v0.7.3, built there

- **Close the two declaration-gate holes.** Found by adversarial probing of `api/declare.py` and
  **confirmed by execution**; neither is exploited today. (1) `DeclaredRoutes` wraps `get`, `post`
  and `delete` only, and only the decorator form — `app.add_api_route("/api/x", handler,
  methods=["GET"])` registers successfully without ever calling `require_declaration`, and the
  route appears in the table. (2) The exemption is by path *prefix*, so a future authenticated
  non-`/api` route — `/metrics` is already on this list — would be exempt by accident. Specified in
  `MODULE-ARCHITECTURE.md` §10.1, including **why the post-hoc assertion over the built app is the
  right fix**: it is complete by construction rather than by enumeration. Deferred from v0.7.3
  because fixing a security-adjacent guard inside a move release forfeits the parity story for a
  latent gap.
- **Split `shaping.py` (476), `rbac.py` (436) and `varbind_profile.py` (417).** The whole remaining
  `DEBT_ALLOWLIST`, with the seams already named in `MODULE-ARCHITECTURE.md` §5 and §10.2. All three
  are small enough that v0.7.3's mixin mechanism is probably overkill — measure before choosing.
- **Four redundant `# nosec B608` markers** at `store/retention.py:23,27,31,35`. `bandit` reports
  "nosec encountered, but no failed test". Untouched in v0.7.3 because changing a `# nosec` on a
  SQL string is a change to SQL handling, and that release changed no SQL.
- **`engine.py` stays `COHESION_EXEMPT`, permanently.** Not a task. Recorded here so nobody reads
  the empty debt allowlist as an invitation to "finish the job" — the entry has no owner and no
  date because the invariant it cites ("ingestion is sacred") has no expiry.
- **v0.8.0 is the next feature release** — the operator-feedback dataset. The v0.7.x series is not
  open-ended: v0.7.3 was the last structural release.

## v0.7.5 — specified in v0.7.4, built there

- **Fix the operator-feedback acquisition path.** The SSE update rebuilds every situation card every
  two seconds, including the one the operator has expanded, and the rebuilt detail is filled only
  after a network round trip — so there is a window in which the card is visibly empty and a click
  lands on a detached node. The failure that matters is not the flicker: a click can be recorded
  against a membership the operator never evaluated, which is a **silently wrong label**, and
  nothing downstream can detect one. Specified in
  `docs/architecture/FEEDBACK-PATH-0.7.5-DRAFT.md`. It is a prerequisite for v0.8.0, because the
  feedback click is the only source of training labels.

## v0.8.0 onward — the chain

<!-- release-claim: v0.8.0 = operator-feedback-dataset -->

**v0.8.0 is the operator-feedback dataset** — capture the feedback as a durable dataset and measure
its bias; it trains nothing. The releases after it, and **why the order cannot be permuted**, are
written down once in [`architecture/ROADMAP-0.8-TO-0.13.md`](architecture/ROADMAP-0.8-TO-0.13.md),
which is the single source of truth for what each release from v0.8.0 to v0.13.0 is:
v0.9.0 shadow mode → v0.10.0 the honest judge → v0.11.0 champion/challenger →
v0.12.0 archetypes (*likely, review before committing*) → **v0.13.0 the external cartridge**
(ONNX, resequenced here from v0.8.0 by DECISIONS #93).

Until v0.7.4 this file said **both** that v0.8.0 was customer-supplied models (twice) and that it
was the operator-feedback dataset (twice). DECISIONS #93 records the resequencing that settles it,
and `tests/test_documentation.py` now fails if the repository ever again gives two answers to
"what is release X".

From v0.7.4 (the last loose ends — deferred, each with the reason):

- **`/openapi.json` is served unauthenticated.** Noticed while building F41's allowlist: the schema
  route is registered by FastAPI itself and carries no security dependency, so the full API surface
  is readable without an identity. Listed in `declare.UNAUTHENTICATED_PATHS` because that set states
  what *is* served, not what should be. Whether to authenticate it, or disable `openapi_url` as
  `docs_url` and `redoc_url` already are, is a public-contract question and not a placement one —
  and v0.7.4's parity story forbids changing a served path. SECURITY-REVIEW-0.7.4 §critical analysis.
- **`test_add_api_route_is_confined_to_the_static_asset_allowlist` counts mentions, not calls.** It
  greps the text of every module under `api/` for the identifier and asserts exactly one file
  contains it, so naming the function in a docstring makes the count wrong. Writing the v0.7.4 gate
  fix tripped it and the prose was reworded rather than the test. An AST-based caller count would be
  a few lines and would say what the test means.
- **`ROUTE_SCOPE` is still descriptive, not enforcing.** Unchanged from v0.7.2 (DECISIONS #80): the
  declaration gate is now complete for *registration*, but completeness of a guard is not
  correctness of what it guards. Every `ROUTE_SCOPE` entry remains a human judgement checked against
  observed behaviour rather than a check the perimeter injects.

From v0.8.0 (deferred, each with the reason):

- **The partial-split affordance — *"these three yes, that one no"*.** **The single
  highest-leverage UI change for the whole ML roadmap.** A `confirm` is an all-positive bag and is
  usable pairwise; a `split` is a bag with *at least one negative* and licenses **nothing** about
  any individual pair (FEEDBACK-DATASET-0.8-DRAFT §3.3a). Letting the operator name which members
  do not belong converts the weak half of the dataset into the strong half, at the source, and no
  modelling cleverness in v0.9.0–v0.13.0 recovers it afterwards. Out of v0.8.0 because that release
  permits exactly one `ui/app.js` change and the UI rebuild is a later release.
- **The merge chain is recorded from v0.8.0 forward, never backwards.** `0008` adds the merge edge
  so lineage is recoverable, but merges that happened before the upgrade are gone — the destination
  was never written. Every pre-upgrade label whose situation was later absorbed keeps an
  unrecoverable referent, and no migration can reconstruct one.
- **The sink's 21-day default is a conservative guess and is documented as one.** v0.8.0 measures
  real label latency; a later release should replace the guess with the measured distribution
  rather than defending the number.
- **The bias report is a CLI subcommand, not a screen.** Deliberate — a deterministic CLI report can
  be a byte-for-byte gate in `make qa` and a UI card never can. It becomes a screen when the UI is
  rebuilt, and the gate should survive the promotion.
- **`incumbent_linked`'s class balance is a property of the traffic.** Measured at 0 % accept on
  quiet corpus traffic and 100 % in a storm. Anything reasoning about it — a sampling strategy, a
  class weight, a threshold — must condition on the traffic profile or it is fitting the weather.

## v0.7.5 — specified in v0.7.4, built there

Everything the release itself deferred, each with the reason it is not in it:

- **Testability as a design input for the UI rebuild.** v0.7.5 changed three things in `ui/app.js`
  and could prove none of them automatically: there is no JavaScript runtime in this repository and
  every UI assertion is a source-inspection test (DECISIONS #99). The behavioural claims rest on
  `docs/gates/v0.7.5-manual-verification.md`, executed by hand.
  Adding a JS harness *now* would be the largest dependency decision since v0.2.0, taken inside a
  patch release, to test three lines. **The planned UI rebuild is the point at which testability
  should be a design input** — decide the runtime and the reconciliation model together, so the
  tests come from the architecture rather than being retrofitted to it. That is the honest place to
  reopen this, and not before.
- **Whether to also pin FastAPI to an upper bound**, or to carry a lockfile / constraints file in
  CI. F42 regressed between `fastapi==0.115.0` and `0.141.1` with no commit and no failing test.
  v0.7.5 added a guard that *notices* a route-representation change on the day of the upgrade,
  naming the new class, which is strictly better than a pin that freezes one — but the two are not
  the same guarantee and the project currently has neither a pin nor a lockfile for any of its five
  runtime dependencies. A supply-chain policy question, not a route-gate one. DECISIONS #101.
- **The route-shape allowlist detects a new shape, not a changed meaning.** If a future `APIRoute`
  carried its verbs somewhere other than `.methods`, the shape set would be unchanged and the gate
  would quietly check nothing. No test in v0.7.5 closes this; it is the residual recorded in
  SECURITY-REVIEW-0.7.5 §critical analysis.
- **The documentation guard's forbidden-phrase half remains enumeration**, and remains
  spelling-sensitive (`->` versus `→`). v0.7.5 took its element-tag half from 31% visibility to
  near-complete; the phrase half is correct by design as the specific-case belt and is *not* a
  general guarantee. Restated so nobody mistakes the new figure for one.
- **`renderEntityDetail` has the clear-then-fill shape v0.7.5 fixed in `renderDetail`.**
  `ui/app.js:583` clears the container before its `await api(...)` resolves, so the entities panel
  has the same visible-and-empty window §5.2 closed for situations. **Found while repairing the
  acquisition path and deliberately not fixed**: it is not on the label path, it carries no
  label-integrity consequence, and a fix smuggled into a small diff is invisible to review — which
  is the whole reason that diff is small (v0.7.5 directive 6). The same `DocumentFragment` swap
  applies verbatim when someone picks it up.
- **A situation id stays in the `expanded` set after its card leaves the list.** Pre-existing and
  harmless — the set is bounded by what one operator opens — but it means "expanded" is really
  "expanded, or was once expanded and has since disappeared". Noted, not fixed.
- **`ROUTE_SCOPE` is still descriptive, not enforcing**; `/openapi.json` is still served
  unauthenticated; `test_add_api_route_is_confined_to_the_static_asset_allowlist` still counts
  mentions rather than calls. All three carried forward from v0.7.4 unchanged — v0.7.5's scope was
  five workstreams and none of them was these.

From v0.8.1 (found while governing the dataset's lifecycle; **not fixed**, per that release's
directive that a defect discovered inside the changed code is a roadmap line and not a fix):

- **`Capture.warnings()` is never surfaced to the operator.** `capture.py` builds a
  `db_error_warnings`-shaped message after a capture write fails, and `tests/test_dataset.py`
  asserts it — but `runner.py`'s warnings lambda never calls it, so a degraded capture is counted,
  logged, and invisible on `/api/stats`. One line in `runner.py` beside
  `engine.db_error_warnings()`. Deliberately left: it is not on the label path and v0.8.1's value
  is the size of its diff.
- **The sink's row cap almost certainly governs, and nobody has measured it in the field.** At
  ~62 pair rows per trap the 2 000 000-row default is exhausted in ~9 hours at 1 trap/s, so most
  deployments have hours of labelling window, not the 21 days `sink_days` advertises. v0.8.1
  documented this honestly and changed nothing, because changing it is a design decision with data
  behind it and the data is what v0.9.0 will have. Whoever raises the cap should also decide what
  `sink_days` is *for* once it can never bind.
- **One label promotes an entire storm's sink.** 45 050 rows from one verdict on `olt_storm.json`.
  The promotion rule is correct — every promoted pair had both ends inside the labelled bag — but
  it means the corpus is bounded by *labels × situation size*, so a single storm label can dominate
  the training set. Weighting, capping promotion per label, or sampling within a bag are all
  options; all three are modelling decisions and belong with the release that trains.
- **DECISIONS #108's prose says `engine.py` "may not exceed 565 lines"** where the recorded ceiling
  is 580. A typo in an ADR's summary sentence, not in the guard, which has always read 580. Left
  alone because ADR text is a record of what was decided and is corrected in place, not edited
  silently — and no release since has needed to touch that entry.

## Found while building v0.9.0 (one line each, not this release's work)

- **`MIN_INCIDENTS_FOR_INTERVAL = 10` is too permissive** in the champion-agreement report — twelve
  incidents printed `[33.3, 91.7]`, a range wide enough to contain any conclusion; thirty would have
  printed `n/a`.
- **`shadow_eval.evaluate` namespaces component ids with `hash()`** — stable in CPython for integer
  tuples and verified across processes, but a documented `(bag, component)` tuple key would not
  depend on that.
- **`situation.merged_into` is resolved one hop, not transitively.** A merge chain longer than one
  hop resolves to the wrong incident, and no code prevents a cycle. v0.10.0's split-by-incident needs
  a fixed-point walk with a cycle guard — recorded in `HONEST-JUDGE-0.10-DRAFT.md` §2.
- **`scorer_config` has no general parameter blob**, only the additive scorer's five columns, so a
  learned coefficient vector has nowhere to live in it. A fact about v0.11.0's promotion path, found
  in v0.9.0's Phase 0.
- **`shadow_opinion` is bounded by a row cap and no age bound**, so a deployment whose traffic falls
  keeps old opinions indefinitely. Deliberate — they hold no label and join to none — but a reader
  auditing "what deletes what" should find it written down.

## Found while building v0.9.1 (one line each, not this release's work)

Every item below is a **miss from the seeded-defect audit**
([`gates/v0.9.1-test-audit.md`](gates/v0.9.1-test-audit.md)) that this release deliberately did not
close, because its themes do not touch it. A patch release that fixed everything it found would not
be one, and its diff could not be read in one sitting.

- **The authorization perimeter fails *open* on an undeclared route, and nothing tests it.** Seed A4
  inverted `if permission is None or permission not in capabilities` so that a route with no
  declared capability is **allowed**, and all 958 tests stayed green. The declaration gate should
  make `permission is None` unreachable in a built application — so this is the *second* layer of a
  two-layer defence, untested. The first layer's own seeds (D1, D2) were caught.
- **Three retention behaviours nothing pins.** Seeds R2, R3 and R4: the sink's row cap deleting
  **newest**-first instead of oldest-first; the audit tier losing its `lifecycle='dataset'` clause;
  and label deletion shifting `<` to `<=`. The tier that could destroy a promoted corpus **is**
  guarded (R1 was caught) — the ordering and the boundaries within each tier are not.
- **The coverage classification is off by one and no fixture notices.** Seed P2 changed
  `promoted >= expected` to `>= expected - 1`, reclassifying `partial` bags as `full`. The bias
  fixture's bags are too small for the boundary to land where it matters. The most likely of the
  twelve misses to bite silently, because `coverage` is a label-quality field v0.10.0 will filter on.
- **Two bounds can be widened by orders of magnitude unnoticed.** Seeds C2 (`capture.py`'s
  observation buffer, ×4 → ×40) and H1 (`SHADOW_MAX_ROWS`, 200 000 → 200 000 000). Both are bounds
  whose only consequence is memory under storm, and no test reaches either.
- **The skew comparison is still blind to a feature divergence that does not move the score.**
  v0.9.1 pinned the *aliasing convention* with one assertion after seed S1 confirmed
  `SECURITY-REVIEW-0.9.0.md`'s prediction by execution. The deeper half — comparing the served
  features against the offline ones rather than only the scores — needs the comparison redesigned,
  which is v0.10.0's.
- **The cluster-bootstrap interval arithmetic is exercised by no gate.** Every report fixture sits
  below `MIN_INCIDENTS_FOR_INTERVAL`, so every frozen expectation prints `[interval n/a]`. When a
  real corpus crosses that threshold the intervals appear in production having never been compared
  against a frozen value. Nothing breaks; the coverage was simply never there.
- **`app.js` is never executed by the test suite.** A thousand lines of behaviour — the held card,
  the atomic detail swap, the reconciler, and v0.9.1's exclusion checkboxes — tested only by string
  search. The defects v0.7.5 existed to fix were *composition* defects between individually correct
  lines, which no string search can see.
- **No test exercises a migration that fails half-applied.** `lifecycle.py` runs a script and *then*
  sets `user_version`, so a crash mid-script leaves a database whose version says "not applied"
  while some statements have landed. Whether `executescript`'s implicit transaction saves this is a
  fact nobody in this repository has checked.
- **Nothing runs the ingest batch loop and a stream of API writes against each other.** The
  invariants are proved and the ingest path is load-tested alone; the property at risk is the
  **latency of the batch under API pressure**, which an operator experiences as a NOC that stops
  keeping up during an incident — exactly when they are also clicking Confirm and Split.
- **`test_f39_every_mutating_handler_uses_the_transaction_helper` sells a source scan as a
  behavioural guarantee.** Its reachability assertion is correctly shaped; two of its assertions
  restate a property `test_store_concurrency.py` already tests behaviourally, and its docstring does
  not say which is which.
