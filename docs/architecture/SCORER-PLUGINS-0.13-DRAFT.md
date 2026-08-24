# Customer-supplied scorers — v0.13.0 draft (specification only, not implemented in v0.6.0)

<!-- release-claim: v0.15.0 = external-cartridge -->

> ## ⚠ Resequenced 2026-07-31 (v0.7.4) — read this box first
>
> This document was written during **v0.6.0** as the **v0.8.0** specification, and was refined
> during v0.7.0. It was `git mv`-d from its old name, SCORER-PLUGINS-0.8-DRAFT, and its elements retagged
> `v0.15.0: planned` by v0.7.4. **The technical content below is unchanged** — the analysis, the
> ONNX determinism engineering, the threat-model entries and the R1–R5 refinements all stand. Only
> the release changed.
>
> | What | Then | Now |
> |---|---|---|
> | Release | v0.8.0 | **v0.13.0** — see [`ROADMAP-0.8-TO-0.13.md`](ROADMAP-0.8-TO-0.13.md) |
> | Why | — | **DECISIONS #93.** v0.8.0 is the operator-feedback dataset; customer models come *after* the champion/challenger framework they plug into, because building the riskiest surface before the framework that receives it inverts how this project has sequenced every release since v0.2.0. |
> | §1 blessed ONNX path | tagged for v0.8.0 | **`v0.15.0: planned`** — unchanged in substance |
> | §2 Python entry-point escape hatch | tagged for v0.8.0 | **REJECTED, not deferred** (DECISIONS #93). See the note on §2. |
> | §R2 worker-process preemption harness | blocking prerequisite | **still a blocking prerequisite** |
>
> **Why the entry-point hatch is rejected rather than postponed.** ONNX is *data executed by a
> pinned runtime*; an entry-point scorer is *arbitrary code running as the process*, holding the
> process's database handle and its network. Every modern framework exports to ONNX, so the hatch
> buys reach the project does not need at a trust cost it should not pay. It is recorded as a
> rejection — the same treatment DECISIONS #44 gave the external-criterion API — so that nobody
> reintroduces it later as an obvious convenience. §2 is preserved below as the historical record of
> a path that was specified and then declined, **not** as a plan.

This document specifies how **v0.13.0** will let an operator run a **customer-supplied model** as
the link scorer, under the `LinkScorer` contract that **v0.6.0 ships**. **It implements nothing.**
Every element below is tagged **`v0.15.0: planned`**.

It supersedes the "pluggable" half of section 3 of `EXTENSIBILITY-0.6-DRAFT.md`. The resequencing
is **DECISIONS #43**: customer models introduce a new runtime dependency (`onnxruntime`) and a new
trust surface (operator-supplied code), whose review is about sandboxing, determinism, and
resource control — a different review from v0.6.0's parity-and-parameters review, and a different
one again from v0.7.0's authorization-perimeter review.

**DECISIONS #44 still binds**: no scorer, of any kind, makes an outbound call to decide a link.
Customer models run **in-process, inference-only, engine-side**, with the same fail-safe discipline
as the built-in scorer. There is no sidecar and no scoring API.

## Two paths, deliberately unequal

| | **Blessed ONNX path** | **Python entry-point escape hatch** |
|---|---|---|
| Artifact | a frozen `model.onnx` | an installed package exposing `netcorenoc.link_scorers` |
| Trust | data, executed by a pinned runtime | **operator-trusted code, executed as code** |
| Dependency | `onnxruntime`, an **optional extra** | none beyond the operator's own package |
| Determinism | engineered (see below) | the operator's problem, bounded by the harness |
| Recommended | yes — this is the supported path | last resort, documented as such |

The asymmetry is the point. The ONNX path is the one an operator should take; the entry-point
hatch exists so that "my model does not export to ONNX" is not a dead end, and it is guarded and
labelled accordingly.

---

## 1. The blessed ONNX path (`v0.15.0: planned`)

### Workflow

Train anywhere, in anything. Export a **frozen** `model.onnx` whose inputs are the v0.6.0
`LinkFeatures` fields the operator declares, and whose outputs are a score plus per-feature
attributions. Register the artifact through the admin scorer surface: NetCoreNOC hashes it
(SHA-256), records `scorer_id`, `contract_version`, the artifact hash, the input signature, and
the opset in an immutable `scorer_config`-family row, and only then may it be activated.

### The adapter's fixed execution posture

`OnnxScorer` is an adapter that satisfies `LinkScorer` and nothing more. It runs:

- **inference only** — `onnxruntime.InferenceSession.run`; no training, no gradient, no file or
  network access from the model graph;
- **FP32**, **single-thread** (`intra_op_num_threads = 1`, `inter_op_num_threads = 1`),
  **CPU execution provider only**;
- against a **pinned opset**, refusing any artifact declaring an unsupported one;
- on an artifact whose **SHA-256 is recorded and re-verified at load**, so a swapped file is a
  refusal, not a silent behaviour change;
- with a **wall-clock timeout per batch** and the mandatory fail-safe fallback (below).

### Determinism is engineered, not assumed

This is the paragraph the review will be graded on. Floating-point addition is **not
associative**, so the same ONNX graph can produce different last-bit results across runtimes,
BLAS/oneDNN builds, thread counts, and CPU microarchitectures. A reduction summed in a different
order gives a different `float32`, and a score `0.4999999` versus `0.5000001` at the threshold is
a *different grouping*.

The controls are therefore:

- **Single-thread, one provider, pinned opset, FP32** — removes thread-count and provider
  scheduling as sources of reorder.
- **A recorded reference vector.** Registration stores the model's output on a fixed set of
  synthetic `LinkFeatures`; the adapter recomputes it at load and refuses to activate on a
  mismatch beyond a declared tolerance. A model that cannot reproduce its own reference on this
  host cannot become authoritative on this host.
- **Provenance carries the runtime.** The `onnxruntime` version and the artifact hash are part of
  the persisted configuration, so "which parameters formed this situation" (v0.6.0's provenance
  question) stays answerable when the answer is "this model, on this runtime".
- **The honest statement**: NetCoreNOC guarantees *reproducibility on a given host and runtime
  version*, not bit-identical scores across arbitrary hosts. Anything stronger would be a lie, and
  the eval gate — which is byte-identical by design — therefore continues to run against the
  **built-in `AdditiveScorer`**, never against a customer model.

### `onnxruntime` is an optional extra, never a base dependency

```toml
[project.optional-dependencies]
onnx = ["onnxruntime>=1.18"]
```

The base install stays exactly as it is today. Absent the extra, the ONNX adapter is not
importable and the admin surface reports it unavailable — it does not crash, and it does not
attempt an install. NetCoreNOC's zero-configuration identity is that the default just works; an
operator who wants a neural scorer opts into one package.

---

## 2. The Python entry-point escape hatch (**REJECTED 2026-07-31 — see the box at the top**)

> **This path is not planned for any release.** DECISIONS #93 rejected it rather than deferring it,
> for the reason the top box states: it executes operator-supplied *code* as the process, where the
> ONNX path executes *data* under a pinned runtime. The section is preserved unedited below because
> the analysis of what it would have cost is the argument for declining it, and deleting the
> analysis would leave only the conclusion. Read it as a record, not a specification.

### Discovery

Standard `importlib.metadata` entry points in the group **`netcorenoc.link_scorers`**. An
installed distribution declares:

```toml
[project.entry-points."netcorenoc.link_scorers"]
my_scorer = "mypkg.scoring:MyScorer"
```

Discovery is **enumeration only** at startup; an entry point is *listed*, never loaded, until an
admin explicitly activates it (an audited `scorer.write`-class action). Nothing is imported by
merely being installed.

### Guards

- **Wall-clock timeout** per scoring batch; exceeding it is a fallback event, not a stall.
- **`resource.setrlimit`** (address space and CPU) applied around the scorer's execution, so a
  runaway plugin degrades to the default instead of taking the process with it.
- **Mandatory fail-safe fallback** to the coded-default `AdditiveScorer` on any exception,
  timeout, contract violation, or malformed `LinkScore` — audited `scorer.fallback`, exactly as
  v0.6.0 already does for the built-in path.
- **Engine-side only.** Like every scorer, it runs under the engine batch lock, never in
  `receiver.datagram_received`.

### This is **not** a security sandbox — stated plainly

Loading an entry point executes the operator's code **in the NetCoreNOC process, with the
NetCoreNOC process's privileges**. `setrlimit` and a timeout bound *resource* misbehaviour; they
do not contain a hostile plugin, which can read the database file, the environment, and the
network exactly as the process can. The trust model is therefore explicit: **a plugin is as
trusted as the operator who installed it**, identical to trusting anything else on the host's
`PYTHONPATH`. This is why the ONNX path is the blessed one — data is a smaller thing to trust than
code — and why the documentation must say "self-hosted, operator-trusted deployments only" rather
than implying isolation the design does not provide.

---

## 3. The v0.6.0 contract accommodates both, without a breaking change

Confirmed against the shipped v0.6.0 interface:

| v0.6.0 element | Why it already fits v0.13.0 |
|---|---|
| `LinkScorer` is a `Protocol` | An adapter class satisfies it structurally; no base class to inherit, no registry to modify, no `src/netcorenoc/` change needed to *accept* a second implementation — proven in v0.6.0 by the test-only alternate scorer. |
| `scorer_id: str` | `"onnx:<name>"` / the entry-point name; already the identity column in `scorer_config`. |
| `contract_version: str` | An adapter declares `"1.x"`; activation refuses an unsupported **major** (DECISIONS #49). |
| `params_fingerprint() -> str` | The ONNX adapter returns the artifact SHA-256 (plus runtime version); the entry-point adapter returns a hash of its declared configuration. Provenance by reference (DECISIONS #47) then works unchanged. |
| `LinkFeatures` reserved optional slots | `severity_i/j`, `topo_distance`, `probable_cause_i/j`, `event_type_i/j` are already present and `None`; populating them is a **minor** bump, so a richer scorer is additive. |
| `LinkScore.terms` is variable-length | A model with five feature attributions emits five `TermContribution`s. The three-column `link` persistence is the *default scorer's* projection (DECISIONS #50); generalising persisted attribution is a v0.13.0 schema task, named here. |
| `score()` is pure, deterministic, side-effect-free, inference-only | Exactly the ONNX and plugin execution posture; it also forecloses the rejected outbound-call design at the type level. |
| Fail-safe fallback + `scorer.fallback` audit action | Already shipped and tested in v0.6.0 against a deliberately-raising test scorer; v0.13.0 reuses it verbatim. |
| Append-only `scorer_config` + active pointer + rollback | An ONNX or plugin activation is one more immutable row; rollback to the built-in default remains a single pointer move. |

**Explainability is preserved even for a neural model.** Emitting `LinkScore.terms` is
*contractual*, not optional: an ONNX artifact must declare an attribution output (e.g. per-feature
contributions from the exported graph), and an entry-point scorer that returns an empty `terms`
list is a contract violation and therefore a fallback event. A scorer that cannot say why is not a
scorer this project will run.

### The one schema change v0.13.0 will need

`link.term_t/term_a/term_e` are three fixed columns. v0.13.0 adds a general per-link attribution
store (a `link_term` child table, or a JSON column) written *only* when the active scorer emits
something other than the three default terms, so v0.6.0-shaped rows stay byte-identical and the
migration is additive and forward-only — the same discipline as `0003_entity.sql` and
`0005_scorer_config.sql`.

## Threat-model entries v0.13.0 must add

- **Arbitrary code execution via an entry-point plugin** — *control*: enumeration-only discovery,
  explicit audited activation, documented "operator-trusted, not sandboxed" trust model, timeout +
  `setrlimit` + fail-safe fallback; *test*: an installed-but-not-activated entry point is never
  imported; a raising/hanging plugin degrades to the default and audits `scorer.fallback`.
- **Model-artifact tampering** — *control*: SHA-256 recorded at registration and re-verified at
  load, refusal on mismatch, hash in provenance; *test*: a mutated artifact refuses to activate.
- **Non-determinism as a correctness hazard** — *control*: single-thread/FP32/CPU/pinned-opset,
  recorded reference vector re-checked at load, provenance records the runtime version, eval gate
  stays on the built-in scorer; *test*: a model failing its reference vector cannot be activated.
- **Resource exhaustion via a customer model** — *control*: per-batch wall-clock timeout,
  `setrlimit`, engine-side execution only (never `datagram_received`), fallback on breach; *test*:
  ingestion stays lossless while a pathological scorer is active.
- **New dependency surface (`onnxruntime`)** — *control*: optional extra, absent from the base
  install and the default image; `pip-audit` in `make qa` covers it when installed; *test*: the
  base install has no new runtime dependency and the adapter reports unavailable rather than
  raising.

---

# Refinement — recorded during v0.7.0 (still `v0.15.0: planned`, still implements nothing)

v0.6.0 shipped the `LinkScorer` seam; v0.7.0 shipped governance. Both are now real code rather than
a plan, so this section re-checks the specification above against what actually exists and records
three things it could not record before: a **blocking prerequisite**, a **governance
reconciliation**, and a **re-confirmation** that the shipped contract needs no breaking change.

Nothing here is implemented. Every element remains **`v0.15.0: planned`**.

## R1. The contract, re-confirmed against the shipped code

Re-read against `src/netcorenoc/scoring.py` as shipped in v0.6.0 (not against the v0.6.0 draft):

| Shipped element | Verified state | Verdict for v0.13.0 |
|---|---|---|
| `LinkScorer` | a `Protocol` with `scorer_id`, `contract_version`, `score()`, `params_fingerprint()` — structurally satisfied, no registry, no base class | **fits unchanged.** v0.6.0 already proves plurality with test-only alternate scorers. |
| `LinkFeatures` | a `NamedTuple` (DECISIONS #52), not a frozen dataclass, with the reserved optional slots present and `None` | **fits unchanged** — and better than specified: a `NamedTuple` is genuinely immutable, so an adapter cannot mutate the features it was handed. Adding a field keeps defaults and remains a **minor** bump (#49). |
| `LinkScore.terms` | a `tuple[TermContribution, ...]`, variable length, **contractual** | **fits unchanged.** A five-attribution model emits five terms; a scorer returning an empty `terms` is already a contract violation that triggers fallback. |
| `SafeScorer` | wraps the active scorer; on exception, contract violation, or over-budget it falls back to the coded defaults, audits `scorer.fallback` once, raises a persistent operator warning | **fits, with one gap — see R2.** |
| `scorer_config` + `scorer_active` | append-only rows, one-row pointer, rollback by pointer, `params_hash` + `contract_version` per row, provenance by reference on `situation` | **fits unchanged.** An ONNX or entry-point activation is one more immutable row. |
| `contract_version` gating | persisted per config; an unsupported **major** is refused | **fits unchanged.** |

**Conclusion: no breaking change to the v0.6.0 contract is required for either v0.13.0 path.** The
one schema change v0.13.0 still needs is the generalised per-link attribution store already named
above (`link.term_t/term_a/term_e` are the *default* scorer's projection, DECISIONS #50).

## R2. The worker-process preemption harness is a **blocking prerequisite** ⛔

This is the finding this refinement exists to record.

`SafeScorer` as shipped is a **post-hoc** guard. It measures each call *after the call returns* and
degrades the **next** one. Against the only scorer that exists today — five floating-point
operations — that is exactly right, and SECURITY-REVIEW-0.6 **F25** records it as **partial** with
the gap stated in the code, in `DESIGN.md`, and on the ROADMAP.

Against **untrusted operator code it is not sufficient**, and the difference is categorical, not a
matter of degree:

- A synchronous in-process call that **never returns** — `while True: pass`, a blocking socket
  read, a `time.sleep(1e9)` — is **not interruptible from the wrapper**. There is no next call to
  degrade. The engine batch loop is blocked, the queue backs up, and the ingest path — the one
  thing this project promises is lossless — starts dropping traps. A `signal.alarm`-based timeout
  does not fix this: it only fires on the main thread, does not interrupt a call blocked in a C
  extension (which `onnxruntime` is), and leaves the interpreter in a state the engine must not
  trust afterwards.
- The same call can exhaust **address space** before it exhausts time. `resource.setrlimit`
  applied in the engine's own process would bound the *whole appliance*, not the plugin.

Therefore, for v0.13.0:

> **A customer-supplied scorer — ONNX artifact or entry-point class — MUST execute in a separate
> worker process, under `resource.setrlimit` (`RLIMIT_AS`, `RLIMIT_CPU`) applied in the child after
> `fork`/`spawn` and before the scorer is imported, with a real wall-clock kill (`SIGKILL` after a
> grace `SIGTERM`) enforced by the parent. This harness is a BLOCKING PREREQUISITE: neither
> customer-scorer path may be merged before it exists and is tested.**

Design constraints the harness must satisfy, so it does not become its own hazard:

- **Batch-oriented, not per-pair.** The engine builds up to `MAX_CANDIDATES` (100) `LinkFeatures`
  per activated alarm. One IPC round trip per *batch*, never per pair — otherwise the seam's
  measured ~4.2 µs per pair (DECISIONS #52) is replaced by a context switch and the release trades
  a security property for an availability one.
- **The parent never blocks unboundedly.** The parent waits with a deadline; a breach kills the
  child and falls back to the coded-default `AdditiveScorer` **in-process**, audits
  `scorer.fallback`, and raises the persistent operator warning — the v0.6.0 discipline, reused
  verbatim, now with a fallback that actually *can* fire on a hang.
- **A dead worker is a fallback, not a stall.** Crash, OOM-kill, `RLIMIT_CPU` `SIGXCPU`, or a
  malformed response are all the same event: fall back, audit once, warn persistently.
- **Restart is bounded.** A worker that dies repeatedly must not be respawned in a hot loop; after
  a bounded number of failures the scorer stays degraded until an admin re-activates it.
- **Determinism is unaffected.** The worker is inference-only; the harness adds a process boundary,
  not a source of variation. The recorded reference vector (§1) is checked **in the worker**, on
  the host that will actually run it.
- **The ingest path still gains nothing.** The harness lives on the engine side, under the batch
  lock the engine already holds. `receiver.datagram_received` is untouched — F24's assertions
  remain in force and must be extended to name the harness.

**It still is not a sandbox, and the documentation must keep saying so.** `setrlimit` plus a kill
bounds *resource* misbehaviour. A hostile plugin in a child process can still read the database
file, the environment, and the network exactly as the parent can, unless the deployment adds
OS-level confinement (a dedicated uid, seccomp, a container boundary) that NetCoreNOC does not
provide and will not claim. **A plugin is as trusted as the operator who installed it.** This is
why the ONNX path is the blessed one: data is a smaller thing to trust than code.

## R3. Reconciliation with v0.7.0 governance

v0.7.0 makes capabilities and visibility admin-configurable. Applied to customer scorers:

- **Activating a customer scorer is `scorer.write` — admin-only, unchanged by v0.7.0.** It stays
  admin-ceiling, so no stored capability policy can move it down to editor or viewer: under the
  `ceiling ∩ policy` model (DECISIONS #53) a policy can only *remove* capabilities, never grant
  one the compiled map reserves for admin. The v0.6.0 decision that there is **no editor
  delegation** for the scoring seam (DECISIONS #43, F21) therefore survives governance
  structurally, not by convention.
- **A customer scorer is never scoped and never delegated.** Visibility scoping restricts *which
  resources a principal is shown*; a scorer is a **system-wide logic change** affecting how every
  alarm groups for everybody. There is no coherent "this scorer applies to my NEs only" — that
  would be per-tenant correlation, which is the tenant isolation v0.7.0 explicitly does not build.
  v0.13.0 must not introduce a per-scope scorer binding.
- **A scorer is not an authorization input.** As with the v0.6.0 parameter set (F21's
  `test_f21_config_change_grants_no_capability`), activating a model grants no capability to
  anyone. v0.13.0 must carry the analogous assertion for the plugin path.
- **The plugin runs engine-side, under the same fail-safe discipline as the built-in scorer, and is
  advisory of nothing on the datagram path.** DECISIONS #44 still binds: no scorer of any kind
  makes an outbound call to decide a link.
- **Governance audit actions do not extend to scorers.** `rbac.policy.update` and
  `scope.policy.update` cover the perimeter; a scorer activation stays `scorer.config.update`. Two
  catalogs would be a second source of truth.

## R4. `onnxruntime` stays an optional extra — re-confirmed

The base install is unchanged, and this is now a checked property rather than an intention: the
runtime dependency list has been **five** (`pysnmp`, `aiosqlite`, `fastapi`, `uvicorn`, `pydantic`)
since v0.2.0, and v0.6.0 and v0.7.0 each ended with the same five. v0.13.0 must not change the base
list.

```toml
[project.optional-dependencies]
onnx = ["onnxruntime>=1.18"]
```

Absent the extra, the ONNX adapter is **not importable** and the admin surface reports it
unavailable — it does not crash, does not degrade the built-in scorer, and does not attempt an
install. The zero-configuration identity is that the default just works; an operator who wants a
neural scorer opts into one package, and the eval gate continues to run against the built-in
`AdditiveScorer`, never against a customer model.

## R5. Sequencing for v0.13.0

1. **The preemption harness first**, with its own tests (a hanging scorer, a memory-bomb scorer, a
   crashing scorer, a scorer that returns malformed output), proving ingestion stays lossless
   throughout. Nothing else may be merged before it.
2. The generalised per-link attribution store (additive, forward-only migration).
3. The **ONNX adapter** — the blessed path — with artifact hashing, opset pinning, and the recorded
   reference vector re-checked at load **in the worker**.
4. The **entry-point escape hatch** last, documented as the last resort it is.

Its security review opens at the next unused finding number after v0.7.0's series (**F34**), and
must be honest that the trust model for path 4 is "operator-trusted, not sandboxed".

> **Amended 2026-07-31 (v0.7.4).** Steps 1–3 stand. **Step 4 does not happen** — the entry-point
> hatch is rejected (DECISIONS #93), so the sequence ends at the ONNX adapter and the security
> review has one trust model to describe rather than two. The finding number above was written in
> v0.6.0 and is stale: F34–F39 were taken by v0.7.1 and v0.7.2, F40 and F41 by v0.7.4. It is left as
> written because this document is a record of what was specified, and the next review takes the
> next unused number whatever it is by then.
