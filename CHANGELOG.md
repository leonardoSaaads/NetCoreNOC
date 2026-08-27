# Changelog

Notable changes per release, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html) — pre-1.0, so a
minor bump may break.

**Every release before v0.15.0 also has a long-form entry at commit `3ecf237`**, where this file was
1 897 lines. What was worth keeping is here; the rest was a build report per release, and
[`docs/record.md`](docs/record.md) has the command to read it. `#N` is a decision in
[`docs/adr/DECISIONS.md`](docs/adr/DECISIONS.md); `FN` is a finding.

What to do to upgrade is in [`MIGRATION.md`](MIGRATION.md), and for nineteen of twenty releases the
answer is nothing.

## [0.15.0] - 2026-08-27 — "the repository"

**`docs/` stops being a warehouse. `src/` is byte-identical.**

```
docs/          ->  62 310 lines across 253 files  ->  5 206 across 24   (-91.65 %)
src/           ->  165 files, ZERO differing by SHA-256 against v0.14.0
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
- **`README.md`** 452 lines to 135; **`MIGRATION.md`** 1 410 to 123; this file 1 897 to 332.
- **`make replay`** replays `eval/corpus/fiber_cut.json`.

### The one number this release missed

The target was **under 5 000 lines of `docs/`** and the result is **5206** — 206 over, a
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

- **Nothing.** No fix inside a move: `src/` is byte-identical and the five open findings are issued,
  not repaired. **Intentional behaviour changes: zero.**

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
