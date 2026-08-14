# Pre-registered analysis plan — v0.11.0 (champion / challenger)

<!-- release-claim: v0.11.0 = champion-challenger -->

**STATUS: RATIFIED — 2026-08-14.** From ratification this document is immutable: its SHA-256 is
recorded in `docs/gates/v0.11.0-phase-0.md` and asserted by `tests/test_preregistration.py`.

Its parent is [`../architecture/CHAMPION-CHALLENGER-0.11-DRAFT.md`](../architecture/CHAMPION-CHALLENGER-0.11-DRAFT.md);
the evidentiary discipline it inherits is
[`PREREGISTRATION-0.10.0.md`](PREREGISTRATION-0.10.0.md), which is **not edited by this release**.

## 0. What was known when this was written

**Known — the corpus, unchanged since v0.10.0 measured it:** 41 labelled bags, 37 merge-aware
incidents, **zero asserted negative pairs**, `asserting_bags` **0** against a floor of 50, ten bags
excluded for `coverage IN ('none','empty')` before anything begins. Every floor of
`PREREGISTRATION-0.10.0.md` §2.2 is unmet by a wide margin.

**Known — the seal holds twelve incidents**, a third of 37, giving a bootstrap interval near 0.50
and a detection threshold near 52 p.p. `SECURITY-REVIEW-0.10.0.md` §3.3 states plainly that as a
decider it cannot resolve anything.

**Known — the corrected detection threshold** (v0.10.1, ADR #154): each arm carries its own
variance, giving 0.238 / 0.149 / 0.099 at n = 37 / 120 / 300, reproducing the registered
0.25 / 0.16 / 0.10. The v0.10.0 conclusion that the table did not reproduce ran backwards.

**Known — the architecture facts** of this build prompt's Part IV: a fitted challenger's parameters
are rejected by `validate_params`; `scorer_lifecycle` has no dispatch; `scorer_config` receives
manual retunes with no judgement.

**NOT known.** No promotion has been proposed, applied or refused under this plan. No model has been
evaluated for promotion. **Every decision below is made from the corpus census and the architecture,
never from a promotion outcome**, because none exists.

**The honest reading.** Every threshold here was chosen knowing the gate will refuse. That is why §6
is written as it is, and why the refusal paths are specified before the applied one.

## 1. Invariants inherited without amendment

> **No metric that decides promotion may be computed against `incumbent_linked`** — including as a
> feature, including as a fallback where a human verdict is missing. **v0.11.0 is the release most
> tempted**, because a promotion gate wants data. It is refused, and the refusal is registered here
> rather than reconsidered after a gate came out empty.

> **Pairwise accuracy is not reported as a quality metric under any name.**

> **Four named quantities, never composed:** `over_merge_rate`, `under_merge_rate`,
> `split_bag_intact_rate`, `asserted_negative_respected_rate`.

> **`resolved = the more demanding of (project floor, deployment policy)`.** No deployment may
> soften a floor, disable the power condition, or make promotion automatic.

> **A quantity that describes the evidence is derived by the server.**

## 2. What a promotion's evidence must contain

Registered in advance so it cannot be trimmed to what a build found convenient. A `promotion` row is
**invalid without every one** of:

1. the `model_version_id`, and through it the `params_hash` and the `challenger_run_id`;
2. the verdict, **as re-derived by the server at decision time**;
3. every trigger that fired, enumerated, not summarised;
4. the four named quantities with their clustered intervals, for **both** challenger and champion,
   computed by the same code path;
5. the evaluation's **fold assignment reference**, so the numbers point at stored rows;
6. the SHA-256 of the ratified plan in force;
7. the seal's **query count** at decision time;
8. the approving admin and the decision timestamp;
9. `outcome`, and for a refusal the reason.

**A refused promotion writes a row carrying 1–9 as far as they exist**, and states which were
unavailable and why. A table of successes answers *"what is deployed"* and not *"what has this
appliance been asked to deploy"*, and the second is the audit question.

## 3. The seal policy — **conditional**, registered in advance

> **Promotion requires the sealed holdout to have been spent — but the seal is read only after the
> floors and the power condition have both passed.**
>
> **Evaluation order, registered:** floors first, power condition second, seal last. The seal is
> spent when and only when the first two pass.

**Why not "spend it".** Twelve incidents give an interval near 0.50 and a threshold near 52 p.p.
Spending it to satisfy a formality buys a number that cannot resolve anything and destroys the
one-shot property permanently.

**Why not "do not require it".** `Trigger.HOLDOUT_UNSPENT` would become decorative, and a later
release would face the question with the habit of ignoring it already formed.

**Why conditional is right.** It preserves the asymmetry that justified the seal — *reserving later
is impossible; spending later is always possible* — while ensuring the seal is never opened on a
corpus that had already failed for other reasons. **On the current corpus the floors fail, so
v0.11.0's query count is 0**, and that is a prediction this plan makes in advance rather than an
outcome it reports.

**Not registered, and deliberately:** re-cutting the seal by a **rule** rather than a **count**,
which `SECURITY-REVIEW-0.10.0.md` §3.3 offers in advance and argues well. This release changes
`scorer_active` and adds dispatch; changing the seal in the same release would remove the only
stable thing to measure against. It stays an option for a later release, **which must register it
before seeing a result.**

**Registered as in scope:** `holdout_access` becomes **hash-chained**, per §3.4 of the same review.
If a promotion cites a query count as evidence, that record must be tamper-evident and not merely
append-only — triggers stop the application, not somebody with the file.

## 4. The two refusals

They are opposite claims and must never be producible by the same condition.

| verdict | the claim | the message must carry |
|---|---|---|
| `INSUFFICIENT_EVIDENCE` | **the corpus cannot decide** | every trigger that fired; the detection threshold at the available `n`; what would have to change, quantified where the arithmetic allows and `undefined` where it does not |
| `NOT_BETTER` | **the corpus decided, against the challenger** | which of the four quantities produced the verdict, its clustered interval, and the champion's number beside it |

**The projection will be `undefined`**, because this repository has measured that it has no
labelling-rate data at all. Printing the harness's rate would be manufacturing.

## 5. The logistic kind's degeneracy rules

The additive validator has five rules won by experience. The logistic kind arrives with none, and a
payload validator without degeneracy rules is a type check wearing a safety check's name. Registered
in advance, so they are not chosen to fit whatever a fit produced:

1. **Finiteness** — intercept and every weight finite. Non-negotiable.
2. **Feature completeness** — exactly the weights `FEATURE_NAMES` declares, no more and no fewer; an
   unexpected key is a malformed document, not a warning.
3. **Non-degenerate discrimination** — an all-zero weight vector gives every pair the same logit and
   the scorer stops discriminating silently. Refused: it is the logistic analogue of
   `MIN_WEIGHT_SUM`, and *silently stops grouping* is the failure `MIN_WEIGHT_SUM` exists to catch.
4. **Threshold reachability** — there must exist an attainable feature vector on each side of the
   threshold, over the features' declared ranges. A threshold no pair can cross links nothing ever;
   one every pair crosses links everything. This is the logistic analogue of `MIN_THRESHOLD` and the
   threshold margin, and it is the rule a build is most likely to omit.
5. **Magnitude sanity** — a bound on `|coefficient|`, argued in an ADR rather than asserted, with
   the reasoning that a coefficient large enough to saturate the link function turns a probabilistic
   scorer into a step function and makes the per-term explanation useless in practice while still
   summing correctly.

**Rules 1–4 are floors and a deployment may not soften them.** Rule 5's bound is a project floor a
deployment may raise.

## 6. What will be concluded under each outcome

**6.1 — Floors unmet.** *The expected branch, and §0 says it was expected.* Verdict
`INSUFFICIENT_EVIDENCE`, seal unread, query count 0, no promotion. The promotion machinery is
demonstrated on a purpose-built fixture. **This is a successful release.**

**6.2 — Floors met, power condition fails.** `INSUFFICIENT_EVIDENCE`; the seal stays unread; the
report prints the detection threshold beside the floor evaluation.

**6.3 — Floors and power met, seal spent, interval excludes zero on both headline rates.**
`BETTER`. Promotion becomes **available for an admin to approve**, and is not applied by anything
else. Query count 1 thereafter, printed beside every holdout number the project ever publishes.

**6.4 — Floors and power met, seal spent, interval contains zero or favours the champion.**
`NOT_BETTER`; the deciding quantity named.

**6.5 — Better on one headline rate, worse on the other.** `NOT_BETTER`. No composite is computed.

**6.6 — `split_bag_intact_rate` high while both headline rates look good.** The model buries splits.
`NOT_BETTER` regardless of the other numbers.

**6.7 — `asserted_negative_respected_rate` near 1.0 while `under_merge_rate` is also high.** The
challenger separates everything and respects every assertion for free. `NOT_BETTER`.

**6.8 — An admin approves and the pointer move fails.** The `promotion` row records the attempt and
its failure; the active pointer is unchanged; the operator is warned. A promotion that half-applied
would be the worst state this system could reach.

**6.9 — A promoted model later loads malformed.** The load path falls back to the built-in default,
warns, and audits. **It does not fall back to the previous model**: an appliance silently running
something other than what the pointer names is worse than one running the documented default.

## 7. Stopping rules

* **The seal is not read unless the floors and the power condition both pass.**
* **No metric is added after a result is seen.** A quantity not named in §1 may be reported under
  *"additional observations"* and may never support a conclusion in §6.
* **No floor is changed**, and the power condition is not converted into a floor.
* **`incumbent_linked` is not relaxed.**
* **No promotion is applied without an admin**, and no configuration makes one automatic.
* **This file is not edited** once ratified.

## 8. Where a disagreement with this plan goes

Into `../security/SECURITY-REVIEW-0.11.0.md` §critical-analysis, as an opinion **for v0.12.0**, with
the data that produced it — never as a change here.
