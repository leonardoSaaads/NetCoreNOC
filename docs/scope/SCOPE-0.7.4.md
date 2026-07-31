# SCOPE — NetCoreNOC v0.7.4

**Theme: close every loose end the v0.7.x series leaves behind, so v0.7.5 and v0.8.0 start from a
repository with no contradictions and no unowned debt.**

Three things were open at v0.7.3, and this release closes all three.

1. **Two holes in the declaration gate**, found by adversarial probing of v0.7.2 and reproduced by
   execution in [`../gates/v0.7.4-phase-0.md`](../gates/v0.7.4-phase-0.md) §2.
   `MODULE-ARCHITECTURE.md` §10.1 specifies both fixes. They become **F40** and **F41** — the first
   security findings since F39 (v0.7.1).
2. **Three modules over the size guard** — `shaping.py` (476), `rbac.py` (436),
   `varbind_profile.py` (417), the entire remaining `DEBT_ALLOWLIST`. This release empties it to
   **zero**.
3. **The repository contradicted itself about what v0.8.0 is.** `ROADMAP.md` said *customer-supplied
   models* in two places and *the operator-feedback dataset* in two others; the whole of the scorer
   plugins draft was tagged `v0.8.0: planned`. The resequencing that settles it — models to
   **v0.13.0**, ONNX only, no Python entry-point — had been decided and acted on for two releases
   but **never recorded**. It is recorded now (DECISIONS #93) and guarded (DECISIONS #94).

The runtime identity is unchanged: one Python 3.12 asyncio process, one SQLite (WAL) file, one
static UI, environment variables only, no build step, **zero new runtime dependencies** (five,
unchanged), **zero new migrations** (seven, unchanged), **zero new routes, capabilities, audit
actions or served paths**. The import path stays `netcorenoc`; `from netcorenoc.rbac import …`,
`from netcorenoc.shaping import …` and `from netcorenoc.varbind_profile import …` keep working for
every symbol; `python -m netcorenoc.main` and `python -m netcorenoc audit verify` keep working.
`netcorenoc.rbac` and `netcorenoc.shaping` becoming packages is internal and invisible to every
caller.

All prior scope documents and their invariants still hold; `docs/security/threat-model.md` keeps the
authority it has held since v0.2.0. On a conflict, this document wins on *scope*, the build prompt
wins on *process and quality*, the threat model wins on *security posture*, and
[`../architecture/MODULE-ARCHITECTURE.md`](../architecture/MODULE-ARCHITECTURE.md) wins on
*placement*.

**Delivery model (unchanged).** The repository is read-only to automation: the maintainer takes the
resulting archive and pushes it by hand. No step depends on pushing, on CI running, or on any
external account, registration, or dashboard action. Every gate is local and reproducible
(`make qa`, `make eval`, `docker compose config`, a locally built wheel).

---

## 1. In scope — exactly five workstreams, and nothing else

### 1. The declaration gate — F40 and F41

**The two intentional behaviour changes this release ships. There are no others.**

* **F40** — `DeclaredRoutes` wraps `get`, `post` and `delete`, and only the decorator form.
  `app.add_api_route("/api/x", handler, methods=["GET"])` registers without ever calling
  `require_declaration`. The fix, per §10.1: **assert after `create_app` has registered every route,
  before it returns, that every `/api` route on the built app is declared.** Complete *by
  construction* rather than by enumeration — it inspects the result, so it catches registration
  paths nobody has written yet. The decorator-time refusal is **kept as well**, because failing at
  the point of registration gives a far better error.
* **F41** — the exemption is by path *prefix*: `require_declaration` returns early for anything not
  starting with `/api`, which is accidentally true of `/metrics` and of every future non-`/api`
  route. The fix: an explicit allowlist of unauthenticated paths, **asserted against what
  `routes_static.py` actually registers**, so it cannot drift from what is served.

The gate gets **stronger, never narrower**. Every route that registered at v0.7.3 still registers;
the authorization matrix, the route-map completeness tests and the route-order parity test all pass
**unedited**.

### 2. The last three splits — `DEBT_ALLOWLIST` reaches zero

The v0.7.3 mechanism: bodies move as identical text, the enclosing module changes, `__init__.py`
re-exports so no consumer notices. `git mv` for the file that becomes a package. **56 function
hashes** taken in Phase 0 and recomputed in Phase 5.

| Module | Becomes | Note |
|---|---|---|
| `shaping.py` (476) | `shaping/fields.py`, `shaping/scope.py`, `shaping/project.py`, `shaping/__init__.py` | **Three** parts, not the two `MODULE-ARCHITECTURE.md` §10.2 assumed — DECISIONS #95 |
| `rbac.py` (436) | `rbac/tables.py`, `rbac/policy.py`, `rbac/__init__.py` | The prose travels with the tables; the three module-level asserts travel with what they assert |
| `varbind_profile.py` (417) | one extraction to `varbind_accum.py` | **Not** a package. `engine` layer, not cross-cutting — DECISIONS #97 |

**`rbac.py` remains the single source of authority for authorization**, and this is the release's
highest structural risk. Re-export by **identity, not equality** (DECISIONS #96): a copying
`__init__.py` (`PERMISSIONS = dict(tables.PERMISSIONS)`) would leave every existing test green while
creating exactly the second source that is forbidden. Eight identity assertions and a
no-shadowing assertion are added, and both were shown to fail against a deliberately-copying
`__init__.py` before being accepted.

### 3. The roadmap, made consistent and recorded

* **DECISIONS #93** — the resequencing, with its reasoning: v0.8.0 is the operator-feedback dataset;
  customer models → v0.13.0; the Python entry-point escape hatch is **rejected, not deferred**; the
  worker-process preemption harness stays a blocking prerequisite.
* **`ROADMAP-0.8-TO-0.13.md`** — the chain written down as the project's own document, one screen
  per release, recording **why the order cannot be permuted**.
* **The drafts superseded in place** (`git mv` + dated notes, never a rewrite):
  the scorer-plugins draft → `SCORER-PLUGINS-0.13-DRAFT.md`, retagged; `EXTENSIBILITY-0.6-DRAFT.md`
  amended where it names the sequence.
* **A documentation-consistency guard** (`tests/test_documentation.py`, DECISIONS #94), installed
  against the **still-contradictory** tree and observed **red before green**.

### 4. Security review

`../security/SECURITY-REVIEW-0.7.4.md`, continuing the series from **F40**, in the established
format, with the threat model updated.

### 5. Specifications — no implementation

* **`FEEDBACK-PATH-0.7.5-DRAFT.md`** — the operator-feedback acquisition path, every element tagged
  `v0.7.5: planned`.
* **`FEEDBACK-DATASET-0.8-DRAFT.md`** — the v0.8.0 dataset, refined, every element tagged
  `v0.8.0: planned`.

---

## 2. The two intentional behaviour changes, stated once

Accidental behaviour change is a build failure. This release ships exactly two, both from
workstream 1, both **import-time or startup-time — never request-time**:

1. A route registered by **any** path without a declaration now fails, where previously only the
   decorator form failed.
2. A **non-`/api` path outside the unauthenticated allowlist** now fails, where previously every
   non-`/api` path was exempt.

`make eval` is byte-identical. Every other test that passed at v0.7.3 passes here.

---

## 3. Out of scope — deferred, each with the release that owns it

1. **The operator-feedback acquisition path → v0.7.5.** The UI card teardown, the SSE update
   granularity, and any read-side API change that follows. Specified in
   [`../architecture/FEEDBACK-PATH-0.7.5-DRAFT.md`](../architecture/FEEDBACK-PATH-0.7.5-DRAFT.md),
   built there. **Deliberately not fixed here**, however tempting a three-line change looks: it is a
   runtime behaviour change on the operator's path and it would forfeit this release's parity story.
2. **The v0.8.0 feedback dataset itself** — schema, capture, migration, bias report. §5 above
   refines the specification; it builds nothing.
3. **Splitting `engine.py`.** It is `COHESION_EXEMPT` **permanently**, and the empty debt allowlist
   is not an invitation to "finish the job". There is no job to finish: the entry has no owner and
   no date because "ingestion is sacred" has no expiry.
4. **Any fix, however small, revealed while reading the code.** A defect noticed during a move
   release is a `../ROADMAP.md` line and a note in the security review — not a fix here.
5. **New abstractions.** Two packages, one extraction, one assertion, one allowlist. No base
   classes beyond what the split mechanically needs, no registries, no framework, and **no second
   level of nesting** in either new package.
6. **The four redundant `# nosec B608` markers** at `store/retention.py:23,27,31,35`. Still
   untouched: changing a `# nosec` on a SQL string is a change to SQL handling, and this release
   changes no SQL. Re-deferred to `../ROADMAP.md`.
7. **`receiver.py`'s timing-dependent coverage** (87–91 % across runs of identical code). Not this
   release's scope; stays on `../ROADMAP.md`.
8. **Making `ROUTE_SCOPE` enforcing** — having the perimeter *inject* the scope check from the
   declared posture. Injection changes control flow, and control flow is behaviour. Still a ROADMAP
   line (DECISIONS #80).
9. SNMPv3, `/metrics`, pcap replay, outbound webhook / `Case` JSON emission — still out. `/metrics`
   is named in workstream 1 as the motivating example for F41; it is **not built here**.

---

## 4. What v0.7.4 leaves behind

* No module under `src/netcorenoc/` over 400 lines except `engine.py` (542), which is
  `COHESION_EXEMPT` by documented design.
* `DEBT_ALLOWLIST` **empty**, and still defended in both directions — the "may only shrink" and "no
  module may join" tests are kept, with `ALLOWLIST_MEMBERSHIP_CEILING` emptied so that *any* new
  entry fails. An empty allowlist that nothing defends is a coincidence, not a guarantee.
* `COHESION_EXEMPT` unchanged at one entry; the layer-rule exemption list still empty.
* A repository that states **exactly one answer** to what v0.8.0 is, with a test that fails if it
  ever again states two.
* Two specifications ready to be built, each traced to the code that motivates it.
