# How correlation works

The product's central claim is that it starts knowing nothing about your network and becomes useful
from the trap stream alone. This page is how, and how to read the evidence when you disagree with
it.

## What decides

Five trained models — GAM, boosted trees, random forest, decision tree and logistic regression —
are shipped as data and compete from the first trap (ADRs #424–#426). A judge ranks them every few
minutes on the same evidence and the best decides; an admin can **pin** one in
**Settings → Correlation** (a reason is required and audited). The **additive formula** below is
the fail-safe: it decides only when no model can be loaded, or when an admin chooses it. A **site
model** — a shipped model adapted to this appliance's labels by the in-product search — can take
over when the judge says it is better here.

A trained model sees **fifteen relations** between two alarms, not three — time apart, same element,
same trap type, shared OID arcs, the learned class and element affinities, how often these two
elements (and these two faults) have failed together **on separate occasions** before, severity,
burst size, chatter, how many elements each has co-failed with, and whether one trap's varbinds name
the other's address. Each was measured by ablation; three that did not pay were dropped (#409).
None is an identifier, and none is derived from what the running decider decided.

It also sees **more candidates**: besides the 120-second window, bounded rings over the last hour
recall live alarms on the same element, under the same OID parent and on elements this one has
co-failed with before (#418). That is what lets an optical degradation whose stages are ten minutes
apart, or a BGP session that times out three minutes after its link, be one situation.

And it **groups differently**: an alarm joins the situation whose members it agrees with *on
average* (log-odds evidence above a bias), and two situations merge only when the evidence between
them, accumulated over many pairs, says so — correlation clustering rather than connected
components, so one weak bridge no longer merges two concurrent incidents (#418). The biases are
chosen on validation as the fewest **operator repair gestures** among the settings that pass the
quality bar itself on every validation split and family (#409, #420, #421).

Every link a model makes stores its **whole explanation** — each feature's contribution to the
log-odds, and the intercept — and they sum to the decision exactly (#407). The model file is
**data**: a JSON table of numbers, validated field by field before it is used, never code (#419).

An all-cleared situation stays live for **five minutes** so a bounce rejoins it instead of opening a
new one (#410) — for an hour while the fault keeps bouncing (#433). When an alarm repeats, clears or
comes back is the same for every decider: [`operate.md`](operate.md#4-how-alarms-repeat-clear-and-come-back).

The rest of this page describes the additive formula, which is what a model's explanation is
compared against and what runs when you choose it.

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

**One condition withholds the entity term** (v0.18.0). When the two alarms are on **different
network elements** *and* their trap OIDs sit in **different enterprise subtrees** — the first seven
dot-components, `1.3.6.1.4.1.<enterprise>` — the learned cross-element affinity contributes 0 and
the link must be carried by time and class affinity alone.

The reason is [F76](findings.md#f76--a-corpus-scenario-fails-its-own-stated-requirement-completely-and-the-aggregate-hides-it):
`E` means *"these two elements go together"*, and six ordinary alarms are enough to establish it
(F61). Two vendors' unrelated alarms inside one window are co-occurrence without relatedness, and
that combination merged two entirely separate incidents in the shipped corpus for three releases.
**No MIB is consulted** — the enterprise arc is arithmetic on the identifier the trap carried, which
is the same opaque token the appliance already keys on. **Class affinity is not withheld**, so two
trap types that genuinely do recur together across vendors keep a route to linking.

**A second condition withholds the class term** (v0.19.0). When the two alarms are on
**different network elements** *and* the learned entity affinity between them is **exactly zero** —
no relationship established at all, the pair never having cleared `MIN_EDGE_N` — the class term
contributes 0 and only the temporal term remains. Since that caps at `w_t` (0.30), such a pair
cannot link.

The reason is [F138](findings.md#f138--seventy-independent-failures-became-one-situation-and-class-affinity-did-it):
`A` means *"these two trap types go together"* and says **nothing about which device**. In an
estate where every element raises the same two classes, `A` climbs until any two of those alarms
anywhere clear the threshold on time alone — seventy independent card failures measured as **one**
situation of 140 alarms, 492 of its 650 links carried by class affinity. The cold-start rule this
page states below — *two alarms group only when they are on the same network element and within
about 21 seconds* — was true in the first hour and stopped being true the moment `A` learned
anything. This makes it true at every hour. **The gate opens the moment the two elements have any
learned relationship at all**, so genuine cross-element correlation is untouched.

On screen both terms show their gated values, so the three printed numbers still sum to the score
exactly. How many pairs were refused is in the correlation-health panel on **Situations**, under
*"how it decided"*.

Under the formula, a **situation** is a connected component of the resulting link graph — exactly
as before v0.26.0. Within one, learned temporal precedence flags the probable root cause.

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
* **A score just over 0.5** is a marginal decision. The **Settings → Correlation** preview will show
  you how many of your recent situations sit there.

Every situation also records **which scorer configuration formed it** (`scorer_config_id`), so a
grouping stays explainable after the parameters change.

## Retuning the formula

The three-term score is the *default implementation of an interface*, not a hard-coded expression.
An admin — and only an admin, there is no editor delegation — can retune `w_t`, `w_a`, `w_e`, `τ`
and the threshold from **Settings → Correlation** (the *Link scorer* screen until v0.26.0; its
address still opens that tab). The numbers group alarms only while the additive formula is the
chosen decider.

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

Six scorer kinds exist. Five of them are trained; all six run **in this process, in pure Python,
with no new dependency**:

| Kind | What it is |
|---|---|
| `gam` | A boosted generalised additive model over the fifteen relations. One of the five shipped league members |
| `additive` | The five-number formula above, tuned by hand. The fail-safe, or an admin's choice |
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

Registering a model is not promoting one, and **no request can assert a model**.

Since v0.19.0 an admin can register **a fit this appliance made itself**, from the Overview or with
`POST /api/models/register`. That body names a `challenger_run_id` and nothing else — no
coefficients, no kind, no contract version — so the parameters come out of the appliance's own row
and a request cannot introduce a model it did not fit. A model trained **somewhere else** still
arrives only by the CLI, which is not reachable from the network:

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

* **`INSUFFICIENT_EVIDENCE` is an answer, not an error.** *"The challenger is not better"* and
  *"this corpus cannot tell"* are opposite claims and the report never conflates them. Since
  v0.26.0 it is final **for the labels it was computed on**, not for the release: the next search,
  on more labels, asks again (#417).

## Adapting to this site

**Settings → Search** starts a hyperparameter search over this appliance's labelled situations:
random search with successive halving, seeded, bounded by a budget you set, stoppable, in its own
process so ingestion never waits on it (#413). It starts from the shipped GAM. Its result is a *site model* that
decides nothing on its own. It is judged against the shipped model on **your newest labels**, paired per incident, and
on a generated benchmark packaged with the shipped model so that it cannot forget what it knew
(#411). Only a `BETTER` verdict lets an admin switch to it, and the server re-derives the verdict
when asked. The floors — 20 labelled incidents, 6 of them in the newest part, 4 splits asserting a
negative, labels from 3 days — and their reasoning are on the Judge screen and in #411.
* Beside every floor the report prints the **minimum detectable difference** at your corpus's `n`,
  because a corpus can meet every floor and still be unable to resolve anything.
