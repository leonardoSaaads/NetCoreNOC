# Security review — v0.11.0 (champion / challenger)

Findings continue from **F51**. One new finding, **F52**, found by this release's own guard
demonstrations and closed in the same release.

Ratified plan `e011ee6ad2367d44f2ede14cad7b072df598298f91ecc1a405744358b589d449`.

---

## 1. The six assessments Phase 8 requires

### 1.1 No path promotes without an admin

**Assessed: holds.**

`POST /api/promotion` maps to `promotion.write`, whose minimum role is `admin`, and the `admin_only`
scope posture is **derived** from that capability rather than asserted independently (the invariant
`rbac/tables.py` enforces at import, DECISIONS #58/#80). Viewer and editor receive 403 **and write no
row**, asserted by test.

There is **no scheduler, no maintenance hook, and no configuration flag** that applies a promotion.
`maintenance.py` is unchanged except that the seal it already constructs can now be spent — by
`promotion.evaluate`, which is only reachable from the route. **There is no `auto_promote` flag, not
even defaulted off**, and the reason is stated in the module: a flag defaulted off is a flag.

### 1.2 The verdict cannot be supplied by a client

**Assessed: holds, structurally.**

`PromotionIn` has exactly two fields, `model_version_id` and `note`. There is no `verdict`,
`metrics`, `floors_met` or `query_count` field to fill. A request carrying all four is answered
`INSUFFICIENT_EVIDENCE` with `query_count 0`.

The guard is two-sided on purpose: a **structural** test asserts the field set exactly, and a
**behavioural** test asserts the outcome. Phase 6 §3 shows why both are needed — adding the field
does not by itself change the verdict, so the behavioural test alone stays green.

### 1.3 Both pointers cannot be active

**Assessed: holds, at the database.**

`CHECK ((config_id IS NOT NULL) + (model_version_id IS NOT NULL) = 1)`. Proven by attempted
violation in both directions **and with a control** that a legitimate single-pointer move is still
accepted — the control caught a defect in the probe itself (Gate 2 §2). Both writers null the other
column in the same statement, so the `CHECK` is the second line of defence rather than the first.

### 1.4 A malformed payload cannot reach the engine

**Assessed: holds.**

Eleven malformed-input classes, each asserting the load path did not raise, fell back to the coded
default **by fingerprint**, cleared `scorer_model_version_id`, and warned with its own reason. The
`except` is deliberately wide and a `RecursionError` injected at the seam proves it: `ModelPayloadError`
cannot express what a future kind's constructor does.

**The residual risk is named rather than dismissed**: `SafeScorer` catches an exception at score
time and cannot catch a parameter set that scores fine and destroys grouping. That is what the five
degeneracy rules are for, and all four injectable ones are demonstrated red (Phase 6 §5–8).

### 1.5 The promotion path cannot stall or fail ingestion

**Assessed: holds, by injected failure.**

The promotion path runs **entirely on the HTTP side**. It is not reachable from
`receiver.datagram_received`, and the ingest path gains no lock, no I/O and no per-packet work —
`make eval` is byte-identical and `scoring.py` has not moved a byte.

The one place promotion touches the engine is the **reload point**, which already existed:
`load_scorer_config` runs at start and at the top of each maintenance pass. The injected failure is
Phase 6 §9 — a load path that raises instead of falling back. It goes red, and the fallback keeps
correlation running on the shipped default rather than stalling.

### 1.6 No capability beyond the promotion routes was added

**Assessed: holds.** Two capabilities, `promotion.read` (viewer) and `promotion.write` (admin), and
two routes. The capability table's expectation test had to be updated deliberately, which is the
mechanism that makes this checkable rather than asserted.

---

## 2. F52 — the asserting-bag predicate was unguarded

**Severity: moderate. Status: closed in this release.**

`Store.asserting_bag_rows` selects `WHERE f.verdict = 'split' AND f.excluded_reconciled >= 1`. That
`1` is what makes a bag *asserting*: a `split` with no reconciled mark asserts no **pair**.

**Widening it to `>= 0` left all 1 296 tests green.** Measured, not inferred — the full suite was run
with the predicate widened.

**Why it matters.** `asserting_bags ≥ 50` is `PREREGISTRATION-0.10.0.md` §2.2's **PRIMARY** floor,
and from v0.11.0 that floor decides whether a promotion may be considered at all. A predicate
counting bags that assert nothing would let the floor be cleared by evidence that does not exist.
It is **F46 in a new place**: a threshold counting a quantity other than the one it names.

**Why nobody noticed.** On this corpus the verdict does not change — 13 `split` bags is below 50
either way — so the release's headline outcome is insensitive to it. **A larger corpus would not
be.**

**How it was found.** By the mandated re-run of F48's injection in Phase 6. F48's own tests passed
under it, which is itself informative: F48 guarded the *label write path*'s `source = 'server'`
predicate, and nothing guarded the *judge's* consumption of the result.

**Closed by** `test_f52_a_bag_with_no_reconciled_mark_is_not_an_asserting_bag`, with the control
first, demonstrated red under the injection and green on restore.

---

## 3. Critical analysis — four honest notes

### 3.1 What a promotion's evidence does and does not license

A `promotion` row with `outcome = 'applied'` licenses **one** inference: *an admin, at a named time,
approved a swap that a server-derived verdict said was justified, citing a stored evaluation over a
stored fold assignment, under a named ratified plan, with the seal's query count recorded.*

It does **not** license *"the challenger is better"* as a claim about networks. The verdict is over
**this corpus**, at **this `n`**, under **this plan**, using **four named quantities** that are never
composed. It says nothing about a different customer's traffic, a different alarm mix, or the same
network six months later.

It also does not license *"the champion was worse"*. `BETTER` requires the interval to exclude zero
on both headline rates — a statement about *this* comparison's resolution, not a ranking.

**And on this corpus no such row can exist.** Every promotion row v0.11.0 can produce carries
`outcome = 'refused'`.

### 3.2 Which of the plan's registered decisions I would argue is wrong, now that I have seen the data

**The conditional seal policy of §3 — specifically its *order* — and I would argue it is subtly
wrong in a way this corpus hides.**

§3 registers *floors first, power second, seal last*, and gives the right reason: never open the
holdout on a corpus that has already failed. **But the power condition as implemented depends on the
observed difference**, and the observed difference is computed from the four named quantities —
which on a corpus with no asserted negative pairs are **not computable at all**. This release
records them as absent and derives `observed_difference = 0`, which makes `Power.sufficient` false
and fires `POWER`.

That is the right *outcome*, reached by an accident of representation. `POWER` fired because the
metrics were **absent**, not because the corpus **could not resolve a real difference**. Those are
different facts, and the trigger conflates them — the same conflation the three-valued verdict exists
one level up to prevent.

**What I would register differently for v0.12.0**: a fourth state for a quantity that is *not
computable*, distinct from *computed as zero*, and a trigger that names it. The `unavailable` column
already carries the information in prose; it should be a trigger. **This is an opinion for the next
release, not an edit here** — the plan is ratified and the data that produced this opinion arrived
after ratification, which is exactly the sequence §8 anticipates.

### 3.3 Whether the fold materialisation makes an evaluation reproducible, or only citable

**Only citable, and the distinction is not academic.**

What Phase 4 buys: the **stored** fold assignment stops moving when the merge graph does. A citation
resolves to the same 111 rows a year later.

What it does **not** buy, and I want this stated plainly because the phase's name invites the wrong
reading:

* **It does not reproduce the evaluation.** The folds are one input. The *metrics* computed over them
  depend on `dataset_pair` rows subject to the sink's dual retention bound, and on labels subject to
  the audit tier. **No retention tier knows what a citation is** — a promotion can cite a run whose
  inputs have since been pruned, and nothing warns.
* **It does not explain why an incident moved.** The merge graph is still unsnapshotted.
* **It does not make the seal's membership reproducible.** `holdout_seal_member.incident_id` was
  written from a map taken at construction. If identity drifts, the sealed set and the estimator's
  exclusions come from two maps at two times — `DATA-LINEAGE.md` §4's second-order hazard, still
  open.

So the honest claim is: **a promotion's numbers now point at rows rather than at a recomputation.**
Calling that "reproducible" would be the kind of sentence `EVIDENCE-BOUNDARY-0.9.2.md` §10 became.

### 3.4 The guard I am least confident in

**The mutation ledger, and by extension the claim that this release's guards are adequate.**

Not because any individual guard is weak — thirteen are demonstrated red with controls. Because
**the ledger is manual and its author chose the mutations**, and Phase 6 produced direct evidence
that this bias is real: the re-run of F48's injection found **nothing**, over a predicate that
decides the release's primary floor, and it was found only because the build prompt *mandated that
particular re-run*. Nothing I chose to try found it.

That is one measured instance of the ledger missing something material. There is no reason to
believe it is the only one, and I would not claim the surviving mutants list is complete.

**A second, narrower one**: §5 of the guard demonstrations records that the `incumbent_linked`
injection **could not be performed**, because no expression reaches it from a promotion decision.
Injecting it would have meant writing the defect first and then catching it — a demonstration of the
test, not of the code. The claim is therefore supported by an absence and by a pre-existing AST
guard, which is weaker than the other twelve, and it is listed as a limitation rather than a check.

---

## 4. Open questions carried to v0.12.0

1. **§3.2's fourth state** for a non-computable quantity, and a trigger that names it.
2. **`MAX_ABS_COEFFICIENT` and "a project floor a deployment may raise".** Read literally as *raise
   the number*, the plan's §5 makes rule 5 the one softenable rule, which contradicts its own §1.
   **v0.11.0 ships no override in either direction**, making the question moot rather than answered
   (ADR #164). v0.12.0 should register which reading it means before shipping one.
3. **Snapshotting the merge edges** (`DATA-LINEAGE.md` §5), which is what §3.3 says is actually
   needed.
4. **Retention does not know what a citation is.** A promotion can cite a run whose evidence is
   later pruned.
5. **The v0.9.2 reconciliation-drift audit gap** (`SECURITY-REVIEW-0.10.0.md` §3.4) remains open by
   deliberate scope decision (ADR #162), not by oversight.
6. **A UI for promotion**, gated on a test that executes `ui/app.js` in a real DOM (ADR #163).
