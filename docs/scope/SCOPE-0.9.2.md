# SCOPE — v0.9.2

**The evidence boundary: a number that describes the evidence is derived by the server; a number
that describes the client may be derived from the client; and the place where the two meet is a
named, stored, auditable act rather than an arithmetic accident.**

A **corrective** release. It builds no new capability, adds no route, adds no model, and moves no
product forward. It exists because the quantity v0.10.0 is most likely to promote into a
pre-registered sufficiency floor — the count of asserted negative pairs — is presently computed from
unvalidated client input, and a floor computed from something the subject controls is not a floor.

Binding authorities, in the order they win: this document on **scope**;
[`../architecture/MODULE-ARCHITECTURE.md`](../architecture/MODULE-ARCHITECTURE.md) on **where code
goes**; [`../architecture/ROADMAP-0.8-TO-0.13.md`](../architecture/ROADMAP-0.8-TO-0.13.md) on
**sequence**; [`../security/threat-model.md`](../security/threat-model.md) on **security posture**;
[`../architecture/EVIDENCE-BOUNDARY-0.9.2.md`](../architecture/EVIDENCE-BOUNDARY-0.9.2.md) on **which
tier a quantity belongs to**; the build prompt on process and quality.

---

## 0. Why this release exists

`labels.py` writes `excluded_count = len(exclusion.alarm_ids) or None` — the raw length of a
client-supplied list, never intersected with the server's own bag — and three consumers multiply it
by `member_count − excluded_count`. Nothing anywhere checks `m ≤ n`.

Measured over HTTP as an ordinary `editor` in
[`../gates/v0.9.2-phase-0.md`](../gates/v0.9.2-phase-0.md) §1:

```
8 honest labels (9-member bags, 2 marked each)      total =     112   (correct: 8 x 2 x 7)
+ ONE label: bag of 4, 600 ids sent (truncated 512) total = -259,984   delta = -260,096
+ ONE label: bag of 60, 30 GHOST ids marked         total = -259,084   delta =    +900
```

**The third line is the release.** `−260 096` is loud, and any `>= N` floor simply fails on it.
`+900` is positive, plausible, of the right order of magnitude, and composed of **zero true
assertions**: the same label read through the path `learn.penalize` actually uses resolves **0**
marks and moves not one matrix cell.

Two findings, and one root cause. `feedback_member` carries a `source` column so that trusted and
untrusted rows stay distinguishable downstream. `feedback_exclusion` carries none — it did not need
one — and the denormalized counter on the label row then inherited the client's trust **without ever
saying so**.

The release succeeds when a reader can say: *the chain that produces that quantity is now under
control, and I can show you the tests that would notice if it stopped being.*

---

## 1. In scope — exactly six workstreams

### W1 — The boundary, written before the code

[`../architecture/EVIDENCE-BOUNDARY-0.9.2.md`](../architecture/EVIDENCE-BOUNDARY-0.9.2.md): the
three-tier model, **every** client-controlled write-path input classified by it, what each consumer
in the tree is entitled to read, and both findings with their severity stated honestly — high for
evidence integrity, nil for confidentiality, nil for availability, and explicitly not a privilege
escalation.

Written in Phase 1, before any implementation, because the code is easy once the words are right.

### W2 — Reconciliation (F46)

Migration `0011`, additive and forward-only, adding to the label row:

* **the reconciled count** — `|reported ∩ the server's own bag|`, computed server-side at verdict
  time. Every quantity about the evidence reads this;
* **its provenance** — whether it was written live by v0.9.2+ or derived by the migration from
  stored evidence. Two different acts; a column that cannot tell them apart will be misread.

`excluded_count` keeps its **exact** present meaning and its exact present bytes, and is
**re-documented, not changed**, as tier 1: *what the client reported; a measurement of the client,
never of the evidence.*

The backfill is permitted and bounded: `feedback_exclusion ∩ feedback_member(source='server')` is
stored evidence that no retention path removes, so recomputing it is **derivation**. Every
backfilled row is marked as backfilled.

The fixed-fixture identity is replaced by a **property test** over generated `(n, m)` including
`m > n`, `m = 0`, `m = n`, `n = 0` and `n = 1`, asserting `0 ≤ m ≤ n`, component non-negativity, and
the sum — and saying in its own docstring that the sum alone is an algebraic identity in `m` and `n`
that cannot fail. `0010`'s comment, which names the wrong test file, is corrected.

### W3 — Interpretability (F47)

At the moment of the verdict, using **the same resolved scope object** the perimeter already
produced for the 404 decision, record for the reconciled marks: **how many were about members
outside the labeller's visible set.**

Zero is a real and common answer and is distinguishable from *not recorded*. Rows written before
`0011` are `NULL`, and `NULL` means unknown **forever** — never `0`, because that would be
fabrication.

The corpus then divides into three populations a reader can name — **clean**, **checked**,
**unknown** — reported separately and **never averaged**, exactly as `acquisition_channel` is
(DECISIONS #126).

### W4 — The consumers, and one more of the same class

`bias.py` (corpus-wide and per-channel), `shadow_report.py`, and the rendered reports, which must
make the tier visible to a reader who is not holding the boundary document. **The disagreement count
— how many rows have `excluded_count ≠ reconciled` — is the first number the report prints**,
because it is what tells an operator whether their corpus was ever written by something other than
the shipped UI.

`store/dataset.py::dataset_stats` splits `feedback_member` by `source`: the row cost is real either
way, but the composition is what an operator sizing retention needs, and a client can inflate one
half of it by up to 512 rows per label.

### W5 — The demonstrated-guard discipline

> **A guard nobody has watched fail is not a guard. Green is not evidence; green *after a recorded
> red* is evidence.**

`docs/gates/v0.9.2-guard-demonstrations.md`: for **every**
guard this release ships — the guard named with its test id, the exact defect injected as a diff
against the **corrected** tree, the command, the verbatim **red**, the verbatim **green**, and the
**control that had to pass in both runs**. A guard with no recorded red blocks the gate; a guard
whose control was absent blocks the gate.

Plus a mutation ledger across the release's own surface, with **every** survivor listed by name and
given a disposition, and a limitations section in the voice of
[`../gates/v0.9.1-test-audit.md`](../gates/v0.9.1-test-audit.md) — which closed 19 of 31 and was a
better document for saying which 12 it missed.

And two guards whose scope is a string, both bounded to tests and guards with **zero production
changes**: the perimeter's fail-open branch, which has no test and whose inversion leaves the suite
960/960 green; and `tests/test_structure.py::_SKIP_DIRS`, which excludes `.venv` **literally**, so a
virtualenv under any other name puts every third-party `README.md` through the broken-link checker.

### W6 — The security review, and what v0.10.0 inherits

`docs/security/SECURITY-REVIEW-0.9.2.md`, continuing from
**F46**: both findings issued properly, with the reproduction, the measured impact, the
exploitability assessment, the fix, and the regression tests that fail on the unmodified tree —
named, with their recorded red referenced from the guard-demonstration gate. Then at least four
honest critical notes.

`HONEST-JUDGE-0.10-DRAFT.md` gains an **inheritance note**: the asserted-negative quantity is now
server-derived, and which of the three populations a floor could honestly be expressed over. It adds
nothing to v0.10.0's scope and **chooses no floor** — moving a threshold after seeing data is what
pre-registration exists to prevent, and choosing one before v0.10.0's plan exists is the same error
wearing a different hat.

---

## 2. The intentional behaviour changes, enumerated

**Four.** Any change not on this list is a defect in this release's work.

1. **`make bias-report` prints new lines and new numbers.** The disagreement count first, then the
   reconciled asserted-negative total beside the client-reported marks, then the three scope
   populations. On any corpus this repository can construct, the reconciled total **equals** the
   previous total and the disagreement count is **0** — so the numbers do not move; the report says
   more about them. The frozen fixture
   [`../../tests/fixtures/bias-report.txt`](../../tests/fixtures/bias-report.txt) is re-frozen, by
   hand, having read the diff.
2. **`make shadow-report` prints the reconciled asserted-negative total** in place of the
   client-derived one, under a label that says which it is. Same equality on every constructible
   corpus.
3. **`make dataset-stats` splits `feedback_member` into its two sources** instead of printing one
   sum. The sum is still printed.
4. **`make migrate` on an existing database writes one new column's worth of derived data**: the
   reconciled count on every partial split that already carries an exclusion, marked as
   backfilled. Nothing from W3 is seeded, and no grouping, verdict or learned state changes.

**Not on the list, and asserted by test:** the HTTP surface. Status, body **and timing** are
unchanged for every request, including the oracle probes. `learn.penalize` is byte-identical.
`make eval` is byte-identical. The UI does not change.

---

## 3. Explicitly out of scope — deferred, with the reasoning

Each becomes a line in [`../ROADMAP.md`](../ROADMAP.md).

* **`client_diverged` compares *ordered* digests**, so a client that sorts its bag differently reads
  as diverged. Legitimate as a measurement of the client — which is what it is — and imprecise only
  as a measurement of staleness, which is not what it claims to be. Not a defect this release
  repairs.
* **`shadow_skew_rows` selects `served_*` and compares by score**, so it is blind to a divergence
  that does not move the score, and its correctness rests on a column-aliasing convention.
* **`ROUTE_SCOPE` is descriptive rather than injecting** — a ROADMAP line since v0.7.2.
* **`make security` fails on the environment's bundled `pip`**, which is an environment fact.
* **An exact stored count of *unobservable asserted pairs***, covering the remainder side as well as
  the marked side. [`../architecture/EVIDENCE-BOUNDARY-0.9.2.md`](../architecture/EVIDENCE-BOUNDARY-0.9.2.md)
  §10 shows the existing columns bound it — exactly, in the measured case — so a second stored
  column would be a convenience with a maintenance cost, and ambiguity about scope resolves to a
  ROADMAP line.
* **The editor tier's structural protection.** `scorer.write` demoted from `admin` refuses at
  *import*; `label.write` demoted from `editor` to `viewer` fails exactly one test out of 960. A
  candidate invariant would give the editor tier the same structural protection. Assessed in W5; if
  it costs more than an import-time assertion it stays a ROADMAP line rather than growing this
  release.
* **Anything the repair reveals that is a refactor.** If a bug is found, it is a ROADMAP line and a
  finding number, not a fix inside a corrective release. This rule has held for nine releases.

---

## 4. What Gate 0 measured that bounds this release's claims

* **The repair is prospective, not retrospective.** On every corpus this repository can construct,
  **0** rows disagree and **0** rows have `excluded_count > member_count`. The backfill will correct
  nothing; it will record that nothing needed correcting. That is a fact about **sequencing** — the
  shipped UI is the only thing that has ever written into one of these corpora — and not a property
  of the system.
* **Non-negativity is necessary and not sufficient.** At `n = 60, m = 30` every component of the
  partition is `≥ 0` and the identity closes, while the label asserts nothing at all. Only the
  intersection discriminates that case. The property test is therefore a guard on the *reconciled*
  quantity, and its docstring says so.
* **The two repairs have different availability profiles.** The reconciled count is recomputable
  forever; *"could the labeller see this member?"* is permanently unanswerable one retention pass
  after the situation closes. Both are therefore computed eagerly and stored.
* **SQLite will hold the invariant.** `ALTER TABLE ... ADD COLUMN` carries an enforced `CHECK`, on
  `INSERT` and `UPDATE` alike, and it is **not retroactive** — so the constraint bounds the future
  and the drift check bounds the past, and both are required.
* **Coverage floor is 96.14 %**, measured on the unmodified tree under this environment's toolchain,
  not the 96.11 % the brief states. Holding the lower number would permit a real regression.

---

## 5. Hard constraints

* **Zero new runtime dependencies.** Five, unchanged.
* **Exactly one migration**, `0011`, additive and forward-only. No existing migration is edited.
* **No column is repurposed.** Columns are added; a column whose semantics differ by release makes
  every historical row ambiguous.
* **No rejection of client input anywhere.** Status, body and timing unchanged for every request.
  Reconciliation is an additional quantity, never a replacement and never a filter on what is
  stored.
* **No new route, no new capability, no new audit action, no UI change.**
* **`make eval` byte-identical.** `correlate.py`, `receiver.py`, `learn.py`, `scoring.py`,
  `challenger.py`, `rbac/`, `shaping/` and `engine.py` are not touched.
* **No module over 400 lines.** `training.py` and `shadow_report.py` are at exactly 400 — split
  properly and record the split in an ADR; never raise a guard to fit a corrective release.
  `DEBT_ALLOWLIST` stays empty; `COHESION_EXEMPT` stays at one entry and at 580.
* **Derivation is permitted; fabrication is not.** A quantity whose input is gone is `NULL`,
  counted and reported as unknown, never assumed zero.
* **Every guard ships with a recorded red beside its green, and a named control.** A guard that
  cannot be demonstrated is recorded as a limitation, not claimed.
