# Security Review — NetCoreNOC v0.6.0

> **Status: closed at Gate 4.** Every row below names the regression test that proves it and the
> status it earned. The skeleton was written in Phase 1 against the design; nothing in it was
> relaxed to make a test pass, and the two rows that could not be closed *completely* say so and
> carry a `docs/ROADMAP.md` line.

An adversarial review of everything v0.6.0 adds: the `LinkScorer` seam
(`src/netcorenoc/scoring.py`), Tier A admin-configurable scoring parameters (persistence,
validation, read path, preview, apply/rollback, provenance), and the removal of the legacy
`OPTICORR_*` environment aliases.

The release is deliberately narrow, and that shapes the review. v0.6.0 adds **no outbound call,
no dynamic code loading, no new runtime dependency, and no change to
`receiver.datagram_received`**. What it does add is a *stored input that changes grouping logic*,
a *compute-bearing admin endpoint*, and *one provenance column*. Those three are what this review
attacks.

Kept **honest**: an unmet control is listed unmet with a ROADMAP line. Findings continue the
series from **F20** (F1–F14 v0.1–v0.4, F15–F19 v0.5.0); no existing finding is renumbered.

Status legend: **met** (file + test prove it) · **planned** · **N/A** (one-line reason) ·
**partial** (met with a documented gap → ROADMAP).

## 1. Standards anchor (continued from v0.4.0/v0.5.0)

- **Application**: OWASP ASVS 4.0.3 Level 2 — re-verified for the three new routes and the new
  stored configuration. Relevant sections: V1.4 (access-control architecture), V4.1/V4.2
  (access control, function-level), V5.1 (input validation), V7.1/V7.2 (logging content and
  integrity), V8.3 (sensitive private data), V12/V13 (API and file/resource limits).
- **Change management / integrity**: the append-only + hash-chain discipline already used for
  `audit_log` is reused verbatim for `scorer_config` — same trigger pattern, same tamper-evidence
  argument.
- **Supply chain**: unchanged. **Zero** new runtime dependencies is itself the control; the
  release is arithmetic, a hash, bounded queries, and set operations.

## 2. Findings — F20…F26 (continuing the F1–F19 series)

| # | Sev | Area | Finding / property asserted | Fix / control | Test | Status |
|---|-----|------|------------------------------|---------------|------|--------|
| F20 | **Med** | Parameter poisoning (the footgun) | A syntactically valid parameter set can destroy correlation without touching an alarm row: `threshold ≤ 0` links every candidate pair into one giant situation; `threshold ≥ w_t+w_a+w_e` links nothing, ever; a near-zero weight sum makes the threshold unreachable. Range checks alone do **not** catch these. | One validation function (`scoring.validate_params`) enforcing named bounds *and* degeneracy rules (DECISIONS #46): weights and threshold in `[0,1]`; `MIN_TAU_S ≤ τ ≤ MAX_TAU_S`; `weight sum ≥ MIN_WEIGHT_SUM`; `MIN_THRESHOLD ≤ threshold ≤ (w_t+w_a+w_e) − THRESHOLD_MARGIN`. Invalid ⇒ 4xx with a precise reason, **never stored**. Backed by preview-before-apply, audit, immutable history + one-action rollback, and the coded-default fallback. | `test_scoring.py::test_f20_out_of_bounds_and_degenerate_sets_are_rejected` (14 cases), `…_boundaries_are_inclusive_where_documented`, `…_degenerate_extremes_would_indeed_have_been_catastrophic`, `test_scorer_api.py::test_f20_invalid_parameters_are_rejected_and_never_stored` (6 cases) | **met** |
| F21 | **Med** | Privilege boundary | Retuning the formula is a system-wide logic change; the v0.5.0 draft's "optionally editor, if the admin delegates" would have made it reachable from an editor session. A stored change must also never escalate a live session. | `scorer.preview` / `scorer.write` are **admin-only, no delegation**, in the single `rbac.py` map; `scorer.read` is viewer+ (an explanation, not a secret). Every new route is in `ROUTE_PERMISSIONS` (deny-by-default); 401/403/404 semantics unchanged; a scoring config is not an authorization input at all, so it grants nothing. | `test_scorer_api.py::test_f21_scorer_capabilities_are_admin_only_with_no_delegation`, `…_non_admin_cannot_preview_write_or_roll_back` (6 cases), `…_denied_attempts_are_audited`, `…_config_change_grants_no_capability`, plus the generated `test_rbac.py::test_authorization_matrix` | **met** |
| F22 | **Med** | Preview as DoS / exfiltration surface | A what-if endpoint is unbounded compute on demand and a candidate side channel: it could re-partition all history, mutate state, or return fields the caller could not otherwise read. | Bounded by `MAX_PREVIEW_ALARMS` **and** `PREVIEW_TIMEOUT_S` **and** the existing token-bucket limiter **and** admin-only auth. Read-only: a throwaway `Correlator`, learned matrices held fixed, zero writes. Deterministic (fixed ordering, no wall clock in the scored path). Returns **aggregate structural deltas only** — no varbinds, no payloads, no field beyond `shaping.py`. Off the ingest path. Imports nothing from `eval/`. | `test_scorer_api.py::test_f22_preview_mutates_nothing`, `…_is_deterministic_across_two_runs`, `…_discloses_no_new_fields`, `…_is_bounded_by_the_alarm_cap`, `…_is_bounded_by_a_wall_clock_budget`, `…_has_its_own_tight_rate_limit`, `…_timeout_is_reported_not_faked`, `…_does_not_disturb_the_running_engine`, `test_scoring.py::test_f24_preview_module_never_imports_the_eval_harness` | **met** |
| F23 | **Med** | Provenance integrity & reproducibility | If the parameter history behind a past grouping can be edited or lost, post-incident review in a regulated NOC cannot answer "how was this situation scored?" — and an attacker could rewrite the apparent decision rule. | `scorer_config` is append-only at the storage layer (`BEFORE UPDATE`/`BEFORE DELETE` → `RAISE(ABORT)`), like `audit_log` but with **no sanctioned deleter** (retention prune does not touch it). Rollback moves a one-row pointer; rows are never mutated. Every situation carries `scorer_config_id`; existing rows backfill to the seed, which *is* the coded defaults that formed them. Each row carries `params_hash` + `contract_version`. | `test_scoring.py::test_f23_situation_provenance_recovers_exact_parameters`, `…_scorer_config_is_append_only`, `…_prune_does_not_touch_scorer_config`, `test_migration.py::test_migrate_populated_v050_database_seeds_the_scorer_config` | **met** |
| F24 | **Low** | Hot-path surface | The prime directive is that ingestion gains nothing. A parameter read, a provenance write, or a preview run in the wrong place would violate it silently. | `receiver.datagram_received` is unchanged, asserted over its own source: the callback must contain none of `scorer`/`scoring`/`score`/`config`/`await`/`lock`/`execute`, and must still do exactly its four v0.5.0 things (allowlist, parse, quarantine, enqueue). `receiver.py` is additionally asserted not to import `correlate`, `scoring`, or `preview` at all. Parameters load at engine configuration load; provenance is written in `_assign_situation` under the batch lock; preview is HTTP-side only. Ingest microbenchmark (`test_perf.py`) unregressed. | `test_scoring.py::test_f24_datagram_received_is_unchanged_and_touches_no_scoring`, `…_receiver_module_does_not_import_the_scoring_seam`, unchanged `test_perf.py::test_ingestion_lossless_with_concurrent_authed_audited_api` | **met** |
| F25 | **Med** | Fail-safe scorer execution | A scorer that raises, hangs, or returns a malformed `LinkScore` must not stall or crash the engine — and must not silently disable correlation either. | Every scoring call goes through a fail-safe wrapper: on any exception, timeout, or contract violation the engine falls back to the coded-default `AdditiveScorer`, audits `scorer.fallback` (system actor) once, and raises a persistent operator warning. The engine can never run scorer-less. Proven with test-only raising / empty-terms / slow scorers, which also prove the `Protocol` accepts implementations that are not `AdditiveScorer`. **Gap:** a synchronous in-process call that hangs *forever* cannot be interrupted from the wrapper; the budget is checked after the call returns and degrades every subsequent call. Stated in the code, in `DESIGN.md`, and in §4. | `test_scoring.py::test_f25_raising_scorer_falls_back_to_the_defaults`, `…_scorer_that_returns_no_terms_is_a_contract_violation`, `…_over_budget_scorer_degrades_on_the_next_call`, `…_degradation_is_sticky_and_counted_once`, `…_engine_never_runs_scorerless_and_audits_the_fallback` | **partial** — see §4 |
| F26 | **Low** | Removed-knob misconfiguration | A removed `OPTICORR_*` alias that silently no-ops is a **security** regression: an operator still setting `OPTICORR_ALLOWLIST` would believe traps are filtered while every source is accepted. | Setting any `OPTICORR_*` variable is a hard startup error naming each variable, its `NETCORENOC_*` replacement, and `MIGRATION.md` — mirroring the v0.3.0 `OPTICORR_API_TOKEN` removal. The message names variables, never values; no secret is printed. | `test_main.py::test_f26_legacy_env_prefix_is_a_startup_error`, `…_legacy_env_error_names_no_value`, `test_cli.py::test_f26_cli_refuses_a_removed_legacy_env_alias` | **met** |
| — | — | Migration integrity (F12 class) | The new migration must ship in the wheel **and** sdist, apply to a populated v0.5.0 DB with data intact and the audit chain verifying, and its seed must make the result byte-identical. | `0005_scorer_config.sql` under the existing `migrations/*.sql` package-data glob; forward-only, additive; seed row = coded defaults, marked active; existing situations backfilled to it. | `test_migration.py::test_migrate_populated_v050_database_seeds_the_scorer_config`, `test_upgrade.py::test_v060_upgrade_preserves_grouping_and_seeds_provenance`, `test_supply_chain.py`, plus the Gate 4 built-wheel/sdist install check | **met** |
| — | — | New runtime attack surface | The three new `/api/scorer*` routes are the only new served paths; no new dependency, no dynamic loading, no outbound call. | All three under the existing `security` dependency, CSP + `SECURITY_HEADERS` middleware, `Cache-Control: no-store`, and the rate limiter. Runtime dependency list unchanged. | `test_scorer_api.py::test_scorer_routes_carry_the_security_headers`, `test_security_ui.py::test_scorer_panel_*`, `test_supply_chain.py` | **met** |

## 3. Rejected by design (recorded, not mitigated)

`EXTENSIBILITY-0.6-DRAFT.md` §3 Tier B specified an external API supplying the linking criterion,
with SSRF, stall-DoS, and untrusted-response-injection threats each carrying a control. v0.6.0
**rejects the design** (DECISIONS #44): `LinkScorer.score` is pure, deterministic,
side-effect-free and inference-only, so no outbound call can decide a link. There is no socket to
allowlist, no timeout to tune, and no response to validate. Removing a hazard is not the same as
controlling it, and this review records which one happened — see `threat-model.md`,
"Rejected by design".

## 4. Critical analysis (prose) — residual risk

An honest assessment of where these choices could still bite:

- **A determined admin can still detune correlation, and that is by design.** The controls are
  bounds (refusing the shapes that cannot be right), preview (showing the effect first), audit
  (recording who and when), immutable history plus one-action rollback (making it undoable), and
  the coded-default fallback. None of them is *prevention*, because an admin is trusted to change
  system logic by definition. What the design guarantees is that such a change is **bounded,
  visible, attributable, and reversible** — not that it is impossible. An operator who tunes to
  the edge of the allowed range and never previews can still degrade grouping, and only the audit
  trail and the eval discipline will tell them so.
- **Preview is directional, not exhaustive — and the UI must say so.** It re-partitions a bounded
  *recent* window (≤ `MAX_PREVIEW_ALARMS`) with the learned matrices held fixed. It therefore
  predicts the **immediate** effect of a parameter change, not the long-run effect after `A` and
  `E` adapt to the new grouping (a genuine feedback loop: grouping shapes learning, which shapes
  grouping). A preview that presented itself as authoritative would be worse than no preview at
  all, so the panel copy states the limit, and this review treats the wording as a control.
- **Bounds are a judgement call and will occasionally be wrong.** DECISIONS #46 resolves every
  ambiguity toward the tighter bound, on the grounds that a slightly-too-tight bound costs tuning
  range while a too-loose one lets an admin shatter or collapse every incident. That means some
  legitimate exotic configuration is refused. The recourse is a code change with a decision entry,
  which is the right amount of friction for a global logic change; it is not silently
  overrideable.
- **Provenance is per-situation, not per-link.** `situation.scorer_config_id` records the
  configuration in effect when the situation was **created**. A long-lived situation that keeps
  absorbing alarms across a parameter change carries its original `config_id`, while later links
  were scored under the newer configuration. This is a deliberate simplification (one column, one
  write, no hot-path cost); the honest reading of the field is "the configuration this situation
  was opened under". Per-link provenance is a ROADMAP line, not a shipped claim.
- **The eval gate proves parity, not quality, of a retuned configuration.** `make eval` is
  byte-identical **at the default parameters**; it deliberately does not score customer-tuned
  parameters, because the corpus is not the operator's network. Nothing in this release tells an
  operator their new weights are *better* — only what they would do to recent grouping.
- **`scorer_config` cannot be pruned.** Immutability is the tamper-evidence argument, so there is
  no sanctioned deleter (unlike `audit_log`'s audited retention prune). The growth is a handful of
  rows per year, which is the right trade; an operator who wants that history gone must delete the
  database.
- **F25 is *partial*, and the gap is real.** `SafeScorer` catches exceptions, contract violations,
  and over-budget calls, and it degrades permanently to the coded defaults. What it cannot do is
  *interrupt* a synchronous in-process call that never returns: `score()` runs on the engine task,
  and the budget is therefore checked **after** the call completes, degrading every subsequent
  call rather than the offending one. For v0.6.0 this is a theoretical gap — exactly one scorer
  ships and it is five floating-point operations with no loop, no I/O and no allocation that could
  block. It stops being theoretical the moment customer-supplied code can run, which is precisely
  why `SCORER-PLUGINS-0.8-DRAFT.md` specifies a worker process with `resource.setrlimit` and real
  preemption as a **prerequisite** of that release rather than an enhancement. Recorded as a
  ROADMAP line; overclaiming it as "timeout protection" would have been the dishonest option.

Next-version follow-ups are `docs/ROADMAP.md` lines, not scope creep: **real scorer preemption**
(a worker process + `resource.setrlimit`, a v0.8.0 prerequisite); per-link scorer provenance;
per-archetype weight profiles; generalised per-link attribution storage (needed by v0.8.0's
customer models); and an operator-facing "effect of the last parameter change" report derived from
the provenance column.

## 5. Threat-model delta

`docs/security/threat-model.md` gains a v0.6.0 section covering: parameter poisoning; the
admin-only privilege boundary and the explicit non-delegation; preview as a DoS and exfiltration
surface; provenance integrity and reproducibility; fail-safe scorer execution; the `OPTICORR_*`
removal as a misconfiguration threat; and a "rejected by design" subsection recording that the
Tier B external-criterion threats were removed rather than mitigated. Each names a control and a
test.

## 6. Tool baselines (inputs, re-run each gate)

`bandit`, `pip-audit`, `ruff`, `vulture`, `mypy --strict`, the documentation link check, the
GitHub-Actions SHA-pin lint, and the structure guard re-run and recorded per gate in
`docs/gates/v0.6-phase-*.md`. Baseline carried from v0.5.0 (`docs/gates/v0.6-phase-0.md`): 328
tests, 95.02 % coverage, all tool gates clean, `make eval` exit 0 with no gated regressions.
