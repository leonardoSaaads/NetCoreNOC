# NetCoreNOC v0.22.0 — "the console, repaired"

**The tree I was given reported `0.21.1`, not the `0.21.0` the brief names:**

```
$ python -c "import netcorenoc; print(netcorenoc.__version__)"   # before
0.21.1
```

So this hands over **v0.22.0** from v0.21.1 (`9f00a7e`). My reading of the tree wins, and I say so
on the first line as the brief asks.

## The headline

**7 761 words of prose removed: 9 582 → 1 821**, counted in Chromium over the seven screens in
scope × three roles × three widths (63 views), same metric before and after (§3).

**Defects fixed that no test could have caught** (each found by looking at a rendered screen):

| | what it was | how it was found |
|---|---|---|
| F156 | another screen's stylesheet redefined `.meter`; at 390 px the health bars had **zero width** beside correct percentages | measured fill vs. figure in Chromium |
| F157 | during a fibre cut the Timeline said *"Nothing was raised or cleared"* — repeating traps move `last_seen`, never `first_seen` | cut the lab and looked |
| — | bar values read **"1 alarms"** | read the Overview |
| — | at 390 px the catalogue's severity source was clipped at the screen edge and the alarm count was off-screen | 390 px screenshot |
| — | at 390 px the Timeline's burst rows broke vendor names mid-word and clipped the repeat count | 390 px screenshot |
| — | at 390 px a section's info icon fell onto its own line under the title | 390 px screenshot |
| — | two duplicated top-level CSS rules this release had itself introduced (`.badge-quiet`, a reused `.mw-targets`) | the new stylesheet guard, written after F156 |

---

## 1. The twenty items

| # | item | verdict |
|---|---|---|
| 1 | notifications never go away | **refined (push-back, §2)** — per-user audited snooze; a security warning's snooze always expires and an admin sees who snoozed it (#387) |
| 2 | System health panel renders wrong | **fixed** — CSS collision (F156); fill within 0.4 pts of the figure at 390/820/1440 |
| 3 | severity glyphs are placeholders | **fixed** — one chip: shape + colour + word on X.733; `low` → `warning` (#386) |
| 4 | "What is happening" severity timeline broken | **fixed** — raises per severity band counted in SQL over the chosen range; `unplaced` is its own line (#386, #381) |
| 5 | "Is the appliance keeping up" — three faults | **fixed** — the range reaches SQL over persisted readings (F154, #380); the four charts align; readings survive a restart |
| 6 | network graph on the Overview | **done** — compact graph of the busiest elements, same deterministic layout, links to Graph |
| 7 | scorer panel off the operator's view | **done** — moved (not deleted) to Judge & promotion, open there |
| 8 | "Raised during maintenance" marker | **refined (push-back, §2)** — kept, scoped to faults still active and not re-reported, acknowledgeable (#388) |
| 9 | situation state machine wrong | **fixed** — rename no longer promotes; operator split creates `open`; the lifecycle is a table (#382) |
| 10 | too much prose on the graph | **fixed** — edge explanation behind the info icon; the accessibility statement stopped being true (nodes are buttons) and its content is the drawing's accessible name (#383) |
| 11 | map unreadable and inert | **fixed** — labels chosen per width, nodes selectable, element panel with severity, situations, recent traps and links; **the regional heat map is deferred** to its own release as a schematic site map with no tile provider (#390) |
| 12 | "same estate, ordered by load" glued on | **fixed** — one "Elements by load" table |
| 13 | Timeline prose | **fixed** — both paragraphs cut; the colour-blind/screen-reader point is in the table markup (glyph + hidden word) |
| 14 | time axis wrong | **fixed** — measured first (1 000 marks spanned 124 s); window-bounded reads, axis proportional to time (F155, #381) |
| 15 | a hundred raw rows | **fixed** — bursts folded (*×14 over 2 min*), 25 per page, class name first, filters in the query |
| 16 | name and prose | **fixed** — "Trap catalogue" everywhere including the sidebar; the one kept fact is behind the info icon |
| 17 | file import | **done** — CSV/text, no MIB compiler, row-level report, all-or-nothing, bounded, audited, `imported` provenance, not evidence (#385) |
| 18 | OID tree | **done** — branch browser by arc from the IANA vendor list, rules on a subtree with most-specific-wins, winning rule shown, bounded and searchable (#384, #385) |
| 19 | scheduled MW is a dead end | **done** — detail view; edit/confirm/cancel while scheduled, end/extend/shorten while running; a running window's start, targets and rules are frozen (#389) |
| 20 | mobile | **checked throughout** — every screen photographed at 390 px for all three roles; no horizontal page scroll at any width; four 390 px defects fixed (above) |

## 2. The two push-backs, with what was measured

**Item 1 — "make the notifications go away".** Measured: the warnings are recomputed from the
appliance's state on every poll, so a delete comes back on the next poll. Of the ten
warnings the appliance can emit, two are security posture (traffic accepted from anywhere; the
console in clear text), and a dismissible *"all sources are accepted"* is the warning least safe to
lose. **Shipped instead:** a per-user snooze (24 h, 7 d, or until the text changes), audited; a
security warning cannot be snoozed "until it changes" (422) and its snooze always expires; an admin
always sees every live snooze of a security warning and by whom; a snoozed warning stays in the bell
as a muted count with *Restore*. Snoozing a warning nobody is shown is a 404, so the route is not an
oracle.

**Item 8 — "remove the raised-during-maintenance marker".** Measured: surfacing was correctly
scoped (only alarms still active when the window closed are surfaced), but the marker **never
expired** — it outlived a recurrence, a clear, and the operator's attention. A fault that outlived a
maintenance window must surface, so the marker stays. **Shipped instead:** it is drawn while the
alarm is active, unacknowledged and not re-reported since surfacing; an editor can acknowledge it
(who and when are recorded, the alarm is untouched, nothing reaches a dataset table); a re-surfacing
resets it.

## 3. Prose, per screen

Words on lines of six or more words in the work area, tables, selects, SVG, the page title and
closed info tips excluded. Before: the v0.21.1 tree on `before.db`; after: this tree on the lab.

| screen | admin, 1440 px — before | after |
|---|---:|---:|
| Overview | 196 | 69 |
| Situations | 10 | 10 |
| Network graph | 241 | 0 |
| Timeline | 176 | 24 |
| Trap catalogue (was Alarm classes) | 37 | 6 |
| Maintenance | 61 | 13 |
| Judge & promotion | 368 | 82 |
| **all 63 views (7 screens × 3 roles × 3 widths)** | **9 582** | **1 821** |

Viewer and editor differ only on the Overview (before: 156 and 167; after: 69). The remaining
Overview prose is data sentences (*"1 window in force over 2 hosts"*, the provenance line).
Judge & promotion is not one of the twenty items; it received the correlation panel (item 7), so
its section hints moved behind the info icon too, and every `SectionHeading` hint in the console now
renders that way.

## 4. Decisions recorded (docs/adr/DECISIONS.md)

#380 host readings persisted, range reaches SQL · #381 Timeline bounded by its window, re-reported
alarms counted · #382 lifecycle as a table, only attention promotes · #383 deterministic
hand-written graph, d3 removed · #384 OID matching on arc boundaries · #385 the Trap catalogue,
precedence, import · #386 severity as shape + colour + word · #387 warning snooze · #388 the outlived
marker scoped and acknowledgeable · #389 what a running window may change · #390 the regional map is
a schematic site map, its own release.

Findings: F154 (range), F155 (axis), F156 (stylesheet collision), F157 (silent Timeline).

## 5. What was removed, one sentence each

- **d3** (279 706 bytes), `app/vendor.js`, `vendor/d3.LICENSE`, its NOTICE entry and checksum — both drawings that used it are hand-written now.
- **`views/parts/estate.js`** and **`compare.js`'s `Map`** — the Overview's load grid, replaced by the compact graph (item 6).
- **The browser's queue ring in `store.js`** — the queue chart reads persisted readings (F154).
- **The Timeline's chart-type, split and depth controls** — a row depth is what truncated the window; there is one drawing now.
- **Correlation health on Situations** — moved to Judge & promotion (item 7), not deleted.
- **The graph's accessibility paragraph** — the drawing became keyboard-operable, so the statement became false.
- **The graph's second "by load" section** — one table (item 12).
- **Every inline section hint** — each is behind the info icon, unchanged in content.
- **Four duplicated top-level CSS rules** (`topbar`, `chart-urgent`, `cell-edit`, `mw-row`) — merged, one owner each; a guard now refuses the pattern.

## 6. What the live pass did not cover

- **Screens outside the seven**: Entities, Corpus, Settings, Users, Tokens, Audit, Quarantine were not photographed.
- **A real phone**: 390 px is Chromium's emulated viewport, not a touch device; Safari and Firefox were not driven.
- **Assistive technology**: no screen reader was run. Keyboard reachability rests on markup (buttons, `aria-describedby`) and DOM tests, not a manual Tab walk of every screen.
- **Dark theme**: photographed in the default theme only.
- **The import's file chooser in a browser**: the import is exercised through its route and the DOM harness, not by picking a file in Chromium.
- **Clears in the lab**: the testbed's repair does not emit a clear the appliance matches, so no clear mark was seen live (clears are covered by tests).
- **The "before" side** ran the v0.21.1 appliance on the recorded `before.db` without live traffic.

## 7. Verification

| gate | result |
|---|---|
| full suite | **2 327 passed, 0 failed** (14 min 23 s); v0.21.1 was 2 220 |
| `mypy --strict` | no issues, 299 files |
| `ruff check`, `ruff format --check` | clean |
| `vulture` | clean |
| `make dom` (executed) | 86 passed, none skipped (85 at v0.21.1, plus the health-bar width test) |
| `make security` | bandit clean; pip-audit: no known vulnerabilities (after upgrading the venv's own `pip` 24.0 → 26.2.1, which is the installer, not a dependency) |
| coverage | **95 %** of `netcorenoc` (11 089 statements, 390 missed; 2 590 branches, 250 partial) |
| `make eval` | no gated regressions; every metric unchanged; **stdout byte-identical** to v0.21.1 (`c75b42aa…` both) |
| behaviour-identity record | re-recorded; every moved line attributed: `/healthz` (version), `/api/me` and `/api/rbac` (four capabilities), `/api/stats` (`imported` provenance), `/api/situations/{sid}` (`outlived_window_id`), the rewritten UI files, and the sixteen new routes |
| wheel and sdist | built; each installed into a clean Python 3.12 venv; both import as 0.22.0 and carry `0022`-`0024`; `tools/release_check.py` agrees on 0.22.0 |
| boot through all migrations | the installed wheel booted on an empty database: schema 24, `/healthz` 0.22.0, the console and new modules 200, `/vendor/d3.v7.min.js` 404 |
| lab up | `testbed/run_local.py` on :18080, driven through cut and repair during the live pass |
| injections | **23 of 23 red, each with a passing control and a verified revert** (§7.1) |

### 7.1 Injections

The brief's ten (B), and thirteen more for guards this release added (X). For each: the target test
passes on the clean tree, the one-edit injection turns it red, the revert is proven by `git diff`,
and the test passes again. **Two came back green the first time, and both times the test was at
fault, not the injection**: `test_every_creation_edge` compared the created status with the table
itself, so changing the table moved both sides (X2); and the scoped-collection guard compared two
empty `/api/activity/*` answers that differed only by their wall-clock `from`/`to` (X5). Both tests
were fixed and both injections now go red.

| | injection | red test |
|---|---|---|
| B1 | subtree rule matches on a string prefix (`…1.2` catching `…1.12`) | `test_catalogue::test_a_branch_rule_stops_at_an_arc_boundary` |
| B2 | a rename advances `new -> open` | `test_transitions::test_attention_is_derived_from_the_table_and_rename_is_not_in_it` |
| B3 | the range never reaches the query | `test_host_series::test_the_route_answers_the_window_it_was_asked_for_on_a_bucket_boundary` |
| B4 | the axis follows row index, not time | `test_activity::test_the_axis_is_proportional_to_time_not_to_rows` |
| B5 | severity by colour alone (shape removed from the chip) | `test_severity::test_every_severity_band_carries_a_glyph_and_text_not_only_colour` |
| B6 | a snoozed security warning never returns / is visible nowhere (two injections) | `test_attention::test_a_snoozed_security_warning_expires_and_the_admin_sees_who_snoozed_it` |
| B7 | an outlived alarm loses its marker | `test_attention::test_the_outlived_marker_is_drawn_until_acknowledged_and_the_alarm_is_untouched` |
| B8 | an imported row reaches the situations the promotion path reads | `test_catalogue::test_an_imported_rule_never_reaches_evidence` |
| B9 | an import past its size cap / its row cap (two) | `test_catalogue_import::test_an_oversized_body_is_refused_before_it_is_read`, `…::test_the_size_and_row_caps_refuse_rather_than_truncate` |
| B10 | a health bar at half its printed width / a second stylesheet rule for the fill (two) | `test_ui_invariants::test_every_health_bar_is_as_wide_as_the_percentage_printed_beside_it`, `test_stylesheet::test_a_bare_class_is_the_whole_selector_of_at_most_one_top_level_rule` |
| X1-X10 | `under_subtree` on a prefix; split creates `new`; bursts unfolded; unplaced folded into `warning`; an activity read unscoped; a security snooze "until it changes"; the marker never expiring; a bad file partly imported; re-reports uncounted; the element panel unscoped | one named test each |

**Coverage does not measure** the console's JavaScript (the DOM harness executes it but reports no
line coverage), layout (F156 is exactly what it cannot see), or behaviour on a live network (F157).

## 8. Delivery notes

- **Tags**: the repository carries one tag, `v0.12.0`, and the ZIP carries it. No `v0.22.0` tag is
  created: this release is a pull-request branch, and a tag belongs on the merged commit.
- **Schema**: 21 → 24 (`0022`-`0024`), additive; see MIGRATION.md.
- **Capabilities**: `notice.snooze` (viewer), `alarm.acknowledge`, `catalogue.write`,
  `catalogue.import` (editor).
