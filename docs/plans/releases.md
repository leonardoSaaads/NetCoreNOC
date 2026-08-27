# The release chain

**This table is the single source of truth for what each release is.** Every document in `docs/`
that asserts what a release *is* carries a machine-readable claim checked against it by
`tests/test_documentation.py`. Where prose and this table disagree, **the table wins and the test
fails**.

It plans; it implements nothing and it schedules nothing. A release here is a theme with a reason
for its position, not a commitment to a date. What each unbuilt release *does* is in the brief
linked from its row — stated once, there, so that this document and that one cannot drift apart.

<!-- The `claim` column is the machine-readable key. `tests/test_documentation.py` parses this table
     and every `<!-- release-claim: vX.Y.Z = key -->` marker across the live documents, and fails if
     any two disagree. Do not reformat this table without reading that test. -->

| Release | Theme | claim |
|---|---|---|
| **v0.8.0** | **The scoreboard** — capture the operator feedback as a durable dataset and measure its bias. Trains nothing. **Shipped.** | `operator-feedback-dataset` |
| **v0.9.0** | **Shadow mode** — models train in the slow loop and record how they *would* have grouped. The built-in scorer decides everything. **Shipped.** | `shadow-mode` |
| **v0.10.0** | **The honest judge** — held-out evaluation split **by time or by incident, never at random**, scored on over-merge and under-merge. **Shipped.** | `honest-judge` |
| **v0.11.0** | **Champion/challenger** — the slow loop proposes a promotion with the evidence; an admin approves; the swap is one more immutable row. **Shipped.** | `champion-challenger` |
| **v0.12.0** | **The instrument and the shape** — a DOM harness that executes the console, and the architecture of the UI that replaces it. **Shipped.** | `ui-harness` |
| **v0.13.0** | **The UI** — sidebar navigation, per-role dashboards, the network graph, themes, the full admin surface. **Shipped.** | `ui` |
| **v0.14.0** | **The model family** — three more scorer kinds this appliance trains and runs itself, in process, and the first end-to-end drive of the whole evidence chain. **Shipped.** | `model-family` |
| **v0.15.0** | **The repository** — `docs/` stops being a warehouse, organised by what a reader is trying to do rather than by which release produced it. `src/` unchanged but for the version string, and a convention that stops the pile returning (#197). **Shipped.** | `repository` |
| **v0.15.1** | **The package tree** — 58 modules at the package root, a layer model enforced in a dictionary, and a filesystem that ignores it. [Brief](v0.15.1-package-tree.md). | `package-tree` |
| **v0.15.2** | **The console repaired** — the detail panel, the narrow viewport, the icons, and the health numbers already being served and thrown away. [Brief](v0.15.2-console.md). | `console-repair` |
| **v0.15.3** | **The console designed** — what v0.15.2 leaves undone. [Brief](v0.15.3-console-design.md). | `console-design` |
| **v0.16.0** | **The external cartridge** — ONNX under the proven framework, behind the worker-process harness. [Brief](cartridge.md), which also argues it should slip again. | `external-cartridge` |
| **v0.17.0** | **Archetypes** — per-archetype weights (PON/access, transport/DWDM, IP core). Marked *likely, review before committing*. [Brief](archetypes.md). | `archetypes` |

## Why the order cannot be permuted

Each link consumes something the previous one produces, and the dependency is on *evidence*, not on
code:

> **You cannot train a challenger without a label. You cannot trust the label without knowing it is
> scarce and biased. You cannot declare a winner without an evaluator that never saw the training
> data. And you cannot automate the promotion without proof of real agreement with humans.**

* **v0.9.0 before v0.8.0** would train on labels that do not exist — there is no other source of
  human judgement in this system.
* **v0.10.0 before v0.9.0** would build an evaluator with nothing to evaluate, and the temptation
  would be to score the built-in scorer against the labels it already influences.
* **v0.11.0 before v0.10.0** would promote on the training metric — worse than usual here, because
  the operator labels what they *see*, so a model that over-merges produces fewer, larger situations
  to label and the label stream moves under the model.
* **v0.13.0 before v0.12.0** would rewrite a UI that no test could execute. The safety net comes
  before the thing it protects — the pattern that bought this whole chain, and now principle 8.
* **The cartridge after the model family** (#183, #184): the sentence that put tree ensembles behind
  the cartridge drew a packaging conclusion from a premise about packages. Three tree kinds now run
  in process with no new dependency, so the release that ships them needed no cartridge at all.
* **The cartridge after the repository** (#202): the cartridge is this project's riskiest step — a
  second process, a preemption harness, an amendment to *"ingestion is sacred"* — and taking it
  while a stranger cannot find the install instructions is the wrong order. Nothing in the
  cartridge's own argument moves; only its position does.
* **v0.17.0 last, deferred out of v0.12.0 by #170 on a measurement rather than a preference.** The
  promotion gate refused on this project's own corpus with `asserting_bags = 0` against a floor of
  50, and per-archetype weights mean splitting an already-insufficient corpus `k` ways.

## The claims

Each row above is claimed here, one marker per line. The table's own document must claim every row
it lists — a table nothing claims against is decoration, and
`test_every_release_in_the_table_is_claimed_by_the_roadmap_document` is what says so. Shipped
releases have their detail in [`../../CHANGELOG.md`](../../CHANGELOG.md).

<!-- release-claim: v0.8.0 = operator-feedback-dataset -->
<!-- release-claim: v0.9.0 = shadow-mode -->
<!-- release-claim: v0.10.0 = honest-judge -->
<!-- release-claim: v0.11.0 = champion-challenger -->
<!-- release-claim: v0.12.0 = ui-harness -->
<!-- release-claim: v0.13.0 = ui -->
<!-- release-claim: v0.14.0 = model-family -->
<!-- release-claim: v0.15.0 = repository -->
<!-- release-claim: v0.15.1 = package-tree -->
<!-- release-claim: v0.15.2 = console-repair -->
<!-- release-claim: v0.15.3 = console-design -->
<!-- release-claim: v0.16.0 = external-cartridge -->
<!-- release-claim: v0.17.0 = archetypes -->

## What this document does not decide

* **Dates.** None of these releases has one.
* **What an unbuilt release does.** That is its brief's job, and duplicating it here is how a
  repository comes to hold two answers to *"what is v0.8.0"* four lines apart, which is what
  `tests/test_documentation.py` exists to prevent.
* **v0.7.5.** Not in this chain: a runtime-behaviour fix to the feedback acquisition path, a
  prerequisite for v0.8.0 rather than a member of the sequence.
* **Anything after v0.17.0.** [`../ROADMAP.md`](../ROADMAP.md) keeps the unsequenced items.
* **Whether v0.17.0 happens at all.** *Likely, review before committing.* It is the one release here
  that may reasonably be dropped.
