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

Read only the rows between your version and the one you are installing. **Two of twenty-eight ask
you to do something; nine more ask you to read a paragraph first. The other seventeen are
start-the-new-binary.** (This sentence said *"six of nineteen"* above a table of twenty from v0.15.0
until v0.15.2 — F78. It counts rows, not sections; recount it when you add one. v0.15.3 did, and
v0.16.0 did not add its row at all — F94 — so v0.16.1 added both. v0.16.2 adds a
read-a-paragraph row: it applies no migration and still changes what your existing situations do.)

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
