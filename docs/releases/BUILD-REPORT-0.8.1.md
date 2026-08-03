# Build report — NetCoreNOC v0.8.1

**Theme: the dataset has a governed lifecycle.**

v0.8.0 captured the operator feedback correctly and measured it honestly. It did not govern what
happened to that data afterwards, and four defects shared one root: **the release designed a
lifecycle for the rows it created and did not check the lifecycle the repository already had.**

In a default deployment the consequence was that the release's own deliverable evaporated — the
human verdict, the least reconstructible asset in the system, deleted seven days after its situation
closed by a maintenance loop that predates the feature and that nothing in v0.8.0's documentation
mentioned.

A patch release in the v0.7.1 mould: **no schema change, no migration, no new route, no new
capability, no new dependency.**

---

## What shipped

| Workstream | Outcome |
|---|---|
| **F44** — the operational prune deleted human labels | fixed; four regression tests, two proven to fail on the unmodified tree |
| **The retention policy is persisted** | one `meta` value, written in the audited transaction, read at the documented reload point; fail-safe on a corrupt value |
| **The three tiers get coherent semantics** | sink deletes, **training selects**, audit deletes — and the audit tier is now the only background path that can reach a label |
| **Orphans and the coverage denominator** | both measured; the rate can no longer exceed 100% |
| **The documents of record** | `MODULE-ARCHITECTURE.md` and `repo-map.md` catch up with four modules; the security review continues from F44 |

Decisions **#109–#113**. Findings: **F44**.

## The numbers

| | v0.8.0 | v0.8.1 |
|---|---|---|
| tests | 837 | **855** |
| coverage | 96% | **96%** |
| runtime dependencies | 5 | **5** |
| migrations | 8 | **8** |
| routes | unchanged | **unchanged** |
| `eval` output hash | `c2e8a0ce…` | **`c2e8a0ce…` (byte-identical)** |
| rows captured per trap | — | **identical on all four fixtures** |
| `engine.py` | 580 (ceiling 580) | **580 (ceiling 580, not raised)** |
| `DEBT_ALLOWLIST` | empty | **empty** |
| `COHESION_EXEMPT` | 1 entry | **1 entry** |

## F44, and the constraint that shaped its fix

`store/retention.py::prune()` deleted `feedback` for every situation closed longer than the
**operational** retention (default **7.0 days**), with `feedback_member` following by
`ON DELETE CASCADE`. The `dataset_pair` features survived, because they carry no foreign key to
`situation` — so the corpus grew and its labels evaporated, silently.

Before v0.8.0 the line was right: feedback was a transient learning signal, applied at click time and
then disposable. v0.8.0 made that row the dataset's **label** and did not revisit it.

**The fix is not the one the brief prescribed, and reproduction is why.** The brief expected
`feedback.situation_id` to become a dangling reference. It cannot: the column is
`NOT NULL REFERENCES situation (id)` with no `ON DELETE` action, under `PRAGMA foreign_keys=ON`.
Removing only the `DELETE FROM feedback` line makes `DELETE FROM situation` raise
`IntegrityError` on every subsequent sweep — converting silent data loss into a maintenance loop
that dies, taking the learned-state flush and session purge with it.

So the labelled `situation` **row** is retained too — and only that row; its `situation_alarm` and
`link` rows are still collected, and the shell is collected by an ordinary later pass once the audit
sweep removes its label. Bounded by the label count. DECISIONS #109.

This is the release's clearest argument for its own process: **a fix written against a description
rather than a reproduction is a fix you cannot size.**

## The tier semantics, and why selection beats deletion

v0.8.0 defined three tiers and enforced one. `training_days` was only the cutoff of an explicit admin
reduction; `audit_days` was validated, recorded, reported — and read by **no deletion path at all**.
Two of three described nothing.

| Tier | Meaning | Mechanism |
|---|---|---|
| sink | pairs awaiting a verdict | **deletes**, dual bound — unchanged |
| training | what a model may *read* | **selects** — a `WHERE` clause. Nothing dies here. |
| audit | the outer bound of the data's life | **deletes** — the only background path that may reach a label |

The reasoning is worth stating because it is not obvious: **a training-retention *deletion* destroys
evidence in order to express a modelling preference.** Wanting to train on the last twelve months is
a statement about *selection*, and selection is a `WHERE` clause. Nothing has to die for a model to
ignore it — and keeping it means the choice stays **revisable**, which matters enormously for a
corpus that v0.9.0 through v0.13.0 will disagree about how to use. A `DELETE` forecloses a decision
those releases have not made yet.

v0.8.0's directive that the loop must never *silently* destroy labels is satisfied rather than
repealed: the audit sweep enforces a bound the operator configured, every deletion is counted, and
the count is reported. That directive was mine and it was **under-specified** — it left no way to
have a middle tier that meant anything, and the build obeyed it correctly by making two tiers inert.
DECISIONS #110 records the resolution rather than blaming the build.

## The persistence

`engine.retention = RetentionPolicy()` and nothing read a stored value at startup, so a policy an
admin set — audited, answered `"saved"` — reverted to the shipped defaults on the next restart. The
asymmetry is what made it serious: **the destruction an admin asked for was permanent and the
configuration they asked for was not.**

One `meta` value (`config.dataset_retention`), not four keys, because the four values are **one
policy with an invariant between them**: a per-field fallback could synthesise a policy no operator
ever set. Written in the same transaction as the audit row and the deletion; read in
`_capture_run`, the documented reload point, which costs `engine.py` zero lines and means a change
written by one process is picked up by another. **No migration.** DECISIONS #111.

## The two report figures

- **Orphaned promoted pairs** — promoted pairs whose label no longer exists. Counted, **never
  collected**: a corpus with orphans is not corrupt, it is one whose *usable* size is smaller than
  its row count, and deleting features whose label an operator destroyed would be a second
  destruction nobody asked for.
- **The coverage denominator** — was `COUNT(*) FROM situation`, a table the operational prune
  collects while labels outlive it; measured at **300.0%**. Now the population the database has
  evidence of, so the numerator is a subset **by construction** and no clamp is needed. The report
  names the population. DECISIONS #112.

The report fixture was re-frozen: three figures and three paragraphs added, one label renamed, and
**no existing measurement changed value**.

## The architecture documents caught up

`capture.py`, `labels.py`, `bias.py` and `bias_report.py` — four modules of a major feature — existed
in the tree and not in `MODULE-ARCHITECTURE.md` or `repo-map.md`, which `docs/` calls binding. Added,
with layer assignments that **agree with `tests/test_layers.py`** rather than restating it loosely,
and with the `capture.py`/`labels.py` split explained by **path** (per activation versus per verdict)
and not by size.

`DESIGN.md` also gains the v0.8.0 fact the audit found stated only in a commit message: **the sink's
row cap, not its age limit, is what governs** at any realistic traffic rate.

## One structural change

`RetentionPolicy` moved to `retention_policy.py` (DECISIONS #113). `capture.py` was at **374 of its
400-line budget** and this release legitimately needed ~44 more lines there. The alternatives were
trimming the reasoning out of docstrings on a data-destroying policy — which DECISIONS #108 rejected
as *"makes the code worse to satisfy a number"* — putting `capture.py` on `DEBT_ALLOWLIST`, which
must stay empty, or raising a second ceiling in two releases. `capture.py` re-exports every name, so
**no import site anywhere else in the tree changed**.

## Honest caveats

1. **This release fixes the governance of the data and does nothing about how much of it there is.**
   At ~62 pair rows per trap the 2 000 000-row sink cap is exhausted in ~9 hours at 1 trap/s, so most
   deployments have hours of labelling window, not the 21 days `sink_days` advertises. Documented
   honestly, deliberately unchanged: changing it is a design decision with data behind it, and the
   data is what v0.9.0 will have. It remains the largest open question about whether the dataset
   will contain what v0.9.0 needs.

2. **One label promotes an entire storm's sink.** 45 050 rows from one verdict on `olt_storm.json`.
   The corpus is bounded by **labels × the pairs evaluated within the labelled situation** — not by
   labels, which is what v0.8.0 claimed. The promotion rule is not leaky (all 45 050 had both ends
   inside the bag), so this is a claim defect, corrected in place in `DESIGN.md` and
   `SCOPE-0.8.0.md`. Its practical consequence — one storm label can dominate a training set — is a
   modelling problem and belongs with the release that trains.

3. **How this shipped past a release that was otherwise careful.** v0.8.0 audited the lifecycle of
   the rows it *wrote* thoroughly: a dual bound, a preview before any destructive change, a test
   that neither sink bound can reach a promoted row. Every one of those controls was correct and
   every one was about rows v0.8.0 created. The label it *depended* on lived in a table from
   `0001_init.sql` with a lifecycle applied by code four releases older, and nobody looked. The
   checklist item is worth more than the fix: **when a release starts depending on data another
   release wrote, enumerate every existing path that deletes or mutates that data before designing
   the new one.** `grep` the table name in every `DELETE`, `UPDATE` and `ON DELETE` clause in the
   tree.

4. **`Capture.warnings()` is still never surfaced.** Built and tested, but `runner.py` does not call
   it, so a degraded capture is invisible on `/api/stats`. Found while wiring this release's own
   fail-safe warning and deliberately left as a ROADMAP line: it is not on the label path, and this
   release's value is the size of its diff.

5. **The audit sweep now deletes where v0.8.0's loop did not.** With the 730-day default this
   changes nothing for two years, but an operator who sets a short `audit_days` will find the loop
   enforcing it. That is the point of the tier, it is stated plainly in `MIGRATION.md`, and every
   deletion is counted and reported — but it is a new behaviour and it is named as one.

6. **A near-miss worth recording.** The dataset sweep was first written as `prune_audit` — already
   the name of the audit-**log** deleter, and both are mixed into one `Store`. It would have
   silently shadowed audit-log retention. `mypy --strict` refused it; no test would have caught it.

## Gates

| Gate | Evidence |
|---|---|
| 0 — reproduction | [`../gates/v0.8.1-phase-0.md`](../gates/v0.8.1-phase-0.md) |
| 1 — decisions and scope | [`../gates/v0.8.1-phase-1.md`](../gates/v0.8.1-phase-1.md) |
| 2 — F44 | [`../gates/v0.8.1-phase-2.md`](../gates/v0.8.1-phase-2.md) |
| 3 — tiers and persistence | [`../gates/v0.8.1-phase-3.md`](../gates/v0.8.1-phase-3.md) |
| 4 — orphans and the denominator | [`../gates/v0.8.1-phase-4.md`](../gates/v0.8.1-phase-4.md) |
| 5 — verification | [`../gates/v0.8.1-phase-5.md`](../gates/v0.8.1-phase-5.md) |
| 6 — review and release | [`../gates/v0.8.1-phase-6.md`](../gates/v0.8.1-phase-6.md) |

Security review: [`../security/SECURITY-REVIEW-0.8.1.md`](../security/SECURITY-REVIEW-0.8.1.md).
