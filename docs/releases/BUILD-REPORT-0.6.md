# Build report — NetCoreNOC v0.6.0 ("the scoring seam")

**One sentence:** the correlation formula stopped being a hard-coded expression and became the
default implementation of a versioned, swappable, explainable interface — with admin-tunable
parameters, a read-only preview, decision provenance and one-click rollback — **without adding a
single runtime dependency, a single byte of hot-path work, or a single black box**, and without
moving a single number at the default parameters.

## What changed

### 1. The `LinkScorer` seam (`src/netcorenoc/scoring.py`, 204 code lines)

- **`LinkScorer`** — a `Protocol` with `scorer_id`, `contract_version`,
  `score(LinkFeatures) -> LinkScore` and `params_fingerprint()`. `score()` is typed **pure,
  deterministic, side-effect-free and inference-only**; that is not a comment, it is the
  type-level statement that forecloses an external scoring criterion on the hot path.
- **`LinkFeatures`** — the current inputs (`delta_t_s`, classes, `class_affinity`, NEs,
  `entity_affinity`) plus **reserved** optional slots (`severity_i/j`, `topo_distance`,
  `probable_cause_i/j`, `event_type_i/j`) that are `None` in 0.6 and unread by the default
  scorer, so X.733/3GPP features and v0.8.0's richer scorers are a **minor** contract bump.
- **`LinkScore.terms`** — a per-term breakdown, **contractually required** of every scorer. The
  default emits `temporal`, `class_affinity`, `entity_affinity` with today's numbers.
- **`AdditiveScorer`** — the built-in score with the five parameters as dataclass fields,
  completing the v0.5.0 P2 tidy. `correlate.py` now selects candidates and applies the verdict; it
  no longer inlines the arithmetic.
- **`SafeScorer`** — the fail-safe wrapper. Any exception, contract violation, or budget overrun
  degrades permanently to the coded defaults, audits `scorer.fallback` once, and raises an
  operator warning.

### 2. Tier A — admin-configurable parameters, preview, provenance, rollback

- **Storage** (`0005_scorer_config.sql`, `user_version` 4 → 5): an **append-only, immutable**
  `scorer_config` table with `RAISE(ABORT)` triggers and — unlike `audit_log` — **no sanctioned
  deleter**; a one-row `scorer_active` pointer, so **apply and rollback are the same UPDATE**; a
  nullable `situation.scorer_config_id`; a seed equal to the coded defaults; and a backfill.
- **Read path**: parameters load at `Engine.start()` and at the top of each maintenance pass
  (≤ 5 s) — never per packet, never mid-batch, so a situation's recorded `config_id` is always the
  one that actually scored it. A reload that finds the same `(id, params_hash)` is a no-op, which
  is what keeps a fail-safe degradation sticky.
- **Validation** rejects the **degenerate**, not merely the out-of-range (DECISIONS #46).
- **Preview** (`src/netcorenoc/preview.py`, 89 code lines): a bounded, deterministic, read-only
  re-partition of recent alarms under the candidate parameters, returning aggregate structural
  deltas. Imports nothing from `eval/`; writes nothing but its own audit row.
- **RBAC**: `scorer.read` viewer+, `scorer.preview` / `scorer.write` **admin only, no editor
  delegation**. Audit: `scorer.config.update`, `scorer.preview`, `scorer.fallback`.
- **UI**: an admin-only **Scorer** panel, pruned from a non-admin DOM entirely.

### 3. The `OPTICORR_*` removal

Deprecated in v0.4.0, extended one version in v0.5.0, **removed here** as promised. Setting any
`OPTICORR_*` variable is a hard startup error naming each variable and its replacement, in the
server *and* the audit CLI.

### 4. Groundwork specified, not built

`GOVERNANCE-0.7-DRAFT.md` (admin RBAC + visibility scoping) and `SCORER-PLUGINS-0.8-DRAFT.md`
(blessed ONNX adapter + Python entry-point hatch). `EXTENSIBILITY-0.6-DRAFT.md` is superseded in
place, never rewritten.

## The parity proof

This is the claim the release stands on, so it is mechanical rather than argued. Both `make eval`
modes were hashed on the untouched v0.5.0 tree **before any edit** and re-hashed after the
extraction and again at the end:

| Mode | sha256 (v0.5.0 and v0.6.0 — identical) |
|---|---|
| learning | `c873d525abd1e8c20a839d37e1c1bc1813f26bbe9ba4bed3e4a3783d5fd6d1bd` |
| cold / parity | `811b4c54231c2b9621f1d58a4020a0b6f02023b1b3a1e95e475ab8d1bbc82754` |

Why it held: the same three products, summed left-to-right in the same order (float addition is
not associative — the *order* was the thing that had to survive), from the same affinity calls,
compared with the same strict `>`. A property test asserts equality with `==`, not a tolerance; a
tolerance would hide exactly the class of error the gate exists to catch. And
`test_upgrade.py::test_v060_upgrade_preserves_grouping_and_seeds_provenance` replays one fixture
on both sides of the migration and compares the situation partitions member-for-member.

## Quality numbers

| | v0.5.0 | v0.6.0 |
|---|---|---|
| Tests | 328 | **419** (+91) |
| Coverage | 95.02 % | **95.06 %** (floor: 92.02 %) |
| Runtime dependencies | 5 | **5** (unchanged) |
| Migrations | 4 | 5 (one added) |
| `mypy --strict` | clean | clean (69 files) |
| `ruff`, `bandit`, `pip-audit`, `vulture` | clean | clean |
| `make eval` | exit 0 | exit 0, byte-identical |

## Decisions (DECISIONS #43–#50)

| # | Decision |
|---|---|
| 43 | Resequence the three configurability surfaces across three releases — different risk profiles must not share a release, because a parity gate only means something when nothing else can move a number |
| 44 | **Reject** the external-criterion API on the hot path rather than mitigating it — the strictest option is not having the socket |
| 45 | Remove the `OPTICORR_*` aliases with a hard startup error — a silently-ignored allowlist is a security regression, not a nuisance |
| 46 | Bound parameters against **degeneracy**, not merely against range; every ambiguity resolves toward the tighter bound |
| 47 | Provenance **by reference** (one FK into an immutable table), not five denormalised floats per situation and not archaeology over the audit log |
| 48 | Preview is a bounded in-memory re-partition, not an `eval/` run — the corpus harness stays the gate and never becomes a runtime dependency |
| 49 | Reserve optional `LinkFeatures` slots now, and write down that adding one is a *minor* bump — otherwise the first real extension strands v0.8.0's scorers |
| 50 | Make per-term explainability **contractual** while keeping the three persisted columns as the default scorer's projection — general requirement, specific storage |

## Deferred (ROADMAP lines, not scope creep)

Admin RBAC + visibility scoping (v0.7.0); customer models (v0.8.0); external scoring criterion
(rejected); per-archetype weight profiles; X.733/3GPP scoring features; generalised per-link
attribution storage; per-link provenance; an "effect of the last change" report; **real scorer
preemption**; SNMPv3, `/metrics`, pcap replay, webhook/`Case` emission; the `device_id` cutover.

## Honest caveats

These are the things a reader should know that the feature list will not tell them:

- **A determined admin can still detune correlation.** The controls are bounds, preview, audit,
  immutable history, one-action rollback, and the coded-default fallback — **visibility and
  reversibility, not prevention**. An admin is trusted to change system logic by definition; what
  the design guarantees is that such a change is bounded, visible, attributable and undoable.
- **Preview is directional, not exhaustive.** It re-partitions a bounded *recent* window with the
  learned matrices held fixed, so it shows the **immediate** effect of a change, not where the
  system settles after `A` and `E` adapt to the new grouping — a real feedback loop. The API says
  so in a `caveat` field and the panel prints it; the wording is treated as a control.
- **The eval gate proves parity at the defaults, not the quality of a retuned configuration.**
  Nothing here tells an operator their new weights are *better*. The corpus is not their network.
- **Provenance is per-situation, not per-link.** `scorer_config_id` records the configuration a
  situation was *opened* under; a long-lived situation spanning a parameter change carries its
  original id while later links were scored under the newer one. ROADMAP.
- **F25 is `partial`.** `SafeScorer` cannot interrupt a synchronous in-process call that never
  returns; it degrades every *subsequent* call. Theoretical while the only scorer is five
  floating-point operations, and a stated prerequisite of v0.8.0 rather than an enhancement.
- **`scorer_config` cannot be pruned.** That is the tamper-evidence argument working as intended;
  the cost is a handful of immutable rows per year that retention will never reclaim.
- **The `OPTICORR_*` removal will break someone's deployment on upgrade** — loudly, at startup,
  naming the fix. That is the intended behaviour and it is the only breaking change in the
  release.
- **Docker/compose behaviour is asserted declaratively, not observed.** `docker compose config`
  validates on this machine; the read-only-rootfs and dropped-capability posture remains
  something the maintainer confirms on a real `docker compose up` (carried over from v0.5.0 F15).

## Gates

| Gate | Evidence |
|---|---|
| 0 — comprehension | [`../gates/v0.6-phase-0.md`](../gates/v0.6-phase-0.md) |
| 1 — scope, decisions, specs | [`../gates/v0.6-phase-1.md`](../gates/v0.6-phase-1.md) |
| 2 — design + migration | [`../gates/v0.6-phase-2.md`](../gates/v0.6-phase-2.md) |
| 3 — implementation | [`../gates/v0.6-phase-3.md`](../gates/v0.6-phase-3.md) |
| 4 — verification | [`../gates/v0.6-phase-4.md`](../gates/v0.6-phase-4.md) |
| 5 — release | [`../gates/v0.6-phase-5.md`](../gates/v0.6-phase-5.md) |
