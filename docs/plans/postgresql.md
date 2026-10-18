# PostgreSQL: the migration, measured

Status: implemented on branch `claude-code/dreamy-albattani-t41a5e` (ADR #452). The assessment that
asked for it is [`pipeline-and-storage-assessment.md`](pipeline-and-storage-assessment.md) §3.4.

## 1. What changed

| | SQLite (v0.30.x) | PostgreSQL (this release) |
|---|---|---|
| Driver | `aiosqlite`, one connection in a thread | `asyncpg`: one writer connection and a pool of readers |
| Console reads | on the writer, behind `store.lock` | on a reader, in a `REPEATABLE READ READ ONLY` snapshot, never behind the lock |
| Schema | 30 migrations, `TEXT`/`REAL`/`INTEGER` | one designed baseline, `0031_postgresql.sql` |
| Instants | epoch `REAL` | `timestamptz` (microseconds; the driver returns epoch seconds) |
| Flags, addresses, varbinds | `INTEGER` 0/1, `TEXT`, JSON `TEXT` | `boolean`, `inet`, `jsonb` (GIN-indexed) |
| Redundancy | `device`, a row-for-row copy of `ne`; three `device_id` columns; two indexes repeating a unique index's lead column; 24 trigger bodies | removed: `ne` is the device; one `forbid_change()` function |
| Durability | `synchronous=NORMAL`, WAL | engine batches `synchronous_commit=off` behind the spool (#450); every other commit synchronous |
| Deployment | a file in a volume | a hardened `postgres` service on an internal network, a least-privilege role, generated secrets; Supabase or any managed PostgreSQL by URL |

## 2. Method

`tools/db_bench.py` drives the store, the real engine and the HTTP API, and nothing else, so the
same workload measures both backends. For SQLite it ran unchanged against `origin/main` (v0.30.x)
with the file's size as `size`.

1. **samples**: 7 days of 30 s host samples, 120 per commit;
2. **ingest**: a storm of distinct alarms classified by the real engine and the packaged league
   while `--readers` console clients poll ten API routes with a 50 ms think time;
3. **reads**: every route 20 times on the idle, populated database;
4. **size**: tables, indexes and TOAST (PostgreSQL), or the file and its WAL (SQLite).

The host was a 4 vCPU, 2.1 GHz sandbox, with the two runs of each pair back to back.
PostgreSQL 16.15 ran with `docker-compose.yml`'s settings (`shared_buffers=256MB`, `jit=off`,
`wal_compression=on`, …) and **full durability** (`fsync`, `synchronous_commit` and
`full_page_writes` on), over a Unix socket. One round trip to it costs about 94 µs there
(`SELECT 1`), which is high for bare metal and is the unit everything below is paid in.

## 3. Results

### 3.1 A storm of 20 000 alarms with four console clients

This was the baseline's configuration.

| Metric | SQLite | PostgreSQL | Change |
|---|---:|---:|---:|
| Console latency **during the storm**, p50 | 719.6 ms | **18.5 ms** | **−97 %** |
| Console latency during the storm, p95 | 888.6 ms | **79.5 ms** | **−91 %** |
| Console requests answered during the storm | 455 | **6 764** | **×14.9** |
| Console latency at rest, p50 (20 reads × 10 routes) | 17.1 ms | **9.7 ms** | **−43 %** |
| Console latency at rest, p95 | 74.1 ms | **58.4 ms** | **−21 %** |
| Traps classified per second | 230.4 | 140.2 | −39 % (see below) |
| Size on disk | 41.8 MB | 51.9 MB | +24 % |

**The traps-per-second figure in this row is not a like-for-like comparison.** Under SQLite each
console request waited for the engine's batch, so four clients made 455 requests in the storm. Under
PostgreSQL they were answered at once and made 6 764, and every one of them is served by the same
Python process that classifies traps. §3.2 is the like-for-like figure.

### 3.2 Ingest alone: 5 000 alarms, no console

| Metric | SQLite | PostgreSQL | Change |
|---|---:|---:|---:|
| Traps classified per second | 197.7 | 182.0 | −8 % |
| Main-process CPU per 1 000 traps | 4.80 s | **4.24 s** | **−12 %** |
| Console latency at rest, p50 | 11.1 ms | **8.0 ms** | **−28 %** |
| Console latency at rest, p95 | 50.5 ms | **38.0 ms** | **−25 %** |
| Host samples written per second | 6 552 | 5 331 | −19 % |

### 3.3 Each route at rest (p50, 20 000-alarm database)

| Route | SQLite | PostgreSQL | Change |
|---|---:|---:|---:|
| `/api/entities` | 38.9 ms | **6.3 ms** | −84 % |
| `/api/stats` | 25.9 ms | **15.7 ms** | −39 % |
| `/api/inventory` | 72.4 ms | **52.5 ms** | −27 % |
| `/api/activity/top?range_s=7200` | 55.0 ms | **49.7 ms** | −10 % |
| `/api/resources?range_s=604800&buckets=48` | 22.9 ms | 22.6 ms | −1 % |
| `/api/activity/lanes?range_s=3600` | 9.5 ms | 9.7 ms | +2 % |
| `/api/notices` | 2.1 ms | 2.3 ms | +11 % |
| `/api/resources?range_s=7200&buckets=24` | 2.9 ms | 3.6 ms | +26 % |
| `/api/timeline?range_s=3600&buckets=60` | 4.8 ms | 9.7 ms | +101 % |
| `/api/situations?limit=100` | 3.2 ms | 6.5 ms | +102 % |

The heavy routes got faster, and the cheapest ones lost about a millisecond. Each request costs a
few round trips that an in-process database did not: the identity lookup, the snapshot's `BEGIN`
and `ROLLBACK`, and each statement. That cost is small against a 50 ms route and doubles a 3 ms
one. On the idle database the light routes' server time is under 1 ms, for example the situation
list's query at 0.6 ms (`pg_stat_statements`).

## 4. What the measurements changed

Each item was found by measuring, not assumed, and each is in the tree with its reason.

| Finding | Fix | Effect |
|---|---|---|
| `epoch()` was declared `IMMUTABLE` over `extract`/`date_part`, which are `STABLE`, so it was never inlined; every time-bucketed read called it once per row. | `STABLE`, and `date_part` (double precision; `extract` has been `numeric` since PostgreSQL 14). | 7-day host series: 43 → 12 ms. Timeline and activity buckets were 2–3× faster. |
| `/api/entities` ran one query per element (N+1). That was cheap in-process and a round trip each on a server. | `entities_by_ne`: one `= ANY(...)` query. | 36 → 6 ms. |
| Every request wrote its session's or token's last use: a transaction with an fsync per console poll. | The write happens at most every 30 s. Pooled connections commit without waiting for the WAL flush; losing one shortens an idle session and never lengthens it. | One fsync fewer per request. |
| Every pooled connection was reset with `RESET ALL; UNLISTEN *; CLOSE ALL` plus an advisory unlock. | A reset that only undoes a transaction a cancelled block left open. | One round trip fewer per acquire. |
| A side write always sent `BEGIN`/`COMMIT`. | Its transaction opens on the first write, as the writer's does. | Identity checks are one statement. |
| A situation's name read its membership twice. | One pass (a materialised CTE). | 9.2 → 4.6 ms per refresh on a 4 000-member storm. |

## 5. What stays slower, and why

**Classification is 8 % slower and spends 12 % less CPU.** The engine awaits about ten statements
per trap, one at a time: the dedup read, the alarm row, membership, links, the situation's update
and root, the observation, and the proposal check. In-process each was a function call. Here each
is a round trip, about 94 µs on this host before the server does anything. That wait is what the
engine's batch spends, and it is CPU the main process no longer burns. Recovering it means
batching the engine's writes per transaction instead of per trap. `engine/operate/engine.py` is
byte-pinned (`TRAP_PATH_HASHES`, DECISIONS #259), and `make eval` holds the correlator exact, so
that work belongs to its own change. Two partial steps would come first:

1. buffering `capture.record`'s observation and pairs per batch, one savepoint per batch instead
   of per trap;
2. merging `touch_situation` and `set_root`, which the engine issues back to back.

The first also keeps a large batch under PostgreSQL's 64-subtransaction cache. A batch is bounded
at 250 ms (#447), about 45 traps on this host, so it stays under that today.

**Host samples are written 19 % slower** for the same reason: one statement each. In production
that is one row every 30 s.

**The database is 22–24 % larger.** Nearly all of it is `dataset_pair`, the capture sink, at about
150 bytes a row against SQLite's 72: a 24-byte tuple header and fixed-width `bigint` keys, against
SQLite's varints. Its retention (`retention_sink_rows`, `retention_sink_days`) bounds it, and the
indexes in both directions are read by the reports and the proposal paths.

## 6. Reproducing

```sh
make pg-test-up                                   # or any PostgreSQL 15+ you may create databases on
export NETCORENOC_TEST_DATABASE_URL=postgresql://netcorenoc:netcorenoc@127.0.0.1:55432/postgres
python tools/db_bench.py --traps 20000 --readers 4 --json pg.json
python tools/db_bench.py --traps 5000 --readers 0 --json pg-ingest.json
```

Compare runs made on the same host. `docker-compose.test.yml` turns durability off, which flatters
every write; the figures above had it on.
