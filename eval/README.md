# `eval/` — offline evaluation, simulation and model training

Development-only: nothing here ships in the wheel or the image (`MANIFEST.in` and `.dockerignore`
prune it), and the product never imports it (`tests/lab/test_lab.py` enforces that).

```text
eval/
├── harness.py        the gate: replays the corpus, compares with the baseline (`make eval`)
├── metrics.py        pairwise F1, ARI, entity accuracy — what the gate measures
├── baselines/        frozen expected metrics and the log of every re-cut
├── corpus/           eleven labelled scenarios, 3 175 events (the gate's input)
├── generators/       scripts that rebuild `corpus/` and the attribution background set
├── simulation/       scenario DSL and the driver that runs scenarios against a live appliance
└── synth/            synthetic estates and incidents; trains the shipped model league
```

## The gate — `harness.py`, `metrics.py`, `baselines/`

`make eval` replays every scenario in `corpus/` through the **real** ingestion path (each event is
encoded as a genuine SNMP trap datagram, parsed by `netcorenoc.ingest.receiver.parse_trap` and
driven through the engine), aligns each predicted grouping with ground truth, and prints the delta
against `baselines/current.json`. It **exits non-zero** on a regression in `pairwise_f1`, `ari` or
`entity_accuracy`, for the aggregate and for each scenario.

Releases quote `python eval/harness.py | sha256sum`; today it is **`43328080…`**. The hash moves only
when grouping behaviour changes on purpose, and each move is recorded in `CHANGELOG.md`. An
unchanged hash means "no grouping decision on these scenarios changed", not "the scorer is
untouched".

| File | Role |
|---|---|
| `baselines/current.json` | The baseline `make eval` compares against. |
| `baselines/v0.2.0.json` | Historical record, asserted by `tests/evaluation/test_eval.py`. Never edited. |
| `baselines/REBASELINE-LOG.md` | Every re-cut: the reason, the digest replaced, the metrics that moved. |

Re-cutting the baseline is a reviewed, single-purpose commit:

```sh
make eval-baseline REASON="why the baseline is being re-cut"
```

The target refuses to run without a reason.

## The corpus — `corpus/`

Eleven hand-shaped scenarios (fibre cut, OLT storm, PON dying gasp, chassis card failure, camera
NVR, dual incidents, flapping and background noise, decoy varbinds). It is a baseline of
**grouping decisions on these scenarios** — not a sample of a real network and not evidence for a
model promotion.

The directory is pinned by digest in `tests/evaluation/test_eval.py` (file names and contents),
because file order is replay order. Adding a scenario means updating that pin and re-cutting the
baseline in the same commit.

Every scenario is also a ready-made traffic source for a running appliance — see
[`docs/simulate.md`](../docs/simulate.md).

## The generators — `generators/`

Scripts, not modules; nothing imports them.

```sh
make corpus                                         # rewrite corpus/*.json from corpus_gen.py
python eval/generators/background_gen.py --check    # verify the shipped attribution background
```

`make corpus` writes into the gate's input, which is why the corpus is pinned: a regeneration that
changes a scenario fails a test instead of passing silently.

## The simulation — `simulation/`

`scenario_dsl.py` describes trap scenarios declaratively and deterministically; `tools/trap_sim.py`
sends them. The other modules boot a real appliance as a process and drive it over UDP and HTTP
(`appliance.py`, `drive.py`), with a simulated network (`generator.py`, `shapes.py`), a simulated
operator (`labelling.py`) and a diagnosis of the results (`diagnose.py`). They are used by
`tests/evaluation/test_simulation.py` and `tests/ops/test_operation.py`.

## Model training — `synth/`

Generates synthetic estates and incident families, records them **through the real engine**, and
trains every member of the model league shipped in `src/netcorenoc/engine/model/league/`.

```sh
make train            # full pipeline; writes the league files (deterministic, checkpointed)
make train-validate   # validation streams only; writes nothing
make train-verify     # reproduce every member's published numbers from the installed package
```

Checkpoints go to `eval/synth/.cache/` (git-ignored).

## The evidence boundary

A generated scenario carries its own `truth`. That truth may **never** reach a training row, a
label or the promotion path: it is traffic an operator may judge, never a label.
`tests/dataset/test_evidence_boundary.py` and `tests/dataset/test_evidence_boundary_observable.py`
enforce this for `eval/` and for `tests/lab/`.

## Which check answers which question

| Question | Check |
|---|---|
| Did a grouping decision change on the corpus? | `make eval` |
| Did the corpus itself change? | `tests/evaluation/test_eval.py` (digest pin) |
| Did the HTTP surface change? | `tests/repo/test_behaviour_identity.py` |
| Does the console still behave? | `make dom` |
| Does a real appliance correlate a fibre cut between two hosts? | `make lab-demo` |
