# Security review — v0.9.2

Continues from **F45**. This release issues **F46** and **F47**. The next finding this project
issues, in any release, is **F48**.

Both are **evidence-integrity** findings on the feedback write path — the path that has already
produced F34 (a scoped editor's existence oracle), F35, F39 and F44. Neither discloses anything,
neither threatens availability, and neither is a privilege escalation. Both are nonetheless rated
**high**, for one reason: the corrupted quantity is the one v0.10.0 is most likely to promote into a
**pre-registered sufficiency floor**, and the dangerous failure mode is silent.

This release adds **no route, no capability, no audit action, no dependency and no served path**.
`make eval` is byte-identical, `learn.penalize` is byte-identical, and every response is unchanged
in status, body and timing.

---

## 1. F46 — the asserted-negative count is the client's list length

**Severity: high (evidence integrity). Confidentiality: nil. Availability: nil. Privilege
escalation: no.**

### 1.1 The defect

`labels.py::record_label` wrote `excluded_count = len(exclusion.alarm_ids) or None` — the raw length
of a client-supplied list, never intersected with the server's own bag. Three consumers multiplied
it by `member_count − excluded_count`:

| Consumer | Location in v0.9.1 |
|---|---|
| corpus-wide asserted negatives | `bias.py:145-151` |
| per-channel asserted negatives | `bias.py:194-196` |
| the shadow report's `asserted_negatives` | `shadow_report.py:55-56` |

Nothing anywhere checked `m ≤ n`.

### 1.2 The reproduction

Executed over HTTP as an ordinary `editor`, against an unmodified v0.9.1 tree
(`../gates/v0.9.2-phase-0.md` §1, output verbatim, with an honest label as the control):

```
bag of 4 members, 10 ghost ids sent
  -> HTTP 200 {'status': 'recorded', 'verdict': 'split'}
  -> recorded: member_count=4 excluded_count=10 excluded_truncated=0
  -> asserted_negative_pairs  m*(n-m) = 10*(4-10) = -60
  -> unasserted_pairs_in_partial      = 66
```

And the amplification against an honest corpus:

```
8 honest labels (9-member bags, 2 marked each)      total =     112
+ ONE label: bag of 4, 600 ids sent (truncated 512) total = -259,984   delta = -260,096
+ ONE label: bag of 60, 30 GHOST ids marked         total = -259,084   delta =    +900
```

### 1.3 The measured impact, and why the third line is the finding

`−260 096` is **loud**. Any `>= N` floor simply fails on it and an operator reading a negative count
knows something is wrong.

`+900` is the finding. It is positive, plausible, of the right order of magnitude, and composed of
**exactly zero true assertions**. The same label read through `Exclusion.marked_positions` — the path
`learn.penalize` actually uses — resolves **0** marks and moves not one matrix cell. A sufficiency
floor denominated in asserted negative pairs would be satisfied by a label that asserted nothing.

**The learner was never affected.** `learn.penalize` has intersected with the server's bag since
v0.9.1 and was the one consumer that was correct all along. The defect is confined to what the
**corpus reports about itself**.

### 1.4 Why `0010`'s checkability argument did not catch it

Migration `0010` offered `m·r + r(r−1)/2 + m(m−1)/2 = n(n−1)/2` as what makes *"and nothing else"*
checkable rather than merely promised. Substituting `r = n − m` and expanding, **`m` cancels
entirely**: it is a polynomial identity in `m` and `n` and it holds for every integer `m`, including
`m > n`. At `n = 4, m = 512` it closes exactly while the asserted component is `−260 096`.

The test that asserts it (`tests/test_partial_split.py:98`, **not** `tests/test_learn.py` as `0010`'s
comment claimed) fixes `n = 9` and derives `marked` as a set of **positions in the bag**, so `m ≤ n`
holds by construction and the hostile case is not merely untested — it is unreachable from that
fixture.

### 1.5 The fix

`feedback.excluded_reconciled` — `|reported ∩ the server's own bag|`, computed server-side at the
instant of the verdict, distinct by alarm id, and **exactly `len(Exclusion.marked_positions(bag))`**,
the value `learn.penalize` has always used. Every consumer of §1.1 now reads it. `excluded_count` is
unchanged, undeprecated, and reported as `client_reported_marks` — a legitimate measurement of the
client, and the gap between the two is the bias report's first number.

Enforced at four layers: the domain computes the intersection rather than accepting a count; the
store refuses a value outside `[0, member_count]` before the `UPDATE`; migration `0011` carries the
same bound as an enforced `CHECK`; and the maintenance pass recomputes and reports drift.

### 1.6 The regression tests, and their recorded red

Every one fails on the unmodified tree. The verbatim red output is in
[`../gates/v0.9.2-guard-demonstrations.md`](../gates/v0.9.2-guard-demonstrations.md), each with a
named control that passed under the injection.

| Test | Demonstration |
|---|---|
| `test_the_corpus_total_is_derived_from_the_reconciled_count` | `M1` — the corpus-wide consumer reverted |
| `test_the_per_channel_total_is_derived_independently` | `M2` — the per-channel consumer reverted **independently** |
| `test_the_partition_is_non_negative_and_closed_over_the_reconciled_count` | `M3` — the write path reverted |
| `test_reported_and_reconciled_differ_exactly_when_they_should` | `M3`, `M4`, `M5` |
| `test_marking_members_of_a_different_situation_asserts_nothing` | `M4` — reconciliation inverted |
| `test_a_report_truncated_at_the_bound_reconciles_what_survived` | `M6` — truncation and reconciliation swapped |
| `test_a_ghost_marking_moves_no_matrix_cell` | `M12` — `penalize` fed the unreconciled set |

---

## 2. F47 — the assertion does not record whether it could have been made

**Severity: high (evidence integrity). Confidentiality: nil. Availability: nil. Privilege
escalation: no.**

### 2.1 The defect

A **scoped** editor may label any situation with at least one visible member — the specified
behaviour since v0.7.1 — and may mark ids they were never shown, because the redaction deliberately
carries no alarm id, address or entity key, so the ids are guessable. v0.9.1 recorded how much of the
**bag** was hidden (`scope_redacted_members`) and nothing at all about how much of the **assertion**
was blind.

### 2.2 The reproduction

Two NEs inside the scope, two outside, one situation, `editor` restricted to `10.1.0.0/16`
(`../gates/v0.9.2-phase-0.md` §3). The control comes first and it is the one that matters: the
redaction is shown to be **real** and to disclose no alarm id.

```
GET  /api/situations/{sid}  as editor -> 2 of 4 alarms; redacted_members.count = 2
                                         (only class OIDs disclosed; no alarm id leaks)
POST /api/situations/{sid}/feedback  {"verdict":"split","excluded_ids":[3]}   ->  200
  feedback: member_count=4 excluded_count=1 scope_restricted=1 scope_redacted_members=2
  bias:     asserted_negative_pairs = 3
```

Three asserted negative pairs, all three involving an alarm the operator could not observe, and
nothing on the row recording that. On a richer case — a 6-member bag, 3 visible and 3 redacted,
marked `[visible, redacted, ghost]`:

```
reported by the shipped code                          9 pairs
recomputed: m_matched = 2                    ->       8 pairs
pairs between two members the operator saw   ->       2 pairs
```

**Nine reported, eight real, two the operator was in a position to assert.**

### 2.3 F47 has a clock, and it is the reason the fix is eager

`store/retention.py::prune()` deletes `situation_alarm` for aged closed situations, and the member
alarms become collectable **in the same pass**. Measured on **one clock**
(`../gates/v0.9.2-phase-0.md` §4):

```
alarm rows                             4 -> 0
alarms with ne_id                      4 -> 0
feedback rows                          1 -> 1     (F44's fix holding — the control)
feedback_exclusion rows                2 -> 2
server bag rows                        4 -> 4
marked ids still resolvable to an NE   2 -> 0
```

So the reconciled count is recomputable **forever**, while *"was this mark about a member the
labeller could see?"* becomes **permanently unanswerable** one retention pass after the situation
closes. **This is F44's shape one level deeper: F44 deleted the label; this deletes the label's
interpretability.**

### 2.4 The fix

`feedback.excluded_reconciled_out_of_scope`, written at the verdict from the **same**
`Perimeter.hidden_member_ids` read that produces `scope_redacted_members` (DECISIONS #137), so the
two can never come from snapshots that disagree. `NULL` means unknown and means it forever; `0` is a
real and common answer. The corpus divides into **clean / checked / unknown**, reported separately
and never averaged.

**Nothing is backfilled**, and there is no exception. `alarm.ne_id` expires, and even where it
survives, the scope policy *as the operator experienced it* is not reconstructible from what the
policy document said — the gap between those two is exactly what the column exists to record.

### 2.5 What the fix does not do, stated plainly

**It does not stop a scoped editor from making a blind assertion. It makes the assertion legible.**
Preventing it would mean rejecting client ids, which would trade an evidence-quality problem for the
existence oracle F34 closed — a strictly worse exchange, refused in DECISIONS #131 and argued in
[`../architecture/EVIDENCE-BOUNDARY-0.9.2.md`](../architecture/EVIDENCE-BOUNDARY-0.9.2.md) §5.

### 2.6 The regression tests, and their recorded red

| Test | Demonstration |
|---|---|
| `test_a_mark_on_a_redacted_member_is_recorded_as_blind` | `M7` — the scope of the marking not recorded |
| `test_zero_blind_marks_is_a_real_answer_and_not_a_silence` | `M7` |
| `test_a_label_with_no_resolved_scope_records_unknown_not_zero` | `M8` — `0` fabricated in place of `NULL` |
| `test_v092_upgrade_reconciles_and_leaves_tier_three_null` | `M9` — the backfill widened to a tier-3 column |

---

## 3. What neither finding is

**Neither can be produced by a label written by the shipped UI**, which sends only ids it rendered.
The corpus's exposure is therefore an empirical question, and it is answered by measurement rather
than by assumption: on the fullest corpus this repository can construct, **zero** rows have a
reported count that disagrees with the reconciled one, and **zero** have `excluded_count >
member_count` (`../gates/v0.9.2-phase-0.md` §6).

**That is a fact about sequencing, not a property of the system.** The write path accepted a hostile
label from an ordinary `editor`, over HTTP, in one request. Nothing prevented it; nothing had yet
tried.

Four things neither finding is, each with its reason:

* **Not a disclosure.** The response is byte-identical and timing-identical whether a marked id names
  a real alarm, an alarm the principal cannot see, or nothing at all. Proved in v0.9.1 by
  `tests/test_partial_split.py` §4 and re-measured in v0.9.2 across four request shapes plus timing.
* **Not an availability issue.** The write is bounded at `MAX_CLIENT_MEMBERS = 512` per label, the
  bound is recorded on the row, and no path here raises. The added work is one set intersection over
  a bag already bounded by `MAX_CANDIDATES`.
* **Not a privilege escalation.** `feedback.write` is required and is not exceeded. An `editor` could
  already label any situation with one visible member; what changes is what the corpus records about
  that label.
* **Not a learned-state defect.** `learn.penalize` read tier 2 throughout. A 20-case parity test
  compares its matrices against a restatement of the v0.9.1 implementation.

---

## 4. Critical analysis

### 4.1 What the reconciled count now licenses, and what it still does not

**Licensed.** A consumer may treat `excluded_reconciled` as a statement about the network: it is
computed by the server, from a bag the client cannot influence, at the instant of the verdict, and
bounded at four layers.

**Not licensed, and this is where the next finding lives.**

1. **It does not mean the operator was right.** It means they marked members that existed in the bag.
   Whether those members truly belonged to another situation is the thing the corpus is evidence
   *for*; no column added here validates it.
2. **On a restricted scope, `m·(n − m)` is not a count of observable pairs.** §2.2 measured 9
   reported, 8 reconciled, and **2** joining two members the operator could actually see. The new
   column records the *marked* side; the remainder may also hold redacted members. The existing
   columns bound the observable count without a further column — with `n`, `m`, `h` and `b` the
   observable pairs lie in `[(m−b)·(n−m−(h−b)), (m−b)·(n−m)]`, which on the measured case is `[2, 2]`
   — but that is a bound a reader must compute, not a number the schema stores. An exact stored
   count is a ROADMAP line.
3. **A truncated report's reconciled count is a lower bound, not a count** (DECISIONS #135).
4. **Nothing here makes the corpus large enough to decide anything.**

### 4.2 The assertions that remain permanently uninterpretable, counted

Every label written before migration `0011` carries `excluded_reconciled_out_of_scope = NULL`, and
so does every label written through a path that resolves no scope. That population is **counted and
named in the report** (`unknown`) rather than assumed clean, because assuming would invent an
observation nobody made.

On this repository's own corpora the number is small — the fullest fixture has one such row — but the
number that matters is on a **deployed** appliance, and it is exactly the count of partial splits
recorded before the upgrade. The migration cannot reduce it and no later release can, which is the
strongest reason the column had to ship in a corrective release rather than waiting for v0.10.0: one
more release of labelling is one more release of permanently uninterpretable assertions.

### 4.3 Which client-controlled input I am least confident I have classified correctly

**`ScorerParamsIn`.** Every field of it is admin-supplied and each one decides what the correlator
links, so a number the client sends genuinely influences the system's behaviour — which is the
sentence F46 was about. I classified it as configuration rather than measurement
(`../architecture/EVIDENCE-BOUNDARY-0.9.2.md` §2.2): it does not *describe* the network, it is a
versioned, audited, rollback-able row, and it is `admin`-only.

I believe that is right and I am not certain it is complete. The uncomfortable case is the one where
a *measurement* is later expressed relative to a configuration — a rate normalised by a threshold, a
sufficiency floor stated in units the scorer parameters determine. At that point an admin's
configuration would be an input to a quantity about the evidence, through an indirection the boundary
document does not currently name. **If v0.10.0 expresses any floor in terms that depend on the active
scorer configuration, this classification needs revisiting before that floor is registered**, and
that sentence is here so it is found by the reader who needs it.

The runner-up is `remainder_together`. It is tier 1 — the operator's own claim about the remainder,
and legitimately so — but its *denominator* (`remainder_offered`) is the partial-split population,
which is now reconciled. So a rate over an affordance nobody was given is computed over a set whose
membership moved in this release. It reads 0/0 on every corpus that exists, which is why it is a note
and not a finding.

### 4.4 Will a future release need to reject client ids after all?

**I do not believe so, and I can say what would have to become true.**

The trade today is unambiguous. Rejection buys nothing that reconciliation does not already buy —
the reconciled count is already exact, already bounded, already the same value the learner uses — and
it costs an existence oracle: a scoped editor who cannot see alarm 4711 would learn whether it exists
by watching a status code. F34 closed that once, and `tests/test_partial_split.py` §4 keeps it
closed in status, body **and** timing.

Three things would have to become true together for the trade to change:

1. **A quantity that decides would have to depend on the reported set rather than the reconciled
   one** — something reconciliation cannot compute, such as *how many ids the client believed it was
   marking*, promoted into a threshold. I can construct no such quantity that is not better served by
   reporting the disagreement, which this release already does.
2. **The write volume of unreconcilable marks would have to become a storage problem.** It is bounded
   at 512 rows per label today, and a label is the rarest event in the system.
3. **The oracle would have to be closed by other means** — a response that is genuinely uniform
   across acceptance and rejection, which is a stronger property than the current one and is hard to
   hold under a rejection path that must decide *something*.

Absent all three, a rejection path would be a strict regression, and a future release that proposes
one should be asked to answer this section rather than to re-derive it.

### 4.5 A fourth note: the guard demonstrations found nothing, and that is itself a result worth doubting

Thirteen mandatory injections, thirteen caught, every control held. That is a good number and it is
**not** evidence that the suite is complete — it is evidence that the guards written *for this
release* catch the defects *this release imagined*. The competent-programmer hypothesis that
justifies fault-based testing is exactly that: real defects resemble small perturbations of correct
code. The defects it does not model are the ones that resemble a *design*, and F46 was one of those —
a defect nobody would have written as a mutation, because it was never a mutation. It was the
original.

`../gates/v0.9.2-guard-demonstrations.md` §Limitations is written in that voice, inheriting the
framing of [`../gates/v0.9.1-test-audit.md`](../gates/v0.9.1-test-audit.md), which closed 19 of 31
and was a better document for saying which 12 it missed.

### 4.6 The audit catalog was not opened, and that is a decision rather than an omission

The drift check reports through the operator-warning channel and writes **no audit row**, because
the catalog is frozen and this release adds no action to it (DECISIONS #138). The substance is
preserved — the disagreement is durable, because `dataset bias` re-derives it from the child tables
on every run rather than holding it in one process's memory — but a reader auditing this appliance
from the audit log alone will not see that a drift detection occurred.

That is a real gap and it is named here rather than in a footnote. The right repair is not a
one-off action for this check: it is an audit action for **system-detected data-integrity events as
a class**, which is a design decision a corrective release should not make alone.

---

## 5. What did not change

| Property | Evidence |
|---|---|
| `make eval` byte-identical | `c2e8a0ced29d9edf986279d41089ddb68e18da65a46bdc7e9f04811e8b9b6f26`, unchanged since v0.7.0 |
| `learn.penalize` byte-identical | `test_penalize_is_byte_identical_to_v091`, 20 cases |
| response status and body | `test_the_response_is_unchanged_in_status_and_body`, four request shapes |
| response timing | `test_the_oracle_stays_closed_in_timing` |
| the client's report stored verbatim | `test_a_mark_repeated_is_one_mark`, and the ghost still present in `feedback_exclusion` after the migration |
| no new route / capability / audit action / served path | `tests/test_declaration.py`, `tests/test_audit.py`, `tests/test_rbac.py` |
| no new runtime dependency | five, unchanged |
| F34–F45 regression tests | unedited and green |
