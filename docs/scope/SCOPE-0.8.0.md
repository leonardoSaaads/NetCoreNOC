# SCOPE — NetCoreNOC v0.8.0

**Theme: the scoreboard — capture the operator feedback as a durable dataset, and measure its bias.
It trains nothing.**

This is the most consequential release the project has shipped, and the reason is not its size. Every
ML release from v0.9.0 to v0.13.0 consumes what this one captures, and **capture is irreversible**:
`A` and `E` decay continuously, `alarm` is deduplicated and mutated on re-fire, and situations are
merged and lose their membership. A field not captured at the moment of decision is not captured
late — it is captured **never**. A schema mistake here does not fail loudly; it produces a model
that is confidently wrong, discovered several releases later, if at all.

That asymmetry runs the **opposite way** from every previous release of this project, and it inverts
the usual decision rule:

> An unneeded column costs bytes. A missing one costs the field forever.

The runtime identity is unchanged: one Python 3.12 asyncio process, one SQLite (WAL) file, one
static UI of four files, environment variables only, no build step, **zero new runtime
dependencies** (five, unchanged), **zero new dev dependencies** (eleven, unchanged), **exactly one
new migration** (`0008`), and **one** `ui/app.js` change — the optional fingerprint field, nothing
else.

All prior scope documents and their invariants still hold; `docs/security/threat-model.md` keeps the
authority it has held since v0.2.0. On a conflict, this document wins on *scope*, the build prompt
wins on *process and quality*, the threat model wins on *security posture*,
[`../architecture/MODULE-ARCHITECTURE.md`](../architecture/MODULE-ARCHITECTURE.md) wins on
*placement*, [`../architecture/ROADMAP-0.8-TO-0.13.md`](../architecture/ROADMAP-0.8-TO-0.13.md) is
the binding sequence, and
[`../architecture/FEEDBACK-DATASET-0.8-DRAFT.md`](../architecture/FEEDBACK-DATASET-0.8-DRAFT.md) —
**as corrected in Phase 1 of this release** — is the binding specification.

**Delivery model (unchanged).** The repository is read-only to automation: the maintainer takes the
resulting archive and pushes it by hand. No step depends on pushing, on CI running, or on any
external account, registration, or dashboard action. Every gate is local and reproducible
(`make qa`, `make eval`, `make bias-report`, `docker compose config`, a locally built wheel).

---

## 1. In scope — exactly seven workstreams, and nothing else

### 0. F43 — the residual fail-open in the declaration gate, closed *before* any route is added

`assert_every_route_is_declared` refuses unknown route *shapes* (F42, `declare.py:202`), but within
a known shape it iterates `route.methods` (`declare.py:217-221`). A route whose `methods` is
**empty** produces zero iterations, so it is **neither checked nor refused** — and Starlette does
not filter by verb when `methods` is falsy, so such a route serves every verb.

Reproduced by execution in [`../gates/v0.8.0-phase-0.md`](../gates/v0.8.0-phase-0.md) §7:

```
Route("/admin/backdoor", ep, methods=[])  → the gate passes
   GET 200 · POST 200 · PUT 200 · DELETE 200 · PATCH 200 · HEAD 200 · OPTIONS 200
```

Reachability, recorded honestly: `DeclaredRoutes` **cannot** produce it, `add_api_route(methods=[])`
is refused by FastAPI's own assertion, and the two **reachable** paths are
`router.routes.append(Route(..., methods=[]))` and clearing `methods` after registration. Latent,
exactly like F40, F41 and F42 before it.

**The fix:** an empty method set is an **unverifiable** route and is refused, by the same logic that
refuses an unknown shape. This corrects the v0.7.5 claim *"every object on `app.routes` is either
checked or refused; none is skipped"*. `SECURITY-REVIEW-0.7.5.md` is **not edited** — records are
not rewritten — and the correction is issued in this release's review.

**It is first because this release adds routes.** A guard is worth having before the surface it
guards grows, not after.

### 1. Correct the specification, before any schema work

`FEEDBACK-DATASET-0.8-DRAFT.md` is good and it contains one sentence that, taken at face value,
produces the wrong model — plus five things the schema depends on that it does not say. It becomes
the document v0.9.0 through v0.13.0 are briefed from, so a wrong sentence here propagates for five
releases. Eight corrections, applied in place and dated, in the project's supersede-never-rewrite
style. Full detail in [`../gates/v0.8.0-phase-1.md`](../gates/v0.8.0-phase-1.md).

### 2. The schema and migration `0008`

The deliverable. Two populations with **different semantics**, not merely different lifetimes:

| | the sink | the dataset |
|---|---|---|
| Contains | rows whose destiny is **unknown** | rows whose destiny is **known** |
| Grows with | **traffic** | **labels** |
| Bounded by | time **and** a row cap, whichever binds first | a retention policy, admin-set |

**The dataset grows with labels, not with traffic.** A 200 000-trap storm on an OLT fills the sink
and does not move the dataset. That is bounded growth *by construction* rather than by a configured
number — which matters because no correct traps/day figure exists across the deployments this
product targets.

One row per **evaluated** pair (linked and rejected alike, before `MAX_LINKS_PER_ALARM`), one
immutable row per alarm **observation**, a two-part membership record, and a capture-context table.
Every column justified against one rule — *store what cannot be recomputed; derive what can* — and
one prohibition — *keys are not features*.

**There is no target column in the pair table.** The only label lives in `feedback`, and reaching it
requires the join. That is deliberate structural friction, and it is what protects against a future
reader's good faith.

### 3. Capture

`correlate.py` changes in **exactly one way — it returns more.** The grouping decision does not
move: `make eval` byte-identical, `score_link`'s function body hashing identically, the `links` list
identical member for member in order. Capture runs **engine-side, under the batch lock**, where
correlation already runs; nothing is added to `receiver.datagram_received`. A capture failure
degrades capture and is recorded — it can never fail ingestion, exactly as `SafeScorer` degrades
scoring.

Promotion on label records **per-label coverage** in three cases — all found, some found, none
found. Case 2 is not an edge case: the sliding window and `MAX_CANDIDATES` mean pairs within a
labelled situation **may never have been evaluated**, and a naive join would drop part of the bag
silently.

### 4. Retention — the first destructive control in the product

Three tiers, ordered, admin-configurable, `sink < training ≤ audit` enforced **fail-closed**.
Defaults 21 days / 12 months / 24 months, and **they are defaults, not policy**.

Every other admin configuration in NetCoreNOC is reversible — `scorer_config` is append-only,
RBAC and scope policies are versioned. **Lowering retention deletes rows, and there is no rollback
for a `DELETE`.** So reduction reuses the pattern v0.6.0 built for exactly this class of decision:
**preview before apply** (bounded, read-only, deterministic, admin-only — the `preview.py`
discipline), **audited as its own action**, and **never applied silently by the maintenance loop**
before the operator has seen the count.

Capture ships **on by default**, because a deployment that discovers this dataset's importance six
months in has lost six months that cannot be reconstructed.

### 5. The bias report

A CLI subcommand and a `make` target beside `make eval`. **Deterministic, and therefore a gate**:
run over a fixture in `make qa` with its output compared byte-for-byte, it fails the suite the day
capture changes shape. A UI card would never have that property.

It reports **effective sample size stated as such** — *n* is the number of independent **bags**, not
the number of pairs — the bag-size distribution with its interpretation, operator concentration,
scope-restricted labels, latency, coverage three ways, `legacy_capture` versus `current` **never
averaged**, the server/client membership divergence rate, leakage exposure, and retention state.

**It emits aggregates only.** No model, no fit, no split, no prediction, no recommendation.

### 6. Security review and the v0.9.0 specification

`SECURITY-REVIEW-0.8.0.md` continuing from **F43**, and `SHADOW-MODE-0.9-DRAFT.md` implementing
nothing.

---

## 2. Out of scope — deferred, each with the reason

1. **Any model, of any kind, in any form, however small.** No fit, no weight update from labels, no
   train/test split, no model file, no `scikit-learn`, no `numpy`. Shadow training is v0.9.0. *If
   this release contains a fit, it has failed.*
2. **The label-derivation policy** — how a bag verdict becomes pair labels. Deliberately left open
   so v0.9.0 can **evaluate** candidate policies instead of inheriting an assumption. Capturing the
   bag is what keeps that door open; deriving now closes it permanently, and invisibly.
3. **The train/test split.** v0.10.0's. This release records what makes *any* split possible later
   — time, lineage, operator, incident — and assigns **nothing**.
4. **Active learning / soliciting labels.** v0.9.0 or later. This release records the *acquisition
   channel* column so organic and solicited labels stay separable, and writes only `organic`.
   Without the column, a later solicitation would destroy the bias characterisation
   **retroactively**, for rows already written.
5. **Optimistic-concurrency rejection on the feedback endpoint.** Rejected with reasoning in the
   draft's §2.1 — rejection is right for *edits* and wrong for *observations*, and in a system that
   updates every two seconds it is a livelock that would make the acquisition path worse than the
   bug v0.7.5 fixed. Recorded so it is not reintroduced as an obvious improvement.
6. **Removing or changing `MAX_CANDIDATES` / `MAX_LINKS_PER_ALARM` / `LEARN_CAP`.** They bound work
   on the ingest path. The dataset observes the uncensored result *before* truncation; the engine's
   own persistence is unchanged.
7. **A UI for the dataset, the bias report, or retention.** The report is CLI. The one permitted
   `ui/app.js` change is the optional fingerprint field on the feedback POST — no new panel, no new
   card, no restyling. The UI rebuild is a later release and is where the report becomes a screen.
8. **The partial-split affordance** (*"these three yes, that one no"*). **The single
   highest-leverage UI change for the whole ML roadmap**, and out of scope because this release
   permits exactly one UI change. A `docs/ROADMAP.md` line and a note in the v0.9.0 spec.
9. **SNMPv3, `/metrics`, pcap replay, outbound webhook / `Case` JSON emission.** Still out,
   unchanged.

---

## 3. What this release must not break

`make eval` byte-identical (`c2e8a0ce…`); the F12/F13 wheel-and-sdist shipping test; the d3
checksum; the scorer parity gate; `mypy --strict`; the v0.7.2 route-order and handler-hash tables;
the v0.7.3 `Store` method hashes and the **one-connection/one-lock** invariant; the layer test; the
module-size guard with `DEBT_ALLOWLIST` empty and `COHESION_EXEMPT` at one entry; the declaration
gate; the documentation-consistency guard; the F34/F35/F37/F39 write-perimeter regressions
**unedited**; and coverage at or above the v0.7.5 figure of **95.89 %**.

`engine.py` is at **542 lines against a recorded ceiling of 542** — zero headroom. Capture code
therefore lives in its own module and `engine.py` gains **call sites only**. The `COHESION_EXEMPT`
entry covers the ingest *reasoning*, not any code that happens to land nearby.

**Do not touch:** `scoring.py`'s contract, `learn.py`'s behaviour, `rbac/`, `shaping/`,
`receiver.py`, `preview.py`, `rootcause.py`, `severity.py`, or any existing migration.

---

## 4. The three invariants that outlive this release

Stated here because they are what the schema is *for*, and because each is easier to violate than
to notice.

1. **The machine's decision is never a stand-in for a human verdict.**
   > No metric that decides promotion may be computed against `incumbent_linked`.

   The column is legitimate — as provenance, as context, as a feature, and as the basis of
   champion/challenger comparison. Being **judged** by it is circular, and the challenger's ceiling
   becomes the champion's performance.

2. **Only a human verdict is a label.** Not the scorer's output, not a derived pair, not an
   inference. Where the release was unsure whether something counted as a label, it did not.

3. **The dataset is a scope bypass by construction, and is admin-only everywhere.** It is captured
   engine-side, where visibility scoping does not exist and must not — correlation learns across the
   whole estate. **No read of dataset rows below admin, on any route, in any format, ever.**
