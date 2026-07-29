# SCOPE — NetCoreNOC v0.7.1

**Theme: the write perimeter — six confirmed defects in which a v0.7.0 guarantee is enforced on
reads and not on writes.**

This is a **security patch release**, not a feature release. It ships **no new capability, no new
surface, no new configurability, and no restructuring.** Every change is either a fix for a numbered
finding, its regression test, or the documentation that stops the review from claiming more than the
code does.

The v0.7.0 review declared, under F32, that scoping is enforced by "**one filter applied to every
NE-bearing read**". That sentence is true, and it is also the defect. The perimeter was designed as
a *read* projection, and the three editor-level write routes were never brought inside it. Worse,
one of the inputs the scope resolver reads — the operator label — is **writable by the very role the
scope is meant to constrain**, which turns a presentation control into a self-service escalation.

The one sentence that governs the whole release:

> **Authorization never reads data the constrained party can write, and a write is inside the
> perimeter or it is a defect.**

With no stored governance policy, v0.7.1 is byte-identical to v0.7.0 on every route, every status
code, and every shaped field — with **three deliberate, documented exceptions**, each a defect fix
with its own `DECISIONS.md` entry (§2 below).

The runtime identity is unchanged: one Python 3.12 asyncio process, one SQLite (WAL) file, one
static UI, environment variables only, no build step, **zero new runtime dependencies** (five,
unchanged). All prior scope documents and their invariants still hold; `docs/security/threat-model.md`
keeps the authority it has held since v0.2.0. On a conflict, this document wins on *scope*, the
build prompt wins on *process and quality*, the threat model wins on *security posture*.

**Delivery model (unchanged).** The repository is read-only to automation: the maintainer takes the
resulting archive and pushes it by hand. No step depends on pushing, on CI running, or on any
external account, registration, or dashboard action. Every gate is local and reproducible on the
maintainer's machine (`make qa`, `make eval`, `docker compose config`, a locally built wheel).

---

## 1. In scope — exactly six findings, one migration, one review, one correction

Each finding was **confirmed by executed proof-of-concept against the v0.7.0 tree**, reproduced as a
failing test before any fix was written, and is fully specified in
`docs/security/SECURITY-REVIEW-0.7.1.md`.

### F34 — High — scope is not enforced on the editor write routes

`scope_for()` is called on every NE-bearing read and on **none** of the three `editor`-level write
routes (`POST /api/situations/{sid}/feedback`, `POST /api/situations/{sid}/close`,
`POST /api/labels`). A scoped editor mutates global learned state for NEs they cannot see; the
200-vs-404 split is an existence oracle on exactly the resources F32 claims are indistinguishable;
and `close` reaches into engine state for an invisible situation.

**Fix.** Each of the three handlers resolves scope through the **existing** `scope_for` and denies
by routing into its **existing** 404 branch — same status, same body, same timing as a nonexistent
target. For `feedback` and `close`, "in scope" is "at least one member alarm's NE is in scope",
reused from `project_situation_detail` so the read and the write can never disagree. For `labels`
with `kind="device"` it is `scope.allows_ne`; `kind="class"` is not NE-bearing and is not scoped,
for the same reason `/api/classes` is not. The denial is audited.

### F35 — Critical — an editor escalates their own scope by writing an operator label

`shaping._matches()` resolves a glob selector against the operator label; `store.list_ne_for_scope()`
supplies that label; `POST /api/labels` is an `editor` route. The scoped role therefore controls an
input to the scope decision. A second, cheaper variant needs no glob at all: `_mark_visible()`
decides timeline visibility by **string equality** against `Scope.labels`, and labels are not unique.

**Fix — all three parts.** (1) Scope selectors resolve against NE **identity and address only**; the
label leaves `_matches()`, and `Scope.labels` plus the label column of `list_ne_for_scope()` become
dead and are removed. (2) The timeline filters on `ne_id`, not on the rendered display string; the
`device` field the UI renders is unchanged. (3) F34's scope check on `POST /api/labels` closes the
write side.

This is a **behaviour change for any existing scope policy that uses a label glob**: such a selector
now matches by address or not at all. Stated in `MIGRATION.md`, and `scope_policy_errors()` now warns
on a selector matching **zero** NEs so an admin finds out at write time.

### F36 — High — operator feedback is unbounded and destroys global learned state

`Engine.apply_feedback` → `learn.learn_epoch` calls `Matrix.tick()` on both matrices, advancing the
global forgetting epoch; `store.add_feedback` has no idempotence, no dedupe and no bound. 60 confirms
plus 20 splits took one pair's mass from 1.000000 to 1.824e-05.

**Fix.** (1) Idempotence per `(situation, verdict)` — a `UNIQUE` index in migration `0007` and an
`INSERT … ON CONFLICT DO NOTHING`; the learning effect applies only on a genuine insert, and a
*changed* verdict is a legitimate correction that applies once. (2) Feedback no longer advances the
global epoch — an epoch is a **closed situation**, which is what the `learn.py` docstring already
says. (3) Attribution: `principal_ref` and `role` columns, written from the calling principal.

### F37 — Medium — `label` accepts writes to nonexistent targets, unbounded, never pruned

`store.set_label` is an unconditional UPSERT into a table with no foreign key, and `store.prune()`
never touches it. Five writes to device ids 900000–900004, none of which exist, all returned 200 and
all persisted.

**Fix.** Verify the target exists and return the **same 404** the out-of-scope case produces, so the
two remain indistinguishable. Migration `0007` deletes orphaned rows. **No foreign key** is added:
SQLite would require a table rebuild, a data-integrity risk disproportionate to a patch release
(DECISIONS #71; the FK is a ROADMAP line).

### F38 — Medium — global truncation before scope filtering

`store.list_situations` and `store.timeline_marks` apply `LIMIT` over the **global** ordering and the
scope filter runs in Python afterwards, so a scoped principal's response is a function of activity
they cannot see. Operationally a scoped viewer's own open incidents vanish when a noisy neighbour is
busy; for security the returned count varies with out-of-scope volume.

**Fix.** Push the scope predicate into the query so `LIMIT` applies to the filtered set, binding
`ne_ids` exactly as `scoped_stats` already does. The unrestricted path keeps the **unmodified v0.7.0
SQL**, so parity is by construction. The parameter count is bounded and documented.

### F39 — Medium — no transaction discipline on the API write path

`Store` holds one `aiosqlite` connection shared by the engine and the API. `main.py` calls
`rollback()`; `api.py` calls it nowhere. A handler that mutates and then raises leaves the statement
pending, and the next `commit()` from any other caller adopts it — the mutation lands, unaudited.

**Fix.** One async context manager, next to `audit_row` inside `create_app`: acquire `store.lock`,
run the body, commit on success, `rollback()` on any exception, re-raise. Every mutating handler is
converted mechanically. The internal `commit()` is removed from `Engine.apply_feedback` so the API
owns the boundary.

### Migration `0007_write_perimeter.sql`

Forward-only, additive, applying cleanly to a populated v0.7.0 database. Two nullable attribution
columns on `feedback`, a `UNIQUE` index on `(situation_id, verdict)` with prior de-duplication
(earliest by `created_at` kept), and the F37 orphan cleanup. **No governance rows, no scorer rows,
no seed of any kind** — the migration changes no behaviour by itself.

### Security review and the correction to v0.7.0

`docs/security/SECURITY-REVIEW-0.7.1.md` continues the finding series from F34 and states the
**class**, not just the instances. The F32 row and §4 of `SECURITY-REVIEW-0.7.md` are **superseded in
place** with a dated note pointing to F38 — never edited to look right in retrospect.

---

## 2. The three deliberate behaviour changes at empty policy

Everything else at empty policy is untouched. Any fourth change is a defect in this release's work.

| # | Change | Finding | Decision |
|---|---|---|---|
| 1 | A label write to a target that does not exist now returns **404** (was 200) | F37 | #70 |
| 2 | A repeated **identical** feedback verdict on the same situation is now a **no-op** (was applied and recorded every time) | F36 | #68 |
| 3 | A list endpoint applies its `LIMIT` **after** filtering — invisible at empty policy, since an unrestricted scope filters nothing, but the SQL changes and the unrestricted result set is asserted unchanged | F38 | #72 |

---

## 3. Explicitly out of scope — deferred, with the version that owns each

1. **The v0.8.0 feedback dataset** — schema, capture, and bias reporting. This release makes the
   *existing* feedback path trustworthy; it does not build the dataset. `feedback` gains attribution
   columns in `0007` **only** because F36/F39 need them to be correct today, not as groundwork.
2. **Any module or package reorganisation → v0.7.2, whose entire theme it is.** `api.py` is 1 644
   lines and four of this release's six findings live in it; that is a real argument for extraction
   and *not* an argument for doing it here. A security patch's value is a reviewable diff, and
   moving the files the fixes touch makes every fix hunk look like a move. The agreed shape is
   recorded now so v0.7.2 inherits it rather than re-deciding it (DECISIONS #74).
3. **True multi-tenant isolation.** Unchanged: scoping is presentation, not isolation. The residual
   risks in `SECURITY-REVIEW-0.7.md` §4 remain true and are re-stated, not re-litigated.
4. **Per-field scoping, custom roles, SSO/SCIM/MFA.** Unchanged from SCOPE-0.7.
5. **Rate limiting redesign.** The existing per-IP token bucket stays. F36 is fixed by idempotence
   and bounded effect, not by a new limiter.
6. **A foreign key on `label`.** Deferred with F37; a ROADMAP line.
7. SNMPv3, `/metrics`, pcap replay, outbound webhook / `Case` JSON emission — still out.

---

## 4. Invariants — what this release may not do

1. **Correlation parity is a hard gate.** `make eval` byte-identical to v0.7.0. Nothing touches the
   receiver, the queue, the engine loop, the window, candidate selection, the scorer,
   `LinkFeatures`, `LinkScore`, or `datagram_received`. The only engine-side change permitted is
   `Engine.apply_feedback` and its transaction boundary — a **feedback** path, not a **correlation**
   path.
2. **Empty-policy parity**, with exactly the three exceptions in §2.
3. **The scope decision may not read data that a scoped role can write.** The resolver's inputs are
   NE identity and NE address only, asserted by test and explained by a comment at the resolver.
4. **A write is inside the perimeter or it is a defect.** One `scope_for`, one 404 branch, no second
   decision site, no scattered `if ne in`.
5. **One mutation, one transaction, one audit row.** A discipline implemented once, not a checklist
   repeated twenty times.
6. **Fail-closed, and never lock the admin out.** Admin is still never scoped.
7. **Zero new runtime dependencies.** Five, unchanged.
8. **No new routes, no new capabilities, no new audit actions.** `PERMISSIONS` (28),
   `ROUTE_PERMISSIONS` (39), `PUBLIC_ROUTES` (1), `AUDITED_DENIED_PERMISSIONS` (14) and
   `audit.ACTIONS` (30) are frozen.
9. **No file moves, no new modules, no package restructuring.** Every fix lands in the file that
   owns the behaviour today.
10. **Never renumber history.** DECISIONS continue from #65, findings from F34, migrations from
    `0007`.

---

## 5. Definition of done

- All 499 v0.7.0 tests pass, with only the documented, justified test repairs required by F37
  (DECISIONS #70).
- Every one of F34–F39 has at least one regression test that **fails on the unmodified tree** and
  passes after the fix.
- A **generated** write-perimeter test over `ROUTE_PERMISSIONS` asserts every mutating route below
  `admin` resolves scope, so a route added in any future release fails CI until it is inside the
  perimeter.
- A **resolver-input test** asserts no scope-resolution input is writable by a scopable role.
- `make eval` byte-identical; `make qa` green; coverage ≥ the v0.7.0 figure minus one point.
- Migration `0007` applies to a populated v0.7.0 DB with data intact, the audit chain verifying and
  `PRAGMA foreign_key_check` clean, and ships in a freshly built wheel **and** sdist.
- The docs claim exactly what the code does — including saying plainly that a defect class was found
  in a release whose review declared it closed.
