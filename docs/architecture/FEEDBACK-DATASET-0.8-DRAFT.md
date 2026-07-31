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

That is the majority class. Supervised training without it is impossible: a model trained only on
pairs that linked learns "everything links".

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
