# Architecture

How the appliance is built, where code goes, and what the current code is the first third of. For
specifications of things that do **not** exist yet, see [`plans/`](plans/).

## One runtime identity

One Python 3.12 asyncio process. One SQLite (WAL) file. One static console the browser loads
directly. Five runtime dependencies — `pysnmp`, `aiosqlite`, `fastapi`, `uvicorn`, `pydantic` —
unchanged since v0.2.0, and every model kind the appliance trains and runs was added without a
sixth.

```
UDP 162 ─▶ ingest/receiver ─▶ bounded queue ─▶ engine/operate/engine ─▶ store/ ─▶ SQLite
                                                     │
                                                     ├─ engine/correlate/  correlate, learn, scoring
                                                     ├─ engine/correlate/  rootcause, severity, profiler
                                                     └─ engine/dataset/    capture (the feedback dataset)

browser ─────▶ api/ (perimeter ─▶ RBAC ─▶ handler) ─▶ store/
```

## The five layers, and the one rule

Four layers are a stack; the fifth is available to all of them. **Since v0.15.1 a layer is a
directory**, so a module's layer is where it was saved rather than something a table has to
remember (decision #207).

| Layer | Directory | Owns |
|---|---|---|
| **http** | `api/` — app, perimeter, context, models, and `api/routes/` | HTTP semantics, the security boundary, request and response shape. **No domain rule** |
| **engine** | `engine/` — six subpackages, below | The domain: what a situation is, what links two alarms, what an entity is, what the root cause is |
| **data** | `store/`, with `migrations/` beside it | One SQLite connection under one asyncio lock. **SQL lives here and nowhere else** |
| **ingest** | `ingest/` — `receiver`, `events`, `known_oids` | The wire: parse, allowlist, quarantine, the trap vocabulary |
| **cross-cutting** | `crosscutting/` — `rbac/`, `shaping/`, `auth`, `audit`, `runtime`, `logsetup`, `settings` | Identity, authorization, visibility, attribution, config, logging |

The package **root** holds four modules and no others: `__init__.py`, and the process entry surface
`__main__.py`, `main.py` and `runner.py`. `python -m netcorenoc.main` is a public interface — the
`Dockerfile`, the systemd unit, `flake.nix`, `docker-compose.yml` and the README all print it — so
those did not move, and `test_layers.py::test_the_package_root_is_closed` keeps a fifth module out.
`api/` and `store/` kept their names rather than becoming `http/` and `data/`, because both locate
`ui/` and `migrations/` as `Path(__file__).parent.parent / …` and moving them would have been a
content change inside a move (decision #209).

> **A layer may import downward, and may import cross-cutting. Never upward.**

Downward means toward the wire: `http` → `engine` → `data` → `ingest`. Cross-cutting is importable
from anywhere and imports only cross-cutting.

An upward import is what turns a stack into a knot: it makes the lower layer untestable without the
higher one, and it makes *"where is this decided?"* unanswerable. `tests/test_layers.py` parses
every module's imports and enforces it, and **its exemption list is empty**. Type-only imports (`if
TYPE_CHECKING:`) are excluded — no runtime edge, no cycle.

Between v0.7.2 and v0.7.3 this rule had a paragraph and no test, which is exactly why the one
violation it had sat recorded and unfixed for a release. Between v0.7.3 and v0.15.1 it had a test
and a filesystem that ignored it, so a new module landed in the right layer only if its author
remembered to edit a dictionary in `tests/`.

### The six domains inside `engine/`

46 modules were one layer called `engine`, which is a true description of all of them and a useful
description of none. The split is by what the imports actually do, and the six form a **strict
order with no cycles between them** (decision #208):

| Domain | Holds | Imports |
|---|---|---|
| `correlate/` | the correlation decision and its vocabulary — `correlate`, `learn`, `scoring`, `scorer_contract`, `rootcause`, `severity`, the varbind profiler, `preview` | no other domain |
| `dataset/` | the feedback dataset — `capture`, `labels`, `census`, `incidents`, `seal`, `retention_policy` | `correlate` |
| `model/` | the model family — `attribution`, `cart`, `tree`, `forest`, `boosting`, `challenger`, `training`, `model_version`, `background` | `correlate`, `dataset` |
| `evaluation/` | shadow mode, the estimator, the judge, the folds, the promotion gate | `correlate`, `dataset`, `model` |
| `report/` | the three deterministic CLI reports, compute and render | the four above; **nothing imports it but the CLI** |
| `operate/` | the running appliance — `engine`, `engine_base`, `maintenance`, `gaps`, `scorer_lifecycle` | the four above; nothing imports it but the entry points |

## Where code goes

> **A module owns one noun or one decision. Over ~250 lines is a smell; over 400 it is debt with a
> named owner.**

* **"One noun"** is a thing the domain talks about — an alarm class, a situation, a scorer
  configuration. **"One decision"** is a question the system answers exactly once: *is this
  principal authorized?*, *which elements may they see?*, *do these two alarms belong together?* A
  module that owns a decision owns **all** of it; a second implementation of an existing decision is
  a defect wherever it lives.
* The line numbers force the *question* — "is this still one noun?" — rather than answering it.
  Splitting a coherent 260-line module into two 130-line ones to satisfy a number makes the code
  worse.
* **400 is enforced** by `tests/test_architecture.py`, with a shrink-only `DEBT_ALLOWLIST` naming
  the release that owns each current offender, and a separate `COHESION_EXEMPT` for a module that is
  large because an **invariant** forbids splitting it. The two are not interchangeable: debt carries
  an owner and a date, a cohesion exemption carries neither, because there is no fix.
  `engine.py` is `COHESION_EXEMPT`'s only entry, permanently.
* Two levels of package nesting where they have been earned, and never three: level one is the
  **layer**, level two is the **domain** inside `engine/` (or the package inside `crosscutting/`).
  A path says what layer a module is in and what part of the domain it belongs to, and there is no
  third thing it is allowed to say. No frameworks, no plugin systems, no dynamic loading.

## Four invariants that outrank tidiness

* **Ingestion is sacred.** `receiver.datagram_received` gains no lock, no I/O and no `await`, ever.
  The engine-side ingest path stays readable in one file: `engine.py` holds the batch lock and every
  decision that reasons about it, deliberately and permanently. Do not tidy it into modules — the
  invariant is only auditable if that path can be read without following imports.
* **One `Store`, one connection, one `store.lock`.** The lock is taken by *callers*, never inside a
  `Store` method, and a new store method must assume its caller holds it.
  `tests/test_store_concurrency.py` is the control.
* **Zero new runtime dependencies in the core.** Five since v0.2.0. New development tooling goes in
  the `dev` extra with a decision beside it.
* **A new route declares itself or the process does not start.** See
  [`security.md`](security.md#a-new-route-declares-itself-or-the-process-does-not-start).

## Explainability is contractual

Every scoring decision decomposes into per-feature contributions that sum to the score exactly. That
is a contract rather than a feature, and it survives the change of model family: a tree predicts a
leaf value, so the contributions are exact marginal Shapley values over all 2³ = 8 coalitions, and
**a model too large to tabulate is refused rather than approximated**.
[`correlation.md`](correlation.md#the-model-kinds) is the reader's version.

## Determinism where it is load-bearing

`python eval/harness.py | sha256sum` has been
`c2e8a0ced29d9edf986279d41089ddb68e18da65a46bdc7e9f04811e8b9b6f26` since v0.7.0. It replays a
labelled corpus offline and fails on any regression in the gated metrics. The four CLI reports are
compared byte-for-byte against frozen expectations by the test suite, so a change in what capture
means turns the suite red rather than moving a number quietly.

This is why `eval/corpus/` is never edited to tidy it: the hash depends on that directory's exact
contents.

## The three phases — what this code is the first third of

NetCoreNOC is designed in three phases. **Only the first is built.** The other two are described
here so that a reader understands the shape the current code is part of, and so that neither arrives
as a surprise that has to be bolted on.

### Phase 1 — correlation and inference *(this is where the project is)*

Traps arrive and are correlated into situations. Entities and classes are inferred without MIBs; a
network graph is built from co-occurrence; probable root causes are ranked; and an operator can see
**why** any grouping happened, per term. The output is a live, explained model of what is happening
in a complex, dynamic ISP network — from the trap stream alone.

Everything in this document, and everything you can run today, is phase 1.

### Phase 2 — automated first response *(not built, not designed)*

Takes phase 1's output and acts as a **NOC level 1**: an AI plus a library of diagnostic and
remediation scripts that **evolve and are scored over time**, trying the steps a human would try. If
the problem is not resolved within a time budget, it escalates.

Two things about the current architecture are what would make this possible rather than a rewrite: a
situation already carries its own evidence and the configuration that formed it, so an action can be
attributed to a decision; and the promotion machinery already knows how to score a candidate against
a pre-registered standard and refuse it — which is the same shape a script library needs.

Two things would have to be decided first, and neither has been: where an action executes relative
to the store lock (nothing on the ingest path, by the invariant above), and what a script's evidence
of having worked actually is.

### Phase 3 — case creation *(not built, not designed)*

An API that opens a ticket when a problem survives phase 2 — Salesforce, a database, whatever the
deployment integrates. A well-formed case carries the phase 1 evidence and the phase 2 attempts.

The `Case` JSON contract was drafted early and is at `docs/architecture/CASE-SCHEMA-DRAFT.md` in
commit `3ecf237` ([`record.md`](record.md) explains how to read it). It is not carried forward as a
live specification, because a contract written three phases ahead of its implementation is a guess,
and the honest version of it is this paragraph.

**Neither phase has a placeholder anywhere in the tree** — no empty modules, no stub routes, no
disabled menu items. The shape absorbs them; it does not announce them.

## The nine principles

1. **Zero-config / plug and play.** The product offers options and never says *"you can't"* — it
   says *"you can, and here is the risk."*
2. **Explainability is contractual.** Every decision decomposes into per-feature contributions.
3. **Determinism and reproducibility.** The `eval/` gate is byte-identical.
4. **Ingestion is sacred.** The trap path never gains a lock, an I/O, or per-packet latency.
5. **Zero new runtime dependencies in the core.** Five since v0.2.0.
6. **One runtime identity.** One process, one SQLite, a static console with no build step and no npm.
7. **Security by construction** — fail-safe, least privilege, hash-chained audit, deny-by-default.
8. **The instrument precedes the change it measures.** Build the guard before the thing it guards;
   record the next release's open questions as questions.
9. **Mechanism is configurable; the standard of evidence is not.**

Principle 8 used to read *"Spec now, implement later — each version writes the next one's
specification."* The foresight in it was real and is this project's best pattern: v0.7.5 fixed the
feedback acquisition path before v0.8.0 built a dataset on it, v0.9.2 fixed the evidence boundary
before v0.10.0 built a judge over it, v0.12.0 built a DOM harness before v0.13.0 rewrote the UI.
**None of that value came from the specification documents; it came from the ordering.** What the
documents produced was 62 000 lines. Decision #200 replaced the sentence and kept the pattern.
