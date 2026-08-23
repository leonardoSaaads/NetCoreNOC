# The external cartridge — v0.15.0 draft (specification only, not implemented in v0.14.0)

<!-- release-claim: v0.15.0 = external-cartridge -->

**Implement none of this in v0.14.0.** Every element below is tagged **`v0.15.0: planned`**.

**The marker above says what the roadmap table says**, and §2 of this document argues the release
should slip. Those are not in conflict and the marker is not quietly retagged to resolve them: the
table is the single source of truth, moving a claim needs an ADR, and a release does not get to
rewrite the next one's plan on its way out. What v0.14.0 may do is record an opinion, with its
reasons, where the maintainer will find it — which is what §2 is.

This supersedes [`CARTRIDGE-0.14-DRAFT.md`](CARTRIDGE-0.14-DRAFT.md), which is **left unedited**. It
was written in v0.13.0 from a premise v0.14.0 disproved, and the useful thing about it is that it was
wrong for a reason worth recording. The boundary it specifies — §1 to §3 of that draft — is
unchanged and is inherited here in full.

---

## 0. What v0.14.0 changed about this question

The v0.14.0 draft's first fact was:

> **Tree ensembles cannot be champion today, and not on merit.** `ROADMAP-0.8-TO-0.13.md` records
> the reason: *"not on merit — on plumbing."* XGBoost, random forests and gradient-boosted trees
> would all be legitimate challengers, and principle 5 forbids the dependencies that would let them
> run in this process.

**That was false, and the falsehood was load-bearing.** v0.14.0 shipped `tree`, `forest` and
`gradient_boosting` in process, in pure Python, with **five runtime dependencies still** — the same
five since v0.2.0 — and each one is a branch in `model_version.scorer_for` and nothing at the call
site. ADR #183 supersedes the ROADMAP sentence; ADR #185 records what shipped instead.

The conflation was between *"a model this project did not write"* and *"a model of this family"*. A
gradient-boosted model over three features is a list of small trees and a shrinkage constant.
`boosting.py` is under 300 lines. The plumbing was never the obstacle for this family; the obstacle
was believing it was.

**So the honest question for v0.15.0 is not "how do we run a tree ensemble" — it is "what is
actually out of reach in process, and is it worth a process boundary?"**

## 1. What is actually out of reach (`v0.15.0: planned`)

Stated as a list, so that a future release has to argue against a specific entry rather than against
a mood:

| Out of reach in process | Why | Would a cartridge help? |
|---|---|---|
| A model whose **weights are the artefact** — a neural net, an ONNX graph, a pickled sklearn object | The artefact is opaque bytes. `model_version.validate_document` cannot inspect it, so **no degeneracy rule of `PREREGISTRATION-0.14.0.md` §2 can be checked at all** | Only if §4.3's behavioural floor replaces every parameter-inspecting rule |
| A model over **more than the three features** | `LinkFeatures` carries three. That is a *contract* change, not a runtime one | **No.** A cartridge does not add a feature |
| A model needing **numpy-class arithmetic on the ingest path** | Principle 5 | Yes, and this is the strongest case |
| A model somebody else trains and hands over | Nothing about running it; everything about trusting it | Yes — the boundary is the point |

**Three of the four are not about plumbing either.** The first is about *inspectability*, the second
about the feature contract, the fourth about trust. Only the third is a dependency problem, and it is
the one nobody has asked for.

## 2. The opinion this release forms (`v0.15.0: planned`)

**Do not build the cartridge in v0.15.0.** Three reasons, in order of weight:

### 2.1 The machinery around the model is where the defects were

v0.14.0 shipped three model kinds and found **three defects, none of them in a model**:

* **F59** — the promotion gate measured `engine.shadow.scorer` and activated the candidate the
  request named, with nothing binding the two. Three releases of tests missed it because no test had
  ever proposed a candidate that *differed* from the shadow scorer.
* **F60** — the console reported the coded additive defaults as the active configuration whenever a
  model version was running. Two releases shipped with it, one of them a console rewrite.
* **F58** — a mass storm pushes `Learner.E`'s pair mass past `MIN_EDGE_N` for **every** NE in the
  window, and `STORM_DAMPING` does not prevent it.

Adding a process boundary to a chain with three known defects in its *existing* joints is adding a
joint. **The next release's value is in the joints that exist**, and F58 is unfixed.

### 2.2 The demonstration is incomplete, and a cartridge cannot complete it

`PREREGISTRATION-0.14.0.md` §5.3's second branch: ten increments, floors unmet, `asserting_bags`
10/50 and `asserting_incidents` 10/30. **The champion has never changed on any corpus this project
holds.** §8.7 registers the outcome where a champion changes and the provenance does not follow, and
this release has no observation either way.

A cartridge would add a *second* untested activation path beside an untested one. The first release
to actually change a champion should change it to something this process can build.

### 2.3 The inspectability problem is the real design question and it is unsolved

`PREREGISTRATION-0.14.0.md` §4.3 is explicit about the shape of the answer:

> for a model whose parameters cannot be inspected — v0.15.0's cartridge — **a behavioural floor is
> the only form threshold-reachability can take.** This is written to be that form, and §4.3 of the
> plan says v0.15.0 must not write a second one.

The discrimination floor of ADR #193 is that behavioural form, and v0.14.0 built it. **What is not
built is the rest**: T5's reachability, T6's saturation, F4's identical-members rule and G4's base
score are all parameter inspections, and an opaque artefact defeats every one. Either each gets a
behavioural equivalent — a different, harder design problem — or a cartridge model is admitted on
strictly weaker evidence than an in-process one, **which is a floor being lowered by the back door.**

That is the question v0.15.0 should answer in a document. It is not a question a subprocess protocol
answers.

## 3. What is inherited unchanged from the v0.14.0 draft (`v0.15.0: planned`)

§1 to §3 of [`CARTRIDGE-0.14-DRAFT.md`](CARTRIDGE-0.14-DRAFT.md) stand. In particular:

* **A separate process that receives features and returns scores, and can be killed at any moment
  without the appliance noticing.** Not a plugin, not an import, not a subclass.
* **The promotion gate does not care what produced a candidate**, and that is what would make this
  safe to build at all.
* Its §5's seven prohibitions, restated here because they are what a v0.14.0 could have broken and
  did not:
  1. No new runtime dependency in the core. **Five, and v0.14.0 added zero.**
  2. No import of cartridge code into the appliance's process, ever, under any flag.
  3. No cartridge on the trap path.
  4. No cartridge-asserted verdict, metric or floor.
  5. No seal access.
  6. No relaxation of the promotion gate to accommodate a cartridge that cannot meet it.
  7. No silent degradation.

**v0.14.0 honoured all seven by not building it**, which is the cheapest way to honour a prohibition
and the only one available to a release that was doing something else.

## 4. What v0.15.0 should do instead (`v0.15.0: planned`)

An opinion, offered as one and not as a plan:

1. **Fix F58, or decide in writing that it is correct behaviour.** It is a finding about the
   correlator on a network where incidents are concurrent, and it is measured, not inferred. It is
   also the one defect this release found and did not repair.
2. **Change a champion.** §5.3 steps 7 and 8 have never run. Whether that needs a different corpus,
   a different driving mechanism or a lower floor is a question for a *new* pre-registration —
   v0.14.0's §5.4 forbids answering it by editing v0.14.0's.
3. **Write the behavioural-floor design** §2.3 describes, in a document, before any process boundary
   exists to make it urgent.
4. **Then** decide whether the cartridge is worth building, with three fewer unknowns.

## 5. What this draft must not become (`v0.15.0: planned`)

**A reason not to ship models.** v0.14.0's whole lesson is that the ONNX door was believed to be the
only door for two releases and was not, and the cost of that belief was two releases in which no
non-additive model competed. A draft that says *"wait for the cartridge"* about the next family is
repeating the error with a different noun.

The test is the one v0.14.0 passed: **can it run in five dependencies, be inspected by its own
degeneracy rules, and decompose its own decision exactly?** If yes, it does not need this document.
