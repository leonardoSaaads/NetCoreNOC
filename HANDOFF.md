# NetCoreNOC v0.29.0 — handoff

**The field review, and two more models.** Eight console items from an operator's review, XGBoost
and k-nearest neighbours in the league (written here, no library), a generator that knows what a bad
day looks like, a longer training — and a second, focused round on incidents whose alarms arrive
minutes apart (ADR #442). Migration `0028` (schema 28) is additive. The v0.28.1
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
regression, **k-NN** (ADR #438). The training data has bad days (ADR #439) and, since the focused
round, incidents spread over time among isolated alerts (ADR #442); the judge averages seven suites,
`test_adverse` and `test_spread` included.

- **The focused round** (ADR #442) began with an analysis of the first round's data
  (`docs/correlation.md`, *Alarms at different times*): the training weight sat in bursts, past a
  minute no feature related two different elements, and a quarter of the late alarms could not
  reach their incident through recall. It added recall by the addresses traps name, five time-spread
  families and their splits, far-end naming, `cross_ref` always kept, and `by_gap` in every manifest.
- **Recording** ~25 min for 380 streams; the focused search ~1 h; grouping and evaluation ~3 h. Every
  phase is cached per kind under `eval/synth/.cache/<digest>/`; the fit cache keys on `train.py` and
  the weighting (`--weights`), so an interrupted build resumes where it stopped.
- **Measured both ways**: the league was trained with the weight balanced over time-gap bands and
  without; validation chose without (fewer repair gestures, bursts intact). `--weights balanced`
  reproduces the other.
- The judge's champion is **logistic regression**. The far-pair gains are largest in the random
  forest and the boosted members, which stay in shadow; a site's labels re-rank the league, and an
  admin can pin one in Settings → Models.

## Known limits, recorded rather than fixed

- **The review's DWDM fibre cut ten times slower** reaches 0.39 pairwise F1 with the champion: the
  break, the services and the protection-and-repair phases land in separate situations. At the
  script's own pace it reaches 0.94. Recall reaches the far end now; what is left is the model's
  confidence across tens of minutes.
- **`background_noise`** is 0.0 since the focused round: a device's two different traps a minute
  apart are now grouped, which the scenario labels unrelated (`eval/baselines/REBASELINE-LOG.md`).
- **Gradient-boosted trees** early-stopped at 500 rounds: ~165 µs per pair, over the 150 µs budget,
  so it never decides.
- Held-out families remain the hardest case for every member (`dwdm_degradation` 0.29-0.36,
  `bgp_flap` 0.33-0.40, `ospf_flap` 0.43-0.46 pairwise F1 for the champion and the random forest):
  they are held out on purpose, to measure exactly this.
- The appliance does not start on native Windows (WSL 2 works; `docs/ROADMAP.md`).

## Verification

| gate | result |
|---|---|
| `ruff check`, `ruff format --check`, `mypy --strict` | clean |
| `vulture`, `bandit` | clean |
| full suite | see the PR description |
| `make eval` | no gated regressions against the re-cut baseline; hash `c80b818b…` (first round `31ea7583…`, v0.28 `43328080…`) |
| DOM tests | executed, including the new maintenance-refusal and brief-judge scenarios |
| live console | screenshots at 1440 and 390 px of the Overview, Maintenance and Judge screens |
