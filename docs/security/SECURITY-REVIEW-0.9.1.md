# Security review — v0.9.1

Continues from **F45**. The next finding this project issues, in any release, is **F46**.

**No new finding is issued.** §§1–4 are the checks that back that claim rather than an omission, §5
records one contract narrowing found and reasoned about inside the release, and §6 is the critical
analysis. The F-series tracks defects in **shipped** code; a gap in the test suite is not one, and
§5.2 explains why the audit's most interesting result therefore gets no number.

This release's attack surface is **two optional fields on two existing endpoints**. There is no new
route, capability, audit action, dependency or served path, and `make eval` is byte-identical.

---

## 1. The exclusion set as hostile input

`excluded_ids` is client-supplied on the feedback write path — the path that produced **F34**
(a scoped editor's existence oracle), **F35** and **F39**. It is treated exactly as
`FEEDBACK-DATASET-0.8-DRAFT.md` §5.4b treats the client fingerprint, and for the same reasons.

### 1.1 Bounded

Two bounds, and they do different jobs:

| Bound | Where | What it stops |
|---|---|---|
| `max_length=4096` | `FeedbackIn` / `CloseIn` (pydantic) | an **unbounded parse** — a caller streaming a million integers |
| `MAX_CLIENT_MEMBERS = 512` | `Exclusion.accept` | an unbounded **write**, and the same ceiling the bag already uses |

The pydantic ceiling is deliberately far above the storage bound, because a parse limit that doubled
as a validation limit would make the truncation a **rejection**, and rejection is the wrong primitive
for an observation.

### 1.2 Never rejected

`Exclusion.accept` has exactly one behaviour: it truncates, and records that it did in
`feedback.excluded_truncated`. There is no path in it that raises, rejects, or inspects an id's
meaning. **The truncation is a fact on the row rather than a silence**, which matters more here than
it does for the bag: a clipped marked set asserts **fewer** negatives than the operator made, and a
reader must be able to tell.

### 1.3 Not an existence oracle — status, body and timing

The marked ids are written to `feedback_exclusion` **verbatim**. Nothing looks them up, compares them
to anything, or checks whether they exist. `Exclusion.marked_positions` intersects them with the
server's own bag, and an id that is not in the bag simply contributes to no pair — it is still
recorded, because it is evidence about what the client reported.

Proven by `tests/test_partial_split.py`:

* `test_a_marked_id_that_does_not_exist_changes_nothing` — identical status and identical body for a
  real member id and for `999_999_999`, and the ghost is stored verbatim;
* `test_the_oracle_is_closed_in_timing_too` — medians over seven posts each way, asserting no usable
  ratio between them.

**The residual, stated rather than glossed.** Work under `penalize` is proportional to
`|marked ∩ bag| × |rest|`, so a caller who guessed an id *that is in the server's bag* causes
marginally more work than one who guessed an id that is not. Three things bound it, and the third is
the one that settles it:

1. the difference is a handful of set operations against an HTTP round trip and several SQLite
   statements — the timing test measures it and finds no signal;
2. a caller already knows the bag for any situation in their scope, so for that population it
   discloses nothing they cannot read from `GET /api/situations/{sid}`;
3. **for the redacted members of a scoped view it is not a new channel at all.** `record_label` has
   written the *full* server bag and `penalize` has operated on it since v0.8.0, regardless of scope,
   so the work a scoped editor's verdict causes has always been proportional to the whole bag. The
   exclusion modulates within a channel the caller already drives; it does not open one.

---

## 2. The close endpoint's new field

| Property | v0.9.0 | v0.9.1 |
|---|---|---|
| capability | `situation.close` | `situation.close`, **plus `feedback.write` when a verdict is carried** |
| scope check | `situation_in_scope`, denying through the same 404 as "no such situation" | identical, same code path |
| audit | one `situation.close` row | one `situation.close` row; `details` gains the verdict when there is one |
| transaction | one `write_txn` | one `write_txn` — close and label land together or not at all |
| closing without a verdict | 200, no label | **byte-for-byte identical** |

**The capability gap, and why it needed closing.** `feedback.write` and `situation.close` are both
`editor` in the compiled table, but they are distinct capabilities and `resolve_capabilities` is
`ceiling ∩ policy`, so a stored governance policy granting one and restricting the other is
**reachable**. Without the check, `POST .../close` would have been a route by which a principal
denied `feedback.write` could write a `feedback` row — a privilege escalation created by an
additive field.

The check reads `request.state.capabilities`, the set **the perimeter already resolved for this
request**, so no authorization decision is re-implemented (DECISIONS #65, #76). It **refuses**
rather than silently dropping the verdict: discarding a judgement without saying so is the exact
failure this release exists to end. `tests/test_partial_split.py::test_a_close_with_a_verdict_needs_feedback_write_as_well`
activates such a policy and asserts 403 for the close **with** a verdict and 200 for the close
**without** one.

**Does the 403 leak anything?** It discloses that the principal lacks `feedback.write` — which they
can already read from `GET /api/me`. No.

---

## 3. `penalize` with an exclusion set

| Requirement | Status |
|---|---|
| bounded by the same caps | ✅ `EPOCH_PAIR_CAP` and `SPLIT_PENALTY` are unchanged and untouched |
| F36 boundedness tests unedited | ✅ the learning effect still applies only on a genuine insert, so a situation's total influence stays bounded at two applications however many times anyone posts |
| no path by which a marked set exceeds today's penalised count | ✅ **provable, not measured** |

The asserted pairs are, by construction, a **subset** of the pairs `penalize` visits without an
exclusion: the predicate `(i in marked) != (j in marked)` filters the same `i < j` enumeration. The
distinct class-pair and device-pair cells they span are therefore subsets of today's, and the
penalised cell count **can never exceed** it. `test_an_exclusion_never_penalises_more_than_no_exclusion`
checks it across bag sizes either side of `EPOCH_PAIR_CAP` and marked counts from 0 to 5, because the
cap is applied **before** marking and that is exactly where an off-by-one would hide.

The degenerate cases resolve **downward**, which is the direction the bound requires: marking every
member asserts nothing (`rest` is empty), and a mark beyond the cap asserts nothing.

---

## 4. What did not change

| Claim | Evidence |
|---|---|
| no new capability | `rbac.PERMISSIONS` unchanged |
| no new audit action | `situation.close` and `feedback` only; the verdict rides an existing row's `details` |
| no new route | `rbac.ROUTE_PERMISSIONS` unchanged; the declaration gate green |
| no new runtime dependency | five, unchanged for nine releases |
| no new served path | `ui/` is four files |
| `make eval` byte-identical | `c2e8a0ced29d9edf986279d41089ddb68e18da65a46bdc7e9f04811e8b9b6f26` |
| the dataset is still admin-only | the three reports are CLI subcommands; no route reads a dataset row |

---

## 5. One contract narrowing, found and reasoned about inside the release

`POST /api/situations/{sid}/close` previously accepted **any** body and ignored it. It now parses one.
Measured, request shape by request shape:

| Request | v0.9.0 | v0.9.1 |
|---|---|---|
| no body at all | 200 | **200** |
| `{}` | 200 | **200** |
| `{"verdict": null}` | 200 | **200** |
| `{"foo": 1}` (unknown field) | 200 | **200** |
| `{"verdict": 123}` (invalid value) | 200 | **422** |

**One row moved**, and only that one: a body that *names* `verdict` with a value outside
`{"confirm", "split", null}`. No shipped client sends one; the UI sends `{}` or a valid verdict, and
a `curl` sends nothing.

**Recorded rather than smoothed over**, because "the API contract extends; it never breaks" is a
directive of this release and this is the one place it is not literally true. The alternative —
accepting a malformed verdict silently — is worse: it would discard a judgement the caller believed
they were recording, which is the exact failure this release exists to end. Every other endpoint in
the product has always answered 422 to a malformed body. It is in `MIGRATION.md` under the upgrade
notes, where an integrator will find it.

**No F-number.** It is a deliberate, argued interface decision affecting only input that was
previously discarded, not a defect in shipped code.

### 5.1 Why the test audit's most interesting result gets no F-number either

The audit found that `feedback.write` and `situation.close` could each be moved from `editor` to
`viewer` — granting every read-only account the ability to write labels and close situations — with
**all 958 tests green**.

**The shipped code was correct throughout.** `PERMISSIONS` says `editor`, the perimeter enforces it,
and no principal ever held a capability they should not have. What was missing was a **test**, and
the F-series tracks defects in shipped code (v0.9.0 §3.1 set that precedent for a defect found and
fixed inside a release). A missing guard is a real risk and is recorded as one, in
`../gates/v0.9.1-test-audit.md` §2.1 and in the commit that closed it; it is not a finding.

**The one that comes closest to being a finding, and why it is not.** Seed A4 inverted the
perimeter's fail-closed branch so that a route with **no declared capability is allowed**, and
nothing failed. That is a fail-*open* default in the authorization perimeter. It is not a finding
because the state is **unreachable in a built application**: the declaration gate refuses to register
an undeclared `/api` route at all, and its own seeds (D1, D2) were caught. What the audit
established is that **the second layer of a two-layer defence has no test**, which is a ROADMAP line
and a good argument for one.

---

## 6. Critical analysis

### 6.1 What the seeded-defect audit did not cover

Everything except one shape of defect. Thirty-one **single-token** slips — an off-by-one, a dropped
clause, a flipped comparison, a widened bound — say nothing about:

* **defects of other shapes**: a wrong algorithm, a missing case, a race, a design that is coherently
  wrong. `penalize` could take the exclusion set and apply it to the wrong matrix and every seed here
  would still have behaved identically;
* **the UI**, which no seed touched because **no seed could**: `app.js` is never executed by this
  suite, so a defect in it cannot be caught or missed — it is simply outside the instrument. This
  release added a stateful `Set` to that file whose contents become an assertion in the corpus, and
  the only thing standing behind it is that it is a dozen lines a reviewer can read;
* **anything requiring two changes at once**, which is what a subtle regression usually is;
* **the sampling itself.** The seeds were chosen by me, in the subsystems the brief named. A
  subsystem I did not think to seed reads as neither caught nor missed, and there is no list of what
  I did not think of.

The honest summary is that the audit **maps the guards, not the code**. Nineteen of thirty-one is a
statement about the suite's coverage of trivial slips in eleven named places, and it should not be
read as a defect density anywhere.

### 6.2 Is the close channel's selection large enough to distort the corpus before anyone notices?

**Not in this release, and that is an accident of DECISIONS #130 rather than a safeguard.** The UI
never sends a verdict with a close, so the shipped appliance writes `organic` on every row and the
`close` population is empty. The question is real for whichever release ships the gesture, so:

**The selection is probably strong.** Closing selects for *resolved* incidents — investigated,
understood, and finished — while organic labelling selects for whatever an operator opened. On the
mixedness cut that the whole ML programme turns on, the two populations plausibly differ a lot: a
resolved incident is more likely to be one the champion grouped correctly, so `close` should skew
toward `confirm`, and confirms on uniform bags are the least informative label the system collects.
The agreement fixture is built to make that visible rather than to flatter it: `organic` reads
50.0 % and `close` reads 100.0 %, and a blended headline of 66.7 % would describe nobody.

**Would anyone notice before it did damage?** The mechanism to notice exists — every conditional cut
is repeated per channel, and the bias report breaks out labels, verdicts, partial splits and
asserted negatives per channel. What does **not** exist is anything that *forces* a reader to look:
no threshold, no warning, no test that fails when the channels diverge. With *n* in the tens, a
channel that quietly became 60 % of new labels would move the headline before either channel had
enough bags for its own interval to be printable — the cluster bootstrap refuses below ten incidents,
so the per-channel intervals would read `n/a` exactly when they were most needed.

**So the honest answer is: the corpus is protected against *blending*, and not against *dilution*.**
The separation is structural and permanent; the vigilance is a person reading a report. If the
gesture ships and the rate genuinely rises, the release that ships it should add the thing this one
did not: a stated expectation for the channel mix, and something that goes red when it is exceeded.

### 6.3 Will this release move the floors? Almost certainly not

Two reasons, and the second is the one that settles it.

**The exclusion is optional and the plain path is still one click.** An operator in a hurry presses
Split. Nothing here makes that harder — deliberately — so the fraction of splits carrying an
exclusion is an operator-behaviour question on which this project has **no data whatsoever**. Gate 0
§2.1 makes that worse rather than better: eleven of the thirteen `split` bags in the fullest corpus
available have fewer than two members, so the affordance could not even be *exercised* on the corpus
this project can construct, let alone measured.

**And the floors are counted in bags.** `split` bags ≥ 50, mixed bags ≥ 20. **A partial split is
still one `split` bag.** Making a bag more informative cannot move a threshold denominated in bags,
and it was never going to. The release changes what a label is *worth*, not how many there are.

What it does buy is real and is worth the diff: when the corpus does reach a size worth modelling,
its `split` bags will support a negative class that is **observed** rather than derived — which is
the choice v0.9.0 had to make between policy A (fabricate negatives) and policy B (discard the
minority class), and neither was good. That is a structural improvement to the evidence, and it does
not show up in any floor.

**The thing that would move the floors is the acquisition rate, which this release deliberately did
not raise.** The endpoint exists, is tested, and is reported per channel. The gesture is a UI
release's to make.

### 6.4 A fourth note: this release made `engine.py`'s ceiling bind, and paid for it structurally

Adding two parameters to `apply_feedback` took `engine.py` from **exactly 580** — its
`COHESION_EXEMPT_CEILING`, with zero headroom — to **600**. The ceiling could not be raised (this
release's own rules forbid it, and DECISIONS #121 refused the same ratchet), so the cost was paid by
moving code out: a `LabelContext` bundle and `server_bag`, both to `labels.py`, taking it to **569**
(DECISIONS #129).

That is the right outcome and it is worth naming as a **standing hazard**: a cohesion exemption whose
ceiling equals the file's exact size means **every future release that touches the ingest path pays a
refactoring tax before it can add a line**. Twice now — #121 and #129 — the tax has been paid in a
way that improved the code. It will not always be, and the release where it is paid badly will be the
one where the exemption's own justification, that the ingest path can be read in one place, quietly
stops being true.
