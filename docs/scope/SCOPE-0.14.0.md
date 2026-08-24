# SCOPE — v0.14.0, the model family

**Theme: three new scorer kinds, in process and in pure Python, and the first end-to-end walk of the
whole evidence chain.**

Two sentences shaped the method, and both are worth stating before the scope.

1. **The kinds are the easy half.** A decision tree over three features is a first-year exercise. The
   hard half is that a tree cannot enter this project the way a model enters most projects: it has to
   pass the degeneracy rules registered *before* it existed, decompose its own decision exactly, load
   through one dispatch, fit byte-identically across two processes, and add **zero** runtime
   dependencies. Every one of those is a constraint the release inherited rather than chose.
2. **The chain has never been walked.** Every release since v0.9.1 has returned
   `INSUFFICIENT_EVIDENCE` on a corpus with zero asserting bags, so the promotion machinery has been
   verified by unit test and never by use. This release built a network to walk it on, walked it, and
   **reports what it found rather than what it hoped** — which turned out to be a shortfall, a defect
   in the gate, a defect in the console, and a defect in the correlator.

---

## 1. In scope

| | |
|---|---|
| Three kinds | `tree`, `forest`, `gradient_boosting` — in process, pure Python, **five runtime dependencies still** |
| The dispatch | each kind is one branch in `model_version.scorer_for` and **nothing at the call site** |
| Attribution | exact marginal (interventional) Shapley over the three features, 2³ = 8 coalitions, against a registered background set |
| The contract | `LinkScore` gains optional `basis` and `base_value` fields — a **minor** bump by DECISIONS #49 |
| The admission band | discrimination, not wall-clock: a model that cannot reach its own threshold is refused |
| The roadmap correction | ADR #183 supersedes the "a gradient-boosted model can only enter through the ONNX door" sentence. **A new ADR, never an edit** |
| The simulated network | six adversarial shapes in registered proportions from a registered seed, outside `eval/corpus/` |
| The chain, walked | real appliance, real UDP, the console's own route, the CLI, the gate |
| **F58** | issued: a storm defeats `MIN_EDGE_N` for every NE in the window. Measured, unfixed, and the reason it is unfixed is recorded |
| **F59** | **fixed**: the promotion gate measured the shadow scorer and activated the candidate. ADR #195 |
| **F60** | **fixed**: the console reported the coded additive defaults as the active configuration whenever a model version was running. ADR #196 |

## 2. Out of scope, and why

| Not done | Why |
|---|---|
| A cartridge, a subprocess, an IPC boundary, `onnxruntime` | Part 0. v0.15.0's question, and `CARTRIDGE-0.15-DRAFT.md` is where this release's opinion about it goes. A process boundary is a security boundary and it is not opened as a side effect of adding a tree. |
| Any change to `correlate.py`, `engine.py`, `receiver.py`, `capture.py`, `learn.py` | The prime directive, verified by hash at every gate. **F58 is a finding about `learn.py` and it is still not fixed**, which is the sharpest test that directive has been given. |
| Fixing F58 | Three reasons in `../gates/v0.14.0-phase-7.md` §3.4, and the third decides it: changing the correlator after seeing the verdict it produced is adaptive selection with the data-generating process as the knob, one layer below where §5.4 forbids it. |
| Changing the generator's shape, seed, proportions or labelling rule after the shortfall | `PREREGISTRATION-0.14.0.md` §5.4, literally. The obvious rescue — separating incidents in time rather than by NE — was available, would probably have cleared both floors, and **was not taken**. It goes to the security review as an opinion for v0.15.0. |
| A migration | None was needed. A new kind is a `model_version` row, and that table has held an arbitrary `params_document` since `0013`. **Zero migrations, as predicted in Gate 0.** |
| Accuracy, fit quality or agreement with the champion as a validator rule | Plan §2.4. Degeneracy is about whether a model can decide at all; whether it decides *well* is the judge's question and no validator may pre-empt it. |
| Any claim about which model is better on a real network | Plan §6, three ways. A corpus this release generated is a demonstration of the machinery. **It is never evidence about a network.** |

## 3. What the release does not deliver, said in the scope rather than in a footnote

**The champion never changed.** §5.3 steps 7 and 8 — an admin approving a promotion, and verifying by
observation that subsequent situations carry the new model's provenance — were not reached, because
the floors were not met:

| | reached | floor | short by |
|---|---:|---:|---:|
| `asserting_bags` | 10 | 50 | **40** |
| `asserting_incidents` | 10 | 30 | **20** |

`PREREGISTRATION-0.14.0.md` §5.3 registered that outcome in advance as one of two successful
stopping conditions, and §8.3 named it before any corpus existed. It is a **gate outcome, not a
failure** — but it is also the thing the release set out to do, so it is stated here and led with in
the build report rather than reported at the end of a table.

What *was* reached, over real surfaces: the appliance booted on an empty database, 855 datagrams
arrived over a UDP socket, situations formed, three principals labelled them through
`POST /api/situations/{sid}/feedback`, three artefacts were registered through the CLI, and
`POST /api/promotion` derived every input server-side and **refused all three, with four named
triggers each and the seal unspent.** The refusal is the machinery working. The missing half is the
observation that a champion change propagates, and this release has no observation either way.

## 4. The three defects the walk found, and none would have come from reading

* **F59 — the gate measured the wrong model.** `_derived_inputs` computed the four named quantities
  against `engine.shadow.scorer` while `propose_promotion` activated the `model_version_id` the
  request named, and nothing bound them. Three releases of tests missed it because **no test had
  ever proposed a candidate that differed from the shadow scorer.** Fixed here (ADR #195); the arm
  measured is now built from the candidate row by `model_version.scorer_for`, the same dispatch the
  activation path uses.
* **F60 — the console reported the wrong parameters.** With a model version active there is no
  scorer configuration row, so `GET /api/scorer` fell back to the **coded defaults** and the screen
  rendered them under the heading *"Active configuration"*. An operator with a promoted champion
  would have read five weights that decided nothing. It predates the tree kinds — a promoted
  `logistic` produced it too — and survived two releases, one of them a console rewrite, because
  nothing had ever been promoted. Fixed here (ADR #196).
* **F58 — a storm defeats the only NE-affinity guard.** 56 dying-gasp alarms on one OLT deposit
  `56 × STORM_DAMPING = 5.6` units of pair mass on **every** other NE in the window, above the 5.0
  `MIN_EDGE_N` requires; past that gate the NPMI is the evidence discount `m/(m+1)` alone, because
  during a storm everything co-occurs with everything. Measured, not inferred: the observed entity
  terms are exactly 5/6, 6/7, 7/8, 8/9, and the largest is 34/35 beside a recorded pair mass of
  34.000. Issued unfixed.

## 5. What did not change

Verified by hash at every gate, not asserted:

```
correlate.py  engine.py  receiver.py  capture.py  learn.py     byte-identical to v0.13.0
python eval/harness.py | sha256sum   c2e8a0ce…8b9b6f26         unchanged since v0.7.0
runtime dependencies                 5                          unchanged since v0.2.0
migrations                           0001-0013                  none added
```

## 6. Anti-overengineering compliance

* **No plugin surface.** A kind is a branch in one `if`. `scorer_for`'s docstring says it is
  *"deliberately closer to a `match` than to a plugin surface"*, and three new kinds did not change
  that.
* **No new abstraction over the three kinds.** `tree.py`, `forest.py` and `boosting.py` each own
  their own `KEYS`, `validate_payload`, `scorer_from_payload`, `fit_document` and degeneracy rules.
  A shared base class would have made the three read as variations on a theme rather than as three
  models with three sets of rules, and the rules are the point.
* **One scorer class for all three.** `attribution.AttributedScorer` — because from the correlator's
  side a tree, a forest and a boosted model are one thing: something that turns three features into
  a score and three contributions.
* **`MAX_CELLS_PER_TREE` refuses; it never approximates.** A model too large to explain exactly does
  not ship. Exactness is the contract and a sampled Shapley value wearing the same name would be
  worse than a refusal.
* **Split, never exempt.** Four modules crossed the 400-line guard during the build and all four
  were split. `DEBT_ALLOWLIST` is empty and `COHESION_EXEMPT` is `engine.py` alone.
