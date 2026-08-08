# SCOPE — v0.9.1

**The operator can say *which* members do not belong — and the project audits whether its own tests
would notice if they were wrong.**

A patch-shaped release in the v0.7.5 mould: a small, auditable diff that improves the **evidence the
product gathers**, and audits the tests that guard everything else. It is the second release
inserted for label integrity, and the first one bought the whole v0.8.0 dataset.

Binding authorities, in the order they win: this document on **scope**;
[`../architecture/MODULE-ARCHITECTURE.md`](../architecture/MODULE-ARCHITECTURE.md) on **where code
goes**; [`../security/threat-model.md`](../security/threat-model.md) on **security posture**; the
build prompt on process and quality.

---

## 0. Why this release exists

Two releases in a row reported insufficiency. v0.9.0's floors, measured on the fullest corpus
available and **re-measured on this tree** in
[`../gates/v0.9.1-phase-0.md`](../gates/v0.9.1-phase-0.md): **13 `split` bags against a floor of 50,
5 mixed bags against 20, and exactly one bag that is both.**

The binding constraint on everything from v0.10.0 to v0.13.0 is not a missing evaluator — it is a
missing **label**. And there is a defect in the label itself, named in four documents and deferred in
all of them:

> a `split` verdict asserts *"these members are at least two situations"* **without saying which**,
> so it supports no truth partition, and `split_bag_intact_rate` had to be invented as a separate
> quantity because folding it into an over-merge rate would fabricate a denominator.

So the minority class — **the only source of negative evidence in the entire system** — is also the
least informative label the product knows how to collect. That is an **acquisition defect**, not a
modelling one, and it is why this release exists.

Gate 0 §1 demonstrates it by query and by arithmetic: the complete recorded evidence of a `split` is
a verdict and an ordered member list, **no pair is asserted negative anywhere in the schema**, and
`learn.penalize()` responds by halving every matrix cell the bag spans — on a nine-member bag, all
36 member pairs driving 72 cells, from an assertion that names none of them.

---

## 1. In scope — exactly five workstreams

### W1 — The partial split

The operator marks *which* members do not belong, and the assertion is captured **exactly as made**.

| | The operator does | Assertion yielded | Cost per click |
|---|---|---|---|
| pairwise | names two members that do not belong together | one negative pair | high on a large bag |
| **exclusion set** ✅ | marks the members that **do not belong** | `\|marked\| × \|rest\|` negative pairs | one gesture |
| full partition | assigns every member to a group | the whole partition | high, and usually unknown |

**The semantics, held exactly** (DECISIONS #124):

* pairs **(marked × rest)** → **negative, asserted by the operator**;
* pairs **within rest** → **unknown**, unless the operator separately asserts otherwise;
* pairs **within marked** → **unknown**;
* the bag is still a `split` — at least two situations, now with some of the boundary named.

On a nine-member bag with two marked, one click yields **fourteen asserted negatives** where today it
yields none; twenty-one pairs within the remainder and one within the marked set stay
**unasserted**, and 14 + 21 + 1 = 36 = n(n−1)/2 exactly. The identity is what makes *"and nothing
else"* checkable rather than merely promised.

The remainder assertion is a **separate, nullable, never-inferred** field where `NULL` means *"not
asserted"*.

### W2 — The acquisition rate

`POST /api/situations/{sid}/close` accepts an **optional** verdict, so judging and closing are one
gesture rather than two — recording the same label the feedback endpoint would, in the same table,
under the same fingerprint and scope discipline, and writing `acquisition_channel = 'close'`,
**never `'organic'`** (DECISIONS #126).

Closing without judging stays exactly as easy as it is today. No modal, no prompt, no nag, no
required field, and no close that fails without a verdict.

### W3 — Informativeness, not only count

Added to the three reports that already exist, all of which stay deterministic, fixture-gated,
aggregates-only and admin-only: asserted negative pairs, total and per label; the
plain-versus-partial split breakdown and the distribution of `|marked|` against bag size; the
remainder-assertion rate and how often it was offered versus taken; every existing conditioning
repeated **per channel**; closes without a verdict; and the v0.9.0 floors re-evaluated — including
the `split ∧ mixed` quantity **as an additional observation, never substituted for the registered
floor**.

### W4 — A critical audit of the test suite

A seeded-defect audit, `docs/gates/v0.9.1-test-audit.md`, **which is a sample and not a proof**. It
closes only gaps this release's own themes touch; everything else is a ROADMAP line.

### W5 — Security review from F46, and the v0.10.0 specification updated

`docs/security/SECURITY-REVIEW-0.9.1.md`, and
[`../architecture/HONEST-JUDGE-0.10-DRAFT.md`](../architecture/HONEST-JUDGE-0.10-DRAFT.md) updated in
place, dated, never rewritten.

---

## 2. Exactly two intentional behaviour changes

1. **The feedback endpoint accepts and stores an exclusion set** (and the optional remainder
   assertion).
2. **`learn.penalize()` uses it when it is present** — penalising only the asserted `(marked × rest)`
   pairs. **Absent, behaviour is byte-identical to v0.9.0.**

Everything else this release adds is recording. `make eval` is byte-identical: the engine's
correlation path is untouched and feedback has never been part of eval.

---

## 3. Explicitly out of scope — deferred, with the reasoning

1. **Anything that trains, evaluates or judges.** v0.10.0. This release produces better evidence and
   draws no conclusion from it.
2. **Active learning / uncertainty-based solicitation.** Asking about the cases the model finds
   uncertain is a different channel with a different bias, and it belongs after there is a model
   worth listening to. The channel column makes it separable when it comes.
3. **A full partition affordance** — the operator assigning every member to a group. More
   expressive, far more work per click, and **an operator usually does not know the whole
   partition**: they know that *those two* do not belong, not what the correct grouping is, and an
   affordance that demands the unknown gets abandoned or guessed at. A guess recorded as an
   assertion is worse than no assertion. Considered and rejected in DECISIONS #123.
4. **Pairwise marking** — naming two members that do not belong together. It yields one negative per
   gesture and is dominated by the exclusion set at every bag size ≥ 3. Also DECISIONS #123.
5. **UI remodelling, a labelling queue, a "situations needing review" screen, keyboard shortcuts, or
   any notification.** Some of these would raise the rate; all of them are the rebuild.
6. **Changing `MAX_CANDIDATES`, `MAX_LINKS_PER_ALARM`, the sink bounds, or retention.**
7. **Re-deriving the v0.9.0 floors.** The security review's argument that the floor should be
   `split ∧ mixed` rather than `split` is **recorded for v0.10.0's pre-registration** and acted on
   there, not here. Moving a threshold in the release that would make it easier to meet is exactly
   what pre-registration exists to prevent. `docs/analysis/PREREGISTRATION-0.9.0.md` is **read-only**
   and its hash guard stays green.
8. **Fixing everything the test audit finds.** A release that fixes every gap it finds is not a patch
   release and its diff cannot be read in one sitting. The audit's value is the map.

---

## 4. What Gate 0 measured that bounds this release's claims

Recorded in scope because it changes what the release may say about itself, not merely what it does.

* **The exclusion set cannot be demonstrated on the corpus.** Eleven of the thirteen `split` bags in
  the fullest corpus this repository can construct have fewer than two members (nine singletons, two
  empty); the other two are storms of 240 and 501 members. Not one would yield a single asserted
  negative pair. The semantics are therefore proved on a **purpose-built fixture**, and any
  projection of how much this raises the `split ∧ mixed` count is **unsupported by the data
  available**.
* **The prize Workstream 2 aims at is not measurable here.** The corpus labels every situation by
  construction, and of its 35 closed situations **none was closed by an operator** — every close came
  from the idle sweep. This release therefore publishes no figure for how much it will raise the
  labelling rate, and instead makes closes-without-a-verdict a **counted quantity from the day it
  ships**, so v0.10.0 has the number this release could not have.

---

## 5. Hard constraints

| Constraint | Value |
|---|---|
| Intentional behaviour changes | exactly **2**, both in §2 |
| Migrations | exactly **1** — `0010`, additive, seeding nothing |
| New runtime dependencies | **0** (five, unchanged) |
| New capabilities / audit actions / routes / served paths | **0** |
| UI files | **4**, unchanged; no panel, modal or restyling |
| `make eval` | **byte-identical** to v0.9.0 |
| Module size | none over **400**; `DEBT_ALLOWLIST` empty; `COHESION_EXEMPT` one entry at 580 |
| v0.7.5 held-card tests, F36 boundedness tests | pass **unedited** |
| v0.9.0 pre-registration | **not edited**; hash guard green |
