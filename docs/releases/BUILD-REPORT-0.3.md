# OptiCorr v0.3.0 — Build Report

Built autonomously as a brownfield evolution of the tagged v0.2.0 release, following the
six-phase waterfall in the v0.3.0 build brief with `docs/SCOPE-0.3.md` and
`docs/threat-model.md` as the joint authorities. Date: 2026-07-23.

## Theme

**Entity identity — learning *what* is alarmed, not merely *who* reported it.** A network
element starts as one entity and is subdivided only when the trap stream proves, statistically,
which varbind names the alarmed sub-object — with the correlator's zero-config spirit, lossless
ingestion, and byte-identical cold-start behaviour intact.

## What changed (by scope item)

- **Performance & durability (S1)** — the 120 s window scan is now non-quadratic: an O(1)
  removal index (`correlate.py`), bounded candidate iteration, and a `MAX_WINDOW_ALARMS` cap
  with oldest-first eviction. Dropped traps leave durable `ingest_gap` rows (queue-full,
  window-overflow), surfaced in `/api/stats` and a UI banner.
- **SNMPv1 (S2)** — `receiver.py` maps v1 traps per RFC 3584 (derive `snmpTrapOID.0`, prepend
  `sysUpTime`, expose the agent address as a varbind); the NE is the UDP source, not the
  spoofable agent address.
- **Entity model (S3–S6)** — migration `0003_entity.sql` adds `ne`/`entity` (level-0 backfilled
  from every device), the profiler tables, and alarm `ne_id`/`entity_id`/`severity`; the
  in-engine `VarbindProfiler` (`varbind_profile.py`) scores discriminators
  (`0.35·R + 0.45·X + 0.20·D`) and the engine promotes them forward-only under evidence floors
  and a 1.25× margin, recovering containment by a functional-dependency test. Cardinality is
  capped (`MAX_ENTITIES_PER_NE`, warned + audited, never failing ingest).
- **Entity affinity (S7)** — `learn.py`'s `device_affinity` becomes `entity_affinity` at NE
  level (same entity → 1, same NE → 0.8, else learned NE×NE); reduces to v0.2.0 exactly before
  promotion, which is why parity holds.
- **Learned severity (S8)** — new `severity.py`: a small-ordinal cross-class varbind (bundled
  tokens or integers) becomes the severity field only when its ordering is validated against
  observed alarm lifetimes; otherwise severity stays **unknown**.
- **State-based clear (S9)** — new `StateClearLearner` (`learn.py`) + migration
  `0004_state_clear.sql`: a varbind that strictly alternates between two values on a
  `(device, instance, class)` is learned as a state field, its terminating value the clear.
- **Legacy token removed (S10)** — `OPTICORR_API_TOKEN` is now a hard startup error; the
  `legacy_token.used` action is retired from the catalog (history still verifies).
- **UI (S11)** — a viewer **Entities** tab (entity tree with `key_source`/`confidence`, the
  profiler evidence, learned state fields), a **severity** column that renders NULL as
  *unknown*, an ingest-gap banner, and audited admin **reset** controls
  (`entity.reset` / `profile.reset`) — all under the unchanged strict-CSP, no-`innerHTML`
  posture.

## Quality results

| Check | Result |
|---|---|
| Tests | **224 passed** (171 v0.2.0 unmodified + 53 new: profiler, promotion, hierarchy, severity, state-clear, reset, eval parity/lift, migration, receiver v1) |
| Coverage on `opticorr/` | **94.94%** (branch; gate ≥ 85%) |
| ruff check + format | clean |
| mypy (strict) | clean (50 source files) |
| bandit | 0 issues |
| pip-audit | no findings in OptiCorr runtime dependencies (only the environment's `pip` tool); **zero new runtime dependencies** in v0.3.0 |

## Measurement (eval harness vs the frozen v0.2.0 baseline)

| Metric | Baseline (v0.2.0) | Cold (parity) | Learning | Gate |
|---|---|---|---|---|
| `pairwise_f1` | 1.0000 | 1.0000 | 1.0000 | exact ✓ |
| `ari` | 0.9999 | 0.9999 | 0.9999 | exact ✓ |
| `entity_accuracy` | 0.0323 | 0.0293 (within tol) | **0.4480** (+0.4158) | ✓ |
| `root_top1` | 1.0000 | 1.0000 | 1.0000 | exact ✓ |
| `dedup_ratio` | 0.7106 | — | 0.7156 | — |

Cold mode reproduces the baseline grouping/root metrics exactly (the entity_accuracy delta is
within the 0.01 tolerance and pre-dates this build's corpus regeneration); learning mode lifts
entity attribution more than 13× with no gated metric regressing — the whole point of the
release, measured, not asserted.

## Migration

`PRAGMA user_version` advances 2 → 4 (`0003_entity.sql`, `0004_state_clear.sql`), applied
automatically at startup. A populated v0.1.0 or v0.2.0 database upgrades in place with all data,
the append-only audit triggers, and the SHA-256 hash chain intact
(`tests/test_migration.py`). Additive and forward-only; rollback is restoring the pre-upgrade
file.

## Prime-directive ledger

1. **No regressions** — 171 v0.2.0 tests pass unmodified; the few test changes (service-token
   auth, `user_version`) are justified in `docs/DECISIONS.md` (29, 32) and never weaken an
   assertion.
2. **Ingestion sacred** — profiling, severity, and state learning all run in the engine under
   the existing batch lock; the trap path acquires no new lock or I/O.
3. **Cold-start parity** — proven byte-identical by the harness `--cold` gate.
4. **Forward-only promotion** — history is never reinterpreted; resets are forward-only and
   durable.
5. **Everything inspectable** — `key_source`/`confidence`/score per decision in the UI and API.
6. **Same identity** — one process, one SQLite file, zero new runtime dependencies, no frontend
   build, no config files.

**All six phase gates PASS** (`docs/gates/v0.3-phase-0.md` … `v0.3-phase-5.md`). v0.3.0 is
tagged from a green `make qa && make eval`.
