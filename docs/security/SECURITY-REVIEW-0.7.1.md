# Security review — NetCoreNOC v0.7.1

**Scope of this review: the write perimeter.** Six confirmed defects, F34–F39, continuing the
F1–F33 series. Every one was reproduced by executed proof-of-concept against the v0.7.0 tree and
turned into a regression test that **fails on the unmodified tree** before any fix was written.

This is a security patch release. It ships no new capability, no new route, no new configurability
and no restructuring. `PERMISSIONS` (28), `ROUTE_PERMISSIONS` (39), `PUBLIC_ROUTES` (1),
`AUDITED_DENIED_PERMISSIONS` (14) and `audit.ACTIONS` (30) are unchanged. Runtime dependencies stay
at five. `make eval` is byte-identical to v0.7.0.

---

## 1. The class, not the instances

**v0.7.0 designed a *read* projection and called it a perimeter.**

Its own review said so, under F32: scoping is enforced by "one filter applied to every NE-bearing
read (list, detail, graph, timeline, entities, stats, SSE)". That sentence is true. It is also the
defect, and every one of the six findings below is a consequence of it:

| | The read side (v0.7.0, correct) | The write side (v0.7.0, absent) |
|---|---|---|
| **Enforcement** | `scope_for()` on every NE-bearing read | **no scope resolution at all** on the three `editor` write routes — **F34** |
| **Resolver inputs** | resolved per request from the live policy | one input, the operator label, was **writable by `editor`** — **F35** |
| **Effect bound** | reads are idempotent by nature | feedback was unbounded, non-idempotent, and moved **global** learned state — **F36** |
| **Target validation** | a read of a missing NE is a 404 | a write to a missing label target was a **200 that persisted** — **F37** |
| **Aggregates** | `scoped_stats` filters in SQL | list endpoints truncated **before** filtering — **F38** |
| **Durability** | reads commit nothing | a failed write's mutation was committed, unaudited, **by an unrelated caller** — **F39** |

The generalisation v0.7.0 lacked, and the sentence this release adds to the threat model:

> **Authorization never reads data the constrained party can write, and a write is inside the
> perimeter or it is a defect.**

### The perimeter is now complete, by construction

Of the 19 non-`GET` routes in `ROUTE_PERMISSIONS`, **5** require a capability below `admin`:

```
POST /api/labels                    -> label.write     (editor)  ← NE-bearing, now scoped
POST /api/situations/{sid}/close    -> situation.close (editor)  ← NE-bearing, now scoped
POST /api/situations/{sid}/feedback -> feedback.write  (editor)  ← NE-bearing, now scoped
POST /api/logout                    -> self.read       (viewer)  ← own session, no NE
POST /api/password                  -> self.read       (viewer)  ← own account, no NE
```

Every other mutating route is admin-only, and **admin is never scoped** (DECISIONS #58). The
enumeration is not a claim in this document — it is
`test_f34_every_mutating_route_below_admin_resolves_scope`, **generated from `ROUTE_PERMISSIONS`**,
with a two-route exemption list that is explicit and justified in the test itself. A route added in
any future release is not on that list, so it fails CI until it is inside the perimeter or is
consciously exempted with a reason. **That test is worth more than the three handler fixes it
protects, because it fails for the next route too.**

---

## 2. Findings — F34…F39

| # | Sev | Area | Finding / property asserted | Fix / control | Test | Status |
|---|-----|------|------------------------------|---------------|------|--------|
| F34 | **High** | Scope not enforced on writes | `scope_for()` ran on every NE-bearing read and on **none** of the three `editor` write routes. Observed against `fiber_cut.json` with the editor scoped to `203.0.113.0/24`: `GET /api/situations/{sid}` → 404 (correct) while `POST …/feedback` → 200, `POST …/close` → 200, `POST /api/labels` → 200. Three consequences: a scoped editor mutates **global learned state** for NEs they cannot see; the 200-vs-404 split is an **existence oracle** on exactly the resources F32 claims are indistinguishable; and `close` calls `engine.forget_situation()` for a situation invisible to the caller. | Each handler resolves scope through the **same** `scope_for` the reads use and denies by routing into its **existing** 404 branch — same status, same body, same timing (DECISIONS #60, #65). For `feedback`/`close`, "in scope" is the predicate `project_situation_detail` already uses, **reused not restated**, so a read and a write cannot disagree. `kind="class"` labels are not NE-bearing and are not scoped, as `/api/classes` is not. Denials are audited. | `test_f34_scope_is_enforced_on_the_editor_write_routes`; `…_an_in_scope_editor_can_still_write`; **generated** `test_f34_every_mutating_route_below_admin_resolves_scope` | **met** |
| F35 | **Critical** | Privilege escalation via an operator-writable authorization input | `shaping._matches()` resolved a glob selector against the **operator label**, and `POST /api/labels` is an `editor` route — so the scoped role controlled an input to its own scope decision. Observed: with `{"editor": ["core-*"]}` and `10.0.0.1` labelled `core-1`, the editor's `/api/me` scope went from `{'scoped': True, 'ne_count': 1}` to `ne_count: 2` after one label write, and the graph gained the out-of-scope node. A second variant needed **no glob at all**: `_mark_visible()` compared the rendered `COALESCE(label, ip)` display string against `Scope.labels`, and labels are not unique, so copying an in-scope NE's label onto an out-of-scope one doubled the editor's visible timeline marks. | **Structural, not guarded.** The label leaves `_matches()`; a glob matches the **address** (DECISIONS #66). `Scope.labels` and the label column of `list_ne_for_scope()` are **deleted** — the dead-code gate keeps them gone. The timeline filters on **`ne_id`**, in SQL, with the rendered `device` field unchanged (DECISIONS #67). F34's scope check closes the write side. `scope_policy_errors()` reports a selector that can never match an address, so an upgrading admin learns at write time. | `test_f35_an_editor_cannot_widen_their_own_scope_with_a_label`; `…_a_colliding_label_does_not_leak_an_out_of_scope_timeline`; `…_scope_selectors_never_read_operator_writable_data`; `…_a_label_glob_selector_is_rejected_at_write_time`; **invariant** `test_f35_no_resolver_input_is_writable_by_a_scopable_role`; `…_the_scopable_roles_are_exactly_viewer_and_editor` | **met** |
| F36 | **High** | Unbounded, non-idempotent operator feedback destroying global learned state | `Engine.apply_feedback` → `learn.learn_epoch` called `Matrix.tick()` on **both** matrices, advancing the global forgetting epoch (`LAMBDA = 0.05`, every mass decays `0.95` per epoch, lazily). `store.add_feedback` checked only that the situation existed — no idempotence, no dedupe, no bound; `split` compounded via `SPLIT_PENALTY = 0.5`. Observed on one situation: 60 confirms then 20 splits took `A.epoch` 0 → 80 and pair `(1,1)` from mass `1.000000` to `1.824e-05`, writing **80** rows. ~600 epochs/minute drives every learned mass to ~1e-14. The role that can do it is `editor`, and under F34 it need not even see the situation. Feedback also recorded **no author**. | Idempotence per `(situation, verdict)`: `UNIQUE` index (`0007`) + `INSERT … ON CONFLICT DO NOTHING`; the learning effect applies **only** on a genuine insert, so a situation's total influence is bounded at **two** applications however many times anyone posts (DECISIONS #68). A *changed* verdict is a correction and applies once. The epoch tick belongs to a **closed situation** only — what `learn.py` has said since v0.1.0 (DECISIONS #69). `principal_ref` / `role` attribute every row. | `test_f36_repeated_feedback_is_idempotent_and_bounded`; `…_a_changed_verdict_still_applies_once`; `…_closing_a_situation_still_ticks_the_epoch`; `…_feedback_records_its_author` | **met** |
| F37 | **Med** | Unbounded, never-reclaimed write primitive | `store.set_label` was an unconditional UPSERT into a table with **no foreign key**, and `store.prune()` never touched `label`. Observed: five writes to `device` ids 900000–900004, none of which exist, all returned 200 and all persisted. Every `editor` therefore held an unbounded write primitive against the database file, whose rows were never reclaimed. | The target must exist, and the failure is **the same 404 the out-of-scope case produces**, so the fix for F37 cannot re-introduce the oracle F34 closes (DECISIONS #70). Migration `0007` deletes existing orphans (6 in the gate fixture). **No FK**: SQLite would need a table rebuild — the one migration class that can lose rows — inside a security patch (DECISIONS #71; ROADMAP). | `test_f37_a_label_write_to_a_nonexistent_target_is_rejected`; `…_label_writes_to_real_targets_still_work`; `test_migrate_populated_v070_database_dedupes_feedback_and_reaps_orphan_labels` | **met** |
| F38 | **Med** | Truncation before filtering: a volume oracle and an operational hazard | `store.list_situations(status, limit)` and `store.timeline_marks(limit)` applied `LIMIT` over the **global** ordering with the scope filter running in Python afterwards. Observed: global situations 2 → viewer at `limit=2` receives 2; after out-of-scope traffic the viewer writes nothing to and cannot see, global 4 → viewer at `limit=2` receives **0**. `timeline?limit=4` → admin 4 marks, viewer **0**. Two defects in one: the returned count varies with out-of-scope volume (the aggregate oracle F32 claims is closed), and — worse in a NOC — a scoped operator's own open incidents vanish while a noisy neighbour is busy. | The scope predicate is bound into the query so `LIMIT` bounds the **filtered** set, exactly as `scoped_stats` already did (DECISIONS #72). The unrestricted path runs the **unmodified v0.7.0 SQL**, so parity is by construction. Above `MAX_SCOPE_PARAMS` (30 000) the bound id list is **not truncated** — the query degrades to Python filtering, which is slower and still correct. | `test_f38_truncation_is_applied_after_the_scope_filter`; `…_the_unrestricted_result_set_is_unchanged`; `…_the_large_scope_fallback_agrees_with_the_bound_path`; `…_an_empty_scope_reads_nothing_without_a_query` | **met** |
| F39 | **Med** | No transaction discipline on the API write path | `Store` holds **one** `aiosqlite` connection shared by the engine and the API. `main.py` called `rollback()`; `api.py` called it **nowhere** (`grep -c` → 0). A handler that mutated and then raised left the statement pending on the shared connection, and the **next `commit()` from any other caller adopted it**. Observed with `audit.write_event` forced to raise inside `POST /api/users/{uid}/role`: request → 500, then the engine's next commit made the role change durable — **with no audit row**, contradicting F31's "every change is attributable". Separately, `Engine.apply_feedback` committed internally *and* the handler committed again: one route, two transactions, mutation durable **before** it was attributable. | One async context manager, `write_txn()`, next to the existing perimeter closures: acquire the lock, run the body, commit on success, `rollback()` on any exception, re-raise. Used by **every** mutating handler — a discipline implemented once, not a `try/except` repeated twenty times (DECISIONS #73). The engine's internal commit is removed so the API owns the boundary and every write path is mutate → audit → commit. | `test_f39_a_failed_write_leaves_nothing_to_commit`; `…_feedback_commits_exactly_once`; **generated** `test_f39_every_mutating_handler_uses_the_transaction_helper` | **met** |
| — | — | Migration integrity (F12 class) | `0007` must ship in the wheel **and** sdist, apply to a populated v0.7.0 DB with data intact and the audit chain verifying, and seed nothing. | Under the existing `migrations/*.sql` package-data glob; forward-only, additive; **zero** seeded rows. De-duplication keeps the **earliest** row by `created_at`, preserving when the operator first said it. | `test_migrate_populated_v070_database_dedupes_feedback_and_reaps_orphan_labels`; `test_v071_upgrade_changes_no_behaviour_except_the_three_documented_changes`; Gate 4 built-wheel/sdist install check (schema 7, index present, 0 policy rows, d3 checksum intact) | **met** |

---

## 3. The properties this review is required to assess

**The new invariant, proven rather than asserted.** No input to `visible_nes()` is writable by a
scopable role. `role` and `principal_ref` come from identities only an admin creates; the policy is
`scope.write`, admin-only with no delegation; the inventory is `ne.id` and `ne.ip`, written by the
engine from the trap stream and settable through no API route.
`test_f35_no_resolver_input_is_writable_by_a_scopable_role` asserts this over the **projection of
`list_ne_for_scope()`** and over the resolver's answer with hostile labels present versus stripped
— the two must be identical. `test_f35_the_scopable_roles_are_exactly_viewer_and_editor` pins the
premise so a new role cannot silently escape it.

**Existence disclosure on writes.** Out-of-scope and nonexistent produce the same status, the same
body and the same code path on every write route, because the scope denial routes into the
handler's pre-existing not-found branch rather than adding one. `POST /api/labels` is the sharp
case: the out-of-scope check (F34) and the target-existence check (F37) both raise the identical
`404 no such label target`, so neither discloses whether the other applied.

**Learned-state integrity.** A situation's total influence on the matrices is bounded at two
applications (one per verdict) whatever anyone posts. The global forgetting epoch is advanced only
by a closed situation. A scoped editor cannot reach the affinity of NEs outside their scope at all,
because the write is refused before `apply_feedback` runs.

**Audit and transaction integrity.** Every mutation and its audit row commit together or not at
all. A forced failure after a mutation leaves nothing for another caller to adopt.
`POST /feedback` performs exactly one commit beyond identity resolution — measured as a delta over
a read, because every authenticated request also commits the session touch.

**No new hot-path surface.** `datagram_received`, the receiver, the queue, the engine loop, the
window, candidate selection and the scorer gained nothing; the v0.6.0 F24 source-level assertions
and the v0.7.0 F33 assertions remain green. The only engine-side change is
`Engine.apply_feedback` — a **feedback** path, not a **correlation** path. `make eval` is
byte-identical (`sha256 c2e8a0ce…`).

**Corrections to `SECURITY-REVIEW-0.7.md`.** F32's "every NE-bearing read" and its aggregate claim
are **superseded in place** with a dated note pointing to F34 and F38; the published row is left
exactly as it was so what the review claimed, and when, stays answerable. The threat model carries
the same corrections.

---

## 4. Critical analysis — honest residual risk

**A defect class was found in a release whose review declared it closed.** That is the most
important sentence in this document, and it is not softened. `SECURITY-REVIEW-0.7.md` rated F32
**High**, walked four attackers through the scoping surface, and marked it **met**. It was wrong —
not in its facts, every one of which is accurate, but in its **frame**: it enumerated read paths
because scoping had been *built* as a read projection, and an enumeration inherits the blind spot of
the thing it enumerates.

*What changed in the review method*, so the next review does not repeat the omission:

1. **Write-path enumeration is now generated, not written.** The perimeter test walks
   `ROUTE_PERMISSIONS` and fails for any future mutating route below `admin` that does not resolve
   scope. A prose enumeration is a snapshot; a generated one is a ratchet.
2. **Resolver inputs are treated as adversarially controlled until proven otherwise.** For every
   input to an authorization decision, the question is now "what is the *lowest* role that can write
   this?", asked from the code and tabulated (Phase 0 gate §3). F35 is invisible unless that question
   is asked; it is obvious the moment it is.
3. **Every control is checked on both sides of the read/write boundary.** F34, F37, F38 and F39 are
   all the same omission wearing different clothes.

**Label globs are gone from scope selectors, and some operators will miss them.** A labelled estate
must now be scoped by address, CIDR, or `ne:<id>`. This is a real capability removed in a patch
release. The trade is that authorization no longer reads operator-writable data, and it is not
negotiable at any label-uniqueness guarantee an operator could offer — uniqueness is a data-quality
property that does not survive a restore, an import, or a determined editor. Existing policies using
a label glob are rejected at write time with a message naming `MIGRATION.md`, and `MIGRATION.md`
says an upgrading admin must review any such policy.

**Idempotence per `(situation, verdict)` is a real usability loss.** An operator who genuinely wants
to reinforce the same verdict twice cannot. That is the correct trade — the second identical verdict
carries no new information, and treating it as if it did is precisely the defect — but it is a
behaviour an operator can notice, and it is documented rather than hidden.

**The redaction cardinality disclosure of v0.7.0 is unchanged and still real.** A scoped viewer
still learns how many members of a partly-visible situation lie beyond their boundary. Nothing here
narrows it, and nothing here should: it is the minimum that keeps an operator from mis-sizing an
incident.

**Scoping is still presentation, not isolation.** Every residual-risk item in
`SECURITY-REVIEW-0.7.md` §4 remains true and is re-stated, not re-litigated. Making the perimeter
symmetric does not make it a boundary: the learned matrices are still global, situation ids are
still monotonic, timing is still shared.

**NE addresses are still created by anyone with network position to send a trap.** The new invariant
says no *authenticated scopable role* can write a resolver input. A party who can reach the trap
port can cause an `ne` row to exist, and therefore influence what a CIDR selector covers. That is
the pre-existing A1 attacker and the pre-existing allowlist control, unchanged by this release — but
it sits next to the new invariant and is named here so the invariant is not read as more than it is.

**`api.py` is still 1 752 lines, and four of six findings lived there because of it.** The security
dependency, the governance cache, `scope_for`, `audit_row`, the new `write_txn`, and forty route
handlers share one file, and that is why these defects were hard to see. The extraction into
`perimeter.py` is v0.7.2's theme with its shape already agreed (DECISIONS #74). Doing it here would
have made every fix hunk look like a move and left this release unauditable — but the debt is real,
it is named, and it is scheduled rather than deferred indefinitely.

**`label` still has no foreign key.** The existence check and the `0007` cleanup close the write
primitive; a restore from a backup taken mid-write, or a direct `sqlite3` session, can still create
an orphan. It would be cosmetic — labels are display strings joined `LEFT` into read models — and
the structural fix needs a table rebuild that a patch release should not carry.

**The `MAX_SCOPE_PARAMS` fallback is a performance cliff, not a correctness one.** Above 30 000
in-scope NEs the two scoped reads fetch unbounded and filter in Python. Correct, tested against the
bound path for exact agreement, and far outside the design point of a one-file SQLite appliance —
but it is an unbounded fetch on a request path and it is named rather than buried.

---

## 5. Mapping to `threat-model.md`

| Threat (v0.7.1 section) | Finding | Proving test |
|---|---|---|
| Write routes outside the read perimeter | F34 | `test_f34_scope_is_enforced_on_the_editor_write_routes`, `test_f34_every_mutating_route_below_admin_resolves_scope` |
| Existence oracle on a write | F34 | `test_f34_scope_is_enforced_on_the_editor_write_routes` (out-of-scope body == nonexistent body) |
| An authorization input the constrained role can write | F35 | `test_f35_an_editor_cannot_widen_their_own_scope_with_a_label`, `test_f35_no_resolver_input_is_writable_by_a_scopable_role` |
| A display string used as an authorization key | F35 | `test_f35_a_colliding_label_does_not_leak_an_out_of_scope_timeline` |
| A dead selector after the upgrade | F35 | `test_f35_a_label_glob_selector_is_rejected_at_write_time` |
| Unbounded operator influence on learned state | F36 | `test_f36_repeated_feedback_is_idempotent_and_bounded` |
| The global epoch advanced by an opinion | F36 | `test_f36_closing_a_situation_still_ticks_the_epoch` |
| Unattributable learned-state change | F36 | `test_f36_feedback_records_its_author` |
| Unbounded, never-reclaimed write primitive | F37 | `test_f37_a_label_write_to_a_nonexistent_target_is_rejected` |
| Volume oracle through truncation | F38 | `test_f38_truncation_is_applied_after_the_scope_filter` |
| A scoped operator losing their own incidents | F38 | `test_f38_truncation_is_applied_after_the_scope_filter` |
| An orphan write surviving a failed request | F39 | `test_f39_a_failed_write_leaves_nothing_to_commit` |
| Mutation durable before its audit row | F39 | `test_f39_feedback_commits_exactly_once` |
| A future route added outside the perimeter | F34 | `test_f34_every_mutating_route_below_admin_resolves_scope` (generated from `ROUTE_PERMISSIONS`) |
| A future handler writing without the discipline | F39 | `test_f39_every_mutating_handler_uses_the_transaction_helper` |
| A migration that changes behaviour | — (F12 class) | `test_migrate_populated_v070_database_dedupes_feedback_and_reaps_orphan_labels`, `test_v071_upgrade_changes_no_behaviour_except_the_three_documented_changes` |
| Isolation over-claim | F32 (unchanged) | `test_f32_scoping_is_not_tenant_isolation_is_documented` |
