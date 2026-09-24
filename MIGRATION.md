# Upgrading NetCoreNOC

**Every schema migration is forward-only, additive and automatic**, applied at startup and again by
`make migrate`. No release has ever required you to export and reimport. Your alarms, learned state,
sessions, tokens and audit chain survive every upgrade in this table.

Two rules that have held since v0.1.0 and are not going to change:

* **Nothing new is on by default.** A release that adds a capability adds it switched off; the
  release that changed how a *decision* is made is called out below and there is only one.
* **A removed setting is a startup error, never a silent no-op.** An ignored `OPTICORR_ALLOWLIST`
  would mean every trap source was accepted while you believed otherwise. The process refuses to
  start and names the replacement.

## What you have to do

Read only the rows between your version and the one you are installing. **Two of thirty-seven ask
you to do something; seventeen more ask you to read a paragraph first. The other eighteen are
start-the-new-binary.** (This sentence said *"six of nineteen"* above a table of twenty from v0.15.0
until v0.15.2 — F78. It counts rows, not sections; recount it when you add one. v0.15.3 did, and
v0.16.0 did not add its row at all — F94 — so v0.16.1 added both. v0.16.2 adds a
read-a-paragraph row: it applies no migration and still changes what your existing situations do.
v0.16.3 adds another: `0016` runs itself, and the names you already set come with it. v0.16.4 adds
a third: **no migration at all**, and the console you sign in to is rearranged. **v0.16.5 did not
add its row either — F94's shape, twice now — so v0.16.6 adds both**, which is why the count moves
by two: v0.16.5's is start-the-new-binary and v0.16.6's is read-a-paragraph, for v0.16.4's reason.
**v0.16.7 adds its own, in its own release** — read-a-paragraph, because the screen you open first
now leads with a number that may read `—`, and an operator who reads that as a fault will file one.)

| From → to | What you must do |
|---|---|
| v0.1.0 → v0.2.0 | Nothing. `OPTICORR_API_TOKEN` still works and warns once |
| v0.2.0 → v0.3.0 | **Unset `OPTICORR_API_TOKEN`** and issue a service token per client — see below |
| v0.3.0 → v0.4.0 | Nothing. The rename lands; `OPTICORR_*` still works and warns |
| v0.4.0 → v0.5.0 | Nothing — packaging and structure only |
| v0.5.0 → v0.6.0 | **Rename every `OPTICORR_*` variable to `NETCORENOC_*`** — see the table below |
| v0.6.0 → v0.7.0 | Nothing. Governance ships inert: no roles assigned, no scopes, nothing filtered |
| v0.7.0 → v0.7.1 | Nothing, and **upgrade promptly** — it closes a write-perimeter hole |
| v0.7.1 → v0.7.4 | Nothing — internal structure and two guard fixes |
| v0.7.4 → v0.7.5 | Nothing. Fixes the feedback path *before* v0.8.0 builds a dataset on it |
| v0.7.5 → v0.8.0 | Nothing, but **read "capture starts" below** — this one costs disk |
| v0.8.0 → v0.8.1 | Nothing. One CLI report's output format changed |
| v0.8.1 → v0.9.0 | Nothing. Models train in the shadow; **nothing groups differently** |
| v0.9.0 → v0.9.2 | Nothing. Existing labels are untouched by either migration |
| v0.9.2 → v0.10.0 | Nothing, and **read "the seal" below** — one action there is irreversible |
| v0.10.0 → v0.10.1 | Nothing |
| v0.10.1 → v0.11.0 | Nothing. Promotion ships requiring an admin; **nothing promotes itself** |
| v0.11.0 → v0.12.0 | Nothing. Contributors need Node ≥ 18 for `make dom` |
| v0.12.0 → v0.13.0 | Nothing. The console is new; **if you reverse-proxy it, see below** |
| v0.13.0 → v0.14.0 | Nothing. Four scorer kinds exist; the additive one is still champion |
| v0.14.0 → v0.15.0 | **Nothing at all.** Documentation only — `src/` is byte-identical |
| v0.15.0 → v0.15.1 | Nothing — packaging and repository structure only |
| v0.15.1 → v0.15.2 | Nothing, but **the console loses a panel and a bad setting now exits** — see below |
| v0.15.2 → v0.15.3 | Nothing, but **the appliance now refuses to lose its last admin, and may mint one on boot** — see below |
| v0.15.3 → v0.15.4 | Nothing — a packaging fix. **If you ran v0.15.3 in Docker, rebuild the image** (F85) |
| v0.15.4 → v0.15.5 | Nothing, but **the theme control stops being a three-state ring** — see below |
| v0.15.5 → v0.16.0 | Nothing, but **every situation status you have ever seen is renamed** — see below |
| v0.16.0 → v0.16.1 | Nothing. `0015` widens one index; **no situation regroups and no verdict changes** |
| v0.16.1 → v0.16.2 | Nothing to run — **no migration** — but situations that were being closed will stop being closed. Read below |
| v0.16.2 → v0.16.3 | Nothing to run. `0016` moves the names you already set and **they start appearing on Entities**. Read below |
| v0.16.3 → v0.16.4 | Nothing to run — **no migration** — but the console is rearranged and two `/api/stats` keys are new. Read below |
| v0.16.4 → v0.16.5 | Nothing to run — **no migration**. CPU, memory and storage appear in the health control, read from `/proc` and the cgroup; `POST /api/alarms/clear` is new |
| v0.16.5 → v0.16.6 | Nothing to run — **no migration** — but four screens are redrawn and one `/api/stats.resources` key is new. Read below |
| v0.16.6 → v0.16.7 | Nothing to run — **no migration**. The Overview leads with active alarms by severity, and on a fresh appliance that panel reads *not measured* rather than zero. Read below |
| v0.18.0 → v0.19.0 | Nothing to run. **Two migrations apply at boot** (`0017`, `0018`) and both are additive: two nullable columns on `challenger_run` for the learning curve, and two indexes on `dataset_pair`. Correlation groups differently on estates of many elements — deliberately, and narrower. Read below |
| v0.19.0 → v0.20.0 | Nothing to run. **No migration.** The console is rearranged again — the Overview, the situation card and the restructure controls — and one API field is gone from `/api/situations/{id}`. Read below |
| v0.20.0 → v0.21.0 | Nothing to run. **Three migrations apply at boot** (`0019`, `0020`, `0021`), all additive. But **read below**: your existing alarms start carrying a severity they did not carry before, some of your entities were never entities, and the container image needs one new OS package |
| v0.21.0 → v0.21.1 | Nothing to run. **No migration.** The Maintenance screen is rebuilt, the Overview gains a *Planned work* card, and `GET /api/maintenance-windows` returns a **smaller `total`** than it did — it now counts what the filters and your visibility scope actually permit. Read below if you read that field |

*(This table has no rows for v0.17.0 or v0.18.0: neither release wrote one, and inventing upgrade notes for a release somebody else built would be describing an upgrade nobody tested.)*

## The two that need an action, and the six that need reading

### v0.3.0 — the shared API token is gone

One token shared by every client cannot be attributed, revoked individually, or scoped. Issue a
**service token** per client from **Administer → Service tokens**: per-identity, revocable, and
shown once. Setting `NETCORENOC_API_TOKEN` is a startup error naming this page.

### v0.6.0 — the `OPTICORR_*` prefix is removed

Deprecated in v0.4.0, warned through v0.5.0, removed here (#34, #39, #45). The mapping is the
prefix and nothing else — `OPTICORR_DB` → `NETCORENOC_DB`, and so on for `TRAP_HOST`, `TRAP_PORT`,
`HTTP_HOST`, `HTTP_PORT`, `ALLOWLIST`, `RETENTION_DAYS`, `AUDIT_RETENTION_DAYS`, `TLS_CERT`,
`TLS_KEY` and `LOG_JSON`. `OPTICORR_API_TOKEN` has no replacement; see v0.3.0 above.

```sh
env | grep OPTICORR_    # must print nothing before you start the new binary
```

### v0.8.0 — capture starts, and it costs disk

Every evaluated pair is recorded at the moment of the decision, because none of it is recoverable
afterwards: `A` and `E` decay continuously and an alarm row is overwritten on re-fire. That is why
it is on by default. Budget roughly **62 pair rows per trap**, and check what you actually have:

```sh
python -m netcorenoc dataset stats      # rows, and the window you ACTUALLY have
python -m netcorenoc dataset retention  # the tiers as they resolve right now
```

The 2 000 000-row cap almost certainly governs before the 21-day horizon does — about **nine hours
at 1 trap/s**. [`docs/configure.md`](docs/configure.md) has the three tiers and what lowering one
deletes.

**Capture is admin-only on every route.** It runs engine-side, where visibility scoping does not
exist and must not, so a dataset row carries every network element in the estate.

### v0.10.0 — the sealed holdout, and the one thing you cannot undo

The judge evaluates against a holdout that is *sealed*: it counts every query made against it, and
that count is the evidence that no one tuned against it. **Breaking a seal is irreversible.** The
migration seeds nothing and seals nothing; you have to ask.

### v0.13.0 — if you reverse-proxy the console

The console is a static ES-module tree with **no build step** — no bundler, no `package.json`, no
npm, and that is a test rather than an intention (`tests/test_build_step.py`). A proxy that rewrites
paths, strips `Content-Type: text/javascript`, or serves `/app.js` from a cache keyed on the old
single-file console will break module loading. Serve the tree as-is.

### v0.15.0 — nothing, and here is the proof

Documentation only. Every file under `src/` is byte-identical to v0.14.0 by SHA-256 — 165 files,
zero differing — and `python eval/harness.py | sha256sum` is unchanged at `c2e8a0ce…`. No
migration, no route, no capability, no audit action, no console change.

What moved is `docs/`: 62 310 lines to about 5 200, organised by what a reader is trying to do.
Every deleted file is at commit `3ecf237` and [`docs/record.md`](docs/record.md) has the command.
If you have a bookmark into `docs/gates/`, `docs/scope/`, `docs/releases/` or `docs/security/`, that
page is the one to read.

### v0.15.5 — the theme control is a toggle, and it cannot go back to "system"

**Console only; no code, no schema, no API.** Three defects on screens an operator uses: the
sign-in card's password field rendered 18 px wide because the reveal button had taken the whole row
(F86), the password inputs were asking the browser to spell-check passwords (F88), and the theme
control had a dead click and a label frozen on `Theme: system.` (F87).

**The one thing that changes for you** is the third. The control cycled `dark → light → system`,
and "system" always renders as one of the other two, so one click in three did nothing visible. It
is now a straight toggle: one click, one change. **`system` is still the default** — a fresh
install, and any browser with no `ncn_theme` cookie, still follows the operating system, and the
control still draws its "auto" icon until the first click. What it can no longer do is *return* to
system once you have chosen. If you want that back, clear the `ncn_theme` cookie for the console's
origin. A control that offers three states needs a menu, and that is a design change rather than a
bug fix, so it is not in a patch release.

Nothing is stored, nothing migrates, and an existing `ncn_theme=dark` or `=light` cookie is
honoured exactly as before.

### v0.16.0 — every situation status you have ever seen is renamed

**This row was missing until v0.16.1 added it (F94), so read it even if you upgraded already.**

Migration `0014` widens `situation.status` from `open | closed | merged` to `new | open |
resolved`, and the console's tabs follow. Nothing regroups: the correlator's decisions, the
learned state, the labels and the audit chain are untouched, and no situation gains or loses a
member. What changes is the vocabulary an operator reads, and one rewrite of history:

* **`open` stays `open`**, and every situation the correlator creates from now on starts as
  **`new`** — *nobody has looked at this yet*. On an untriaged appliance that is most of them, so
  the Situations screen opens on the New tab.
* **`merged` becomes `resolved` with `resolution = merged`.** Exact: `merge_situations` is what
  wrote that status, so nothing is inferred.
* **`closed` becomes `resolved` with `resolution = unattributed`.** `closed` conflated an
  operator's close with the idle sweep and nothing recorded which, so the migration says *"this
  one cannot be attributed"* rather than guessing (DECISIONS #253). Historical rows are the only
  ones that carry it; every close from v0.16.0 onward records its own reason.

**A client that still asks `GET /api/situations?status=closed` gets a 422 naming the three values**
rather than an empty list, which is the honest answer to a filter that no longer exists. If you
script against that route, that is the one thing to change.

The release also adds five operator gestures — move, merge, split, manual clear, rename — each
behind its own capability, and **every one of them ships switched off**: no role holds them until
an admin grants them.

### v0.16.1 — one index widens, and nothing you can see moves

**No action.** Migration `0015` adds `feedback.bag_key` and replaces the two-column unique index on
`(situation_id, verdict)` with a three-column one that includes it. Existing rows are filled with a
sentinel that keeps the old bound exactly, so nothing you have already recorded changes meaning and
no situation regroups.

What it changes is what happens the *second* time an operator corrects the same situation. Before
this release the second correction recorded its event and no label, so a busy operator's repeated
work contributed one row rather than several (F89). It now records its own label when the
membership has actually changed — and a repeated post about an **unchanged** bag still records
once, which is the v0.7.1 bound that stops N identical posts driving N learning effects.

The same release repairs how the promotion judge reads those labels (F90). **No verdict this
appliance has ever returned changes**: the gate still refuses on the corpus floors, which no
number here moves.

### v0.16.2 — situations you were used to seeing closed will stay open

**Nothing to run: this release applies no migration.** What changes is what the appliance does on
its own, and it is the point of the release.

The idle sweep closed every live situation nobody had touched for an hour. It never asked whether
any of that situation's alarms were **still active** — and because a repeating trap increments an
existing alarm's `count` rather than raising a new one, a situation closed that way never came back
into a live view, no matter what the network did afterwards. **The symptom of the defect was the
absence of a symptom**, which is why an appliance that has been running for months may have been
quietly hiding live incidents from you.

After this release:

* the sweep resolves only what is **quiet** — an idle situation whose alarms have all cleared, or
  one with no members at all;
* an idle situation that still holds an active alarm **stays open**, carries a **stale** badge in
  the console, and is counted in a warning on `/api/stats` and the Overview screen;
* `resolution='idle'` narrows to the empty bag. A bag whose members all cleared still records
  `self_cleared`; nothing already recorded changes meaning.

**What you will notice on the first run.** Your open-situation count may rise, once, and stay
higher. That is not a regression and nothing is being created: those situations existed, they were
being resolved out from under you, and the number you had was the wrong one. Anything on that list
with a stale badge is an incident nobody has looked at while something is still broken — start
there.

**A situation that never resolves is never pruned**, so its membership rows are retained while an
alarm in it is on. That is the same trade: retention follows the incident, and an incident that is
still burning is not history.

Two new things an operator can do, neither of which is switched on by default:

* **`POST /api/situations/{sid}/promote`** — "I am working this", which moves a situation from
  **New** to **Open** and records *no* opinion about whether the grouping is right. It needs the
  new `situation.promote` capability (35 in total now); grant it to whoever triages.
* Confirming a grouping is unchanged and is still the only way to say the grouping is correct.

**One behaviour you may have scripted against**: `GET /api/situations` rows now carry a `stale`
boolean, and moving an alarm no longer promotes the situation it was moved **into** — only the one
it was moved out of.

### v0.15.4 — rebuild the image if you ran v0.15.3 in a container

**Packaging only; no code, no schema, no behaviour.** v0.15.3's wheel, when built the way the
Dockerfile builds one, was missing five console modules — `views/parts/{why,verdict,facts,model,
retention}.js` — so the container's first page load logged five
`RuntimeError: File at path … does not exist` and those screens were broken. Nothing was wrong
with the source or the git tree: one package-data glob was missing, and `MANIFEST.in` — which the
image build does not receive and every other build does — had been quietly making up the
difference (F85).

`docker compose up --build` is the whole upgrade.

**If you install any other way, you were not affected**, and that is measured rather than assumed:
a `pip install .` from a source checkout and a `pip install` of the sdist both carried all fifty UI
files under v0.15.3's globs, because the checkout has `MANIFEST.in` and the sdist carries the
`SOURCES.txt` it produced. The container build is the one path that has neither. Upgrading is still
worth doing — the guards that would have caught this are what v0.15.4 is — but nothing you are
running is broken.

### v0.15.3 — nothing to change, but two behaviours are new

**No configuration that started on v0.15.2 fails to start on v0.15.3.** There is no migration; the
schema is untouched. Two things behave differently and both are refusals or recoveries you want:

* **The last enabled admin cannot be removed.** A role change or a deletion that would leave the
  appliance with no admin account is refused with a 400 naming the way out. If you have automation
  that demotes accounts, it can now get a 400 it did not get before — which is the point: the
  request it was making would have locked the appliance (F79).
* **A boot with no enabled admin mints one and prints the password once**, exactly as first boot
  does, taking the name `admin` if it is free and `recovery-admin` if it is not. Before v0.15.3 a
  database in that state was unrecoverable without deleting it. If your appliance has an admin —
  and it does, or you could not read this — nothing prints and nothing changes.

The console changes visibly: seventeen drawn icons in place of Unicode glyphs, a tablet layout that
did not exist, and **the Density control is gone** (#235). If you had chosen `comfortable`, the
`ncn_density` cookie is now ignored and can be deleted; there is one density.

### v0.15.2 — nothing to change, but two things will look different

**No configuration that started on v0.15.1 fails to start on v0.15.2.** Every setting this release
learned to refuse by name was already a failure: an out-of-range port reached `bind()` and came back
as an `OverflowError` from inside asyncio *after* the log said it was listening; one TLS variable
without the other made the appliance report plain HTTP to admins while uvicorn was handed the
certificate and died on it; an unreadable database or a malformed allowlist **hung**. What changed
is the failure, not the set of things that fail:

```
NETCORENOC_HTTP_PORT=99999   before: hung, SIGKILL after 32.0s   now: exits 2 in 0.57s, naming it
```

If you supervise the process, that is the difference that reaches you. `deploy/netcorenoc.service`
sets `Restart=on-failure`, so a misconfigured appliance that used to sit in `active (running)` doing
nothing now exits and — at systemd's default start limit — stops in `failed`, which is the state you
want it in. *Measured as a process exit code and duration on this project's CI container; not
observed under systemd, which does not run as PID 1 there.*

**The console's right-hand detail panel is gone** — 320 px on every screen. No view ever wrote to
it (DECISIONS #219). Nothing you could previously read is now unreachable; the Overview gained the
queue depth, the five receiver counters and a trap rate with its window named.

One thing to check *before* you upgrade, if you ever saved an allowlist through **Administer →
Configuration**: an unparseable entry used to be accepted with `200 {"status":"saved"}` and then
stop the next boot (F75). It is now refused at save time with a `422`, and if one is already stored
the refusal at startup names the entry and the shape it wanted instead of hanging.

### v0.16.3 — the names you already set start appearing where you expected them

**Nothing to run.** Migration `0016` applies itself at startup, like every one before it, and it
touches no alarm, no situation and no learned state.

**Every device name you have ever set is carried over, keyed on the address rather than on an id.**
If you renamed a host on the Network Graph and then found the Entities screen still showing its IP,
that was a real defect and not your mistake: the name was written against one table and the screen
read another. It is one record now, and the name you set months ago appears on the Entities screen,
on the graph and on every situation row from the first boot after the upgrade — no re-entry, no
action.

**Three things you can now tell the appliance**, all from the row where the trap appears:

* what a piece of equipment is called;
* what a kind of trap means — and where you have not said, the row shows the vendor the appliance
  resolved from the OID rather than the bare OID it used to show;
* how serious a kind of trap is, from the five severities the console renders.

**Nothing you declare overwrites what the appliance learned.** Both are kept, the console marks
which one it is showing, and *Clear* puts the learned value back. None of it teaches the correlator
anything, so no grouping changes and no verdict changes.

**Two things worth knowing before you upgrade.**

* **If you drive the API yourself**, `POST /api/labels` no longer accepts `{"kind": "device"}` — it
  is `"ne"` — and answers **422** rather than 200. This is the only breaking request change in the
  release. `"class"` is unchanged, and `"severity"` is new.
* **A viewer sees less than they did, deliberately.** An address typed *inside* a name — `core-sw
  at 10.1.2.77` — is now coarsened to `10.1.2.0/24` for anyone below `editor`, exactly as the
  address field beside it always was. It was leaking; it is not any more.

## Downgrading

**A newer schema is not readable by an older binary.** Every migration is additive, so an older
release will refuse a database whose `user_version` is ahead of it rather than corrupt it. The
supported rollback is to restore the SQLite file you copied before upgrading, which is the whole
reason to copy it:

```sh
systemctl stop netcorenoc && cp netcorenoc.db netcorenoc.db.bak    # before every upgrade
```

`v0.15.0` is the one release you can downgrade from freely, because it changed no code.

## Visibility scoping is not tenant isolation

Worth stating on the page an operator reads before an upgrade, because it is the limit people
assume away: scoping narrows what a signed-in identity **sees**. Correlation learns across the whole
estate, so a scoped principal sees a filtered view of one shared engine, not a private one. It is
not a tenancy boundary and it is not sold as one. [`docs/security.md`](docs/security.md) states
exactly what it does and does not give you.

### v0.16.4 — the console is rearranged, and nothing in your database moves

**No migration.** `0016` is still the last one. Start the new binary; every row you have is read by
the same queries, and two of them stop being read at all.

**What you will notice on the first screen.**

* **The four counters left the top bar.** `devices` and `alarm classes` are on the Overview;
  `active alarms`, `new` and `open` are cards on the **Situations** screen, where pressing one
  selects the tab it counts. `p95 latency` is in the new health control, which is in the top bar on
  every screen rather than on the Overview alone.
* **The warning strip became a bell.** The same warnings, one per line, each linking to the setting
  that resolves it where one exists. An **ingest gap** is still a banner as well, because it is the
  one message that must not wait for a panel to be opened.
* **The sidebar collapses to icons**, remembered in a cookie (`ncn_nav`) exactly as the theme is.
* **Every absolute time now reads `2026-09-06 14:32:07 -03:00`** — your browser's zone, with the
  offset from UTC in the text. Nothing about what is stored changed: the database has always held
  epoch UTC. If you have scripts that scrape times out of the console's HTML, they will need to
  read the new shape; the API is unchanged.

**Two new keys on `GET /api/stats`**, both additive: `new_situations` and `working_situations`,
which split the `open_situations` you already had. Nothing was removed from that response.

**One field left three API responses**, and nothing rendered it: `vendor` on `/api/graph`'s nodes,
on `/api/entities`, and `device_vendor` on every alarm row of `/api/situations/{sid}`. It has been
`NULL` on every row of every database since v0.1.0 — 25 elements and 0 vendors after 2 252 alarms
on the reference corpus — because no writer has ever set it (F105). The **columns are still in the
schema**; only the reads are gone, so nothing is dropped and nothing needs backfilling.

**One warning says less than it did, deliberately.** The denied-trap warning named your allowlist
verbatim, in a response any **viewer** can read while every address they are shown elsewhere is
coarsened to a `/24`. It now says how many entries the allowlist holds; the value is on Settings,
where the admin who can change it reads it (F107).

**A situation you have already judged looks different.** Confirm and Split fold behind one *Adjust
the grouping* button that names what was recorded and by whom. Nothing is removed — the same
controls are one press away, in every status the server accepts them in.

### v0.16.7 — the Overview leads with severity, and on your appliance it may read `—`

**No migration, and nothing in your database moves.** `alarm.severity` and `alarm.severity_rank`
have existed since v0.16.2; this release counts them and draws the count.

**What you will see, and why it is not a fault.** The first thing on the Overview is now *active
alarms by severity*, in six rows: critical, major, minor, low, `indeterminate`, and **not placed**.
On most appliances the last row holds everything and every other row reads `—` rather than `0`.

That is correct and deliberate. `engine/correlate/severity.py` names a severity only when a
vocabulary match **and** an ordinality check against observed alarm lifetimes agree, and the second
check needs fifty closed alarms on the element before it will commit. Measured on the ten scenarios
this repository ships: **2 119 alarms, none with a severity, one closed alarm in total.** A `0`
beside that would be the appliance claiming it had checked; a `—` is the appliance saying it has
not been able to.

**What makes the panel fill.** Either the element's traps stop and start again often enough for the
learner to confirm a severity field, or an operator declares one: *Alarm classes → a class →
severity*, which has existed since v0.16.3 and which the panel resolves ahead of the learned value.
The panel names how many of its bands came from a declaration, so the two are never confused.

**One additive API key.** `GET /api/stats` and the `/api/events` stream both gain a nested
`severity` object. Nothing that existed changed name, type or meaning. A client that does not read
it is unaffected.

### v0.16.6 — four screens are redrawn, and nothing in your database moves

**No migration.** `0016` is still the head of the schema, which the startup log prints as
`schema version 16`. Nothing is written, nothing is backfilled, and the trap path is byte-unchanged.

**What you will notice is that the Overview looks different.** It answers five questions in order —
what is happening, where, which element is worst, is the appliance keeping up, what it has learned —
and eight charts replaced eleven counter tiles and six paragraphs. The receiver's five counters did
not leave; they are one line of numbers under the host charts.

**Three things the charts will tell you that the counters could not**, worth knowing before you
read them during an incident:

* **Every chart's axis is the span its data actually covers**, not the window it asked for. Ask the
  timeline for seven days on a busy appliance and the axis may say it is showing the most recent
  minute, because the read is bounded at a thousand alarms. That is the chart telling you the truth
  about its own page rather than a bug.
* **A metric your host will not give up renders `—` and the words *not measured*, never `0`.** A
  gap in a series **breaks** the line rather than being drawn through it. Both are deliberate: a
  zero reads as *idle* and a joined line asserts a measurement nobody took.
* **The host series are held in memory and start again when the appliance restarts.** Each chart
  says so, and each says how much of its two-hour window it currently covers.

**One additive API key**: `GET /api/stats` gains `resources.bucket_s` — how much wall-clock time one
point of the CPU, memory and storage series covers (300 s). It is absent exactly where the whole
`resources` block is already absent, which is an API running without the process runner. Nothing was
removed from that response.

**The timeline's controls are now in its address.** `#/timeline?ne=3&win=21600&depth=100&chart=column`
is a link you can send to a colleague, and it restores the screen you were looking at. If you have
bookmarks to `#/timeline`, they still work and open the unconfigured screen. The element, window and
depth are applied by the server **inside your own visibility scope**, so a link from someone who can
see more of the estate than you shows you only your own part of it.

**Judge & promotion now draws the record over time** — the verdict's three states, the four named
quantities separately, and the seal's query count. It also states, on the screen, the three things
this appliance does **not** measure: a loss curve, a residual distribution and per-fold results.
Those are absences by design rather than gaps in the screen, and
[`docs/plans/releases.md`](docs/plans/releases.md) records what each would need.


### v0.19.0 — correlation groups less on large estates, and it is meant to

**Nothing to run**, and both migrations apply themselves at boot. Two things will look different.

**Groupings on an estate of many elements get smaller.** Learned *class* affinity no longer links
two alarms on different network elements when the appliance has learned no relationship between
those elements at all. Before this, an estate where every device raises the same two trap classes
could merge unrelated incidents without limit — measured at seventy independent card failures in
**one** situation of 140 alarms. Afterwards the same traffic makes seventy situations.

If you were relying on those wide groupings, what you want back is the *learned* relationship: let
the appliance see the elements co-occur, or correct the groupings it makes and let the entity
affinity build. The gate opens the moment `E` is above zero for the pair. All ten corpus scenarios
are unchanged, so small estates and single incidents behave exactly as before.

**Situations you already have keep the name they were given.** `derived_name` is computed when a
grouping's membership changes and stored, so an existing situation of eight alarms goes on reading
`Storm -> 10.0.0.1` until something changes it. New and changed groupings get the new form
immediately. Nothing recomputes the old ones, deliberately: a migration that renamed every stored
situation would rewrite rows an operator may have been reading all week, to fix a word.

**The decision comes before the analysis on a situation.** *Confirm* and *Split* used to sit below
the per-term score breakdown, so judging a grouping of a thousand alarms meant scrolling past the
member table and the breakdown to reach the buttons. The breakdown is still there, directly under
them.

**Two panels left the foot of the Overview.** *Judge and promotion* and *What capture is holding*
each rendered "Not computed yet" beside a `Compute now` button until you pressed it. The first is
answered live by the model line at the top of the same screen; the second is the Corpus screen's
subject. The seal's query count and the decision history are unchanged on **Judge & promotion**.

**The Overview leads with a line about the models.** One sentence, one bar, and everything else
behind a click — who is deciding, how far your judgements are from training a model, and the loss
curve once a fit exists. An admin gets two controls there: register the fit this appliance made,
and ask the server to hand correlation over. Neither shortens the evidence path — the judge still
re-derives every floor and may refuse, which on a corpus below the floors is the expected answer.

### v0.20.0 — the console is rearranged, and one API field is gone

**No migration.** Your database, alarms, learned state, sessions, tokens and audit chain are
untouched. What changed is the screen, and one field on one read route.

**`/api/situations/{id}` no longer sends `terms` on each link.** The three columns it was built
from — `term_t`, `term_a`, `term_e` — are unchanged and are still on every link, and the console
builds the named list from them, which it has always been able to do. This matters only if you
wrote your own client against `link.terms`: map `temporal → term_t`, `class_affinity → term_a`,
`entity_affinity → term_e`. The reason is measured — 993 KiB of a 1 843.9 KiB response on a
1 051-member situation was the same three floats written a second time, held in the browser for as
long as the card was open (F145).

**The Overview has a time range**, and it is remembered in a cookie named `ncn_range`, beside
`ncn_theme` and `ncn_nav`. It carries one of eight durations and nothing else; a value outside
that set is discarded and the default (2 h) applies.

**The situation card puts every decision in one row above the member table**, and the three
`Declare …` buttons are gone from the actions cell — the device, the class and the severity are
now edited by clicking the value itself, which carries a dotted underline to say so. Nothing was
removed: every gesture that existed still exists, in the place the thing it changes is shown.

**`Split (wrong grouping)` now reads `Grouping is wrong`.** Same route, same payload, same
evidence. If you have a runbook that names the button, it is the one beside `Confirm grouping`.

**Restructure asks for a selection first.** Tick the members, then choose `Move N elsewhere` or
`Merge another situation in`, and pick the destination from the list of situations that exist.
The two typed situation-id fields are gone; `A new situation` at the top of the move list is what
the old `Split the marked members out` did, and it posts to the same `/split` route.

### v0.21.0 — planned work, a severity your alarms already carried, and one new OS package

**Three migrations, all additive, all automatic.** Nothing is exported and nothing is reimported.
But three of the changes below are visible in data you already have, so read them before you
upgrade a production appliance rather than after.

**One thing to do if you build your own image: install `tzdata`.** Time zones are mandatory on a
maintenance window and `zoneinfo` reads the **operating system's** IANA database. The shipped
`Dockerfile` gains one `apt-get install -y --no-install-recommends tzdata` line in its final stage;
a Debian or Ubuntu host generally has the package already, an Alpine one generally does not, and a
`FROM scratch` image never will. **The appliance tells you** rather than failing quietly: a startup
self-check resolves every curated zone against the `tzdata` that image actually has and reports the
ones that do not through the same operator-warning channel that already carries eight others. No
Python dependency is added — `pyproject.toml` is unchanged.

**Your existing alarms start carrying a severity (`0019`).** Until this release the ingest path
could only place a severity it had *learned*, so `alarm.severity` was NULL on every row whose NE
had not yet been confirmed — including rows whose trap carried a standardised X.733 severity word
in the RFC 3877 column all along. In the reference lab, 29 of 30 alarms carried the word and **none
of the 30 rows had a severity placed**. From v0.21.0 the standard column is read at ingest and
wins over the learned path.

* **History is not rewritten.** `0019` adds `alarm.severity_source` and backfills it to `learned`
  for every row that already had a severity — true by construction, because the learned path was
  the only writer that had ever existed. It does **not** go back and place severities on old rows.
* **New alarms will read `critical`, `major`, `minor`, `warning` where they used to read `—`.**
  If you have an alerting rule keyed on "unplaced", it will fire less. That is the fix, not a
  regression.
* **The Overview's severity census does not change**, because v0.17.1 had already taught the
  *census* to read the standard column. What changes is the **row**, which is what a maintenance
  window's severity rule reads.

**Some of your "entities" were never entities (`0019`).** The severity varbind is usually the
most-observed varbind on an NE, so it crossed the entity-promotion floor first and was promoted to
the entity role uncontested. Where that happened, the dedup instance became a severity word: one
alarm row reading `inst='major'` with a count of eight, **eight separate faults collapsed into
one**, and inventory entries literally named `major`, `cleared` and `critical`.

* `0019` **withdraws the entity role** from any varbind whose promoted keys are *all* X.733
  vocabulary tokens. It touches the role only; no alarm, no situation and no history is edited.
* **After the upgrade, those alarms separate again** as new traps arrive — you will see the count
  on affected rows fall and the number of distinct alarms rise. In the reference lab, 30 alarms
  became 26, which is the true fingerprint count.
* **Check for the inventory entries** on the Entities screen and delete any named after a severity
  word. They are inert once the role is withdrawn; the migration leaves them because deleting rows
  an operator may have annotated is not an upgrade's job.

**Maintenance windows exist, and nothing is in one (`0020`, `0021`).** The tables are created
empty. No trap is suppressed until somebody declares a window, and a window over six hours
suppresses nothing until an editor or an admin confirms it. Your ingest path gains **one dictionary
lookup per trap**, measured at **+82 ns** on an appliance with no windows.

**Every network element gains an organization, and there is exactly one (`0020`).** The migration
seeds a single `Default organization` and assigns every existing element to it. Rename it on the
Admin screen; add more when you have more, and move elements between them with
`POST /api/entities/{ne_id}/organization` (admin).

**Devices and situations under planned work carry a marker**, on every screen and for **every
role** — a badge saying a window is in force and when it ends, never its name or its owner. A
member alarm the end-of-window sweep surfaced carries a second, different badge: *"raised during
maintenance, still active"*, which means the appliance knows the fault happened and knows nothing
else about it, because the trap itself was never collected.

> **This is not tenant isolation, and it does not become tenant isolation by being used.**
> Correlation still learns across every network element and a situation may still form across an
> organization boundary. The column answers *"whose equipment is this?"*, nothing else. The same
> warning `docs/security.md` carries about visibility scoping applies here word for word.

**New routes and capabilities.** `/api/maintenance-windows` (list, read, create, update, preview,
confirm, cancel, end, extend), `/api/organizations`, `POST /api/entities/{ne_id}/organization` and
`/api/timezones`. The capabilities are
`mw.read`, `organizations.read` and `timezones.read` at **viewer**; `mw.write` and `mw.confirm` at
**editor**; `organizations.write` at **admin**. Nothing is granted that was not granted before —
`resolve_capabilities` is still `ceiling ∩ policy`, so a deployment that has narrowed a role keeps
its narrowing. If you want the *declare* and *approve* powers held by different people, withhold
`mw.confirm` from the role that holds `mw.write`; they are two capabilities for exactly that.

**Two API responses gain a key.** `GET /api/situations` and `GET /api/entities` now carry
`maintenance`, which is `null` when nothing is in force and otherwise a window id, a status and
when it ends — never a name. A client that ignores unknown keys is unaffected; one that asserts an
exact key set will see it.

**No SNMP polling.** It was planned for this release and it is not in it — see `HANDOFF.md` §1.
Nothing polls your elements, no credential is stored and no capability promises otherwise.

### v0.21.1 — the maintenance screen is rebuilt, and one API number gets smaller

**Nothing to run. No migration.** Start the new binary.

**`GET /api/maintenance-windows` returns a smaller `total`, and the old one was wrong.** It counted
every window in the table, whatever you filtered by and whatever your visibility scope permitted —
so `?ne_id=7` on a device with one window reported `"total": 40`, and a scoped principal's total
was a count of the windows the scope had just withheld (F151). It now counts exactly what the same
call's `windows` array contains. **If you built a client that derived a page count from it, it will
now agree with the page.** No other field changed.

**The idempotency replay may answer redacted.** `POST /api/maintenance-windows` with a key that
already exists still returns `{"created": false, ...}` and the same window id. If the window covers
elements your visibility scope does not reach, the body is now the **public half** — status,
timing, target count, `"redacted": true` — instead of the full record (F152). On an appliance with
no scope policy, which is the default, nothing changes.

**Your audit log gains one action string.** `maintenance.window.read`, written only when a scope
denies somebody a window's details. Denials that would previously have been filed as
`maintenance.window.update` are now filed as the action attempted — `read`, `confirm`, `cancel`,
`end` or `extend` (F153). A dashboard counting `maintenance.window.update / denied` will see that
number fall and the others appear.

**The console.** The Maintenance screen is restyled from nothing — v0.21.0 shipped it with no CSS
rules at all (F150) — the four-card form now submits (F149), and the Overview gains a **Planned
work** card between the alarm counts and the estate. The card needs `mw.read`; a role without it
does not see it, and a deployment where the route fails renders one line rather than taking the
dashboard with it.
