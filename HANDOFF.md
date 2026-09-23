# NetCoreNOC v0.21.0 — "planned work, and the severity that was there all along"

**Confirmed by execution.** The brief expected `0.18.0`. The tree I was given reported
`0.20.0`:

```
$ python -c "import netcorenoc; print(netcorenoc.__version__)"
0.20.0
```

So the release this document hands over is **v0.21.0**, not v0.19.0, and every version number in
the brief shifts by two: D3's slip is to **v0.21.1**. My reading of the tree wins, and I am saying
so on the first line, as the brief asks.

---

## 1. What is not in this release, first — D3 slips to v0.21.1

**Basic SNMP polling (D3) is not in v0.21.0.** The maintainer marked it important and it does not
slip silently, so it is on page one.

**Why.** II.6 does not describe a feature; it describes a security surface with a minimum:
credentials that are never in the database in the clear, SNMPv3 where the element offers it, a
bounded target set, a rate ceiling a request cannot raise, and every read audited. That is a
credential-handling subsystem — key derivation, at-rest encryption, a rotation story, a failure
mode when a credential is wrong that does not lock out an element — and Part VII's scope order puts
it seventh, behind the analysis that is never cut. Shipping it below that bar would put SNMPv3
credentials into this appliance on a rushed design, which is the one kind of mistake this codebase
cannot walk back after a customer has upgraded.

**What ships instead is not a gap.** The reason D3 was wanted at all is II.2's: a fault that starts
inside a window and outlives it. The **state ledger** closes that, and closes it *better* for the
appliance that has no credentials — it records what the appliance itself saw, needs nothing from
the element, and works on an estate whose owner never gave us a community string. A poll was
always corroboration ("what does the element say *now*?"), not detection. See §4.3.

**The written plan for v0.21.1.**

| | |
|---|---|
| **Scope** | Read-only. `sysUpTime`, `ifOperStatus` for a named interface list, and one vendor OID set per NE kind. No `SET`, ever, and no MIB walk of an arbitrary subtree. |
| **Credentials** | A new `snmp_credential` table storing **only** a ciphertext and a key id. The key comes from `NETCORENOC_SECRET_KEY` through the derivation `crosscutting/secrets.py` already uses for session material; a missing key is a startup error naming the variable, never a silent fallback to plaintext. SNMPv3 authPriv is the default and the only shape the console offers; v2c is reachable only by an explicit per-target opt-in that the UI labels as unauthenticated. |
| **Blast radius** | Targets come from `maintenance_window_target` and from an explicit poll list; both are bounded sets of NEs the operator already named. There is no "poll the estate" verb. |
| **Rate** | A module-level ceiling (targets/second and concurrent sockets), not a request parameter. A request may ask for *less*. `tests/test_security_poll.py` asserts no route can raise it. |
| **Audit** | One `poll.read` row per target per sweep, with the actor, the OIDs requested and the outcome. A poll is a read of somebody else's equipment and the record is the point. |
| **Where it attaches** | `engine/mw/ledger.py` already says what the poll will do when it exists: a reading becomes a **second opinion** on an alarm the ledger surfaced. Nothing in the ledger changes to receive it. |
| **What must be true to start** | The three migrations of this release are in production and `0019`'s entity withdrawal (§3.3) has been observed on a real estate. Polling an estate whose entities are mislabelled would attribute readings to the wrong thing. |

**The placeholders are removed, not left behind.** `poll.read` and `poll.write` and the
`poll.target.update` audit action were written before the slip was decided and are deleted from
this release: a capability with no route behind it is a promise of a feature nobody built, which
`ui/app/registry.js` already refuses for screens on exactly that ground. `engine/mw/ledger.py`'s
sentence claiming a poll reading attaches to a surfaced alarm is corrected to the future tense it
actually holds. ADR **#373**.

---

## 2. The acceptance test — the maintainer's own example

> *"A MW on hosts A and B, 10:00 to 12:00 Brasília. On host A, collect only critical alarms. On
> host B, collect traps only from 11:15 to 11:20, and only OIDs under
> `1.3.6.1.4.1.2011.5.25.31.1.1.1.1`."*

`tests/test_maintenance_window.py::test_the_maintainers_example_end_to_end` builds exactly that
window in the store, compiles it, and drives five traps through `Engine._process` — the real ingest
path, not `WindowIndex.decide` in isolation. What is asserted is the `alarm` table afterwards,
because a trap that did or did not become an alarm is the only thing an operator can see.

| # | host | wall clock (Brasília) | trap | what decides it | outcome |
|---|---|---|---|---|---|
| 1 | A | 11:00 | `linkDown`, **critical** | the severity rule admits | **collected** (`span-a`) |
| 2 | A | 11:01 | LOS, **major** | the severity rule refuses | suppressed |
| 3 | B | 11:17 | LOS, varbind under `…1.1.1.1` | slot **and** subtree admit | **collected** (`onu-in`) |
| 4 | B | 11:18 | LOS, varbind under `…1.1.1.10` | in the slot, **wrong arc** — the AND across kinds | suppressed |
| 5 | B | 11:40 | LOS, varbind under `…1.1.1.1` | right arc, **outside the slot** | suppressed |

```
assert collected == ["onu-in", "span-a"]
```

Row 4 is II.3 at the ingest path: `…1.1.1.10` is the arc a `startswith` would wrongly admit, and
it arrives *inside* the slot so that only the subtree can refuse it. Row 5 is the mirror — only the
slot can refuse it — and together they are what makes the composition an intersection rather than a
union. Row 2 is why D5 had to ship in the same release: with no severity placed there is no rank
for the rule to test, and every trap would take row 2's branch.

**The three cases this test deliberately does not carry**, because each belongs to a test that can
fail for one reason: `under_subtree` at the arc boundary as a pure function
(`test_an_oid_rule_matches_on_arc_boundaries_and_never_on_a_string_prefix`), an element that is not
a target (`test_the_check_is_free_on_an_appliance_with_no_windows`), and the window being over
(`test_the_patch_band_widens_the_window_at_both_ends`).

---

## 3. D5 and the severity measurement (II.1)

II.1 asked for the placement measured before and after, and for the mechanism named if 100% of
alarms were unplaced. Both are below. **They were, and the mechanism is named.**

### 3.1 Before

Reference lab, two hosts, fourteen cut/repair cycles, `make lab` then `control.py cut`:

```
alarms                                   30
  carrying an X.733 severity word        29
  with alarm.severity placed              0      <-- 0 of 30
census read (/api/stats)     {declared: 0, standard: 10, learned: 0}
```

The census and the rows disagreed, which is the whole finding. **v0.17.1 fixed the census, not the
row.** It taught the read model to parse `alarm.varbinds` and look for a standard severity word at
*display* time. D4's severity rule runs at **ingest**, where there is no census and no read model —
so `alarm.severity` was NULL and *every* severity rule on *every* estate would have admitted
nothing. The feature would have shipped, passed its unit tests against synthetic ranks, and done
nothing in the field.

### 3.2 The mechanism, named (II.1c)

II.1 asked specifically whether the severity column had been typed as an entity discriminator.
**It had.** Reading the profiler on the lab's own database:

```
varbind                     role      n_obs   n_distinct   score
<severity column>           entity      230            2   0.659
```

`ENTITY_PROMOTE_OBS` is 200 and the severity varbind is the **most-observed varbind on every NE** —
it is on every trap, and identifiers are not. So it crossed the floor first, and on the tick it
crossed, it was the only candidate above the floor. **It won uncontested as the lowest-scoring of
five candidates.** That is the mechanism: not a bad score, a race.

**A second defect, beyond what the brief asked me to look for.** `_resolve_entity` makes the finest
value in the promotion chain the alarm's dedup **instance**. With the severity column promoted, the
instance became a severity word:

```
alarm id=27   inst='major'   count=8        <-- six distinct ONUs in one row
entities: 'major', 'cleared', 'critical', 'minor'
```

Six ONUs with independent faults were one alarm. The inventory contained four "network elements"
named after severity words. This was not in the brief and it is the more serious of the two.

### 3.3 After

```
alarms                                   26      <-- the true fingerprint count
  carrying an X.733 severity word        25
  carried a severity, unplaced            0      <-- was 29
  with alarm.severity placed             25
promoted entity roles on severity columns 0
entities named after a severity word      0
instances                              ONU ids again
```

The one remaining unplaced alarm is the lab's deliberate no-severity rectifier fault — a trap that
genuinely carries no severity, which the appliance reports as unplaced rather than inventing one.

**What did it.**

* `engine/correlate/severity.py::place` — standard column first (value-keyed via
  `known_oids.standard_severity`), learned second, unplaced third. Called once per trap by the
  engine; the store never defaults. ADR **#365**.
* `0019_severity_provenance.sql` — adds `alarm.severity_source`, backfills `learned` for every
  existing non-NULL severity (true by construction: the learned path was the only writer that had
  ever existed), and **withdraws the entity role** from varbinds whose promoted keys are *all*
  X.733 tokens, via `HAVING COUNT(*) = SUM(CASE WHEN lower(trim(key)) IN (…) THEN 1 ELSE 0 END)`.
  History is untouched: no alarm, situation or label is edited.
* `engine/correlate/varbind_profile.py::speaks_severity` — stops it recurring, in
  `promotion_chain`, `best_promotable` and `_emerging_finer_child`.

### 3.4 The consequence nobody asked for: the census got 3.7× cheaper

The census no longer parses JSON per row; it reads three scalar columns the ingest path writes.
Measured on the lab's database amplified to **737 active alarms**, mean of 20 runs:

```
v0.20.0   JSON parse + vocabulary walk per row    5 105.2 us
v0.21.0   three scalar columns                    1 374.8 us      3.7x
```

Linear in the estate, on a route the console polls every few seconds. It is a **consequence** of
D5, not its purpose, and `_varbinds_of` — the defence against a truncated blob on that route — is
deleted, because there is no longer any JSON on the route to be truncated.

---

## 4. The decisions, and the refinement each answers

Full text in `docs/adr/DECISIONS.md`. One paragraph each here.

### 4.1 D1 — organization (ADR #366, #367; refinement II.4)

`organization` table, `ne.organization_id`, one seeded default, `/api/organizations`. **Attribution,
not isolation**, and the migration says so in a banner rather than leaving it to prose:

> ⚠ THIS IS NOT TENANT ISOLATION, AND NOTHING BELOW MAKES IT ONE.

Correlation still learns across every network element; a situation may still form across an
organization boundary; **no read path denies on the column**. II.4 asked whether this should reuse
the existing visibility-scope resolver and the answer is no: **F35's rule is that no resolver input
may be writable by a scopable role**, and an organization name is admin-written through an API. Two
questions, two mechanisms — *"which elements may I see?"* and *"whose are they?"*. `device`
deliberately does not gain the column: it belongs to whatever NE reports it, and a second
independently-writable answer is a second answer that can disagree.

### 4.2 D2 — time zones (ADR #362, #363; refinement II.5)

**Mandatory, with no new Python dependency.** `zoneinfo` is standard library and reads the
operating system's IANA database, so `pyproject.toml` is unchanged and `Dockerfile` gains one
`apt-get install tzdata` line.

II.5 named three cities with no zone of their own. Checked against this build's own `zoneinfo`:

```
America/Washington   INVALID  ->  America/New_York
America/Brasilia     INVALID  ->  America/Sao_Paulo
Asia/Beijing         INVALID  ->  Asia/Shanghai
```

So the picker **shows cities and stores canonical zones**: 114 curated entries over 94 distinct
zones. A window stores its instants **and** its zone — not one or the other — because a window
across a DST transition is not a fixed number of seconds, and `extend` on such a window would
otherwise move the wrong edge. A naive datetime is **rejected** at the API.

**The self-check, which is the part II.5 actually asked for.** A zone list validated on the build
machine is `docs/findings.md` F131's shape exactly — a thing verified where it does not run.
`timezone_selfcheck()` runs **in the running appliance**, against the `tzdata` that image actually
has, and reports a curated entry that does not resolve through the operator-warning channel that
already carries eight others.

### 4.3 D4 and II.2 — rules, and the ledger (ADR #368, #369, #371, #372)

A target under a window is **not collected by default**. Rules are the exceptions:

```
rules of DIFFERENT kinds are ANDed;  rules of the SAME kind are ORed.
```

The maintainer's own example settles it (§2): the slot and the subtree must intersect, and two
subtrees on one target are plainly a union because the operator wrote the second to admit more.

**II.3 — arc boundaries.** `under_subtree(oid, root)` is `oid == root or oid.startswith(root + ".")`.
`…1.1.1.1` admits `…1.1.1.1.4.2` and refuses `…1.1.1.10`, which a bare `startswith` accepts — the
tenth column of a table is not a member of the first column's subtree. Run as a standing injection,
not described. And **an OID rule says where it looks**: `trap` matches `snmpTrapOID.0` (what an
operator means by *"only link traps"*), `varbind` matches any varbind OID (what they mean when the
subtree is a table column — the maintainer's arc is `…Entry.column` under Huawei's `hwEntityMIB`,
so the example uses `varbind`). Explicit per rule, never guessed per estate.

**II.2 — the ledger.** Per `(window, device, class, instance)`: raise seen, clear seen, two
instants. Six scalars. **No varbinds, no severity, no community tag, no payload** — storing those
would be collecting the trap. The consequence is stated rather than hidden: an alarm surfaced from
the ledger says *"raised during the window, never cleared"* and **does not say how serious it is**,
because the appliance genuinely does not know; inventing a severity would be prime directive 2's
fabrication. Not an alarm, not shown, not correlated, not trained on, not in the dataset — four
standing injections, one per path. `ledger_enabled` defaults on and may be turned off, with the
risk in the operator's own words on the card.

**The third state, which the brief did not name.** A trap a window's rules *admit* is collected and
correlated normally and **excluded from learning and from the dataset** (#372). The operator asked
to see critical alarms during the work; they did not ask the appliance to learn that a splice
technician's traps co-occur. `teaches = not decision.under_window` is the one new term on the hot
path.

**An ordering defect this found.** `_seed_clear_pair` originally ran *after* the window check, so a
suppressed `linkUp` was never recognised as the clear for a suppressed `linkDown` and **two phantom
alarms surfaced at window end**. It now runs **before** the check, and the comment there says the
ordering is load-bearing.

### 4.4 D6 and II.7 — who confirms, and what an unconfirmed window does (ADR #370)

≤ 6 h: no confirmation. > 6 h: an editor or admin confirms, and the window waits in
`pending_confirmation`.

**What it does while waiting: nothing.** It suppresses not one trap, and if it is never confirmed
it expires having suppressed nothing. That is II.7's second edge answered in the only safe
direction — the failure mode of "waiting" is noise; the failure mode of "assume confirmed" is
silence.

**Who** (II.7's first edge): an **agent**-created window waits for a human editor or admin and the
agent may not confirm its own — `Principal.is_token` is the one place that is decided, read from
`kind` rather than guessed from an actor's name. A **human** editor or admin may confirm their own:
the form asks for an explicit second gesture, not a second person. Four-eyes is a policy this
appliance has nowhere else and inventing it in the one place nobody asked for it is how a product
acquires rules its operators cannot predict. Instead `mw.confirm` is a **separate capability** from
`mw.write`, so a deployment that wants four eyes withholds it from the role that holds `mw.write`.

### 4.5 D7 and D8 — the console

**D7**: the upcoming list shows the next **5 / 10 / 20**, and for `pending_confirmation` the confirm
action is **on the row**, not two clicks inside it — and only on that row, because offering it on an
`active` window would teach an operator that the control means nothing. `End now` is the mirror.

**D8**: the form is four cards, **one open at a time**, each collapsing to a one-line summary —
*What*, *When*, *Where*, *What still gets through*. Asserted at the DOM, because it is a claim about
what is in the document rather than about what a function returns:
`tests/test_maintenance_dom.py::test_the_form_opens_one_card_at_a_time` counts four cards and
exactly one open.

**IV.3, and the defect it nearly shipped as (F146).** *"Markers on every device and situation,
visible to every role."* This release wrote the component (`parts/mwmarker.js`, three exports, a
header on prime directive 4), wrote the query (`window_markers`, scoped in the WHERE clause so a
marker is never an existence oracle), cited it in four modules' prose as the mechanism — and
**wired neither end**. No route called the query; no view imported the component. It was found by
`vulture`, not by a test, because every test that mentioned a marker asserted it over a fixture
payload rather than over a response the appliance produced.

That is v0.16.3's `ne.label` again: a field the console rendered and the API never served, for
three releases, invisibly. What ships now is the marker on `/api/entities`,
`/api/entities/{ne_id}`, `/api/situations` and `/api/situations/{sid}`, rendered by `entities.js`
and `parts/card.js`, with `SurfacedMark` beside the instance in `parts/members.js` — and a test
that drives a real editors-only window through a real viewer's `/api/entities`, with the
absent-marker control beside it.

A situation is marked when **any** member element is under a window, and the **earliest-ending**
window wins: the question is *"when does this stop being incomplete?"*, and one suppressed member
is enough to make a situation's alarm counts incomplete.

---

## 5. The API shape (Part III)

Thirteen routes, 54 → **67** declared on the appliance.

```
GET    /api/maintenance-windows                    mw.read
POST   /api/maintenance-windows                    mw.write
POST   /api/maintenance-windows/preview            mw.read
GET    /api/maintenance-windows/{wid}              mw.read
POST   /api/maintenance-windows/{wid}              mw.write
POST   /api/maintenance-windows/{wid}/cancel       mw.write
POST   /api/maintenance-windows/{wid}/confirm      mw.confirm
POST   /api/maintenance-windows/{wid}/end          mw.write
POST   /api/maintenance-windows/{wid}/extend       mw.write
GET    /api/organizations                          organizations.read
POST   /api/organizations                          organizations.write
POST   /api/entities/{ne_id}/organization          organizations.write
GET    /api/timezones                              timezones.read
```

The last one is not decoration. `0020` seeds one default organization and attributes every element
to it, so an appliance that never calls it is consistent — but with no way to *move* an element,
a second organization is a row nothing can be put into, and D1 would be a column whose only
reachable value is its default.

Everything Part III asked for, and where it is:

| Requirement | Where |
|---|---|
| Create / read / list / update / cancel | The five above. **Cancel is a status, not a DELETE** — a window that was called off is a fact about the estate's history. |
| Preview as a dry run, **same code as the form** | `POST …/preview` calls `compile_rules` + `WindowIndex.build` — the identical functions the live check uses. A preview that ran different code would be a lie with a green tick on it. |
| Confirm / end / extend | Three routes, three audit actions, three capabilities. |
| Idempotency | `idempotency_key` with a **partial unique index**, so a replayed create returns the original window instead of a second one. |
| RFC 3339, explicit offset always | `AwareInstant`. A naive datetime is **rejected** — not assumed UTC, not assumed local. |
| Errors name the field and the rule | Every refusal. An agent cannot read a docstring; it corrects its request from the error. |
| Explicit enums in `/openapi.json` | Every enum is a `Literal`, so the schema carries the values instead of the word `string`. |
| No existence oracle | A window a caller may not see is **404, not 403**, and the timing is the same. |
| RBAC | Six capabilities; the declaration gate refuses an undeclared route at `create_app`. |
| Audit every write, actor and human-vs-token | Six `maintenance.window.*` actions in the frozen catalog; `.create` and `.update` carry `agent: true` from `Principal.is_token`. Organization creation reuses `config.change` — it is admin inventory edit, which that action already names. |
| Agent-created windows marked | `created_by_agent`, in the API **and on screen** (asserted at the DOM). |

---

## 6. Part V — the per-trap cost, before and after

`WindowIndex.decide()` driven directly, 200 000 repetitions per case:

| case | ns/trap | Δ vs v0.20.0 |
|---|---|---|
| **v0.20.0 (no check at all)** | **32.6** | — |
| no windows anywhere | 115.0 | **+82.3** |
| windows exist, none on this NE | 114.5 | **+81.8** |
| under a window, no rules (suppressed) | 1 249.9 | +1 217.3 |
| under a window, 5 rules (admitted) | 2 995.6 | +2 963.0 |
| under a window, 5 rules (refused on slot) | 1 951.3 | +1 918.7 |

**The headline is +82 ns per trap** on an appliance with no windows, or with windows on other
elements — the branch that has to be free, and it is one dictionary lookup. The suppressed path
costs more *in the check* and is **cheaper overall** than a collected one, because it writes no
alarm, forms no situation and produces no dataset rows.

**How the cost is held at that.** `decide` is a plain synchronous method over an immutable snapshot
swapped in whole by the maintenance loop. **No query, no lock, no I/O** — and that is a property
nobody can edit away, not a convention:
`tests/test_maintenance_window.py::test_the_per_trap_check_performs_no_query` reads the module's
AST and fails on an `await`, an `async def` or any name imported from the store. Same shape of
guard that replaced `TRAP_PATH_HASHES` in v0.18.0.

**Compared against the trap's own timestamp**, never against "the tick we are in". The index
refreshes every five seconds; a window boundary does not. A trap that arrived 40 ms before a window
opened is collected and one 40 ms after is not, however far either is from the refresh. That is why
the index carries windows that have not started (`LEADING_HORIZON_S = 3600`) and windows that have
just ended (`TRAILING_GRACE_S = 60`).

**Where it runs**: `decode → class/device/NE → entity → severity → [CHECK] → store → correlate`.
After severity because the severity rule needs one (§3); before the store because a suppressed trap
is one this appliance does not record.

---

## 7. Part VI — the analysis

### 7.1 Routes

54 → 67. **Every one of the thirteen is declared** in `crosscutting/rbac/route_map.py`; the
declaration gate refuses an undeclared route at `create_app`, so there is no way to add a handler
and forget the capability. The split of `tables.py` into `tables.py` (capabilities) +
`route_map.py` (routes) is the 400-line guard and nothing else, with the re-export asserted by
identity.

The honest criticism of this surface: **nine routes for one resource is a lot**, and the reason is
Part III's own — an agent needs verbs it can name, and `POST /api/maintenance-windows/{wid}` with
an `operation` field would make every refusal a paragraph about which operation was invalid. The
five gestures are five routes for the reason DECISIONS #255 gives about the operator gestures.

Route ordering is load-bearing in one place and it is documented where it bites:
`maintenance_ops.py` registers **before** `maintenance.py`, because FastAPI resolves the first
match and `POST …/preview` would otherwise read as a window whose id is `preview`. The route-order
baseline test pins it.

### 7.2 Database

Schema 18 → **21**. Five new tables and three new columns on existing ones:

| | |
|---|---|
| `organization` | id, name, slug, created_at, is_default. One seeded row. |
| `maintenance_window` | six statuses, `tz`, `starts_at`/`ends_at`, `patch_s`, `ledger_enabled`, `visibility`, `owner_ref`, `created_by_agent`, `needs_confirmation`, `idempotency_key` (partial unique index). |
| `maintenance_window_target` | window × NE. |
| `maintenance_window_rule` | **sparse**: one row per rule, the unused columns NULL. A wide row per target with nine nullable columns would make "no rule" and "a rule that matches nothing" the same shape. |
| `maintenance_ledger` | the six scalars, and a banner naming the columns it must never gain. |
| `ne.organization_id` | attribution (§4.1). |
| `alarm.severity_source` | provenance (§3.3). |
| `alarm.surfaced_from_window_id` | which window's ledger produced this alarm, so the console can say so. |

**All three migrations are forward-only and additive.** None deletes a row. `0019` is the only one
that changes existing data and it changes exactly one thing — a **role**, on varbind profiles —
which §3.2 argues was never correct.

The honest criticism: **`maintenance_window` is a wide table** (23 columns) and three of them
(`needs_confirmation`, `confirmed_at`, `confirmed_by`) are one fact stored three ways. I kept them
because the status machine reads `needs_confirmation` on every transition and deriving it from a
threshold at read time would put `CONFIRMATION_THRESHOLD_S` in two places. That is a trade I would
revisit if a fourth confirmation-related column ever appeared.

**A layer violation I built and then inverted.** The store originally imported `engine/mw/` to
compile windows. The layer model is `http > engine > data > ingest` with no upward imports, and
that was upward. Inverted: the **store returns rows** (`window_index_rows` returns plain dicts,
`LedgerRow` is a tuple of eight scalars) and the **engine compiles** them
(`engine/mw/compile.py`, `ledger.to_rows`). The cost is that `INDEX_LOOKAHEAD_S` and
`INDEX_LOOKBEHIND_S` are now literals in both places, so
`test_the_store_and_the_engine_agree_on_the_index_horizons` asserts them against each other. A
duplicated constant with a test on it is better than an inverted dependency; a duplicated constant
without one is how they come to differ by a factor of sixty.

### 7.3 Ingestion

**One new term on the hot path and one new call**, both in `engine/operate/engine.py::_process`:

```python
placed = severity.place(item.varbinds, self.ne_severity.get(ne_id))
await self._seed_clear_pair(item.trap_oid, class_id, item.ts)  # BEFORE the check — load-bearing
decision = self.windows.decide(
    ne_id, item.ts, item.trap_oid, tuple(vb.oid for vb in item.varbinds), placed.rank
)
if decision.suppressed:
    self.ledger.observe_suppressed(...)
    return
teaches = not decision.under_window
```

`severity.place` replaces a learned-only lookup that was already there. `windows.decide` is the
+82 ns of §6. `teaches` guards three calls that already existed. **Nothing else on the trap path
changed**, and `make eval` confirms the correlator's behaviour is identical on the frozen corpus
(§8) — which it must be, because no corpus trap is under a window.

`engine/operate/engine.py` is the one `COHESION_EXEMPT` module. Its ceiling moved 566 → 599 with
the compensating test the exemption requires. That is the single worst thing about this diff and I
am naming it rather than burying it: a module that is exempt from the cohesion guard is a module
whose growth nobody is measuring except by a number somebody raises. The sweep that ends windows
lives in `engine/operate/window_sweep.py` rather than here for that reason.

### 7.4 What I removed, one sentence each

1. **`_varbinds_of` (`store/read_models.py`)** — it defended the census against a truncated
   `alarm.varbinds` blob, and D5 moved the placement to ingest so the census reads three scalar
   columns and there is no JSON left on that route to be truncated.
2. **The `poll.read` and `poll.write` capabilities** — they gated D3's routes, and D3 is not in
   this release, so they promise an operator a power that nothing grants.
3. **The `poll.target.update` audit action** — it recorded a write to a poll target, and there are
   no poll targets and no route that writes one.
4. **The clock fields (`now`, `local_time`) on `GET /api/timezones`** — they made the response body
   vary second to second, which broke the unscoped-identity guard; the nicety is replaced by an
   optional `at` parameter, and **weakening the guard to keep the nicety was refused**.
5. **The entity role on varbinds whose promoted keys are all X.733 tokens (`0019`)** — it declared
   a severity word to be an element identifier, which collapsed six ONUs into one alarm row.
6. **`MaintenanceStat` and `nes_under_window_count`** — a console component and the query behind
   it for *"the Overview's one new number"*, neither of which was ever wired to the other, and
   rather than wire a third surface this release did not measure, both are deleted (F146).
7. **`load_ledger`, `mark_surfaced`, `nes_under_window`, `TargetRules.describe` and
   `mw_reads.PUBLIC_FIELDS`** — five helpers this release wrote and never called, each with a
   docstring claiming a job something else was already doing: the ledger survives a restart
   through `unresolved_ledger`, the sweep marks rows through `mark_ledger_surfaced`, and the
   redaction list lives in `api/mw_shape.py`, so a second copy in the store was the two-lists
   failure waiting to happen.
8. **`v0.16.8`'s `maintenance-windows` release claim** — it said this release's content shipped
   three releases ago, which is the exact contradiction `tests/test_documentation.py` exists to
   catch, and it was re-keyed rather than deleted so a reader who meets the number is told where
   the content went.

### 7.5 Declared behaviour changes

Things an existing operator will notice. All are in `MIGRATION.md` under *v0.21.0*.

1. **New alarms carry a severity where they used to read `—`.** An alerting rule keyed on
   "unplaced" will fire less.
2. **Alarms that were merged by a severity-word instance separate again** as new traps arrive.
   Counts on affected rows fall; the number of distinct alarms rises. 30 → 26 in the lab.
3. **Inventory entries named after severity words become inert** but are not deleted — deleting
   rows an operator may have annotated is not an upgrade's job. They should be removed by hand.
4. **Every NE joins one seeded default organization.**
5. **The container image needs `tzdata`.** Debian/Ubuntu hosts usually have it; Alpine usually does
   not; the appliance warns at startup rather than failing quietly.
6. **Nothing is suppressed until somebody declares a window**, and a window over six hours
   suppresses nothing until it is confirmed.
7. **Devices and situations under planned work are badged**, for every role, on every screen — and
   a member alarm the sweep surfaced carries a different badge saying the appliance knows the fault
   happened and nothing else about it.
8. **`GET /api/situations` and `GET /api/entities` gain a `maintenance` key**, null when nothing is
   in force. A client that ignores unknown keys is unaffected; one that asserts an exact key set
   will see it.
9. **Typing a city without its accent now finds it.** `Brasilia` finds `Brasília` (F147). Nothing
   about what is stored changed — still the canonical IANA identifier.

---

## 8. Verification

Run after the last change, in this order. Where a gate is quoted, the number is the one the command
printed.

| Gate | Result |
|---|---|
| Full suite | *see §8.1* |
| `mypy --strict` | *see §8.1* |
| `ruff` | *see §8.1* |
| `vulture` | *see §8.1* |
| `make dom` — **executed**, not compiled | *see §8.1* |
| `make security` — both halves | *see §8.1* |
| Coverage | *see §8.1*, **and what it does not measure is below** |
| `make eval` | *see §8.1* |
| Behaviour-identity record | *see §8.1* |
| wheel + sdist into a clean venv | *see §8.1* |
| Appliance booted through all migrations | *see §8.1* |

### 8.1 The recorded run

*(Filled from the final verification pass — see the commit that carries this file.)*

### 8.2 What coverage does not measure

Coverage says a line executed. It does not say:

* **that the assertion was meaningful** — the four-band timeline test would pass on a bar drawing
  four bands of any width, which is why it asserts `window > lead > 0` and `|lead − trail| < 1`;
* **that the DOM was driven** — `make dom` executes the console under `vm.SourceTextModule`; a unit
  test importing the same module scores identical coverage and proves nothing about the screen;
* **the migration path** — `tests/test_upgrade.py` drives the current store against *frozen old
  migration directories*, which is a different axis entirely, and the schema probes
  (`_has_severity_source` and its three siblings) exist because of it;
* **the injections** — a guard that has been green since the day it was written has demonstrated
  nothing. Each one below was **run red first**;
* **what a browser does** — §9.

### 8.3 The injections

Each is a deliberate defect, expected **red**, with a passing control beside it so a guard that had
simply stopped asserting would be caught. **The brief asked for sixteen; there are twenty**, and
the four extra are not padding — they are the injections the live pass and `vulture` forced into
existence after the first sixteen were green (§9.3).

| # | Injection | Guard |
|---|---|---|
| 1 | OID rule matched with `startswith` instead of arc boundaries | `test_an_oid_rule_matches_on_arc_boundaries_and_never_on_a_string_prefix` |
| 2 | OID rule matched varbinds when the rule said `trap` | `test_an_oid_rule_says_where_it_looks` |
| 3 | Rules of different kinds ORed instead of ANDed | `test_rules_of_different_kinds_are_anded_and_of_the_same_kind_are_ored` |
| 4 | A target with no rule collects everything | `test_a_target_with_no_rule_collects_nothing` |
| 5 | A severity rule admitted a trap with no placed severity | `test_a_severity_rule_refuses_a_trap_with_no_placed_severity` |
| 6 | The check compared against wall-clock now, not the trap's `ts` | `test_the_decision_is_made_against_the_traps_own_timestamp` |
| 7 | `decide` awaited a store call | `test_the_per_trap_check_performs_no_query` (AST) |
| 8 | A ledger row reached an alarm / a situation / the dataset | `test_the_ledger_reaches_no_alarm_no_situation_and_no_dataset` |
| 9 | Ledger disabled and a fault still surfaced | `test_the_ledger_can_be_turned_off_and_then_nothing_survives_the_window` |
| 10 | A fault that cleared inside the window surfaced anyway | `test_a_fault_that_cleared_inside_the_window_does_not_surface` |
| 11 | A collected-under-window trap taught the learner | `test_a_collected_trap_under_a_window_teaches_the_learner_nothing` |
| 12 | An unconfirmed window suppressed traps | `test_an_unconfirmed_window_suppresses_nothing_and_then_expires` |
| 13 | A city label stored in place of its canonical zone | `tests/test_timezones.py` |
| 14 | A window across a DST transition lost its wall-clock duration | `test_a_window_across_a_dst_transition_keeps_its_wall_clock_duration` |
| 15 | A viewer saw no marker at all | `test_a_viewer_sees_that_planned_work_exists_without_seeing_what_it_is` (DOM) |
| 16 | A naive datetime accepted and assumed UTC | `tests/test_maintenance_api.py` |
| 17 | `Brasilia` typed without the accent finds nothing (**F147, from the live pass**) | `test_the_three_named_cities_are_found_from_an_ascii_keyboard` |
| 18 | A surfaced fault in no situation, so on no screen (**F148, from the live pass**) | `test_a_surfaced_fault_lands_in_a_situation_an_operator_can_actually_see` |
| 19 | A marker served to an admin and not to a viewer (**F146**) | `test_a_viewer_sees_the_marker_on_a_device_under_planned_work` |
| 20 | An organization nothing can be moved into | `test_an_admin_can_move_an_element_to_another_organization` |

---

## 9. The live pass, and what it did not cover

**A real process, a real UDP socket, a real SQLite file, real HTTP, and a real Chromium.** No test
doubles, no ASGI transport, no fixtures. Two halves:

### 9.1 The appliance — 20 of 20

Booted from an **empty database** (`21 migrations applied, schema 0 -> 21`), with the bootstrap
banner printed once, exactly as `docs/operate.md` §1 describes. Traps were BER-encoded by a
throwaway script — nothing from this repository — and sent over `127.0.0.1:11162/udp`.

```
PASS  bootstrap admin signs in and rotates its password (HTTP 200)
PASS  the picker finds Brasilia: [('Brasília', 'America/Sao_Paulo')]
PASS  and stores America/Sao_Paulo rather than the city label
PASS  tzdata self-check warnings: []
PASS  three hosts discovered from traps alone: ['127.0.0.2', '127.0.0.3', '127.0.0.4']
PASS  D5: severities placed at ingest from the standard column: ['major']
PASS  preview (same code the form runs): devices=2 active_alarms=2 rules=3
PASS  a naive datetime is refused and the refusal names the rule
PASS  window created; under six hours, no confirmation: status=scheduled
PASS  a replayed create returns the original window: 2 == 2
PASS  window is active
PASS  IV.3: 2 situation(s) carry a maintenance marker
PASS  IV.3: exactly the two targets are marked on the device list
PASS  window ended early: {'ended': True, 'surfaced': 2}
PASS  II.2: 2 fault(s) surfaced at window end: [('major', None), ('x', None)]
PASS  and each carries NO severity, because the trap itself was never collected
PASS  the marker clears when the window is over
```

### 9.2 The browser — 45 of 45

Chromium, three widths × three roles, driven through the real login form:

```
viewer / editor / admin  @  390 × 844, 820 × 1180, 1440 × 900
PASS  screen renders
PASS  viewer not offered the form (0 controls) / editor and admin offered it (1)
PASS  2 device marker(s) visible  — to the VIEWER too, on an editors-only window
PASS  the marker carries no window name ('Under maintenance · 1 h 54 min left')
PASS  horizontal overflow 0px
```

The third and fourth lines together are prime directive 4 in a browser: an `editors`-visibility
window, a viewer who may not read its name, and a marker on the device anyway.

### 9.3 It found two defects that every test in this repository missed

* **F147** — the time-zone search was not accent-blind, so **`Brasilia` returned nothing**. The one
  city the brief names most often, unfindable from an ASCII keyboard, in the feature whose premise
  is mandatory time zones. Second assertion of the run.
* **F148** — a surfaced fault belonged to **no situation**, and Situations is the only view that
  lists alarms. `POST …/end` answered `{"surfaced": 2}` and the console showed nothing. II.2 was
  true of the database and false of the product. Last assertion of the run.

Both are fixed, both have a test that fails without the fix, and both are in `docs/findings.md`.

### 9.4 What it did not cover, and why

* **No container.** There is no Docker daemon in this environment, so `make lab`, the two-host NE
  images and `testbed/` did not run. **`Dockerfile`'s new `tzdata` line is therefore unexecuted** —
  reviewed, not built. On this host `zoneinfo` resolves every curated zone from the system
  database, which is precisely why the self-check exists rather than a build-time assertion
  (F131's lesson). **If one thing in this release is verified only by reading, it is that line.**
* **No real network element.** Traps were synthesised. Their varbind sets are thinner than a real
  NE's, which shows in the live run: `instance` came out as `'major'` because the severity column
  was the *only* varbind on the trap and there was nothing else to key on. That is an artefact of
  the driver, not the appliance — the same traffic through the lab produces ONU ids (§3.3) — but
  it means the live pass did **not** exercise entity resolution under realistic varbind sets.
* **No DST transition in wall-clock time.** The window across a transition is asserted in
  `test_a_window_across_a_dst_transition_keeps_its_wall_clock_duration`, not live: a live one would
  need the clock moved, and an appliance whose clock jumps is a different test.
* **No long window.** D6's confirmation path was driven by unit and API tests, not live — a live
  six-hour window needs six hours.
* **No multi-organization estate.** One seeded organization, one moved element. Two providers with
  overlapping elements was not driven.
* **No load.** One trap at a time. The Part V figures are a benchmark of `decide`, not a
  measurement of the appliance under a storm.

## 10. Appendix A — the handover

* **Branch**: `claude-code/focused-brown-7u4wao`.
* **Tags**: **there are none.** `git tag -l` returns nothing in this repository — not "none for this
  release", none at all, for any release. The CHANGELOG and `docs/plans/releases.md` are the record
  of what shipped, and `docs/record.md` names the commit (`3ecf237`) that holds the deleted
  long-form history. Stating this is the brief's instruction where the tags do not exist.
* **`.git`** is complete.
* **No caches** in the tree handed over: `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`
  and `node_modules` are excluded.
* **This file is at the root.**
