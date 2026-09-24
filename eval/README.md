# `eval/` — four roles under one name

This directory holds four different kinds of thing, and knowing which is which is the whole of what
a newcomer needs from it. v0.17.0 considered splitting them into directories and **refused**, because
the split would have moved files without removing the one real problem (DECISIONS #322, superseded by
#328); the problem was fixed with a digest instead (#327). So the roles are stated here.

## 1. The gate — `harness.py`, `metrics.py`, `baselines/`

`make eval` replays the labelled corpus offline through the **real** ingestion path — each event is
BER-encoded to a genuine trap datagram, parsed by `netcorenoc.ingest.receiver.parse_trap`, and driven
through the engine — then aligns every predicted alarm to ground truth and prints a delta against
the frozen baseline. It **exits non-zero** on a regression in `pairwise_f1`, `ari` or
`entity_accuracy`.

Releases quote `python eval/harness.py | sha256sum`. It is **`c75b42aa…`** today. It held at
`c2e8a0ce…` from v0.7.0 until the corpus was re-cut, and `CHANGELOG.md` records that move with
its reason — this line said *"held at `c2e8a0ce…` since v0.7.0"* for several releases after it
stopped being true, which is the one thing a quoted hash must not do. What makes the number
useful is that it moves **only** when somebody meant it to, so the value here is the current
one and the history is in the log.

**What it can and cannot see.** It is a snapshot of *aggregate* metrics, so it sees a change that
moves one and nothing else. Measured in v0.17.0: forcing every pair unlinked collapses it to two
gated regressions, and **halving the class-affinity term moves nothing at all**, because no link on
this corpus crossed the threshold differently. Do not read an unchanged hash as "the scorer is
untouched"; read it as "no grouping decision on these ten scenarios changed".

### Re-cutting the baseline

    make eval-baseline REASON="why the baseline is being re-cut"

It **refuses without a reason** and appends the digest it replaced beside the digest it wrote, plus
every aggregate metric that moved, to `baselines/REBASELINE-LOG.md` (#324). A baseline exists to
answer *did this change behaviour when I did not mean to?*; it is not a reason the corpus may never
grow, and a re-cut nobody can audit is what the mandatory reason prevents.

## 2. The corpus — `corpus/`

Ten labelled scenarios, 3 159 events. **What it is a baseline of: grouping decisions on these ten
scenarios.** It is not a baseline of scoring arithmetic, not a sample of any customer's network, and
not evidence for a promotion (`docs/analysis/PREREGISTRATION-0.10.0.md` §6).

Pinned by digest in `tests/test_eval.py` — path and contents, with the scenario and event counts
beside it. The filenames are in the digest because `harness.run_all` globs this directory, so they
are the **replay order**. Growing the corpus means updating that pin and re-cutting the baseline, in
one reviewable commit.

## 3. The generators — `corpus_gen.py`, `background_gen.py`

Scripts, not modules: nothing imports them.

    make corpus                          # rewrite corpus/*.json from the generator
    python eval/background_gen.py --check  # compare the shipped constant against its derivation

`make corpus` **writes into the gate's subject**. That is why the corpus is pinned: a regeneration
that changes a scenario used to be invisible, and is now a red test.

## 4. The simulation — `scenario_dsl.py`, `simulation/`

A declarative scenario DSL and the package that drives a **live appliance** over UDP with it —
`appliance.py`, `drive.py`, `generator.py`, `labelling.py`, `shapes.py`, `diagnose.py`. Used by
`tests/test_simulation.py` and `tests/test_operation.py`, and by `make sim`.

    make sim SCENARIO=login_burst        # against a running appliance on port 1162
    make replay-list                     # every scenario of both kinds, derived

**The evidence boundary.** A generated scenario carries its own `truth`, and that truth may never
reach a training row, a label or the promotion path. It is traffic an operator may judge, never a
label. `tests/test_evidence_boundary.py` and `tests/test_evidence_boundary_observable.py` are the
guards; `testbed/` is bound by the same rule and by the same tests.

## Which instrument answers which question

| Question | Instrument |
|---|---|
| Did a grouping decision change on the ten scenarios? | `make eval` |
| Did the corpus itself change? | `tests/test_eval.py`'s digest (#327) |
| Did the HTTP surface change? | `tests/behaviour_identity.py` |
| Does the console still behave? | `make dom` |
| Does a real appliance still correlate a fibre cut? | `testbed/` |
