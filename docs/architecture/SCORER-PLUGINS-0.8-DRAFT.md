# Customer-supplied scorers — v0.8.0 draft (specification only, not implemented in v0.6.0)

This document specifies how **v0.8.0** will let an operator run a **customer-supplied model** as
the link scorer, under the `LinkScorer` contract that **v0.6.0 ships**. **It implements nothing.**
Every element below is tagged **`v0.8.0: planned`**.

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

## 1. The blessed ONNX path (`v0.8.0: planned`)

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

## 2. The Python entry-point escape hatch (`v0.8.0: planned`)

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

| v0.6.0 element | Why it already fits v0.8.0 |
|---|---|
| `LinkScorer` is a `Protocol` | An adapter class satisfies it structurally; no base class to inherit, no registry to modify, no `src/netcorenoc/` change needed to *accept* a second implementation — proven in v0.6.0 by the test-only alternate scorer. |
| `scorer_id: str` | `"onnx:<name>"` / the entry-point name; already the identity column in `scorer_config`. |
| `contract_version: str` | An adapter declares `"1.x"`; activation refuses an unsupported **major** (DECISIONS #49). |
| `params_fingerprint() -> str` | The ONNX adapter returns the artifact SHA-256 (plus runtime version); the entry-point adapter returns a hash of its declared configuration. Provenance by reference (DECISIONS #47) then works unchanged. |
| `LinkFeatures` reserved optional slots | `severity_i/j`, `topo_distance`, `probable_cause_i/j`, `event_type_i/j` are already present and `None`; populating them is a **minor** bump, so a richer scorer is additive. |
| `LinkScore.terms` is variable-length | A model with five feature attributions emits five `TermContribution`s. The three-column `link` persistence is the *default scorer's* projection (DECISIONS #50); generalising persisted attribution is a v0.8.0 schema task, named here. |
| `score()` is pure, deterministic, side-effect-free, inference-only | Exactly the ONNX and plugin execution posture; it also forecloses the rejected outbound-call design at the type level. |
| Fail-safe fallback + `scorer.fallback` audit action | Already shipped and tested in v0.6.0 against a deliberately-raising test scorer; v0.8.0 reuses it verbatim. |
| Append-only `scorer_config` + active pointer + rollback | An ONNX or plugin activation is one more immutable row; rollback to the built-in default remains a single pointer move. |

**Explainability is preserved even for a neural model.** Emitting `LinkScore.terms` is
*contractual*, not optional: an ONNX artifact must declare an attribution output (e.g. per-feature
contributions from the exported graph), and an entry-point scorer that returns an empty `terms`
list is a contract violation and therefore a fallback event. A scorer that cannot say why is not a
scorer this project will run.

### The one schema change v0.8.0 will need

`link.term_t/term_a/term_e` are three fixed columns. v0.8.0 adds a general per-link attribution
store (a `link_term` child table, or a JSON column) written *only* when the active scorer emits
something other than the three default terms, so v0.6.0-shaped rows stay byte-identical and the
migration is additive and forward-only — the same discipline as `0003_entity.sql` and
`0005_scorer_config.sql`.

## Threat-model entries v0.8.0 must add

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
