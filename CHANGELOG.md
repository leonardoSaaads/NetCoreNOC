# Changelog

Notable changes per release, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html) — pre-1.0, so a
minor bump may break.

**Every release before v0.15.0 also has a long-form entry at commit `3ecf237`**, where this file was
1 897 lines. What was worth keeping is here; the rest was a build report per release, and
[`docs/record.md`](docs/record.md) has the command to read it. `#N` is a decision in
[`docs/adr/DECISIONS.md`](docs/adr/DECISIONS.md); `FN` is a finding.

What to do to upgrade is in [`MIGRATION.md`](MIGRATION.md): of twenty-five rows, two ask for an
action, seven ask you to read a paragraph, and sixteen are start-the-new-binary.

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
