# The external cartridge — v0.14.0 draft (specification only, not implemented in v0.13.0)

<!-- release-claim: v0.14.0 = cartridge -->

**Implement none of this in v0.13.0.** Every element below is tagged **`v0.14.0: planned`**.

v0.13.0 built the console. This is the shape of what lets a model that cannot run inside this
process compete for the champion slot — decided **now**, in a document, rather than under deadline
while someone is writing a subprocess protocol.

Its parents are [`ROADMAP-0.8-TO-0.13.md`](ROADMAP-0.8-TO-0.13.md) and
[`UI-0.13-DRAFT.md`](UI-0.13-DRAFT.md) §8. The decisions it must honour are
[`../adr/DECISIONS.md`](../adr/DECISIONS.md) **#171** (nothing new is asked of the constitution) and
the promotion machinery of v0.11.0.

---

## 0. The three facts this draft starts from (`v0.14.0: planned`)

**Tree ensembles cannot be champion today, and not on merit.** `ROADMAP-0.8-TO-0.13.md` records the
reason: *"not on merit — on plumbing."* XGBoost, random forests and gradient-boosted trees would all
be legitimate challengers, and principle 5 forbids the dependencies that would let them run in this
process.

**The provenance is already first-class.** `challenger_run` stores `iterations`, `learning_rate` and
`fit_seconds` (migration `0009`), and `model_version.params_document` feeds `params_hash`. A
cartridge does not need a new provenance model; it needs to fill in the one that exists.

**The promotion gate does not care what produced a candidate.** `routes_promotion.py` re-derives the
floors, the power condition, the seal and the verdict from the corpus. A cartridge that produces a
`model_version` row with a `challenger_run_id` behind it is judged by exactly the machinery a
built-in scorer is judged by, and **that is the property that makes this safe to build.**

---

## 1. What a cartridge is (`v0.14.0: planned`)

**A separate process that receives features and returns scores, and can be killed at any moment
without the appliance noticing.** Not a plugin, not an import, not a subclass.

```
┌────────────────────────┐         ┌──────────────────────────┐
│ netcorenoc (5 deps)    │  pipe   │  cartridge (its own env) │
│                        │ ──────► │                          │
│  worker harness        │ ◄────── │  xgboost / sklearn / …   │
│  - spawns, supervises  │  JSON   │  reads stdin, writes     │
│  - times out, kills    │  lines  │  stdout, nothing else    │
│  - degrades to builtin │         │                          │
└────────────────────────┘         └──────────────────────────┘
```

**The appliance's five runtime dependencies do not change.** A cartridge's dependencies are the
cartridge's, installed in its own environment, and the appliance never imports them.

## 2. The properties that are not negotiable (`v0.14.0: planned`)

1. **Ingestion is sacred (principle 4).** The trap path never waits on a cartridge. Scoring happens
   in the engine's slow loop, exactly where challenger training already happens.
2. **Absence is not an error.** No cartridge configured, a cartridge that will not start, a
   cartridge that times out, a cartridge that returns nonsense — all four degrade to the built-in
   scorer, and the degradation is **visible** (`/api/scorer` already carries `degraded` and
   `degraded_reason`, and the console already renders them).
3. **A cartridge cannot assert a verdict.** It returns scores. The gate derives everything else,
   exactly as v0.9.2's evidence boundary requires — the enforcement is that the response model has
   no field for it, not that a handler ignores one.
4. **A hyperparameter that changes the trained model appears in `params_document`**, and therefore
   in `params_hash`. Otherwise two models with the same hash and different hyperparameters are
   indistinguishable and v0.11.0's provenance becomes fiction. **Registered as a constraint on
   v0.14.0 by `UI-0.13-DRAFT.md` §8 and repeated here.**
5. **The seal is not reachable from a cartridge.** Its query count is the number every holdout
   figure is printed beside; a subprocess that could read the holdout would end that guarantee.

## 3. The boundary, stated as a threat model (`v0.14.0: planned`)

A cartridge is **operator-supplied code running on the appliance**. That is a larger trust change
than anything since the token, and the draft's honest position is that it is *not* a sandbox:

* it runs as a **separate uid** with no database handle and no network egress by default;
* it receives **features, never raw traps** — no varbind values, no addresses, no operator labels;
* it is **not audited per call** (per-call auditing on the slow loop is unmeasured cost); its
  *lifecycle* is — spawn, degrade, kill, and the configuration change that pointed at it;
* **configuring one is admin-only and audited**, and the audit row names the executable and its
  hash.

**What this does not do**: stop a malicious cartridge from returning scores designed to merge
everything into one situation. The promotion gate is what stops that reaching the champion slot, and
a cartridge that is merely a bad model is what the gate exists for.

## 4. Open questions, deliberately (`v0.14.0: planned`)

1. **Whether a cartridge may be a container rather than a process.** A container is a better
   boundary and a much larger operational surface.
2. **Whether the feature vector is versioned.** It must be; whether the version lives in
   `params_document` or beside it is undecided.
3. **What happens to a promoted cartridge model when the cartridge is removed.** The pointer would
   name an artefact nothing can evaluate. Refusing the removal, degrading, and rolling back the
   pointer are all defensible and none is obviously right.
4. **Whether the console gets a cartridge screen in v0.14.0 or later.** `UI-0.13-DRAFT.md` §8
   requires the hyperparameter surface to be an honest statement rather than a greyed-out field
   until the cartridge exists; once it does, the statement becomes a screen, and adding it is
   adding entries to `app/registry.js` and a group to `app/sidebar.js` — which is the whole of what
   v0.13.0 owed the future.

## 5. What v0.14.0 must not do (`v0.14.0: planned`)

1. **No new runtime dependency in the core.** Five.
2. **No import of cartridge code into the appliance's process**, ever, under any flag.
3. **No cartridge on the trap path.**
4. **No cartridge-asserted verdict, metric or floor.**
5. **No seal access.**
6. **No relaxation of the promotion gate** to accommodate a cartridge that cannot meet it.
7. **No silent degradation** — every fallback is visible on the scorer screen and in the audit log.
