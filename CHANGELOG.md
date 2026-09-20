# Changelog

Notable changes per release, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html) — pre-1.0, so a
minor bump may break.

**Every release before v0.15.0 also has a long-form entry at commit `3ecf237`**, where this file was
1 897 lines. What was worth keeping is here; the rest was a build report per release, and
[`docs/record.md`](docs/record.md) has the command to read it. `#N` is a decision in
[`docs/adr/DECISIONS.md`](docs/adr/DECISIONS.md); `FN` is a finding.

What to do to upgrade is in [`MIGRATION.md`](MIGRATION.md): of thirty-three rows, two ask for an
action, thirteen ask you to read a paragraph, and eighteen are start-the-new-binary.

## [0.19.0] - 2026-09-20 — "the operator's day"

The brief was to **use the appliance the way its operator does**: set it up from the boot banner,
point real traffic at it, work the queue with three people over several shifts, and fix what hurt.
Five defects came out of driving it that no test in this repository could have found, and the
worst of them merged an entire estate.

**The headline defect (F138): seventy independent failures became one situation.** Seventy devices
on distinct addresses, four vendor arcs, one card failure each — every incident separate by
construction. The appliance produced **one** situation of 140 alarms: 650 links, **580 between
different devices**, and **492 of the 650 carried by class affinity**. Every device raised the same
two trap classes, so `A[c_i, c_j]` climbed, and after a few dozen `0.294 + 0.220 = 0.514` cleared a
`0.50` threshold for any two of those alarms anywhere in the estate. A situation is a connected
component, so one chain of defensible links swallowed the lot.

This is [F76](docs/findings.md) one term over, and v0.18.0 measured exactly this repair and found
it changed nothing — on ten scenarios of two to four elements each. Seventy have a failure mode
four cannot exhibit. **Class affinity is now withheld when the two alarms are on different network
elements and `E` is exactly zero**, which is where the appliance has learned nothing whatever
connecting them. The same replay after the fix: **70 situations, two alarms each, zero cross-device
links**, and the whole shipped corpus **unchanged on every metric** (#353).

### The models stopped being prose

The Judge screen carried a heading reading *"Not drawn, because nothing measures it"* over the loss
curve, and the Labelling screen said the corpus figures were *"deliberately not computed here"* and
pointed at a `make` target needing shell access. Both were honest and both answered the wrong
question.

* **The optimiser records its loss now** (#355), so the curve is drawn rather than explained. On a
  separable fixture it runs 0.693 — `ln 2`, the coin-flip baseline — down to 0.061 over 21 points.
  Old rows are `NULL` and are not back-filled: those fits happened and nobody recorded them.
* **The Overview leads with one line and one bar**: *"The built-in formula is deciding. Your
  judgements are 12 % of the way to training a model."* Behind a click: the four registered floors
  as bars, the curve, and — for an admin — the two controls.
* **The bars are live.** A verdict moves them on the very next read (5 → 6, measured), because the
  census is cached against the row count and highest id of the tables a judgement writes rather
  than read off a training run that happens every five minutes (#356).
* **An admin can register the appliance's own fit from the console** (#354) and then ask the
  server to hand correlation over. Registering is not promoting: the judge still re-derives every
  floor, the power condition, the sealed holdout and the verdict. **The request body names a run
  id and nothing else**, so it cannot assert a model this appliance did not fit — the same
  construction `POST /api/promotion` uses, one table earlier.

### A query that took three minutes to return nothing (F140)

`gesture_positive_pairs` matched alarm ids in both orders as an `OR` inside a join, which no index
can serve, against a table with no index on an alarm id at all. On 222 050 sink pairs: **176.550 s,
returning 0 rows** — on every training tick. Migration `0018` adds the two indexes and the query
became a union of two indexable joins **in the same commit**, because neither half is worth
anything alone:

```
                   OR in the join     UNION ALL
without the index     176.550 s        220.591 s
with the index        175.345 s          0.003 s
```

### What an operator actually looks at

* **The health charts are readable** (F141, F142). CPU and memory ran at 5 % against a fixed 100 %
  ceiling — a flat line along the floor — so a percentage axis now takes the smallest band that
  contains the data and **prints its ceiling**. Charts were blank for the first ten minutes after a
  restart because buckets were sized against the ring's capacity rather than the readings taken;
  they now appear from the second reading. Axis ticks were rendering as `09:36:3309:37:03`.
* **The database has its own chart**, in megabytes beside the filesystem's percentage — "Storage"
  is the disk, and an operator reading 89 % could not tell how much of it was this appliance.
* **The panel answers its own heading.** *"Is the appliance keeping up"* now carries a one-line
  verdict above the charts, by the same rule and in the same three tones as the grouping verdict.
* **Captions say what is measured, not which endpoint served it.** `/api/stats.resources — the
  host's memory` became `this host's memory`, on the screen an operator opens first.
* **"Storm" is reserved for a storm** (F139). Seventeen situations in a live queue were all named
  `Storm -> …`, including groupings of two. The threshold is `STORM_ALARMS`, the count the engine
  already damps learning at, with a test pinning the two together.
* **`23 quieter elementsare not drawn`** — F112's missing space, third occurrence, found by
  reading the rendered page rather than the template.

### The gates

**2 064 passed**, coverage **95.59 %**, `mypy --strict` clean on 226 files, ruff clean, vulture
clean, **79 DOM tests executed**, `make eval` with **no gated regressions** and every metric
identical to v0.18.0. Schema 16 → **18**. Still **five runtime dependencies**.

One guard was made less brittle rather than updated: `test_no_server_derivation_reaches_operator_name`
pinned a line number and went red when an unrelated constant was added above the method. It asserts
the property — one writer, in that file — because a guard that fails for a reason it does not care
about teaches people to update it without reading it.

## [0.18.0] - 2026-09-19 — "the audit"

Not a feature release. The brief was *use the product, find what is broken, fix it* — so the
headline is a count: **nine defects found by driving the appliance, of which six no test in this
repository could have found**, and one of them made a flapping link invisible.

**The headline defect (F134).** `CLEAR_PAIR_SEEDS` ships `linkDown → linkUp`. The alternation
learner registered the **inverse** of that pair as soon as a `(device, instance)` alternation
happened to begin with `linkUp` — the ordinary case for an appliance deployed while a link is
already down. From that moment every `linkDown` trap was dispatched to `_handle_clear` and no
alarm was ever raised for it again. Measured on a real appliance over UDP: eight traps ending in
`linkDown`, **0 active alarms**. The link was down and the console said the network was clean.
The corruption was durable — both directions were written to the `edge` table — so a code-only
fix would have come back on the next restart. After the fix, the same replay: **1 active alarm**.

**F76 is closed.** `eval/corpus/dual_incident.json` has described itself as *"two unrelated
incidents … must stay separate"* since v0.15.0 and they did not; a test pinned the wrong answer
on purpose, saying what to replace it with. The scorer no longer applies *learned* cross-element
affinity to a pair on two different elements whose trap OIDs sit in different enterprise subtrees
— `same_oid_root`, the fourth feature `PREREGISTRATION-0.9.0.md` §2.3 registered in v0.9.0 and
which went unimplemented for nine releases for one recorded reason: it needed an edit to
`correlate.py`, whose bytes were pinned. **The v0.18.0 brief withdrew that pin.**

Measured over the whole corpus: `dual_incident` `pairwise_f1` 0.6364 → **1.0000**, `ari` 0.0000 →
**1.0000**, `over_merge_rate` 1.0000 → **0.0000**; the other nine scenarios do not move on any
metric and `under_merge_rate` stays 0.0000 on all ten. Corpus-wide `over_merge_rate` 0.0312 →
**0.0000**. A score threshold could not have done it: the seven cross-incident links scored
0.5857 to 0.7243 against within-incident links at 0.6161 to 0.7684, overlapping, with one pair
either side of the boundary at 0.7134 and 0.7131.

**This is a declared correlation behaviour change**, so `make eval`'s stdout hash moves —
`c2e8a0ce…` → `c75b42aa…` (the intermediate `ecab6c45…`, before the baseline was re-cut, is recorded here because the printed table carries the baseline's own numbers, so the re-cut moves the hash a second time) — and the baseline is re-cut with its reason in
`eval/baselines/REBASELINE-LOG.md`.

### The appliance can see itself (Part II)

Nothing measured the running scorer. `GET /api/correlation` now answers *"is the correlator doing
a good job right now?"* without an offline report: accept rate, the score distribution against the
threshold, how many decisions land within 0.05 of it, which of the three terms carried each link,
merges per activation, and the subtree refusals — each **lifetime and over the last 500
activations**, because one number cannot say whether a figure is normal.

**On screen it is one line on Situations, and the detail is behind a click.** It shipped first as
a whole view and that was wrong: Situations already explains *this* grouping, and an operator
wants the *result* — whether what they are looking at is trustworthy — not a second screen of
metrics. So the answer is a sentence above the list (*"Grouping looks steady — 2.6 % of decisions
were close calls"*), and the panel is not in the DOM until it is opened.

It found something immediately. On four replayed scenarios: learned entity affinity carried
**518 of 520** accepted links. That is F58's arithmetic, visible in the product for the first time.

**It is not evidence.** Nothing is stored, no promotion path reads it, and the screen offers no
control that records an opinion.

### The newcomer path (F128, F129, F130, F131, F132)

`tools/trap_replay.py` bound each simulated source under `contextlib.suppress(OSError)`. **Eight
of the corpus's twenty-five source addresses are TEST-NET-3**, which no host has an interface in,
so `make replay SCENARIO=dual_incident` delivered its four devices as **one**, on every machine,
every time — and the appliance then merged them correctly, because on the wire they were one
device. `tests/test_operation.py` carried a private rewrite that worked around this, so the one
test that drives that scenario over a socket could not see it. The rewrite now lives in the tool
and the test calls it.

`testbed/Dockerfile.ne` could not be built: the root `.dockerignore` excluded all three of its
COPY sources. `docker compose config` parses YAML and never reads `.dockerignore`, which is why
v0.17.0 shipped it. Fixed, with a guard that derives every Dockerfile's COPY sources and checks
them against the ignore file — no daemon required, because a guard that needs one is a guard
that skips.

The lab now picks a free port when its default is busy and says so; `control.py` finds the
running lab through a descriptor instead of printing a hardcoded `8080`.

### What was deleted

* **`TRAP_PATH_HASHES` and `TRAP_PATH_BODY_HASHES`** (173 lines) — they protected the ingest path
  from casual edits, the brief withdrew byte-pinning by name, and they could not tell a comment
  fix from a blocking `open()`. Replaced by the constraint the brief keeps: a guard that reads
  `datagram_received` and fails on an `await`, a lock or an I/O call, with a control proving it
  can fail.
* **`test_score_link_body_is_unchanged_by_the_capture_change`** — a source-text hash policing one
  sentence of v0.8.0's build prompt, ten releases past. Replaced by a behavioural parity test
  that checks the *number* rather than the text.

### Also

* `shadow_opinion.same_oid_root` recorded a hardcoded `0` for nine releases — not empty, which
  would have been honest, but **wrong**, and no reader could tell. It now records the pair.
* A count chart's header printed its **last bucket** and called it the reading (F133): the
  Overview said `resolved 1` beside three resolved columns. A `column` series now reports the
  window total and a `line` series its latest sample, and the header says which.
* `eval/baselines/v0.2.0.json` was both an immutable historical record three tests assert against
  **and** the file `make eval-baseline` overwrites (F136). The first release to re-cut a baseline
  since that target was added found it. Split into `current.json` and `v0.2.0.json`.
* `learn.py` split at the 400-line guard: the two alternation learners answer *"which class turns
  this one off?"*, which is not the affinity matrices' question. `ui/app/charts.js` split at the
  module-graph ceiling: `Bars` and `Map` compare things to each other, `Series` compares a thing
  to its own past.

### What was measured and left alone

* **The snowball is refuted.** Three configurations, none of which grows: a cycler with its own
  situation forms a fresh one per re-raise and each resolves; a cycler sharing a never-closing
  situation stays at two members; with the structural bridge in place it grows **once** and then
  stops. The situation stays open and does **not** swallow. What was really wrong with repeating
  alarms is F134.
* **The idle-close decision (v0.16.2) is upheld.** A situation holding an active alarm is not
  resolved, is badged `stale`, is counted in an operator warning and can be closed by hand. The
  measurement above says it does not grow, so the population it leaves is bounded.
* **The gates**, all after the last change: **2048 passed**, `mypy --strict` clean on 252 files,
  `ruff check` and `ruff format --check` clean, **79 DOM tests executed** (not skipped), bandit
  and `pip-audit` clean, coverage **95.59 %**, wheel and sdist installed into a clean venv and
  the appliance booted through all 16 migrations from it.
* **The architecture is unchanged and still a real structure.** v0.17.0's measurement, repeated:
  `engine/` is six domains with **15** cross-domain edges, one 2-cycle (`dataset ↔ model`),
  `correlate` a pure sink, and `store/` 26 modules with **0** imports of `engine/` or `api/`.

## [0.17.1] - 2026-09-15 — "the alarm vocabulary"

The appliance had been receiving the word `critical` since v0.8.0 and refusing to use it. This
release reads it, says where every severity came from, and lets an operator overrule any of it.

**The headline number.** On one `make lab-demo` during a live cut: **0 placed / 14 unplaced** became
**13 placed / 1 unplaced**, every one of the thirteen labelled `standard`. After one operator gesture
on the remaining alarm: **14 placed / 0 unplaced**, `declared 1, standard 13`. `make eval`'s stdout
hash did not move — `c2e8a0ce…` before and after, checked against a stashed tree.

**What the release is not.** No vendor rows, no vendor MIB file, no severity anybody invented.

### The standard read (#337, #338, #340)

`known_oids.standard_severity` reads the X.733 perceived-severity word a trap carried in its own
varbinds. **This asserts nothing about any vendor**: the device transmitted the word, ITU-T X.733
defines that word as a perceived severity, and the appliance believes the device about its own alarm.
Its provenance is `standard`.

Precedence is **declared > standard > learned**, resolved at *read* time in `store/read_models.py`
consistent with #315 — so the trap path is untouched, its content hashes are intact, there is no
migration and no per-packet cost. An operator's declaration outranks both, because a severity read
out of a standard column is knowledge and not consent (`PREREGISTRATION-0.10.0.md` §6).

**The read is keyed on the value, not the OID.** The intended design keyed on RFC 3877's registered
column; `www.rfc-editor.org` and `www.iana.org` both answer 403 to the environment this was built in,
so every OID-keyed row would have carried a citation nobody here could open. Keying on the value
needs only the vocabulary already shipped — and works at whatever OID a device chose.

`unplaced` stays a first-class count. A trap that carries no severity word, matches no bundled row
and has no declaration is still unplaced, counted, and rendered `—`.

### The console (#341, #348)

The Overview's severity band now reads *"14 placed, 0 not placed — 13 read from the word the trap
carried, 1 from an operator's declaration"*, and its source line names the whole precedence chain.
The arms are derived from the payload with a fallback to the raw key, so a later release that adds a
fourth source shows it without that file being edited.

Driven in Chromium against a live lab appliance, three roles at 390/820/1440: zero horizontal
overflow, zero console errors. `POST /api/labels` as **viewer** is refused `403 insufficient role`.

### What was refused, and why (#339, #343, #346)

- **No vendor rows.** Every one would have rested on an OID unverifiable against any reachable
  source, dressed in a citation nobody could open. A table of plausible-looking OIDs with
  real-looking references is worse than no table: it is *a severity asserted on a vendor's behalf*
  wearing the costume of the fix.
- **No renaming of alarm classes.** The maintainer asked for three verbs — rename, re-grade,
  override — and this delivers two. Stated plainly rather than implied by silence.
- **The ALARM-MIB column is used and not named.** A draft called `1.3.6.1.2.1.118.1.2.2.1.4`
  `alarmActiveResourceId`; RFC 3877 is unreachable from here, and a citation nobody checked is the
  defect this release exists to close.

### Citations, and a guard that found more than it was written for (#344, #347, F125)

Every bundled table in `known_oids.py` now carries a source in `BUNDLED_SOURCES`, and the guard
**derives its subject from the module**. Written as a list it would have covered the one table anyone
was thinking about; derived, its first run named four more. Writing those citations caught an error
in an existing comment: `STANDARD_TRAPS` was labelled "RFC 3418", which is wrong about three of its
six entries.

`tests/test_supply_chain.py` proves **no vendor MIB file is in this repository**, by what files
*contain* — an ASN.1 module signature plus an SMIv2 macro — rather than by what they are called. A
MIB renamed `notes.md` with no MIB extension fails it by name.

### Findings

- **F125** — four bundled tables shipped public data with no citation; only the fifth was noticed.
- **F126** — with the standard read in, the lab placed *everything* and `unplaced` read 0. True, and
  a claim about a corpus chosen to make it true. The lab now raises one rectifier fault that carries
  no severity at all and never clears, so both halves of the census are visible (#345).
- **F127** — `ui/app/format.js` had **228 bytes** of headroom under the module-graph ceiling and
  nothing said so. Routed around, not fixed: the vocabulary moved to its only consumer and
  `format.js` is byte-identical.
- **F116** is still open and still visible: the unplaced row reads *"1 alarms"*.

### Verification

2 004 tests. `make test` reports **96 %** over **8 923** statements — and that number **does not
cover `testbed/` at all**. Adding `--cov=testbed` gives **9 266** statements at 93 %: the lab
contributes **343** statements of which **36 %** are exercised, because `run_local.py` and
`ne/agent.py` are subprocess entry points the suite drives as processes rather than imports.

Four injections, each red, with a behaviourally-neutral control that stayed green: a fabricated
default severity; the precedence inverted so the appliance outranks the operator; a provenance the
build does not know silently dropped; an ungraded band printing `0` instead of `—`.

## [0.17.0] - 2026-09-14 — "the foundations"

Three movements, in the order that made each one safe: prove what the safety net can see, change
what measurement said was worth changing, and build the lab that was missing.

**The headline numbers.** **22 seconds** from a clean state to a correlated fibre cut visible in the
console — and **zero files moved** with the behaviour record unchanged across them, because
measurement said the package tree had no named cost to move.

That second number is the release arguing with its own brief, and it is the honest answer. The brief
asked whether `engine/` earns 59 files. Measured: its six domains form a near-DAG — `correlate` is a
pure sink at 28 import edges in and 0 out, `operate` and `report` pure sources, one 2-edge cycle
between `dataset` and `model`. `store/` is a data layer, not a second domain layer: 23 of 26 modules
hold SQL and **zero** import `engine` or `api`. Part VIII decides it — *no named cost, no move* — and
Part VII.7 forbids a rename whose only argument is taste. So nothing moved, and the effort went where
the measurements pointed instead.

### The safety net (#324, #325, #327)

`make eval-baseline REASON="…"` **refuses to run without a reason**, checks that before the
two-minute replay, and appends the digest it replaced beside the digest it wrote — plus every
aggregate metric that moved — to `eval/baselines/REBASELINE-LOG.md`. A pinned baseline exists to
answer *did this change behaviour when I did not mean to?*; it is not a reason the corpus may never
grow, and before this the only way to grow it was an edit no reviewer could tell from a behaviour
change that was papered over.

**Both instruments were measured rather than trusted, and both are narrower than their reputation.**

| Injection | `make eval` |
|---|---|
| every pair forced unlinked | **red**, `bb890b78…`, two gated regressions |
| class-affinity term halved | **identical** — no link crossed the threshold differently |
| the `W_T` constant changed | **identical** — live weights come from the seeded `scoring_config` row |
| the corpus directory moved | **identical** — so the restructure needed no re-baseline |
| one scenario added | **moved**, `28b77470…` |

So the corpus is now pinned by a path-and-contents digest with its scenario and event counts
(#327) — the instrument `make eval` is not, since a digest over bytes is insensitive to nothing.
And the behaviour record's blind spots are declared where the next release reads the instrument: it
drives **nine query parameters across four routes with no query string**, `q` — v0.16.1's
server-side search — among them. The live set is **derived** from `route.dependant.query_params`, so
a parameter added to a route goes red until someone drives it or declares it.

### Guards that derive their sets (#326, F119)

`tests/test_structure.py`'s `SUBMODULES` was a hand-written list of **84 names against a package of
128**. The 44 nobody checked were every module of `engine/dataset/`, `engine/model/`,
`engine/evaluation/` and `engine/report/` — **four of engine's six domains**, `engine.dataset.capture`
on the trap path among them. The rule was right and the list was a third short, which is F112 and
F113's shape at 34 entries. The set is read off disk now; `test_every_submodule_resolves` went from
84 parametrized cases to **128**.

**F119**: the guard whose stated job is *"the UI tree is enumerated"* compared the console's top
level as `top_level - {".well-known"} == {…}` and checked that directory's contents under an `if`.
Subtracting an absent member is a no-op, so it passed whether `ui/.well-known/` was there or gone —
and it holds RFC 9116's `security.txt`, a **served route**. Moving the directory away left the
pre-fix guard green and makes the repaired one red.

**F118** and **F120** are recorded and open: the 400-line module guard covers `src/` and `ui/` and
not `tests/`, where **31 of 90 files** are over it; and the release-claim guard reads markers, not
prose, so `docs/README.md` had disagreed with the release table for four releases on two of three
rows.

### `testbed/` — a two-host fibre cut you can trigger while watching it (#329-#334)

Two simulated GPON OLTs with **different addresses** send genuine SNMPv2c trap PDUs at an
unconfigured appliance. `python testbed/control.py cut` and the span goes down, the ONUs storm behind
it, the appliance collapses them into one situation and **names it itself**. `… repair` and it clears.

Measured from database queries, not log lines: `/healthz` in **1.0 s**; three cut/repair cycles in
**128 s**; `SELECT DISTINCT ip FROM device` → `127.0.0.2`, `127.0.0.3`; one situation of **24
members** across both devices, `derived_name = "Storm -> 127.0.0.2 and 1 more"`; **25 of 25 alarms
cleared**, all five situations `resolved`; mid-cut `severity_census` → `active: 25, unplaced: 25,
placed: {}`.

The scenario uses the **standard** `linkDown`/`linkUp` pair for the span and a **vendor** arc for the
ONUs, deliberately: the span clears on the first repair from `CLEAR_PAIR_SEEDS`, and the ONU pairing
has to be *learned* — `CLEAR_CYCLES_TO_LEARN = 2`, so from the third cut those clear too. That
progression is the product's thesis and `make eval` cannot show it, because the harness replays each
scenario once.

**A scenario cannot carry ground truth.** Not *does not* — the loader refuses a `truth` key at any
depth, so generated traffic has no label to leak. The walk is recursive because the corpus keeps
`truth` inside each event; a top-level check would accept a labelled corpus file copied in.

No new dependency (five, unchanged), no new route, no migration (`0016` still head), no `/api` change.

### CI: four jobs, and the four things it never ran (#335)

The 74 DOM tests **never ran** — collected and skipped, because the runner had no Node, and a skip is
not a failure. Coverage was measured and thrown away. The image was never built. The appliance was
never started. All four are in, in four parallel jobs; the console job **fails on the word
`skipped`**, so the exact state CI was in for five releases is now red.

### Not in this release

`v0.17.1`'s vendor severity defaults — the seam is left, and its three open questions are recorded
**as questions** in `docs/plans/releases.md` (principle 8). No vendor MIB file enters this
repository, in this release or the next.

    make eval          c2e8a0ce…8b9b6f26   unchanged since v0.7.0
    behaviour record   acd2763b…9f2d2bae   unchanged, byte for byte
    trap path          5 modules, byte-identical by hash
    suite              1984 passed (from 1891)
    make dom           74 executed
    coverage           95.52 %
    mypy --strict      clean, 248 files
    ruff / format      clean, 286 files
    migrations         0016, user_version 16 — no new migration

## [0.16.7] - 2026-09-13 — "severity, and the number it refuses to invent"

The Overview has eight charts and, until this release, **answered no question about how bad
anything is**. *"How many critical alarms are active right now"* was on no screen in this console.
It is now the first thing on the first one — and on the corpus this repository ships, what it
answers is **not measured**, which is the release.

```
                                              before              after
  screens counting alarms by severity              0                  1
  severity bands on the Overview                   -                  6
  .stat tiles on the Overview                      4                  2
  words of prose on the Overview (admin)         122                114
  charts on the Overview                           8                  9
  DOM tests executed                              69                 74
  new routes / route parameters / migrations       -            0 / 0 / 0
```

### Phase 0 measured what could be counted before anything was drawn

**Ten corpus scenarios, replayed separately over real UDP into ten fresh databases: 2 119 alarms,
2 119 with `severity IS NULL`.** No scenario this repository ships produces one confirmed severity.

**The cause is not the vocabulary.** `varbind_profile.role` never once holds `severity`, so the
learner does not reject a candidate — it never nominates one. Gate one (200 observations of a
varbind on an NE) *is* reachable. Gate two is `confirm_ordinality`, which validates a candidate
ranking against **observed alarm lifetimes**, and a lifetime needs a close: the corpus closes
**one alarm in ten scenarios**. Severity is unknowable here because nothing ends — a property of
the corpus, and v0.17.0's work (#314).

**A declaration does not fill the column.** Measured end to end against a booted appliance:
`POST /api/labels {kind: severity}` returns 200 and writes `label(kind='severity')`; `alarm.severity`
afterwards is **1 716 of 1 716 still NULL**. Precedence is a read-time decision (#284), so the
census resolves declared-then-learned in one join (#315).

### Added

* **Active alarms by severity, on the Overview, above the fold at 390 px** (#312, #318). Six rows
  in scale order: critical, major, minor, low, `indeterminate`, and **not placed**. Four encodings
  per row — glyph, position, bar width, printed count — so it reads in greyscale.
* **`GET /api/stats` and the `/api/events` stream carry a nested `severity` census** (#316):
  `{active, placed: {rank: n}, unplaced, vendor_scaled, declared}`. **No new route, no new route
  parameter, no migration.** Measured on 1 716 active alarms, 25 runs: **0.563 ms** median for the
  resolved query against **0.387 ms** for the learned-only one.
* **The estate map links to the Graph screen** (#317), which is decision 5's answer. Measured:
  `edge` holds one `kind='device'` row at weight 0.0 on the three-scenario estate, so a topology on
  the Overview would draw two unconnected circles.

### Changed

* **A band the appliance has not graded reads `—`, not `0`** — prime directive 1 at the place it
  is hardest to hold. `0 critical of 1 684 active alarms` rendered in display type over an estate
  whose every alarm had been refused, with every assertion green. Once something *is* placed, an
  empty band reads `0`, because that emptiness is measured. Both states are asserted, each as the
  other's control.
* **`views/parts/pulse.js` split at the subject** (#318): 17 095 → 7 517 bytes, with
  `views/parts/keeping.js` (11 100 B) taking the appliance's own half. `pulse.js` is about the
  network; `keeping.js` is about the appliance.
* **`format.js::band` is the one place a rank becomes a name**, so the census panel and an alarm's
  own pill cannot disagree about what rank 3 is called.

### Removed

* **"Your labelling" on the Overview** — a heading, a 24-word paragraph and two stat tiles counting
  situations an editor could judge, on a screen with nothing to do about them. The Labelling screen
  answers the same question with the situations in front of the operator.

### Fixed

* **F114** — `test_the_four_named_quantities_are_drawn_as_four_and_never_composed` passed while a
  fifth chart titled **"Gate score"**, plotting the mean of the four, rendered on the Judge screen.
  Two causes: the count was `== 4` over charts *already known by name*, so a fifth of any other name
  never entered the set; and the composite refusal was a **denylist** four words long. And its scope
  was one file, while `verdict.js` declares the same four. Both repaired by **derivation**, and the
  guard's scope is now demonstrated by two tests of its own.
* **F115** — `/api/stats` and the `/api/events` stream **assembled the same payload twice**. They
  agreed until this release added a key to one of them; the console reads the other. One
  `api/livestats.py` now serves both.

### Known

* **F116** — `unitText` prints `1 alarms`. Open: it is a grammar defect in the shared chart
  vocabulary and moves every chart in the console, so it belongs in a commit of its own.
* **Severity over time is not drawn**, and the column, the query, the route parameter and the
  precondition a later release needs are written down instead (#313). `alarm.first_seen` spans
  **1.14 seconds** across the whole estate and `cleared_at` is non-null on **none** of it.

## [0.16.6] - 2026-09-12 — "the evidence screens"

Four screens stopped describing and started showing. **And the release's most valuable output is
the list of what cannot be drawn**, which is four of the charts the brief asked for.

```
                                              before              after
  charts on the Overview                           0                  8
  chart captions naming a source                   0                  8
  .stat tiles on the Overview                     11                  4
  words of prose on the Overview                 172                122
  paragraphs a chart replaced                      -                  6
  projections of the estate                        1                  2
  timeline controls, and all of them in the URL    2                  5
  charts on the Judge screen                       0                  3
  DOM tests executed                              52                 69
  new routes                                       -                  0
  new route parameters                             -                  0
  migrations                                       -                  0
```

### Phase 0 measured what could be drawn before anything was drawn

Nine charts had data behind them; **four did not**, and each one now names the table and the
columns a later release would need (`docs/plans/releases.md`):

* **a loss curve** — `challenger_run` has 24 columns and not one holds a per-iteration loss, a
  residual or a convergence trace; `iterations` is a count.
* **a residual distribution** — the only label in the schema is in `feedback`, and
  `incumbent_linked` is a comparison basis and never a target. **No module under `src/` mentions
  `shadow_opinion` and an `/api/` path**: `0009`'s posture is *no read below admin, on any route, in
  any format, ever*, and v0.9.0 added no route. It is a security decision before it is a chart.
* **fold results** — `evaluation_fold` stores which incident went into which fold, not what each
  fold scored.
* **top elements over a chosen week** — the appliance counts alarms *active now*. Measured:
  `GET /api/timeline?limit=1000` came back **full, 1 000 marks spanning 15.6 seconds**, so a chart
  titled *"last 7 days"* would have shown fifteen seconds of one storm. The Overview draws
  *"busiest now"* instead, which is exact.

**Two numbers in the brief were wrong and execution said so.** `cpu_series` is **24 points, not
240** — `SAMPLES_KEPT = 240` is the ring and `SERIES_POINTS = 24` is what is served. And
`promotion.metrics` already holds the four named quantities with clustered intervals, **both arms,
per decision, with a timestamp**, so *"which model is winning over time"* is a render rather than a
migration; `0013`'s own column comment is where that was written down.

### Added

* **Three chart types, reused everywhere** (#305, #307): `Series` (line or column over time),
  `Bars`, `Map`. Hand-written SVG and HTML, no dependency, no charting library. The measurement
  that settled it: descendants of `<svg #graph>` = **0** and of `<svg #timeline>` = **0** in the
  harness's rendered DOM, while the same document carries 21 hand-written `<svg>` elements and 42
  `<path>`s from `icons.js`. A d3 chart is invisible to every assertion here; a hand-written one is
  not.
* **The Overview answers five questions in the order an operator asks them** (#304) — what is
  happening, where, which element is worst, is the appliance keeping up, what has it learned.
* **A second projection of the estate** (#310), on the Overview and the Graph screen. The force
  drawing cannot answer *is this one element or the whole estate*: the radius saturates at **47**
  alarms, so `127.0.0.1` at 1 458 and `127.0.0.4` at 501 both draw at exactly **24.0 px** — and its
  layout is not deterministic, so two glances disagree. The grid is a pure function of the payload,
  asserted order-independent.
* **Urgency animation on heavily-alarming nodes and cells** (#309), as a CSS animation — so the
  stylesheet's existing `prefers-reduced-motion` block turns it off **by construction**. Driven both
  ways in Chromium: the ring stays 3 px and the node stroke 2 px with motion off, so urgency is
  never carried by motion alone.
* **The timeline's five controls live in its address** (#311): element, window, depth, chart type
  and element split. Element, window and depth become `ne_id`, `since` and `limit` **in SQL**; the
  other two never reach the server. A configured screen is a permalink.
* **Evidence over time** (#308): the verdict's three states, the four named quantities
  **uncomposed**, the seal's query count, and the trigger census — plus the three absences above,
  stated on the screen where someone would look for them.

### Changed

* **`GET /api/stats.resources` gains `bucket_s`**, how much time one served point covers. The host
  charts had no time axis and their caption claimed *"last 2 hours"* over a series that, on a
  freshly started appliance, held two points — the exact failure #306 exists to prevent, reached
  from the one direction that decision did not look. The caption now reads *"10 min of a 2.0 h
  window"*, derived from the data.
* **The Overview's own `Health` component is gone** — seven tiles, two headings, two paragraphs.
  The receiver's five counters **did not leave**: they are one secondary line, which is the shape
  #300 chose for the health panel.
* **A `line` needs two readings.** One drew an axis, printed a number beside it and left the plot
  empty; it now says *"only one reading so far"*.

### Fixed

* **F112** — F110's element-to-element exemption rests on *"the container is a flex row whose `gap`
  separates them"*, and nothing checks that it is. Three sites, and **the first two repairs were
  both wrong**: a CSS margin and a `content: " · "` separator each fixed the pixels and left
  `textContent` glued, which is what a screen reader announces and what an operator pastes into a
  ticket. The guard half is open and recorded.
* **F113** — that guard asked *"am I inside a template literal?"* with a backtick parity count,
  which assumes one template is open; this console nests them constantly and **two open templates
  give even parity**. Closing the hole exposed three real sites, **two shipped since v0.13.0**: the
  admin scorer's refusal rendered `the project floor is1 s.Your value was not applied`. Parity is
  now a scanner over template and `${…}` contexts.

### Behaviour changes, one

1. **`GET /api/stats.resources` gains `bucket_s`** (`WINDOW_S / SERIES_POINTS`, 300.0 s). Additive,
   and absent exactly where the whole `resources` block already was.

**No new route, no new route parameter, no migration.** The `/api` surface is unchanged at 52 and
the served surface moves 111 → 117, all six additions static modules. `make eval` is byte-identical
at `c2e8a0ce…`. Findings: **F112** issued (guard half open), **F113** issued and closed.

## [0.16.5] - 2026-09-06 — "the shell, corrected"

Six things the maintainer found by using v0.16.4's console. One of them was a defect of mine that
no guard in this repository could see, and it is the reason this release exists.

```
                                            before        after
  notification panel width, every width      26 px        338 px
  its notice text box                         0 px        291 px
  scrollHeight inside a 388 px panel       7 732 px       no scrolling needed
  a visible way to dismiss it                 none        an X in the header
  distance from bell to account name        723 px          0 px (same group)
  health panel text                    320 chars/49 words  157 chars, 3 meters
  host metrics shown                            0          3, measured
  member checkbox drawn / hit area        28 / 28 px     18 / 28 px (mouse)
  clearing a 12-member situation        12 presses       1, with a confirmation
```

### Fixed

* **F111 — the disclosure panels rendered 26 px wide at every width.** v0.16.4 added
  `.topbar { position: relative }` to anchor them and left `.disclosure { position: relative }` two
  rules above; only the nearer ancestor can be the containing block, so `100%` resolved against the
  28 px opener. Every warning wrapped to about one character per line, 7 732 px of scrollHeight
  inside a 388 px box. **The comment beside the rule asserted the anchor was `.topbar`** — a claim
  written in the same commit and never checked against a rendered box. Neither guard nor live pass
  could see it: the harness has no layout engine, and a 26 px panel overflows nothing.

### Added

* **CPU, memory and storage in the health control**, each with a two-hour sparkline (#300).
  v0.16.4 refused these because reading them was believed to need `psutil`. **It does not**:
  `/proc/stat` differenced, the cgroup's `memory.max` or `/proc/meminfo`, and `os.statvfs` on the
  database's filesystem. The dependency count is still **five**. The cgroup is read first, because
  inside the 512 MiB container v0.16.4's compose file defines, `/proc/meminfo` reports the host's
  16 GiB. #289's rule survives intact: an unreadable metric renders `—` and says "not measured",
  never `0%`, and a gap in a series **breaks** the sparkline rather than being drawn through.
* **A bulk hand-clear** — `POST /api/alarms/clear` (#301). The largest corpus situation holds
  1 051 members and the gesture was one button per row. Its set is named by `situation_id` and
  **derived on the server** from the members the caller can already see; a list of alarm ids would
  have been an existence oracle. It stays in the alarm namespace because a clear is a fact about an
  alarm's lifecycle, which is the rule `annotate.clear_alarm` has always stated.
* **A visible dismiss on both disclosures.** Escape, a second press and a click outside all worked
  in v0.16.4 and not one of them was visible.
* **Hover on the health control** (#303), behind `(hover: hover) and (pointer: fine)` with a 120 ms
  open and a 260 ms close delay. The bell keeps click only: a glance is not the same gesture as
  acting on a warning.

### Changed

* **The bell and the health control moved to the right-hand group**, beside the account controls.
  At 1440 px they sat 723 px from the account name, in the strip where the appliance says what it
  is doing rather than where the operator's own controls live.
* **The checkbox tick is 18 px and its hit area is still 28** (#302). F103's repair fixed the
  target and overshot the glyph. `appearance: none` and a drawn box, because a native control at
  `appearance: auto` discards padding — measured, after the obvious `content-box` approach gave a
  hit area of 18×28.
* **The health panel's four correlation counters became one secondary line.** They did not leave.

### Behaviour changes, three

1. **`GET /api/stats` gains a `resources` block** when the process runner is present: current CPU,
   memory and storage plus three 24-point series. Additive; absent without the runner, and the
   panel says so rather than showing zeros.
2. **`POST /api/alarms/clear` is new.** The `/api` surface moves 51 → 52.
3. **`alarm.clear_all` is a new audit action**, one row per batch **in addition to** the unchanged
   `alarm.clear` row per alarm — so "who cleared this alarm" is answered by the same query it
   always was.

No migration. Findings: **F111** issued and closed.

## [0.16.4] - 2026-09-06 — "the console's shell"

The screens were built; what surrounds them was not. This release is about the chrome an operator
lives inside and the one table they work in most — and **four of its nine items are closed by
deleting something**.

```
at 390 px, as an editor                    before                    after
  chrome above the work area               360 px of 844  (43 %)      94 px  (11 %)
  top bar                                  126 px, 4 wrapped rows     46 px, 1 row
  member-table columns                     11                          8
  member table overflowing its box         602 px                     377 px
    …the same at 820 px                    172 px                       0 px
  controls under the 28 px touch floor     72                           0
  frozen cell, scrolled fully right        (empty)                    127.0.0.2
  screens naming a timezone, visibly       0 of 9                      9 of 9
  scenarios reachable through `make`       1 of 13                    13 of 13
```

### The navbar (#288, #289)

**The four counters are gone.** They were a strip of numbers nobody acts on from a chrome bar, and
at 390 px they cost four wrapped rows. Two moved to the Overview, two became **filters** on
Situations, and the p95 latency moved into a health control that is now on every screen.

**A bell** holds the operator warnings — a mechanism that already existed and already interrupted,
in a strip an operator could read and never return to. A warning naming a parameter this console
knows about links to Settings; one that does not renders as text with **no** affordance, because a
control that navigates somewhere unhelpful teaches an operator that the links are noise. Measured
across every producer `runner.py` composes: **3 of 10** resolve. The other seven are why the
no-link case had to be designed rather than assumed away.

**A health control** shows queue depth, p95 latency, the derived trap rate with its window, and the
two receiver counters that mean loss. **CPU, RAM and disk are not there and are not invented** —
there is no `psutil`, no `resource` and no `/proc` read anywhere in `src/`, so the alternative to
four true numbers is adding a source. The panel says which four it shows and which it cannot.

### The member table (#293)

Eleven columns became eight: the three declarations and the hand-clear share one actions cell.
Every one of them is still reachable — driven at 390 px, the expanded card offers **76** controls
and **not one** is under the touch floor.

The mark column's header is now the select-all. One corpus situation holds **1 051** members, and
neither marking nor unmarking them one at a time is a gesture anybody completes.

### What an operator may do, and when (#291)

A situation that has already been judged folds Confirm and Split behind one *Adjust the grouping*
button naming what was recorded. **Nothing is removed from any status.** The surface turns on
whether a judgement is on record, not on the status: `open` is reached by a bare promote or a
rename, and hiding Confirm there would break the ordinary path.

`resolved` is settled by measurement rather than preference. The server accepts a verdict there
(200) and refuses all three restructuring gestures (409), so the console offers exactly that — the
post-incident review keeps its evidence path, and reopening stays a decision nobody has made.

### Every timestamp says which clock (#294)

Nine surfaces printed a time and **none named a zone in visible text**; `TIMEZONE` was referenced in
one place, a `title=`, so even there it was hover-only. Every absolute stamp now reads
`2026-09-06 14:32:07 -03:00`, with the offset **of the instant**, and the top bar names the zone.

### Also

* **The sidebar collapses to icons**, remembered in a cookie exactly as the theme is (#290). Every
  label stays in the accessible tree, because a collapsed rail's accessible name is the whole of its
  usability for a screen-reader operator.
* **Docker resource limits** (#298), with what each costs when it is hit: memory is an OOM kill that
  loses traps and records no gap, CPU is a throttle the health control shows. Raise memory first.
* **`make replay SCENARIO=` and `make replay-list`** (#295). One of thirteen scenarios had a target.
* **`docs/operate.md` states** that a trap's timestamp is when the appliance received the datagram,
  which a post-incident narrative can get wrong with nothing to contradict it.
* **Bug 2** — the gesture history rendered `by admin` and `2m` as `admin2m`, which a maintainer read
  as a counter incrementing. Two halves: a missing flex context (`.age`'s `margin-left: auto` was
  inert), and a missing character (a gap is not a space in `textContent`).

### Findings

* **F103** closed — the tap floor's selector excluded checkboxes, so the control an operator ticks
  most measured **13 × 13 px**. The exclusion is gone and they are 28 × 28. The guard that *passed*
  over them for two releases is the more useful repair: it normalised away the exact substring that
  was the defect.
* **F105** closed by deletion — `device.vendor` and `ne.vendor` are `NULL` on every row ever written
  (25 elements, 0 vendors, 2 252 alarms) and were rendered on **three** surfaces, one of them
  printing *"unknown vendor"* on hover for every node. Columns kept, reads gone, no migration.
* **F107** issued, disclosure half fixed — `/api/stats` scopes every counter and then appends an
  unshaped warning list that named the trap allowlist verbatim to any **viewer**. It now names the
  entry count. The oracle half (a whole-estate stale count reaching a scoped reader) is open.
* **F108** closed — a permalink followed from *inside* Situations changed the address and opened
  nothing, which is the case that happens during an incident.
* **F109** closed — #237 froze the first column so a row keeps its identity, and for an editor the
  first column is a checkbox: scrolled right at 390 px, a viewer's frozen cell read `127.0.0.0/24`
  and an editor's read nothing.
* **F110** closed — Bug 2 is a family. Three more sites, two pre-existing and **one written by this
  release**, with a narrow guard.

### Behaviour changes, declared

1. `GET /api/stats` gains **`new_situations`** and **`working_situations`** (additive).
2. `GET /api/graph`, `GET /api/entities`, `GET /api/entities/{ne_id}` drop **`vendor`**, and
   `GET /api/situations/{sid}` drops **`device_vendor`** — a field nothing wrote and nothing
   rendered (F105).
3. The denied-trap warning names the allowlist's **entry count** instead of its entries (F107).
4. Three static modules join the served set — `app/notices.js`, `views/parts/judge.js`,
   `views/parts/finder.js`. **The `/api` surface is unchanged at 51 routes.**

## [0.16.3] - 2026-09-06 — "the operator's declaration"

The appliance learns everything by itself and had nowhere for an operator to say what they already
knew. Three complaints came from three screens — *"I renamed the host and nothing changed in
Entities"*, *"what is Alarm Classes for if it changes nothing?"*, *"where is the severity?"* — and
they are **one gap**. This release builds the place, once, and applies it three times.

```
the class column, on a real corpus       before                    after
  a bare OID and nothing else            46 / 48  (95.8 %)         0 / 48   (0 %)
  a vendor beside the OID                 0 / 48                  46 / 48   (95.8 %)
  a NAME                                  2 / 48   (4.2 %)         2 / 48   (4.2 %)
    the third row is the honest one: a vendor is a qualifier, not a name. What moved is that an
    unnamed class reads `Huawei · 1.3.6.1.4.1.2011.5.104.1` instead of the OID alone. The 46
    missing NAMES arrive when an operator declares them, and the release is that they now can.

the naming gap, both halves              before                    after
  a name on the Situations row           resolved                  resolved
  the same name on Entities              NEVER — no join at all    resolved
  the same name on the Network Graph     resolved                  resolved
    one POST, three screens, demonstrated. `list_ne` selected five columns and joined no label
    while `entities.js` rendered `${ne.label || ne.ip}`, so the fallback was permanent.

device.id == ne.id       25 / 25 addresses on a populated corpus database — and a COINCIDENCE:
                         no foreign key, no shared key, two AUTOINCREMENT sequences. `0016`
                         migrates every label BY ADDRESS, so the repair does not spend it (#281)
alarm_class.name         == trap_name(oid) for 48/48; .vendor == vendor_of(oid) for 48/48.
                         Two stored derivations, dropped: `0008`'s own rule settles which of two
                         class-name homes loses, by measurement rather than by age (#280)
severity, learned          0 of 2 252     unchanged, and expected: the gates are byte-identical
label.kind        device|class -> ne|class|severity, with `qualifier` in the primary key so that
                         class + varbind is a read rule later, not a second migration (#283)
capabilities             35 -> 35        no new capability; a declaration is `label.write` (#260)
migrations             0015 -> 0016      one, additive, forward-only; data intact, chain verifying
/api surface             50 -> 51        DELETE /api/labels/{kind}/{target_id} — the revert (#284)
runtime dependencies      5 -> 5         no npm, no build step, no MIB parser, CSP unchanged
make eval                byte-identical: c2e8a0ce…8b9b6f26
make qa                  1 786 passed (was 1 759); 44 DOM (was 35); mypy 237 files
```

### The three declarations

* **Which equipment this is.** `label.kind='ne'`, and `device` is **gone rather than aliased**: it
  named `device.id` while the screen built to describe that element read `ne`.
* **What this trap means.** One home for the declared name and one **call** for the derived one.
  Where nothing is declared, the row shows the vendor the appliance had already resolved — beside
  the OID, never in the name's slot, because a vendor is not a name (#282).
* **How serious it is.** Per alarm class, from the five bundled severity tokens. **The learned value
  is never overwritten**: precedence is decided at read time, the pill marks which value it is
  showing and names the other in its tooltip, and *Clear* puts the appliance's own back (#284).

All three are made from the **member row**, where the operator already is. The naming control
leaves the Network Graph, which is where it had ended up only because there was nowhere else.

### One interruption, and it is rare by construction

A confirmation appears only when the appliance's severity is **confirmed** — 200 observations and
50 closed alarms whose lifetimes bore out the ordering — **and** the declaration is two or more
steps away on the 0–4 scale. **A cancel writes nothing.** The brief read a declined disagreement as
*"kept, and recorded"*; a confirmation that saves regardless is a notification wearing a dialog's
clothes, and the second one an operator meets is dismissed unread (#285). The disagreement itself is
recorded server-side on every declaration that lands, and consumed by nothing: no declaration
produces a training row and `PREREGISTRATION-0.16.0.md` §2 is unamended (#286).

It is also an element on the page rather than `globalThis.confirm`. The first draft reached for the
native dialog and `test_security_ui.py` refused it — rightly: a native dialog is invisible to the
DOM harness, which is how eight consecutive releases shipped a console defect no test could see.

### Fixed

* **F99** — an integer severity rank outside the vocabulary rendered as one identical pill, so a
  vendor numbering severity 10/20/30 lost an ordering the appliance had validated against observed
  lifetimes. Placed by **order within the field's set**, never by magnitude: order is all
  `confirm_ordinality` established. *(F99 as issued says they render as `low`; v0.16.2's pill had
  already moved them to UNKNOWN. The entry is corrected — the defect survives either reading.)*
* **F100** — closed by the two halves above, and reported as two numbers rather than one.
* **F104** — a name an operator typed carried a raw address past the field-shaping axis. A viewer
  received `device_ip: "127.0.0.0/24"` and `device_label: "core-sw at 127.0.0.10"` in one body, on
  two endpoints; `operator_name` leaked identically. Commit `8609962`'s defect in the declared
  register, on a release that puts declared text on four screens. **And the half the repair itself
  creates**: once a declaration is coarsened on the way out, matching the stored column in the
  search would hand the address straight back, so the declared columns are gated on what the needle
  contains (#287).
* **F106**, found by this release's own probe — `/api/entities` handed a viewer
  `ip: "127.0.0.0/24"` and `entities[0].key: "127.0.0.2"` in the same object, because `0003` defines
  a level-0 entity as the NE itself with `key = its IP`.

### Issued and left open

* **F105** — `device.vendor` and `ne.vendor` are never written by anything (25 rows, 0 vendors,
  after 2 252 alarms) and two screens render them, one with a tooltip describing a different
  table's column. Deleting a rendered column belongs with v0.16.4's shell.

### Removed

`alarm_class.name` and `alarm_class.vendor`; `kind='device'` from the label domain and from every
reader; the rename control on the Network Graph; two copies of the class-name precedence that had
been written out as `COALESCE(cl.label, c.name, c.oid)`; and `read_models.py`'s situation cluster,
which is `store/situation_reads.py` — split at the seam the import graph shows, taking both
sibling-inheritance edges with it and leaving a plain `StoreBase` mixin behind.

**Deliberately not built:** MIB loading. A file parser, a validation surface and an attack surface,
against a manual declaration that delivers the same operational value at a hundredth of the risk.
It becomes the automation of a gesture that now exists, when someone asks for it.

## [0.16.2] - 2026-09-05 — "a situation with a live alarm stops disappearing"

The idle sweep closed a situation that was still burning, and because a repeating trap increments
an existing alarm rather than raising a new one, nothing put it back. **The symptom of the defect
was the absence of a symptom.**

```
the sweep, four arms at one clock       before                    after
  stale, 2 ACTIVE members               resolved / idle           NOT selected, still live
  fresh, 2 ACTIVE members  (control)    not selected              not selected
  stale, 2 cleared members (control)    resolved / self_cleared   resolved / self_cleared
  stale, EMPTY bag         (control)    resolved / idle           resolved / idle
  replay after the sweep                alarm active, count 1->2, and the situation is LIVE

census (--gestures)    10 / 10 / 2 222  ->  16 / 16 / 2 227
  attributed: the census was re-run AT the repair commit and reads 16 there already. The
  promotion split moved it by ZERO — §4.1, the branch the plan predicted. §4.3's three checks
  are each verified rather than asserted, in the commit that made the change.
capabilities             34 -> 35        `situation.promote`, and it ships switched off
migrations             0015 -> 0015      NONE; the idle-but-active situation is DERIVED
runtime dependencies      5 -> 5         no npm, no build step, no bundler, CSP unchanged
plans pinned              6 -> 7         PREREGISTRATION-0.16.2.md, ratified before any repair
make eval                byte-identical: c2e8a0ce…8b9b6f26
make qa                  1 759 passed (was 1 741); 35 DOM (was 34); mypy 235 files
coverage                 95.89 % TWICE, module table byte-identical — see #279
```

### The critical repair (#274, #275)

`open` → `resolved` now requires that no member is still active, and the invariant is on the
`UPDATE` itself, not only in the caller — so a call site added later cannot reach the transition
around it. It binds the **appliance's** close and not the operator's: forbidding a human to close a
burning situation would leave one way to do it, hand-clearing every member first, which
manufactures `manual_clear` facts about alarms nobody cleared.

The other half of the population — live, untouched, still burning — is **derived, never stored**,
because staleness is a function of `now` and a column would be a cached clock reading. One
expression feeds both readers: an operator warning through the channel that already carries seven
degradations, and a `stale` badge on the card. `engine.py` is byte-identical.

### Promotion and assertion are two actions (#273, `PREREGISTRATION-0.16.2.md`)

Promoting a situation is **not** a `confirm`. §2.1 rejects that on the failure mode rather than on
taste: an operator required to promote in order to work a situation will promote to get on with the
shift, and the appliance would record, at scale, agreement that means *"I needed this out of my
way"*. `POST …/promote` is the explicit action that asserts nothing; `Confirm` is unchanged and is
still the only way to say a grouping is right. Six of the seven implicit promotions survive; the
one that goes is the move's **destination**, whose id was typed and which nobody had read.

### The severity pill (#276, #277)

A filled badge, five bands, five distinct glyphs, and a luminosity ladder so the order survives
deuteranopia. `critical` and `major` shared the glyph `▲` while sitting two hue-steps apart, which
made two adjacent bands one encoding. **`indeterminate` stops rendering as `low`** — the
vocabulary's own word for *"I do not know how serious this is"* was being shown as a level. No
MEDIUM is invented: no token maps to one.

Measured, and it is why v0.16.3 exists: **0 of 2 252 corpus alarms resolve a severity at all**, and
the binding floor is `SEVERITY_MIN_CLOSED = 50` against a corpus that closes **one** alarm.

### `api/routes/` (#278), and six findings

Twelve route modules move, derived from the import graph — twelve of twelve import the machinery
and none imports another. The behaviour record is the gate and it holds: not one `/api` route
changed.

```
F98   apisource.py globbed the API package NON-recursively behind a floor its own machinery
      cleared alone (74 398 vs 60 000). The move would have emptied the corpus four guards read
      and left all four green. Fixed with the move.
F99   an integer severity rank above 4 has no place on the five bands.       open, v0.16.3
F100  48 alarm classes, 2 with a name, 46 with a vendor — an operator reads a raw OID 96 % of the
      time while the vendor sits one column away. (The brief said these columns are never
      written; measured, that is wrong.)                                     open, v0.16.3
F101  five of twenty test citations in `src/` named a test that does not exist, including the
      guard `LIVE` had claimed since v0.16.0. Written, repointed, and guarded.
F102  two runtime paths were a count of `.parent`s; moving one repointed the console at a
      directory that does not exist and every static route answered 500.      fixed, guarded
F103  the member checkbox is 13 x 13 px because `--tap`'s selector excludes checkboxes.
      Found in a browser.                                                    open, v0.16.4
```

## [0.16.1] - 2026-09-04 — "the judge reads what the operator asserted, and then the screens learn a verb"

v0.16.0 moved `asserting_bags` off zero for the first time since v0.9.1. This release opens by
proving that the number is being read correctly, because it was not.

```
F90  the judge's marked set     members[:n] of LIVE membership  ->  feedback_exclusion ∩ snapshot
     measured, bag of 8 marked [7,8]:  reconstructed [1,2]      ->  [7,8]
     pairs asserted / measured / overlap:  12 / 12 / 4          ->  12 / 12 / 12
F89  the second correction      event recorded, label lost      ->  its own label, keyed on the bag
migrations             0014 -> 0015      (one; bag_key, and the index it widens)
census (--gestures)    10 / 10 / 2 222 / 1 050   unchanged, exactly as §4.1 predicted
capabilities             34 -> 34        no gesture was invented to fill a screen
runtime dependencies      5 -> 5         no npm, no build step, no bundler, CSP unchanged
console                  41 -> 42 modules, 6 349 -> 6 719 lines, three directories
make eval                byte-identical: c2e8a0ce…8b9b6f26
```

**F90 first, and not as a courtesy to the schedule.** `_asserting_bags` rebuilt the operator's
marked set as a positional prefix of the situation's *live* membership while the ids they actually
marked sat unread in `feedback_exclusion` — so two thirds of the pairs feeding
`asserted_negative_respected_rate`, one of the four quantities the promotion gate reads, were
asserted by nobody. It did not read as an error. It read as a rate. Every day it stayed open, every
gesture an operator made was producing evidence the gate would misread.

**F89 is the same question from the other end**, and both are answered once, in
[`PREREGISTRATION-0.16.1.md`](docs/analysis/PREREGISTRATION-0.16.1.md), ratified in a commit that
changes nothing else and pinned beside the other five. A bag's identity is its member **set**, so
the second correction of one situation is a second assertion — and three identical posts about an
unchanged bag still record once, which is exactly where F36 measured its defect in v0.7.1.

**Then the four screens, and two of them were told "none, and here is why".** A raise and a clear
are facts about an *alarm*; `PREREGISTRATION-0.16.0.md` §1 forbids letting one do the work of a
measurement about a grouping, so the timeline gains no gesture. An assertion that two *elements* are
unrelated is not in that plan's registered map, so the graph gains none either — what it gains is
that the gesture it already had, renaming a device, stopped being double-click-only on a screen
whose own caption said it was not keyboard-operable.

- **The search box asks the server** — `GET /api/situations?q=`, a parameter on the route that
  already lists situations rather than a route of its own (#266). It finds a situation by the name
  an operator gave it, by the derived name, by the device, by the trap OID and by the instance —
  and **only the ones that principal is shown**: a scoped editor cannot find a situation by the
  address of a member the redaction hides, and a viewer cannot confirm an address the console
  coarsens for them. Both refusals are demonstrated red with controls, and both were re-confirmed
  in a browser.
- **The timeline filters by element and by window, in SQL** (#268). The element is an `ne_id` — the
  key the scope predicate uses — never the rendered `device` string, because two elements can share
  a label and v0.7.0 already proved what that costs (F35). Measured: with a busy neighbour and
  `limit=1`, a query filter answers and a render filter returns nothing, which on screen reads as
  *"this element is quiet"*.
- **The graph answers its two questions from numbers already on the wire** (#270). `active_alarms`,
  `weight` and `n` have been served since v0.13.0 and were encoded as a radius that saturates at
  24 px and an opacity. **No route was added**; the tables are ordinary DOM, so the harness executes
  them — part of the one screen with no behavioural coverage, closed.
- **Alarm classes gains the control its own caption promised since v0.13.0** (#271): it said a label
  set here is cosmetic and there was no way to set one. Entities is kept unchanged and the reason is
  not that it exists — it is the graph's only keyboard-operable form and the only home of the
  varbind profiler.
- **`by user:2` becomes `by alice`** where the server will say so (#269). A deleted account falls
  back to the reference and never to an invented name, and `FIELD_RULES` decides who is told.
- **The card an operator is working on stops leaving the default tab** (#267), and a permalink to a
  situation that tab excludes stops rendering nothing at all (F97).

**Guards, not features.** F91's behaviour record no longer drops a route it cannot address — three
lines, one per principal that cannot mint a token. F92's promotion-path guard listed four modules
and the path had five; it is now **derived** from the import graph and covers seven, bounded at
`engine/evaluation/` for a measured reason (the unrestricted closure is 112 modules, four of which
name `entity_key` legitimately). F95: a UI guard attributed a write by a one-level filename scan and
lost the five gestures when the card moved one directory deeper.

**Six findings issued** — F93 (the hidden members of a scoped label are a count, not a set),
F94 (`MIGRATION.md` had no row for v0.16.0), F95, F96 (`/favicon.ico`'s 404, whose one-line fix is
forbidden by this appliance's own CSP), F97 — and four closed: F89, F90, F91, F92.

**The verdict is still `INSUFFICIENT_EVIDENCE`.** No number this release touches moves a floor, and
§4.4 of the amendment says so in advance.

## [0.16.0] - 2026-09-03 — "a situation is something you can work, and every gesture is evidence"

`asserting_bags` has been **0** since v0.9.1, against a registered floor of 50, and six releases of
evidence machinery have had nothing to count. The reason was never the machinery: a console that
offers `Confirm` and `Split` collects judgements about groupings and nothing else, while the thing
an operator actually does — moving an alarm that is in the wrong situation — was not expressible.
This release makes it expressible, and records it.

```
asserting_bags           0  ->  10        (floor 50; tools/corpus_census.py --gestures)
asserting_incidents      0  ->  10        (floor 30)
asserted_negative_pairs  0  ->  2 222     (1 050 of them from one 1 051-alarm storm)
situation.status         open|closed|merged  ->  new|open|resolved + resolution
capabilities            30  ->  34
migrations             0013 ->  0014      (one, additive, forward-only)
runtime dependencies     5  ->  5         no npm, no build step, no bundler, CSP unchanged
make eval                byte-identical:  c2e8a0ce…8b9b6f26
make qa                  1 696 passed (was 1 643); 32 DOM; mypy 227 files; coverage 95.74 %
```

**The verdict is still `INSUFFICIENT_EVIDENCE`**, which `PREREGISTRATION-0.16.0.md` §7 registers in
advance as an outcome rather than a failure. What changed is that the quantity is no longer
structurally zero — and the reason it is 10 rather than 41 is stated in
[`docs/plans/v0.16.1-visualisation.md`](docs/plans/v0.16.1-visualisation.md) §4 rather than left for
someone to infer.

### Five operations, and a distinction that is the whole release

**move**, **merge** and **split** say something about a **grouping**. **rename** and **clear an
alarm by hand** say something else — a label, and an alarm's lifecycle — and they reach the link
scorer through nothing at all. A zombie clear becoming a grouping signal would be the
`incumbent_linked` mistake in a new register, and two tests fail if a training row ever appears for
one (#254, #256, #259, #260).

Every gesture records its **membership snapshot**, so a bag is judged against what the operator was
looking at rather than against what it became. Every restructuring gesture carries a **confidence**
the operator sets on the card: stored per gesture and per actor, exactly as given, shrinking that
gesture's weight by at most 20 % (`m(c) = 0.6 + 0.4c`), applied at derivation and **never folded
into a stored weight**. Below 0.50 the action happens and produces no training row — the card says
so before you commit.

### Three states instead of one word

`closed` meant five different things. It is now `resolved` plus a `resolution` — `operator`,
`self_cleared`, `idle`, `merged`, `manual_clear` — and a historical close that nobody recorded a
reason for becomes `unattributed` rather than being given one it did not earn (#253). Situations
start `new`; the first operator gesture makes them `open` (#254).

Names are **two columns**: one the appliance derives from membership and recomputes when membership
changes, one the operator writes. **No model proposes a name in this release**, and the guard is
that exactly one statement in the tree writes `operator_name`.

### The defect only a browser could find

`GET /api/events` — the stream every screen reads — still asked for situations with
`status = 'open'`. After `0014` the correlator creates them as `new`, so the stream published an
**empty list** while the header beside it said two. The behaviour record does not drive the stream,
the DOM harness feeds views from captured payloads rather than from it, and every API test asks
`/api/situations` directly and was right all along. Found in Chromium at three widths, in the live
pass, on the last day. **F91** is the same shape one layer up.

### The derived name was a protected field and was not treated as one

`derived_name` is built from device addresses. A viewer shown `127.0.0.0/24` for a device on the
graph was shown `Storm -> 127.0.0.2` beside it, `GET /api/situations` passed its body through no
shaping at all — correctly, until this release gave the row its first protected field — and a
**scoped** reader's list carried a name built partly from devices outside their scope. Caught by
`test_sse_stream_graph_is_shaped_for_viewer`, which asserts on the stream's raw text and is the only
guard that could see an address hiding inside a composite string. The name is now coarsened by the
field axis, dropped on the list and **recomputed from the visible members** on the detail.

### Four findings, none of them fixed here

**F89** a second move out of one situation loses its label to F36's unique index. **F90** the
promotion judge reconstructs an operator's marked set positionally, from live membership — measured
with a control, invisible since v0.9.1 because nothing reached it, and reachable now. **F91** the
behaviour record silently drops a route it cannot address: `DELETE /api/tokens/{tid}` is missing
from three of four principals. **F92** the promotion-path guard names four modules and the path has
five. A defect found inside a feature release is an entry, not a patch.

## [0.15.5] - 2026-08-30 — "three places the source and the screen disagreed"

**All three were reported by someone using the console, and all three were invisible to the
suite.** Nothing here is a server change: `src/netcorenoc/` moves only under `ui/`.

```
the login password field  ->  18 px wide (5 % of the row)      ->  298 px (90 %)
the reveal button         ->  330 px (100 % of the row)        ->  28 px, and to the LEFT
the two of them           ->  8 px out of vertical alignment   ->  0
the password input        ->  spellcheck="true" in the DOM     ->  "false", as the source said
the theme control         ->  1 click in 3 changed nothing     ->  every click changes it
its label                 ->  "Theme: system." through 6       ->  names the state, every click
                              clicks while the page flipped
make eval                 ->  byte-identical: c2e8a0ce…8b9b6f26
make qa                   ->  1643 passed (was 1640).  runtime deps: 5.  migrations: 0
```

### The reveal button took the whole row (F86)

`.login-card input` and `.login-card button` — element selectors, descendant combinator — were
written when the card held one input and one submit button, so *"every button in this card"* and
*"the card's submit button"* named the same thing. v0.15.3 composed a `PasswordInput` into the
card, and the selector reached its controls too. `width: 100%` on an item that is also `flex: none`
is a base size with shrink factor 0: the reveal button claimed the row and would not give any back,
and the field's `flex: 1` collapsed beside it. The 8 px was `margin-top` on one and `margin-bottom`
on the other, neither meant for a row.

**This is F85's shape in CSS** — a rule whose meaning silently widens every time the tree beneath
it grows — and it sat four lines below a comment claiming *"the input keeps the full width the
login card gives it"*, which the stylesheet had never delivered. `>` says what the rules always
meant.

The icon now sits to the **left** of the field, by `order: -1` rather than by reordering the
markup. The button stays after the input in the DOM on purpose: tabbing out of the username box
has to land in the password box, not on a toggle you did not ask for. Measured focus order is
unchanged — username → password → reveal → submit.

### The theme control had a dead click, and lied about its state (F87)

Two causes, and the second is what made the first unreadable.

**Three states, two appearances.** The ring was `dark → light → system → dark`. "system" is not an
appearance, it is a deferral, and it always resolves to one of the two beside it — so one
transition in three did nothing on screen. No ordering of three states over two appearances avoids
that, so the control is now a toggle: it switches to whichever appearance you are not looking at.

**And it never re-rendered.** `TopBar` read the theme from a cookie, which Preact cannot observe,
and leaned on a `forceRepaint()` that called `store.setConnection(store.get().connection)` — a
setter that returns early when the value is unchanged. It published nothing. Through six clicks the
label read `Theme: system.` while the page went dark, light, dark, light; the only thing moving was
`data-theme`, which is written straight to the document root and never goes through the framework.
The helper also stamped a `data-theme-tick` attribute that **nothing has ever read**. Both are
deleted, and the control is a class component holding its own state — Preact core is vendored
without hooks (ADR #174), so state means a class.

**One deliberate loss.** `system` is still what an absent cookie means, still the state a fresh
install boots in, and still draws its own icon until the first click — but it is no longer a stop
on the ring, so the control cannot return to it. Getting back to "follow the system" means clearing
`ncn_theme`. Three states need a menu, and a menu is a design decision rather than a bug fix.

### The appliance asked the browser to spell-check passwords (F88)

`spellcheck` is an IDL **boolean**. Preact assigns the property, and the non-empty string `"false"`
is truthy — so `spellcheck="false"` in every source file rendered `spellcheck="true"` in the DOM,
on both password fields and the username field. Some browser builds send spell-checked text to a
remote service. It was in the served HTML for two releases, and reading either the source or the
DOM alone could not have told you: they disagreed. `spellcheck=${false}` is the fix, and the guard
names `draggable` and `contenteditable` beside it because they carry the identical trap.

### What the guards do differently now

The existing theme test read only the **endpoint** state after a click, which can see neither
defect — a dead click and a frozen label both leave a perfectly reasonable endpoint. The DOM
harness now records a per-click **trail**, and the new assertion walks it. Each of the three new
guards was demonstrated red under injection with a passing control, including one injection that
re-freezes the label specifically.

## [0.15.4] - 2026-08-30 — "the wheel the container actually builds"

**A packaging fix, and a lesson about what the tests were measuring.** v0.15.3 shipped a console
that worked in every environment this project tests in and was missing five modules in the one it
ships as. If you ran v0.15.3 in Docker, `docker compose up --build` is the whole upgrade; if you
did not, nothing here changes behaviour.

```
a wheel built as the Dockerfile builds one  ->  console minus 5 modules, 5 RuntimeErrors on
                                                first page load  ->  50 of 50 UI files
package-data                                ->  one glob per directory level  ->  one recursive
                                                glob (+ one line for the dotted dir `**` skips)
the guard for exactly this (F12)            ->  matched with fnmatch, whose `*` crosses `/`
                                            ->  glob, the matcher setuptools uses
what verified a release                     ->  a wheel completed by MANIFEST.in's `graft src`,
                                                which the image build never sees  ->  a wheel
                                                built from a Docker-shaped context and read
the guard on the glob guard                 ->  green in the exact state that shipped F85
                                            ->  red; it drives the real expander now
make eval                                   ->  byte-identical: c2e8a0ce…8b9b6f26
make qa                                     ->  1640 passed (was 1637);  coverage 95.89 %
migrations: 0.  src/netcorenoc/: unchanged except the version string
```

### Five modules that never reached the container (F85, #251)

`[tool.setuptools.package-data]` listed `ui/*.js`, `ui/app/*.js`, `ui/app/views/*.js` — a line
added each time the console grew a level. v0.15.3 added `ui/app/views/parts/` (#239) and did not
add the fourth line, so the container's first page load raised five
`RuntimeError: File at path … does not exist`, for `why.js`, `verdict.js`, `facts.js`, `model.js`
and `retention.js`. A glob per level is a rule that must be re-obeyed every time the tree changes,
which is the same shape as the defect it was introduced to fix. `ui/**/*` cannot miss a level, so
adding a directory is no longer a packaging decision. (`**` does not match a path component
beginning with a dot, so the RFC 9116 `ui/.well-known/security.txt` keeps its own line — found by
building a wheel and looking for the file, not by reading the glob.)

**Why nothing caught it is the larger half of this entry**, and it is not really about globs.
`package-data` is not the only thing that decides a wheel's contents: setuptools runs `egg_info`
during every build, and `include_package_data` ships whatever `SOURCES.txt` names. **Two different
files each complete a wheel that `package-data` leaves incomplete, and the container has neither.**

1. **`MANIFEST.in`, whose `graft src` names every file under `src/`.** The Dockerfile copies
   `pyproject.toml README.md LICENSE` and `src/` and not this, so it is absent from the image build
   and present in every build done in this repository — a clean clone and CI's included.
2. **`src/netcorenoc.egg-info/SOURCES.txt`**, left by `pip install -e` and excluded by
   `.dockerignore`. Redundant with the first here; sufficient on its own.

So v0.15.3's own delivery check — install the wheel, boot it, fetch all 45 declared assets — was
green at 45/45 while measuring a wheel no container can build. And a third mask, independent of
those: **the F12 guard used a looser matcher than the tool it was speaking for.**
`fnmatch("ui/app/views/parts/why.js", "ui/*.js")` is `True`; setuptools expands package-data with
`glob`, whose `*` stops at a separator. The guard agreed with the packaging for the same reason a
spell-checker agrees with a misspelling it also holds.

Reproduced as a 2×2 over those two files with v0.15.3's globs held fixed. The last row is what
`docker build` gets; the first three are every machine anyone develops this on:

```
MANIFEST.in  src/*.egg-info | parts modules in the wheel   UI files
True         True           | 5/5                          50
True         False          | 5/5                          50
False        True           | 5/5                          50
False        False          | 0/5                          45   <- the container
```

The first attempt at that reproduction copied `MANIFEST.in` into the context and came out green —
the same mistake as the one under investigation, which is why the guard now **asserts** neither
file is present rather than merely leaving them out.

### Three guards, and only one of them reasons about globs

Package-data patterns are now expanded by one function using `glob.glob(..., recursive=True)`, and
the guard-on-the-guard drives **that function** with the F85 case rather than restating a property
of `fnmatch` — so reverting the matcher turns both red. It did not before: in the exact state that
shipped F85 (looser matcher, per-level globs) the guard-on-the-guard was green.

The third is the one that would have caught F85 without anyone knowing what to suspect.
`test_a_wheel_built_the_way_docker_builds_one_carries_every_declared_asset` builds a real wheel and
reads it. Its context is derived from the Dockerfile's own `COPY` lines instead of a hand-written
list, and it refuses to run if `MANIFEST.in` or an `.egg-info` is in it, because the one way this
guard goes quiet is somebody making its context "more realistic". Each guard was demonstrated red
against the per-level globs and green after.

`MANIFEST.in` keeps `graft src`. An sdist that could not rebuild the wheel would be the worse
defect; what had to change is that the guard builds without it.

Verification for this release was done the same way: the wheel from a Docker-shaped context,
installed into a clean virtualenv that has never seen `src/`, booted, and every declared asset
fetched over HTTP from the installed package. 50 of 50 UI files in the wheel, 45 of 45 assets
served 200, and the five F85 modules by name.

**Nothing else is in this release.** `src/netcorenoc/` is byte-identical to v0.15.3 apart from the
version string, and the behaviour record moves on exactly eight rows — `/healthz` and
`/openapi.json` for each of the four principals, which are the two responses that carry it.

## [0.15.3] - 2026-08-29 — "the console: identity, responsiveness, and the door that could lock you out"

**F79 first: a sole admin could demote itself and the appliance was gone.** Reproduced with a
control and a restart before anything was changed, then repaired at both of its two causes.
Everything else in this release is the console — an identity somebody chose, three widths instead
of one, and a screen that answers *"is this grouping sound?"* before it answers anything else.

```
the sole admin        ->  demote self: 200, then 0 enabled admins forever  ->  400, refused
a database with none  ->  bootstrap counted USERS, so never ran again      ->  counts admins, recovers
the forced first pw   ->  one field, no confirm, no meter, no reveal       ->  all three, mismatch caught
"other sessions"      ->  said unaffected; the route revokes them all      ->  says what it does
17 Unicode glyphs     ->  four Unicode blocks, the font stack's weight     ->  17 drawn, one family
the type scale        ->  ratios 1.09, 1.08, 1.23, 1.31                    ->  1.2, five steps
spacing               ->  4, 8, 12, 18, 28 (two off their own grid)        ->  4/8/12/16/24/32
Density               ->  a knob scaling one step of the type ramp         ->  removed, mechanism too
the wordmark          ->  a <div>; clicking it did nothing                 ->  a link to Overview
820 px                ->  byte-identical to 1440 px, no tablet at all      ->  its own layout
the account screen    ->  30 elements 2 443 px off a 390 px phone          ->  0, at all 3 widths
the timeline y axis   ->  every device label at x = -9, since v0.13.0      ->  0
"why these grouped"   ->  30 rows of thousands, chosen by insertion order  ->  summary + all links
the link threshold    ->  never served; the sentence printed without it    ->  served, margin shown
touch targets         ->  3-5 per view under 24 px, at every width         ->  0-1, in running text
console prose         ->  2 275 words rendered                             ->  2 004 (-12 %)
make eval             ->  byte-identical: c2e8a0ce…8b9b6f26
make qa               ->  1637 passed (was 1613);  coverage 95.89 %
DOM tests executed    ->  31 (was 29).  runtime deps: 5.  migrations: 0
```

### The appliance cannot be locked out of itself (F79)

Two independent defects composed into one lost environment. `POST /api/users/{uid}/role` checked
that the user existed and nothing else, so an admin could set its own role to `viewer` while it was
the only one — and the same route revokes the caller's sessions, so the operator was signed out
mid-gesture. Then `auth.bootstrap_admin` guarded on `count_users() > 0` — **users, not admins** — so
one second account stopped it ever running again. A restart did not help. There was no CLI recovery.
The only remedy was deleting the database.

One predicate now answers it for every transition that could cause the state, taking the transition
as a parameter rather than existing three times, and the bootstrap counts the quantity its own name
always implied. An operator locked out today restarts and reads the new password from the log.

Two things the repair taught that were not in the brief. A **session** admin can never reach the
deletion arm — self-deletion is refused and any other target leaves the caller — but an admin
**service token** has no `user_id`, so it can delete the last account that can sign in and leave the
appliance one revocation from having no administrator; that is why the count is over `user` rows.
And **there is no disable route in this product**: `user.disabled` is read by `perform_login` and
written by nothing, so rather than add a feature to make a guard testable, the predicate takes
`disabling` and a structural test fails the day any module writes that column.

### The password surface (F82, #240)

The forced first change had one field, so the two entries could not disagree — a typo was committed
rather than caught, with the bootstrap password already shown for the last time. It now has a
confirmation refused before anything is sent, a length meter, and a keyboard-reachable reveal, from
one module the account screen shares. The bounds are **served**: a `12` written into JavaScript
would be a second source of truth about what a valid password is, and the meter measures length
because length is the only rule the server has.

F82 turned up while wiring it: the account screen said *"Other sessions are unaffected."* The route
calls `revoke_user_sessions`, its own return says *"sign in again"*, and a test has asserted since
v0.2.0 that both sessions die. The suite knew; the caption said the opposite for three releases.

### Two-factor and recovery are declarations, not placeholders (#238)

Visible on the sign-in card and the account screen, naming **v0.17.0** and that 2FA will be
**required for admins**, with no control, no mechanism, and no region reserved on the other fifteen
screens — which is the specific failure #219 recorded.

### An identity somebody chose (#236, #243)

Seventeen Unicode glyphs from four Unicode blocks become seventeen drawn marks on one grid: 24x24,
1.5 stroke, `currentColor`, sized at 1em so an icon is the size of the text beside it. Drawn rather
than acquired because the build-step guard rules out a package and the supply-chain guard would want
a checksum and a licence for a vendored set. `tests/test_icons.py` fails in both directions, and it
caught two flaws in **itself** first: it read every `name="…"` in the tree, so `<input
name="username">` was reported as a missing icon, and it missed `name=${shown ? "eye-off" : "eye"}`,
so two live icons read as unused.

The type scale is 11/13/16/19/23 at a 1.2 ratio; spacing is a 4 px grid it actually sits on. Density
is gone **with its mechanism** (#235, #45) — it scaled four spacing tokens and one step of the type
ramp, so the two densities had different sets of relationships and one was chosen by nobody.

### Three widths, measured as geometry (#237)

    desktop 1440   sidebar 232x900 column, nav in 16 rows, work 1208 wide
    tablet   820   sidebar 820x48 strip,   nav in  1 row,  work  820 wide
    phone    390   sidebar 390x48 strip,   work 390, topbar wrapped to 126

Text comparison could never have seen the difference and did not — the earlier pass reported the
same words at 820 and 1440 while the layouts were identical, and again after they stopped being.
Dense tables scroll with the **first column frozen**: dropping columns by priority was refused
because an operator cannot tell a dropped column from an empty one, and cards everywhere was refused
because the vertical scan is what the density is for.

### "Why these were grouped", for a storm (#245, F84)

It rendered `links.slice(0, 30)` — in a 400-trap storm, thirty rows chosen by insertion order. It
now leads with a summary over **every** link: the weakest link and its margin over the threshold,
the strongest, the count, and which of the three named terms is carrying the grouping. The per-link
decomposition is one interaction away and **complete** when opened.

The completeness guard was **green under injection first**: driven against the corpus's own sixteen
links, reinstating the thirty-row cap changed nothing, so the assertion could not fail for the defect
it names. The fixture is now grown past thirty from the real captured links.

F84 was found by looking at the screen: the console has passed `detail.threshold` to this section
since v0.13.0 and the route never sent it, so the sentence degraded to something grammatical with
the only checkable number missing. `GET /api/situations/{sid}` now joins the configuration the
situation names — not the active one, because the threshold this grouping cleared is a fact about
when it was decided.

### What a browser found that no assertion could

F80, F83 and F84 were all found by opening the product, and none of them is visible to the DOM
harness — it has no layout, substitutes a recording double for d3, and cannot see whitespace. That
is now **seven** defects across two releases on screens the suite reports green. Two whitespace
collapses in this release's own new copy were caught the same way.

The same live pass found the F80 repair itself half-done, which is the last commit under this tag.
`.kv dd` is a grid item, and a grid item's default `min-width: auto` refuses to shrink below its
content's min-content width — so the column could not narrow and the capability chips never got the
chance to wrap. That, not the text, is what put the account screen 2 443 px off a phone. Reaching
for the text first was wrong twice: `overflow-wrap: anywhere` broke `classes.read` across two lines
as `classes.rea` / `d`, and softening it to `break-word` stopped the wrapping entirely, because the
chips carry no whitespace between them and the browser had no break opportunity in the whole run.
The list is now a wrapping flex row with `min-width: 0` on the column; `break-word` stays for the
case it is actually for, a 64-hex run id. That is where the `0, at all 3 widths` above comes from.

### Structure

`crosscutting/administration.py` splits out of `auth.py`, which reached 409 lines against a 400
guard with `DEBT_ALLOWLIST` empty and staying empty. Four modules under `views/` that were never
views move to `views/parts/` — `registry.js` imports seventeen and the directory held twenty-one.
**Nothing else moved**: a directory per tier would rename 15 static routes and rewrite 37 imports to
encode a fact the import graph already states (#239).

### Not in this release

The situation lifecycle — states, self-clear, manual clear, merge, split, move, semantic naming —
is v0.16.0, deliberately, because it is a schema and domain change. What was noticed while working
inside `situations.js` is in [`docs/plans/v0.16.0-situation-lifecycle.md`](docs/plans/v0.16.0-situation-lifecycle.md)
rather than half-built here.

## [0.15.2] - 2026-08-28 — "the fine-toothed comb"

**The product was installed six ways, booted, driven in a browser and fed real traps — and what
that found was fixed.** Thirteen findings, F66 to F78, every one reproduced by execution with a
control before anything was changed.

```
a failed startup       ->  hung, ignoring SIGTERM (32.0 s to SIGKILL)  ->  exits in ~1 s
5 env variables        ->  a bare ValueError naming the value          ->  named, exit 2
an unusable allowlist  ->  stored, 200 "saved", next boot cannot start ->  422, nothing written
the detail panel       ->  "Select something…" on 17 of 17 screens     ->  removed (#219)
a link row at 390 px   ->  51 px over, 30/30 pair labels clipped       ->  wraps, 0 clipped
queue_depth, receiver  ->  served on every poll, rendered nowhere      ->  on the Overview
a denied trap          ->  0 log lines, 0 warnings, 0 counters         ->  a banner naming it
a first boot           ->  13 migrations applied, silently             ->  says so, and where
the network graph      ->  1 of 4 nodes on canvas, r up to 80.7 px     ->  4 of 4, r <= 24
d3 (279 706 bytes)     ->  loaded on all 17 screens                    ->  on the 2 that draw
an operation test      ->  295 lines nothing ran                       ->  9 tests, 17 s, in qa
make eval              ->  byte-identical: c2e8a0ce…8b9b6f26
make qa                ->  1613 passed (was 1576);  coverage 95.94 %
runtime deps           ->  5, unchanged since v0.2.0.  migrations: 0
```

### The startup nobody could stop

`runner.run` opened the store and then did seventy lines of work outside any `try`; its cleanup
re-raised a failed task's exception before reaching the close; and uvicorn calls `sys.exit()` when
it cannot bind, which is a `BaseException` and leaves the event loop without resuming the
coroutine. Any of the three left an `aiosqlite` connection open on a **non-daemon** thread, and the
process then blocked in `threading._shutdown` after printing its traceback. Under
`Restart=on-failure` and `restart: unless-stopped`, a hung process is never restarted.

Measured with `timeout --signal=TERM --kill-after=20`, five ordinary misconfigurations — including
*the HTTP port is already in use* — needed `SIGKILL` after 32.0 s. Controls: the two settings the
design already refuses by name exited in 0.5 s. All five now exit (F66, #225).

Every setting that cannot be read now names itself and exits 2, including the ports' range, both
TLS variables, and `NETCORENOC_DB` (F69, #226). `POST /api/config` parses the allowlist **before**
the write, so an admin can no longer store a value that stops the next boot (F75).

### The console

The detail panel was populated by **no view of seventeen** — `situations.js` imported `setContext`
and never called it — so 320 px of every screen said *"Select something to see its detail here."*
It is removed rather than completed (#219). The per-term contributions turned out **not** to be
unreachable on a phone, as the brief predicted: they render in the work area. What was unreachable
is the pair each row is about, clipped 51 px past a non-wrapping row (F67, #220).

`queue_depth` and the five `receiver.*` counters are on the Overview, with a trap rate derived
between two polls and labelled with the window it covers (#222). A denied trap raises a warning
through the channel that already banners on every screen — a counter read, never a log line per
packet (F68, #227). A boot says which database it opened and which migrations it applied.

The Network graph had no centring force, an uncapped node radius and no `viewBox`: **one of four
nodes was on the canvas** and the largest circle covered 3.79 % of it. Now four of four, and 0.34 %
(F77). It is the screen the DOM harness substitutes a double for, so no test in this repository
could have seen it (#231).

### What was removed

`app/context.js`, `router.situationHref`, `registry.declaredCapabilities`, an `overview.js`
suppression for an import nobody used, and `eval/simulation/{drive_http,measure}.py` — 573 lines
imported by nothing (#232). Removal was chosen over completion in each case, and each decision says
so rather than calling a deletion a cleanup.

### The operation test

`tests/test_operation.py` boots `python -m netcorenoc.main`, sends sixteen real SNMPv2c PDUs from
four bindable sources over a real socket at their real 0.3 s gaps, and reads the outcome back over
HTTP as an admin and as a minted viewer token. Deterministic across two processes on a clock-free
projection of what the appliance decided (#224).

Driving it found **F76**: `dual_incident.json` says *"must stay separate"* and the two incidents
merge inside five seconds. Offline the same scenario scores `over_merge_rate 1.000, ari 0.000`
while `make eval` reports `pairwise_f1 1.0000` in aggregate and passes — a 16-event scenario is
0.2 % of a corpus a 1 051-event storm dominates. The test pins the failure deliberately and its
message says what to do when it goes red; repairing the correlator is F58/F61's disposition.

### Also

`flake.nix` had said `version = "0.1.0"` for fifteen releases and `tools/release_check.py` read
three of the four declarations (F73, #230). Three shipped files cited documents deleted in v0.15.0,
one of them **on screen** (F71). The timeline's caption described two encodings it does not have
(F72). F65's count was 50 and not 67 (F70), and it gets a reading rule rather than a guard (#229).
F63's intermittent test goes from 1 failure in 60 to 0 in 60, with a control proving the speed
check is still reachable. `MIGRATION.md` gains the two rows it owed — v0.15.1 shipped without one —
and the sentence above its table, which had said *"six of nineteen"* over twenty rows since
v0.15.0, is recomputed rather than nudged (F78).

## [0.15.1] - 2026-08-27 — "the package tree"

**The filesystem starts telling the truth about the architecture. No behaviour changes.**

```
src/netcorenoc/ root   ->  58 modules  ->  4   (__init__, __main__, main, runner: the entry surface)
layers                 ->  a dict of 62 module names in a test  ->  5 directories on disk
engine                 ->  46 modules in one bucket  ->  6 domains, ZERO cycles between them
imports rewritten      ->  666 statements across src/, tests/, eval/ and tools/
content census         ->  61 moved files, ZERO changed beyond their imports
make eval              ->  byte-identical: c2e8a0ce…8b9b6f26
make qa                ->  1576 passed (was 1558);  coverage 95.97 %
runtime deps           ->  5, unchanged since v0.2.0.  dev deps unchanged
migrations             ->  0.  routes, capabilities, audit actions, console: unchanged
```

The layer rule — *a layer may import downward and may import cross-cutting, never upward* — has
been tested since v0.7.3 against a dictionary of module names kept in `tests/test_layers.py`. The
disk was flat, so a module's layer was a **declaration**: a new module landed correctly only if its
author remembered to add a line. It is now an **observation** (#207): five top-level directories,
each one a layer, and a package root closed to everything but the four entry modules.

`engine` held 46 of the 62 mapped modules, which is a true description of all of them and a useful
description of none. It is six domains now — `correlate/`, `dataset/`, `model/`, `evaluation/`,
`report/`, `operate/` — derived from the import graph rather than from the names (#208). Measured
over the same 190 edges they form a **strict order with no cycles**; the grouping the plan sketched
has nine.

### The gate this release needed and did not have

`tests/behaviour_identity.py` seeds four databases from `eval/corpus/fiber_cut.json` through the
real ingest path at a fixed clock, drives every route the app registers as anonymous, viewer,
editor and admin, and pins the result at
`f2a74ae5bdde7c1bd615abc6516049b259c763e5887c917c53983c44ce47a9c7`. **That hash is unchanged from
before the first `git mv` to after the last one.** In a release that is entirely moves, *"the tests
pass"* is a weaker claim than *"the HTTP surface is unchanged"*, because the assertions were written
against the same code that produces the shape.

### Fixed

- **F64** — `test_documentation.py` filtered `COMMENT` and `STRING`, and PEP 701 moved f-strings out
  of `STRING` in Python 3.12, so every citation inside one has been invisible since. Exactly one
  existed, `#176` in `test_security_ui.py`, and v0.15.0 deleted that entry *on the measurement this
  blind spot corrupted*. The filter is widened and the entry restored (#215).
- **Three guards that had stopped checking anything**, each found by the move rather than by review:
  the seal-isolation guard read `node.module.split(".")[1]`, F24's receiver guard looked for
  `"netcorenoc.scoring"` as a substring, and five more read modules at literal paths with
  `if not path.exists(): continue`. All read `util.module_path` / `util.imported_modules` now, and
  a missing module raises instead of skipping.

### Changed

- The module-size guard measures a module's **body**, not its imports (#218). A longer import path
  wraps and had pushed `capture.py` from 398 lines to 402 — a package reorganisation consuming a
  module's budget. `COHESION_EXEMPT_CEILING` for `engine.py` **falls** 580 → 545.
- Two levels of package nesting where earned, never three (#210). It was one, never two, and the
  guard that said so exists — contrary to the plan for this release, which reports finding none.
- `from netcorenoc.correlate import …` is now `from netcorenoc.engine.correlate.correlate import …`,
  with **no compatibility re-exports** (#213). `netcorenoc`, `netcorenoc.main`, `netcorenoc.api` and
  `netcorenoc.store` are unchanged, so every documented entry point still resolves and
  `python -m netcorenoc.main` still starts the correlator.

### Known

- **F65** — 67 module paths written in prose still name the pre-move import path. None is an import;
  rewriting them inside a move release would forfeit the census that makes the move reviewable.

## [0.15.0] - 2026-08-27 — "the repository"

**`docs/` stops being a warehouse. `src/` does not change.**

```
docs/          ->  62 310 lines across 253 files  ->  5 206 across 24   (-91.65 %)
src/           ->  165 files, EXACTLY ONE differing by SHA-256: the version string
make eval      ->  byte-identical: c2e8a0ce…8b9b6f26
make qa        ->  1558 passed  (was 1554);  coverage ~96 %, not deterministic
runtime deps   ->  5, unchanged since v0.2.0.  dev deps unchanged
migrations     ->  0.  routes, capabilities, audit actions, console: unchanged
```

Coverage is quoted without a second decimal deliberately: five `make qa` runs on this tree gave
95.92, 95.94, 95.99, 96.01 and 96.01 %. The gate floor is 85 %, so the variation decides nothing —
but a figure that moves between runs should not be written down as though it did not.

### Removed

- **`docs/gates/`, `docs/scope/`, `docs/releases/` and `docs/security/`** — 242 files, 53 137
  lines: 173 phase-gate files, 22 scope documents, 24 build reports and 23 security reviews. One
  commit, because the four are a single strongly connected component of the internal link graph and
  no ordering deletes them separately and stays green.
- **`docs/architecture/`** — 21 files, 7 220 lines. Eighteen were drafts for releases that have
  since shipped; a draft for shipped code is a description written before the thing it describes.
- **291 lines of *"found while building vX"*** across eight sections of `ROADMAP.md`, which goes
  from 649 lines to 148 and is now open items only.
- **50 decision entries no code and no live document cites**, measured rather than judged.
  **Nothing was renumbered** — `src/` and `tests/` cite 130 distinct decision numbers in 295
  places, several as *"argued in #N rather than asserted"*.
- **The four duplicated test fixtures** (`tests/fixtures/{background_noise,fiber_cut,flapping_noise,olt_storm}.json`).

### Added

- **An eight-page manual** organised by what a reader is trying to do: `install`, `configure`,
  `operate`, `console`, `correlation`, `security`, `troubleshoot`, `architecture`. Written against a
  running appliance; every command in it was executed.
- **[`docs/findings.md`](docs/findings.md)** — every open finding, five bullets each, with a
  runnable reproduction and its measured output. Three are new: **F61**, **F62**, **F63**.
- **[`docs/record.md`](docs/record.md)** — where the deleted documentation went, the one rule for
  reading a `docs/gates/…` citation, and the new second home for the four pre-registration hashes
  and the simulated network's seed.
- **[`docs/plans/`](docs/plans/)** — specifications you cannot run, including briefs for v0.15.1
  (the package tree), v0.15.2 (the console, measured) and v0.15.3, stating measurements and open
  questions rather than designs.
- **A guard that every decision number cited in the tree resolves to an entry.** It found two
  dangling citations on its first run.
- **A guard that the loader's strip is exactly right**, with a control proving each half of it is
  load-bearing.

### Changed

- **A release now writes no gate document, no scope document, no build report and no security
  review** (#197). A finding is five bullets in `docs/findings.md`; a decision is six lines in
  `DECISIONS.md`; everything else is a commit message and a line here. Working notes are scratch
  files outside the repository. **This release practises the rule it institutes.**
- **Principle 8** was *"spec now, implement later — each version writes the next one's
  specification"* and is now **"the instrument precedes the change it measures"** (#200). The
  foresight was real and is kept; it never came from the documents, it came from the ordering.
- **The cartridge moves from v0.15.0 to v0.16.0** (#202). Nothing in its own argument moves.
- **`README.md`** 452 lines to 135; **`MIGRATION.md`** 1 410 to 123; this file 1 897 to 338.
- **`make replay`** replays `eval/corpus/fiber_cut.json`.

### The one number this release missed

The target was **under 5 000 lines of `docs/`** and the result is **5 206** — 206 over, a
91.65 % reduction rather than 92 %. Reported rather than closed by trimming something a reader
needs, because the arithmetic says where the remaining lines are and neither holder is free:

```
docs/analysis/   1 455   four hash-pinned pre-registrations, untouchable by directive
docs/adr/        1 513   156 entries at a measured mean of 6.1 content lines — the density
                         the format claims. 468 of those lines are one heading and two blank
                         separators per entry, and 130 numbers cited from code forbid renumbering
the rest         2 238   the eight-page manual, the roadmap, findings, record, and six plans
```

Two levers exist and both are the maintainer's, not this release's. Dropping the blank line above
and below every decision heading would save those 468 lines and put the tree at **4 738** — it
renders identically and makes the log materially harder to read, which is optimising the metric
against the thing the metric exists to protect. Trimming the manual would trade a stranger's first
hour for 200 lines. **The measurement stands as it is.**

### Fixed

- **Nothing.** No fix inside a move: no file under `src/` moves but the version string, and all
  five open findings are issued rather than repaired. **Intentional behaviour changes: zero.**

### Findings issued

- **F61 — F58's scope is stated backwards.** The `MIN_EDGE_N` guard is defeated by **6** ordinary
  alarms, not 52; and defeating it *in a storm* yields an entity affinity of exactly **0.0**,
  because `STORM_DAMPING` damps the pair mass while `observe_activation` leaves the marginals
  undamped. The branch documented as the hazard cannot move a grouping; the branch documented as
  safe produces 0.833.
- **F62 — the discrimination floor's decision half rests on one row.** Over the frozen 256-row
  background the champion links 255 probes and leaves one unlinked; of all 256 single-row deletions
  exactly **one** makes the incumbent fail the floor it is the reference for.
- **F63 — the admission speed check compares one order statistic against itself.** Over 250 paired
  runs of the *same* scorer class at the production budget the p99 ratio ranges **0.25 to 3.66** — a
  property of the machine, not the model, and nothing measures or pins the headroom.

## [0.14.0] - 2026-08-23 — "the model family"

**Three more scorer kinds this appliance trains and runs itself, and the first end-to-end walk of
the whole evidence chain.** Five kinds now exist — `additive`, `logistic`, `tree`, `forest`,
`gradient_boosting` — all in process, in pure Python, with **zero new dependencies** and **zero
migrations**: a kind is a `model_version` row and always was.

- **Exact attribution or the kind does not ship.** A tree predicts a leaf value, so contributions
  are exact marginal Shapley values over all 2³ = 8 coalitions; a model too large to tabulate is
  **refused rather than approximated**.
- **The admission band's lower bound became discrimination, not the clock** (#193): a scorer that
  links every probe or none is refused, which is a behavioural floor and the only form available
  to a model whose parameters cannot be read.
- **The chain walked end to end for the first time**, and ended in `INSUFFICIENT_EVIDENCE` — the
  outcome `PREREGISTRATION-0.14.0.md` registered in advance as a success.
- **Three findings.** F59 (the promotion gate measured one scorer and activated another) and F60
  (the console reported coded defaults as the active configuration) fixed; **F58 open**.
- The trap path stayed byte-identical, and is now pinned by a test rather than by a habit.

## [0.13.0] - 2026-08-15 — "the UI"

**The largest single change in the project's history, and the first whose deliverable is something a
human looks at.** `ui/app.js` — 52 738 bytes in one file — becomes an entry point plus 34 ES
modules. Sidebar navigation, per-role dashboards, the network graph, themes, the full admin surface.
Eight routes that had no screen got one.

Every screen was rendered and driven as every role while it was built, against the harness v0.12.0
built first. The 44 route/method pairs are order-byte-identical to the v0.7.1 baseline. **Still no
build step, no `package.json`, no npm** — and that is `tests/test_build_step.py`, not an intention.

## [0.12.0] - 2026-08-15 — "the instrument and the shape"

**Before rewriting 52 kilobytes that no test executes, build the thing that would notice. This
release changes no pixel.** That no test executed `app.js` was *demonstrated* rather than assumed:
with the file made unparseable by any JavaScript engine, the full suite still reported 1302 passed.

18 DOM tests now execute it, under `node:vm`, with no npm. The number was zero.

## [0.11.0] - 2026-08-14 — "champion/challenger"

**Promotion becomes possible, auditable and refusable — and on this corpus it refuses.** The slow
loop proposes a scorer swap with its evidence, an admin approves, and the swap is one more immutable
row. Against the real corpus the gate returns `INSUFFICIENT_EVIDENCE`, **the sealed holdout is not
read, and its query count remains 0.** `PREREGISTRATION-0.11.0.md` §6.1 predicted that before any of
this release's code existed. That is the expected result and a successful release.

## [0.10.1] - 2026-08-13 — "the corrections v0.10.0 earned"

A guard that was not merely untested but **wrong** (F50: `incidents.resolve` took the minimum over
the walk, not over the cycle), a conclusion about a statistic that ran backwards, and a reported
number that did not reproduce — all three fixed **without moving a line of the plan those numbers
were measured against**. No migration, no new route, one declared behaviour change.

## [0.10.0] - 2026-08-12 — "the honest judge"

**An evaluation whose verdict cannot be produced by the thing being evaluated, and a holdout built
and deliberately not spent.** Held-out evaluation split by time or by incident, **never at random**,
scored on over-merge and under-merge. It does not produce a better model; it produces the machinery
that could one day tell whether one is better. Verdict on this corpus: `INSUFFICIENT_EVIDENCE`,
holdout queries **0** — the pre-registered expected outcome.

## [0.9.2] - 2026-08-10 — "the evidence boundary"

A corrective release. `labels.py` recorded `excluded_count` as the raw length of a **client-supplied
list**, never intersected with the server's own bag, and three reports multiplied it. **A floor
computed from something the subject controls is not a floor.** A number describing the evidence is
now derived by the server; a number describing the client may be derived from the client; where they
meet is a named, stored, auditable act.

## [0.9.1] - 2026-08-08 — "the partial split"

The operator can say **which** members do not belong. A `split` verdict used to assert *"these are at
least two situations"* without saying which, so it supported no pairwise claim at all — the minority
class, the only source of negative evidence in the system, was also the least informative label it
knew how to collect. Existing labels are untouched.

## [0.9.0] - 2026-08-03 — "shadow mode"

**A challenger runs beside the champion and writes its opinion where nobody acts on it.** The
built-in scorer decides everything; nothing groups differently. The release's most valuable output
is not a model but two numbers — how well the champion already agrees with operators, and whether
there is enough signal to learn anything at all. **The second came back *no*,** which is the release
succeeding.

## [0.8.1] - 2026-08-02 — "the dataset has a governed lifecycle"

**F44 — the operational prune deleted human labels.** v0.8.0 designed a lifecycle for the rows it
created and did not check the one the repository already had: in a default deployment the release's
own deliverable evaporated after seven days, taking the least reconstructible asset in the system
with it. No schema change, no migration; `make eval` byte-identical.

## [0.8.0] - 2026-08-01 — "the scoreboard"

**Capture the operator feedback as a durable dataset, and measure its bias. Trains nothing.** Every
ML release from v0.9.0 on consumes what this one captures, and **capture is irreversible**: `A` and
`E` decay continuously, `alarm` is mutated on re-fire, situations merge and lose their membership. A
field not captured at the moment of decision is not captured late — it is captured never. Migration
`0008`: four tables, and capture on by default.

## [0.7.5] - 2026-07-31 — "the click means what the operator meant"

Makes the operator's click mean what the operator meant, and makes the two guards that protect
v0.8.0 actually guard. **Exactly four intentional behaviour changes**, three of them in the browser —
and the entry stated plainly that the suite did not prove those three, because there was no
JavaScript runtime in the repository yet. That admission is what v0.12.0 was built to end.

## [0.7.4] - 2026-07-31 — "no contradictions, no unowned debt"

Closes every loose end the v0.7.x series left, so v0.7.5 and v0.8.0 start from a repository that
agrees with itself. **F40 and F41**, both in the route-declaration gate, both found by adversarial
probing and **reproduced by execution** rather than by reading.

## [0.7.3] - 2026-07-30 — "the data and engine layers"

Internal structure only. `store.py` — 1 512 lines, 109 methods on one class — becomes eighteen
modules split along its own section comments, largest 213 lines, one level deep.
`from netcorenoc.store import Store, …` keeps working verbatim.

## [0.7.2] - 2026-07-30 — "the HTTP package"

Internal structure only. v0.7.1 closed six findings and four lived in `api.py` — one 1 752-line file
holding the CSRF gate, identity, the policy cache, capability and scope resolution, the audit
helper, the rate limiter, the transaction discipline **and** forty handlers. It becomes a package,
and the string-joined route/permission convention becomes **a declaration that fails before the
process can serve**.

## [0.7.1] - 2026-07-29 — "the write perimeter"

**A security patch.** Six confirmed defects (F34–F39) in which a v0.7.0 guarantee was enforced on
reads and not on writes — and one of the scope resolver's own inputs, the operator label, was
writable by the very role the scope constrains.

> **Authorization never reads data the constrained party can write, and a write is inside the
> perimeter or it is not inside it at all.**

## [0.7.0] - 2026-07-25 — "governance"

An admin can define what each role and principal may **do** and may **see** — stored, audited policy
read through the **existing** single decision points. No new authorization mechanism, no second
decision site, nothing on the ingest path. **With no stored policy, v0.7.0 is byte-identical to
v0.6.0**; migration `0006` seeds no rows, and that parity is a release gate rather than a claim.
Resolved permissions are `ceiling(role) ∩ granted(role) ∩ granted(principal)`.

## [0.6.0] - 2026-07-25 — "the scoring seam"

The correlation formula stops being a hard-coded expression and becomes the default implementation
of a **versioned, swappable, explainable interface**, with admin-tunable parameters, safe preview
and one-click rollback. **Grouping behaviour does not change** at the default parameters.

⚠ **Breaking:** the legacy `OPTICORR_*` environment aliases are removed and now refuse at startup
rather than being ignored — an ignored `OPTICORR_ALLOWLIST` would mean every trap source accepted
while the operator believed otherwise. See [`MIGRATION.md`](MIGRATION.md).

## [0.5.0] - 2026-07-24 — "legible, installable, contributable"

Structure only; the running correlator does not change. The PyPA `src/` layout, so tests run against
the **installed** package — the standing guard against the F12 class of bug.

## [0.4.0] - 2026-07-23 — "trustworthy by construction"

Security and reliability hardening under a new identity: renamed to **NetCoreNOC** (#34), import
package `netcorenoc`, env prefix `NETCORENOC_*`, session cookie `netcorenoc_session`. Legacy
`OPTICORR_*` honoured for one version with a warning. The cookie rename forces a one-time re-login.

## [0.3.0] - 2026-07-23

**Entity identity — learning *what* is alarmed, not merely who reported it.** A network element
starts as one entity and is subdivided only when the trap stream proves, statistically, which
varbind names the alarmed sub-object: `S = 0.35·R + 0.45·X + 0.20·D`, promoted only above
conservative floors. Cold start is byte-identical to v0.2.0 on every fixture.

⚠ **Breaking:** the shared `API_TOKEN` is removed. Issue a service token per client.

## [0.2.0] - 2026-07-20

Identity, role-based authorization and a **tamper-evident, hash-chained audit log**, plus
remediation of six findings from the independent v0.1.0 security review. `scrypt` at n=2¹⁷,
server-side sessions, per-username and per-IP exponential lockout with no user enumeration, a
bootstrap admin printed once, forced password change. **The ingestion path is unchanged and still
lossless**; every control lives on the HTTP side.

## [0.1.0] - 2026-07-19

First release: a zero-configuration SNMP trap correlator in one Python process, one SQLite file and
one web UI.

- SNMPv2c trap receiver on UDP 162 with a source allowlist, defensive parsing and raw quarantine —
  **nothing can crash or block ingestion**.
- Zero-config discovery: devices from source IPs, classes from trap OIDs, vendors from a bundled
  IANA table. No MIBs, no inventory, no topology file.
- Incremental learning of the class-affinity matrix `A` and the entity-affinity matrix `E` by
  evidence-discounted normalised PMI with exponential forgetting, an `n ≥ 5` trust threshold and
  10× damping during storms. **The learned graph is the living topology.**
- Correlation by the three-term link score over a 120 s window; situations as connected components;
  the three terms stored on every link, so a grouping can always be explained.
- Probable root from learned temporal precedence; raise/clear pairs learned from strict alternation.
- SQLite (WAL) with plain-SQL forward-only migrations; state survives restarts.

[0.15.0]: https://github.com/leonardoSaaads/NetCoreNOC/releases/tag/v0.15.0
[0.14.0]: https://github.com/leonardoSaaads/NetCoreNOC/releases/tag/v0.14.0
[0.13.0]: https://github.com/leonardoSaaads/NetCoreNOC/releases/tag/v0.13.0
[0.12.0]: https://github.com/leonardoSaaads/NetCoreNOC/releases/tag/v0.12.0
[0.11.0]: https://github.com/leonardoSaaads/NetCoreNOC/releases/tag/v0.11.0
[0.10.1]: https://github.com/leonardoSaaads/NetCoreNOC/releases/tag/v0.10.1
[0.10.0]: https://github.com/leonardoSaaads/NetCoreNOC/releases/tag/v0.10.0
[0.9.2]: https://github.com/leonardoSaaads/NetCoreNOC/releases/tag/v0.9.2
[0.9.1]: https://github.com/leonardoSaaads/NetCoreNOC/releases/tag/v0.9.1
[0.9.0]: https://github.com/leonardoSaaads/NetCoreNOC/releases/tag/v0.9.0
[0.8.1]: https://github.com/leonardoSaaads/NetCoreNOC/releases/tag/v0.8.1
[0.8.0]: https://github.com/leonardoSaaads/NetCoreNOC/releases/tag/v0.8.0
[0.7.5]: https://github.com/leonardoSaaads/NetCoreNOC/releases/tag/v0.7.5
[0.7.4]: https://github.com/leonardoSaaads/NetCoreNOC/releases/tag/v0.7.4
[0.7.3]: https://github.com/leonardoSaaads/NetCoreNOC/releases/tag/v0.7.3
[0.7.2]: https://github.com/leonardoSaaads/NetCoreNOC/releases/tag/v0.7.2
[0.7.1]: https://github.com/leonardoSaaads/NetCoreNOC/releases/tag/v0.7.1
[0.7.0]: https://github.com/leonardoSaaads/NetCoreNOC/releases/tag/v0.7.0
[0.6.0]: https://github.com/leonardoSaaads/NetCoreNOC/releases/tag/v0.6.0
[0.5.0]: https://github.com/leonardoSaaads/NetCoreNOC/releases/tag/v0.5.0
[0.4.0]: https://github.com/leonardoSaaads/NetCoreNOC/releases/tag/v0.4.0
[0.3.0]: https://github.com/leonardoSaaads/NetCoreNOC/releases/tag/v0.3.0
[0.2.0]: https://github.com/leonardoSaaads/NetCoreNOC/releases/tag/v0.2.0
[0.1.0]: https://github.com/leonardoSaaads/NetCoreNOC/releases/tag/v0.1.0
