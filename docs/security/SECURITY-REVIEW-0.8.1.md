# Security review — NetCoreNOC v0.8.1

Continues from **F43** (v0.8.0). One new finding, **F44**. Scope: the five workstreams of
[`../scope/SCOPE-0.8.1.md`](../scope/SCOPE-0.8.1.md) — the dataset's lifecycle, and nothing else.

**Summary for a reader in a hurry.** This release adds **no attack surface**: no route, no
capability, no audit action, no migration, no dependency, no served path, no schema change. Its one
finding is a **data-integrity** defect with no confidentiality, audit-chain or access-control
consequence, and the security-relevant work is the enumeration in §2 — *every path that can now
delete a human label, and what bounds each one.*

---

## 1. F44 — the operational prune deleted human labels

| | |
|---|---|
| **Severity** | **High for data integrity. Nil for security.** |
| **Confidentiality** | none — no data is disclosed, and nothing crosses a scope boundary |
| **Integrity of the audit chain** | none — `audit_log` is untouched; its own retention is a separate, admin-triggered path |
| **Access control** | none — no principal gains any capability, and the deletion was never attacker-triggerable |
| **Availability** | none |
| **Introduced** | v0.8.0, by omission — the deleting line predates it |
| **Fixed** | v0.8.1, `store/retention.py` |

Stated that way deliberately. This is the most consequential defect the project has shipped *for the
product's purpose*, and it is **not a vulnerability**. Calling it one would devalue the F-numbers
that are.

### The finding

`store/retention.py::prune()` deleted the `feedback` rows of every situation closed or merged longer
than the **operational** retention (`NETCORENOC_RETENTION_DAYS`, **default 7.0 days**).
`feedback_member` followed by `ON DELETE CASCADE`, with `PRAGMA foreign_keys=ON` set in
`store/lifecycle.py:30`.

Before v0.8.0 the line was **correct**: a `feedback` row was a transient learning signal, applied by
`learn.penalize()` at click time and then genuinely disposable. v0.8.0 made that row **the dataset's
label** and did not revisit the line that deleted it.

The failure is **silent and asymmetric**. `dataset_pair` deliberately carries no foreign key to
`alarm` or `situation` (DECISIONS #107), so the *features* survived while the *judgement* they
existed to justify disappeared. Nothing logged it, nothing counted it, and the bias report's label
count only ever reflected the last seven days — a number that looks plausible and is wrong.

### Reproduction

[`../gates/v0.8.1-phase-0.md`](../gates/v0.8.1-phase-0.md) §1, by driving the real engine:

```
BEFORE   feedback=1  feedback_member=8  dataset_pair=[('dataset', 19), ('sink', 9)]  situation=[(2,)]
AFTER    feedback=0  feedback_member=0  dataset_pair=[('dataset', 19), ('sink', 9)]  situation=[(0,)]
```

`IDLE_CLOSE_S` is 3600, so a label died roughly **seven days and one hour** after its situation's
last activity.

### The fix, and the constraint that shaped it

`feedback` leaves `prune()`'s deletion set. The **labelled `situation` row is retained with it** —
and only that row.

That second half is not defensive coding. `feedback.situation_id` is
`NOT NULL REFERENCES situation (id)` with **no `ON DELETE` action** (`0001_init.sql:89`), so under
`foreign_keys=ON` the "dangling reference by design" that the obvious fix assumes is one SQLite
**refuses**:

```
IntegrityError: FOREIGN KEY constraint failed
```

Removing only the `DELETE FROM feedback` line would have converted silent data loss into a
maintenance loop that raises on **every pass** — taking the learned-state flush, the gap recording
and the session purge down with it, which *would* have been an availability defect. The constraint
was found by reproduction, not by reading. DECISIONS #109.

The situation's `situation_alarm` and `link` rows are still collected, so retention is bounded by
the label count — the rarest event in the system — and the shell is collected by an ordinary later
pass once the audit sweep removes its label.

### Regression tests

`tests/test_dataset.py::test_f44_*`, four of them, **two proven to fail on the unmodified tree** by
stashing (`../gates/v0.8.1-phase-2.md`). The other two assert behaviour the fix must *preserve* —
that the operational data is still collected, and that an unlabelled situation still goes — because
a regression test that only proves the new behaviour would not catch the fix over-reaching.

`test_f44_repeated_prunes_do_not_raise_on_the_retained_situation` runs **four** consecutive sweeps:
it is the control for the `IntegrityError` above, and one pass would not have caught it.

---

## 2. Every path that can delete a human label, after this release

The security-relevant deliverable. There are **two**, and no others.

| # | Path | What bounds it | Who is attributed | What is recorded |
|---|---|---|---|---|
| 1 | **The audit sweep** — `capture.Capture.prune` → `store.prune_dataset_audit`, on the maintenance tick | `retention.audit_days` (default **730 days**), a value the operator set and the product validated. Cannot reach anything newer than the bound — asserted by `test_the_audit_sweep_deletes_outside_its_bound_and_nothing_newer`, which places two cohorts either side of it | `system` — no principal; it enforces a policy an operator configured | counted per row kind in `capture.audit_swept`, surfaced on `GET /api/dataset/retention` |
| 2 | **An explicit admin reduction** — `POST /api/dataset/retention` with `preview=False` | the operator's own new `audit_days`, **after** seeing `preview_retention`'s count. `preview` defaults to `True`, so the destructive branch cannot be reached by accident or by a client that forgot a field | the authenticated **admin** principal (admin-only route; admin is never scoped) | `retention.preview` and `retention.change` audit rows, the latter carrying before, after, and the impact that was shown. The audit row **precedes** the deletion, so a crash mid-delete leaves the intent on record |

**And nothing else.** Specifically:

* **`store.prune()` cannot** — F44's fix, with four regression tests.
* **The training tier cannot**, at any time, by anybody. It is a `WHERE` clause (DECISIONS #110).
  `test_four_passes_past_the_training_bound_delete_nothing` and
  `test_lowering_the_training_window_destroys_nothing` hold both halves — the background loop and
  the explicit reduction.
* **`store.prune_sink()` cannot** reach a promoted row or a label; every statement carries
  `lifecycle='sink'` and `test_neither_sink_bound_can_reach_a_promoted_row` is the standing control.
* **`store.prune_audit()`** — the audit-**log** deleter — does not touch `feedback`. Note the
  near-miss: the dataset sweep was first written under that exact name, and both are mixed into one
  `Store`, so it would have **silently shadowed audit-log retention**. `mypy --strict` refused it
  (`../gates/v0.8.1-phase-3.md` §S1) and it was renamed `prune_dataset_audit`. Worth recording: the
  type checker was the control that caught a change to the audit log's lifetime.

**Deletion order within the audit sweep** is pairs → observations → labels. A crash mid-sweep
therefore leaves *features without a label* — a state the bias report already measures and names as
an orphan — rather than a label whose evidence has silently gone.

---

## 3. The retention persistence, and its fail-safe

Stored as **one `meta` value**, `config.dataset_retention`, written in the **same transaction** as
the `retention.change` audit row and the deletion, so the record and the policy cannot disagree.
`meta` is where this product has always kept operator configuration (`config.allowlist`,
`config.retention_days`, `community_hmac_key`). **No migration; no schema change.**

Read at `maintenance.py::_capture_run`, the documented configuration reload point.

**The fail-safe, which is the security-relevant part.** A stored value that cannot be read —
malformed JSON, a missing field, a wrong type, or a stored **ordering violation** — falls back to
the **shipped default as a whole**, and never to a partial reconstruction of the good fields.

The reasoning is the same shape as the governance policies': *a policy that cannot be parsed must
never become a policy that deletes more than the default would.* Reconstructing three valid fields
around one bad one could synthesise a policy **no operator ever set** — and, because the tiers are
ordered, potentially one that destroys more than either the stored or the shipped value. So the unit
of parsing is the unit of policy. `validate()` runs on the *stored* value rather than being trusted
from the write path, because a `meta` row is durable state that a restore or a hand-edit could have
left inconsistent; `test_a_corrupt_stored_policy_falls_back_to_the_shipped_default_and_warns`
includes exactly that case (valid JSON, valid types, illegal ordering).

The operator is warned through `store.integrity_warnings` — the existing channel for damaged durable
state — added **once** and removed when a valid policy is stored, so a maintenance pass every five
seconds cannot flood `/api/stats` and a repaired policy leaves no stale complaint.

---

## 4. No new surface

Verified rather than asserted (`../gates/v0.8.1-phase-5.md`):

| Claim | Evidence |
|---|---|
| no new route | route tables and the declaration gate unchanged; `tests/test_declaration.py` unedited since v0.8.0 |
| no new capability, no new audit action | `rbac/tables.py` and the audit action set unchanged |
| no new dependency | `pyproject.toml` — **five** runtime dependencies |
| no new migration, no schema change | `PRAGMA user_version` = **8**; schema SHA-256 identical across the upgrade |
| no new served path | the static-asset allowlist unchanged; the wheel serves the same five paths |
| API contract unchanged | two **additive** response fields (`bound`, `training_deletes`); nothing removed or retyped |
| capture and correlation untouched | `git diff` over `correlate.py`, `scoring.py`, `learn.py`, `receiver.py`, `events.py` is **empty**; `engine.py` is one line |
| `make eval` byte-identical | `c2e8a0ced29d9edf986279d41089ddb68e18da65a46bdc7e9f04811e8b9b6f26` |

`bandit` and `pip-audit` clean.

---

## 5. Critical analysis

**(a) This release fixes the governance of the data and does nothing about how much of it there is.**
The sink's row cap, not its age limit, is what governs at any realistic traffic rate: at the eval
corpus's ~62 pair rows per trap, the 2 000 000-row default is exhausted in **~3.7 days at 0.1
traps/s and ~9 hours at 1 trap/s**. An operator reading `sink_days: 21` and concluding they have
three weeks to label something is wrong. v0.8.1 documents this in `DESIGN.md` beside the tiers —
where the audit found it stated only in a commit message — and **changes nothing**, because changing
it is a design decision with data behind it and the data is what v0.9.0 will have. It remains the
largest open question about whether the dataset will contain what v0.9.0 needs, and it is now a
ROADMAP entry rather than a thing nobody wrote down.

**(b) v0.8.0's claim that the dataset grows with labels and not with traffic is true per situation
and false per row, and v0.8.0 stated the row version.** Executed on `olt_storm.json`, a **single**
verdict promoted **45 050 pairs — the entire sink** (Phase 0 §4). Promotion is per situation, so one
label promotes every evaluated pair of that situation, and a situation's evaluated-pair count grows
with its member count, which grows with traffic during a storm. The corpus is bounded by **labels ×
the pairs evaluated within the labelled situation**. The promotion rule itself is not leaky — all
45 050 promoted pairs had both ends inside the labelled bag — so this is a **claim** defect, not a
code defect, and it is corrected in place in `DESIGN.md` and `SCOPE-0.8.0.md` rather than quietly
restated. Its practical consequence is that one storm label can dominate a training set; weighting,
capping promotion per label, and sampling within a bag are all modelling decisions and belong with
the release that trains.

**(c) How a defect of this size shipped past a release that was otherwise careful — and it is a
review question, not a coding one.** v0.8.0 designed a lifecycle for the rows it *created*
(`capture_run`, `dataset_observation`, `dataset_pair`, `feedback_member`) and audited it thoroughly:
a dual bound, a preview before any destructive change, a dedicated test that neither sink bound can
reach a promoted row, and a directive that the maintenance loop must never silently destroy labels.
Every one of those controls was correct, and every one of them was about **rows v0.8.0 wrote**. The
label it *depended* on — `feedback`, a table from `0001_init.sql` — already had a lifecycle, applied
by code four releases older, and nobody looked at it. `MIGRATION.md` even stated the opposite
outright: *"The background maintenance loop **never** deletes labelled data."*

The checklist item this yields is worth more than the fix:

> **When a release starts depending on data another release wrote, enumerate every existing path
> that deletes or mutates that data, before designing the new one.** `grep` for the table name in
> every `DELETE`, `UPDATE` and `ON DELETE` clause in the tree. A feature's own lifecycle being
> well-designed says nothing about the lifecycle it inherited.

Two secondary observations in the same spirit. First, the directive that produced the inert tiers
was **mine and under-specified**: "the loop must never silently destroy labels" left no way to have
a middle tier that meant anything, and the build obeyed it correctly by making two of three tiers
describe nothing. A directive that forces a correct implementation into a useless shape is a
specification defect, and #110 records the resolution rather than blaming the build. Second, the
defect was reachable by the **default** configuration — no operator action, no unusual deployment —
which is the category of defect that testing on a fresh database never finds, because a fresh
database is younger than seven days.

**(d) Residuals, recorded and not fixed.** `Capture.warnings()` is built, tested, and never called
by `runner.py`'s warnings lambda, so a degraded capture is counted, logged and invisible on
`/api/stats`. Found while wiring this release's fail-safe warning and deliberately left: it is not on
the label path, and this release's value is the size of its diff. It is a ROADMAP line, and it is
named here so it is not rediscovered as a surprise.

---

## 6. Threat-model impact

`threat-model.md` gains F44 mapped to a control and a check. No trust boundary moves, no asset
changes classification, and no mitigation is withdrawn. The dataset remains a **scope bypass by
construction** — it contains every NE, entity and raw varbind, ungoverned — and no route below
`admin` may reach any of it, unchanged from v0.8.0.
