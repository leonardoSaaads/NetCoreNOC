# Security review — NetCoreNOC v0.7.3

**Findings: none.**

v0.7.3 moved 2 591 lines — `store.py` and `main.py`, the two oldest and largest files in the
project — into twenty-six modules. It changed no behaviour, and this review found nothing to
number. A move-only release should produce zero findings, and a review that manufactures one to
look diligent is worse than one that says "none" — so this one says none, and then spends its
length on the three things that matter: **the concurrency invariant that was this release's real
risk**, **proving nothing moved**, and **saying plainly what the release does not buy and what it
makes worse.**

The finding series stays at **F39** (v0.7.1). F40 is unused.

---

## 1. The release's highest risk, and how it was contained

Everything else in this document is bookkeeping. This section is the review.

`store.py` became a package. The invariant that split could have broken is:

> **One `Store` class, with one `aiosqlite` connection and one `store.lock`.**

This is not style. F39 (`SECURITY-REVIEW-0.7.1.md` §4.2) exists *precisely because* the single
connection is shared between the engine task and every API request: a handler that mutated and then
raised left the statement pending, and the **next commit from any unrelated caller adopted it** —
the mutation landing with no audit row. v0.7.1's `write_txn` fixed that by making `store.lock` the
one mutual exclusion and rolling back inside it.

So a v0.7.3 that produced several `Store` objects, or several connections, or several locks, would
have silently un-fixed F39 and worse. **And the failure mode is the dangerous kind: data corruption
under concurrency, invisible to every test the project had.** Nothing in the v0.7.2 suite drove two
domains at once, let alone three.

### 1.1 What was measured, before anything moved

Phase 0 measured the coupling rather than assuming it. The result corrected the expectation this
release was briefed with:

* The private caches (`_device_ids`, `_ne_ids`, `_entity0_ids`, `_entity_ids`, `_class_ids`,
  `_touched`) are **not** shared across domains. Each is touched by `__init__` plus methods that
  all land in a single target module.
* **103 of the 109 methods, across 15 of the 16 domain modules, read `self.conn`.** The connection
  *is* the cross-domain coupling.
* **No `Store` method acquires `store.lock` at all.** It is taken entirely by callers —
  `Engine._commit_batch`, `Engine.maintenance`, `Perimeter.write_txn`. The lock is therefore a
  **public contract of the `Store` object**, not an implementation detail of anything inside the
  package. That is exactly why splitting `Store` into several objects would have been invisible to
  the data layer's own tests.

### 1.2 The control, installed before the move and mutation-tested

`tests/test_store_concurrency.py` (9 tests) was written in Phase 2 against the **unmodified**
tree, so it could not be shaped to fit the outcome. It asserts identities structurally rather than
by `isinstance`, and drives real concurrency:

* exactly one `aiosqlite.Connection` reachable from the store, and no nested store-like delegate;
* exactly one `asyncio.Lock`, stable across accesses;
* no `Store` method acquires it (a new mixin that "helpfully" did would deadlock against all three
  real callers);
* concurrent writes from **three different domain modules** — a device upsert, a feedback insert
  and an audit append — under `asyncio.gather`, with **one commit from one caller** having to make
  all three durable, which is only true on a shared connection;
* **24 concurrent audit appends** must produce contiguous ids and a chain that re-verifies. This is
  the sharpest probe available for a fragmented lock: two appenders that are not mutually excluded
  read the same predecessor and fork the chain;
* `write_txn` rolls back into nothing, commits on success, and **contends on the same lock the
  engine takes**.

Each was verified to fail on a real violation before being trusted:

| Mutation injected into the real tree | Result |
|---|---|
| a second `aiosqlite` connection on the `Store` | connection-identity test **FAILED** |
| `Perimeter.write_txn` given its own `asyncio.Lock` | lock-contention test **FAILED** |
| concurrent audit appends stop taking `store.lock` | serialisation **and** chain tests **FAILED** |

All nine tests pass after the split, **unchanged**.

### 1.3 The write-transaction discipline survived

`Perimeter.write_txn` is untouched — `src/netcorenoc/api/` has an empty diff for the whole release.
It still wraps every mutating handler, still rolls back on any exception before re-raising, and
still does so inside `async with self._store.lock`, which is the same object
`Engine._commit_batch` takes.

`tests/test_api.py::test_f39_a_failed_write_leaves_nothing_to_commit` — the F39 regression test —
**exercises the post-split `Store` with no edit at all.** It obtains a `Store` from the fixture and
drives the discipline through a real HTTP request; it names no module path and touches no internal
of `Store`, so the split is invisible to it. Confirmed green at every gate. Its sibling
`test_f39_feedback_commits_exactly_once` and the source-scanning
`test_f39_every_mutating_handler_uses_the_transaction_helper` are likewise unedited and green.

---

## 2. No new surface

| Surface | v0.7.2 | v0.7.3 | Evidence |
|---|---:|---:|---|
| Routes | 48 | **48** | `test_route_table_order_is_unchanged` — identical and in order |
| Capabilities (`PERMISSIONS`) | 25 | **25** | `rbac.py` untouched |
| `ROUTE_PERMISSIONS` / `ROUTE_SCOPE` / `PUBLIC_ROUTES` | unchanged | **unchanged** | `rbac.py` untouched |
| Audit actions | unchanged | **unchanged** | `test_audit_catalog_completeness` |
| Migrations | 7 | **7** | none added; `schema_version` stays 7 |
| Runtime dependencies | 5 | **5** | `pyproject.toml` |
| Served static paths | 7 | **7** | `routes_static.py` untouched |
| Environment variables | unchanged | **unchanged** | `Settings` moved verbatim to `settings.py` |
| SQL statements | unchanged | **unchanged** | all 109 method bodies hash identically |

The package gained modules and nothing else.

---

## 3. No decision moved, and none was duplicated

Authorization, visibility scope, and the transaction boundary each still have **exactly one**
implementation, and all three are still in `api/perimeter.py`, which this release did not open.

* **Authorization** — `rbac.resolve_capabilities`, called by the perimeter's security dependency.
  `rbac.py` is byte-identical.
* **Scope** — `shaping.visible_nes`, called by `Perimeter.scope_for`. `shaping.py` is
  byte-identical.
* **The transaction boundary** — `Perimeter.write_txn`, the single `async with store.lock` wrapper.

The `store/` package holds no authorization logic and makes no visibility decision. The scope-aware
read models it contains (`scoped_stats`, `list_situations`, `timeline_marks`,
`situation_member_nes`) **apply** a set of NE ids the perimeter computed; they do not compute it.
That was true at v0.7.2 and the method bodies are unchanged text, so it is true now by construction
rather than by inspection.

### 3.1 The proof

`sha256` of every method's source, taken before the move and recomputed after it — through
`inspect.getsource` on the **live attribute resolved via each class's `__mro__`**, so the proof
covers what Python actually resolves after a sixteen-mixin assembly rather than merely what the
files contain.

```
Store : baseline=109 now=109  missing=NONE  added=NONE  MISMATCHES=NONE
Engine: baseline= 32 now= 32  missing=NONE  added=NONE  MISMATCHES=NONE
```

**141 method bodies, zero mismatches.**

Two things that are not method bodies did change text, and both are named rather than left to be
found:

1. `MIGRATIONS_DIR` gained one `.parent` because the file moved a level down. The resolved path was
   asserted equal to the pre-split value, not assumed.
2. Three module-attribute patch targets in `tests/` follow the symbols they name. This is
   unavoidable: a module-level constant is read through the `__globals__` of the module where the
   reading *function* was defined, and no re-export can change that — a re-exported name is a
   second binding, not an alias.

---

## 4. The audit chain across a real upgrade

Done as an executed test, not an argument. A database was written by the **real v0.7.2 code
extracted from git at the merge commit `990d280`** — 120 traps through the real `Engine`, a
maintenance pass, a scorer configuration, an active governance policy, a bootstrap admin and an
audit row — and then opened by v0.7.3.

```
wrote  v0.7.2 DB: schema v7, 12 alarms, 3 situations, 1 audit rows, final hash f5bb4fa01a446d98…
opened v0.7.3   : schema v7 (unchanged), 1 audit rows, final hash f5bb4fa01a446d98…
chain verifies  : True
snapshot keys   : 20 compared

UPGRADE: IDENTICAL — no migration, no drift
```

No migration ran, the final chain hash is the same, and twenty snapshot keys — alarms with severity
and rank, situations with root and scorer provenance, members, links, learned edges, entities,
varbind profiles, meta, users, scorer configs, governance policies, state clears, ingest gaps —
compare identical.

---

## 5. The layer rule is now enforced, and the recorded violation is resolved

`MODULE-ARCHITECTURE.md` §1 has stated *a layer may import downward and may import cross-cutting,
never upward* since v0.7.2, and recorded one genuine violation: `main.py` → `netcorenoc.api`,
because `main.py` was the `Engine` (domain) **and** the process entry point that builds the HTTP
server.

**No test enforced the rule.** That is not a small observation: it is why the violation sat
recorded-but-unfixed for a full release. `tests/test_layers.py` now parses every module's imports
and fails on any upward edge, with an **empty** exemption list, and
`test_the_engine_does_not_import_the_http_layer` states the specific edge separately so it cannot
regress quietly.

The security value is modest and worth stating honestly: an upward import is not itself a
vulnerability. What it costs is the ability to answer *"where is this decided?"* — and that
property, not any single bug, is what let F34–F39 hide in a 1 752-line file. The guard protects the
property.

### 5.1 A note on what the guard found

The layer test caught two things during Phase 4 that review had not: a module (`engine_base.py`)
that had escaped the layer table by not appearing in it, and `main.py` importing `runner.py` while
still classified as domain code — which was the guard noticing that `main.py` had stopped being the
domain. Both are process observations rather than security findings, recorded because a guard that
has been seen to catch something is worth more than one that has only ever been green.

---

## 6. Critical analysis — what this release does **not** buy

**It does not make the data layer more correct.** The same 109 methods in sixteen files have
exactly the same behaviour. Every residual risk in `SECURITY-REVIEW-0.7.1.md` §4 stands unchanged:
the shared connection is still shared, the lock is still coarse, an API write still joins whatever
transaction is open, and the scope model is still a presentation control rather than tenant
isolation.

What it buys is narrower and worth naming precisely: `store.lock`'s single ownership and the ingest
path's cohesion are now **visible in the file layout** instead of being facts a reviewer had to
reconstruct from 1 512 lines. `engine.py`'s docstring says why it may not be split; `store/base.py`
says the lock is a caller's contract; `COHESION_EXEMPT` says it in CI.

### 6.1 And it makes one thing worse

Stated plainly because a structural release invites the opposite claim.

**A mixin split makes it *easier* for a future contributor to add a method that forgets the lock.**
In v0.7.2, someone adding a `Store` method wrote it surrounded by 108 others; the pattern was
visible in the neighbours. Now a contributor adding a method to `store/feedback.py` sees three
methods, none of which acquires a lock — because none of them should — and nothing on screen tells
them a caller must. The layout that makes the invariant legible to a *reviewer* makes it less
legible to an *author*.

The controls against that are **`tests/test_store_concurrency.py` and v0.7.1's `write_txn`
discipline — not the layout.** Specifically: `test_no_store_method_acquires_the_store_lock` fails on
a method that takes the lock, and `test_f39_every_mutating_handler_uses_the_transaction_helper`
fails on a mutating handler that does not go through `write_txn`. Neither catches a *store method
that a caller forgets to wrap*; that gap existed at v0.7.2 and exists now, unchanged.

A second, smaller cost: `Store.__mro__` is eighteen entries. Any reader asking "where does this
method come from?" now has an indirection to follow. `MODULE-ARCHITECTURE.md` §6's table is the
index, and the linear MRO makes the answer unambiguous, but it is one more step than before.

---

## 7. Threat model

**No change.** `docs/security/threat-model.md` describes assets, actors, trust boundaries and
assumptions. This release moved no trust boundary, added no actor, changed no asset and altered no
assumption. Saying so is the right answer; editing it to look busy would be worse than leaving it
alone.

---

## 8. Deferred, with the reason

**Two gaps in the v0.7.2 declaration gate**, found by adversarial probing of `api/declare.py` and
confirmed by execution. Neither is exploited today; both are latent holes in a guard whose whole
value is completeness. Specified in `MODULE-ARCHITECTURE.md` §10 and **deferred to v0.7.4**:

1. `DeclaredRoutes` wraps `get`, `post` and `delete` only, and only the decorator form.
   `app.add_api_route("/api/x", handler, methods=["GET"])` registers successfully **without ever
   calling `require_declaration`** — verified: the route appears in the table.
2. The exemption is by **path prefix**, not by absence of capability. `/metrics`, already on the
   roadmap, would be exempt by accident.

They are deferred rather than fixed because fixing a security-adjacent guard inside a move release
forfeits the parity story for a latent, unexploited gap — and a fix buried in a 2 600-line diff is a
fix nobody can review. That is the same reasoning that kept v0.7.2 from fixing what it found, and
it is why F34–F39 were found at all.

**Four redundant `# nosec B608` markers** now at `store/retention.py:23,27,31,35`. Not touched:
changing a `# nosec` on a SQL string is a change to SQL handling, and this release changed no SQL.
ROADMAP.

---

## 9. Verdict

**Zero findings. The series stays at F39; F40 is unused.**

The reorganisation added no surface, moved no decision, and duplicated none. The one invariant that
could have been broken silently was measured first, guarded by a test written before the move and
proven to fail on a real violation, and re-verified after. The audit chain survives a real v0.7.2
database unchanged.

What the release does not do is make anything safer, and §6.1 names the one thing it makes harder.
