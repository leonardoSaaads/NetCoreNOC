# Gate 2 — Learning and correlation (self-review)

Date: 2026-07-19. Verdict: **PASS**.

## Criteria and evidence

All evidence is asserted in `tests/test_scenarios.py` (fixtures pass through the real
wire encoder and the real parser; timestamps are synthetic for determinism). Full run:

```
94 passed in 9.56s

Name                     Stmts   Miss Branch BrPart  Cover
opticorr/correlate.py       63      0      8      0   100%
opticorr/learn.py          172      1     44      2    99%
opticorr/main.py           242     39     78      6    85%
opticorr/rootcause.py       63      0     22      1    99%
opticorr/store.py          207      0     20      1    99%
TOTAL                      897     44    208     12    95%
Required test coverage of 85.0% reached. Total coverage: 94.57%
```

### fiber_cut.json ⇒ exactly 1 situation, 8 alarms, plausible root

`test_fiber_cut_after_training_collapses_to_one_situation`: after three training
replays (each closed situation is a learning epoch), a fresh replay produces exactly one
open situation containing all 8 alarms. The probable root is the first LOS alarm on the
NE that alarms first — learned via class and device precedence, not hard-coded. Every
stored link carries the three score terms and `score == term_t + term_a + term_e`.

Cold start is honest and observable
(`test_fiber_cut_cold_start_groups_per_device_until_evidence_accrues`): the first
cross-device alarms stay in per-device situations; the merge only happens once the
device pair has accumulated n ≥ 5 co-occurrence observations.

### olt_storm.json ⇒ exactly 1 situation

`test_olt_storm_collapses_to_one_situation_with_damped_learning`: 1 uplink + 500
ONU-unreachable alarms group into exactly one situation (501 alarms); the flagged root
is the uplink alarm. Storm damping is asserted numerically: the onu-onu co-occurrence
mass lands near 48 + 451x0.1 ≈ 93, ~5x below an undamped run.

### Background noise does not merge

`test_background_noise_does_not_merge_into_the_fiber_cut`: an uncorrelated 24-event
noise stream interleaved with the fiber-cut replay produces 24 singleton situations; the
fiber-cut situation contains exactly its 8 alarms and no noise class.

### Matrices demonstrably change, with versioned persistence

Asserted before/after training: `device_affinity` goes 0.0 → >0.9, pair mass crosses the
n ≥ 5 trust threshold, and the learned edge is present in the `edge` table with `weight
> 0.9` (`version` increments on every upsert; see `test_matrix_persistence_is_versioned`).

### Raise/clear and auto-close

The seeded linkDown→linkUp pair clears alarms end-to-end and the flapping fixture is
demoted (`is_flapping = 1`) with zero open situations left. A vendor pair is learned
from strict alternation (X, Y, X, Y), survives restart via the `clear_pair` edge rows,
and retires the stale clear-class alarm created before promotion.

### Property-based tests

Hypothesis fuzz on the parser (Gate 1) plus scorer properties: score ∈ [0, w_t+w_A+w_E],
exact decomposition into the three terms, and monotone decay in Δt.

## Design corrections made during this phase (with regression tests)

- NPMI now normalizes against the activation total and is shrunk by an n/(n+1) evidence
  discount — a single accidental co-occurrence can no longer manufacture a link
  (Decision 8; `test_npmi_single_cooccurrence_is_weak_evidence`).
- Co-occurrence is observed once per activation per distinct class/device, so pair mass
  cannot outrun the activation total mid-incident (Decision 9;
  `test_observe_pairs_dedups_per_activation`).
- Same-class-cross-device affinity is learned, never assumed (would otherwise merge any
  two devices emitting the same trap class;
  `test_same_class_cross_device_affinity_is_learned_not_assumed`).
- Links stored per alarm capped at the 5 strongest (Decision 10) — keeps storms linear
  and audits readable.
