# NetCoreNOC v0.26.0 — handoff

**Where my measurements contradict the brief:** (1) **no model ships.** The model trained for this
release did not pass its own quality bar, so the maintainer's decisions *"models ship pre-trained,
validated and stored in the repo"* and *"the judge acts from day 0"* are **not met**. Every
appliance groups with the additive formula, and the bell says why (DECISIONS #420–#422). (2) The
brief's `dual_incident` over-merge of **1.000 "before" is stale**: v0.18.0's vendor gate (F76)
made it 0.000. 1.000 is true of the same-vendor variant, `dual_incident_same_vendor`, which this
release adds to the corpus.

Verified by execution before any change: `__version__` 0.25.0 at `a53e6e1`, migrations through
`0025`. This hands over **v0.26.0** on `claude-code/trusting-goldberg-j8xpoo`
([PR #45](https://github.com/leonardoSaaads/NetCoreNOC/pull/45)).

## The three numbers

| | measured | on |
|---|---|---|
| **1. Shipped model quality, unseen data** | **None shipped.** The best candidate, on fresh test streams (second reading): repair work **0.316 vs the formula's 0.370** per incident on `test_iid` (32 streams, 4 122 incidents; 95 % CI 0.248–0.403 vs 0.291–0.473), and less than the formula on **all five held-out families** (e.g. `dwdm_line_cut` 1.885 vs 3.310, 174 incidents). It missed the bar on `test_concurrency` (× 0.927 against × 0.90) and, on `make eval`'s hand-labelled corpus, **dropped pairwise F1 from 1.000 to 0.388** | generated streams; `eval/corpus` |
| **2. A fresh appliance's first hour** | What ships (the formula): **2 024 grouping decisions**, pairwise F1 **0.935** [0.795, 0.993], over-merge 0.019, under-merge 0.139, 0.433 repair gestures per incident — 32 fresh streams, 360 incidents, 2 369 alarms. The candidate would have made 2 037 at F1 0.966. Live, day 0: the lab's two-host fibre cut became **one situation of 25 alarms from both hosts** in 40 s | `test_iid`, first 3 600 s of each stream; the lab |
| **3. `dual_incident` over-merge, before → after** | **0.000 → 0.000** (v0.25.0 → v0.26.0 as shipped). Its same-vendor variant: **1.000 → 1.000**, because the formula still decides. The candidate: 0.000 on both | `make eval`, v0.25.0 at `a53e6e1` and this tree |

## What was cut, and why

- **The shipped model.** Two test readings missed the bar (7 of 70 checks, then 2 of 70). Once
  validation covered every regime the bar reads, no grouping setting passed it there, so no third
  reading was taken. The candidate also fails the hand-labelled corpus, which no bar here read.
  Relaxing the one binding limit is the maintainer's call, and it would not be enough (#422).
- **Autonomy and site adaptation in practice**: both need a model. They are built and tested, and
  they are inert on this build; a search is refused with the reason.
- **Real-world labels**: none exist. Training is generated data only.
- **Address or topology features**: the generator's address plan is arbitrary, so such a feature
  would only learn the generator.
- **fANOVA importance**: replaced by Spearman ρ² on the first rung, and the screen says so.
- **Two-dimensional interaction charts**: the manifest carries the tables, and no chart draws them.
- **The Docker Compose lab**: it needs a registry pull. `testbed/run_local.py` ran instead, as in
  v0.17.0.

## How the training data was built, and what it cannot produce

`eval/synth/` generates appliance histories (#408):

- **Estate**: vendors, elements, ONUs, links.
- **Eleven training incident families**: GPON fibre cut, ONU power, OLT card, OLT uplink, router
  link, router board, port flapping, NE reboot, planned maintenance, site power, environment.
- **Nuisances**: noise traps, chatter, clock skew, missing clears.

Every trap is encoded as BER, parsed by the appliance's own parser, and run **through the real
`Engine`** with a probe model, so the features trained on are the features served.

Splits are by **stream**:

- `train` 96 streams, plus `train_long` 12 (the older 70 % for training, the newer 30 % as
  `test_time`, cut by incident);
- `valid` 24 and `valid_concurrency` 12 (tuning only);
- `test_iid` 32, `test_concurrency` 24, `test_optical` 24, `test_protocol` 24.

Rows: 160 000 training, 50 000 validation.

Recording is deterministic: re-recorded, all 132 train/valid streams came back content-identical.
Only gzip's timestamp differs, so the cache files differ in bytes but not in content.

**What it cannot produce**:

- a real vendor's undocumented trap semantics;
- operators' naming habits;
- topology it does not model (DWDM rings, microwave, SD-WAN);
- families nobody wrote;
- a real network's joint fault distribution.

**Measured, not assumed**: a model validated on it split five of the eleven scenarios of
`eval/corpus`, which a different program generates. That is the gap to close first.

## Held-out families and their scores (second reading, fresh streams)

The model never saw these families. Repair gestures per incident, model vs formula, with 95 %
bootstrap intervals over 24 streams:

| family | incidents | model | formula | over-merge (model / formula) |
|---|---|---|---|---|
| `dwdm_degradation` | 143 | 2.881 [2.527, 3.292] | 3.622 [3.325, 3.866] | 0.524 / 0.482 |
| `dwdm_line_cut` | 174 | 1.885 [1.579, 2.177] | 3.310 [3.069, 3.521] | 0.563 / 0.529 |
| `optical_protection` | 140 | 0.429 [0.169, 0.622] | 0.493 [0.182, 0.821] | 0.386 / 0.393 |
| `bgp_flap` | 194 | 2.459 [2.096, 2.849] | 2.845 [2.555, 3.155] | 0.402 / 0.433 |
| `ospf_flap` | 205 | 2.605 [2.104, 3.127] | 3.122 [2.792, 3.453] | 0.268 / 0.346 |

Every held-out check passed on this reading. On the first reading, with the earlier grouping rule,
four of the five failed on over-merge (#420 has both tables).

## The superseding ADRs (`docs/adr/DECISIONS.md`, appended)

- **#415** supersedes *"do not replace the formula with ML"*. The shipped model is the default
  decider, the formula becomes opt-in, and the formula stays reachable and serves as the fallback.
- **#416** supersedes *"synthetic truth never enters the promotion path"*. Generated truth trains
  the shipped model and never enters a site verdict.
- **#417** supersedes *"`INSUFFICIENT_EVIDENCE` is terminal within a release"*. It is terminal for
  the labels it was computed on.
- **#420–#422** are this release's own record:
  - the first reading, and the selection rule that caused it;
  - the second reading, and validation for every regime;
  - why nothing ships, and the maintainer's options.

## Sufficiency floors and their reasoning (#411)

A site model is judged against the shipped one on this site's newest 30 % of labelled incidents,
paired per incident, with a 95 % bootstrap. A do-no-harm check runs on 2 000 generated benchmark
pairs (at most 0.02 nats worse).

The floors are **20 labelled incidents, 6 in the newest part, 4 splits asserting a negative, and
labels from 3 days**. The reasoning:

- A paired interval's half-width is about t₀.₉₇₅,ₙ₋₁/√n SD. At n = 6 that is 2.571/2.449 ≈ 1.05 SD,
  so six is the least that can declare anything.
- 80 % power for a 1-SD effect needs about ten.
- Twenty keeps the training part at 14 or more.
- Three days means the labels cannot all come from one shift.

The floors replace `operators ≥ 3`, which measured independence of opinion less directly.

## What autonomy can and cannot do (#412)

**It can**, once an admin turns a grade on:

- accept a grouping, name, close or grade a situation — four grades, each its own switch, all off
  by default;
- every act is attributed to the model and explained, and the schema refuses an empty explanation;
- each act is judged by what operators do next;
- it **turns itself off** below the agreement floor (80 % of the last 30 judged, at least 8);
- **any editor can stop it from the top bar of every screen**.

**It cannot**:

- act on the formula's situations, since it needs a probability — **so on this build it does
  nothing**;
- undo an operator;
- run on the ingest path;
- write `operator_name`. It has its own column, `model_name`.

## Charts drawn, and charts that cannot be drawn

- **Drawn when a model ships** (built, and exercised by the DOM tests against a stand-in model),
  on generated data:
  - headline tiles with CI and n;
  - F1 by split and held-out family;
  - precision–recall against its baseline, and ROC;
  - calibration with Murphy's decomposition;
  - confusion at the threshold;
  - first-hour bars;
  - the search's history, score × hyperparameter, importance, time × performance;
  - train vs validation;
  - the ablation;
  - one shape per feature.
- **Drawn on this build**: sufficiency (site data) and the live monitor (live traffic). The
  generated-data block says there is no model.
- **Cannot be drawn**:
  - interaction heat maps (no chart built);
  - a site calibration curve (needs more held-out labels than the floors);
  - drift of live scores against training (needs the training score distribution in the
    manifest);
  - per-family site charts (sites label incidents, not families);
  - anything of a model on this build.

## What was removed, one sentence each

- **The Link scorer screen as a navigation entry**: its content is Settings → Correlation, and
  `#/scorer` still lands there.
- **Learning from closed situations**: it fed the running decider's opinion back into the
  features (#406).
- **Immediate resolution of an all-cleared situation**: it is held 300 s for a bounce (#410).
- **Connected components for trained models**: it is kept for the formula (#418).
- **`autonomy.warnings`**: unused, and replaced by the live-stats warning.

## What the live pass did not cover

- **A shipped model live**: there is none. The model screens were exercised only by DOM tests with
  a stand-in model.
- **Autonomy acting**: it was switched on and the kill switch was shown. Nothing acted, because
  there is no model.
- **A running site search**: it is refused on this build (409, with the reason).
- **A real phone or other browsers**: 390 px is Chromium's emulated viewport. Safari and Firefox
  were not driven.
- **Assistive technology**: no screen reader was run.
- **The dark theme**: not photographed.
- **Real traffic**: the lab's two hosts and two corpus replays, not a network.
- **An upgrade under load**: the v0.25.0 → v0.26.0 boot used a database built from `fiber_cut`.

## Verification (Part VIII)

| gate | result |
|---|---|
{{VERIFICATION}}

### Injections

{{INJECTIONS}}

## Delivery

- **ZIP**:
  - `HANDOFF.md` at the root, and the tree under `NetCoreNOC/` with its full `.git`.
  - The clone arrived shallow and was unshallowed: 362+ commits, tag `v0.12.0`, the only tag on
    the remote. No v0.26.0 tag is created, because a tag belongs on the merged commit.
  - No caches: `make_zip` walks the tree, prints what it leaves out, and refuses to write if a
    cache got in.
- **Verify from the unpacked copy**:
  - `make qa`
  - `make dom`
  - `python -m pytest tests/test_behaviour_identity.py`
  - `python eval/harness.py | sha256sum` gives `31ea7583…`
  - `python testbed/run_local.py --demo`
  - `make dist` then a clean venv (`pip install dist/*.whl`); the installed wheel reproduces the
    eval hash.
- **Reproduce the refusal**: `make train` from this tree reproduces *"no grouping setting passes
  the quality bar on validation; the model is not shipped"*. It takes about 90 minutes without
  caches, and it reads no test split.
- **Schema**: 25 → 26 (`0026`, additive). See `MIGRATION.md`.
- **API**:
  - 11 routes added: `/api/decider`, `/api/autonomy*`, `/api/search*`, `/api/judge`,
    `/api/situations/{sid}/severity`.
  - No field was removed.
