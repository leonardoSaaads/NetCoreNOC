# NetCoreNOC v0.29.0 — handoff

**The field review, and two more models.** Eight console items from an operator's review, XGBoost
and k-nearest neighbours in the league (written here, no library), a generator that knows what a bad
day looks like, and a longer training. Migration `0028` (schema 28) is additive. The v0.28.1
handoff (the repository reorganisation) is at `2b86120`: `git show 2b86120:HANDOFF.md`.

## What changed, by the review's numbering

| # | Report | Now | ADR |
|---|---|---|---|
| 1 | Overview charts change shape on refresh | buckets on fixed boundaries; a bucket is the peak of 8 sub-readings | #437 |
| 2 | cards of uneven height | the severity card fills its row; Top assets shows six rows like Situations | #437 |
| 3 | Planned work and The models: too much text | three counts and the next three windows; who decides and three charts from `GET /api/judge?brief=true` | #437, #441 |
| 4 | two more "keeping up" metrics | trap rate and correlation latency p95, sampled with the host series (`0028`) | #437 |
| 5 | moving twenty alarms: twenty gestures, a ghost situation, no Confirm | one gesture, one label; moving every member is a merge; Confirm stays | #436 |
| 6 | maintenance UX; Confirm did nothing | refusal shown on the row; confirm as you create; presets; Back/Next; patch band 1 min | #440 |
| 7 | Who decides: too much text | five icons, a line each | #441 |
| 8 | Learning and comparison: unclear, wrong scales | three groups; log axes where values span decades | #441 |

## The models

Seven members: GAM, gradient-boosted trees, **XGBoost**, random forest, decision tree, logistic
regression, **k-NN** (ADR #438). The training data adds seven adverse families and a bad-day regime
— twin incidents, storms, working-day noise, a congested management network — and the judge
averages six suites, `test_adverse` included (ADR #439).

- `make train` took ~8 h on 4 cores (recording 14 min, search 1.4 h, grouping and evaluation the
  rest). Every phase is cached per kind under `eval/synth/.cache/<digest>/`, so an interrupted
  build resumes where it stopped.
- **The first full training was not shipped**: the feature ablation had dropped `same_ne`, and
  every member split the corpus's fibre cut. The fix (`train.CORE_FEATURES`) is in ADR #439 with
  the experiment that confirmed it.
- Every member needs fewer operator repair gestures than the formula on every test split. The
  champion is the **decision tree**; it merges the corpus's `dual_incident_same_vendor`, which the
  other simple members keep apart — the one gated scenario that moved down. The numbers are in
  `CHANGELOG.md`; the reason in `eval/baselines/REBASELINE-LOG.md`.
- Gradient-boosted trees and XGBoost show the largest train/validation gap (~10 %) and the weakest
  corpus suite; the judge ranks them last. k-NN's explanation is a sampled Shapley estimate,
  labelled `shapley-sampled`.

## Known limits, recorded rather than fixed

- `dual_incident_same_vendor` with the decision tree deciding (above). A site's labels re-rank the
  league within minutes; an admin can pin another member in Settings → Models.
- Held-out families remain the hardest case for every member (`dwdm_degradation`, `ospf_flap`,
  `bgp_flap` around 0.4–0.5 pairwise F1): they are held out on purpose, to measure exactly this.
- The appliance does not start on native Windows (WSL 2 works; `docs/ROADMAP.md`).

## Verification

| gate | result |
|---|---|
| `ruff check`, `ruff format --check`, `mypy --strict` | clean |
| `vulture`, `bandit` | clean |
| full suite | see the PR description |
| `make eval` | no gated regressions against the re-cut baseline; hash `31ea7583…` (was `43328080…`) |
| DOM tests | executed, including the new maintenance-refusal and brief-judge scenarios |
| live console | screenshots at 1440 and 390 px of the Overview, Maintenance and Judge screens |
