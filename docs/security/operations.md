# NetCoreNOC Security & Operations Guide

This guide is for the operator **deploying and running** NetCoreNOC. If you have found a
vulnerability, do not use this document — follow the coordinated disclosure policy in the root
[`SECURITY.md`](../../SECURITY.md).

NetCoreNOC provides identity, role-based authorization, and a tamper-evident audit log (since
v0.2.0), served over one process with one SQLite file and one static UI.

## Recommended deployment

1. **Bind the trap listener to the management interface** and **set an allowlist**:
   ```sh
   NETCORENOC_ALLOWLIST=10.20.0.0/16,192.0.2.10 \
   NETCORENOC_TRAP_HOST=10.20.0.5 NETCORENOC_HTTP_HOST=10.20.0.5 \
   python -m netcorenoc.main
   ```
   With no allowlist every source is accepted (zero-config default) and NetCoreNOC shows a
   persistent banner to admins until you set one.
2. **Terminate TLS.** Either give NetCoreNOC a certificate directly, or front it with a
   reverse proxy (below). Without TLS on a non-loopback bind, NetCoreNOC warns admins.
3. **Complete the bootstrap.** On first start NetCoreNOC prints a one-time `admin` password
   to the console inside a banner. Sign in, change it immediately (you are forced to), and
   create per-operator accounts with the least role each needs.
4. **Issue service tokens, not shared secrets.** For scripts and integrations, create a
   named service token (admin → Tokens) with the minimum role. The value is shown once.

The quickest hardened start is `docker compose up` — see the root [`README.md`](../../README.md)
and `docker-compose.yml` at the repo root.

### Roles

| Role | Can do |
|------|--------|
| **viewer** | Read situations, graph, timeline, stats, classes; receive the live stream |
| **editor** | viewer + confirm/split feedback, rename devices/classes, close/ack situations |
| **admin** | editor + manage users/tokens, change runtime config, read quarantine, read/export/prune the audit log |

Authorization is deny-by-default and enforced from a single map
(`src/netcorenoc/rbac.py`); viewers never see mutating controls in the UI.

## TLS options

**Built-in** (simplest):
```sh
NETCORENOC_TLS_CERT=/etc/netcorenoc/tls.crt NETCORENOC_TLS_KEY=/etc/netcorenoc/tls.key \
python -m netcorenoc.main
```
The session cookie automatically gains the `Secure` flag when TLS is enabled.

**Reverse proxy** (e.g. nginx/Caddy terminating TLS): proxy `/` to NetCoreNOC's HTTP port on
loopback. Because the cookie is `SameSite=Strict` and CSRF also checks `Origin`/`Host`,
make sure the proxy preserves the `Host` header and forwards the browser `Origin`. If the
proxy terminates TLS, NetCoreNOC's own listener may stay plain HTTP on loopback (no warning
is shown for a loopback bind).

## Sessions, passwords, throttling

- Passwords are `scrypt` (n=2¹⁷) hashes; policy is length-only (12–128 chars, no
  composition rules, no forced expiry) per NIST SP 800-63B.
- Sessions are server-side; the cookie holds a random id whose **SHA-256 is stored**, so a
  stolen database yields no live session. Idle timeout is 30 min (sliding); absolute is
  12 h. Logout, password change, and role change revoke sessions.
- Login is throttled per username **and** per source IP with exponential backoff after 5
  failures (to a 15-minute cap). Unknown-user and wrong-password are indistinguishable.

## The audit log

Every mutating action and every sensitive read (including denied attempts) writes one
append-only, hash-chained `audit_log` row. History is immutable even to the application
(SQLite `BEFORE UPDATE`/`BEFORE DELETE` triggers).

- **Verify integrity** (run this periodically, e.g. from cron):
  ```sh
  python -m netcorenoc audit verify      # or: make audit-verify
  ```
  It walks the chain and reports the first broken link, if any.
- **Back up / export**:
  ```sh
  python -m netcorenoc audit export > audit-$(date +%F).ndjson
  ```
  Emits one JSON row per line plus the final chain hash on stderr. Keep exports off-box so
  the audit trail survives loss of the node.
- **Retention**: audit rows are excluded from the ordinary prune and kept for
  `NETCORENOC_AUDIT_RETENTION_DAYS` (default 365). Only an explicit admin action removes old
  rows, and that prune is itself audited. Pruning removes only the oldest rows, so the
  surviving suffix stays verifiable against an archived boundary hash.

## What the banners mean

The admin UI shows a persistent warning when a deployment default is risky:

- **"Trap allowlist is empty"** — every source IP is accepted. Set `NETCORENOC_ALLOWLIST`
  (or configure it under admin → Config) to the CIDRs your equipment sends from.
- **"HTTP is not using TLS on a non-loopback bind"** — credentials could travel cleartext.
  Enable built-in TLS or a TLS reverse proxy.

## Removed configuration knobs (both now fail loudly)

- The shared `NETCORENOC_API_TOKEN` was **removed in v0.3.0**: setting it is a hard startup error
  naming the migration path. Migrate every client to a named service token.
- The legacy `OPTICORR_*` environment-variable **names** were **removed in v0.6.0** as promised
  (see [`../adr/DECISIONS.md`](../adr/DECISIONS.md) #39 and #45). Setting any of them is a hard
  startup error naming each variable and its `NETCORENOC_*` replacement; the mapping table is in
  [`../../MIGRATION.md`](../../MIGRATION.md). Check with `env | grep OPTICORR_` before upgrading.

Both fail at startup rather than being ignored, and that is a security decision, not a style one:
a removed knob that silently no-ops is how an operator ends up believing `OPTICORR_ALLOWLIST` is
filtering their trap sources while every source is in fact accepted.

## Retuning the correlation formula (v0.6.0)

The link-score parameters are admin-configurable from the **Scorer** tab (or the `/api/scorer`
routes). Operationally:

- **You do not need to touch this.** The defaults are the documented behaviour and are what the
  evaluation corpus is scored against.
- **`scorer.read` is viewer+** (the parameters explain grouping and are not a secret);
  **`scorer.preview` and `scorer.write` are admin only**, with no delegation to editors.
- **Preview before applying.** The what-if is read-only and shows what would merge and split on
  your own recent alarms. It is directional — a bounded recent window with the learned matrices
  held fixed — not a prediction of the steady state.
- **A change takes effect at the next engine maintenance pass** (≤ 5 s), never mid-batch.
- **Every change is audited, history is immutable, and rollback is one click.** The configuration
  table is append-only and is never pruned, because a situation's provenance must outlive the
  alarms that formed it.
- **If a scorer ever fails**, the engine falls back to the built-in defaults, writes a
  `scorer.fallback` audit row, and raises a persistent operator warning in `/api/stats`.

## Data at rest

`netcorenoc.db` contains scrypt password hashes, SHA-256 session/token digests, the audit
log, learned state, and quarantined packet metadata (community strings are never stored).
Protect the file with filesystem permissions and back it up alongside your audit exports.
Run the process as a non-root user (the bundled Dockerfile already does).

## Container deployment (CIS-style hardening)

The image is multi-stage (no build tools in the final layer) and runs as a non-root user
(`netcorenoc`, uid 10001). The only path it writes is the SQLite database, so it runs fine with a
read-only root filesystem. The bundled `docker-compose.yml` (repo root) expresses the hardened
run declaratively; the equivalent `docker run`:

```
docker run --read-only --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /tmp -v netcorenoc-data:/home/netcorenoc \
  -p 162:162/udp -p 8080:8080 netcorenoc
```

- **Pin the base image by digest** for a reproducible, tamper-evident build. The `Dockerfile`
  pins a specific patch tag (`python:3.12.8-slim`); to pin the digest, run
  `docker inspect --format='{{index .RepoDigests 0}}' python:3.12.8-slim` and substitute
  `python@sha256:<digest>`.
- **Vendored assets** (d3) are integrity-pinned in `src/netcorenoc/ui/vendor/CHECKSUMS.txt` and
  checked by CI (`make checksums`); a tampered swap fails the build. The upstream licence is
  shipped beside the asset (`src/netcorenoc/ui/vendor/d3.LICENSE`).
- **Back up** the database with a copy-with-WAL or `sqlite3 netcorenoc.db ".backup out.db"`, and
  run `netcorenoc audit verify` periodically to confirm the audit chain is intact.

For a non-container host, a hardened example systemd unit is provided at
`deploy/netcorenoc.service` (repo root).
