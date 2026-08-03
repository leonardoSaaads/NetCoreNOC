# Upgrading NetCoreNOC

## v0.8.0 → v0.8.1 (the dataset's lifecycle — no migration, and one thing you have lost)

**Read this one, even though it is a patch release.** It changes nothing about how you run the
appliance and everything about how long your data lives. Two of the three things below cannot be
undone by upgrading, so they need a decision from you rather than just a restart.

Replace the code and restart. **No migration runs.**

| | |
|---|---|
| Schema | **`user_version` stays 8** — no new table, no new column, no migration file |
| Data | untouched. A v0.8.0 database opens with an identical schema hash and identical row counts |
| Audit chain | verifies with the **identical final hash** |
| Routes | **none added**. The two retention routes are unchanged in path, method and request shape |
| API contract | unchanged. `POST /api/dataset/retention` gains two **additive** response fields (`bound`, `training_deletes`); nothing was removed or retyped |
| Environment variables | **unchanged** |
| Runtime dependencies | **unchanged** (still five) |
| How you run it | **unchanged** |

### ⚠ 1. Your operator labels were being deleted after seven days. The ones already gone are gone.

This is **F44**. Until v0.8.1, the background maintenance loop deleted every `feedback` row — the
human verdict, the label your feedback dataset is *made of* — once its situation had been closed for
longer than **`NETCORENOC_RETENTION_DAYS`, whose default is 7 days**. The membership record
(`feedback_member`) went with it. The `dataset_pair` features those verdicts justified **survived**,
because they carry no foreign key to the situation.

So the failure was silent and one-sided: your corpus kept growing and its labels kept evaporating,
and the bias report's label count only ever showed the last week.

**Labels already lost cannot be recovered.** Nothing recorded them, nothing archived them, and no
upgrade can reconstruct a human judgement. If you have been running v0.8.0 for longer than a week,
assume you have the labels from roughly the last seven days and no others. Run
`python -m netcorenoc dataset bias` after upgrading: the **`ORPHANED: promoted, label since gone`**
figure is the size of what you lost, in feature rows.

Nothing you can do repairs it. It stops happening the moment you upgrade.

### ⚠ 2. Any retention policy you set was never saved. Set it again.

If you used `POST /api/dataset/retention` with `"preview": false` on v0.8.0, the route answered
`"saved"` and wrote an audit row — and the policy lived only in memory. **The next restart silently
reverted to the shipped defaults** (21 d / 2 000 000 rows / 365 d / 730 d).

The deletion it performed at the time was real and permanent. The configuration was not.

**Re-apply your policy after upgrading.** It is now stored in `meta` and read at startup, so it
survives restarts. You can confirm what is actually in effect with `GET /api/dataset/retention`.

### 3. The three tiers now mean what they say

v0.8.0 shipped three tiers and enforced one. `training_days` was only the cutoff of an explicit
admin reduction, and `audit_days` was validated, recorded and reported — and read by **no deletion
path at all**. Two of the three were numbers that changed nothing.

| Tier | Default | What it does now |
|---|---|---|
| **sink** | 21 d **and** 2 000 000 rows | **deletes**, on the maintenance loop — unchanged |
| **training** | 12 months | **selects.** A query window. **Nothing is deleted at this boundary.** |
| **audit** | 24 months | **deletes** — the one background path that can reach a label |

**What this changes for you in practice:**

* **Lowering `training_days` no longer destroys anything.** On v0.8.0 it was the cutoff the
  destructive apply used. It is now a statement about what a model may *read*, so narrowing it is
  free and reversible. The response says so: `"training_deletes": 0`.
* **Lowering `audit_days` is now the destructive control.** The preview and the apply both cut on
  it, and both responses carry `"bound": "audit"` so you can see which tier the counts belong to.
  This is the one to be careful with, and it still previews by default.
* **The maintenance loop now deletes at the audit bound.** It did not before. With the default of
  730 days this will do nothing to any existing deployment for two years — but if you set a *short*
  `audit_days`, understand that the loop will enforce it, count what it destroyed, and report the
  running total on `GET /api/dataset/retention`.

The ordering rule `sink < training ≤ audit` is unchanged and still refuses a policy that breaks it,
naming the tier that is wrong.

### 4. If the stored policy is ever unreadable

A `meta` value that cannot be parsed — bad JSON, a missing field, a wrong type, or an ordering
violation — is **ignored as a whole**, the shipped defaults apply, and a warning appears in
`/api/stats`. It is never partially reconstructed: a policy nobody set must not become a policy that
deletes. Re-apply through the route and the warning clears.

### What to check after upgrading

```bash
python -m netcorenoc dataset bias     # ORPHANED tells you what F44 cost you
curl .../api/dataset/retention        # confirm the policy in effect is the one you want
python -m netcorenoc audit verify     # unchanged: the chain verifies to the same final hash
```

---

## v0.7.5 → v0.8.0 (the feedback dataset — one migration, and capture starts)

**Read this one.** Unlike the last four upgrades, this release changes what the appliance stores and
adds a control that can destroy data.

Replace the code and restart. Migration `0008` applies automatically at startup, as every migration
does.

| | |
|---|---|
| Schema | **`user_version` 7 → 8** — four new tables, fifteen nullable columns on `feedback`, one on `situation` |
| Data | untouched. Every existing row keeps its values; the migration seeds **no** dataset rows |
| Audit chain | verifies with the **identical final hash** — no audit row is touched |
| Routes | **two added**, both admin-only: `GET`/`POST /api/dataset/retention` |
| API contract | `POST /api/situations/{sid}/feedback` still accepts `{verdict}` alone. Two optional fields are added; omitting them is legal and means *unrecorded* |
| Environment variables | **unchanged** |
| Runtime dependencies | **unchanged** (still five) |
| How you run it | **unchanged** — plus `python -m netcorenoc dataset bias` and `… dataset stats` |
| Downgrade | **one-way in practice.** v0.7.5 code opens a v0.8.0 database and runs — it ignores the new tables — but anything captured after the upgrade stops being written and the sink stops being pruned. Take a copy of the database before upgrading if you want a real rollback |

### Capture starts on upgrade, and it is on by default

From the first trap after restart, the appliance records **one row per evaluated correlation pair**
and one per alarm activation. This is the dataset every release from v0.9.0 onward is built on, and
it ships **on** for one reason: its value compounds with time, and a deployment that discovers it
six months in has lost six months that **cannot be reconstructed**.

**What it costs.** Measured over the project's own evaluation corpus:

| | |
|---|---|
| added rows per trap | **~62** |
| added bytes per trap | **~6.9 kB** |
| database growth | **~9.9×** |

**That corpus is 86 % storm by construction — it is the worst case, not the typical one.** A quiet
network produces far fewer candidate pairs per alarm and therefore far fewer rows. Check yours:

```
python -m netcorenoc dataset stats
```

**To turn capture off**, set `enabled = False` on the engine's `Capture` object at startup. There is
deliberately no environment variable: switching it off silently loses data nothing can recover, so it
is a code-level decision a deployment makes once and can point at, rather than a flag someone flips
during an incident.

### Retention: three tiers, and the defaults

| Tier | Default | What it holds | What the bound *does* |
|---|---|---|---|
| Sink | **21 days**, **and** 2 000 000 rows — whichever binds first | evaluated pairs awaiting a label | **deletes**, on the maintenance loop |
| Training dataset | **12 months** | pairs promoted by an operator's verdict | **selects** — a query window. Nothing is deleted here. |
| Audit archive | **24 months** | the outer bound | **deletes** — the one background path that can reach a label |

Read and change them with `GET` / `POST /api/dataset/retention` (admin only). The ordering
`sink < training ≤ audit` is enforced, and a rejection tells you which tier is wrong and why.

> **Clarified 2026-08-02 (v0.8.1) — what the middle tier means.** v0.8.0 shipped these three tiers
> and enforced only the first: `training_days` was the cutoff of an explicit admin reduction and
> nothing else, and `audit_days` was validated, recorded and reported but read by **no deletion path
> at all**. Two of the three were numbers that described nothing.
>
> v0.8.1 gives them meanings that are true. **The training tier is a selection window, not a
> deletion policy** — a training-retention *delete* destroys evidence in order to express a
> modelling preference, and wanting to train on the last twelve months is a statement about
> *selection*, which is a `WHERE` clause. Nothing has to die for a model to ignore it, and keeping
> it means the choice stays revisable. **The audit tier is the outer bound of the data's life**, and
> the only background sweep that can delete a label. DECISIONS #110.

> **The row cap is what actually governs, not the 21 days.** At ~62 rows per trap the 2 000 000-row
> default is used up after roughly **3.7 days at 0.1 traps/s** and **9 hours at 1 trap/s**. The cap
> is a **disk budget** (~220 MB of pair rows); the 21-day figure is a ceiling most deployments never
> reach.
>
> `python -m netcorenoc dataset stats` reports the window you **actually** have. Use that number,
> not the configured one. A label given after the sink has evicted its pairs is still recorded — it
> is marked `coverage: none`, and the bias report counts it.

### ⚠ Lowering retention deletes data, and there is no undo

This is the **first destructive control this product has ever had**. Every other admin setting is
reversible — scorer configurations are append-only and roll back in one click, governance policies
are versioned. **A `DELETE` is not.** Moving training retention from twelve months to three destroys
nine months of operator labels: the most expensive and least reconstructible thing in the system.

So the endpoint **previews by default**:

```bash
# Shows what WOULD be removed. Deletes nothing.
curl -X POST .../api/dataset/retention -d '{"sink_days":21,"sink_rows":2000000,
                                            "training_days":90,"audit_days":730}'
# => {"status":"preview","applied":false,"would_delete":{"pairs":…,"labels":…,"oldest":…}}
```

Applying requires `"preview": false`, sent deliberately, after you have seen the count. Both the
preview and the change are written to the audit log (`retention.preview`, `retention.change`, the
latter with before, after, and the impact you were shown).

~~The background maintenance loop **never** deletes labelled data. It bounds the sink and nothing
else.~~

> **Corrected 2026-08-02 (v0.8.1). The struck sentence was false when it was written.** The
> maintenance loop called `store.prune()`, which deleted `feedback` rows — the human verdicts — for
> every situation closed longer than the **operational** retention (`NETCORENOC_RETENTION_DAYS`,
> default **7 days**), taking `feedback_member` with them by `ON DELETE CASCADE`. The promoted
> `dataset_pair` rows survived. That is **F44**, reproduced in
> [`docs/gates/v0.8.1-phase-0.md`](docs/gates/v0.8.1-phase-0.md) §1, and it is fixed in v0.8.1.
>
> The sentence is now true, and for a different reason than it claimed: labels are no longer
> governed by the operational retention at all, and the **only** background path that can delete one
> is the **audit sweep**, at the audit bound the operator set. See the tier table above and
> DECISIONS #109, #110.

### Labels written before v0.7.5 are marked, and excluded from training by default

v0.7.5 repaired a defect in which a situation card could be rebuilt underneath the operator, so a
click could be recorded against a grouping they had not been reading. Labels written **before** that
fix are marked `capture_provenance = 'legacy_capture'` by the migration.

**They are not deleted, and they are not assumed to be wrong.** They are of *unknown quality*, which
is a weaker and different claim, so they are kept, marked, **excluded from training by default**, and
includable by an explicit choice — which preserves a comparison a later release may want. The bias
report counts them separately and never averages them in.

The migration writes only this marker. It makes no guess about any individual verdict.

### The bias report

```
make bias-report        # or: python -m netcorenoc dataset bias
```

Aggregates only — no NE names, addresses, OIDs or varbind values ever leave it. It is deterministic,
so `make qa` compares it byte-for-byte against a fixture and fails the day capture changes shape.

Read the **effective sample size** section before you believe any number derived from this data: *n*
is the number of independent labelled **bags**, not the number of pairs, and the two differ by more
than an order of magnitude.

### One more thing

The route-declaration gate now refuses a route registered with an **empty method set** (F43). Like
F40–F42 before it, this is a startup-time check on the appliance's own routes, **not on the request
path**, and no route in the shipped application is affected.

---

## v0.7.4 → v0.7.5 (the operator's click, and two guards)

**There is nothing to do. Stop reading.**

No migration runs. No configuration changes. No API changes. Replace the code and restart.

| | |
|---|---|
| Schema | **unchanged** — `user_version` stays **7**; no migration file added |
| Data | untouched — learned state, scorer configuration, governance policy, provenance, labels, feedback and the audit chain all carry over byte for byte |
| Routes | **unchanged** — every path, method, status code and response field is identical, in the same order |
| Capabilities | **unchanged** — `PERMISSIONS`, `ROUTE_PERMISSIONS`, `ROUTE_SCOPE`, `PUBLIC_ROUTES` and the audit action catalog are all the same |
| Environment variables | **unchanged** |
| Runtime dependencies | **unchanged** (still five) |
| How you run it | **unchanged** — `python -m netcorenoc.main` and `python -m netcorenoc audit verify`, exactly as before |
| Downgrade | safe — v0.7.4 reads a v0.7.5 database, because it is the same database |

**One thing you will notice, and it is deliberate.** In the Situations panel, a card you have
**expanded is now held**: it stops being rebuilt by the live update while it is open, so the grouping
you are reading — and the Confirm / Split buttons under it — cannot change under your click. A held
card carries a **`held while open`** badge in its header saying so, and resumes live updates the
moment you collapse it. Before v0.7.5 the card was destroyed and rebuilt every two seconds, which
meant a click could be recorded against a grouping you had never actually looked at. Nothing else in
the UI changes.

It also makes the **route-declaration gate** refuse route shapes it cannot check (F42) — an included
router, a mount, a websocket route, or a `HEAD`-only route. Like F40 and F41 before it, this is a
startup-time check on the application's own routes; **it is not on the request path** and cannot
change how an existing request is answered. It matters to you only if you have added routes to a
fork by one of those means. On an unmodified NetCoreNOC there is nothing to change.

**Verified, not assumed.** A database written by the **real v0.7.4 code** — devices driven through
the real engine from real traps, situations, links, a user, an API token and a hash-chained audit
row — was opened by v0.7.5: no migration ran, the schema and every row count were identical,
`PRAGMA integrity_check` returned `ok`, and the audit chain verified under **both** versions to the
same final hash (`405275083f09…`). `make eval` is byte-identical. See `docs/gates/v0.7.5-phase-5.md`
§1 and §8.

## v0.7.3 → v0.7.4 (the last loose ends — structure, plus two gate fixes)

**There is nothing to do. Stop reading.**

No migration runs. No configuration changes. No API changes. Replace the code and restart.

| | |
|---|---|
| Schema | **unchanged** — `user_version` stays **7**; no migration file added |
| Data | untouched — learned state, scorer configuration, governance policy, provenance, labels, feedback and the audit chain all carry over byte for byte |
| Routes | **unchanged** — every path, method, status code and response field is identical, in the same order |
| Capabilities | **unchanged** — `PERMISSIONS`, `ROUTE_PERMISSIONS`, `ROUTE_SCOPE`, `PUBLIC_ROUTES` and the audit action catalog are all the same |
| Environment variables | **unchanged** |
| Runtime dependencies | **unchanged** (still five) |
| How you run it | **unchanged** — `python -m netcorenoc.main` and `python -m netcorenoc audit verify`, exactly as before |
| Downgrade | safe — v0.7.3 reads a v0.7.4 database, because it is the same database |

v0.7.4 splits `src/netcorenoc/shaping.py` into the package `src/netcorenoc/shaping/`,
`src/netcorenoc/rbac.py` into `src/netcorenoc/rbac/`, and extracts
`src/netcorenoc/varbind_accum.py` from `varbind_profile.py`. All three are internal:
`netcorenoc.shaping`, `netcorenoc.rbac` and `netcorenoc.varbind_profile` keep their names and every
symbol reachable through them, so anything importing them keeps working unchanged.

It also closes two holes in the **route-declaration gate** (F40, F41). Both are import-time or
startup-time checks on the application's own routes; **neither is on the request path**, and neither
can change how an existing request is answered. They matter to you only if you have added a route to
a fork: an `/api` route registered by any means must now declare its capability and scope posture in
`rbac/tables.py`, and a non-`/api` route must be listed in `declare.UNAUTHENTICATED_PATHS` or live
under `/api`. On an unmodified NetCoreNOC there is nothing to change.

**Verified, not assumed.** A database written by the **real v0.7.3 code** — users, devices driven
through the real engine from real traps, situations, operator feedback and a hash-chained audit log —
was opened by the v0.7.4 wheel in a clean virtualenv: no migration ran, the store snapshot was
byte-identical (`sha256 28f636fd…2e96`) and the audit chain verified with the same final hash
(`3d1bdf8a…79d9`). Separately, all **56** function bodies moved by the three splits were proved
unchanged by a `sha256` table taken before the move and recomputed after it, and `make eval` is
byte-identical. See `docs/gates/v0.7.4-phase-5.md` §1 and §10.

If you have written code against the internals, every name still resolves from its original module
path — including the private helpers. The v0.7.3 caveat about monkeypatching a module-level constant
applies here too: patch it on the module that *reads* it. `netcorenoc.varbind_profile`'s constants
are now defined in `netcorenoc.varbind_accum` and re-exported, so patch the former only if the reader
is `varbind_profile` itself. That is a test-harness concern, not a runtime one, and no supported
configuration is affected.

---

## v0.7.2 → v0.7.3 (the data and engine layers — internal structure only)

**There is nothing to do. Stop reading.**

No migration runs. No configuration changes. No API changes. Replace the code and restart.

| | |
|---|---|
| Schema | **unchanged** — `user_version` stays **7**; no migration file added |
| Data | untouched — learned state, scorer configuration, governance policy, provenance, labels, feedback and the audit chain all carry over byte for byte |
| Routes | **unchanged** — every path, method, status code and response field is identical, in the same order |
| Capabilities | **unchanged** — `PERMISSIONS`, `ROUTE_PERMISSIONS`, `ROUTE_SCOPE`, `PUBLIC_ROUTES` and the audit action catalog are all the same |
| Environment variables | **unchanged** |
| Runtime dependencies | **unchanged** (still five) |
| How you run it | **unchanged** — `python -m netcorenoc.main` and `python -m netcorenoc audit verify`, exactly as before |
| Downgrade | safe — v0.7.2 reads a v0.7.3 database, because it is the same database |

v0.7.3 splits `src/netcorenoc/store.py` into the package `src/netcorenoc/store/` and
`src/netcorenoc/main.py` into `engine.py`, `runner.py` and four smaller modules. Both are internal.
`netcorenoc.store` and `netcorenoc.main` keep their names and every symbol that was reachable
through them, so anything importing either keeps working unchanged — **including
`python -m netcorenoc.main`, which is why `main.py` stayed a module and did not become a package.**

**Verified, not assumed.** A database written by the **real v0.7.2 code** — 120 traps through the
real engine, a maintenance pass, a scorer configuration, an active governance policy, a bootstrap
admin and a hash-chained audit log — was opened by v0.7.3: no migration ran, twenty snapshot keys
compared identical, and the audit chain verified with the same final hash. Separately, all 141
method bodies in `Store` and `Engine` were proved unchanged by a `sha256` table taken before the
move and recomputed after it. See `docs/gates/v0.7.3-phase-5.md` §1 and §8.

If you have written code against the internals — importing `netcorenoc.store` or `netcorenoc.main`
in a script of your own — everything in the public surface still resolves. The one thing that does
**not** carry over is monkeypatching a module-level constant: `netcorenoc.store.MAX_SCOPE_PARAMS`
and `netcorenoc.main.MAX_ENTITIES_PER_NE` must now be patched on the module that *reads* them
(`netcorenoc.store.read_models` and `netcorenoc.engine`). That is a test-harness concern, not a
runtime one, and no supported configuration is affected.

---

## v0.7.1 → v0.7.2 (the HTTP package — internal structure only)

**There is nothing to do. Stop reading.**

No migration runs. No configuration changes. No API changes. Replace the code and restart.

| | |
|---|---|
| Schema | **unchanged** — `user_version` stays **7**; no migration file added |
| Data | untouched — learned state, scorer configuration, governance policy, provenance, labels, feedback and the audit chain all carry over byte for byte |
| Routes | **unchanged** — every path, method, status code and response field is identical, in the same order |
| Capabilities | **unchanged** — `PERMISSIONS`, `ROUTE_PERMISSIONS`, `PUBLIC_ROUTES`, `AUDITED_DENIED_PERMISSIONS` and the audit action catalog are all the same |
| Environment variables | **unchanged** |
| Runtime dependencies | **unchanged** (still five) |
| Downgrade | safe — v0.7.1 reads a v0.7.2 database, because it is the same database |

v0.7.2 splits `src/netcorenoc/api.py` into the package `src/netcorenoc/api/` and adds a
route-declaration table. Both are internal: `netcorenoc.api.create_app` keeps its name, its
signature and every symbol that was reachable as `netcorenoc.api.X`, so anything importing the
package keeps working unchanged.

**Verified, not assumed.** A database written by the real v0.7.1 code — 40 traps through the engine,
users, service tokens, four scorer configurations, both governance policy kinds with history,
labels, feedback and a hash-chained audit log — was opened by v0.7.2: no migration ran, the whole
store snapshot compared identical, the audit chain verified with the same final hash, and all 46
routes returned byte-identical responses under both versions. See
`docs/gates/v0.7.2-phase-5.md` §5.

---

## v0.7.0 → v0.7.1 (the write perimeter — a security patch)

v0.7.1 fixes six defects (F34–F39) in which a v0.7.0 guarantee was enforced on reads and not on
writes. It adds no feature, no route, and no configuration.

**Migration `0007_write_perimeter.sql` is transparent.** It adds two nullable columns, one unique
index, and two clean-ups, and it **seeds nothing**. Apply it the way you always do — it runs at
startup, or `make migrate`. Back up the database file first, as always.

### What the migration does

| | |
|---|---|
| Schema | `user_version` 6 → 7, forward-only and additive |
| Adds | `feedback.principal_ref` and `feedback.role` (both nullable, **not** backfilled); a `UNIQUE (situation_id, verdict)` index on `feedback` |
| Removes | duplicate `feedback` rows (keeping the **earliest** of each `(situation, verdict)` by `created_at`), and `label` rows naming a device or alarm class that does not exist |
| Seeds | **nothing** |
| Runtime dependencies | unchanged (still five) |

The de-duplication and the orphan cleanup only remove rows that v0.7.0's missing constraints let in.
Your alarms, situations, links, learned edges, scorer configuration, governance policy, provenance
and audit chain are untouched, and the audit chain's head hash is byte-identical across the upgrade.

### Action required: review any scope policy that uses a label glob

**This is the one breaking change, and it affects one configuration only.**

v0.7.0 resolved a glob scope selector against the **operator label** when a network element had
one, so `{"editor": ["core-*"]}` selected everything labelled `core-…`. But the operator label is
written by `POST /api/labels`, an **editor**-level route — so the role being scoped could widen its
own scope simply by relabelling a device it was not allowed to see (F35). That is a privilege
escalation, and closing it properly means the label cannot be an input to the decision at all.

**Since v0.7.1 a selector resolves against network-element identity and address only.** A selector
that can never match an address is now rejected when you write the policy, with a message saying
so.

| Selector | v0.7.0 | v0.7.1 |
|---|---|---|
| `ne:12` | NE id 12 | unchanged |
| `10.0.0.1` | that address | unchanged |
| `10.0.0.0/24` | that range | unchanged |
| `10.0.*` | address glob | unchanged |
| `core-*` | matched the **label** | **matches nothing** — rejected at write time |
| `POP-SUL` | matched that label | **matches nothing** — rejected at write time |

**What to do.** Open **Governance → Visibility scope** and look at each selector. If any is a name
rather than an address, replace it with the addresses, CIDRs, or `ne:<id>` values of the elements
you meant. Until you do, that line selects nothing — which is fail-closed: a viewer or editor sees
*less* than intended, never more. Admins are never scoped, so you can always get in to fix it.

If you have no scope policy stored — the default, and most installations — **there is nothing to
do.**

### Two other behaviour changes you may notice

Both are defect fixes, and both are deliberate:

- **A label write to a target that does not exist now returns 404** (was 200, and the row
  persisted). If you have automation that labels devices by id, it will now get an honest error for
  an id that is not there. This is the same 404 an out-of-scope target returns, deliberately, so the
  two are indistinguishable.
- **Posting the same feedback verdict on the same situation twice is now a no-op.** The second call
  still answers 200, but it records nothing and changes no learned state. Changing your mind
  (`confirm` after `split`, or the reverse) is a correction and still applies. Previously each
  repeat applied again *and* aged the whole appliance's learned state, so a loop could drive every
  learned mass to near zero.

Everything else is byte-identical to v0.7.0: every route, every status code, every shaped field,
and `make eval`.

---

## v0.6.0 → v0.7.0 (governance — one migration, and nothing changes until you ask it to)

v0.7.0 lets an admin restrict what each role and principal may **do** (capabilities) and may
**see** (network elements). Both are stored, audited policy read through the existing single
decision points.

**Nothing changes on upgrade.** Migration `0006_governance.sql` adds two tables and **seeds no
rows**. With no policy stored, the compiled role permissions and full visibility are what you get
— byte-identically v0.6.0. Every route, status code, and shaped field is unchanged, `make eval` is
unchanged, and most operators never open the Governance panel. There is **no breaking change and
no action required**.

### What the migration does

| | |
|---|---|
| Schema | `user_version` 5 → 6, forward-only and additive |
| Adds | `governance_policy` (append-only history, `RAISE(ABORT)` on UPDATE/DELETE) and `governance_active` (a per-kind pointer) |
| Seeds | **nothing** — that is what makes the upgrade invisible |
| Removes | nothing |
| Runtime dependencies | unchanged (still five) |

Apply it the way you always do — it runs at startup, or `make migrate`. Back up
`netcorenoc.db` first, as with any upgrade.

### If you do choose to write a policy

- **A capability policy can only take capabilities away.** The compiled permission map is the
  *ceiling*: the resolved set is `ceiling(role) ∩ policy`, so an entry naming a capability above a
  role's ceiling has **no effect** rather than granting it. There is no way to give a viewer an
  admin capability through configuration.
- **An admin can never be locked out.** The capabilities needed to read and repair the governance
  policy stay with the admin role no matter what a policy says, and **admins are never scoped**.
- **Clearing is one click** and returns the appliance to the shipped baseline. Policy history is
  append-only, so a clear removes the pointer, not the record.
- **If a policy becomes unreadable**, capabilities fall back to the built-in permissions (nobody
  gains anything) and scoping denies viewers and editors (nobody sees anything new). Both raise an
  operator warning and write an audit row; the admin repairs it from the Governance panel.

### ⚠ Visibility scoping is a presentation control and is **not tenant isolation**

Read this before you use scoping to separate customers or teams.

Scoping decides **what a principal is shown**. It does **not** partition what NetCoreNOC learns,
correlates, or groups:

- correlation still learns across **all** network elements — the class and NE affinity matrices are
  global, and feedback from any operator still moves them;
- a situation may still **form across** a scope boundary; its out-of-scope members are then shown
  to a scoped reader as a redacted count and their alarm classes, never as identifiers;
- situation ids, timing, and learned edge weights are global by construction.

A scoped operator therefore sees a **partial picture** and could mis-size an incident that spans
the boundary — which is exactly why the redacted count is shown rather than the members being
silently omitted. True multi-tenant isolation (per-tenant learning, per-tenant situation
boundaries, per-tenant retention and audit segmentation) is a separate, larger feature on
[`docs/ROADMAP.md`](docs/ROADMAP.md), and v0.7.0 does not provide it.

## v0.5.0 → v0.6.0 (the scoring seam — one migration, one removal)

v0.6.0 makes the correlation formula configurable, explainable, reproducible, and reversible.
**Grouping does not change**: at the default parameters v0.6.0 produces byte-identical output to
v0.5.0, and the migration seeds exactly those defaults. It is still one Python process over one
SQLite file, with zero new runtime dependencies.

### ⚠ One breaking change: the legacy `OPTICORR_*` environment aliases are removed

Deprecated since v0.4.0, warned once per variable through v0.5.0, and **removed now** as promised
(DECISIONS #34, #39, #45). Setting **any** `OPTICORR_*` variable is a **hard startup error** that
names each offending variable and its replacement.

**One-time action, before you upgrade:** rename every `OPTICORR_*` variable to `NETCORENOC_*` and
unset the old one. The mapping is mechanical — the prefix, nothing else:

| Removed | Use |
|---|---|
| `OPTICORR_DB` | `NETCORENOC_DB` |
| `OPTICORR_TRAP_HOST` / `OPTICORR_TRAP_PORT` | `NETCORENOC_TRAP_HOST` / `NETCORENOC_TRAP_PORT` |
| `OPTICORR_HTTP_HOST` / `OPTICORR_HTTP_PORT` | `NETCORENOC_HTTP_HOST` / `NETCORENOC_HTTP_PORT` |
| `OPTICORR_ALLOWLIST` | `NETCORENOC_ALLOWLIST` |
| `OPTICORR_RETENTION_DAYS` | `NETCORENOC_RETENTION_DAYS` |
| `OPTICORR_AUDIT_RETENTION_DAYS` | `NETCORENOC_AUDIT_RETENTION_DAYS` |
| `OPTICORR_TLS_CERT` / `OPTICORR_TLS_KEY` | `NETCORENOC_TLS_CERT` / `NETCORENOC_TLS_KEY` |
| `OPTICORR_LOG_JSON` | `NETCORENOC_LOG_JSON` |
| `OPTICORR_API_TOKEN` | *(nothing — the shared token was removed in v0.3.0; issue a service token)* |

Check with `env | grep OPTICORR_`. The `netcorenoc` audit CLI refuses for the same reason: reading
the wrong database would give a confidently wrong answer about the audit chain's integrity.

**Why an error rather than silently ignoring them.** A removed knob that quietly no-ops is a
*security* regression, not a nuisance: an operator still setting `OPTICORR_ALLOWLIST` would
believe trap sources were filtered while **every source was being accepted**. Failing at startup,
naming the replacement, turns that into a five-second fix.

### What else changes for you

- **One schema migration, `0005_scorer_config.sql` (`user_version` 4 → 5)**, applied
  automatically at startup, forward-only and additive. It adds the immutable `scorer_config`
  history table, a one-row `scorer_active` pointer, and a nullable `situation.scorer_config_id`
  provenance column. It **seeds the coded defaults and marks them active**, and backfills every
  existing situation to that row — a truthful record, because those situations really were formed
  by those parameters. Your data, learned state, sessions, tokens, and audit chain are untouched.
- **Nothing else changes by default.** The defaults are unchanged, the API responses are
  unchanged (the `link` objects gain an additive `terms` list alongside the existing
  `term_t`/`term_a`/`term_e`), and the UI gains one admin-only **Scorer** tab that most operators
  will never open.
- **New capabilities**: `scorer.read` (viewer+), `scorer.preview` and `scorer.write` (admin only,
  no editor delegation). New audit actions: `scorer.config.update`, `scorer.preview`,
  `scorer.fallback`.

### The upgrade

1. Back up the database (copy the file, or `sqlite3 netcorenoc.db ".backup backup.db"`).
2. **Rename any `OPTICORR_*` variables** (table above) and unset the old names.
3. Install v0.6.0 into the same environment (`pip install .`), or `docker compose up --build`,
   pointing `NETCORENOC_DB` at the existing database.
4. Start the process. Migration `0005` applies automatically. Verify with
   `python -m netcorenoc audit verify` — the chain still verifies across the upgrade.

### Rollback

Restore the pre-upgrade backup and reinstall v0.5.0. A v0.5.0 binary will not read a
`user_version=5` database's new tables, but it ignores them; the safe path is the backup.

*Rolling back a **scoring configuration** is a different, much cheaper thing: it is one click in
the Scorer tab (or `POST /api/scorer/rollback`), it moves a pointer, and it never edits history.*

## v0.4.0 → v0.5.0 (organization/structure — no behaviour change)

v0.5.0 makes the project legible, installable, and contributable. **Nothing in the running
correlator changes** — not ingestion, learning, the API contract, the schema, or the UI
behaviour. It is still one Python process over one SQLite file.

### What changes for you

- **Nothing operationally.** No schema migration, no config change, no data change. A live v0.4.0
  database is read as-is; the audit chain still verifies across the upgrade
  (`python -m netcorenoc audit verify`).
- **The import path is unchanged** (`netcorenoc`), even though the source now lives under `src/`
  (the PyPA layout). `pip install .` / `pip install netcorenoc-0.5.0-*.whl` and
  `python -m netcorenoc.main` work exactly as before.
- **Environment variables are unchanged.** All `NETCORENOC_*` names, and the legacy `OPTICORR_*`
  aliases, still work. The alias removal that v0.4.0 scheduled for v0.5.0 has been **extended one
  version to v0.6.0** (DECISIONS #39) — you have another release to rename them; they still emit a
  one-time deprecation warning each. *(That removal has since happened — if you are upgrading past
  v0.5.0, read the v0.5.0 → v0.6.0 section above first.)*
- **The easiest way to run it is now `docker compose up`.** The bundled `docker-compose.yml`
  expresses the hardened run declaratively; copy `.env.example` to `.env` for any configuration.

### The upgrade

1. Back up the database (copy the file, or `sqlite3 netcorenoc.db ".backup backup.db"`).
2. Install v0.5.0 into the same environment (`pip install .`), or `docker compose up --build`,
   pointing `NETCORENOC_DB` at the existing database.
3. Start the process. No migration runs (there is no schema change); learned state, sessions,
   tokens, and the audit chain are untouched.

### Rollback

Reinstall v0.4.0 and restore the backup if you wish — but nothing in v0.5.0 writes an
incompatible on-disk format, so a v0.4.0 binary reads a v0.5.0-touched database unchanged.

## v0.3.0 → v0.4.0 (the rebrand)

v0.4.0 renames the project from *OptiCorr* to **NetCoreNOC** and hardens security and
reliability. **No schema change is required by the rebrand itself** (the audit chain, learned
matrices, promoted entities, sessions, and tokens all survive untouched), and it is still one
Python process over one SQLite file.

### What the rename changes for you

- **Environment variables** move from `OPTICORR_*` to `NETCORENOC_*`. The legacy `OPTICORR_*`
  names are still honoured and emit a single startup deprecation warning each (naming the
  variable, never its value). The v0.4.0 removal target was **extended by one version**: they are
  now **removed in v0.6.0** (DECISIONS #39 — v0.5.0 is an organization/structure release and
  removing them here would be an unrelated breaking change) — rename them at your convenience
  before then. `NETCORENOC_*` wins if both are set.
- **The session cookie** is renamed `opticorr_session` → `netcorenoc_session`. This invalidates
  live sessions exactly once: **every operator re-logs-in after the upgrade.** No data is lost.
- **The CSRF header** (browser UI only) changed `X-OptiCorr-Client` → `X-NetCoreNOC-Client`. The
  bundled UI already sends the new header; a *custom* browser client that forged the old header
  must update it. Service-token API clients (`Authorization: Bearer …`) are unaffected.
- **The import package** is `netcorenoc` and the module entry points are `python -m netcorenoc`
  (audit CLI) and `python -m netcorenoc.main` (the server).

### The upgrade

1. Back up the database (copy the file; WAL is checkpointed on clean shutdown, or use
   `sqlite3 netcorenoc.db ".backup backup.db"`).
2. Install v0.4.0 into the same environment (`pip install .`), pointing `NETCORENOC_DB`
   (or the still-honoured `OPTICORR_DB`) at the existing database.
3. Start the process. No migration is required for the rebrand; if you also adopt any optional
   v0.4.0 schema change it applies forward-only and idempotently at startup. Learned state
   survives and `python -m netcorenoc audit verify` still reports the chain OK across the upgrade.
4. Tell operators to sign in again (the cookie rename logged them out once).

### Rollback

Restore the pre-upgrade backup and reinstall v0.3.0. Nothing in the rebrand writes an
incompatible on-disk format.

## v0.2.0 → v0.3.0

v0.3.0 adds the **learned entity model**: NetCoreNOC now learns *what* is alarmed (the ONU, the
port, the camera) from the trap varbinds, not just *who* reported it. The upgrade is **in
place and forward-only**: migrations `0003_entity.sql` and `0004_state_clear.sql` add tables
and columns without touching your data, and it is still one Python process over one SQLite file.

### Before you upgrade

- Back up `netcorenoc.db` (copy the file; WAL is checkpointed on clean shutdown).
- **Remove `OPTICORR_API_TOKEN` from the environment.** It was deprecated in v0.2.0 and is
  removed in v0.3.0: starting v0.3.0 with it still set is a **hard startup error** that names
  the migration path. Move every API client to a named **service token** (admin → Tokens in
  the UI, or the tokens API) before upgrading.

### The upgrade

1. Install v0.3.0 into the same environment (`pip install .`), pointing `OPTICORR_DB` at the
   existing database.
2. Start the process. The schema migrates automatically (`PRAGMA user_version` 2 → 4): each
   device becomes a reporting element (`ne`) plus one level-0 `entity`, every existing alarm is
   attributed to its NE's level-0 entity, and the profiler/ingest-gap/state-clear tables are
   created empty. Learned matrices, sessions, tokens, and the audit chain survive untouched —
   the audit hash chain still verifies across the upgrade.

### What an operator should expect to observe

- **Day 0 is identical to v0.2.0.** Every NE begins undivided (one entity), so grouping and
  root-cause output are byte-for-byte what v0.2.0 produced. Intelligence is additive and
  earned, never a different day-one behaviour.
- **Over the first hours to days, entities are promoted.** As a proxying NE (an OLT, a
  chassis, an NVR) reports the same identifier across different alarm classes, the profiler
  accumulates evidence and — once it crosses the promotion thresholds — subdivides that NE
  into its real entities (ONUs, ports, cameras). Promotion is forward-only: historical alarms
  are never reinterpreted.
- **You can see why.** For every promoted entity the UI and API show the varbind OID that
  identified it (`key_source`) and the score that promoted it (`confidence`). If the profiler
  ever picks the wrong discriminator, an admin can reset an NE's learned entity key (audited).
- **Dropped traps now leave a trace.** If ingestion ever sheds load (queue full or window
  overflow), an `ingest_gap` record appears in `/api/stats` and the UI: "events lost between
  t1 and t2" is now first-class information.
- **SNMPv1 devices are no longer invisible.** v1 traps (much access equipment, most cameras)
  are mapped in per RFC 3584 and correlated like any other trap — no configuration needed.

### Rollback

Restore the pre-upgrade `netcorenoc.db` backup. The v0.3.0 tables and columns are additive and
harmless to a v0.2.0 binary, but the clean path is the backup.

## v0.1.0 → v0.2.0

v0.2.0 adds identity, role-based authorization, and a tamper-evident audit log. The
upgrade is **in place and forward-only**: the schema migration adds tables without
touching v0.1.0 data, and the process is still one Python process over one SQLite file.

### Before you upgrade

- Stop the v0.1.0 process (or upgrade during a maintenance window; the trap listener is
  briefly down while the process restarts).
- Back up `netcorenoc.db` (copy the file; WAL is checkpointed on clean shutdown).

### The upgrade

1. Install v0.2.0 into the same environment (`pip install .`), pointing `OPTICORR_DB` at
   the existing database file.
2. Start the process. On first start the schema migrates automatically
   (`PRAGMA user_version` 1 → 2; migration `0002_auth_audit.sql`), all v0.1.0 rows intact.
3. **Bootstrap admin.** Because the `user` table is empty, NetCoreNOC creates an `admin`
   account and prints a random 20-character password **once** to the console inside a
   banner. Copy it now — it is never printed again.
4. Open the UI, log in as `admin`, and set a new password when prompted (the account
   starts with `must_change_password`; the app is locked to login + password change until
   you do). Create the users and roles your operators need.

### What changes for operators and clients

- **The UI now requires login.** The old `localStorage` API-token box is gone.
- **API clients** move from the single shared token to **service tokens**: an admin
  creates a named token with a role in the UI (or the tokens API), and the value is shown
  once. Send it as `Authorization: Bearer <value>`.
- **Roles**: viewer (read + live stream), editor (+ feedback, rename, close), admin
  (+ user/token/config management, quarantine, audit).

### Legacy token deprecation timeline

`OPTICORR_API_TOKEN` still works in v0.2.0 for backward compatibility: it is accepted as a
synthetic admin identity `legacy-token`. On startup NetCoreNOC logs a deprecation warning
naming the variable (never its value) and writes one `legacy_token.used` audit event the
first time it is used. **`OPTICORR_API_TOKEN` is removed in v0.3.0** — migrate every
client to a named service token before upgrading past v0.2.x.

### TLS

v0.2.0 can terminate TLS itself: set `OPTICORR_TLS_CERT` and `OPTICORR_TLS_KEY` (the
session cookie then gains `Secure` automatically). A reverse proxy terminating TLS is the
documented alternative — see `SECURITY.md`.

### Rollback

If you must roll back to v0.1.0, restore the pre-upgrade `netcorenoc.db` backup. The v0.2.0
tables are additive and harmless to a v0.1.0 binary (which ignores them), but v0.1.0
cannot read audit history, so restoring the backup is the clean path.

### Audit log operations

- Verify integrity: `python -m netcorenoc audit verify` (walks the hash chain, reports the
  first broken link).
- Export: `python -m netcorenoc audit export > audit-YYYYMMDD.ndjson` (NDJSON + final chain
  hash on stderr).
- Retention: audit rows are kept for `OPTICORR_AUDIT_RETENTION_DAYS` (default 365) and are
  never touched by the ordinary prune; only an explicit, audited admin action removes
  them.
