# Storage portability — draft

**Status: `future`. This document specifies and implements nothing.** No dialect layer, no
abstraction, no dependency, no migration, no schema change. Building any of it from this document is
a build failure.

**Direction of travel, recorded as a preference rather than a decision**: PostgreSQL becomes the
larger-focus backend; **SQLite is not being removed**; and **this is a future effort, not a scheduled
one**. The document's job is to make the constraints legible now, so that whoever schedules it is not
surprised by them.

---

## 1. The coupling, measured rather than estimated

```
$ for p in RETURNING PRAGMA AUTOINCREMENT rowid user_version; do
    echo "$p: $(grep -rhoiw "$p" --include='*.py' --include='*.sql' src/netcorenoc | wc -l)"; done
$ grep -rhoiE 'INSERT OR (IGNORE|REPLACE)' --include='*.py' --include='*.sql' src/netcorenoc | wc -l
```

| construct | count | portability |
|---|---:|---|
| `RETURNING` | **43** | **portable** — PostgreSQL has had it since 8.2 and it is the older of the two |
| `PRAGMA` | **28** | SQLite-only. 10 of them are in `store/lifecycle.py` (journal mode, foreign keys, synchronous, integrity checks) |
| `AUTOINCREMENT` | **21** | spelling only — `GENERATED … AS IDENTITY` or `BIGSERIAL` |
| `INSERT OR IGNORE` / `OR REPLACE` | **6** | `ON CONFLICT DO NOTHING` / `DO UPDATE`; both dialects have the standard form |
| `rowid` | **2** | SQLite-only, and both are incidental |
| `user_version` | **6** | **the schema-version mechanism itself** — a `PRAGMA`, so the migration runner's own state has no PostgreSQL equivalent and needs a table |

**The syntax is the easy part.** The measurement that matters:

```
$ python - <<'PY'   # AST over src/netcorenoc/store/*.py
methods on Store mixins total: 148
methods carrying SQL: 142
files carrying SQL: 19
PY
```

**142 `Store` methods carry SQL across 19 files, with no dialect layer anywhere.** (The build brief
estimated 144 across 12; the count above is what the tree measures, by AST over the mixin classes
with docstrings excluded. The discrepancy is in how files are counted — `store/` holds 21 modules —
and the number that matters is the same either way.)

Every one of those methods is a hand-written statement executed against a shared connection. There is
no query builder to retarget and no repository interface to reimplement: **porting means touching
142 methods, and the risk is not that a statement fails to parse — it is that it parses and means
something slightly different.**

---

## 2. Zero-config is a property of packaging, not of the database

The temptation is to read *"PostgreSQL support"* as *"the product now scales"* and to treat
principle 1 as a casualty. Both halves are wrong.

**Zabbix is the worked example.** The appliance provisions the database, creates the schema and
writes the credentials; the same product installed by hand requires the operator to do all three.
Same software, same schema, and one of the two is plug-and-play. **The difference is entirely in the
packaging.**

So:

> **No PostgreSQL release makes PostgreSQL plug-and-play.** A container that ships a database beside
> the application can be zero-config; a `pip install` never can be, because credentials and a
> reachable server are prerequisites no software can conjure.

**On PostgreSQL 19**, in beta as of mid-2026 with GA expected late in the year: its headline features
help whoever *operates* a database, not whoever wants not to know one exists. `REPACK`, parallel
autovacuum and `pg_plan_advice` are administration; SQL/PGQ is a query language for graphs this
project does not have.

**`ON CONFLICT DO SELECT` is worth recording and is not a dependency.** It would simplify several of
the six upsert sites — the `INSERT OR IGNORE`-then-`SELECT` pattern becomes one statement. Record it
as a **simplification available once it is GA**, never as something the design assumes: binding the
architecture to a beta binds this project's schedule to someone else's, and a release that cannot
ship until another project's GA slips is a release with a dependency nobody agreed to.

---

## 3. The access model — the real product gain

Today **the only way to reach data is the API**, and that is the most closed axis the product has. A
NOC that cannot point Grafana at its own alarm history is doing something no other tool in its
category does.

PostgreSQL opens that *safely* if one asymmetry is respected:

> **Reads open, writes governed.**

| | mechanism | who |
|---|---|---|
| **schema ownership** | the application's own role owns every object | the application |
| **writes** | through the API only, with RBAC, scoping and audit | `EDITOR`, `ADMIN` via HTTP |
| **reads** | a **read-only role over views**, never over tables | `ADMIN`, `EDITOR` |

Views rather than tables, and the distinction is not cosmetic:

* a view can **omit** a column, which is how a read-only consumer sees alarm history without seeing
  `holdout_seal_member`;
* a view is a **stable contract** where a table is an implementation detail — twelve migrations of
  evidence say the tables move;
* a view can **carry the scope predicate**, which is the only way a direct reader can be scoped at
  all.

That unlocks Grafana, Metabase, `psql`, spreadsheet export and BI — **most of what anyone means by
"let me at the database"** — while writes stay behind the API, **because that is where a change
becomes explainable.** An `UPDATE` at a `psql` prompt has no actor, no reason, no audit row and no
before-image. The product's central proposition is that an operator can see *why*; a write path that
produces no explanation is that proposition switched off.

---

## 4. What direct write access would destroy — four named invariants

Not offered as caution. These are the four things that stop being true the moment anything but the
application can write, and one of them stops being true the moment anything but the application can
*read*.

1. **The seal's query count stops being structural.** v0.10.0's entire deliverable is that
   `holdout_seal_member` has exactly **one** reader in the package, asserted by AST, so *"query
   count 0"* is a fact about the holdout rather than about one code path. A read-only role that can
   `SELECT` that table makes the count a statement about the application only — and the holdout can
   be spent by a curious analyst with no row appended to `holdout_access` and nothing to notice.
   **This is a READ, not a write**, which is why it is first: the read-only role §3 recommends is
   itself the threat, and the mitigation is §5.

2. **The hash-chained audit breaks — or, worse, does not.** A direct `INSERT` into `audit_event`
   that computes the chain hash correctly produces a **valid chain with an invented actor**.
   `make audit-verify` passes. That is strictly worse than a broken chain, because a broken chain is
   detected and a forged one is believed.

3. **`excluded_reconciled` becomes plantable — F46 through the back door.** The whole evidence
   boundary rests on tier 2 being *derived by the server at the instant of the verdict*. A writable
   `feedback.excluded_reconciled` restores exactly the property F46 was issued for: a number
   describing the evidence, produced by something that is not the evidence. The `CHECK` constraint
   bounds it into `[0, member_count]` and F46's measured attack produced **+900**, comfortably
   inside.

4. **The byte-identical `eval` presumes the application is the only writer.** `make eval` has
   produced the same hash since v0.7.0 and is this project's oldest regression gate. It is a gate
   because the corpus is deterministic; a second writer makes it a measurement of the database's
   history instead.

### The mitigation for (1), which is the one that changes the schema shape

**Under PostgreSQL the seal wants its own schema, invisible even to the read-only role.**

```
REVOKE ALL ON SCHEMA seal FROM PUBLIC;      -- illustrative, not a specification
GRANT  USAGE ON SCHEMA seal TO netcorenoc_app;
```

The guarantee then survives with a **database-permission proof added beside the AST proof** — two
independent mechanisms for one property, which is what the property's importance justifies. The AST
guard says *no second reader exists in the package*; the grant says *no second reader can exist at
all*. Under SQLite only the first is available, and that is a real difference between the backends
rather than a portability detail.

---

## 5. The constitutional conflict, raised and **not resolved**

> Principle 6: **one runtime identity.** One process, one SQLite, four static UI files, env vars
> only.

*"One connection, one lock"* is **correct for SQLite/WAL** — it is why the store has a single
connection and an `asyncio.Lock`, and why `tests/test_store_concurrency.py` guards it. It is the
**wrong shape for PostgreSQL**, where connection pooling and real concurrency are the point and a
single serialised connection would deliver worse throughput than SQLite while requiring an operator
to run a database.

**Principle 6 therefore has to be reopened deliberately, in its own ADR, by whoever schedules the
work.** Not amended in passing, and not here. A principle that erodes through implementation
convenience was never a principle; the project's own rule is that mechanism is configurable and the
standard of evidence is not, and *"one runtime identity"* is closer to the second than it looks.

**Do not reopen it in this document.**

---

## 6. Two backends or one — the question, with the consequence of each

| | one (PostgreSQL only) | two (both supported) |
|---|---|---|
| runtime identity | **preserved** — one process, one backend, one story | two configurations, two failure modes, two sets of operator documentation |
| zero-config for small installs | **lost** unless packaged as an appliance (§2) | **preserved** — SQLite stays the default and `pip install` still works |
| test matrix | unchanged in size | **doubled, permanently**, and every future release pays it |
| the `eval` hash | unchanged | **the open question** — see below |
| migrations | one path | every migration written twice, or written in a subset both accept |

**The `eval` hash is the sharpest edge and deserves its own sentence.** `make eval` has run against
the same engine since v0.7.0. Under two backends, either it runs against SQLite only — and stops
being evidence about the PostgreSQL build, which would then have this project's oldest gate not
covering it — or it must produce the same bytes on both, which is a much stronger claim about
floating-point aggregation, sort stability and collation than either database offers by default.

**Record the question. Do not answer it.**

---

## 7. What this document deliberately does not answer

* **One backend or two.** §6, and it is the decision everything else follows from.
* **Whether principle 6 survives.** §5. Its own ADR, by whoever schedules the work.
* **What the dialect seam looks like** — a layer, a subclass per backend, or 142 methods written in
  a portable subset. Naming a seam is designing one, and this document is not a design.
* **Whether the read-only role is scoped.** §3 says a view *can* carry the scope predicate. Whether a
  direct reader is scoped **at all** is a policy question with an RBAC model behind it, and the
  honest default — a direct reader sees everything the view exposes — may simply be unacceptable.
* **Whether `user_version` becomes a table or the migration runner is rewritten.** Both are small;
  the choice interacts with §6.
* **When.** *Future*, not *scheduled*, and this document deliberately does not argue for a date.
