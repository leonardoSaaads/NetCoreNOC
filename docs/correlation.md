# How correlation works

The product's central claim is that it starts knowing nothing about your network and becomes useful
from the trap stream alone. This page is how, and how to read the evidence when you disagree with
it.

## Every trap becomes three numbers

A trap is reduced to a **device** (the source IP), a **class** (the trap OID, as an opaque token —
no MIB is consulted, ever) and an **instance**. Alarms deduplicate on that fingerprint, so a
flapping port is one alarm with a count, not four hundred.

Two alarms inside a 120-second sliding window are **linked** when

```
s = 0.3·e^(−Δt/30s)  +  0.35·A[class_i, class_j]  +  0.35·E[ne_i, ne_j]   >  0.5
```

| Term | Name | What it measures |
|---|---|---|
| `0.3·e^(−Δt/30s)` | **temporal** | How close in time. Decays with a 30 s constant, so 21 s halves it |
| `0.35·A[i,j]` | **class affinity** | How often these two *trap types* have been seen together |
| `0.35·E[i,j]` | **entity affinity** | How often these two *network elements* have been seen together |

A **situation** is a connected component of the resulting link graph. Within one, learned temporal
precedence flags the probable root cause.

## What is learned, and how

`A` and `E` are matrices of normalised pointwise mutual information over co-occurrence, updated
incrementally with exponential forgetting. Nothing sweeps the matrix; only the touched cell is
updated, so learning is O(1) per observation and stays off the critical path.

Three details decide how they behave in practice:

* **An entity pair needs `n ≥ 5` observations before its edge is trusted** (`MIN_EDGE_N`). Below
  that the entity affinity is 0, not a small number — an edge with two observations is noise, and
  reporting it as weak evidence is worse than reporting it as none. **See
  [F61](findings.md#f61--f58s-scope-is-stated-backwards-and-the-case-that-matters-is-the-ordinary-one):
  six alarms alternating between two elements clear this gate, which is fewer than it looks.**
* **Storms are damped 10×** (`STORM_DAMPING`, above 50 alarms in the window). During a mass event
  everything co-occurs with everything, so undamped learning would conclude that the whole estate is
  one entity.
* **Entity affinity is kept at network-element level**, not device level: same entity ⇒ 1.0, same
  network element but a different entity ⇒ 0.8, otherwise the learned NE×NE affinity. Before any
  entity is subdivided this is numerically identical to plain device affinity.

Raise/clear pairs are learned from strict alternation — `linkDown`/`linkUp` is pre-seeded, the rest
is observed — including at the varbind level for single-OID state traps. A fully cleared situation
closes and reinforces the matrices.

## Cold start, honestly

With nothing learned, `A` is zero everywhere and `E` is 1.0 only within a network element, so the
temporal term alone has to clear the threshold: **two alarms group only when they are on the same
network element and within about 21 seconds** (Δt < 30·ln 2). That is why running it alongside your
existing NMS costs nothing — everything beyond that rule is learned from your network rather than
assumed about it.

## What is alarmed, not just what alarmed

Since v0.3.0 the appliance also infers *which thing* a trap is about — the ONU, the port, the card —
by profiling varbinds on three explainable terms:

* **repeat rate** — a varbind whose value repeats across activations is naming something stable;
* **cross-class overlap** — a value that appears under several trap types is an identity, not a
  payload;
* **non-monotonicity** — a counter is not an identifier.

A varbind is promoted to name the entity only when the evidence clears conservative floors **and**
beats the runner-up. Containment (card → port, port → ONU) is recovered by a functional-dependency
test. Promotion is forward-only, every decision is inspectable on the **Entities** screen with its
`key_source`, `confidence` and score breakdown, and an admin can reset a poisoned one.

## Reading a breakdown

Every link stores its three contributions, so a grouping is auditable months later:

```json
{"alarm_a": 1, "alarm_b": 3, "score": 0.636,
 "terms": [{"name": "temporal",        "contribution": 0.286},
           {"name": "class_affinity",  "contribution": 0.0},
           {"name": "entity_affinity", "contribution": 0.35}]}
```

**The contributions sum to the score, exactly.** That is a contract, not a convenience — see
[the model kinds](#the-model-kinds) below for what it costs to keep it.

How to read one:

* **A high temporal term and nothing else** is a cold-start grouping, or two genuinely unrelated
  alarms that happened to arrive together. If it is wrong, **Split** it.
* **A high class-affinity term** means these two trap types have been seen together before. If that
  is a coincidence in your network rather than a real relationship, splitting a few of them teaches
  it.
* **An entity-affinity term of exactly 0.35** means the two alarms are on the same network element —
  structural, not learned. A term of exactly **0.0** means the pair has not cleared `MIN_EDGE_N`.
* **A score just over 0.5** is a marginal decision. The **Link scorer** screen's preview will show
  you how many of your recent situations sit there.

Every situation also records **which scorer configuration formed it** (`scorer_config_id`), so a
grouping stays explainable after the parameters change.

## Retuning the formula

The three-term score is the *default implementation of an interface*, not a hard-coded expression.
An admin — and only an admin, there is no editor delegation — can retune `w_t`, `w_a`, `w_e`, `τ`
and the threshold from the **Link scorer** screen.

Four things make that safe to offer:

* **Preview before you apply.** A read-only what-if re-partitions your own recent alarms under the
  candidate parameters and shows what would merge and what would split. It is directional, not
  exhaustive — a bounded recent window, learned matrices held fixed — and it says so.
* **Values that would collapse or shatter every incident are refused**, not warned about.
* **Every change is audited, the configuration history is immutable and append-only, and rollback is
  one click** — it moves a pointer, it never edits history.
* **If a scorer ever fails, the engine falls back to the built-in defaults and says so**, rather
  than stalling or grouping wrongly in silence.

At the default parameters the current version produces byte-identical grouping to v0.5.0. That
parity is a release gate, not a claim.

## The model kinds

Five scorer kinds exist. Four of them are trained; all five run **in this process, in pure Python,
with no new dependency**:

| Kind | What it is |
|---|---|
| `additive` | The five-number formula above, tuned by hand. The default and the champion |
| `logistic` | The same three features with coefficients fitted from labelled evidence |
| `tree` | A CART over the three features |
| `forest` | A bagged ensemble of them |
| `gradient_boosting` | A boosted one |

**Explainability survives the change of family.** A tree predicts a leaf value, not a weighted sum,
so the contributions are computed as **exact marginal (interventional) Shapley values** — all 2³ = 8
coalitions enumerated against a background set fixed at registration. No approximation, no library.
`sum(contributions) + base_value == score`, exactly, and **a model too large to tabulate is refused
rather than approximated.** A kind that cannot explain its own decision is not a scorer this project
runs.

There is no plugin surface, no registry and no dynamic import: each kind is one branch in
`model_version.scorer_for`, so *"which models can this appliance run"* stays a question the source
answers.

## Nothing is promoted without evidence

Registering a model is not promoting one, and there is **no HTTP route that creates a model
version** — the thing that could put a new model in front of your traffic is not reachable from the
network.

```sh
python -m netcorenoc promotion register --kind tree --params "$(cat model.json)"
python -m netcorenoc promotion list       # every decision, refusals included
```

`POST /api/promotion` names a candidate and nothing else: the server re-derives the floors, the
power condition, the sealed holdout, the metrics and the verdict, and the request has no field that
could assert any of them. An admin approves, and the swap is one more immutable row.

**On a corpus below the pre-registered floors it refuses, names every trigger that fired, and says
what would have to change.** That is the expected outcome and it is not a fault — it is this
project's own outcome today. The floors it refuses against were registered in
[`analysis/`](analysis/) before any of the data existed.

Two limits worth knowing when you read a verdict:

* **`INSUFFICIENT_EVIDENCE` is a terminal answer, not an error.** *"The challenger is not better"*
  and *"this corpus cannot tell"* are opposite claims and the report never conflates them.
* Beside every floor the report prints the **minimum detectable difference** at your corpus's `n`,
  because a corpus can meet every floor and still be unable to resolve anything.
