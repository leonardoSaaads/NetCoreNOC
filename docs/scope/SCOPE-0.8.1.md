# SCOPE — NetCoreNOC v0.8.1

**Theme: the dataset has a governed lifecycle.**

v0.8.0 captured the operator feedback correctly and measured it honestly. What it did not do is
govern what happens to that data afterwards. An independent audit found four defects that share one
root:

> The release designed a lifecycle for the rows it created and did not check the lifecycle the
> repository already had.

In a default deployment the consequence is that the release's own deliverable evaporates. The human
verdict — the single least reconstructible asset in the system — is deleted **seven days** after its
situation closes, by a maintenance loop that predates the feature and that nothing in v0.8.0's
documentation mentions.

This is a patch release in the v0.7.1 mould: a small, auditable diff that fixes real defects and
adds no capability. It **changes no schema, adds no migration, adds no route, and adds no
dependency.** The correlation path, the capture path, the bias report's content and the API contract
are untouched.

The runtime identity is unchanged: one Python 3.12 asyncio process, one SQLite (WAL) file, one
static UI of four files, environment variables only, no build step, **five runtime dependencies**
(unchanged), **eight migrations** (unchanged), and **no** `ui/` change of any kind.

All prior scope documents and their invariants still hold. On a conflict, this document wins on
*scope*, the build prompt wins on *process and quality*,
[`../security/threat-model.md`](../security/threat-model.md) wins on *security posture*, and
[`../architecture/MODULE-ARCHITECTURE.md`](../architecture/MODULE-ARCHITECTURE.md) wins on
*placement*.

**Delivery model (unchanged).** The repository is read-only to automation; the maintainer takes the
resulting archive and pushes it by hand. No step depends on pushing, on CI running, or on any
external account. Every gate is local and reproducible (`make qa`, `make eval`, `make bias-report`,
a locally built wheel).

---

## 1. In scope — exactly five workstreams, and nothing else

### 1. F44 — the operational prune deletes human labels

`store/retention.py::prune()` — code that predates the feature — deletes the `feedback` rows of
every situation closed or merged for longer than the **operational** retention, whose default is
**7.0 days**. `feedback_member` follows by `ON DELETE CASCADE`. The promoted `dataset_pair` rows
survive, because `dataset_pair` deliberately carries no foreign key to `alarm` or `situation`.

The failure is therefore **silent and asymmetric**: the corpus grows, the labels evaporate, and the
bias report shows a label count that only ever reflects the last seven days.

Reproduced in [`../gates/v0.8.1-phase-0.md`](../gates/v0.8.1-phase-0.md) §1:

```
feedback        1 -> 0        (the human verdict)
feedback_member 8 -> 0        (the bag, by ON DELETE CASCADE)
dataset_pair    [('dataset', 19)]   (the features SURVIVE)
situation       []
```

**The fix**: a label is not operational data and is not governed by the operational retention.
`feedback` leaves `prune()`'s deletion set.

**The complication reproduction found, which changes the fix.** `feedback.situation_id` is a
**restricting** foreign key (`0001_init.sql:89`, no `ON DELETE` action) under
`PRAGMA foreign_keys=ON`. Removing only the `DELETE FROM feedback` line does not produce a dangling
reference — SQLite refuses the delete, and every `maintenance` pass would raise. So `prune()` also
**retains the `situation` row that carries a label**, while still collecting that situation's
operational satellites (`situation_alarm`, `link`, and the cleared alarms behind them). One row per
label, bounded by the label count. See DECISIONS #109.

Severity: **high for data integrity, nil for security** — no confidentiality, audit-chain or
access-control consequence. Stated that way in the review and not inflated.

### 2. The three tiers get coherent semantics

v0.8.0 defines three tiers and enforces one. `training_days` is the cutoff of an explicit admin
reduction only; `audit_days` is validated, recorded and reported, and read by **no deletion path at
all** (Phase 0 §3). Two of the three are numbers that describe nothing.

**The rule this release adopts:**

| Tier | Meaning | Mechanism |
|---|---|---|
| **Sink** | pairs awaiting a verdict | deleted by the maintenance loop under the dual bound — **unchanged** |
| **Training** | *what a model may read* | **a query window. Nothing is deleted at this boundary.** |
| **Audit** | the outer bound of the data's life | the one background deletion that can reach a label |

The reasoning is worth stating because it is not obvious: a training-retention *deletion* destroys
evidence in order to express a modelling preference. Wanting to train on the last twelve months is a
statement about **selection**, and selection is a `WHERE` clause. Nothing has to die for a model to
ignore it, and keeping it means the choice is revisable — which matters enormously for a corpus that
four subsequent releases will disagree about how to use.

The audit tier then becomes the only background path that can delete a label, at a bound the
operator set, far outside the window anything trains on. That satisfies v0.8.0's directive 9 in
substance: the loop is not destroying labels on a schedule nobody chose; it is enforcing the outer
bound of a policy the operator configured, and every deletion is counted and reported.
DECISIONS #110.

The ordering invariant `sink < training ≤ audit` is unchanged, and so is its validation.

### 3. The retention policy is persisted

`engine.retention = RetentionPolicy()` in the constructor, and nothing reads a stored value at
startup. An admin sets a policy, the route answers `"saved"`, the change is audited as
`retention.change` — and the next restart silently returns the shipped defaults (Phase 0 §2).

The asymmetry is what makes it serious rather than annoying: **the destruction an admin asked for is
permanent, and the configuration they asked for is not.**

Persisted through `meta`, the mechanism this product already uses for `config.allowlist`,
`config.retention_days` and `community_hmac_key`, read at startup by the pattern in
`runner.py:146-148`. **No migration.** A malformed or partial stored value falls back to the shipped
defaults with an operator warning, in the same fail-safe shape governance policies already use — a
stored policy that cannot be parsed must never become a policy that deletes more than the default
would. DECISIONS #111.

### 4. Orphans and the coverage denominator

Two second-order consequences of (1) and (2). Both small; both would otherwise be discovered by
whoever writes v0.9.0, at a much worse moment.

**Orphaned features.** After F44's fix, labels and their promoted pairs die together at the audit
bound, so the routine orphan disappears. Two paths still create them: an explicit reduction of the
audit bound between a label and its features, and any pre-v0.8.1 database that already lost labels
to F44. **No cleanup job** — they are counted and reported. A corpus with orphans is not corrupt; it
is a corpus whose usable size is smaller than its row count, which is exactly what this report exists
to say out loud.

**The coverage denominator.** `bias.py:163` divides labelled situations by
`SELECT COUNT(*) FROM situation`, a table pruned on the operational schedule. Phase 0 §6 measured the
result at **300.0%**. Fixed so the denominator is a population that cannot shrink under its own
numerator, and the report says which population it is. DECISIONS #112.

### 5. The documents of record, and the security review

`MODULE-ARCHITECTURE.md` and `repo-map.md` do not mention `capture.py`, `labels.py`, `bias.py` or
`bias_report.py` — four modules of a major feature exist in the tree and not in the architecture
documents that `docs/` says are binding. Added, with layer assignments that agree with
`tests/test_layers.py` rather than restating it loosely, and with the `capture.py`/`labels.py` split
explained by **path** (per activation versus per verdict) and not by size.

`DESIGN.md` gains the v0.8.0 fact the audit found stated only in a commit message: **the sink's row
cap, not its age limit, is what governs at any realistic traffic rate.**

`docs/security/SECURITY-REVIEW-0.8.1.md` continues from **F44**, enumerating every path that can
delete a label after this release, and `threat-model.md` maps F44 to a control and a check.

---

## 2. Explicitly out of scope — deferred, with the reason

1. **Anything that trains.** Still v0.9.0. Still nothing here fits, splits, weights or predicts.
2. **Changing capture volume**, `MAX_CANDIDATES`, `MAX_LINKS_PER_ALARM`, `WINDOW_S`, or the sink's
   dual bound. The sink cap's real duration is uncomfortable and **honestly documented**; changing
   it is a design decision with data behind it, and the data is what v0.9.0 will have. This release
   deliberately fixes the *governance* of the data and does nothing about the *quantity*.
3. **A `dataset/` package.** Workstream 5 updates the architecture documents to describe what
   exists; reorganising four modules in a patch release trades a reviewable diff for tidiness.
4. **A UI for retention or the report.** Still CLI and the admin route.
5. **Changing the bias report's measurements**, except the one denominator defect and the orphan
   count in workstream 4.
6. **Sampling, compression, or archival export of the dataset.** ROADMAP lines.
7. **A cleanup job for orphaned pairs.** They are measured, not collected — deleting features whose
   label an operator destroyed is a second destruction nobody asked for.
8. **Recovering labels already lost to F44.** They cannot be recovered. `MIGRATION.md` says so
   plainly rather than implying an upgrade repairs anything.

---

## 3. Invariants this release does not touch

`make eval` byte-identical (`c2e8a0ce…`); `correlate.py`, `scoring.py`, `learn.py`, `receiver.py`,
`capture.py`'s write path and the engine's ingest path unchanged; the API contract unchanged; zero
new routes, capabilities, audit actions, dependencies, served paths or migrations; `DEBT_ALLOWLIST`
empty; `COHESION_EXEMPT` at one entry with `engine.py`'s ceiling unchanged at **580** — this release
does not raise it; no module over 400 lines.

---

## 4. What "done" means

Six gates, each with recorded evidence in `docs/gates/v0.8.1-phase-N.md`. Gate 5 additionally
requires an upgrade from a database written by real v0.8.0 code — no migration, identical schema,
identical row counts, `integrity_check` ok, the audit chain verifying to the same final hash, and any
pre-existing orphans **reported rather than silently collected**.
