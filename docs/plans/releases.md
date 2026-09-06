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
| **v0.16.0** | **The situation lifecycle** — states, self-clear, manual clear, operator merge/split/move, semantic naming, and the feedback each of those produces. [Brief](v0.16.0-situation-lifecycle.md). | `situation-lifecycle` |
| **v0.16.1** | **Visualisation and search** — the judge's input repaired first, then graph analytics, the timeline, entities and alarm classes. [Brief](v0.16.1-visualisation.md). **Shipped.** | `visualisation-search` |
| **v0.16.2** | **The critical repairs** — a situation holding a live alarm stops leaving the live view, promotion stops being an implicit assertion, and severity becomes legible without reading. | `critical-repairs` |
| **v0.16.3** | **The operator's declaration** — naming an NE, naming an alarm class, declaring a severity, and propagating all three. | `operator-declaration` |
| **v0.16.4** | **The console's shell** — navigation, the situation cards, the members table, and the timezone the console already computes and does not always show. | `console-shell` |
| **v0.16.5** | **The evidence screens** — overview graphs, health, the map, a configurable timeline, and the model metrics beside the grouping they explain. | `evidence-screens` |
| **v0.16.6** | **Maintenance windows** — a planned-work declaration, and the composed severity/time filters that read it. | `maintenance-windows` |
| **v0.17.0** | **The external cartridge** — ONNX under the proven framework, behind the worker-process harness. [Brief](cartridge.md), which also argues it should slip again. | `external-cartridge` |
| **v0.18.0** | **Archetypes** — per-archetype weights (PON/access, transport/DWDM, IP core). Marked *likely, review before committing*. [Brief](archetypes.md). | `archetypes` |

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

### The v0.16 block, and why it is five releases rather than one

* **v0.16.2 first, because a defect that hides an active alarm outranks every feature behind it.**
  The idle sweep resolved a situation that still held a live alarm, and a repeating trap increments
  an existing alarm rather than forming a new situation — so the symptom of that defect is the
  absence of a symptom. Nothing that renders a situation is worth building above a view a situation
  can silently leave.
* **v0.16.3 after v0.16.2, on the severity numbers rather than on preference.** The declaration
  exists because the appliance's *learned* severity needs `SEVERITY_MIN_OBS = 200` observations and
  `SEVERITY_MIN_CLOSED = 50` closed alarms per NE before it will commit to one, and v0.16.2
  measured what that means on a real corpus. An operator declaration is what fills the gap the
  measurement describes; declaring it before the gap was measured would have been a feature looking
  for a reason.
* **v0.16.5 and v0.16.6 after v0.16.3**, and the dependency is on the declaration rather than on the
  screens: a severity filter and a per-severity health panel both read a severity that, today,
  resolves for almost nothing. Building either first would produce a screen whose every row says
  *unknown* and no way to tell a broken screen from an honest one.
* **v0.16.4 anywhere after v0.16.2**, because it is the one member of the block with no evidence
  dependency at all — it is shape, not signal. It is placed third because the declaration of
  v0.16.3 needs somewhere to live, and a shell built after the thing it must hold is a shell built
  once.

## What v0.16.2 measured, and which block needs it

A release's own measurements belong where the release that will *spend* them can find them.
These were taken by v0.16.2 and are reproducible commands, not quoted numbers — the reason
`tools/corpus_census.py` exists rather than a figure in a Markdown file.

**For v0.16.3 — `python tools/evidence/severity_census.py`.** All ten corpus scenarios through one
live appliance, with the maintenance sweep that confirms a severity field:

| | |
|---|---|
| alarms | 2 252 |
| alarms with a resolved severity | **0** |
| NEs with a confirmed severity field | **0** |
| NEs clearing `SEVERITY_MIN_OBS = 200` | 6 |
| closed alarms against `SEVERITY_MIN_CLOSED = 50` | **1** |

The sixth row is the control and it is what makes the zero mean something: the observation floor
*is* reachable on this corpus, so the zero is not *"no severity-shaped varbind exists"*. The
binding constraint is the ordinality test, which validates a candidate ranking against **closed
alarm lifetimes**, and the corpus closes one alarm in 2 252. That is a corpus question before it is
a threshold question, and it is the gap an operator declaration fills.

**For v0.16.5 and v0.16.6 — the same table.** A severity filter and a per-severity health panel
both read a field that resolves for nothing. Either would render a screen whose every row says
*unknown*, with no way to tell a broken screen from an honest one.

**For v0.16.4 — F103.** The member checkbox renders 13 × 13 px at every width, because the
tap-target floor's own selector excludes checkboxes. It is the control that decides which members a
partial split marks, and the repair belongs with the shell, where the row height, the checkbox
column and the touch floor are one decision.

**Also open for v0.16.3**: F99 (an integer severity rank above 4 has no place on the five bands)
and F100 (48 alarm classes on the corpus, 2 with a name, 46 with a vendor — so an operator reads a
raw OID 96 % of the time while the vendor sits one column away). **Both are closed in v0.16.3.**

## What v0.16.3 measured, and what it deferred

**The number the release exists to move**, re-measured on the same ten-scenario replay after the
change: of 48 classes, the fraction whose class column is a **bare OID and nothing else** fell from
**46/48 (95.8 %) to 0/48**, and the fraction carrying a **name** is unchanged at **2/48 (4.2 %)**.
Both halves are reported deliberately. The vendor is a qualifier and not a name, so what moved is
that an unnamed class now reads `Huawei · 1.3.6.1.4.1.2011.5.104.1` instead of the OID alone; the
46 missing *names* arrive when an operator declares them, and the release's contribution is that
they now can. A report of the first number alone would be Appendix B's triumphant one.

**Severity is still 0 of 2 252 on the corpus**, and that is expected: `severity.py`'s two gates are
byte-unchanged, a declaration is a separate source rather than a lowered threshold, and no
declaration is made during a replay. It is also what makes the disagreement prompt rare by
construction — nothing on this corpus can reach it.

### Deferred by v0.16.3, with the reason

* **MIB loading.** Parsing an operator-supplied MIB is a file parser, a validation surface and an
  attack surface, and the manual declaration delivers the same operational value at a hundredth of
  the risk. It becomes the *automation* of a gesture that already exists when there is demand for
  it — which is a better position to design it from than a blank page. **Not scheduled**: it needs
  a user asking for it, and there are none yet.
* **Severity per class + varbind.** `0016` put `label.qualifier` in the primary key so that this is
  a read rule rather than a second migration (DECISIONS #283). It waits on evidence that one class
  genuinely carries two severities on one NE, which nothing has yet produced.
* **F105** — `device.vendor` and `ne.vendor` are never written by anything (25 rows, 0 vendors,
  after 2 252 alarms) and two screens render them, one with a tooltip describing a different
  table's column. Deleting a rendered column belongs with **v0.16.4**, where the graph's tables and
  the entity card are one decision; inferring an NE vendor from the enterprise arcs of the traps it
  sends is a correlation question and belongs later still.
* **The member table's shape.** Working inside `members.js` made three problems obvious and none of
  them is this release's: the row is now nine columns wide for an editor and scrolls horizontally
  on a phone; the three declaration controls sit in three separate columns rather than in one
  actions cell; and the mark checkbox is still 13 × 13 px (F103). All three are **v0.16.4**, where
  the row height, the checkbox column, the actions cell and the touch floor are one decision rather
  than four.

## What v0.16.4 measured, and what it deferred

**The numbers the release exists to move**, all taken in Chromium at 390 / 820 / 1440 px as viewer,
editor and admin, before and after:

| | before | after |
|---|---|---|
| chrome above the work area at 390 px | **360 px of 844 (43 %)** | **94 px (11 %)** |
| top bar at 390 px | 126 px, four wrapped rows | 46 px, one row |
| member-table columns, editor | **11** *(not the nine the v0.16.3 note recorded)* | **8** |
| member table overflow at 390 px | 602 px | 377 px |
| member table overflow at 820 px | 172 px | **0** |
| controls below the 28 px tap floor, expanded card | **72** | **0** |
| frozen cell, editor, scrolled fully right | *(empty)* | `127.0.0.2` |
| screens naming a timezone in visible text | **0 of 9** | **9 of 9** |
| scenarios reachable through `make` | 1 of 13 | 13 of 13 |

**Three corrections to the brief, each by measurement**: the member row was eleven columns and not
nine; the restructure block did **not** render the same on every status (it was already hidden under
`resolved`); and `resolved` is not an open question the console must answer alone, because the
server accepts a verdict there (200) and refuses all three restructuring gestures (409).

### Findings issued by v0.16.4

* **F107** — `/api/stats` scopes every counter and then appends an unshaped `warnings` list that
  interpolated the trap allowlist verbatim, to a **viewer** whose graph the same session coarsens to
  a `/24`. The disclosure half is fixed (the warning names the entry count, following #227's own
  answer for the boot banner); **the oracle half is open** — the stale-situation count is
  whole-estate and reaches a scoped reader unchanged, and the repair is engine work.
* **F108** — a permalink followed from inside Situations changed the address and opened nothing.
  Fixed.
* **F109** — #237's frozen first column keeps a row's identity, and for an editor the first column
  is a checkbox: scrolled right at 390 px a viewer's frozen cell read `127.0.0.0/24` and an
  editor's read nothing. Fixed.
* **F110** — Bug 2 is a family. Scanning for its shape found three more, two pre-existing and **one
  written by this release**. Fixed, with a narrow guard.

### Deferred by v0.16.4, with the reason

* **F107's oracle half**, above. A per-scope idle-active count belongs where the sweep runs.
* **A bulk hand-clear.** One corpus situation holds **1 051** members and the zombie clear is one
  button per row. The mark column's select-all covers the *grouping* gestures, which need no route;
  clearing is a write with an audit row and a lifecycle consequence per alarm. **ROADMAP.**
* **Reopening a resolved situation.** The server refuses restructuring there on #254's ground.
  A verdict is still accepted, so no evidence path is foreclosed. **ROADMAP.**
* **An operator-chosen timezone.** The offset removes the ambiguity; choosing a zone is a per-user
  row and a migration (#294). **ROADMAP.**
* **An NE vendor, derived.** F105's measurement — 25 elements, 0 vendors, against 46 of 48 classes
  resolving one from their OID — is where a later release starts. **ROADMAP.**

### For v0.16.5, which this release made room for

* **The navbar's health control is where a sparkline goes**, and it needs a stored series: nothing
  in this appliance keeps one, and `psutil`, `resource` and `/proc` are all absent from `src/`. The
  control shows four served numbers and says which it cannot show.
* **The Overview has room and no placeholder.** Two of its five tiles became filters on Situations
  and one moved into the health control; nothing was left behind to be moved aside.
* **A severity filter still reads a field that resolves for nothing** — 0 of 2 252 on the corpus,
  unchanged by v0.16.3 and unchanged here, because a declaration is a separate source rather than a
  lowered threshold.

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
<!-- release-claim: v0.16.0 = situation-lifecycle -->
<!-- release-claim: v0.16.1 = visualisation-search -->
<!-- release-claim: v0.16.2 = critical-repairs -->
<!-- release-claim: v0.16.3 = operator-declaration -->
<!-- release-claim: v0.16.4 = console-shell -->
<!-- release-claim: v0.16.5 = evidence-screens -->
<!-- release-claim: v0.16.6 = maintenance-windows -->
<!-- release-claim: v0.17.0 = external-cartridge -->
<!-- release-claim: v0.18.0 = archetypes -->

## What this document does not decide

* **Dates.** None of these releases has one.
* **What an unbuilt release does.** That is its brief's job, and duplicating it here is how a
  repository comes to hold two answers to *"what is v0.8.0"* four lines apart, which is what
  `tests/test_documentation.py` exists to prevent.
* **v0.7.5.** Not in this chain: a runtime-behaviour fix to the feedback acquisition path, a
  prerequisite for v0.8.0 rather than a member of the sequence.
* **Anything after v0.18.0.** [`../ROADMAP.md`](../ROADMAP.md) keeps the unsequenced items.
* **Whether v0.17.0 happens at all.** *Likely, review before committing.* It is the one release here
  that may reasonably be dropped.
