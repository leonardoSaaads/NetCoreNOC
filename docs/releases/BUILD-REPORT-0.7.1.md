# Build report — NetCoreNOC v0.7.1 "the write perimeter"

**A security patch.** Six confirmed defects, one migration, one security review, one correction to
the previous review. No new capability, no new route, no new configurability, no restructuring.

| | |
|---|---|
| Tests | 499 → **524** (24 new test functions + 1 new parametrized case) |
| Coverage | 95.43 % → **95.46 %** |
| `make eval` | **byte-identical** (`sha256 c2e8a0ce…`) |
| Migrations | `0006` → **`0007`** |
| Decisions | #64 → **#74** |
| Findings | F33 → **F39** |
| Runtime dependencies | 5 → **5** |
| `PERMISSIONS` / `ROUTE_PERMISSIONS` | 28 / 39 → **28 / 39** |
| Files moved, modules added | **none** |

---

## 1. The defect class, and how it was found

v0.7.0 shipped visibility scoping and its review, under F32, described the control as **"one filter
applied to every NE-bearing read (list, detail, graph, timeline, entities, stats, SSE)"**. Every
word of that is accurate. It is also, read carefully, an admission: the perimeter had been *designed*
as a read projection, and an enumeration of read paths inherits the blind spot of the thing it
enumerates.

The finding method for this release was deliberately the inverse of the one that missed it:

1. **Enumerate the writes, not the reads.** A table of every mutating route → capability → does it
   resolve scope? → does it verify the target exists? → is it idempotent? → are the mutation and its
   audit row in one transaction? That table is §2 of `docs/gates/v0.7.1-phase-0.md`, and four of the
   six findings fell straight out of it. Of 19 non-`GET` routes, 5 sit below `admin`; 3 of those name
   a network element; **none of the 3** resolved scope.
2. **Treat every authorization input as adversarially controlled until proven otherwise.** For each
   input to `visible_nes()`, ask: *what is the lowest role that can write this?* Four of the five
   answered "admin" or "the engine". The fifth — the operator label — answered **"editor"**, the
   very role the scope constrains. That is F35, and it is invisible unless the question is asked.
3. **Check every control on both sides of the read/write boundary.** F34, F37, F38 and F39 are the
   same omission wearing four different coats.

Each finding was then reproduced as a **failing test against the unmodified v0.7.0 tree** before any
fix was written — 11 failures at Gate 1 — because a finding without such a test is not understood.

---

## 2. The six findings, each with its proof-of-concept and fix

### F34 — High — scope was not enforced on the editor write routes

**PoC.** `fiber_cut.json` replayed; editor scoped to `203.0.113.0/24` (matching nothing):

```
GET  /api/situations/{sid}            -> 404   (correct)
POST /api/situations/{sid}/feedback   -> 200   {"status":"recorded","verdict":"split"}
POST /api/situations/999999/feedback  -> 404
POST /api/situations/{sid}/close      -> 200
POST /api/labels  (out-of-scope NE)   -> 200
```

**Fix.** Each of the three handlers resolves scope through the **same** `scope_for()` the reads use
and denies by routing into its **existing** 404 branch. For `feedback` and `close`, "in scope" is
the predicate `project_situation_detail` already uses — reused, not restated, so a read and a write
cannot disagree. `kind="class"` labels are not NE-bearing and are not scoped, for the same reason
`/api/classes` is not. Denials are audited. DECISIONS #65.

### F35 — Critical — an editor escalated their own scope by writing a label

**PoC 1** (glob): with `10.0.0.1` labelled `core-1` and policy `{"editor": ["core-*"]}`, the editor
posts one label:

```
BEFORE  /api/me scope: {'scoped': True, 'ne_count': 1}   NEs: ['10.0.0.1']
POST /api/labels {device 2, "core-pwned"}  -> 200
AFTER   /api/me scope: {'scoped': True, 'ne_count': 2}   NEs: ['10.0.0.1', '192.168.50.1']
```

**PoC 2** (collision, no glob needed): `_mark_visible()` compared the rendered `COALESCE(label, ip)`
display string against the scope, and labels are not unique:

```
BEFORE  editor timeline marks: 2
POST /api/labels {device 2, "POP-SUL"}  -> 200
AFTER   editor timeline marks: 4
```

**Fix, structural rather than guarded.** The label leaves `_matches()` — a glob matches the address.
`Scope.labels` and the label column of `list_ne_for_scope()` are **deleted**, and the dead-code gate
keeps them gone. The timeline filters on `ne_id`, in SQL, with the rendered `device` field
unchanged. F34's check closes the write side. Scope-checking the label write would have closed
today's path and left the next one — a bulk import, a discovery integration, a migration — open.
DECISIONS #66, #67.

### F36 — High — feedback was unbounded and destroyed global learned state

**PoC.** One situation:

```
initial            A.epoch=0  E.epoch=0   pairs in A: 10
60x POST confirm   A.epoch=60 E.epoch=60  accumulated decay (1-0.05)^60 = 4.607e-02
20x POST split     mass of pair (1,1): before=1.000000  after=1.824e-05
rows in `feedback` = 80
```

**Fix.** Idempotence per `(situation, verdict)` — a `UNIQUE` index and `ON CONFLICT DO NOTHING`,
with the learning effect applied only on a genuine insert, so a situation's total influence is
bounded at **two** applications whatever anyone posts. The epoch tick belongs to a **closed
situation**, which is what `learn.py`'s docstring has said since v0.1.0. `principal_ref` and `role`
attribute every row. DECISIONS #68, #69.

### F37 — Medium — `label` accepted writes to nonexistent targets, never pruned

**PoC.** Five writes to `device` ids 900000–900004, none of which exist: all `200`, all persisted,
and `store.prune()` never reclaims them.

**Fix.** The target must exist, and the failure is **the same 404 the out-of-scope case produces**,
so the fix for one finding cannot re-introduce the oracle another closes. Migration `0007` deletes
existing orphans. **No foreign key**: SQLite would need a table rebuild — the one migration class
that can lose rows — inside a security patch. DECISIONS #70, #71.

### F38 — Medium — global truncation before scope filtering

**PoC.**

```
[before]  global situations=2;  viewer with limit=2 receives 2
          (out-of-scope traffic on 192.168.50.0/24, viewer writes nothing)
[after]   global situations=4;  viewer with limit=2 receives 0
timeline limit=4 -> admin 4 marks, viewer 0 marks
```

**Fix.** The scope predicate is bound into the query so `LIMIT` bounds the filtered set, exactly as
`scoped_stats` already did in v0.7.0. The unrestricted path runs the **unmodified v0.7.0 SQL**, so
parity is by construction. Above `MAX_SCOPE_PARAMS` the bound id list is not truncated — the query
degrades to Python filtering, which is slower and still correct, and is tested to agree exactly with
the bound path. DECISIONS #72.

### F39 — Medium — no transaction discipline on the API write path

**PoC.** `audit.write_event` forced to raise inside `POST /api/users/{uid}/role`:

```
before:                       role of 'vwr' = viewer
POST /api/users/{id}/role  -> internal 500 (mutation NOT committed)
(engine's next commit)
after:                        role of 'vwr' = admin      ← with NO audit row
```

**Fix.** One async context manager, `write_txn()`, beside the existing perimeter closures: acquire
the lock, run the body, commit on success, `rollback()` on any exception, re-raise. Used by all 22
mutating handlers. `Engine.apply_feedback`'s internal commit is removed so the API owns the boundary
and every write path is **mutate → audit → commit**. DECISIONS #73.

---

## 3. What is worth more than the six fixes

Two **generated** tests, because they fail for the *next* route and the *next* input, not only for
these:

- `test_f34_every_mutating_route_below_admin_resolves_scope` — walks `ROUTE_PERMISSIONS`. A mutating
  route added in any future release below `admin` fails CI until it is inside the perimeter or is
  consciously exempted with a written reason. The exemption list holds exactly two entries
  (`logout`, `password`), both acting on the caller's own session or account.
- `test_f35_no_resolver_input_is_writable_by_a_scopable_role` — asserts the projection of
  `list_ne_for_scope()` carries no operator-writable column, and that the resolver's answer is
  identical with hostile labels present or stripped.

Plus `test_f39_every_mutating_handler_uses_the_transaction_helper`, which stops a future handler
from quietly reverting to `async with store.lock: … commit()`.

All three were verified to **fail on the unmodified v0.7.0 tree** (`git stash push -- src/`), with
diagnostics naming the exact three routes and the exact column.

---

## 4. Corrections to the v0.7.0 review

`SECURITY-REVIEW-0.7.md` is **superseded in place**, never rewritten. The published F32 row is left
exactly as it was — what the review claimed, and when, stays answerable — and a dated *"Correction
to F32 — added 2026-07-29 (v0.7.1)"* block follows the findings table, naming both wrong sentences
and pointing at F34 and F38. The row's status now reads *partially superseded*, and the two §5
mapping rows carry their corrections. The threat model gains the same corrections in its `v0.7.1
extension`.

---

## 5. Decisions #65–#74

| # | Decision | Finding |
|---|---|---|
| 65 | A write is inside the perimeter or it is a defect — and it denies through the *existing* 404 | F34 |
| 66 | Scope selectors resolve against NE identity and address only — never operator-writable data | F35 |
| 67 | The timeline filters on NE identity, not on a rendered display string | F35 |
| 68 | Feedback is idempotent per `(situation, verdict)` | F36 |
| 69 | The learning epoch belongs to a closed situation, not to feedback | F36 |
| 70 | A label write to a nonexistent target is a 404 — and the affected tests are repaired, not weakened | F37 |
| 71 | No foreign key on `label` in a patch release | F37 |
| 72 | Truncation applies to the filtered set: the scope predicate moves into the SQL | F38 |
| 73 | One transaction discipline, implemented once | F39 |
| 74 | The perimeter extraction is v0.7.2's theme; its shape is recorded now and none of it built here | — |

Two decisions were **amended during Phase 3** rather than quietly re-implemented, and both are
recorded in place:

- **#66** — the migration aid was specified as "warn on a selector matching zero NEs". Built that
  way, it rejected a forward-looking CIDR like `203.0.113.0/24`, contradicting DECISIONS #57 (which
  resolves selectors against the live inventory on every request *because* NetCoreNOC discovers NEs
  continuously). The check became **static**: a selector whose literal characters cannot appear in
  an address can match nothing now or ever — exactly the dead label glob, with no false positives.
- **Gate 3 §2** also records the `situation_in_scope()` restructure that moved the unrestricted
  short-circuit into the helper, so the question is answered in one place.

---

## 6. Deferred, with the version that owns each

- **The `perimeter.py` extraction — v0.7.2's theme.** `api.py` is 1 752 lines and four of six
  findings lived there because of it. The agreed shape is recorded now (DECISIONS #74) so v0.7.2
  inherits it: the security dependency, `GovernancePolicies`, `resolve_identity`, `csrf_ok`,
  `scope_for`, `audit_row`, `RateLimiter`, `DENIED_ACTION` and `write_txn` move to one flat module,
  with every route handler left **textually unchanged** so the move is provable. This release's new
  perimeter helpers were deliberately placed next to the existing perimeter closures, and no
  existing closure was renamed, so v0.7.2 lifts the block as a unit.
- **A foreign key on `label`** — needs a table rebuild. DECISIONS #71, ROADMAP.
- **The v0.8.0 feedback dataset.** `feedback` gained two attribution columns because F36/F39 need
  them to be correct *today*. No part of the dataset was built.
- **`store.py` split by domain, `api.py` split by route group** — ROADMAP lines, weaker arguments
  than the perimeter extraction.
- True multi-tenant isolation, per-field scoping, custom roles, SSO/SCIM/MFA, rate-limiter redesign,
  SNMPv3, `/metrics`, pcap replay, webhook emission — all unchanged from SCOPE-0.7.

---

## 7. Honest caveats

**A defect class was found in a release whose review declared it closed.** `SECURITY-REVIEW-0.7.md`
rated F32 High, walked four attackers through the scoping surface, and marked it **met**. It was
wrong in frame, not in fact. The review method has been changed rather than the claim quietly
corrected — see `SECURITY-REVIEW-0.7.1.md` §4.

**Label globs are gone from scope selectors, and some operators will miss them.** A labelled estate
must now be scoped by address, CIDR, or `ne:<id>`. This is a real capability removed in a patch
release. The trade is that authorization no longer reads operator-writable data, and it is not
negotiable at any label-uniqueness guarantee an operator could offer. `MIGRATION.md` says plainly
that a stored policy using one must be reviewed, and such a selector is rejected at write time with
a message naming the file.

**Idempotence per `(situation, verdict)` is a real usability loss.** An operator who genuinely wants
to reinforce the same verdict twice cannot. Correct trade, real cost, documented rather than hidden.

**The redaction cardinality disclosure of v0.7.0 is unchanged and still real.** Nothing here narrows
it.

**Scoping is still presentation, not isolation.** Making the perimeter symmetric does not make it a
boundary: the learned matrices are global, situation ids monotonic, timing shared.

**NE addresses are still created by anyone with network position to send a trap.** The new invariant
says no *authenticated scopable role* can write a resolver input; address creation sits outside that
boundary by design, under the pre-existing allowlist control. Named so the invariant is not read as
more than it is.

**The `MAX_SCOPE_PARAMS` fallback is an unbounded fetch on a request path.** Correct, tested against
the bound path for exact agreement, and far outside a one-file SQLite appliance's design point — but
it is a performance cliff and it is named rather than buried.

**`label` still has no foreign key.** A restore from a mid-write backup, or a direct `sqlite3`
session, can still create an orphan. It would be cosmetic — labels are display strings joined `LEFT`
into read models — but it is not zero.
