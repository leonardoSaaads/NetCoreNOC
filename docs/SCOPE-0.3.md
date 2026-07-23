# OptiCorr — v0.3.0 Scope

Authoritative product scope for the v0.3.0 release. On any conflict: this document wins on
*what* ships; `docs/threat-model.md` wins on security posture; the autonomous build
document (`docs/BUILD-REPORT-0.3.md` records the process it prescribes) wins on process and
quality. v0.2.0 behaviour (`docs/SCOPE-0.2.md`) remains intact except where a change here
deliberately supersedes it.

## Theme

**Entity identity — learning *what* is alarmed, not merely *who* reported it.**

v0.1.0 and v0.2.0 assume the sender is the sufferer: an alarm's device is the trap's source
IP, and its instance is guessed by a fixed heuristic (`receiver.py::_instance_of`: an
`ifIndex` varbind, else the first non-standard varbind). That assumption breaks against real
access and transport networks, where proxied reporting is the norm, not an edge case:

- a PON OLT emits traps on behalf of thousands of ONUs, the ONU identity buried in varbinds;
- a chassis reports its own line cards and ports;
- a DSLAM reports subscriber ports; an NVR reports cameras.

Under v0.2.0 a power outage that produces thousands of dying-gasp traps from one OLT either
collapses into a handful of alarms (constant varbind chosen) or explodes into thousands of
unrelated ones (per-event varbind chosen). Worse, the heuristic fails **silently**: when the
first non-standard varbind is a timestamp, sequence number, or alarm serial, the instance is
unique per trap, deduplication never fires, the flap detector never sees a repeat, and the
clear-pair learner never completes an alternation — the system degrades to "no learning at
all" with no error and no warning.

v0.3.0 replaces the heuristic with a **learned entity model**. Everything below serves that
single goal.

## Non-negotiable invariants (carried from v0.2.0)

1. **No regressions.** Every v0.2.0 test keeps passing unmodified, except where behaviour
   legitimately changes (minimal, justified in `docs/DECISIONS.md`, never weakening an
   assertion).
2. **Ingestion is sacred.** The trap path (receiver → queue → engine) acquires no new lock
   and no new I/O. Varbind profiling runs in the engine, in memory, under the batch lock the
   engine already holds, and is flushed by the existing `maintenance()` loop.
3. **Cold-start parity.** With nothing learned, v0.3.0 produces byte-identical grouping to
   v0.2.0 on every existing fixture, proven by the evaluation harness.
4. **Promotion is forward-only and evidence-gated.** An NE starts as one entity and is
   subdivided only when statistical evidence crosses an explicit threshold. Historical
   alarms are never reinterpreted, migrated, or rewritten.
5. **Every learned decision is inspectable.** For each promoted entity key, severity field,
   and state field, the UI and API expose which varbind OID was chosen, the score that chose
   it, and how much evidence backs it — the same discipline as the three-term link score.
6. **Same identity.** One process, one SQLite file, zero new runtime dependencies, no
   frontend build step, no configuration files.

## What ships

### The varbind profiler

A new in-engine module accumulates, per `(NE, alarm class, varbind OID)`, bounded statistics
(observation count, a capped value-frequency map, repeat count, monotonic-run count, numeric
count). From these it answers four questions off the same statistics: which varbind
identifies the alarmed entity, which expresses containment, which expresses severity, which
expresses alarm state.

The entity discriminator is scored by exactly three explainable terms:

- **R** — how often values recur (an index recurs; a timestamp never does);
- **X** — cross-class support: the *same* values appearing under *different* alarm classes on
  the same NE. This is the decisive term — only an entity identifier makes the same token
  appear across semantically different alarm types (ONU 42 appears in both loss-of-signal and
  dying-gasp); a timestamp never repeats, a sequence number never repeats a value across
  classes, a constant has no discriminating power;
- **D** — evidence the value is not a counter or a sequence number.

A candidate is promoted to an NE's entity discriminator only when the score, the observation
count, the distinct-value count, the cardinality ratio, and an unambiguous margin over the
runner-up all cross explicit thresholds. Promotion is per NE, not per class.

### Entity model and hierarchy

A forward-only migration adds `ne` (the reporting element), `entity` (the alarmed thing,
level 0 = the NE itself), `varbind_profile` (persisted profiler state), and `ingest_gap`.
Each v0.2.0 `device` becomes one `ne` plus one level-0 `entity`; `alarm` gains `entity_id`
and `ne_id` (backfilled), while `device_id` is retained and kept in sync for one version
(its removal is a v0.4.0 line). The alarm uniqueness constraint becomes
`UNIQUE (entity_id, class_id, instance)`, which at level 0 is exactly equivalent to the
v0.2.0 constraint — the mechanical basis of the parity gate.

Containment is recovered by a classical functional-dependency test over co-observed events:
if almost every value of a finer discriminator maps to exactly one value of a coarser one,
the coarser is the parent. This recovers PON port → ONU, chassis → slot → port, and NVR →
camera without a MIB, an inventory, or a vendor assumption. Depth is capped.

### Learned severity (with an honest fallback)

A varbind is treated as severity when it has a small ordinal range, appears across classes,
and its values are ordinal — integers, or members of a small bundled vocabulary of common
severity tokens added to `known_oids.py` as public data. Ordinality is validated against
observed alarm lifetimes, not assumed; when it cannot be validated, severity stays
**unknown**, and the UI and API render it as *unknown* — a fabricated severity is worse than
none.

### State-based clear

Many platforms send one trap OID carrying a state varbind rather than distinct raise/clear
OIDs. The clear-pair learner is extended to learn at the varbind level: a small-range varbind
that strictly alternates on an `(entity, class)` for enough full cycles is promoted to a
state field, and the terminating value to the clear value. The existing class-level
alternation learner is unchanged; the varbind-level learner is additive. Storm suppression
applies identically.

### Entity affinity

`Learner.device_affinity` becomes `entity_affinity`, keeping the learned matrix at **NE
level**: same entity → 1.0; same NE, different entity → a fixed structural constant; different
NE → the learned NE×NE affinity as today. An entity×entity matrix on a large network is
computationally impossible and, more importantly, wrong: intra-NE proximity is a structural
fact, not a statistic. Before any promotion, every NE has exactly one entity, so this reduces
numerically to v0.2.0's device affinity — which is why parity holds.

### Performance and durability (P0, shipped first)

- The sliding-window scan is made non-quadratic: an O(1) removal index, bounded candidate
  iteration, and an absolute window cap with oldest-first eviction.
- Dropped traps leave a durable trace: `ingest_gap` rows (queue-full or window-overflow),
  surfaced in `/api/stats` and the UI. "I know I lost events between t1 and t2" is
  first-class NOC information.

### SNMPv1 (RFC 3584)

v1 traps — still emitted by much access equipment and most cameras — are mapped into the
pipeline per RFC 3584 §3.1 (deriving `snmpTrapOID.0` from the enterprise and generic/specific
trap, prepending `sysUpTime.0` and the agent address as varbinds). This needs no
configuration, unlike SNMPv3, which stays out of scope. The NE IP is the UDP source (not the
spoofable v1 agent-address, which is exposed as a varbind).

### Legacy token removal

`OPTICORR_API_TOKEN`, deprecated in v0.2.0 with removal promised in v0.3.0, is removed:
setting it now produces a startup error naming the migration path to service tokens. The
`legacy_token.used` audit action is retired from the catalog but not deleted from history.

### Measurement

A committed evaluation harness (`eval/`) replays a labelled corpus offline and reports every
algorithmic change's delta against a frozen v0.2.0 baseline. The headline number is
`entity_accuracy`; `pairwise_f1`, `ari`, and `entity_accuracy` are non-regression gates in CI.

## New authorization and audit surface

- One new capability: an admin-only, audited reset of an NE's learned entity key. Reading the
  entity tree and varbind profiles is viewer+.
- New system-actor audit actions: `entity.promote`, `entity.reset`, `profile.reset`,
  `ingest.gap`.

## Explicitly out of scope (deferred, in this order)

1. Typed relations — physical adjacency / containment / common-cause-of-site (**v0.4.0**).
2. Device archetype clustering by emitted-class vector (**v0.4.0**).
3. Situation subsumption — N children under one parent collapsing to one situation (**v0.5.0**).
4. Impact scope — "probably affected equipment" (**v0.5.0**).
5. Situation fingerprint and pattern recurrence (**v0.5.0**).
6. `Case` JSON contract **implementation** (**v0.6.0**); the *specification* is written now in
   `docs/CASE-SCHEMA-DRAFT.md`.
7. Decomposition of composite `ifIndex` encodings (slot/port/ONU packed into one integer).
8. SNMPv3, automatic MIB enrichment, PostgreSQL/NATS, external identity providers, MFA.

These are named so they are decisions, not omissions.
