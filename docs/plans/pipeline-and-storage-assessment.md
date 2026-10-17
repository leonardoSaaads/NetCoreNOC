# Trap pipeline and storage: assessment against industry practice

Status: implemented on branch `claude-code/dreamy-albattani-t41a5e` (ADRs #450, #451), measured on
a 4 vCPU, 2.1 GHz sandbox. The audit that preceded it is
[`correlation-kpi-audit.md`](correlation-kpi-audit.md).

## 1. The questions

1. Is there a queue that guarantees no accepted trap is lost, however slow classification is, and
   that absorbs high volume and bursts?
2. Is a trap that misses the KPI kept, and is there a metric for how many miss it, against a KPI
   an administrator can change?
3. Do the queue, its consumption and the processing pipeline follow industry practice (Zabbix)?
4. Are the database and the queue/processing design appropriate for large enterprises and future
   proxies?

## 2. What the pipeline is now

```
UDP 162 ─▶ receiver process ─▶ spool/ (raw datagrams, CRC, fsync per group)
           ingest/worker.py      ingest/spool.py
                                    │  read after the cursor
                                    ▼
           engine intake ⇄ decoder process ─▶ engine queue (10 000, backpressure)
           engine/operate/intake.py   ingest/decoder.py
                                    ▼
           batch ─▶ SQLite transaction: classification + spool cursor (one commit)
           engine/operate/engine.py     store/  (meta row `spool.cursor`)
```

| Stage | Process | Responsibility | Failure outcome |
|---|---|---|---|
| Receive | `python -m netcorenoc.ingest.worker` | read socket, kernel arrival stamp, allowlist, append raw | dies: restarted on the same socket; loses at most the few ms not yet written |
| Spool | files in `<db dir>/spool/` (0700/0600) | durable FIFO, segments of 64 MiB, `spool.lock` | disk full or `NETCORENOC_SPOOL_MAX_MB`: held in memory, then counted as dropped, warning, `/readyz` 503 |
| Decode | `python -m netcorenoc.ingest.decoder` | BER, SNMPv3, SNMP policy, quarantine | dies or hangs: restarted, batch replayed one record at a time, the culprit quarantined |
| Classify | main process | correlation, situation selection | storage error: rollback and retry; crash: replay from the cursor, poison record to `dead-letter.jsonl` |
| Commit | SQLite (WAL) | classification and cursor in one transaction | a restart resumes after the last classified trap: nothing skipped, nothing twice |

## 3. Assessment

### 3.1 Queue and durability — now aligned with practice

| Practice | Reference | Here |
|---|---|---|
| Persist before processing | Zabbix: `snmptrapd` writes a trap file, the SNMP trapper parses it | raw spool before any parsing (`ingest/worker.py`) |
| Consumer offset stored with the results | Zabbix `snmp_lastsize`; Kafka committed offset | `spool.cursor` committed in the batch transaction |
| Segmented append-only log, CRC, retention by offset | Kafka log segments | `ingest/spool.py`; segments deleted after a WAL checkpoint |
| Group commit | databases, Kafka `linger.ms` | one `fdatasync` per up to 1 024 records |
| Backpressure, not loss | reactive streams | `await queue.put`; the spool is the buffer |
| Dead-letter queue | brokers (RabbitMQ, SQS, Kafka DLT) | `dead-letter.jsonl`, exact bytes, replayable |
| Crash isolation | Erlang supervision | supervised engine task, rewind, one-at-a-time replay |
| Readiness ≠ saturation | Kubernetes probes | `/readyz` fails only when the spool refuses writes |

**Measured** (`make storm`, 20 000 distinct alarms):

| Run | Kernel buffer | Kept and classified | Kernel drops | Other losses | Restarted mid-storm |
|---|---|---:|---:|---:|---|
| decode before spool, 5 000/s | 8 MiB | 15 762 | 4 238 | 0 | no |
| raw spool, 5 000/s | 8 MiB | **20 000** | **0** | 0 | no |
| raw spool, all at once (54 ms) | 8 MiB | 17 656 | 2 344 | 0 | no |
| raw spool, all at once (54 ms) | 32 MiB | **20 000** | **0** | 0 | **yes, no duplicate** |

The only loss left is an instantaneous burst larger than the kernel's socket buffer itself; raise
`net.core.rmem_max` to 32 MiB (`docs/configure.md`).

### 3.2 KPI and observability — aligned with Zabbix's queue model

Every trap is timed from its kernel arrival to the commit that placed it. A target (60 s) and a
limit (120 s) are counted, never enforced by discarding; an admin changes both on **Settings**
without a restart (`POST /api/config`, audited). Published live in `GET /api/stats` →
`classification` and recorded every 30 s per interval (`host_sample`, migration 0030): throughput
in and out, backlog peak, oldest wait, p50/p95/p99, worst, counts past target and limit, Zabbix's
delay buckets, spool size. The Overview's **Trap pipeline** panel draws them for any range.

### 3.3 Classification throughput — the remaining gap

The KPI is **not** met on this host for a 20 000-alarm storm: classification runs at about
140-180 traps/s, and the storm needs about 334/s to place every trap within 60 s. In the runs
above the median trap waited 115-130 s and 2 000-3 200 traps passed the 120 s limit. None was lost;
all are counted. The options are the four in the audit's §9 (reference hardware, offloading the
observe-only work, a storm mode, partitioned correlation); each is a decision for the owner.

### 3.4 Database — adequate for one appliance, not for an enterprise with proxies

SQLite in WAL mode with one writer is a sound choice for a single appliance: one file, crash-safe,
no administration, and it sustains this workload. Its limits are structural, not tuning:

| Limit | Consequence at enterprise scale |
|---|---|
| One writer, one connection, one `asyncio.Lock` shared by the engine and the API | console writes and classification serialise; a long API read delays a batch |
| No replication or failover | one host is the availability of the whole service |
| No concurrent multi-process writers | correlation cannot be partitioned across processes or hosts |
| Time series in an OLTP table (`host_sample`, alarm history) | long ranges are scans; retention is row deletion |

**Recommendation**, as a separate project with its own ADR and migration plan:

1. **PostgreSQL** as the system of record for the central server: concurrent writers (partitioned
   correlation), streaming replication and failover, PITR backups, row-level security if tenants
   are added. Keep the store's mixin interface; port it behind it.
2. **TimescaleDB** (or native partitioning by time) for alarms history and pipeline/host samples:
   compression, retention by dropping partitions, continuous aggregates for the Overview's ranges.
3. **Keep SQLite on proxies**, where it remains the right tool: one site, no administrator.

### 3.5 Proxies — the spool is the transport

The Zabbix proxy model fits directly: a proxy receives and spools locally, and forwards to the
central server, surviving a WAN outage on disk. The spool already has what forwarding needs —
segments, sequence numbers, CRCs, a cursor — so a proxy is the receiver plus a forwarder:

1. proxy: `ingest/worker.py` unchanged, spool on the proxy's disk;
2. forwarder: ships records after its cursor over **mutually authenticated TLS** (per-proxy
   certificate), batched and compressed, and advances its cursor on the server's acknowledgement;
3. server: an ingest endpoint appends to its own spool, keyed by `(proxy id, sequence)` so a
   retransmission is idempotent; the proxy id and arrival stamp travel with each record so the KPI
   still runs from the trap's own arrival;
4. observability per proxy: backlog, oldest unsent, link state — the same panel, one row per proxy.

Volume then scales with the number of proxies at the edge; the central bottleneck is
classification, which is why partitioned correlation (3.3, option 4) and PostgreSQL (3.4) belong
to the same roadmap step.

## 4. Priorities

| Priority | Item | Reason |
|---|---|---|
| 1 | Measure `make storm` on the reference host; set `net.core.rmem_max` | sizes the real gap before any further change |
| 2 | Choose a throughput option (audit §9) | the only open KPI gap |
| 3 | PostgreSQL + TimescaleDB for the central server | prerequisite for partitioning, HA and proxies |
| 4 | Proxy forwarder over mTLS on the existing spool format | volume growth at the edge |
