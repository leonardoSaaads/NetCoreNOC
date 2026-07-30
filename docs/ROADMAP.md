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
- **Customer-supplied models → v0.8.0** — a blessed ONNX adapter and a Python entry-point escape
  hatch under the v0.6.0 `LinkScorer` contract; `onnxruntime` an optional extra, never a base
  dependency. Specified in `docs/architecture/SCORER-PLUGINS-0.8-DRAFT.md`. DECISIONS #43.
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
  `link_term` child table or a JSON column. Additive, forward-only, and a v0.8.0 prerequisite.
- **Per-link scorer provenance.** `situation.scorer_config_id` records the configuration a
  situation was *opened* under; a long-lived situation spanning a parameter change carries its
  original id. Per-link provenance would answer the finer question.
- **"Effect of the last parameter change" report** derived from the provenance column — an
  after-the-fact companion to the before-the-fact preview.
- **Real scorer preemption** — `SafeScorer` degrades on an *over-budget* call but cannot interrupt
  a synchronous in-process call that never returns (SECURITY-REVIEW-0.6 F25, listed **partial**).
  Harmless while the only scorer is five floating-point operations; a **prerequisite** for v0.8.0's
  customer-supplied code, where `SCORER-PLUGINS-0.8-DRAFT.md` specifies a worker process with
  `resource.setrlimit`.

From v0.7.0 (governance — deferred to keep the release to the authorization perimeter only):

- **Customer-supplied models → v0.8.0** — unchanged in substance from the v0.6.0 line above, with
  one addition recorded during v0.7.0: the **worker-process preemption harness**
  (`resource.setrlimit` + a real wall-clock kill, batch-oriented IPC) is a **blocking
  prerequisite**, not a nice-to-have. `SafeScorer` degrades the *next* call, which cannot fire on a
  plugin that never returns. See `SCORER-PLUGINS-0.8-DRAFT.md` §R2 and SECURITY-REVIEW-0.6 F25.
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
- **Split `store.py` by domain and `api.py` by route group.** Larger, weaker arguments than the
  perimeter extraction; they stay lines here rather than becoming a version theme.
- **The v0.8.0 feedback dataset** — schema, capture, and bias reporting. v0.7.1 made the *existing*
  feedback path trustworthy (idempotent, bounded, attributed); it deliberately built no part of the
  dataset.

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
- **Split `store.py` and `main.py` → v0.7.3.** Fully specified in
  `docs/architecture/MODULE-ARCHITECTURE.md` §6–§8, including the invariants (one `Store` class, one
  connection, one `store.lock`; the batch lock never leaves `Engine`), the two candidate mechanisms
  for `Store` with the `mypy --strict` cost of each, and the gates v0.7.3 inherits. DECISIONS #83.
- **Split `rbac.py` (436) → v0.7.4.** v0.7.2 pushed it past the module-size guard by adding
  `ROUTE_SCOPE`, the declaration whose absence was F34. It is on the `DEBT_ALLOWLIST` with a
  named owner and a named seam: the route/capability **tables** on one side, the
  capability-policy parser and resolver on the other. DECISIONS #87.
- **Split `shaping.py` (476) and `varbind_profile.py` (417) → v0.7.4.** On the module-size guard's
  `DEBT_ALLOWLIST` with a named owner. `shaping.py` holds two axes in one file — field shaping by
  role, and NE scoping by policy — and the split is along that seam. DECISIONS #81.
- **Two layer-rule violations, named not fixed.** `main.py` imports `netcorenoc.api`
  (engine → http, the one genuine upward import; resolved naturally by v0.7.3's separation of the
  process runner from `Engine`), and `runtime.py` imports `receiver.Network`/`parse_allowlist`
  (cross-cutting → ingest). `MODULE-ARCHITECTURE.md` §1. A violation found while writing an
  architecture document is a line here, not a fix in the release that found it.
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
