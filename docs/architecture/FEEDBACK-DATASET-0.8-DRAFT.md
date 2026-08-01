# The operator-feedback dataset — v0.8.0 draft (specification only, not implemented in v0.7.5)

<!-- release-claim: v0.8.0 = operator-feedback-dataset -->

**Implement none of this in v0.7.5.** Every element below is tagged **`v0.8.0: planned`**. This
document *refines* a specification the project already had in outline
([`../ROADMAP.md`](../ROADMAP.md), v0.7.1's deferred list); it does not re-derive one. Every
constraint below is traced to the code that causes it.

> **Refined 2026-07-31 (v0.7.5).** Every constraint in §3 was re-verified against the **v0.7.5**
> tree, file and line, and the two references that had moved are corrected in place — the shaping
> package split (§5) and the `alarm` uniqueness mechanism (§3.4). **No constraint changed in
> substance**, which is the answer to the question §8 of the v0.7.5 build prompt asks: a changed
> constraint would have been a finding, not an edit. What v0.7.5 adds to this document is §0.

> **Corrected 2026-08-01 (v0.8.0) — the build that implements this document.** Every constraint in
> §3 was re-verified a third time, against the **v0.8.0** tree, and **all four are unmoved at every
> cited line** ([`../gates/v0.8.0-phase-0.md`](../gates/v0.8.0-phase-0.md) §5). What changed is not
> the constraints but this document's **reasoning about them**, in eight places, each traced to a
> Phase 0 reproduction or to a directive of the v0.8.0 build prompt:
>
> | # | What | Where |
> |---|---|---|
> | 1 | §3.1's "majority class" sentence — **it authorises the imitation trap**, and it is also arithmetically wrong on the corpus | §3.1, and the new §3.1a |
> | 2 | The merge finding — the *stronger* reason the membership record exists | §2.2a, answering §6a(3) |
> | 3 | The formalism named: all-positive bag vs at-least-one-negative bag | §3.3a |
> | 4 | The leakage vectors, each with the column that measures it | new §3.5 |
> | 5 | The security posture — scope bypass, admin-only, no existence oracle | new §5a |
> | 6 | "Keys are not features", and store-what-cannot-be-recomputed | new §4a |
> | 7 | The acquisition channel | new §5b |
> | 8 | Confidence/uncertainty as v0.9.0+, and **calibration as a metric v0.9.0 owes** | new §6b |
>
> §6a's four open questions are **answered** below, each by a measurement rather than a preference.
> Nothing in the original text is deleted: where a sentence is wrong it stays, struck through in
> prose, with the correction beneath it — this document is the record of what the project believed
> as well as the specification of what it built.

**v0.8.0 is the scoreboard**: capture the operator feedback as a durable dataset and measure its
bias. **It trains nothing.** The chain it opens, and why the order cannot be permuted, is
[`ROADMAP-0.8-TO-0.13.md`](ROADMAP-0.8-TO-0.13.md) (DECISIONS #93).

Its prerequisite is [`FEEDBACK-PATH-0.7.5-DRAFT.md`](FEEDBACK-PATH-0.7.5-DRAFT.md): the acquisition
path is fixed first, because a click the operator did not mean produces a label nothing downstream
can detect as wrong.

---

## 0. What v0.7.5 hands this release, and what it does not (`v0.8.0: planned`)

**v0.7.5 hands v0.8.0 a click the operator meant.** The acquisition path is repaired: an expanded
situation card is no longer destroyed and rebuilt underneath the operator every two seconds, the
detail container is never displayed empty, and a card held open carries a marker saying it is
stale. The click therefore lands on the card the operator was looking at, and the operator knows
the view is frozen while they decide.

**That is a precondition for this dataset, and it is not a substitute for the membership
fingerprint of §2.2.** The two solve different halves and only one of them is done:

| | v0.7.5 | v0.8.0 |
|---|---|---|
| The operator's click reaches the card they were reading | **fixed** | — |
| The operator is told the card is held | **fixed** | — |
| The record says **which membership** the verdict was about | not addressed | **§2.2, the fingerprint** |

A label recorded at v0.7.5 is still a judgement about a membership nothing preserved. The operator
meant it, and no later reader can recover what *it* was. Nobody should read v0.7.5 as having solved
label provenance — it closed the path by which a label could be recorded that the operator never
formed at all, which is the strictly worse failure because it is undetectable at every layer
downstream.

One consequence for the bias report (§6) that is easy to miss: labels written **before** v0.7.5
were acquired over the defective path and a fraction of them — unknowable, because nothing recorded
it — are clicks the operator did not mean. If any pre-v0.7.5 `feedback` rows are carried into the
dataset, the report must **say so and count them separately**. They are not interchangeable with
rows acquired afterwards, and averaging the two hides the one population whose noise is known to be
non-random.

---

## 1. What exists today (`v0.8.0: planned` — the baseline this refines)

| Today | Where |
|---|---|
| `feedback` rows carry `situation_id`, `verdict`, `principal_ref`, `role`, idempotent per `(situation, verdict)` | migration `0007`, F36 |
| The verdict adjusts learned masses and is then gone | `learn.penalize()`, `Engine.apply_feedback` |
| Nothing records **what the operator was looking at** | — |

Migration `0007`'s own comment says it: *"These two columns are here because F36/F39 need them to be
correct today. They are NOT the v0.8.0 feedback dataset."* This is that dataset.

---

## 2. Traceability: capture, do not reject (`v0.8.0: planned`)

The label should be traceable to the state the operator saw. **The mechanism must not be an
optimistic-concurrency precondition**, and this section exists because "add a version and reject on
mismatch" is the obvious-looking design and it is wrong here.

### 2.1 Why rejection is the wrong primitive

**Rejection is right for *edits*. A label is an *observation*.**

An edit asserts *"change X from A to B"*, and if X is no longer A the edit is meaningless — rejecting
is the only safe answer. An observation asserts *"when I looked, I saw this"*, and that stays true
however the world moves afterwards. Rejecting an observation discards information that was correct.

Two consequences make the difference concrete here:

1. **A stale label is not invalid — it is a label about a subset.** "These four belong together"
   stays true when a fifth alarm joins the situation. What is missing is not validity; it is knowing
   *which four*. Rejection throws away a true statement because the system cannot express its scope.
2. **In a system that updates every two seconds, rejection is a livelock.** `SSE_UPDATE_S = 2.0`
   (`api/routes_events.py:27`), and an open situation in a live network changes membership
   constantly. The operator would click, be told to try again, click, be told to try again. That
   makes the acquisition path *worse* than the bug v0.7.5 fixes — it converts a rare race into a
   reliable refusal, and the operator stops giving feedback at all. A dataset that is empty because
   the UI kept saying no is not a safer dataset.

### 2.2 The primitive: a membership fingerprint captured with the label

> **A dataset column, not a precondition on the endpoint.**

The write records, alongside the verdict, a fingerprint of the membership the operator was shown:
the ordered set of member alarm ids and a digest over it, plus the situation's `updated_at` at
render time. The server does not compare it to anything and does not refuse anything. It is
**evidence**, and evidence is what makes a subset-label interpretable later: v0.9.0 can ask "was
this label about the situation as it now stands, or about four of its nine members?" and answer it.

**The API contract therefore needs no change now.** `POST /api/situations/{sid}/feedback` stays
`{verdict}`; the fingerprint is an **additive, non-breaking extension** — an optional field the UI
starts sending, absent on every row written before v0.8.0, exactly as `principal_ref` is absent
before `0007`. Absence means "unrecorded", never a guess. (Migration `0007`'s comment states that
principle for its own columns; it applies unchanged here.)

### 2.2a The stronger reason, and it is not staleness (added 2026-08-01, v0.8.0)

> **A merged situation's label loses its referent entirely.** Not "refers to a subset" — refers to
> **nothing**.

§2.2 above justifies the fingerprint by *staleness*: the membership moved under the operator, so
record what they saw. That is true, and it is the weaker half. Phase 0 reproduced the stronger one
by execution ([`../gates/v0.8.0-phase-0.md`](../gates/v0.8.0-phase-0.md) §1):

`engine._assign_situation` merges with `sid = min(sids)` (`engine.py:437`), and
`store.merge_situations` (`store/situations.py:43`) moves `situation_alarm`, re-points `link`, marks
the absorbed situation `merged` — and **does not touch `feedback`**. So after a merge:

| Query | Result |
|---|---|
| the `feedback` row | still points at the **absorbed** id |
| `situation_alarm` for that id | **empty** |
| `feedback JOIN situation_alarm` | **empty** — the natural join returns nothing |
| `link WHERE situation_id = absorbed` | **empty** — re-pointed to the survivor |
| `situation_alarm` for the **survivor** | the **union** of both bags plus the bridging alarm |

**No query recovers which alarms the verdict was about.** The union is not a recovery: nothing in it
distinguishes the two members the operator judged from the three they never saw.

**A second loss, found in the same reproduction and not previously recorded anywhere:**
`merge_situations` writes `status='merged'` but **not the destination**. The merge chain is
therefore unrecoverable too — a reader holding the labelled id cannot follow it forward to the
situation that absorbed it. This is why §5.5's *situation lineage* is a **schema requirement**, not
a nicety: without a recorded merge edge, lineage is unimplementable, and v0.10.0's split-by-incident
cannot be built at all.

**This is what answers §6a(3), with an argument rather than a preference.** A digest proves that
something changed; it does not say what it was, and after a merge there is nothing left to compare
the digest *against*. The record must therefore be:

* **server-side** — written from the server's own state, because the client's report cannot be
  trusted and, for a merged situation, there may be no client involved at all;
* **the ordered member ids**, not only a digest — because the ids are the bag, and the bag is what
  §3.3 says the dataset is *for*;
* **a child table keyed by feedback id** — because it is a list, and a situation with hundreds of
  members makes the digest cheap and the list not. Store the digest **too**, for cheap comparison.

**And the degenerate case is informative, not an error.** Phase 0 §2 recorded what happens when a
verdict is posted *to* an already-merged situation: **HTTP 200, a full `feedback` row is written,
and `learn.penalize()` receives an empty bag** — a silent no-op indistinguishable from a real label
by any column that exists today. The server-side record must write an **empty bag as empty** rather
than declining to write, which makes that population countable for the first time. It also means the
membership child table may legitimately have **zero rows** for a given feedback id, so no
"at least one member" constraint may be imposed on it.

---

## 3. Four constraints the schema must answer (`v0.8.0: planned`)

Each is confirmed from the code, with the location. **Re-verified against the v0.7.5 tree**
(2026-07-31); every line below was read, not carried forward. The verification table:

| Constraint | Cited location | v0.7.5 status |
|---|---|---|
| §3.1 no negatives | `correlate.py:298`, `:295`, `:60`, `:59` | **unmoved**, all four |
| §3.2 contributions not features | `scoring.py:257`, `:266`, `migrations/0001_init.sql:80` | **unmoved**, all three |
| §3.3 per-situation label | `learn.py:366–376` | **unmoved** (`penalize` at 366, the pair expansion at 369) |
| §3.4 capture at decision time | `learn.py:65–86`, `migrations/0003_entity.sql:94` | **unmoved**; mechanism description corrected below |
| §5 scoped reads | `shaping.project_situation_detail` | **moved** → `shaping/project.py:59` (v0.7.4 package split) |

**No constraint changed in substance.** v0.7.5 touches `api/declare.py`, `ui/app.js` and
`tests/test_documentation.py` and nothing else, so the engine, store, correlation and scoring seam
that cause these constraints are byte-identical to v0.7.4 — which `make eval` proves independently.

### 3.1 There are no negatives — the dataset is censored on both ends

`correlate.py:298` returns `CorrelationResult(links=links, considered=candidates, storm=storm)`.
**Only `links` is persisted.** `considered` — the pairs that were evaluated and *rejected* — is
discarded at the end of the call.

~~That is the majority class. Supervised training without it is impossible: a model trained only on
pairs that linked learns "everything links".~~

> ### 3.1a **Corrected 2026-08-01 (v0.8.0). The struck sentence above produces the wrong model.**
>
> It is wrong twice, and the second way was only discoverable by measurement.
>
> **Wrong conceptually — and this is the one that matters.** The evaluated-and-rejected pairs are
> not the majority class of the **human label**. They are the **machine's own decision**. The human
> majority class does not exist in quantity, and never will: an operator labels a handful of
> situations a week, not two hundred thousand pairs an hour. Read in good faith by whoever writes
> v0.9.0 — and it *will* be read in good faith, because it is a specification — that sentence
> authorises training a challenger against `incumbent_linked`, which is the imitation trap. A model
> fitted to reproduce the champion cannot exceed the champion; it can only converge on it, and it
> will look excellent while doing so.
>
> **The invariant, stated so it cannot be read any other way:**
>
> > **No metric that decides promotion may be computed against `incumbent_linked`.**
>
> Note precisely what this does and does not forbid. `incumbent_linked` is a **legitimate column**:
> as provenance, as context, as a *feature*, and as the basis of champion/challenger comparison in
> v0.11.0. Training against it is a technique with a purpose (distillation, warm-starting,
> agreement analysis). Being **judged** by it is circular, and it silently redefines "good" as
> "agrees with what we already have". The distinction is between a *feature* and a *target*, and
> the schema expresses it structurally: **there is no target column in the pair table.** The only
> label lives in `feedback`, and reaching it requires the join. That is deliberate friction —
> whoever writes the v0.9.0 training loop must go and *get* the human label, and cannot reach for
> the machine's one by accident.
>
> **Wrong arithmetically, on this project's own corpus.** Phase 0 counted at `score_link` rather
> than at `process()`, which separates three populations the single figure conflates
> ([`../gates/v0.8.0-phase-0.md`](../gates/v0.8.0-phase-0.md) §3a):
>
> | Population | Count | Share |
> |---|---|---|
> | pairs **evaluated** | 194 341 | 100 % |
> | pairs **accepted** by the scorer | 194 002 | 99.83 % |
> | links **persisted** after `MAX_LINKS_PER_ALARM` | 10 973 | 5.6 % |
> | links **lost to truncation** | 183 029 | 94.2 % |
> | pairs **evaluated and rejected** | **339** | **0.17 %** |
>
> The 17.7× amplification is **truncation of accepted links**, not rejection. The
> "majority class" is 0.17 % of the corpus.
>
> **And the balance is a property of the traffic, not of the scorer** — which is the finding that
> makes the invariant above non-negotiable rather than merely prudent:
>
> | Scenario | accept rate | storm |
> |---|---|---|
> | `background_noise` | **0 %** (276 of 276 rejected) | 0 % |
> | `dual_incident` | 55 % | 0 % |
> | `fiber_cut` | 68 % | 0 % |
> | `olt_storm`, `pon_dying_gasp`, `camera_nvr`, `decoy_varbinds` | **100 %** (nothing rejected) | 79–95 % |
>
> A storm saturates both affinity terms, so `0.3·decay + 0.35·A + 0.35·E` clears the 0.5 threshold
> on essentially every pair. The four storm scenarios are 97 % of the evaluated pairs and set the
> aggregate single-handedly. **A quantity that swings from 0 % to 100 % with the weather is not a
> training target and is certainly not an evaluation basis.**
>
> **What survives of the original sentence.** The *operational* consequence is unchanged and is the
> reason this release exists: capture must record **one row per evaluated pair**, linked and
> rejected alike, before either censoring applies. That was right. Only the justification — and the
> licence it accidentally granted — is withdrawn.

And the positive side is truncated too. `correlate.py:295`:

```python
links = sorted(links, key=lambda link: link.score, reverse=True)[:MAX_LINKS_PER_ALARM]
```

with `MAX_LINKS_PER_ALARM = 5` (`correlate.py:60`). A six-way situation loses a real link to a cap
that exists for good reasons (components need one link, audits need few) and has nothing to do with
truth.

**Consequence for v0.8.0:** capture must record **one row per evaluated pair** — linked *and*
considered-but-rejected — at the moment `process()` returns, before either censoring applies. The
truncation stays in place for what the *engine* persists to `link`; the dataset is a separate sink
that sees the uncensored result.

### 3.2 The `link` table stores contributions, not features

`scoring.py:257`:

```python
term_t = self.w_t * decay
```

`term_t`/`term_a`/`term_e` are **weight × value**. `migrations/0001_init.sql:80` stores exactly that
one number per term.

Training needs the **value** — Δt, `A`, `E` — not the contribution. Recovering it by dividing out
the weight from `scorer_config_id` **fails when a weight is zero**, and zero is a legal setting: an
admin may disable a term entirely, which is a supported configuration and the most interesting one
to have data about.

The in-memory `TermContribution` already carries both (`scoring.py:266`:
`TermContribution("temporal", self.w_t, decay, term_t)`) — the value is computed and then dropped at
the persistence boundary.

**Consequence for v0.8.0:** capture the **`LinkFeatures` values**, not the contributions. Record
`scorer_config_id` too, so the contribution stays derivable in the direction that always works.

### 3.3 The label is per situation; the model is per pair

`learn.py:366–376`: `penalize()` takes a "split" verdict on a situation and expands it to **every
distinct pair** among its members:

```python
class_pairs = {_pair(a[0], b[0]) for i, a in enumerate(sample) for b in sample[i + 1 :]}
```

That is correct as a *learning heuristic* — it degrades the masses that produced a grouping the
operator rejected. It would be **wrong as training data**. An operator splitting a nine-member
situation is usually saying "these three do not belong with those six", not "all thirty-six pairs
are negatives". Recording pairwise labels derived that way would train on **fabricated negatives**,
and the fabrication would be invisible because every row would look like a real observation.

**Consequence for v0.8.0:** record the **bag**, not the derived pairs — the member set, the verdict,
and the partition the operator could actually see. **Leave the label-derivation policy to v0.9.0**,
where a choice between candidate policies can be *evaluated* against held-out data rather than
assumed at capture time. A capture format that bakes in a derivation policy makes that comparison
impossible after the fact, which is the one mistake this release cannot undo.

### 3.3a Name the problem class (added 2026-08-01, v0.8.0)

"We left it open" reads as indecision. It is not: the problem has a name, the two verdicts sit in
**different** classes, and saying so tells v0.9.0 what it is solving instead of leaving it to
rediscover the asymmetry.

> This is **multiple-instance learning**. The label attaches to a *bag*, not to its instances.

| Verdict | Formally | What it licenses about the pairs |
|---|---|---|
| `confirm` | an **all-positive bag** — every instance is positive | every pair in the bag is a positive. The bag label transfers to all instances. |
| `split` | a bag with **at least one negative**, and it does not say which | **nothing about any individual pair.** At least one is negative; which ones is exactly what the operator did not say. |

**The asymmetry is the whole point, and it is why `learn.penalize()` is right where it is and wrong
one layer up.** `penalize()` expands a `split` to *every distinct pair among the members*
(`learn.py:369`) and halves each mass. As a **learning heuristic** that is correct and defensible: it
degrades the masses that produced a grouping the operator rejected, the effect is bounded (F36) and
reversible by later evidence, and no claim is made that any particular pair is a negative. As
**training data** the same expansion is a fabrication: an operator splitting a nine-member situation
is usually saying *"these three do not belong with those six"*, not *"all thirty-six pairs are
negatives"*, and the fabricated rows would be indistinguishable from observed ones.

**Consequences that follow from the naming, and that v0.9.0 must not re-litigate from scratch:**

* **`confirm` and `split` are not two values of one variable** for modelling purposes. A `confirm`
  is usable pairwise today; a `split` is not usable pairwise at all without a derivation policy.
  Any schema or report that averages them is averaging two different kinds of statement.
* **Confirmation strength decays with bag size.** An operator confirming a twenty-member situation
  did not verify one hundred and ninety pairs — they glanced at a card and agreed with its shape.
  One confirming a two-member situation verified the only pair there was. The bag-size distribution
  is what lets v0.9.0 **weight** rather than assume, which is why §6 reports it.
* **The candidate derivation policies v0.9.0 must choose between** (and *evaluate*, not pick):
  transfer the bag label to all pairs and accept the noise on `split`; use `confirm` bags only and
  discard `split` entirely; treat `split` as a constraint rather than a label (a must-link /
  cannot-link formulation); or solicit the partition from the operator, which is the
  partial-split affordance deferred to a later UI release and named in §7.
* **The single highest-leverage UI change for the whole ML roadmap** is the one that turns a `split`
  into an *all-negative-identified* bag — "these three, not those six". It converts the weak half of
  the dataset into the strong half. It is deferred, deliberately, and recorded on
  [`../ROADMAP.md`](../ROADMAP.md) so it is not lost.

### 3.4 Capture at the moment of decision

`A` and `E` decay lazily against the learning epoch (`learn.py:65–86`, `_decayed`), and `alarm` is
deduplicated on `(entity_id, class_id, instance)` and **mutated on re-fire** — `count` and
`last_seen` advance in place.

> **Corrected 2026-07-31 (v0.7.5).** The uniqueness is a **unique index**, not a table constraint:
> `migrations/0003_entity.sql:94` reads
> `CREATE UNIQUE INDEX ux_alarm_entity ON alarm (entity_id, class_id, instance);`. The line number
> is right and the consequence is unchanged — the row is deduplicated and mutated either way — but
> a v0.8.0 build looking for a `UNIQUE (...)` clause in the table definition would not find one.

So the state that produced a decision is **not recoverable afterwards**. An offline job over history
would reconstruct features that were never the ones the scorer saw, and would do it silently.

**Consequence for v0.8.0:** capture is a **live workstream on the engine side**, not a batch job.
This is the reason v0.8.0 is a release rather than a script.

## 3.5 The leakage vectors, each with the column that measures it (added 2026-08-01, v0.8.0)

Leakage here is not the textbook kind (a target column left in the features). It is subtler and
every vector below is **invisible unless a column is captured now**. None of them can be measured
retrospectively, which is why they belong in this release rather than in the one that trains.

### (a) The matrix epoch — a label at *T* is inside the features at *T+1*

A human verdict mutates `A` and `E` through `Engine.apply_feedback`, so a pair evaluated afterwards
carries features that **already contain information from an earlier human label**. That is not
acausal — the information genuinely existed at that instant — but it inflates apparent performance
relative to a cold start, and a v0.9.0 that does not know about it will report a number it cannot
reproduce on a fresh appliance.

> **Corrected 2026-08-01 (v0.8.0) — the mechanism, executed rather than assumed.** It is tempting
> to say "the label advances the epoch, so record the epoch". **It does not.** Phase 0 §6 ran it:
>
> ```
> epoch before penalize : (0, 0)
> epoch after  penalize : (0, 0)     -> advanced: False
> A/E pair mass before  : (1.0, 1.0)
> A/E pair mass after   : (0.5, 0.5)   -> mutated: True
> ```
>
> Since v0.7.1's F36 fix (DECISIONS #69) **neither** feedback path ticks the epoch: `confirm` calls
> `learn_epoch(advance_epoch=False)` and `split` calls `penalize()`, which calls `scale_pair` and
> leaves `at_epoch` alone. **Only a closed situation advances the epoch.**
>
> The contamination is real — the mass moved 1.0 → 0.5 — but it travels through the **mass values**,
> not through the epoch counter. The column survives, for a different and better reason: a captured
> `A` value is a *decayed reading* against `Matrix.epoch` at that instant
> (`_decayed(mass, at_epoch) = mass·(1-λ)^(epoch - at_epoch)`, `learn.py:65`, λ = 0.05), so without
> the epoch a reading of 0.4 at epoch 12 and one at epoch 900 are not comparable — and no later join
> recovers it, because the matrix is mutated in place.

**Measured by:** `a_epoch` and `e_epoch` on the pair row, joined against `feedback.created_at`.
The measurement is *pair epochs relative to label times*, not the epoch alone.

### (b) Re-fire near-duplicates — the same alarm, counted many times

`alarm` is deduplicated on `(entity_id, class_id, instance)` and mutated on re-fire (§3.4). A
flapping port produces many activations that are *nearly* the same observation, differing only in
timestamp and count. Treated as independent samples they inflate *n* without adding information,
and they cluster — so they inflate it exactly where the data is least diverse.

**Measured by:** the immutable observation row (§4), which makes "how many observations share an
`alarm` id" answerable at all. The bias report states the near-duplicate rate.

### (c) Situation lineage — two labels that look like different incidents

`sid = min(sids)` and merges mean **situation identity is not stable**. Two labels on what appear to
be different situations may be the same incident, seen before and after a merge. A train/test split
that puts one on each side leaks — and leaks **silently**, because nothing in the schema says the
two are related.

**Measured by:** the situation id at label time **plus the recorded merge edge** (§2.2a). Phase 0
found the merge destination is not recorded today, so this column is a prerequisite for v0.10.0's
split, not an enhancement to it.

### (d) Four consecutive releases tuning against one small dataset

Named here so it is not rediscovered in v0.10.0, when it will be expensive. v0.9.0 through v0.12.0
all consume **this** dataset. If each release tunes against the same held-out set, the fourth is
reporting a number that has been optimised against four times — the held-out set becomes a
training set by slow leakage through the researchers, and no code change causes it.

This is **v0.10.0's problem to solve** (it owns the split), but it is **v0.8.0's to record**,
because the material that makes a defensible split possible — time, lineage, operator, incident —
must be captured now or not at all. v0.8.0 assigns **no** split and takes **no** position on which
one is right.

**Measured by:** nothing in v0.8.0. It is a process hazard, not a data property, and the honest
record is that the schema enables a fix rather than providing one.

---

## 4. The shape: generous in columns, not in row multiplicity (`v0.8.0: planned`)

**On "record everything raw and generous" — agree with the principle, disagree with the shape.**

The principle is right: this data is captured once and cannot be recreated (§3.4), so under-capturing
is the expensive mistake. The shape usually proposed — raw varbinds stored **per pair** — is wrong,
for two reasons:

1. **~100× duplication of information that belongs to the alarm.** `MAX_CANDIDATES = 100`
   (`correlate.py:59`), so one activated alarm can be evaluated against up to a hundred candidates.
   Storing its varbinds on every pair row writes the same blob a hundred times.
2. **It is I/O amplification inside the batch lock.** Correlation runs on the ingest path.
   Multiplying write volume there is backpressure on the queue — and *"ingestion is sacred"* dying
   by an indirect route is still dying. The project has refused faster-but-heavier options on this
   path since v0.2.0; a dataset feature is not the thing to make an exception for.

> **The right shape: one row per evaluated pair carrying the `LinkFeatures` values, and one row per
> alarm *observation* carrying the raw material.**

The alarm-observation row is not redundant with the `alarm` table, and this is the subtle part:
`alarm` is deduplicated and mutated on re-fire (§3.4), so it holds the *latest* state, not the state
at decision time. The observation row is the immutable record of what arrived, and the pair rows
reference it. Columns may be generous — that is where the "record everything" instinct belongs —
because there is one row per alarm observation, not one per pair.

## 4a. The two rules that decide every column (added 2026-08-01, v0.8.0)

"Generous" is not a rule; it is a mood. Two rules make it operational, and **every** column in
`0008` is justified against them or does not exist.

### Rule 1 — store what cannot be recomputed; derive what can

> The asymmetry runs the opposite way from every previous release of this project. **An unneeded
> column costs bytes. A missing one costs the field forever.** Capture is irreversible: `A` and `E`
> decay continuously, `alarm` is mutated on re-fire, situations are merged and lose their
> membership. A moment not captured is not captured late — it is captured never.

**Must be stored** — irrecoverable after the instant:

| Column | Why it cannot be recomputed |
|---|---|
| `delta_t_s` | the window has moved on |
| the **value** of `A`, the **value** of `E` | the matrices are mutated in place and decay continuously |
| `a_epoch`, `e_epoch` | the decay clock at that instant (§3.5a) |
| `score` | a function of all of the above, none of which survives |
| `incumbent_linked` | the champion's decision at that instant, and the threshold may be retuned |
| `storm` | window occupancy at that instant |
| `scorer_config_id` | the configuration may be rolled forward or back |
| severity, entity, alarm `count` at decision time | `alarm` is mutated on re-fire |

**Store the value, never the contribution.** §3.2 is the reason and it is worth restating as a
rule: `term_a` is `w_a · A`, and dividing the weight back out **fails when the weight is zero** —
which is a legal, supported, and *especially* interesting configuration to have data about. The
`link` table's three `term_*` columns are a projection for the UI and stay that; the dataset stores
`A` and `E` themselves and keeps `scorer_config_id` so the contribution remains derivable in the
direction that always works.

**Capture generously where the instant is lost, even for fields the incumbent ignores.** Severity,
entity and the alarm's `count` are not read by `AdditiveScorer`. `LinkFeatures`' reserved slots
(`scoring.py:92-98`) are `None` because **the incumbent does not use them**, not because v0.9.0 will
not. The cost of carrying them is bytes; the cost of omitting them is that the release which wants
them cannot have them for any period before it shipped.

**Must NOT be frozen** — derivable later from stored raw material:

* *"same /24?"* — derive from the addresses on the observation rows.
* *"same OID root?"* — derive from the OIDs.
* *"same vendor?"* — derive from the enterprise OID prefix.

These are **modelling decisions**, and freezing one now is the same mistake as freezing the
label-derivation policy (§3.3): it forecloses a comparison v0.9.0 should be able to run. Store the
raw material; let the release that models decide what a feature is.

### Rule 2 — keys are not features

> `alarm_a`, `alarm_b`, `ne_id`, `class_id`, `entity_id`, `situation_id` exist **for joins**. They
> are not inputs to a model, and the schema comments in `0008` say so at the column.

Feeding an identifier to a model as a numeric feature teaches it *the training customer's estate*.
`ne_id = 47` means "the forty-seventh NE this appliance ever saw" and nothing else; it is not
ordered, not continuous, and not comparable across deployments. A model that learns "NE 47 and NE 52
co-occur" has learned one customer's topology and **generalises to nobody** — while scoring
extremely well on that customer's held-out data, which is what makes the mistake survive review.

The legitimate uses are: joining to the observation row for raw material, grouping for the
effective-sample-size calculation (§6), and stratifying a split (v0.10.0). All three are operations
*on* the dataset, not inputs to a model.

This rule is why the pair table stores identifiers **and** the raw material separately, rather than
denormalising the NE's identity into the pair row where it would look like a feature.

---

## 5. The scoping consequence (`v0.8.0: planned`)

From v0.7.1: a **scoped editor** sees a situation whose out-of-scope members are redacted to a count
and a class list (`shaping/project.py:59`, `project_situation_detail`, DECISIONS #59). Their label
is a judgement about a **partial view** — and they cannot tell you which part, because the
redaction deliberately carries no NE id, address or entity key.

> **Corrected 2026-07-31 (v0.7.5).** This section cited `shaping.project_situation_detail` when
> `shaping.py` was one module. v0.7.4 split it into the `shaping/` package (DECISIONS #95) and the
> function now lives in `shaping/project.py:59`. The import path `from netcorenoc.shaping import
> project_situation_detail` still resolves — the package re-exports it — so this is a placement
> correction, not a behaviour one.

A label made over four visible members of a nine-member situation, recorded as though it were about
all nine, is wrong in a way that **correlates with the scope policy** — which means it is
**systematic noise, not random noise**. Random noise averages out with more data. Systematic noise
does not: it teaches the model the shape of the scope policy.

`feedback` already carries `principal_ref` and `role` — **reconfirmed against the v0.7.5 tree**:
`migrations/0007_write_perimeter.sql:35–36` are
`ALTER TABLE feedback ADD COLUMN principal_ref TEXT;` and `... ADD COLUMN role TEXT;`, unchanged
since v0.7.1. **The scope fingerprint is what is still missing**: the resolved visibility scope in effect at label time — the active scope
policy's identity, and whether the situation was redacted for this principal and by how many
members. Without it the label is uninterpretable; with it, v0.9.0 can weight, stratify, or exclude,
and the bias report below can *measure* the effect instead of guessing at it.

## 5a. The security posture — the dataset is a scope bypass by construction (added 2026-08-01, v0.8.0)

This section did not exist and it should have. The dataset's security posture is not a consequence
of its contents; it is a consequence of **where it is captured**, and it is the strictest posture in
the product.

> **Capture runs engine-side, where visibility scoping does not exist — and must not.** Correlation
> learns across the whole estate; a correlator that could only see one operator's NEs would
> correlate worse. The dataset is written on that path, so it contains **every NE, every entity and
> every raw varbind in the network, ungoverned by any scope policy.**

That is not a defect to be fixed by adding scoping to the capture path. Scoping the capture would
either corrupt correlation or produce a dataset that is a different, quieter lie. The correct
response is to treat the resulting corpus as what it is: **a bypass of the visibility model, and
therefore admin-only everywhere, on every route, in every format, forever.**

The rules, stated so a later release cannot soften them by increments:

1. **No read of dataset rows below `admin`.** Not filtered, not redacted, not
   aggregated-for-an-editor. There is no scoped view of this data, because constructing one would
   require the scope machinery to reason about rows it was never designed to see.
2. **The bias report emits aggregates only** — counts, rates and distributions. It never emits a
   row, an NE name, an address, an OID or a varbind value.
3. **If an export ever exists it is `config`-class, admin-only and audited**, and is treated exactly
   as the audit export is — the closest existing analogue, and the precedent to follow rather than
   to re-derive.
4. **The client-reported fingerprint is untrusted input on a write path that has already produced
   three findings** (F34, F35, F39). It is bounded, never rejected (§2.1's argument, one level
   down), and — the one that matters —

   > **it must never become an existence oracle.** A scoped editor must not be able to learn, from
   > any difference in status code, response body or timing, whether an alarm id they named exists.

   Out-of-scope and non-existent ids are **recorded as reported** and change nothing about the
   response. This is the same discipline F34 established for situations and F37 for label targets:
   "no such thing" and "not yours" are one code path, one status, one body, one timing. Closing it
   is a security requirement, not a nicety, and it is what makes the field safe to add at all.

## 5b. The acquisition channel — one column now, or an unrecoverable ambiguity later (added 2026-08-01, v0.8.0)

**v0.8.0 writes `organic` on every row it creates, and that is the entire behaviour.** The column
exists for what happens if a later release ever *solicits* labels — active learning: asking the
operator about the cases the model is least sure of.

The reason it must be added **now**, when it does nothing:

> Solicited labels have a **deliberately different distribution**. That is the point of soliciting
> them — you ask about the uncertain, the unusual, the boundary. Mixed into one undifferentiated
> column with organically-offered ones, they do not merely add noise: they **destroy the bias
> characterisation retroactively**, including for the rows written before solicitation began.

Every measurement in §6 — coverage, bag-size distribution, operator concentration, confirm/split
ratio — describes *the population of labels the dataset contains*. Introduce a second population
with different sampling and no marker, and every one of those numbers becomes uninterpretable for
the whole history, not just for the new rows. Nothing can un-mix them afterwards, because the
information that would separate them was never written.

Adding the column later costs a migration **plus** an ambiguity no migration can resolve: every
pre-existing row would be `organic`-by-assumption rather than `organic`-by-record, and the
distinction between "we know" and "we assume" is the distinction this whole document is about.

**This is the same reasoning as the capture-provenance column (§5c), deliberately parallel:** both
mark a population whose *sampling* differs, both are written by the release that knows the answer,
and both refuse to let a later reader guess.

## 5c. What else the label row must carry (added 2026-08-01, v0.8.0)

Three more facts are one-to-one with a verdict, and all three are irrecoverable afterwards.

### Capture provenance — `legacy_capture` versus `current`

§0 establishes that labels written **before v0.7.5** were acquired over the defective path: an
expanded situation card could be destroyed and rebuilt underneath the operator, so a fraction of
those clicks — **unknowable, because nothing recorded it** — landed on a card the operator was not
reading.

The temptation is to delete them. The precise claim does not support that:

> They are **not known to be bad**. They are of **unknown quality**, which is a different and
> weaker claim, and the difference decides what to do with them.

So they are **stored, marked `legacy_capture`, excluded from training by default, and includable by
an explicit and conscious choice.** That preserves a comparison a future release may well want —
models trained on new data versus new plus old — which deleting them would have destroyed
permanently. `"these are contaminated"` is a hypothesis wearing the clothes of a fact, and a schema
is the wrong place to promote one.

Migration `0008` backfills `legacy_capture` on existing `feedback` rows. That is the **one**
permitted data write in the migration, and it writes a **marker about provenance**, never a guess
about content.

### Label latency — enough to compute open-to-verdict

A verdict given four seconds after a situation opened and one given after ten minutes of
investigation are **not the same evidence**, and nothing today distinguishes them.
`feedback.created_at` exists; `situation.created_at` exists; what is missing is the guarantee that
the second survives — `prune()` deletes closed situations on the operational retention schedule
(`store/retention.py:14`), taking the open time with it and leaving a label whose latency is
permanently uncomputable.

Latency is also **how the sink's 21-day default gets tested against reality** rather than defended
as a guess (§7 of the build prompt): if the observed distribution has mass beyond the window, the
window is too short and the next release has a number instead of a feeling.

### Situation lineage — the id at label time, and the merge chain

Covered in §2.2a and §3.5c. Restated here because it is a **label-row** fact: the situation id as it
stood when the verdict was given, which is not necessarily the id anything holds afterwards.

---

## 6. The bias report — the deliverable (`v0.8.0: planned`)

**v0.8.0 trains nothing and measures everything.** The report is not a by-product; it is the release's
output, and it must exist before anything downstream consumes the dataset. A dataset whose bias
nobody has measured is not an asset — it is a liability with a schema.

At minimum:

* **Confirms versus splits.** If the ratio is extreme, the minority class is what any model will get
  wrong, and knowing it before training is the difference between a caveat and a surprise.
* **The size distribution of labelled situations.** Operators label what they open. If two-member
  situations are labelled ten times more often than twenty-member ones, the dataset describes a
  different population from the one the scorer runs on.
* **How many labels come from how few operators.** A dataset that is three people's judgement is a
  dataset about three people. Report the concentration, not just the count.
* **How many labels were made under a restricting scope**, and the redacted-member distribution
  among them (§5) — the systematic-noise measurement.
* **Label latency**: time between situation open and verdict. A verdict given seconds after opening
  and one given after ten minutes of investigation are not the same evidence.
* **Coverage**: what fraction of situations, and of evaluated pairs, ever receive a label at all.
  The answer will be small, and v0.9.0 needs to know how small before it believes anything.

**Added 2026-08-01 (v0.8.0) — six more, each answering a question the list above cannot:**

* **Effective sample size, stated as such.** Not *"5 000 labelled pairs"*. Pairs from one alarm
  share a side; pairs from one situation are strongly correlated; situations from one operator share
  a criterion. The report states **the number of labelled bags, distinct operators and distinct
  incidents**, and says plainly that ***n* is the number of independent bags, not the number of
  pairs**. A report claiming 5 000 where the truth is 300 produces confidence intervals wrong by a
  factor of four, and nobody checks.
* **The bag-size distribution *with its interpretation*.** The existing bullet asks for the
  distribution; what makes it actionable is §3.3a's reading of it — **confirmation strength decays
  with bag size**. Twenty members confirmed is not one hundred and ninety pairs verified.
* **`legacy_capture` versus `current`, reported separately and never averaged** (§5c). The one
  population whose noise is *known* to be non-random must never be blended into the one whose noise
  is merely unmeasured.
* **The divergence rate** between server-side and client-reported membership (§2.2a). This is not an
  error rate: it is **the residual race v0.7.5 narrowed but did not eliminate, measured rather than
  assumed**, and it is only measurable because both halves are recorded.
* **Leakage exposure** (§3.5): the distribution of matrix epochs relative to label times, and the
  rate of near-duplicate observations arising from re-fire.
* **Retention state** (§7 of the build prompt): the policy in effect, the age of the oldest
  surviving label, and how many rows have been pruned. Pruning a training corpus is **censoring by
  policy** — legitimate, and it must be visible, because a model trained under twelve months'
  retention and one trained under three are not comparable and nothing else would record which was
  which.

## 6c. Why the report is a CLI subcommand and not a route (added 2026-08-01, v0.8.0)

Two reasons, and the second is the stronger:

1. **It adds no HTTP surface** to the scope bypass of §5a. A route would need a capability, a scope
   posture, a declaration, a rate limit and a place in the perimeter — five things that can each be
   got subtly wrong — to deliver a report that no scoped principal may read anyway.
2. **A deterministic CLI report can be a guard.** Run over a fixture in `make qa` with its output
   compared byte-for-byte, it **fails the suite the day capture changes shape**. That is precisely
   the `make eval` pattern, which is the most valuable thing this repository has, and no UI card
   would ever have that property.

The report becomes a screen when the UI is rebuilt. It becomes a **gate** now, and the gate is worth
more.

## 6a. Where the columns live — **a question for v0.8.0's Phase 0, not settled here** (`v0.8.0: planned`)

Added 2026-07-31 (v0.7.5). The dataset needs columns in two clearly different populations, and one
group that is genuinely undecided. Recording the split — and recording that the third group is
**open** — is the deliverable; deciding it from outside the release that has to build it would be
guessing with someone else's constraints.

**Group A — must extend the `feedback` write path.** One row already exists per operator verdict and
these are facts about *that* row:

* the **membership fingerprint** (§2.2) — the ordered member alarm ids and a digest over them, plus
  the situation's `updated_at` at render time;
* the **scope fingerprint** (§5) — the active scope policy's identity, whether the situation was
  redacted for this principal, and by how many members;
* **label latency** (§6) — enough to compute time between situation open and verdict.

These are one-to-one with a verdict, they are useless if written anywhere else, and `feedback`
already carries `principal_ref` and `role` for exactly the same reason.

**Group B — must be a new table, or new tables.** These have a different cardinality from `feedback`
entirely and cannot be columns on it:

* **one row per evaluated pair** — linked *and* considered-but-rejected — carrying the
  `LinkFeatures` **values** and `scorer_config_id` (§3.1, §3.2);
* **one row per alarm observation**, immutable, carrying the raw material that `alarm` overwrites on
  re-fire (§3.4, §4).

Most evaluated pairs never receive a label at all — §6's coverage metric exists to measure how few
do — so these rows are not a widening of `feedback`, they are a different sink with a different
lifetime and a different retention question.

**Group C — genuinely open, and v0.8.0's Phase 0 must answer it.** The questions, stated so that
release can execute rather than rediscover:

1. **Does the pair-row sink write synchronously on the correlation path, or through a queue?** §4
   rejects per-pair varbind duplication because it is I/O amplification inside the batch lock —
   but the same argument applies with less force to a narrow pair row, and "less force" is not a
   measurement. Take one.
2. **Is the alarm-observation row written per trap, or per activation?** §3.4 requires the state at
   decision time; whether every re-fire is a new observation or only the ones that triggered a
   correlation changes the row count by roughly the dedup ratio, which `make eval` reports as
   ~0.71.
3. **Does the membership fingerprint go on `feedback` as columns, or into a child table keyed by
   feedback id?** Group A assumes columns. A situation with hundreds of members makes the digest
   cheap and the ordered id list not; if the list is stored rather than only its digest, it is a
   child table and Group A shrinks by one row.
4. **What retention applies to each sink?** `retention_days` today governs operational data. A
   training dataset whose oldest rows are deleted on the operational schedule is a dataset that
   silently re-censors itself — and §3.1 exists because censoring is the thing this schema is
   built to avoid.

**Ambiguity about a v0.8.0 design decision resolves to "the v0.8.0 build decides."** Each of these
depends on a measurement that release will take in its own Phase 0, and a specification that
pre-empts a measurement is a specification that will be wrong in a way nobody re-checks.

### 6a-answered — Group C, resolved by measurement (added 2026-08-01, v0.8.0)

All four were taken in [`../gates/v0.8.0-phase-0.md`](../gates/v0.8.0-phase-0.md), and the two that
also depend on a design measurement are recorded in `DESIGN.md`'s v0.8.0 section. Each answer
records the number that decided it, so a later reader can disagree with the *choice* while holding
the same *fact*.

| # | Question | Answer | The measurement that decided it |
|---|---|---|---|
| 1 | Sink write synchronous or queued? | **Synchronous**, in the transaction the batch already holds | Phase 2. Buffering trades a bounded memory cost for fewer transactions; the batch is already one transaction per 500 items, so the transaction count is *unchanged* either way — buffering would buy nothing and would put dataset rows outside the atomicity the batch already provides. Phase 6 measures the resulting cost per trap. |
| 2 | Observation row per trap or per activation? | **Per activation** | 3 159 traps → 2 256 activations, ratio **0.7142** (agreeing with `make eval`'s independently-reported `dedup_ratio` 0.7156). The 903 extra rows a per-trap policy would write are re-fires that by construction never reached `score_link`, so **no pair row could ever reference them**: write amplification on the ingest path buying nothing joinable. |
| 3 | Membership as columns or a child table? | **A child table**, keyed by feedback id, **plus** the digest stored alongside for cheap comparison | §2.2a. The deciding argument is not size, it is the merge: after a merge there is nothing left to compare a digest *against*, so a digest-only record proves that something changed and cannot say what it was. The bag is what §3.3 needs, so the bag is what is stored. |
| 4 | What retention applies to each sink? | **Three tiers, admin-configurable, ordering enforced fail-closed**: sink 21 days *and* a row cap; training 12 months; audit 24 months | §7 of the build prompt. The sink grows with **traffic** and the dataset grows with **labels**, so one retention number cannot serve both. The 21-day sink default is explicitly a **conservative guess**, and this release measures real label latency (§5c) so a later one can replace faith with data. |

**Group A shrinks by one row, exactly as the question anticipated.** The membership fingerprint is
not a `feedback` column; it is a child table. What stays on `feedback` is the scope fingerprint, the
acquisition channel, the capture provenance, the latency material and the lineage.

## 6b. Confidence, uncertainty, and the calibration v0.9.0 owes (added 2026-08-01, v0.8.0)

**Not in v0.8.0.** Recorded here because the direction is real, it is asked for repeatedly, and the
half that is a *trap* needs writing down before somebody builds it.

**The direction (v0.9.0+).** A challenger that emits a probability rather than a bare verdict can
route what the operator is asked: surface the uncertain cases, stay quiet on the confident ones. That
is a legitimate and valuable use, and it is the natural bridge to the active learning §5b's
acquisition-channel column exists for.

**The obligation that comes with it.** A probability is only useful if it means what it says:

> **Calibration is a metric v0.9.0 owes** — reliability curves, and Brier score or ECE. A threshold
> on an uncalibrated score is meaningless.

A model that says "0.9" on cases it gets right 60 % of the time is not 90 % confident; it is
wrong about its own reliability, and every downstream decision that reads the number inherits the
error. Calibration is cheap to measure and is not implied by accuracy — a model can rank perfectly
and be badly calibrated, which is exactly the case where a threshold does the most damage. v0.9.0
reports it or its probabilities are decoration.

**The trap, stated as a rule.** The long-term roadmap's autonomy trigger is **measured agreement
with humans** — never the model's own confidence.

> **Confidence may route what the operator is asked. It may never authorise autonomy.**

The two are different quantities and the failure mode is silent: a model that is confidently wrong
is *more* dangerous than one that is uncertainly wrong, because confidence is what a self-authorising
system would use as its gate. Self-reported certainty is not evidence of correctness; it is evidence
about the model's internal state, and the two diverge exactly where it matters. The only admissible
gate is the one measured **against human verdicts** — which is what this release captures, and why
directive 3's invariant (§3.1a) applies to that measurement too.

Both halves belong in the record: the direction is worth pursuing, and the licence it must never be
read as granting.

## 7. Explicitly not in v0.8.0

1. **Any model, of any kind.** Shadow-mode training is v0.9.0.
2. **The label-derivation policy** (§3.3) — deliberately left open so v0.9.0 can evaluate it.
3. **Any change to `POST /api/situations/{sid}/feedback`'s required contract** — the fingerprint is
   additive and optional (§2.2).
4. **Optimistic-concurrency rejection** (§2.1). Recorded as rejected, with the reasoning, so it is
   not reintroduced as an obvious improvement.
5. **Removing `MAX_LINKS_PER_ALARM` or `MAX_CANDIDATES`.** They bound work on the ingest path. The
   dataset sees the uncensored result *before* truncation (§3.1); the engine's own persistence is
   unchanged.

*Added 2026-08-01 (v0.8.0), from the build prompt's out-of-scope list:*

6. **The train/test split.** v0.10.0's. This release records what makes *any* split possible later
   — time, lineage, operator, incident — and **assigns nothing**. §3.5d names the hazard that
   waiting creates so v0.10.0 does not rediscover it.
7. **Active learning / soliciting labels.** v0.9.0 or later. v0.8.0 records the *acquisition
   channel* column (§5b) and writes only `organic`.
8. **A UI for the dataset, the bias report, or retention.** The report is CLI (§6c). The one UI
   change v0.8.0 permits is §2.2's optional fingerprint field on the feedback POST — nothing else
   in `ui/app.js`, no new panel, no new card, no restyling. The report becomes a screen when the UI
   is rebuilt.
9. **The partial-split affordance** — *"these three yes, that one no"*. Deferred, and recorded on
   [`../ROADMAP.md`](../ROADMAP.md), because it is **the single highest-leverage UI change for the
   whole ML roadmap** (§3.3a): it converts a `split` from a bag with at-least-one-negative — which
   licenses nothing pairwise — into a bag with the negatives identified, which licenses everything.
   No amount of modelling cleverness in v0.9.0–v0.13.0 recovers what that one affordance would
   provide at the source.
10. **Confidence-driven autonomy** (§6b). The direction is v0.9.0+; the autonomy trigger is measured
    agreement with humans, never self-reported confidence, in any release.
