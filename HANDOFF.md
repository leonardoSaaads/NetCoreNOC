# NetCoreNOC v0.27.0 — handoff

**Where my measurements contradict the brief:** (1) the models compete from the first trap and the
formula decides nothing while a model loads — as asked — but **the day-0 champion is worse than the
retired formula on three of the eleven hand-labelled corpus scenarios** (and better on one). The
judge's registered rule chose it; the numbers are below and in DECISIONS #429. (2) **Live, the lab's
two-host fibre cut became two situations, one per OLT, under every model tried** (random forest,
logistic regression, GAM); v0.26.0's formula made one of 25 alarms. (3) The pooled `make eval` gate
could not see the corpus regressions: it now reads every scenario.

Verified by execution before any change: `__version__` 0.26.0 at `029ede0`, migrations through
`0026`. This hands over **v0.27.0** on `claude-code/trusting-goldberg-j8xpoo`
([leonardoSaaads/NetCoreNOC#46](https://github.com/leonardoSaaads/NetCoreNOC/pull/46)).

## What was asked, and what was built

| asked | built | where |
|---|---|---|
| A **Pending** state: traps the model would add to an *Open* situation wait for an operator | `new → pending → open/new`; accept = merge, reject = `new` and never proposed there again; both answers are labels; lapses when the target leaves `open` | #428; `engine/operate/proposals.py`, `store/proposals.py`, `POST /api/situations/{sid}/proposal`, Situations → Pending |
| No "what a model needs before it can decide" floors; pre-trained models compete from the start | floors removed; the judge ranks the league from day 0 and re-ranks it on site labels | #425; `engine/model/league_judge.py` |
| Decision tree, random forest, … trained and competing | five families over one feature vector: GAM, boosted trees, random forest, decision tree, logistic regression — data documents, never code | #424, #426; `engine/model/league/` |
| The formula "a thing of the past"; the judge picks the best | the formula is only the fail-safe when no model loads; an admin may pin a model (audited) | #425; Settings → Models |
| The ten ML charts, per model, comparable | Judge: every model on shared axes, or one model's ten charts, each naming its dataset and n | #427; `views/parts/league*.js` |
| OpenRAN-style fast and slow loops | fast loop = the batch (champion + sampled challenger shadow); slow loop = the judge every 5 min, writing an append-only decision | #423; `engine/operate/league_loop.py`, `evaluation/league_shadow.py` |
| Settings, Judge, Corpus, Labelling UX on a PC | Settings: four named tabs with introductions; Labelling and Corpus read as sentences | see the live pass below |

## The league, as trained

`make train` (seed 2026, dataset `34c08fafb036b1be`, 160 000 training and 50 000 validation rows):

| # | member | judge score | corpus (mean of 11) | repair / incident (unseen streams; formula 0.403) | quality bar |
|---|---|---|---|---|---|
| 1 | **random forest — champion** | 0.946 | 0.854 | 0.344 | 68 / 70 |
| 2 | logistic regression | 0.947 | 0.909 | 0.346 | 66 / 70 |
| 3 | GAM | 0.948 | 0.842 | 0.371 | 64 / 70 |
| 4 | boosted trees | 0.917 | 0.709 | 0.322 | 69 / 70 |
| 5 | decision tree | 0.902 | 0.593 | 0.344 | 66 / 70 |

The first three tie within the judge's 0.005; the registered tie-break (fewest repair gestures)
puts the random forest first. Every member beats the formula on every generated test split;
none clears the whole bar (it is a scorecard now, #426).

**On the hand-labelled corpus** (pairwise F1, formula → champion): `background_noise` 1.000 →
**0.000** (it merges same-vendor traps from unrelated elements — every member does), `camera_nvr`
1.000 → **0.713** and `pon_pon_port_down` 1.000 → **0.680** (it splits two proxied storms),
`dual_incident_same_vendor` 0.636 → **1.000**; the other seven unchanged. The logistic regression
fails only `background_noise`. **If the corpus is the yardstick you trust most, pin the logistic
regression** in Settings → Models; that is what the pin is for.

## Found and fixed in this release's own verification

- **The corpus suite was pooled over pairs** (#429): three storms hold almost all of them, so a
  member that split a ten-alarm fibre cut still scored 1.000. The judge now averages scenarios, and
  `make eval` gates every scenario. Corrected after the first member's corpus numbers were read,
  before any other member's.
- **A model's link was not explained on the wire** (#430): the explanation was stored and no route
  read it, so "why grouped" showed the formula's three terms, not summing to the score. Found by
  `tests/test_operation.py` over a real socket on the first build with a packaged model.
- **The training run ran out of memory** holding every test stream for the benchmark; streams are
  now read one at a time.

## What was not done

- **A feature that tells a proxied storm from same-vendor background noise** — the reason every
  member fails `background_noise` — and the retraining after it. The same training-data gap is
  the likeliest cause of the split two-host lab cut: the generator's cross-element storms do not
  look like the lab's two OLTs.
- **Real-world labels**: none exist; training is generated data only.
- **Re-recording the dataset with the final code**: its digest changes (the engine's sources moved
  after recording); six streams re-recorded across train, valid and three test splits came back
  content-identical.

## Verification

| gate | result |
|---|---|
| full suite | **2 671 passed, 0 failed** (10 min; the league packaged, the suite on the fail-safe formula unless a test asks for a model) |
| `mypy --strict`, `ruff check`, `ruff format --check` | clean (379 files typed, 419 formatted) |
| `vulture`, `bandit`, `pip-audit --skip-editable` | clean; no known vulnerabilities |
| `make dom` (Node 22) | **101 passed**, none skipped |
| `make eval` | no gated regressions against the re-cut baseline, aggregate **and every scenario**; hash `43328080…`, identical over two runs |
| behaviour-identity record | re-written and identical over two runs: 16 lines added (three console modules and the proposal route, four roles each), 4 removed (`shippedjudge.js`), the rest attributed to the league (`/api/decider`, `/api/judge`, `/api/search`), the champion grouping the seed scenario, `link_terms`/`score_scale` on situation details, and the console's files. Each member's scoring time is pinned in the record like the clock |
| wheel | built; installed in a clean Python 3.12 venv: 0.27.0, **five members load, none refused**, champion `random_forest:d2cdd3cd10c7`, 2 000 benchmark pairs |
| live pass (real appliance, the lab, Chromium) | champion on boot in 2 s; the judge's first decision written and audited; **Pending end to end**: an editor promoted the cut's situation to Open, the next cut's new alarms went to a Pending situation proposing to join it (model probability 0.989), accept merged it (9 → 16 members); live shadow scored 246 pairs per challenger (agreement 94–100 %); both `dual_incident` replays kept apart |
| browser | Situations, Settings (four tabs), Judge, Corpus, Labelling at 1440 and 390 px: **0 px page overflow, 0 console errors**. Found and fixed here: the league table laid out as a flex box (its class collided with the board's container class), a decision time rendered as a table cell inside a paragraph, and two words glued together |
| injections (red with the fix removed, green with it) | the corpus suite pooled again; parity mode letting a model decide; the link explanation unread on the server; the console ignoring it |

## Delivery

- **Schema**: 26 → 27 (`0027`, additive). See `MIGRATION.md`.
- **API**: one route added (`POST /api/situations/{sid}/proposal`); `/api/decider` and `/api/judge`
  describe the league; `/api/situations/{sid}` adds `link_terms` and `score_scale`. No field removed.
- **`make eval`**: `43328080…` (baseline re-cut with its reason in `eval/baselines/REBASELINE-LOG.md`).
