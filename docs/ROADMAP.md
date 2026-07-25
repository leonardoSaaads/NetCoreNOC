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
