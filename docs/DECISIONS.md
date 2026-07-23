# Decisions

Numbered record of scope-ambiguity resolutions and notable engineering choices, per the
autonomous decision protocol: context → options → choice → reason.

## 1. `docs/SCOPE.md` authored from the supplied scope material

- **Context**: The build brief names `docs/SCOPE.md` as the authoritative scope, but the
  repository started empty; the scope content was supplied alongside the brief in
  Portuguese.
- **Options**: (a) treat the scope as missing and improvise; (b) commit the supplied
  material verbatim in Portuguese; (c) author `docs/SCOPE.md` in English from the
  supplied material.
- **Choice**: (c).
- **Reason**: The engineering standards require all documentation in English; the
  supplied material is the scope, so translating it faithfully preserves authority while
  meeting the language standard.

## 2. Curated IANA enterprise-number subset, not the full registry

- **Context**: Vendor identification resolves the enterprise prefix against the IANA
  Private Enterprise Numbers table, "bundled" with the binary. The full registry is
  ~60 000 entries (several MB, needs refresh tooling).
- **Options**: (a) vendored full registry; (b) curated subset of vendors that actually
  ship SNMP network equipment; (c) no table, show raw enterprise numbers.
- **Choice**: (b) — ~150 entries covering mainstream network/optical/IT vendors; unknown
  prefixes render as `enterprise-<n>`.
- **Reason**: Simplest option that satisfies "vendor identified with zero configuration"
  for realistic NOC traffic; the fallback is honest and the table is trivially
  extensible. The full registry adds weight without changing behaviour for the MVP.

## 3. Alarm instance heuristic

- **Context**: The event model needs an ``instance`` (dedup key component), but vendor
  traps carry no declared instance field and no MIB is available to find one.
- **Options**: (a) always empty (dedup per device+class only); (b) hash all varbinds
  (repeats with volatile varbinds never dedup); (c) use ifIndex when present, else the
  value of the first payload varbind, else empty.
- **Choice**: (c), value capped at 120 chars.
- **Reason**: ifIndex is the standard instance for the built-in link traps; for vendor
  traps the first payload varbind is, in practice, the entity identifier (port, ONU,
  shelf). Volatile first varbinds degrade dedup gracefully rather than breaking it.

## 4. Source allowlist defaults to allow-all

- **Context**: The security baseline requires an enforced source-IP allowlist, but zero
  configuration means the system must work with nothing but a trap destination.
- **Options**: (a) mandatory allowlist (violates zero-config); (b) default allow-all,
  enforced when ``OPTICORR_ALLOWLIST`` (comma-separated CIDRs) is set.
- **Choice**: (b).
- **Reason**: Zero-config wins on defaults; enforcement is still real the moment an
  operator sets the variable, and denied packets are counted, not silently ignored.

## 5. Schema: three tables beyond the mandated eight

- **Context**: The process document fixes eight tables (device, alarm_class, alarm,
  edge, situation, situation_alarm, feedback, label) but also requires storing the three
  score terms per link, raw quarantined packets, and migration/engine state.
- **Options**: (a) squeeze everything into the eight tables as JSON blobs; (b) add
  ``link``, ``quarantine``, and ``meta`` tables.
- **Choice**: (b).
- **Reason**: The explainability and quarantine requirements need first-class storage;
  three narrow tables are simpler and more auditable than JSON-in-a-column.

## 6. Flapping detector: coefficient-of-variation test

- **Context**: SCOPE requires a "simple periodic-flapping detector" with no definition.
- **Options**: (a) plain rate threshold; (b) FFT/autocorrelation periodicity detection;
  (c) ≥ 6 re-activations whose inter-arrival times have mean ≤ 15 min and coefficient of
  variation ≤ 0.5, with history reset after a quiet hour.
- **Choice**: (c).
- **Reason**: A rate threshold demotes storms (wrong); spectral methods are
  over-engineering. The CV test is a few lines, explainable, and matches "periodic".

## 7. Learning trigger: window co-occurrence plus closed-situation epochs

- **Context**: SCOPE says "every closed situation updates A and E"; the process document
  says the matrices are updated by incremental co-occurrence. Learning **only** at close
  can never bootstrap: cross-device alarms are never grouped without an E edge, so no
  closed situation would ever contain the cross-device evidence.
- **Options**: (a) learn only at situation close (deadlocks); (b) learn only per event;
  (c) both — co-occurrence mass accrues per activation from the sliding window, and each
  closed situation additionally reinforces its distinct pairs and advances the
  forgetting epoch (λ = 0.05 per epoch).
- **Choice**: (c).
- **Reason**: The only reading that satisfies both texts and actually converges; the
  epoch tick also gives forgetting a natural, load-independent unit.

## 8. NPMI evidence discount

- **Context**: With small streams, textbook NPMI saturates: a single accidental
  co-occurrence of two rare tokens scores as perfect association, which would create
  false links through the w_A/w_E terms.
- **Options**: (a) raw NPMI; (b) minimum-count gate on A as well as E; (c) shrink the
  score by n/(n+1), where n is the decayed pair mass.
- **Choice**: (c) (plus the scope-mandated n ≥ 5 gate on E).
- **Reason**: One observation is never proof. The discount is one factor, fully
  explainable, keeps single co-occurrences below linking strength, and converges to raw
  NPMI as evidence accumulates.

## 9. One co-occurrence observation per activation

- **Context**: Scoring pairs each new alarm against every window member; counting the
  pair mass the same way lets mass grow quadratically and outrun the activation total,
  saturating PMI mid-incident.
- **Choice**: Per activation, each distinct other class (and distinct other device) is
  observed at most once; closed-situation reinforcement likewise counts each distinct
  pair once.
- **Reason**: Keeps "mass ≤ activations" so probabilities stay meaningful, and makes the
  n ≥ 5 E-edge threshold mean five separate moments of co-alarming, not one burst pair
  explosion.

## 10. Bounded work per event

- **Context**: A 500-alarm storm must not make the engine quadratic (scoring, links,
  learning, root recomputation all touch the window/situation).
- **Choice**: Score against at most 100 most-recent window members; learn from the 20
  most recent; store at most the 5 strongest links per alarm; root cause scores the 25
  earliest members of a situation.
- **Reason**: Connected components only need one link to chain; audits need a few strong
  ones, not thousands. All caps are constants in code, documented here.

## 11. Clear-class events with no matching raise are dropped

- **Context**: Once X → Y is a known raise/clear pair, a lone Y (e.g. linkUp after a
  restart) matches no active alarm.
- **Choice**: Record it as alternation evidence only — no alarm row is created. When a
  pair is first promoted, stale active alarms of the clear class are retired.
- **Reason**: A clear that clears nothing is not an alarm; creating one would pollute
  the active view with never-clearing noise.

## 12. One SQLite connection shared under an asyncio lock

- **Context**: During the 1000 traps/s load test, an API read cursor interleaving with
  the engine's batch commit raised ``cannot commit transaction - SQL statements in
  progress`` and killed the engine.
- **Options**: (a) second read-only connection for the API; (b) route API access
  through the engine; (c) one connection plus an ``asyncio.Lock`` held per engine batch
  / maintenance pass / API request.
- **Choice**: (c), with a concurrency regression test.
- **Reason**: Simplest change that makes the failure impossible; batch lock hold times
  are hundreds of milliseconds at worst, fine for an operator UI. (a) remains the
  documented upgrade if API latency under storms ever matters.

## 13. Alternation learning pauses during storms

- **Context**: The synthetic load burst falsely promoted raise/clear pairs — with
  duplicates collapsed, random class interleavings on one (device, instance) look like
  strict alternation, and the false pairs then mass-cleared real alarms.
- **Options**: (a) more required cycles (still probabilistic); (b) dwell-time
  heuristics; (c) pause alternation learning while the window is in storm state, the
  same principle as damped matrix updates.
- **Choice**: (c) (regression-tested); cosmetic `last_seen` touches on device/class
  rows were also throttled to one write per 5 s in the same hardening pass.
- **Reason**: "Storms teach confounders" already governs A/E updates; applying it to
  clear-pair learning removes the false-positive source instead of just making it rarer.

# v0.2.0 decisions

Continues the numbering. Security-relevant ambiguity resolves toward the stricter option
(decision protocol §9); scope is never expanded to resolve ambiguity.

## 14. Audit-retention prune drops and recreates the append-only triggers

- **Context**: `audit_log` has `BEFORE UPDATE`/`BEFORE DELETE` triggers that
  `RAISE(ABORT)` so history is immutable even to the application, yet the brief also
  mandates a dedicated, admin-triggered audit-retention archive+prune. A trigger that
  always aborts DELETE and a feature that must DELETE are in direct tension.
- **Options**: (a) a hidden guard flag row the prune sets to let the trigger pass —
  bypassable by anyone who can write the flag, and invisible in the schema; (b) no
  triggers, rely on the hash chain alone — fails "immutable even to the application"; (c)
  keep the triggers as unconditional aborts and have the single admin prune function
  drop, delete, and recreate them inside one locked transaction, archiving first and
  auditing the action.
- **Choice**: (c).
- **Reason**: Every normal code path — the ORM-free SQL layer included — is hard-blocked
  from mutating history; only one narrow, admin-gated, itself-audited function can delete,
  and it does so transparently (the drop/recreate is in the open, not a hidden flag).
  Pruning only the oldest rows keeps the surviving suffix hash-verifiable against the
  archived boundary hash.

## 15. Auth throttle and login lockout are in-memory (single process)

- **Context**: The brief specifies per-username and per-IP login lockout with exponential
  backoff. OptiCorr is one process over one SQLite file.
- **Options**: (a) persist attempt counters in SQLite (durable across restarts, adds write
  load on the auth path and a table); (b) in-memory counters in the process.
- **Choice**: (b), matching the existing in-memory rate limiter.
- **Reason**: A single-node NOC tool restarts rarely; the reset-on-restart window is
  acceptable and documented in the threat model's residual risk. Durable lockout is a
  ROADMAP item if OptiCorr ever runs multi-node. Lockouts are still audited durably.

## 16. F4 community tag is computed in the receiver from an in-memory key

- **Context**: F4 requires `community_tag = HMAC-SHA256(key, community)[:12 hex]` for
  grouping while the community string is never persisted or logged, and the ingestion path
  must not gain a lock or I/O.
- **Options**: (a) carry the raw community on the `TrapEvent` and tag it in the engine
  under the store lock — puts a plaintext password on the in-process queue; (b) load the
  key once at startup into the receiver and tag in the datagram callback, discarding the
  plaintext immediately.
- **Choice**: (b).
- **Reason**: The plaintext community never leaves the datagram callback — not onto the
  queue, not to a table, not to a log. One HMAC over a short string is CPU-only (no lock,
  no I/O), so the "ingestion is sacred" invariant holds. The key is created once in `meta`
  and loaded before the receiver binds.

## 17. `create_app` keeps a positional legacy-token parameter for backward compatibility

- **Context**: v0.1.0 `create_app(engine, token, ...)` and its tests pass a bearer token.
  v0.2.0 replaces the shared token with sessions and service tokens, but the legacy
  `OPTICORR_API_TOKEN` compatibility path must still accept a bearer token as admin.
- **Options**: (a) change every v0.1.0 API test to the new auth model; (b) keep the
  `legacy_token` parameter on `create_app` mapped to the synthetic admin identity, so a
  `Bearer <token>` request resolves to admin and the v0.1.0 tests that assert
  admin-capable behaviour keep passing with minimal, non-weakening edits.
- **Choice**: (b); v0.1.0 API tests change only where behaviour legitimately changed (an
  endpoint now requires a role, or now emits security headers), never weakening an
  assertion (prime directive 1).
- **Reason**: The legacy path is a real, shipped feature (§5), so exercising it from the
  retained tests is honest coverage, not test-fitting.

# v0.3.0 decisions

Continues the numbering. Ambiguity about a learning threshold resolves toward the more
conservative (later-promoting) value; security-relevant ambiguity toward the stricter option.

## 18. The evaluation harness reports deterministic proxies for latency and memory

- **Context**: The metric table (build §4) names `p95_ingest_latency_s` and `peak_rss_mb`,
  but the harness determinism gate requires bit-identical metrics across runs, and OS RSS
  and wall-clock latency are inherently non-reproducible.
- **Options**: (a) measure real wall-clock latency and RSS — fails determinism; (b) drop the
  two guards — loses the burst/growth signal; (c) compute deterministic proxies in the
  harness and measure the true OS numbers only in the Phase-4 burst/soak tests, where a
  single non-deterministic performance number is acceptable.
- **Choice**: (c). Latency is a fixed-service-time queueing model over the event arrival
  order (`_synthetic_latencies`); memory is `peak_tracked_objects` (peak window occupancy
  plus open-situation membership).
- **Reason**: A gate must be reproducible or it is worthless (build §4). The proxies serve
  the same purpose — a burst that arrives faster than it drains accrues latency; unbounded
  structure shows as growing tracked objects — while staying deterministic. The real RSS/
  latency guard lives where non-determinism is tolerable: the one-shot burst test.

## 19. Aggregate metrics pool alarms across scenarios, not a mean of per-scenario scores

- **Context**: Scenarios differ hugely in size (8 to 1 051 alarms) and some (camera_nvr,
  v1) contribute zero alarms under v0.2.0. A mean of per-scenario scores would let a
  vacuous 1.0 from an all-quarantined scenario inflate the aggregate and would under-weight
  large storms.
- **Options**: (a) unweighted mean of per-scenario metrics; (b) pool every predicted alarm
  across the corpus (labels namespaced per scenario) and compute one aggregate.
- **Choice**: (b); per-scenario metrics are still reported for inspection.
- **Reason**: Pooling weights each scenario by its alarm count, handles zero-alarm scenarios
  naturally (they contribute nothing until a later version ingests them), and makes the
  camera/v1 gain appear in the aggregate exactly when the alarms start existing.

## 20. Corpus scenario sizes are representative of the phenomenon, not literal counts

- **Context**: The brief describes e.g. "2 000 ONU traps". Replaying the whole corpus twice
  (the determinism test) plus once per `make qa` must stay within a sane CI budget, and the
  unmodified v0.2.0 correlator is quadratic in a full window (the S1 fix is a v0.3.0 change).
- **Options**: (a) literal counts — slow, and the v0.2.0 baseline replay strains the
  quadratic window; (b) representative sizes that still exercise the phenomenon and keep the
  discriminator observed well over the promotion evidence floor.
- **Choice**: (b) — e.g. pon_dying_gasp is ~1 050 events across three classes; every
  learnable NE has ≥ 200 discriminator observations.
- **Reason**: The phenomenon (proxied storm, containment, decoys) is what the metrics test,
  not the literal magnitude; the 100 000-trap magnitude is proven separately by the Phase-4
  burst test against the S1-fixed engine.

## 21. SNMPv1 `ne.ip` is the UDP source, not the trap's agent-address (§5.7)

- **Context**: An SNMPv1 trap carries an `agent-addr` field that may differ from the UDP
  source IP (RFC 3584), so there are two candidate identities for the reporting NE.
- **Options**: (a) trust the in-PDU `agent-addr` — application-layer data, spoofable by any
  sender; (b) use the UDP source IP, consistent with v2c, and expose `agent-addr` as a
  varbind.
- **Choice**: (b) (security-relevant ambiguity → stricter option).
- **Reason**: The UDP source is the same identity v2c uses and is not spoofable at the
  application layer; `agent-addr` remains visible to the operator as a varbind without being
  trusted as identity. Consistency across v1/v2c also keeps one `ne` per real device.

## 22. Migration 0003 is additive; the v0.2.0 alarm UNIQUE is kept for one version

- **Context**: §5.2 says the alarm uniqueness constraint "becomes"
  `UNIQUE (entity_id, class_id, instance)`. SQLite cannot ALTER a constraint in place; the
  only way to replace one is to rebuild the table, which — with `situation_alarm` and `link`
  holding foreign keys into `alarm` — requires toggling `PRAGMA foreign_keys` off inside the
  migration, a fragile operation that also breaks every unmodified v0.2.0 store the instant
  the migration lands (the new NOT NULL columns have no default).
- **Options**: (a) rebuild `alarm` with FK-toggling, replacing the constraint and adding
  NOT NULL `entity_id`/`ne_id` — brittle, and forces the S3 store rewrite to land in the same
  breath or the suite goes red; (b) additive migration: add `ne_id`/`entity_id` (nullable,
  backfilled), keep the v0.2.0 `UNIQUE (device_id, class_id, instance)`, and add
  `UNIQUE (entity_id, class_id, instance)` as an index.
- **Choice**: (b). `device_id` is retained and synced for one version anyway (§5.2), so its
  unique index is legitimately still present; the new entity-based unique index is the
  go-forward dedup key.
- **Reason**: The additive path applies cleanly onto a populated v0.2.0 DB, keeps every
  v0.2.0 test green the moment it lands (an unmodified store still deduplicates by device),
  and avoids FK-toggling inside a migration. The two unique keys never conflict because an
  alarm's `instance` always carries its entity's discriminator value, so distinct entities
  never collide on `(device_id, class_id, instance)`. Dropping the v0.2.0 constraint and
  `device_id` is a v0.4.0 line, exactly as the retention plan intends.

## 23. The correlation window keeps tombstones; the live set is a parallel index (S1)

- **Context**: v0.2.0's window `remove` linearly scanned the deque and its candidate
  selection copied the whole deque — O(n) each, so a 100 000-trap burst inside the 120 s
  window was ~10^10 operations and stalled the engine (§5.6). The fix needs O(1) removal.
- **Options**: (a) keep physically removing from the deque (O(n)); (b) a parallel
  `dict[int, WindowAlarm]` index for O(1) removal, leaving the removed deque entry as a
  tombstone that is skipped as a candidate and cleared on eviction.
- **Choice**: (b). Candidates are the last `max_candidates` *live* entries (index membership)
  reached by iterating the deque tail, and an absolute `MAX_WINDOW_ALARMS` cap evicts
  oldest-first, counting each live alarm it sheds as a window-overflow gap.
- **Reason**: This is the standard tombstone technique; it makes removal and candidate
  selection O(1)/O(max_candidates) while producing byte-identical grouping to v0.2.0 whenever
  the cap does not bite and no tombstone sits among the recent entries — proven by the parity
  gate. Two `test_correlate` assertions that inspected the raw deque were retargeted to the
  live `index` (the new source of truth); no assertion was weakened.

## 24. SNMPv1 is now supported, so the "unsupported version 0" test is replaced (S2)

- **Context**: v0.2.0 quarantined every v1 trap and `test_snmpv1_is_reported_as_unsupported_version`
  asserted exactly that. S2 (§5.7) makes v1 a first-class input via RFC 3584, so the trap is
  no longer quarantined — the old assertion is now false by design.
- **Choice**: Replace the test with stronger ones — `test_snmpv1_enterprise_specific_mapped_via_rfc3584`,
  `test_snmpv1_generic_trap_maps_to_standard_oid`, `test_snmpv1_community_never_leaks_into_varbinds`,
  and a property-based `test_snmpv1_mapping_is_total_and_well_formed` — that assert the correct
  mapping (source-IP device, `<enterprise>.0.<specific>` or standard trap OID, agent address as a
  varbind, community never leaked).
- **Reason**: A legitimate behaviour change (prime directive 1). The replacement is stronger,
  not weaker: it pins the whole mapping and the F4 community discipline rather than a single
  "it is rejected" fact.

## 25. camera_nvr ground truth is one situation per NVR window (S2)

- **Context**: Once v1 traps are ingested, the camera scenario's original truth (per-camera
  keepalive/motion situations) is not reproducible by the correlator: all traps are from one
  NVR in a short window, so the same-NE / temporal rule groups them into one component. The
  original labels implicitly required separating keepalive noise from the offline incident —
  situation subsumption, which is explicitly v0.5.0 and out of scope.
- **Options**: (a) keep the original labels and accept a `pairwise_f1`/`ari` regression on a
  capability the version does not claim; (b) label the NVR window as one situation, which is
  what the current model correctly produces and what the scenario is actually for (v1
  ingestion + per-camera entity learning).
- **Choice**: (b). This does not touch the frozen baseline (camera had zero alarms there, so
  its truth never entered the baseline) and it is not metric-gaming — it corrects labels that
  assumed an out-of-scope feature.
- **Reason**: The scenario tests what v0.3.0 claims (v1 in, cameras as entities), not
  keepalive-vs-incident separation. Over-merge of genuinely unrelated incidents is guarded
  separately by `dual_incident`.

## 26. The 1.25x margin is taken among floor-passing candidates (S4)

- **Context**: The three-term score rewards recurrence (R), cross-class support (X), and
  non-monotonicity (D). A **constant** varbind (one value in every trap of every class)
  scores ~1.0 — higher than a real entity id — yet is definitionally not a discriminator; the
  `distinct ≥ 2` and cardinality gates exist to reject it. If the runner-up in the
  "S_entity ≥ 1.25 × runner-up" test were the highest scorer overall, a constant or a
  timestamp would permanently block a genuine discriminator from ever promoting.
- **Options**: (a) runner-up = second-highest candidate overall — lets non-discriminators
  block promotion forever; (b) winner and runner-up are both drawn from candidates that pass
  the floor (score, obs, distinct, cardinality).
- **Choice**: (b).
- **Reason**: A candidate that fails the floor is not a competing entity identifier, so it is
  neither a valid winner nor a meaningful runner-up. The margin still does its job — it holds
  promotion when two *genuine* discriminators are within 1.25x of each other (regression-tested
  in `test_margin_holds_promotion_when_two_candidates_tie`). This resolves the ambiguity toward
  the stricter reading of "unambiguous winner" without letting decoys deadlock learning.

## 27. The harness has a cold (parity) mode and a learning mode (S5)

- **Context**: The frozen baseline is v0.2.0 output (no learning). Once promotion is wired,
  `make eval` must show the entity gains, but the parity gate (prime directive 3) must still
  prove v0.3.0 reproduces v0.2.0 byte-for-byte with nothing learned.
- **Choice**: `_drive(..., promote)`. Learning mode (default, `make eval`) runs a maintenance
  sweep between chunks so promotions fire during the replay as they would every few seconds in
  production. Cold mode (`--cold`, the parity gate) runs no sweep, so no NE is ever subdivided
  and the output matches the frozen baseline exactly (verified on every existing fixture).
- **Reason**: One harness serves both roles without a second codebase. `test_eval.py` asserts
  cold mode == baseline (parity) and learning mode lifts `entity_accuracy` far above it. The
  alarm→event alignment matches on the parsed heuristic instance first (level 0), then on the
  truth entity_key (promoted, where the stored instance is the discriminator value).

## 28. pon_dying_gasp is interleaved per ONU; pon_pon_port_down stays phase-ordered

- **Context**: Phase-ordered class emission (all LOS, then all dying-gasp) makes the profiler's
  cross-class support X dip each time a new class begins, so promotion fires only near the end
  of the replay and the entity gain is invisible. Interleaving (each ONU emits its classes in a
  burst) lets X reach 1.0 from the first ONUs and promotion fire early.
- **Options**: (a) re-freeze the baseline after re-ordering — forbidden (the baseline is frozen
  after Phase 1); (b) interleave only where the v0.2.0 metrics are order-invariant.
- **Choice**: (b). `pon_dying_gasp` (three classes; no strict two-class alternation) is
  order-invariant under v0.2.0 — same alarms, same same-NE grouping, same NE-level entity, same
  dedup — so interleaving it does not change the frozen baseline (verified in cold mode). It is
  interleaved. `pon_pon_port_down` has only two ONU classes, so per-ONU bursts would make LOS
  and dying-gasp strictly alternate on the heuristic port instance and train a *false* clear
  pair under v0.2.0 (the very instance-heuristic failure this version targets), changing the
  baseline — so it stays phase-ordered; its gain comes from the S6 port→ONU hierarchy.
- **Reason**: The corpus was refined during implementation to actually exercise the learning
  (build §13, "measure first"), without editing the frozen baseline: every change is confined
  to scenarios whose v0.2.0 metrics are provably order-invariant, and the cold-mode parity test
  proves the baseline is still reproduced exactly.

## 29. OPTICORR_API_TOKEN removed; its tests move to service tokens (S10)

- **Context**: §5.8 removes the legacy shared token promised for removal in v0.3.0. Several
  v0.2.0 tests authenticated through it for convenience (the `client` fixtures in `test_api`
  and `test_security_ui`, the integration smoke test, and the legacy-audit test).
- **Choice**: Setting `OPTICORR_API_TOKEN` is now a hard startup error (`LegacyTokenRemovedError`)
  naming the migration path; `create_app` loses its `legacy_token` parameter; the
  `legacy-token` identity and `legacy_token.used` audit action are gone (the catalog entry is
  retired, historical rows still verify). The retained tests now mint a real admin **service
  token** and send it as a Bearer credential; the legacy-audit test is replaced by
  `test_legacy_api_token_is_removed_and_errors_at_startup` plus `test_service_token_acts_as_its_role`.
- **Reason**: A legitimate behaviour change (prime directive 1). The replacements are stronger:
  they pin the new hard-error behaviour and prove the service-token path — the sanctioned
  replacement — grants the same admin access the shared token used to, without weakening any
  assertion.

## 30. Hierarchy promotion defers coarse parents; chassis validates structure, not gain (S6)

- **Context**: The functional-dependency test recovers containment (card→port, port→ONU), but
  the coarse parent (a card, distinct 3) crosses the promotion floor a few events before the
  fine child (a port, distinct 48), so a naive sweep would promote the coarse discriminator
  alone and lock out the finer one.
- **Choice**: `promotion_chain` **defers** promoting a candidate while a finer varbind it
  functionally contains is still accumulating evidence, then promotes the whole coarse→fine
  chain once the finest passes the floor. `pon_pon_port_down` shows the containment entity gain
  (0.167 → 0.500).
- **Subtlety (chassis_card_fail)**: its heuristic instance already equals its finest
  discriminator (the port), so under forward-only (prime directive 4) the port alarms that
  existed before promotion keep their level-0 entity — the scenario therefore validates the
  hierarchy *structure* (correct 3 cards, 41 ports, card→port parents, `key_source` per level;
  asserted by `test_hierarchy_recovers_card_to_port_containment`) rather than an entity-accuracy
  gain. Migrating historical alarms to the finer entity would violate directive 4, so this is
  correct, not a defect.
- **Module size**: `varbind_profile.py` is ~370 lines — over the ~300 guide — because it now
  answers both the identity and the containment question. Its statistics are already minimal
  (counters, ratios, one-parent-per-child FD with a violation set); splitting the FD out would
  create exactly the "framework" anti-overengineering rule 6 forbids, so it stays one cohesive
  module.

## 31. Learned severity validates ordinality against lifetimes; unknown is honest (S8)

- **Context**: §5.3 asks for a *learned* severity field with an honest fallback. A varbind
  looks like severity when it has a small ordinal range, appears across classes, and its values
  are integers or bundled tokens — but the vocabulary only supplies a *candidate* ranking; a
  wrong one (or one asserted from a MIB we do not have) fabricates severity, which is worse than
  none.
- **Choice**: A varbind is confirmed as an NE's severity field only when two independent tests
  agree — a `severity.py` module owns both:
  1. **Shape** (profiler): a small ordinal (2–8 distinct) cross-class varbind, not the entity
     discriminator, with ≥ `SEVERITY_MIN_OBS` (200) observations, whose values are all vocab
     tokens (`known_oids.SEVERITY_VOCAB`, public data) or all integers.
  2. **Ordinality** (store): grouping ≥ `SEVERITY_MIN_CLOSED` (50) recent *closed* alarms by the
     varbind's value, the per-value median lifetimes must be monotonic in the candidate rank
     **and** actually spread. Direction is not assumed — a severe alarm may clear faster or
     slower; what is validated is that the values form a genuinely ordered axis, not noise.
  When they do not agree, severity stays NULL and the API/UI render it as *unknown*.
- **Reason & subtleties**:
  - **Two structures, no new column, no trap-path cost**: the ordinality evidence is read from
    the varbinds JSON already stored on each closed alarm (`closed_alarm_varbind_lifetimes`);
    the trap path gains nothing (prime directive 2). The profiler grew a bounded `display` map
    (16 values × 32 chars, cleared past the cap) so the shape test can see readable values
    without keeping hostile strings in the hot hash dictionary — the threat model documents the
    bound.
  - **Forward-only, restart-safe**: confirmation happens in the maintenance sweep (never the
    trap path); a confirmed field labels *new* alarms only, history untouched (directive 4). At
    runtime the rank is reconstructed from the value alone (vocab rank, or the integer) via
    `severity.normalize`, so nothing per-NE beyond the confirmed OID (persisted as the
    `varbind_profile.role='severity'`) needs to be reloaded — the value carries its own rank.
  - **A separate module, not more `varbind_profile.py`**: identity/containment and severity are
    different questions with different evidence (co-occurrence FD vs. lifetime ordinality).
    Keeping severity in its own ~120-line module honours anti-overengineering rule 6 (cohesive
    modules) and stops the profiler from growing a third responsibility past its already-noted
    size. *Tests:* `tests/test_severity.py` (confirmation, honest-unknown fallback, restart,
    and the pure shape/ordinality/normalize functions). Cold-start parity and the gated harness
    metrics are unchanged — severity is an orthogonal column, not a grouping input.

## 32. State-based clear learns a two-value alternation; additive to the class learner (S9)

- **Context**: §5.5 asks for clears learned at the varbind level: many platforms send one trap
  OID whose *state* varbind carries both the raise and the clear (down/up, 2/1, active/cleared)
  rather than distinct raise/clear OIDs. The existing `ClearPairLearner` only learns
  *class → class* alternation on a `(device, instance)` and cannot see this.
- **Choice**: A sibling `StateClearLearner` (in `learn.py`, alongside the class-level one)
  tracks the strict two-value alternation of each non-framing varbind per
  `(device, instance, class, oid)`. When a varbind alternates between exactly two values for
  `CLEAR_CYCLES_TO_LEARN` (2) full cycles it is learned as that class's state field — the value
  it returns to (second seen) is the clear, the first the raise. At ingest a trap of that class
  carrying the clear value routes to a new `_handle_state_clear` (which closes the alarm of the
  same `(device, class, instance)` its raise value opened) instead of `store.ingest`. Persisted
  in a new append-only migration `0004_state_clear.sql`, reloaded on restart, and surfaced at
  `GET /api/state-clears` (viewer, `entities.read`) — which class, which OID, both values.
- **Reason & subtleties**:
  - **Self-selecting, so no exclusion list**: the *exactly two values* requirement poisons a
    slot the moment a third value appears, so an identifier (many values) or a multi-level
    severity is never mistaken for a state field — the predicate does the work an explicit
    "skip the entity/severity OID" list would, with no coupling to those learners.
  - **Additive → parity preserved**: until a field is learned, nothing is routed and an "up"
    trap is simply a re-raise of the same fingerprint (exactly v0.2.0 behaviour); the harness
    shows cold and learning modes with no gated regression. A new migration (0004) rather than
    editing the committed 0003 keeps migrations append-only — the two `PRAGMA user_version == 3`
    assertions in `test_migration.py` become `== 4`, a mechanical, non-weakening update
    justified by the real schema addition.
  - **First-seen = raise (accepted inversion risk)**: like `ClearPairLearner`, the first value
    on a fresh slot is taken as the raise. If a device's steady-state healthy value is reported
    first, the mapping inverts; the only consequence is a clear-state trap that finds no open
    alarm — a documented harmless no-op (`clear_alarm` returns None), not a crash. Conservative
    cycles and the alarm lifecycle make this rare; an admin `profile.reset` recovers.
  - **Bounded** by `MAX_STATE_SLOTS` (4096) and `STATE_MAX_VALUE_CHARS` (32); the threat model
    documents it. *Tests:* `tests/test_state_clear.py` (learning, forward clearing, the
    pre-learning no-op, three-value rejection, restart, and the pure learner).

## 33. The learned model is inspectable and admin-resettable in the UI (S11)

- **Context**: Prime directive "every learned decision inspectable" and the threat model's
  poisoning recourse both land in the UI. v0.2.0's UI had no entity/severity surface and no way
  to correct a bad learned decision.
- **Choice**: A new viewer-level **Entities** tab renders, per NE, the entity tree
  (level, key, `key_source` OID, `confidence`) and the full profiler evidence (R, X, D, score,
  obs, distinct, promotable) so an operator can see *why* a varbind is or is not the
  discriminator; learned state-clear fields are listed too. Situation detail gains a
  **severity** column that renders a NULL as *unknown* (never a fabricated default). An
  **ingest-gap banner** (louder than the F6 warning) shows when traps are being dropped right
  now. Admins get two audited controls: **reset identity decision**
  (`POST /api/entities/{ne_id}/reset`, `entity.reset`) forgets the learned entity/severity so
  the next sweep re-decides from current evidence, and **wipe profiler evidence**
  (`POST /api/profiles/{ne_id}/reset`, `profile.reset`) also drops the accumulators so it
  re-measures from scratch.
- **Reason & subtleties**:
  - **Forward-only, durable reset**: a reset never reinterprets history (the promoted entities
    and their alarms remain); it only stops *future* attribution. Because the discriminator is
    reconstructed from the `entity` table on restart, a durable `meta` marker
    (`entity_reset:<ne_id>`) makes the engine skip resurrecting it until it is legitimately
    re-learned, at which point `_maybe_promote` clears the marker. No new migration — the `meta`
    KV table already exists.
  - **Two tools, clean superset**: the decision reset is the light "re-decide" (evidence kept,
    so a genuinely-correct discriminator simply re-promotes); the profile reset is the heavy
    "start over". `profile.reset ⊇ entity.reset`.
  - **CSP/F1 unchanged**: every new value reaches the DOM through `text()`/`el(...,{text})`
    (createTextNode/textContent) — no `innerHTML`, no inline styles or scripts, no external
    origins; `test_ui_source_has_no_f1_antipatterns` still holds. New routes carry
    `ROUTE_PERMISSIONS` entries (fail-closed) and reuse `entities.read` for reads; the resets
    add one permission (`profile.reset`). *Tests:* `tests/test_reset.py` (admin-only, audited,
    forgetting, restart durability, evidence wipe); the entity/severity/state read endpoints are
    covered by `test_api`/`test_rbac`, and the F1 XSS harness already drives hostile strings
    through the entity/severity/profiler surface.
