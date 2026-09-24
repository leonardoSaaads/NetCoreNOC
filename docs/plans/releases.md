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
| **v0.16.5** | **The shell, corrected** — six things a browser found in v0.16.4's console, and the host metrics v0.16.4 believed needed a dependency. **Shipped.** | `shell-corrected` |
| **v0.16.6** | **The evidence screens** — overview charts, the estate map, a configurable timeline, and the model metrics beside the grouping they explain. | `evidence-screens` |
| **v0.16.7** | **Severity, and the screen an operator runs a shift from** — active alarms by band on the Overview, with the ones the appliance has **not** been able to place counted and named. | `severity-census` |
| **v0.16.8** | **Planned work, deferred** — the maintenance-window slot the v0.16 block reserved. **The content moved to v0.21.0** (#375) and nothing else took the number; the row stays so a reader who finds `v0.16.8` in an old document is told where it went rather than finding a hole. | `planned-work-deferred` |
| **v0.17.0** | **The foundations** — an eval baseline that can be re-cut with a recorded reason, guards that derive their sets instead of listing them, and `testbed/`: a two-host fibre cut a newcomer can deploy and trigger. **Shipped.** | `foundations` |
| **v0.17.1** | **The standard alarm vocabulary and vendor severity defaults** — a vendor-published severity shown immediately, with provenance and an operator override, beside the learned one. | `alarm-vocabulary` |
| **v0.17.2** | **The corpus and the correlation window** — scenarios whose alarms clear, a scenario spanning two enterprise subtrees in ONE incident (the case v0.18.0's gate is unmeasured against), and the window the correlator reasons over. **F76 left this release**: v0.18.0 closed it. | `corpus-window` |
| **v0.17.3** | **The external cartridge** — ONNX under the proven framework, behind the worker-process harness. [Brief](cartridge.md), which also argues it should slip again. | `external-cartridge` |
| **v0.18.0** | **The audit** — no theme: use the product, find what is broken, fix it. Nine defects found by driving it, F76 closed, and the first measurement of the scorer that is actually running. **Shipped.** | `audit` |
| **v0.18.1** | **Archetypes** — per-archetype weights (PON/access, transport/DWDM, IP core). Marked *likely, review before committing*. [Brief](archetypes.md). | `archetypes` |
| **v0.19.0** | **The operator's day** — set the appliance up from the boot banner, point real traffic at it, work the queue over several shifts, and fix what hurt. Five defects no test in this repository could have found, the worst of which merged an entire estate. **Shipped.** | `operators-day` |
| **v0.20.0** | **The value is the control** — the Overview made compact and direct, the situation panel's verdict promoted over its arithmetic, editing where the value is shown rather than behind a button, and the read models the console was waiting on. **Shipped.** | `value-is-the-control` |
| **v0.21.0** | **Maintenance windows** — planned work as a first-class declaration with a mandatory time zone and an organization, per-target collection rules composed from severity, OID subtree and time slot, a state ledger so a fault outliving its window still surfaces, and severity placed at ingest so the rules have something to read. **Shipped.** | `maintenance-windows` |
| **v0.21.1** | **Planned work, repaired** — the maintenance screen made usable by an operator rather than by a test: the form that could not submit, the stylesheet that was never written, the API errors that rendered as `[object Object]`, an Overview card for what is scheduled, and the three disclosure defects a role-by-role pass found behind them. No new capability, no migration. **Shipped.** | `planned-work-repaired` |

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
* **v0.18.0 last, deferred out of v0.12.0 by #170 on a measurement rather than a preference.** The
  promotion gate refused on this project's own corpus with `asserting_bags = 0` against a floor of
  50, and per-archetype weights mean splitting an already-insufficient corpus `k` ways.
* **v0.17.0 first in its block, and it is the reason the other three moved** (#336). The foundations
  release builds the machine the rest of the block needs: a baseline that can be re-cut with a
  recorded reason, so v0.17.2 can grow the corpus at all; a testbed whose scenarios **close alarms**,
  which #314 recorded as the missing ingredient for severity; and a scenario format that can carry a
  severity varbind, which is what v0.17.1 reads. Shipping the cartridge before any of that would have
  put this project's riskiest step — a second process, a preemption harness, an amendment to
  *"ingestion is sacred"* — on top of a gate nobody could re-cut.

### The v0.16 block, and why it is six releases rather than one

> **The numbering has moved twice, and both times for the same reason.** The block was planned as
> five and shipped as eight. v0.16.4's shell went out, a browser found six defects in it, and
> repairing them was a release of its own rather than a patch on the one that followed — so v0.16.5
> is *the shell, corrected* and the evidence screens are v0.16.6. Then v0.16.6 shipped eight charts
> onto the Overview and **still did not answer how many critical alarms are active**, because
> nothing on any screen counted them; that is v0.16.7, and maintenance windows move to v0.16.8.
> Nothing about any release's *content* moved; only their numbers did.


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
* **v0.16.6, v0.16.7 and v0.16.8 after v0.16.3**, and the dependency is on the declaration rather than on the
  screens: a severity filter and a per-severity health panel both read a severity that, today,
  resolves for almost nothing. Building either first would produce a screen whose every row says
  *unknown* and no way to tell a broken screen from an honest one.
* **v0.16.4 anywhere after v0.16.2**, because it is the one member of the block with no evidence
  dependency at all — it is shape, not signal. It is placed third because the declaration of
  v0.16.3 needs somewhere to live, and a shell built after the thing it must hold is a shell built
  once.
* **v0.16.5 immediately after v0.16.4, and not folded into it.** Its six defects were found by using
  the shell, which could not happen before the shell existed; and one of them — a panel 26 px wide
  at every width whose own comment asserted otherwise (F111) — is the reason this project's releases
  now end in a browser rather than in a gate document.

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

**For v0.16.6, v0.16.7 and v0.16.8 — the same table.** A severity filter and a per-severity
health panel both read a field that resolves for nothing. **v0.16.7 is the release that decided
to say so rather than to wait**: the panel it ships counts the unplaced alarms as their own band
and reads `—` on every other one, which is the difference between a broken screen and an honest
one made visible instead of assumed. A *filter* still needs the field, so v0.16.8 still waits.

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
  *(**Refuted by v0.16.5**, DECISIONS #300: all three host metrics are stdlib reads, the dependency
  count is still five, and the series is a two-hour in-memory ring rather than a table. The premise
  "the alternative is add a source" was wrong, and the sentence is kept so the correction has
  something to point at.)*
* **The Overview has room and no placeholder.** Two of its five tiles became filters on Situations
  and one moved into the health control; nothing was left behind to be moved aside.
* **A severity filter still reads a field that resolves for nothing** — 0 of 2 252 on the corpus,
  unchanged by v0.16.3 and unchanged here, because a declaration is a separate source rather than a
  lowered threshold.

## What v0.16.6 measured: which charts have data behind them, and which do not

A chart the appliance cannot measure is worse than the counter it replaced, so v0.16.6 answered
three questions for every chart the maintainer asked for **before drawing any of them**: what does
it answer, what data exists, and what does it cost. Nine were drawable, four were not, and the four
are the valuable half — each one names the table and the columns a later release would have to add.

**Drawn, and where the data comes from:**

| Chart | Answers | Source | Cost |
|---|---|---|---|
| CPU / memory / storage | is the box about to stop? | `stats.resources.{cpu,mem,disk}_series` | free, already polled |
| queue depth, p95 latency | is correlation falling behind, **and getting worse**? | `stats.queue_depth`, `stats.latency_p95_s`, ringed **in the client** (#222's precedent) | free; resets on reload, and the chart says so |
| situations over time | is this a burst or a trickle? | `GET /api/situations?limit=500` → `created_at`, `status` | **2.5 ms** measured |
| open vs new over time | is anyone triaging them? | the same rows, split by `status` | shared with the above |
| alarms over time | when did it start? | `GET /api/timeline?since&limit=1000` → `ts`, `kind` | **8.3 ms** measured |
| elements by active alarms | which element is worst *now*? | `GET /api/graph` → `nodes[].active_alarms` | free, already polled |
| the estate map | is it one element or the whole estate? | the same nodes | shared |
| verdict over time | is the challenger getting closer? | `promotion.decided_at`, `promotion.verdict` | **4.3 ms**, on demand |
| the four named quantities, per decision | which quantity moved? | `promotion.metrics` — four quantities, **both arms**, per `0013` | shared |
| seal query count over decisions | has the holdout been spent? | `promotion.query_count` | shared |
| the retention census | what is capture holding? | `/api/dataset/retention` → `stats` | **32.6 ms** — stays behind its on-demand control |

**Cannot be drawn, with what a later release would need:**

* **A loss curve.** `challenger_run` has 24 columns and not one holds a per-iteration loss, a
  residual or a convergence trace: `iterations` is a count, `learning_rate` and `fit_seconds` are a
  duration each. **What it would need**: a `challenger_iteration(run_id, iteration, loss)` table
  written by `engine/model/training.py` — a migration, and therefore a pre-registration question
  before it is a schema one. **v0.17.2**, where the corpus work now lives (#336).
* **A residual distribution.** The only label in the schema is in `feedback` and reaching it needs
  the join; `incumbent_linked` is a comparison basis and **never a target** (`0009`, and
  `PREREGISTRATION-0.16.0.md` §1). What could be drawn is the *score* distribution of
  `shadow_opinion.score` — but **no module under `src/` mentions `shadow_opinion` and an `/api/`
  path**, because `0009`'s posture is *no read below admin, on any route, in any format, ever* and
  v0.9.0 added no route at all. **What it would need**: an admin-only aggregate route, which is a
  security decision before it is a chart — and whatever it drew would have to name
  `challenger_run.sample_rate` beside it (**0.01** by default, deployment-settable through
  `config.shadow_sample_rate`).
* **Fold results.** `evaluation_fold` holds `(run_id, incident_id, repeat, fold)` — **membership,
  not results**, which is exactly what `0013` says it is for. Fold *sizes* are drawable and answer a
  different question; per-fold metrics are not. **What it would need**: the per-fold metrics
  `shadow_cv` computes and discards.
* **Top elements by alarms over a week.** The appliance serves *active* alarms per element and a
  recent mark page, and neither is a count over a window. Measured, and it is why this one is
  refused rather than approximated: `GET /api/timeline?limit=1000` came back **full — 1 000 marks
  spanning 15.6 seconds** — on a corpus of 1 976 traps, so a chart titled *"last 7 days"* drawn
  from that page would have shown fifteen seconds. **What it would need**: `GROUP BY ne_id` over
  `alarm.first_seen` inside a window — a query and one route parameter, no migration. The Overview
  draws *"by active alarms now"* instead, which is exact and complete.

**And the two numbers the release corrected in its own brief, both by execution:**

* `cpu_series` is **24 points, not 240**. `SAMPLES_KEPT = 240` is the ring; `SERIES_POINTS = 24` is
  what `snapshot()` serves, meaned into five-minute buckets. An appliance up for ten minutes serves
  two points, and the chart draws two.
* `/api/promotion` carries the four named quantities **per decision, with a timestamp**. The brief
  read `challenger_run` and `shadow_opinion` and concluded that *"which model is winning over
  time"* needed a new table. It needs a render.

### What v0.16.6 deferred, with the reason

* **F112's guard half.** F110's element-to-element exemption is justified by *"the container is a
  flex row whose `gap` separates them"* and nothing reads the container. Repairing it means
  resolving a CSS container per candidate pair — a stylesheet-parsing problem. The three sites are
  fixed; the guard is a **ROADMAP** line.
* **A second "map".** The maintainer asked for *"more than one map"* and got exactly one more, per
  decision 7: one that answers a question the first cannot. A third projection with no question
  behind it would be the placeholder #219 recorded, with a chart's face.
* **A frozen timeline window.** The window control is rolling (`since = now - win`), so a permalink
  shares the *configuration* and not the *instant*. An `at` anchor plus `until` would make it exact
  and is one parameter the route already accepts — but it needs a gesture nobody asked for, and
  decision 8's four controls are what the brief named. **ROADMAP.**
* **The four CLI reports still have no HTTP route**, so an operator reads a verdict's label in the
  browser and its content in a terminal. Unchanged by this release and unchanged deliberately: a
  route into the corpus is the security decision the residual chart is blocked on.

### The live pass, and what it found

**162 screens driven** — eighteen fragments at 390 / 820 / 1440 as viewer, editor and admin — with
**156 charts** checked for three things by looking: that the chart fits, that its axis is readable,
and that it agrees with the number printed beside it. Fifteen percentage line charts had their
**geometry** compared against their printed reading (`y = 40 - value/100 x 40`); none disagreed.

**It found four defects that no assertion in this repository could see**, and all four were in
work this release wrote:

1. the host series had **no time axis at all**, and their caption claimed *"last 2 hours"* over a
   series holding two points. `resources` serves values with no timestamps, so the fix is one
   additive key — `bucket_s` — and a span derived from `points x bucket_s`;
2. a **one-point line** drew an axis, printed a reading beside it, and left the plot empty;
3. the last x tick was **one bucket short**, because `buckets()` labels a bucket's *start* — right
   for a column, wrong for a series of means;
4. an `aria-label` reading *"over over 7 min"*, because both the span and the label added the
   preposition.

Numbers 1 and 3 are the same class as F111: a chart that renders perfectly and says something
untrue. Neither is visible to a harness with no layout engine, and number 3 is not visible to a
browser either unless someone reads the ticks against the span — which is what Part III means by
*check that it says the same thing the number beside it says*.

## What v0.16.7 measured, and what it refuses to draw

### The severity inventory: ten scenarios, 2 119 alarms, zero placed

Every scenario in `eval/corpus/` was replayed **separately**, over real UDP, into its own fresh
database, and `alarm.severity` censused after each:

| scenario | alarms | with a severity | closed alarms |
|---|---:|---:|---:|
| `background_noise` | 24 | 0 | 0 |
| `camera_nvr` | 300 | 0 | 0 |
| `chassis_card_fail` | 99 | 0 | 0 |
| `decoy_varbinds` | 240 | 0 | 0 |
| `dual_incident` | 8 | 0 | 0 |
| `fiber_cut` | 8 | 0 | 0 |
| `flapping_noise` | 1 | 0 | **1** |
| `olt_storm` | 501 | 0 | 0 |
| `pon_dying_gasp` | 926 | 0 | 0 |
| `pon_pon_port_down` | 12 | 0 | 0 |
| **total** | **2 119** | **0** | **1** |

**The cause is the last column, not the second.** `varbind_profile.role` never once holds
`severity` across any of the ten, so the learner does not reject a candidate — it never nominates
one. Gate one (`SEVERITY_MIN_OBS = 200` observations of one varbind on one NE) *is* reachable: nine
rows clear it on the three-scenario estate. Gate two is `confirm_ordinality`, which validates a
candidate ranking against **observed alarm lifetimes**, and a lifetime needs a close.

So severity is unknowable on this corpus **because nothing ends**. That is a property of the
corpus, not of the learner, and lowering `SEVERITY_MIN_CLOSED` to make a panel fill would fabricate
exactly the ordering `engine/correlate/severity.py` exists to refuse. **What a later release needs
is a scenario in which alarms clear** — v0.17.2's corpus work.

> **v0.17.0 changed the second half of that sentence without changing the first.** `testbed/`
> closes 25 alarms per run, which is the first thing in this repository that produces alarm
> lifetimes at all. It does **not** make severity placeable: `SEVERITY_MIN_CLOSED = 50` is per NE
> and the lab is nowhere near it, and v0.17.0 deliberately did not lower it. What the lab supplies
> is the *shape* v0.17.2 needs in the corpus — a raise, a lifetime, and a close.

### The three refusals this release records

**1. Severity over time.** Blocked twice over, and either alone is fatal:

* there is no severity to plot (above);
* **there is no time to plot it against.** `MIN(alarm.first_seen)` to `MAX(alarm.first_seen)` across
  the whole 1 716-alarm three-scenario estate is **1.14 seconds**, and `COUNT(alarm.cleared_at)` is
  **0**. A chart labelled *"last 24 hours"* would draw one column and call it a day.

*What a later release needs*, so it is not rediscovered: the column is **`alarm.first_seen`**; the
query is `SELECT severity_rank, COUNT(*) FROM alarm WHERE first_seen >= ? GROUP BY severity_rank,
bucket`; the window is a **route parameter** on `/api/stats` or a read of its own; and the
precondition is a corpus whose alarms clear.

**2. Top elements over a window.** v0.16.6's fourth refusal, re-checked here and **still refused**,
though it is now exactly one route parameter away (`GROUP BY ne_id` over `alarm.first_seen` inside
a window). The same 1.14 seconds refuses it: the window control would have one useful setting.

**3. A topology projection on the Overview.** Measured: `edge` holds **one** row of `kind='device'`
on the three-scenario estate and its weight is **0.0**, so `graph_snapshot`'s `min_edge_n` filter
returns **zero** edges — a topology here would draw two unconnected circles. The estate map keeps
*"where"* and its caption now links to the Graph screen, which owns the force scene (#317).

**The three v0.16.6 refusals that are unchanged**: a loss curve (`challenger_run` holds no
per-iteration trace), a residual distribution (`0009`'s posture is no read of `shadow_opinion`
below admin, on any route, in any format), and per-fold results (`evaluation_fold` stores
membership, not results). None became drawable.

### What was measured and left exactly as it was

**The receiver's five counters stay a line and do not become a chart.** Measured on the
three-scenario estate: `received` 1 816, `accepted` 1 816, `denied` 0, `quarantined` 0, `dropped`
0. As bars that is two identical full-width marks and three of zero width — less legible than the
line it would replace, and legible for a reason that has nothing to do with the network. F68 still
binds and `receiver.denied` is still on screen, which is what F68 actually asked for.

## What v0.17.0 measured, and the seam it leaves for v0.17.1

### The two instruments, measured rather than trusted

Both are narrower than their reputation, and a release that leans on them should know how.

**`make eval`** has held at `c2e8a0ce…` since v0.7.0 and that is worth what it looks like — but only
for a change that moves an aggregate. Measured here:

| Injection | Result |
|---|---|
| every candidate pair forced unlinked | **red** — hash `bb890b78…`, two gated regressions |
| the class-affinity term halved (`× 0.5`) | **identical**; no link crossed the threshold differently |
| the `W_T` module constant changed | **identical**; the live weights come from the seeded `scoring_config` row, not the Python default |
| the corpus **directory moved** | **identical** — so the restructure needed no re-baseline |
| one scenario added | **moved** to `28b77470…` |

**The behaviour record** (`acd2763b…`) is byte-sensitive to every response body and every served
console file, and it drives **no query parameters at all** — nine of them across four routes, `q` (the
v0.16.1 server-side search) among them. That is now declared in `behaviour_identity.py` and checked by
a derivation, so a parameter added to a route goes red until someone drives it or declares it.

One surprise worth carrying forward: a module **rename** leaves the record identical, but editing a
route **docstring** moves it, because FastAPI publishes docstrings as OpenAPI descriptions.

### The seam for v0.17.1, and the question this release does not answer

The maintainer's specification, in his own example: if Huawei publishes trap `1.2.3.4.5.6.7.8.9` as
**GRAVE / Remote Fail**, then when that trap arrives over SNMPv2 the console shows **GRAVE Remote
Fail** — and the customer may rename it, re-grade it, or override it.

That is a **default with provenance, not a fabrication**, and the distinction is the design:

* a **vendor-published** severity is an attested fact with a citation, shown immediately;
* a **learned** severity is what `engine/correlate/severity.py` confirmed from this stream;
* a **declared** severity is what this operator said — v0.16.3 already ships the route;
* and **the screen says which one it is looking at**, because an operator who cannot tell a vendor
  default from a measurement cannot correct either.

**Most of that seam already exists and v0.17.1 must not rebuild it.**
`store/read_models.py::severity_census` already resolves **declared first, then learned**, at read
time, and already reports `placed`, `unplaced`, `vendor_scaled` and `declared` separately. Note that
`vendor_scaled` there means *a rank above `VOCAB_MAX_RANK` from a vendor's own numbering* (F99) and is
**not** the pre-loaded table — do not overload the name.

So v0.17.1 inserts **one source into an existing chain**, and the open question is where:

> **Is the order `declared > vendor-published > learned`, or `declared > learned >
> vendor-published`?** A vendor's published severity is attested but static; a learned one is
> measured but only from this deployment's stream. Which should win when they disagree?

> **And what does the console show when they disagree?** The census reports buckets, not conflicts.
> Is a vendor default that the stream contradicts a fourth bucket, a badge on the learned value, or
> nothing at all?

> **And is a vendor default evidence?** It is attested, so it is not generated data — but it is also
> not a measurement of *this* network. Does a vendor-defaulted severity count toward
> `SEVERITY_MIN_OBS`, or is it display-only until the stream confirms it?

**These are left as questions deliberately** (principle 8). v0.17.1 will need a pre-registration, and
answering them here would be deciding an analytical question in a release that measured nothing about
it.

**What v0.17.0 owes v0.17.1, and delivered**: a scenario format that carries a severity varbind
(`testbed/scenarios/pon_fiber_cut.json` uses X.733 perceived severity at RFC 3877's ALARM-MIB arc,
whose six tokens are exactly `known_oids.SEVERITY_VOCAB`), and a `known_oids`-shaped place for the
table to land. **No vendor MIB file enters this repository, in this release or the next** — vendor MIBs
carry the vendor's copyright even when published, so v0.17.1 ships derived rows with cited sources
instead. The public source material is the **IANA-ITU-ALARM-TC** registry of ITU probable causes
(`lossOfSignal 8`, `lossOfFrame 6`, `transmitFailure 18`, `excessiveBER 12`, `degradedSignal 3`),
**RFC 3877 (ALARM-MIB)**, and **X.733 perceived severity** — whose six values `known_oids` already
holds, uncited.

### What v0.17.0 refused, with the reason

* **The package tree did not move** (#320). `engine/`'s six domains form a near-DAG — `correlate` is a
  pure sink at 28 edges in and 0 out, `operate` and `report` pure sources — with one 2-edge cycle
  between `dataset` and `model`. `store/` is a data layer: 23 of 26 modules hold SQL, **zero** import
  `engine` or `api`. No named cost, no move (Part VIII).
* **The test directories did not appear** (#321). 57 of 81 test files are named as text somewhere, and
  every one of those is prose, a citation or a `-k` selector — not a guard that would silently stop
  guarding. A move that rewrites 57 references against no defect is the trade the maintainer refused.
* **The `eval/` move was withdrawn after being decided** (#322, superseded by #328). Its named cost —
  generators writing into the gate's subject — **survives a directory change**, so the directory was
  not the remedy. A digest was (#327).
* **`SEVERITY_MIN_CLOSED` was not lowered.** The lab now closes alarms, which #314 named as the
  missing ingredient, but 25 closes is nowhere near 50 per NE and lowering the gate to make a chart
  draw would fabricate the ordering `severity.py` exists to refuse.

## What v0.18.0 measured about the 120-second window, and why it is a plan and not a patch

The audit's brief names four defects. Three were settled in the release (F76 closed, the snowball
refuted, the idle-close decision upheld and measured). The fourth — *"correlation cannot see past
120 seconds"* — is a design question larger than one release, and this is the written answer the
brief allows in place of code.

### What is actually true

`WINDOW_S = 120.0`, and eviction is on `now - window[0].ts > window_s`. **A pair more than 120 s
apart never reaches the scorer at all**, so no weight, no threshold and no model changes the
outcome. That much is confirmed. What is *not* true is that the appliance has no long memory: `A`
and `E` accumulate across all time with exponential forgetting per learning epoch, and they are
the only reason cross-element correlation works at all.

So the appliance has long-horizon memory and **cannot reach it from the linking decision**. The
window is not a memory; it is a **candidate set**.

### Why widening it is not the fix

Candidate selection is `O(min(n, MAX_CANDIDATES))` per activation against a window bounded by
`MAX_WINDOW_ALARMS = 20 000`. Widening `WINDOW_S` to a month does not produce a month-wide window;
it produces a 20 000-alarm window that evicts on the cap instead of on time, silently, counting
each shed live alarm as a gap. On the `olt_storm` corpus scenario alone — 501 traps in 25 s — the
cap is 2.5 % consumed by one incident. A month of a real estate overruns it constantly, and the
result is not longer memory but **arbitrary** memory.

### What recurrence should mean here, stated so the next release can disagree with it

Three candidate mechanisms, none built:

1. **A second, coarse candidate source.** Keep the 120 s window for *what is happening now*, and
   add a bounded set of *representatives* — one entry per (class, element) active in the last N
   hours, capped. A new alarm scores against both. Cost is bounded by the representative cap, not
   by the horizon. This is the smallest change that makes a month reachable.
2. **Recurrence as a feature, not a candidate.** Leave candidate selection alone and let the
   scorer see *"this exact pair has co-occurred k times over the last 30 days"* — which `A` and
   `E` already know and `LinkFeatures` does not carry. This links nothing new; it changes how
   confidently the pairs already in the window are linked.
3. **Situation-level recurrence.** Do not link across the horizon at all; instead recognise that a
   *closed* situation resembles one from last week, and say so. This is the only one that does not
   touch the ingest path, and the only one that answers *"is this the same fault again?"* rather
   than *"are these two alarms one incident?"* — which are different questions an operator asks
   with different words.

### What v0.18.0 measured that bears on the choice

**Affinity mass is driven by burst density, not by recurrence**, and that is the more urgent
defect. Thirty recurrences of the same genuine cross-element pair, 600 s apart, produce
`entity_affinity = 0.0000` — the pair mass reaches 1.0 against `MIN_EDGE_N = 5.0` and decays
between rounds. Sixteen alarms in five seconds produce **0.833**. The appliance therefore trusts
*"many alarms at once"* far more than *"the same two elements together thirty times"*, which is
backwards for exactly the recurrence question I.1 asks about.

**So mechanism 2 is the one this measurement supports**, and it needs F58/F61 resolved first:
there is no point serving a recurrence feature computed from an accumulator that recurrence
barely moves. A release that takes I.1 should decide what `MIN_EDGE_N` is counting before it
decides how far back the correlator can see.

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
<!-- release-claim: v0.16.5 = shell-corrected -->
<!-- release-claim: v0.16.6 = evidence-screens -->
<!-- release-claim: v0.16.7 = severity-census -->
<!-- release-claim: v0.16.8 = planned-work-deferred -->
<!-- release-claim: v0.17.0 = foundations -->
<!-- release-claim: v0.17.1 = alarm-vocabulary -->
<!-- release-claim: v0.17.2 = corpus-window -->
<!-- release-claim: v0.17.3 = external-cartridge -->
<!-- release-claim: v0.18.0 = audit -->
<!-- release-claim: v0.18.1 = archetypes -->
<!-- release-claim: v0.19.0 = operators-day -->
<!-- release-claim: v0.20.0 = value-is-the-control -->
<!-- release-claim: v0.21.0 = maintenance-windows -->
<!-- release-claim: v0.21.1 = planned-work-repaired -->

## What this document does not decide

* **Dates.** None of these releases has one.
* **What an unbuilt release does.** That is its brief's job, and duplicating it here is how a
  repository comes to hold two answers to *"what is v0.8.0"* four lines apart, which is what
  `tests/test_documentation.py` exists to prevent.
* **v0.7.5.** Not in this chain: a runtime-behaviour fix to the feedback acquisition path, a
  prerequisite for v0.8.0 rather than a member of the sequence.
* **Anything after v0.21.0.** [`../ROADMAP.md`](../ROADMAP.md) keeps the unsequenced items,
  among them the two this release deliberately did not take: D3's SNMP poller, which slips to
  v0.21.1 at the quality II.6 defines (#373), and real per-organization isolation, which the
  organization column is explicitly **not** (#366).
* **Whether v0.17.3 happens at all.** *Likely, review before committing.* It is the one release here
  that may reasonably be dropped. (It was numbered v0.17.0 until #336 renumbered the block.)
