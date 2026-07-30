# Build report — NetCoreNOC v0.7.3

**"The data and engine layers become legible" — internal structure only, zero behaviour change.**

The two largest and oldest files in the project — `store.py` (1 512 lines, 109 methods on one
class) and `main.py` (1 079 lines) — became twenty-six modules. Nothing else moved: not a status
code, not a path, not a field, not a row, not a number. `make eval` is byte-identical, and all
**141** method bodies in `Store` and `Engine` are proved unchanged by a hash table taken before the
move and recomputed after it.

This is the **last structural release**. v0.8.0 resumes the feature line.

---

## 1. What shipped

### 1.1 `store.py` → the `store/` package

Eighteen modules, largest **213** lines, one level deep, split along `store.py`'s own section
comments exactly as `MODULE-ARCHITECTURE.md` §6 fixed them.

| Module | Lines | | Module | Lines |
|---|--:|---|---|--:|
| `read_models.py` | 213 | | `lifecycle.py` | 85 |
| `governance.py` | 194 | | `audit_log.py` | 85 |
| `auth.py` | 168 | | `feedback.py` | 68 |
| `entities.py` | 158 | | `types.py` | 66 |
| `situations.py` | 130 | | `base.py` | 51 |
| `alarms.py` | 118 | | `retention.py` | 48 |
| `scoring_config.py` | 108 | | `learned.py` | 41 |
| `devices.py` | 100 | | `state_clears.py` | 38 |
| `__init__.py` | 97 | | `ingest_gaps.py` | 29 |

**No module needed a size exemption.** §4.1's contingency ("if a section lands over 400, name the
seam") never arose.

### 1.2 `main.py` → the engine, the runner, and six siblings

| Module | Lines | Owns |
|---|--:|---|
| `engine.py` | 542 | `Engine`, `FlapDetector` — the batch lock and everything that reasons about it |
| `runner.py` | 227 | `run()`, `Supervisor`, `operator_warnings`, the bootstrap banner |
| `maintenance.py` | 113 | the promotion sweep, severity confirmation, the profiler flush |
| `scorer_lifecycle.py` | 112 | the v0.6.0 seam's lifecycle: load, fail safe, warn, audit |
| `settings.py` | 106 | `Settings`, `read_env`, the removed-alias errors |
| `gaps.py` | 94 | `GapTracker`, `_OpenGap`, `_record_ingest_gaps`, `GAP_CLOSE_S` |
| `main.py` | **79** | `main()`, the `__main__` guard, the re-exports |
| `engine_base.py` | 61 | `EngineBase` — fifteen attribute declarations, nothing else |

`python -m netcorenoc.main` is unchanged, which is why `main.py` stayed a **module** and did not
become a package (DECISIONS #89).

### 1.3 What did *not* move

`src/netcorenoc/api/` has an **empty diff** for the whole release. So do `correlate.py`,
`scoring.py`, `learn.py`, `receiver.py`, `preview.py`, `rootcause.py`, `severity.py`,
`varbind_profile.py`, `rbac.py`, `shaping.py`, the migrations, and `ui/`.

---

## 2. The mechanism, and why option (b) was rejected

`MODULE-ARCHITECTURE.md` §6 named two candidates and left the choice to this release's Phase 1,
to be settled by measuring `mypy --strict` on two real sections.

**Chosen: mixins over a thin annotated `StoreBase`** — a third option §6 did not name (DECISIONS
#88). `store/base.py` holds the ten attribute annotations and the `conn` accessor and **nothing
else**: no queries, no state, no behaviour. It is neither a `Protocol` restating `Store`'s shape nor
duplicated annotations, but one declaration site.

**Option (b) — free functions taking `conn`, with `Store` as a façade — was rejected on a ground
§6 understated and which is disqualifying.** It would rewrite all 109 method bodies as module-level
functions with a `conn` parameter, plus 109 delegating one-liners. That changes all 109 hashes, so
**the parity proof becomes impossible to state**. A refactor of the data layer that cannot prove it
moved nothing is not one this project should ship. The 109 one-liners are also 109 chances at a
transcription error, each of which would be a silent behaviour change in a data-layer method.

**Option (a) — a `Protocol` base — was rejected as §6 rejected it**: a `Protocol` restating
`Store`'s internals is a second source of truth for its shape.

### 2.1 The measurement, and where it contradicted the brief

The build brief supplied a validated prototype using `devices` + `audit_log`. Those two sections
happen to have **no cross-mixin calls at all**, which is why it measured zero errors. The sections
chosen here were picked to be adversarial: **`devices` + `alarms`**, the pair containing a real
cross-mixin call.

```
$ mypy --strict np          # StoreBase exactly as specified: annotations + conn, nothing else
np/alarms.py:27: error: "AlarmMixin" has no attribute "device_id"  [attr-defined]
np/alarms.py:28: error: "AlarmMixin" has no attribute "class_id"   [attr-defined]
np/alarms.py:29: error: "AlarmMixin" has no attribute "ne_id"      [attr-defined]
np/alarms.py:33: error: "AlarmMixin" has no attribute "entity_level0" [attr-defined]
Found 4 errors in 1 file (checked 5 source files)
```

**Four errors, not zero.** The mechanism is sound; the base as literally specified is not quite
sufficient. An AST sweep bounded the problem exactly: **six** methods are called across a mixin
boundary in the entire class — `conn` (already on the base), the four `devices` methods
`alarms.ingest` calls, and `governance.situation_member_nes` that `read_models.list_situations`
calls.

Two amendments were tried:

| Variant | Result |
|---|---|
| **(A)** declare the five signatures on `StoreBase` | **5 errors** — `...` bodies fail `mypy --strict` with `empty-body`. Making it pass needs `raise NotImplementedError` or `@abstractmethod`, i.e. *behaviour* on a base that must hold none. Worse, it is a live hazard: a mixin assembled without its sibling resolves the stub, and the failure mode is a **silent no-op write**. Rejected. |
| **(B)** the calling mixin inherits the sibling mixin | **0 errors**, zero declarations, zero duplication, linear MRO, 9/9 moved bodies hashing identically. **Chosen.** |

Two inheritance edges in the whole package: `AlarmMixin(DeviceMixin)` and
`ReadModelsMixin(GovernanceMixin)`. `MODULE-ARCHITECTURE.md` §6 is superseded by a **dated note
pointing at the ADR** — the original paragraph is left as written, because a superseded decision
that has been rewritten cannot be audited.

The `Engine`'s mixins needed no such amendment: `EngineBase` has **no method declarations at all**,
which is a consequence of DECISIONS #90 rather than luck.

---

## 3. The parity proof

```
Store : baseline=109 now=109  missing=NONE  added=NONE  MISMATCHES=NONE
Engine: baseline= 32 now= 32  missing=NONE  added=NONE  MISMATCHES=NONE

PARITY: ALL METHOD BODIES IDENTICAL
```

The hash is `sha256` of each method's source from its `def` line to its last, dedented to column
zero, with decorator lines excluded and recorded separately. That is deliberate: the hashed region
sits **below** the class header, so the one edit a move requires is outside it *by construction*.
The class header changes; the method does not. That is the whole trick, and it is what makes a
1 512-line split provable rather than merely careful.

It is taken through `inspect.getsource` on the **live attribute resolved via each class's
`__mro__`**, not by re-reading files. A file-level check would prove the text exists somewhere; this
proves the text Python actually resolves for `Store.<method>` is byte-for-byte what v0.7.2 resolved.
A split that placed a method correctly but shadowed it would pass the former and fail this.

**Two things that are not method bodies did change**, both named rather than left to be found:

1. `MIGRATIONS_DIR` gained one `.parent` because the file moved a level down. The resolved path was
   asserted equal to the pre-split value.
2. Three module-attribute patch targets in `tests/` follow the symbols they name — unavoidable,
   because a module-level constant is read through the `__globals__` of the module where the
   reading *function* was defined, and a re-exported name is a second binding, not an alias.

---

## 4. The concurrency invariant, and how it was protected

This was the release's real risk, and the reason its guard was written first.

> **One `Store`, one `aiosqlite` connection, one `store.lock`.**

Phase 0 measured the coupling rather than assuming it, and **corrected the brief's expectation**.
The private caches are *not* shared across domains — each is touched by `__init__` plus methods that
all land in one module. The real coupling is that **103 of the 109 methods, across 15 of the 16
modules, read `self.conn`**. And **no `Store` method acquires `store.lock` at all**: it is taken
entirely by callers (`Engine._commit_batch`, `Engine.maintenance`, `Perimeter.write_txn`), which
makes the lock a **public contract of the object** and explains why splitting `Store` into several
objects would have been invisible to the data layer's own tests.

`tests/test_store_concurrency.py` (9 tests) was installed in Phase 2 against the **unmodified**
tree and **mutation-tested**:

| Mutation injected into the real tree | Result |
|---|---|
| a second `aiosqlite` connection on the `Store` | connection-identity test **FAILED** |
| `Perimeter.write_txn` given its own `asyncio.Lock` | lock-contention test **FAILED** |
| concurrent audit appends stop taking `store.lock` | serialisation **and** chain tests **FAILED** |

All nine pass after the split, unchanged. The sharpest of them drives **24 concurrent audit
appends** and requires contiguous ids and a chain that re-verifies — two appenders that are not
mutually excluded read the same predecessor and fork the chain.

---

## 5. The §5.3 decision, with its number

**DECISIONS #91 — `COHESION_EXEMPT`.** Phase 0 measured 425 must-stay method lines and concluded the
mechanism would be required; Phase 4 measured **`engine.py` at 542 lines** and it was.

`DEBT_ALLOWLIST` means *"too big, will be fixed by release N"*. `engine.py` will never be fixed,
because there is nothing to fix — directive 4 forbids splitting it permanently. Filing it as debt
would put a promise in CI that nobody intends to keep, and the first time a reviewer noticed the
date slip, the honest response would be to move the date — which is exactly how a ratchet becomes a
comment.

Five constraints, each with its own test: the reason must cite an invariant **by name** from §1; a
module may be in one list or the other, never both; entries carry **no owner and no fix date** (that
absence *is* the semantic difference, and it is asserted); the exempt module may not grow past its
recorded count; and at most **two** entries may exist. `engine.py` is the only one.

### 5.1 One departure from both module tables

`maintenance()` and `maintenance_loop()` stayed in `engine.py`, against `MODULE-ARCHITECTURE.md` §7
*and* the build prompt's §5.1 table, under §5.2's escape hatch (**DECISIONS #90**). `maintenance` is
the only extraction candidate that does `async with self.store.lock:` — the same `asyncio.Lock`
object `_commit_batch` takes, because there is only one — and the only one that calls a directive-4
must-stay method (`_close_situation`). A reviewer asking "what closes a situation, and under which
lock?" must not have to follow an import.

The structural payoff is concrete: keeping it removed the **only** mixin→`Engine` call, which is why
`EngineBase` needs no method declarations and stays a pure declaration site.

---

## 6. The layer violation, resolved — and the rule finally enforced

`MODULE-ARCHITECTURE.md` §1 recorded one genuine upward import since v0.7.2: `main.py` →
`netcorenoc.api`, because `main.py` was the `Engine` (domain) **and** the process entry point.
`runner.py` now holds the entry point and may reach up; `engine.py` may not, and does not.

**No test enforced the layer rule before this release.** That is why the violation sat
recorded-but-unfixed for a full release, and it is the honest reason `tests/test_layers.py` exists
(DECISIONS #92). Its exemption list is **empty**.

The guard found two things review had not:

1. **`engine_base.py` was unplaced** — a module that had escaped the layer table by not appearing in
   it, which is the exact hole `test_every_runtime_module_is_assigned_a_layer` closes.
2. **`main.py` was still classified `engine` and started importing `runner`.** The guard flagged an
   upward import and was right: `main.py` had *stopped being the domain*. Reclassifying it as an
   entry point is this release stated in one line of a table.

`engine_base.py` is a ninth module §5.1's table did not name. The import graph forced it —
`engine.py` imports the mixins to assemble `Engine`, so a mixin importing `EngineBase` from
`engine.py` is a cycle. `store/base.py` sits apart from `store/__init__.py` for the same reason.

---

## 7. Guard state at the end of the release

| Guard | Before | After |
|---|---|---|
| `DEBT_ALLOWLIST` | 5 entries | **3** — `rbac.py`, `shaping.py`, `varbind_profile.py`, all v0.7.4 |
| Allowlist may grow? | **yes, silently** | no — `test_no_module_may_join_the_allowlist` |
| `COHESION_EXEMPT` | did not exist | 1 entry, cap 2, no owner, no date, ceiling 542 |
| Layer rule | a paragraph | a test, exemptions **empty** |
| Modules over 400 | 5 | 4, each accounted for |

The allowlist hole is worth naming: "the allowlist may only shrink" was asserted in **one direction
only**. A *stale* entry failed; a **newly added** module would have passed green. Build prompt §6
asked that the test "actually would fail if a module were added" — it would not have.

---

## 8. Honest caveats

* **This release does not make the data layer more correct.** The same 109 methods in sixteen files
  have the same behaviour, and every residual risk in `SECURITY-REVIEW-0.7.1.md` §4 stands.
* **It makes one thing worse.** A mixin split makes it *easier* for a future contributor to add a
  method that forgets the lock, because the neighbouring methods that would have shown the pattern
  now live in another file. The controls are `tests/test_store_concurrency.py` and v0.7.1's
  `write_txn` — **not** the layout. `SECURITY-REVIEW-0.7.3.md` §6.1.
* **`Store.__mro__` is eighteen entries.** "Where does this method come from?" now has an
  indirection. The linear MRO and §6's table make it unambiguous, but it is one step more than
  before.
* **The Docker image was not built** — no daemon in the build environment. `docker compose config`
  validates, and the equivalent was verified from a wheel in a clean virtualenv serving every public
  path with the security headers intact. Stated plainly rather than skipped.
* **`receiver.py`'s coverage is timing-dependent** (87–91 % across runs of identical code, including
  at the v0.7.2 baseline). This release does not touch it. ROADMAP.
* **The two declaration-gate holes are real and unfixed.** Confirmed by execution, specified in
  `MODULE-ARCHITECTURE.md` §10.1, deferred to v0.7.4 — because a fix buried in a 2 600-line diff is
  a fix nobody can review.

---

## 9. Decisions recorded

| # | Decision |
|---|---|
| **88** | Mixins over a thin annotated base, with sibling inheritance where a mixin calls a sibling — supersedes `MODULE-ARCHITECTURE.md` §6 in place, by dated note |
| **89** | `main.py` stays a module; the `Engine` gets the same mechanism via `EngineBase` |
| **90** | `maintenance()` does not leave `Engine`, against both module tables |
| **91** | `COHESION_EXEMPT` — "cohesive by design" is not "unfinished" |
| **92** | The layer rule gets a test, seven releases after it got a paragraph |

---

## 10. The numbers

```
Tests                646 → 701   (+55: 9 concurrency, 13 layer, 8 guard, 25 submodule/package)
Coverage             95.69% → 95.80%   (above baseline; final run, not the best run)
Method bodies proved unchanged        141 / 141
make eval            byte-identical (sha256 d6f3fc39…5a512b9d)
Runtime dependencies 5 → 5
Migrations           7 → 7
Routes               48 → 48, in the same order
Security findings    F39 → F39 (F40 unused)
Modules over 400     5 → 4
Largest module       1 512 → 542 (and 542 is permanent, by design)
```

### 10.1 A gate that did real work

After the first store step, coverage fell **95.69 % → 95.52 %** while `make qa` stayed green. The
lost lines were the over-`MAX_SCOPE_PARAMS` branches of `list_situations` and `timeline_marks`:
`test_governance.py`'s F38 test patched a name that no longer reached the code reading it.

**The test still passed.** It compares the bound path against the fallback path and asserts they
agree; with the patch inert, both runs took the same branch and the equality held trivially. A test
whose entire purpose is comparing two code paths was silently comparing one path with itself, and
nothing but the coverage number said so.

That is why "coverage may not drop" is a gate and not a nicety, and it is the single best piece of
evidence in this release that the guards are load-bearing.
