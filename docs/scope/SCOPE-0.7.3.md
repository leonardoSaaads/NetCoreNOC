# SCOPE — NetCoreNOC v0.7.3

**Theme: the data and engine layers become legible — with zero behavioural change.**

This is a **structural release**, and the **last** one. It ships no feature, no fix, no
configurability, and **no behaviour**. Not one status code, not one path, not one field, not one
row, not one number.

v0.7.2 rebuilt the HTTP layer as sixteen modules behind one readable perimeter and proved, hash by
hash, that behaviour did not move. It deliberately left two files alone: `src/netcorenoc/store.py`
(1 512 lines, 109 methods on one class) and `src/netcorenoc/main.py` (1 079 lines, an `Engine` of
659 lines wearing the same hat as the process runner). `MODULE-ARCHITECTURE.md` §6 and §7 already
specify both targets. This release **executes a decision it did not make**, at the two points marked
otherwise.

The one sentence that governs the whole release:

> **The class header changes; the method does not — and the single connection and the single lock
> that the whole write discipline rests on come through untouched.**

The runtime identity is unchanged: one Python 3.12 asyncio process, one SQLite (WAL) file, one
static UI, environment variables only, no build step, **zero new runtime dependencies** (five,
unchanged), **zero new migrations**, **zero new routes, capabilities or audit actions**. The import
path stays `netcorenoc`; `from netcorenoc.store import Store` and `from netcorenoc.main import
Engine` keep working verbatim; `python -m netcorenoc.main` and `python -m netcorenoc audit verify`
keep working. `netcorenoc.store` becoming a package is internal and invisible to every caller.

All prior scope documents and their invariants still hold; `docs/security/threat-model.md` keeps the
authority it has held since v0.2.0. On a conflict, this document wins on *scope*, the build prompt
wins on *process and quality*, the threat model wins on *security posture*, and
`docs/architecture/MODULE-ARCHITECTURE.md` wins on *placement*.

**Delivery model (unchanged).** The repository is read-only to automation: the maintainer takes the
resulting archive and pushes it by hand. No step depends on pushing, on CI running, or on any
external account, registration, or dashboard action. Every gate is local and reproducible on the
maintainer's machine (`make qa`, `make eval`, `docker compose config`, a locally built wheel).

---

## 1. In scope — exactly five workstreams, and nothing else

### 1. `store.py` → the `store/` package

One `Store` class, one connection, one `store.lock`, sixteen domain modules split **along
`store.py`'s own section comments** as `MODULE-ARCHITECTURE.md` §6 fixes them. All **109** method
bodies move as textually identical source; the enclosing class header is the only edit.

Mechanism (DECISIONS #88): **mixins over a thin annotated `StoreBase`** that declares the ten
attributes and the `conn` accessor and holds no behaviour. `Store.__init__` stays in
`store/__init__.py` and remains the only place those ten attributes are assigned. Where a mixin
calls a method that lands in a sibling mixin — measured: exactly two edges — the mixin inherits that
sibling rather than the base restating a signature.

### 2. `main.py` → the engine and the runner

`main.py` **stays a module** (DECISIONS #89), because `python -m netcorenoc.main` is the documented
way to run the correlator and `main.py` carries the `if __name__ == "__main__"` guard. The
extractions are flat siblings: `settings.py`, `gaps.py`, `scorer_lifecycle.py`, `maintenance.py`,
`runner.py`, `engine.py`.

The `Engine` keeps the batch lock and everything that reasons about it. `maintenance()` and
`maintenance_loop()` stay with it, against both documents' module tables, because `maintenance`
takes `store.lock` and calls `_close_situation` (DECISIONS #90). This workstream also resolves the
project's one remaining layer violation: `runner.py` and `main.py` may reach up into
`netcorenoc.api`; **`engine.py` may not**.

### 3. The guards tightened

* A **layer-dependency test** that does not exist yet (DECISIONS #92), driven from a table
  mirroring `MODULE-ARCHITECTURE.md` §1, with an **empty** exemption list.
* **`COHESION_EXEMPT`** (DECISIONS #91) — a mechanism distinct from `DEBT_ALLOWLIST`, for a module
  that is large by deliberate, permanent design rather than unfinished. Required, because
  `engine.py`'s must-stay content measures 425 method lines before scaffolding. At most two entries;
  no owner, no fix date; must cite an invariant by name; may not grow; may not also be allowlisted.
* **`DEBT_ALLOWLIST` shrinks to three entries** — `store.py` and `main.py` leave; nothing joins.

### 4. A short security review

`docs/security/SECURITY-REVIEW-0.7.3.md`, continuing the series from **F40** *only if the
reorganisation actually reveals something*. A move-only release should produce **zero** findings; a
review that manufactures one to look diligent is worse than one that says "none".

### 5. Specification for v0.7.4 — specification only

Two declaration-gate gaps found reviewing v0.7.2, plus the three remaining oversized modules.
Specified here in `MODULE-ARCHITECTURE.md` and `docs/ROADMAP.md`, **built there**.

---

## 2. Out of scope — deferred, with the reason

1. **The two declaration-gate gaps → v0.7.4.** Specified in `MODULE-ARCHITECTURE.md` §10. Both live
   in `api/declare.py`, a file this release does not touch. Fixing a security-adjacent guard inside
   a move release forfeits the parity story for a latent, unexploited gap — and a fix buried in a
   1 500-line diff is a fix nobody can review. Specified now so v0.7.4 executes rather than
   rediscovers.
2. **Splitting `rbac.py` (436), `shaping.py` (476), `varbind_profile.py` (417) → v0.7.4.** They stay
   on `DEBT_ALLOWLIST` with their existing owners and seams. Three small releases' worth of work is
   not one release's worth.
3. **Any fix, however small, revealed while reading the code.** Directive 5. It becomes a
   `docs/ROADMAP.md` line and, where security-adjacent, a note in the security review — never a
   change here. A fix inside a move is invisible to review, and that is precisely how F34–F39 stayed
   invisible for a release.
4. **Changing any SQL.** Not a query, not an index, not a `PRAGMA`, not a parameter binding. The
   `# nosec` markers on `store.py` that bandit now reports as redundant are a v0.7.4 ROADMAP line,
   **not** a tidy to slip in while the file is open.
5. **New abstractions.** No repository pattern, no unit-of-work, no session objects, no query
   builder, no ORM, no DI container, no async context manager beyond what exists. The only new
   constructs are `StoreBase`, `EngineBase` and `COHESION_EXEMPT`, each justified by a measurement.
6. **Anything from the v0.8.0 feedback-dataset roadmap.**
7. SNMPv3, `/metrics`, pcap replay, outbound webhook / `Case` JSON emission — still out.

---

## 3. The invariants this release may not break

1. **One `Store`, one connection, one `store.lock`.** The load-bearing invariant. F39 exists
   *because* one connection is shared between the engine task and every API request, and v0.7.1's
   `write_txn` discipline is built on `store.lock` being the one mutual exclusion. Several `Store`
   objects, several connections, or several locks is a behaviour change whose failure mode is **data
   corruption under concurrency, invisible to every existing test**. A dedicated concurrency test is
   written **before** anything moves.
2. **The batch lock does not fragment.** The ingest path stays readable in one file, because
   "ingestion is sacred" is only auditable if a reviewer can confirm — without following imports —
   that nothing on it takes a lock, does I/O, or awaits where it must not.
3. **All 109 `Store` method bodies and every moved `Engine` method body are edited by exactly zero
   characters**, proved by the Phase 0 hash table recomputed in Phase 5.
4. **`make eval` byte-identical** to the frozen baseline.
5. **No test assertion is edited** — only module lists, import paths that name a moved symbol, and
   the genuinely new structural tests.
6. **Import paths are preserved.** Every symbol in the Phase 0 inventory keeps resolving from
   `netcorenoc.store` and `netcorenoc.main`.
7. **The HTTP layer is not touched.** `src/netcorenoc/api/` is finished.
8. Coverage **at or above** 95.69 % — a pure move cannot lower it, and a drop means something is no
   longer exercised.

---

## 4. What this release does *not* buy

Stated plainly, because a structural release invites the claim that it made things safer:

**It does not make the data layer more correct.** The same 109 methods in sixteen files have exactly
the same behaviour, and every residual risk in `SECURITY-REVIEW-0.7.1.md` §4 stands unchanged. What
it buys is that `store.lock`'s single ownership and the ingest path's cohesion are now visible in
the file layout instead of being facts a reviewer has to reconstruct from 1 512 lines.

And it carries an honest cost: a mixin split makes it *easier* for a future contributor to add a
method that forgets the lock, because the neighbouring methods that would have shown the pattern now
live in another file. The controls against that are the concurrency test and v0.7.1's `write_txn`
discipline — **not** the layout.

---

## 5. After this release

`store.py` and `main.py` leave the debt allowlist, `docs/ROADMAP.md` records what remains for
v0.7.4 (the two declaration-gate gaps and the three smaller modules), and **v0.8.0 resumes the
feature line** with the operator-feedback dataset. The project arrives at v0.8.0 with no module over
the size guard except those that are large by deliberate, documented design.
