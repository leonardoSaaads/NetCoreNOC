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

---

# v0.4.0 — "trustworthy by construction" (hardening + rebrand)

## 34. Rebrand NewProjectNetworj/OptiCorr → NetCoreNOC (Phase 1, gated rename)

- **Context**: the repository moved to `github.com/leonardoSaaads/NetCoreNOC`. The whole
  codebase still called itself *OptiCorr* (import package `opticorr`, env prefix `OPTICORR_*`,
  cookie `opticorr_session`, CSRF header `X-OptiCorr-Client`, UI wordmark, logger name).
- **Options**: (a) a big-bang rename touching wire identifiers with no compatibility, breaking
  live deployments; (b) a mechanical, fully test-covered rename that treats the wire identifiers
  like the v0.2.0 legacy-token deprecation — a one-version compatibility window for what would
  otherwise break a running install.
- **Choice**: (b). Import package `opticorr` → `netcorenoc`; all project metadata, CI, flake,
  Dockerfile, Makefile updated. Env prefix `OPTICORR_*` → `NETCORENOC_*`, with the legacy names
  accepted for **one version** (removed in v0.5.0) and a single startup deprecation warning per
  variable — naming the variable, never its value. Cookie `opticorr_session` →
  `netcorenoc_session` (invalidates live sessions once; operators re-login — documented). CSRF
  header `X-OptiCorr-Client` → `X-NetCoreNOC-Client`, changed in UI and server in the same commit
  with **no** compatibility window (it is not a persisted credential).
- **Reason**: the rename changes names, never behaviour. The rename gate proves it: `make qa`
  green, all 226 tests pass under the new package name, and `make eval` produces a
  byte-identical delta table against the frozen `v0.2.0` baseline (`pairwise_f1=1.0000`,
  `ari=0.9999`, `entity_accuracy=0.4480`, `root_top1=1.0000`; only the printed header brand
  changed). *Legacy `OPTICORR_*` acceptance is regression-tested* (`test_settings_legacy_*`,
  `test_settings_new_env_takes_precedence_over_legacy`, and the CLI export test drives
  `OPTICORR_DB`). `OPTICORR_API_TOKEN` survives verbatim only as a historical name in the
  removed-token error and docs — both prefixes are rejected at startup.

## 35. Re-defer the `device_id` → `entity_id`/`ne_id` cutover to v0.5.0 (§A.2)

- **Context**: `learn.py::device_affinity` is the retained v0.2.0 compatibility shim and the
  `alarm.device_id` column removal was tentatively promised for v0.4.0. The shim and the column
  are woven through the core scoring path (`correlate.py` `entity_id` defaulting, `rootcause.py`
  precedence, `store.py` joins) and several tests (`test_learn`, `test_scenarios`).
- **Options**: (a) complete the cutover now — remove the shim, migrate the API/UI, add a
  forward-only `0005_*.sql`, and re-run parity; (b) re-defer to v0.5.0 with this entry.
- **Choice**: (b). This is a security- and reliability-hardening release; the cutover is
  behaviour-adjacent churn on the hottest path with no safety benefit. The brief's explicit
  guidance is "if in doubt, re-defer." It is not left silently half-done: `docs/ROADMAP.md`
  carries the v0.5.0 line and `docs/SCOPE-0.4.md` lists it as deferred.
- **Reason**: Prime directive 3 (no metric regression) and directive 4 (safety over churn) both
  point away from touching the scoring path this release.

## 36. Re-defer typed relations and device-archetype clustering to v0.5.0

- **Context**: SCOPE-0.3 tentatively tagged typed relations (physical adjacency / containment /
  common-cause-of-site) and device-archetype clustering (by emitted-class vector) for v0.4.0.
- **Options**: (a) build them now; (b) re-defer to v0.5.0.
- **Choice**: (b). v0.4.0 is explicitly a hardening release that adds **no new inference
  capability**; both are new inference. Recorded in SCOPE-0.4 (deferred list) and ROADMAP.
- **Reason**: Prime directive — no feature outside the hardening scope; keep the release focused.

## 37. New fault/abuse scenarios are engine-driven tests, not scored eval-corpus additions

- **Context**: C.2/C.3 ask for a broad labelled scenario set. The eval harness computes its gated
  aggregate (`pairwise_f1`, `ari`, `entity_accuracy`, `root_top1`) by **pooling every scored alarm
  across all `eval/corpus/*.json`** and comparing to the frozen `eval/baselines/v0.2.0.json`.
- **Options**: (a) add the new scenarios to the scored corpus; (b) drive them through the real
  ingest path in `tests/` and assert their grouping/containment/situation-count directly.
- **Choice**: (b). Adding scored scenarios shifts the pooled aggregate (denominators change) and
  would move the gated metrics off the frozen baseline — a Prime-Directive-3 build failure — with
  no offsetting benefit, since a scenario's *correctness* is exactly what a targeted assertion
  checks. The declarative DSL (`eval/scenario_dsl.py`, C.1) is the authoring path for these tests;
  `tools/trap_sim.py` can also replay them over real UDP. The scored corpus stays frozen.
- **Reason**: honours the non-regression directive with certainty while still delivering the
  security-event-correlation (C.3, P0) and network-fault-breadth (C.2, P1) coverage. Phase-4
  "new scenarios scored" is met as "the engine scores them and the test asserts the outcome".

## 38. Role-aware UI (S9): admin screens pruned from the DOM; gating verified statically

- **Context**: §A.4 requires that a viewer sees no mutating controls and that admin-only screens
  are *absent from a non-admin DOM*. The v0.3.0 UI gated the tab *buttons* by role but left the
  admin `<section>` panels present-but-hidden in every DOM.
- **Options**: (a) keep panels hidden; (b) remove non-permitted panels from the DOM on login;
  and, for testing, (c) a headless-browser (Playwright) render test vs (d) static discipline tests.
- **Choice**: (b) + (d). `prunePanels()` removes every panel whose role the caller lacks, called
  in `enterApp()`; `logout()` does a full reload so a later higher-role login rebuilds a fresh DOM.
  Role gating is verified by static-analysis tests (TABS role map ⇒ admin panels admin-gated;
  prunePanels present and called; mutating controls created only under `canEdit()`/`isAdmin()`;
  the UI stays exactly four files; CSP unchanged).
- **Reason**: adding Playwright would be a heavy new test dependency outside the dev whitelist for
  a P1 concern; the security-relevant properties (CSP, F1 escaping, role-gated DOM) are asserted
  without it. The aesthetic refresh (expanded design tokens, a `prefers-color-scheme` light
  variant, visible `:focus-visible` states, AA-contrast nudges, responsive stacking) is
  hand-written CSS only — no framework, no inline styles, CSP intact. The "why did it decide
  that?" surface already exists (entity R/X/D + evidence counts; link t/A/E terms), so no new view
  was needed. UI is P1 and simplified here per this entry; its security properties are not.

---

# v0.5.0 — "legible, installable, contributable" (structure & growth readiness)

Continues the numbering; never renumbers history. This release changes packaging, docs,
process, and a specification only — no engine, schema, API, or UI-behaviour change. Ambiguity
resolves toward the simplest option consistent with zero-config and the threat model;
security-relevant ambiguity toward the stricter option.

## 39. Extend the legacy `OPTICORR_*` env-alias deprecation window to v0.6.0 (a non-removal)

- **Context**: DECISIONS #34 accepted the legacy `OPTICORR_*` environment names for "one
  version" with a once-per-variable startup warning, promising removal in v0.5.0. v0.5.0 is a
  deliberately small organization/structure release whose theme is "no behaviour change."
- **Options**: (a) remove the aliases now as originally scheduled; (b) keep them one more
  version and re-target removal to v0.6.0.
- **Choice**: (b). The aliases and their once-per-variable warning stay; the removal target
  moves to v0.6.0. Warning strings in `netcorenoc/main.py` and `netcorenoc/__main__.py`,
  `MIGRATION.md`, `README.md`, and `docs/ROADMAP.md` are updated to "v0.6.0".
- **Reason**: Removing a still-warned compatibility path in an organization release is exactly
  the kind of unrelated breaking change this release is meant to avoid (prime directive: no
  behaviour change). This is the *only* behaviour-adjacent decision in v0.5.0, and it is a
  non-removal — strictly more compatible than the alternative. The alias regression tests assert
  the *presence* of the warning and the variable names, not the version string, so the bump is
  mechanical and non-weakening. The removal will land with v0.6.0's other breaking changes (the
  `device_id → entity_id/ne_id` cutover, DECISIONS #35), where a MIGRATION section already
  frames a breaking upgrade.

## 40. Reorg mechanics: src/ layout, docs taxonomy, and dev-only guard tests (Phase 2)

- **Context**: §6 adopts the PyPA `src/` layout and a documentation taxonomy. Two mechanical
  choices needed recording: (a) the `src/` move changes ruff's isort classification of
  `netcorenoc` from third-party (flat layout) to first-party, which would reorder imports across
  ~31 test/eval files; (b) the reorg needs a structure guard and a link check, and prime
  directive 4 forbids new runtime dependencies (a link checker is the named example).
- **Options**: (a-1) accept `ruff --fix` reordering every affected file; (a-2) configure isort so
  the churn is minimal and the shipped package files stay pristine. (b-1) add a third-party link
  checker in the dev extra; (b-2) write the guards as pure-stdlib pytest tests.
- **Choice**: (a-2) + (b-2). isort config: the eval/tools/test **helper** modules (`metrics`,
  `trap_replay`, `harness`, `corpus_gen`, `scenario_dsl`, `trap_sim`, `util`, `authutil`) are
  classified `known-local-folder` (they are local dev/test helpers, not the distributed package),
  which keeps their import blocks unchanged; the only remaining normalization is a conventional
  blank line before the now-first-party `netcorenoc` group in ~20 test files — the shipped
  `src/netcorenoc/*` files already followed that convention and are untouched. The structure
  guard and documentation link check are stdlib-only pytest tests
  (`tests/test_structure.py`), run by `make test`/`make linkcheck` (hence `make qa`).
- **Reason**: `netcorenoc` genuinely *is* first-party under `src/`, so fighting the
  classification with a config lie (e.g. `known-third-party`) would misrepresent the package;
  the local-folder classification is honest and minimizes churn to import grouping only (no
  assertion, no logic, byte-identical eval). A stdlib pytest link check adds **zero runtime and
  zero new dependencies** while satisfying "a link check in `make qa`/CI"; the decision log stays
  one append-only file (§6) with `adr/README.md` explaining the format — splitting the 38 entries
  would be churn without benefit.

## 41. Community scaffolding: security.txt location, contacts, and the `.well-known/` exemption

- **Context**: §4 requires a committed, app-served `.well-known/security.txt` (RFC 9116), a
  disclosure policy and Code of Conduct with editable contacts, and licence compliance — added
  against the hard constraint that the UI stays four files (DECISIONS #38) and the CSP/security
  headers are unchanged.
- **Options**: (security.txt) one copy in the package vs. a package copy plus a repo-root copy;
  (serving) a new dynamic route vs. the existing static allowlist; (contacts) a hardcoded personal
  email vs. GitHub private reporting + an editable placeholder; (#38) relax the four-files rule vs.
  keep it and bound the addition.
- **Choice**: **One** copy at `src/netcorenoc/ui/.well-known/security.txt`, shipped via a
  `ui/.well-known/*` package-data glob and served by adding it to `STATIC_ASSETS`
  (`text/plain; charset=utf-8`) — no new dynamic surface, same CSP/security-headers middleware, no
  repo-root duplicate to drift. Contacts route to GitHub **private vulnerability reporting** and
  the maintainer's profile (no external setup, no personal email published), with clearly-labelled
  editable placeholders in `CODE_OF_CONDUCT.md`. The #38 four-files test is updated to assert the
  UI *code* is still exactly the four artifacts while permitting a single `.well-known/` dir that
  holds *only* `security.txt` (a stronger, not weaker, assertion).
- **Reason**: security-relevant ambiguity resolves toward the stricter/simpler option: a single
  served copy can't drift; reusing the static allowlist keeps the new path static, public, and
  under the existing CSP with no code; private-reporting contacts avoid publishing a personal
  address while needing no infrastructure. The `.well-known/` file is standardized served metadata,
  not UI code, so #38's anti-sprawl intent is preserved by bounding it rather than dropped.

## 42. Self-contained deployment: compose hardening, sdist pruning, and dormant CI (Phase 4)

- **Context**: §5 makes "start using it" equal `docker compose up` and adds local release tooling
  and dormant CI — all wrapping the unchanged single process, with zero new *runtime* deps.
- **Decisions & reasons**:
  - **`build` added to the `dev` extra** (not `[project.dependencies]`) — a build-time/dev tool
    for `make dist`; the shipped app gains nothing to import. This is the rule-4-required
    justification for the only new dependency in the release.
  - **Compose privileged-port trade**: `cap_drop: [ALL]` then `cap_add: [CAP_NET_BIND_SERVICE]`
    so the non-root container (uid 10001) can bind UDP 162. The single-capability grant is the
    deliberate trade; the documented alternative (map a high port + drop the cap) is in the
    compose/`.env.example` comments and the systemd unit.
  - **Healthcheck uses the image's Python** (`urllib` against `/healthz`) because slim images
    ship no curl/wget — no new package in the image.
  - **`MANIFEST.in` prunes `tests/`, `eval/`, `tools/`, `docs/`, CI, and any `.env`/`*.db`** from
    the sdist (F18), while `graft src` keeps the whole package (UI, `.well-known`, migrations,
    `d3.LICENSE`); `.dockerignore` keeps the build context secret-free. `.env` is now git-ignored
    (only `.env.example` is committed).
  - **Dormant, SHA-pinned CI**: `ci.yml`/`release.yml` pin every action by 40-char commit SHA
    (with the human tag in a comment) and declare least-privilege `permissions:`; `release.yml`
    runs only on a `v*` tag with the built-in `GITHUB_TOKEN`, and every publish/sign step is
    commented opt-in. A stdlib pytest lint (`tests/test_workflows.py`) fails CI on any floating
    tag, missing `permissions:`, or an active publishing step — so a dormant workflow cannot
    become a live risk by accident.
