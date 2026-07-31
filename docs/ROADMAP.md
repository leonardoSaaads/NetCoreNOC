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
