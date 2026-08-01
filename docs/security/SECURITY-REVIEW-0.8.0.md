# Security review — NetCoreNOC v0.8.0

**Scope of this review.** One confirmed finding, **F43**, continuing the F1–F42 series; a
**correction to the completeness claim v0.7.5 made**; and an assessment of the release's genuinely
new security object — **a corpus that is a scope bypass by construction**, plus the product's
**first destructive control**.

The finding is fifteen lines. The corpus is the thing to review. v0.8.0 creates a body of data that
contains every NE, entity and raw varbind in the network, captured on a path where visibility
scoping does not exist and must not, and it will grow for years. Getting F43 right and the corpus
wrong would be the wrong trade by a wide margin.

---

## 1. What this release changed

| | |
|---|---|
| New migration | **one** — `0008_feedback_dataset.sql` |
| New tables | 4 (`capture_run`, `dataset_observation`, `dataset_pair`, `feedback_member`) |
| New columns | 15 on `feedback`, 1 on `situation` |
| New routes | **2** — `GET`/`POST /api/dataset/retention`, both `config`-class and `admin_only` |
| New audit actions | **2** — `retention.preview`, `retention.change` |
| New request fields | **2**, both optional — `member_ids`, `updated_at` on the feedback POST |
| New CLI subcommands | 2 — `dataset bias`, `dataset stats` |
| New dependencies | **none** (five runtime, eleven dev, unchanged) |
| `make eval` | **byte-identical** — `c2e8a0ce…`, unchanged since v0.2.0 |
| UI | one function signature and one call site in `app.js`; CSP unchanged; no `innerHTML` |

---

## 2. Findings — F43

| ID | Severity | Title |
|---|---|---|
| **F43** | **Med** | The declaration gate neither checks nor refuses a route with an empty method set, and Starlette serves every verb on one |

### F43 — an empty method set is skipped

**Location.** `src/netcorenoc/api/declare.py`, `assert_every_route_is_declared`, v0.7.5 form:

```python
checked = cast(Route, route)
methods: set[str] = checked.methods or set()
for method in sorted(methods):
    if method == "HEAD" and "GET" in methods:
        continue
    require_declaration(method, checked.path)
```

Over an **empty** set that loop runs **zero times**. The route's *type* is in `KNOWN_ROUTE_SHAPES`,
so F42's shape refusal does not fire either. It is neither checked nor refused — the exact gap
between the two findings, and the reason F42's shape check did not make the completeness claim true.

**Threat (A3 malicious/careless contributor, A6 operator error).** A route reaching a running
appliance without declaring its capability or its scope posture, on a guard whose entire value is
completeness.

**Reproduced by execution** (`docs/gates/v0.8.0-phase-0.md` §7), with the doc routes disabled so the
finding is not masked by F41:

```
routes on the app : ['/admin/backdoor']
route.methods     : set()
>>> assert_every_route_is_declared(app) PASSED — the gate did not refuse it.

   GET 200 · POST 200 · PUT 200 · DELETE 200 · PATCH 200 · HEAD 200 · OPTIONS 200
```

Starlette does not filter by verb when `methods` is falsy, so one undeclared route serves all seven.

**Reachability, recorded honestly.** Two paths can produce it and two cannot:

| Path | Result |
|---|---|
| `DeclaredRoutes.get/post/delete` | **cannot** — each passes a literal verb to `require_declaration` |
| FastAPI's non-decorator registration helper with `methods=[]` | **cannot** — refused by FastAPI's own assertion |
| `router.routes.append(Route(..., methods=[]))` | **REACHABLE** |
| clearing `.methods` after registration | **REACHABLE** |

Both reachable paths are demonstrated; both unreachable ones are asserted by
`test_f43_the_unreachable_construction_paths_stay_unreachable`, so a dependency upgrade that relaxed
FastAPI's assertion would say so. **Latent** — no route in this repository is registered either way,
exactly as F40, F41 and F42 were.

**Fix.** An empty method set is **unverifiable** — there is no verb to look up in either
authorization table, so there is nothing to check — and it is therefore **refused**, by the same
reasoning F42 applied to unknown shapes.

**The option not taken, because it is worse.** Treating an empty set as *the full verb set* — the
"what Starlette actually serves" reading — is superficially more precise. It invents a declaration
requirement for seven verbs nobody wrote, so the natural fix a contributor reaches for is to declare
all seven, **turning a mistake into seven authorizations**. It also encodes an assumption about
Starlette's dispatch behaviour, and DECISIONS #101 already recorded a dependency's internal
representation as the mechanism by which this gate silently regressed once. DECISIONS #106.

**Regression tests.** Five `test_f43_*`. The two asserting the new refusal were **proven to fail on
the unmodified tree** by deleting only the refusal branch:

```
FAILED test_f43_a_known_shape_with_no_methods_is_refused          — DID NOT RAISE
FAILED test_f43_clearing_methods_after_registration_is_refused_too — DID NOT RAISE
2 failed, 3 passed
```

The other three pass on both trees, and that is correct: they are controls, and a control that
changed with the fix would not be controlling anything.

---

## 3. The correction to v0.7.5's completeness claim

`SECURITY-REVIEW-0.7.5.md` §3 states:

> *"every object on `app.routes` is either checked or refused; none is skipped"*

**That was true of shapes and false of methods.** F42 made the shape half complete; the method half
was never examined, and the sentence claimed both.

Per the build prompt, `SECURITY-REVIEW-0.7.5.md` is **not edited** — records are not rewritten — and
the correction is issued here. The claim now reads:

> **Every object on `app.routes` is either checked against both authorization tables for every verb
> it carries, or refused — as an unknown shape, or as a known shape carrying no verb to check.**

This is the **third** consecutive release to narrow a completeness claim about this gate (F40 → F41
→ F42 → F43). That pattern is itself a finding and is §8's first critical note.

---

## 4. The dataset as an ungoverned corpus

The release's real security object.

> Capture runs **engine-side**, where visibility scoping does not exist and **must not** —
> correlation learns across the whole estate, and a correlator that saw only one operator's NEs
> would correlate worse. The corpus therefore holds every NE, entity, address, OID and raw varbind
> in the network, **ungoverned by any scope policy**.

That is not a defect to fix by scoping the capture: scoping it would either corrupt correlation or
produce a quieter lie. It is a **bypass of the visibility model**, and the response is to treat it
as one.

### Every read path, enumerated

| Path | Access | Evidence |
|---|---|---|
| `GET /api/dataset/retention` | `config.read` → **admin_only** | `rbac.ROUTE_PERMISSIONS`, `rbac.ROUTE_SCOPE`, `test_retention_routes_are_admin_only` (viewer and editor → 403) |
| `POST /api/dataset/retention` | `config.write` → **admin_only** | as above |
| `python -m netcorenoc dataset bias` | **no HTTP surface at all** | a CLI subcommand; filesystem access to the database is already full compromise |
| `python -m netcorenoc dataset stats` | as above | as above |
| Any other route | **none touch dataset rows** | no `dataset_*`, `capture_run` or `feedback_member` reference exists in any route module outside `routes_admin.py` |

`test_the_three_postures_are_all_populated` pins the admin-only count at 24 (was 22), so adding a
route without a posture fails, and the declaration gate refuses one without a declaration.

### The bias report emits aggregates only

`test_the_report_emits_aggregates_only` asserts that **every** NE address, OID and operator name in
the fixture is **absent** from the rendered output. The report may say *how many*; never *which*.

### Export

**There is none**, and that is the safest available answer. If one is ever added, §5a of the
specification binds it: `config`-class, admin-only, audited, and treated exactly as the audit export
is.

### Residual, stated

An **admin** can read the whole corpus, and admin is never scoped. That is unchanged from every
other admin capability in this product and is the documented posture (DECISIONS #58). The dataset
raises the *value* of an admin compromise without changing its *reachability*.

---

## 5. The client-reported fingerprint as hostile input

A new field on the write path that already produced **F34**, **F35** and **F39**. Three properties,
each tested:

**Bounded.** `ClientFingerprint.accept` truncates at `MAX_CLIENT_MEMBERS` (512), and the truncation
is **recorded** on the row (`client_truncated`) rather than silently applied — a silence would make
the bound itself invisible in the data. Pydantic's `max_length=4096` is a *parse* bound set well
above the truncation point, so the rejection path never fires in practice.

**Never rejected.** Rejection is the wrong primitive for an observation (§2.1, one level down).
`test_the_client_fingerprint_never_rejects_anything` posts `-1`, `0`, `2**62` and a nonexistent id:
all accepted, all recorded as reported.

**Not an existence oracle — the one that matters.**
`test_the_client_fingerprint_is_not_an_existence_oracle` posts a real alarm id and a nonexistent one
as a **scoped editor** and compares all three channels:

| Channel | Result |
|---|---|
| status | **200 both** |
| body | **identical** (modulo the verdict string the caller chose) |
| timing | **\|Δt\| < 0.25 s** |

The timing property is structural rather than tuned: **the write path never looks the id up**, so
there is no query whose cost could differ. `test_the_client_fingerprint_is_recorded_verbatim_including_unknown_ids`
proves the storage side — a nonexistent id is stored, not filtered, which a validating
implementation could not do.

This is the discipline F34 established for situations and F37 for label targets: "no such thing" and
"not yours" are one code path, one status, one body, one timing.

---

## 6. The write path

**F34/F35/F37/F39 regression tests pass unedited.** `tests/test_findings.py` and
`tests/test_abuse.py` are untouched by this release.

The new write obeys the v0.7.1 perimeter:

| Requirement | How |
|---|---|
| Scope-checked | the feedback route's `situation_in_scope` gate is unchanged and still precedes everything; `redacted_member_count` reuses the **same** `scope.allows_ne` the visibility decision uses, so the record cannot disagree with what the operator was shown |
| Audited | `retention.preview` and `retention.change`, both with before/after; the feedback audit row is unchanged |
| Transaction-disciplined | every new write is inside `write_txn()`; the retention delete is inside the same transaction as its audit row and **after** it, so a crash mid-delete leaves the intent on record |
| Bounded | pair rows bounded by `MAX_CANDIDATES` per activation; the client report bounded at 512; the sink bounded by age **and** a row cap |
| One connection, one lock | `tests/test_store_concurrency.py` unchanged and green; no method in `store/dataset.py` takes `store.lock` |

---

## 7. Ingestion

**Measured, not asserted** (`docs/gates/v0.8.0-phase-6.md` §3):

```
added rows per trap    : 62.24
added bytes per trap   : 6929
database growth factor : 9.88x
replay wall-time delta : +31.5%
```

**A capture failure degrades capture and never ingestion.** `test_a_capture_write_error_degrades_capture_and_never_ingestion`
injects `sqlite3.OperationalError` into `store.add_pairs`: **40 of 40 traps still land**,
`engine.db_errors` stays **0** (a capture error is not a batch loss), `capture.errors` rises, and the
operator warning says *"Ingestion was unaffected."*

The alternative — letting the error propagate — would roll back the **whole batch**, so one
malformed varbind blob could cost 500 traps. That is prime directive 1 dying by an indirect route,
and it is the specific failure this release was warned about.

Capture is also **switchable off** (`test_capture_can_be_switched_off_and_writes_nothing`), and
`engine.py` contains **no capture logic at all** — enforced by
`test_the_engine_holds_no_capture_logic`, which forbids any SQL statement or dataset table name in
that file and was verified non-vacuous by injecting `INSERT INTO dataset_pair` and observing red.

---

## 8. Retention — the product's first destructive control

Every other admin configuration in NetCoreNOC is reversible: `scorer_config` is append-only and
rollback is a pointer move; governance policies are versioned. **Lowering retention deletes rows and
there is no rollback for a `DELETE`.**

| Control | Implementation |
|---|---|
| Preview before apply | `preview` defaults to **True**; the destructive branch needs `preview=false` sent deliberately. Bounded, read-only, deterministic — the `preview.py` discipline |
| Audited as its own action | `retention.preview` **and** `retention.change`; the *look* is on record, not only the destruction |
| Never applied silently by the loop | the maintenance loop prunes **only the sink**; `test_the_maintenance_loop_prunes_the_sink_and_never_the_dataset` |
| Ordering fail-closed | `sink < training ≤ audit` with a **precise reason** naming the offending tier |
| Recorded as provenance | the policy in effect is on `capture_run` |

**A real defect found during Phase 6 and fixed.** `vulture` flagged `store.prune_dataset` as having
no caller — which is exactly what an unwired destructive path looks like. Applying a reduced
retention stored the policy, audited the change, and **deleted nothing**. An operator would have set
three months, seen the count, confirmed, and kept twelve. The dead-code guard caught a security-
relevant bug that no security test was looking for.

**Residual, stated.** An admin who reads the count and proceeds **can still destroy the corpus
deliberately**. The control is *preview plus audit*, not prevention — exactly as it is for scorer
parameters. That is the correct posture for this product and it is not a gap.

---

## 9. Critical analysis

### 9.1 This is the fourth consecutive release to narrow a completeness claim about one guard

F40 (registration paths) → F41 (the non-`/api` exemption) → F42 (route shapes) → F43 (empty method
sets). Each fix was correct; each accompanying claim was **broader than what had been checked**, and
the next release found the gap.

The pattern is the finding. The guard is now complete along three axes — *how a route is
registered*, *what shape it is*, and *what verbs it carries* — and the honest statement is that
**nobody has enumerated the axes**. A fifth may exist. What would actually close this is a property
test over the built app asserting that every reachable `(method, path)` pair the ASGI router will
dispatch appears in the authorization tables — dispatch-derived rather than attribute-derived. That
is a real piece of work and it is not in this release; it is on `docs/ROADMAP.md`.

### 9.2 What the bias report cannot see

The report measures the dataset. It cannot measure:

* **Whether a verdict was correct.** There is no ground truth, only what an operator said. Every
  figure describes the *labels*, never the *network*.
* **Selection into the dataset at all.** Operators label what they open, and what they open is
  driven by the UI's ordering, which is driven by the correlator. The dataset is a sample of *what
  the current scorer surfaced*, and no amount of measuring the sample reveals the sampling.
* **Which `legacy_capture` rows are actually bad.** Nothing recorded it. They are marked, not
  diagnosed, and that is precisely why they are marked rather than deleted.
* **Pairs never evaluated.** They are absent by construction. `partial` coverage counts the gap; it
  cannot characterise it.

The report says all four of these in its own closing section, kept from deletion by
`test_the_report_says_what_it_cannot_tell_you`. A dataset whose limits are not written down invites
a later reader to assume it has none, which is how a measured bias becomes an unmeasured confidence.

### 9.3 Which schema decision I most expect v0.9.0 to want changed

**The observation row's `varbinds` blob, stored as opaque JSON.**

Rule 1 says store the raw material and let the release that models decide what a feature is, and
that is right. But v0.9.0 will want to *query* it — "pairs where both sides carry an interface
index", "pairs sharing an enterprise OID" — and a JSON string in SQLite supports none of that
without a full scan of the whole corpus.

The likely v0.9.0 request is a normalised `observation_varbind(observation_id, oid, value)` child
table, or a generated column over the JSON. Both are additive migrations, so **nothing is lost** —
which is the test the decision has to pass, and it passes. Recorded here so v0.9.0 does not treat it
as a defect in this release's judgement: it is a deliberate deferral of a *modelling* decision, and
the raw material is captured either way.

Second most likely: `incumbent_linked` being on the pair row at all, rather than in a
`champion_decision` side table. The current placement is right for provenance and makes the schema's
"no target column" claim depend on a *comment* rather than on structure. A future release that wants
that claim to be structural would move it.

### 9.4 Is the sink's dual bound actually sufficient? — measured, and the answer is uncomfortable

**The row cap binds long before the age limit at every realistic traffic rate.** At the measured
62.24 rows/trap against the 2 000 000-row default:

| traps/s | cap binds after |
|---|---|
| 0.1 | 3.7 days |
| 1 | 8.9 hours |
| 10 | 0.9 hours |

The **21-day setting is close to decorative**. One 200 000-trap storm produces 12.4 M rows — 6.2× the
cap on its own.

Two honest caveats in *both* directions:

* Phase 0's corpus is **storm-heavy by construction** (86 % storm, median 100 candidates) and
  **overstates** rows/trap for a quiet deployment. `background_noise` runs at a median of 12
  candidates, so a steady-state regional ISP might see 5–10× less.
* Even at 6 rows/trap and 1 trap/s the cap still binds at ~3.9 days — **under the 21-day default**.
  The conclusion survives the caveat.

**Raising the cap is not the fix**: 21 days at 10 traps/s needs ~1.1 billion rows, roughly 124 GB,
which is not an appliance default. The cap is a **disk budget** (2 M rows ≈ 220 MB) and the age limit
is a ceiling most installations never reach.

**The security consequence** is availability-shaped rather than confidentiality-shaped, and it is
real: a label arriving after the sink has evicted its pairs records `coverage: none`, so the corpus
is silently biased **toward situations labelled quickly**. Fast labels are not a random sample of
labels. The release makes this *visible* — per-label coverage, and `dataset stats` reporting the
**observed** window rather than the configured one — and does not claim to have solved it. A later
release with real label-latency data should retune the cap, which is exactly what §5c's latency
column exists to enable.

---

## 10. Mapping to `threat-model.md`

| Actor | Relevance |
|---|---|
| **A1** untrusted trap source | Unchanged. Capture is engine-side, downstream of parsing, quarantine and the allowlist. A hostile trap can inflate the sink, which is what both bounds exist for. |
| **A2** authenticated low-privilege user | The new field is the exposure, and §5 closes it: bounded, unrejected, and not an existence oracle in status, body or timing. No dataset read below admin. |
| **A3** malicious/careless contributor | F43's primary actor. Also `test_the_engine_holds_no_capture_logic`, which stops persistence accreting on the ingest path. |
| **A4** network attacker | Unchanged. No new transport, no new listener. |
| **A5** compromised admin | **Raised in value, not in reachability.** The corpus is a richer prize and retention is a destructive control. Mitigation is preview plus audit, not prevention. |
| **A6** operator error | The retention preview, and the `dataset stats` window figure, both exist for this actor. The unwired-delete bug (§8) would have been an A6 disaster. |

---

## 11. Verdict

**One finding, F43, fixed and regression-tested, with two reachable construction paths demonstrated
and two unreachable ones asserted.** One completeness claim corrected. The finding series stands at
**F43**; the next review continues from **F44**.

The release adds a corpus that is a scope bypass by construction and treats it as one: admin-only on
every path, aggregates only from the report, no export, and a capture path that cannot fail
ingestion. It adds the product's first destructive control and builds it with preview, audit, and a
background loop that is structurally forbidden from destroying labels.

**The two things a reader should carry away** are in §9.1 and §9.4: this is the fourth consecutive
narrowing of one guard's completeness claim and nobody has enumerated its axes; and the sink's row
cap — not its documented 21-day window — is what governs, which biases the corpus toward
quickly-labelled situations in a way this release measures and does not fix.
