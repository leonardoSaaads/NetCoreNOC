# NetCoreNOC

**Zero-configuration alarm correlation for network operations.** Point your equipment at
NetCoreNOC as an SNMP trap destination — that's the whole setup. From the raw trap stream
alone (no MIBs, no mappings, no inventory, no topology files) it discovers devices,
learns a living topology graph from alarm co-occurrence, groups related alarms into
situations, and hints at the probable root cause. As of v0.3.0 it also learns *what* is
alarmed — subdividing a device into the ONU, port, or camera the trap is really about, and
learning a severity and a state-clear field where the evidence supports it. It improves
continuously from its own stream and from one-click operator feedback.

One Python process. One SQLite file. One web UI.

![CI](https://github.com/leonardoSaaads/NetCoreNOC/actions/workflows/ci.yml/badge.svg)

## Quickstart (5 minutes)

### Docker

```sh
docker build -t netcorenoc .
docker run -d --name netcorenoc \
  -p 162:162/udp -p 8080:8080 \
  -v netcorenoc-data:/home/netcorenoc \
  netcorenoc
docker logs netcorenoc   # shows the one-time bootstrap admin password
```

Point your devices' SNMP trap destination (v2c) at the host's IP, open
`http://<host>:8080/`, and sign in as `admin` with the bootstrap password from the logs
(you'll be asked to change it on first login). Watch the network assemble itself as traps
arrive. Create per-operator accounts (viewer / editor / admin) under **Users**, and issue
**service tokens** for API clients — see [`SECURITY.md`](SECURITY.md).

### Nix

```sh
nix run github:leonardoSaaads/NetCoreNOC
# or, in a clone:  nix run .
```

Binding UDP 162 needs privileges; unprivileged, run with `NETCORENOC_TRAP_PORT=1162` and
point devices at port 1162.

### Plain Python (3.12+)

```sh
python3.12 -m venv .venv && .venv/bin/pip install .
.venv/bin/python -m netcorenoc.main
```

### No hardware handy?

Start NetCoreNOC on an unprivileged port and replay the bundled fiber-cut scenario with
real SNMP PDUs over UDP:

```sh
NETCORENOC_TRAP_PORT=1162 .venv/bin/python -m netcorenoc.main &
make replay
```

## Configuration — all optional, all environment variables

| Variable | Default | Meaning |
|----------|---------|---------|
| `NETCORENOC_DB` | `netcorenoc.db` | SQLite file (WAL mode) |
| `NETCORENOC_TRAP_HOST` / `NETCORENOC_TRAP_PORT` | `0.0.0.0` / `162` | Trap listener (UDP, SNMPv2c and, since v0.3.0, SNMPv1 via RFC 3584) |
| `NETCORENOC_HTTP_HOST` / `NETCORENOC_HTTP_PORT` | `0.0.0.0` / `8080` | Web UI and API |
| `NETCORENOC_ALLOWLIST` | *(allow all)* | Comma-separated source CIDRs; set it to enforce |
| `NETCORENOC_API_TOKEN` | *(unset)* | **Removed in v0.3.0**: setting it (or the legacy `OPTICORR_API_TOKEN`) is a hard startup error naming the migration path — use service tokens |
| `NETCORENOC_RETENTION_DAYS` | `7` | Pruning horizon for cleared/closed history |
| `NETCORENOC_AUDIT_RETENTION_DAYS` | `365` | Retention for the audit log (admin-triggered prune only) |
| `NETCORENOC_TLS_CERT` / `NETCORENOC_TLS_KEY` | *(unset)* | Enable built-in TLS; the session cookie then gains `Secure` |
| `NETCORENOC_LOG_JSON` | *(off)* | Structured JSON logging when set |

> **Rebrand (v0.4.0):** the project was renamed from *OptiCorr* to **NetCoreNOC**. The legacy
> `OPTICORR_*` variable names are still honoured for this one version and emit a single startup
> deprecation warning each (naming the variable, never its value); they are removed in v0.5.0.
> See `MIGRATION.md`.

## How it works — three numbers per decision

Every trap is reduced to *device* (source IP), *class* (trap OID as an opaque token),
and *instance*. Alarms deduplicate by that fingerprint. Two alarms in the 120 s sliding
window are linked when

```
s = 0.3·e^(−Δt/30s) + 0.35·A[class_i, class_j] + 0.35·E[device_i, device_j] > 0.5
```

`A` and `E` are learned incrementally (normalized PMI over co-occurrence, exponential
forgetting, 10× damped during storms; a device pair needs n ≥ 5 observations before its
edge is trusted). Since v0.3.0 `E` is the **entity affinity**, kept at network-element
level: same entity ⇒ 1, same NE but a different entity ⇒ 0.8, otherwise the learned NE×NE
affinity — which reduces to v0.2.0's device affinity exactly until an NE is subdivided.
Situations are the connected components of the link graph; the three terms are stored on
every link, so every grouping is auditable by inspection. Within a situation, learned
temporal precedence flags the probable root. Raise/clear pairs are learned from strict
alternation (linkDown/linkUp is pre-seeded), now also at the varbind level for single-OID
state traps; fully cleared situations close and reinforce the matrices. `Confirm`
reinforces a grouping, `Split` penalizes it.

**What is alarmed (v0.3.0).** A bounded, in-engine profiler scores each varbind by three
explainable terms — repeat rate, cross-class overlap, and non-monotonicity — and promotes the
one that names the alarmed entity (the ONU, the port) only when the evidence clears
conservative floors and beats the runner-up. Containment (card → port, port → ONU) is recovered
by a functional-dependency test. Promotion is forward-only and every decision is inspectable in
the **Entities** tab (`key_source`, `confidence`, and the score breakdown); an admin can reset a
poisoned one. See [`docs/DESIGN.md`](docs/DESIGN.md).

Cold start is honest: with nothing learned the class affinity `A` is zero and the entity
affinity `E` is 1 only within a network element, so the temporal term alone must clear the
threshold — two alarms group only when they are on the **same NE and within ≈ 21 s** of each
other (Δt < 30·ln 2). Everything beyond that — cross-device correlation, raise/clear pairs,
which varbind names the alarmed entity — is learned. Run NetCoreNOC in parallel with your
existing NMS from day one (it only needs a copy of the traps) and let it learn.

## Security (v0.2.0)

Identity, role-based authorization, and a tamper-evident audit log. Accounts with three
roles (viewer / editor / admin) and a deny-by-default permission map; `scrypt` passwords
(NIST SP 800-63B policy) with per-username/per-IP login throttling and no user
enumeration; server-side sessions (SHA-256-stored ids, `HttpOnly; SameSite=Strict`
cookie, sliding idle + absolute timeouts) with CSRF protection; revocable per-identity
service tokens; optional built-in TLS with an auto-`Secure` cookie. The UI runs under a
strict CSP with locally vendored d3 (no CDN); externally sourced strings never touch
`innerHTML`. The SNMPv2c community string is never persisted or logged (kept only as an
HMAC grouping tag). An append-only, hash-chained audit log records every mutating action
and sensitive read — verify it with `python -m netcorenoc audit verify`. Source-IP
allowlist (enforced when set), per-client rate limiting, defensive parsing with
quarantine, non-root container, no secrets in logs. `bandit`, `pip-audit`, and a
secret-leak scan run in CI. See [`SECURITY.md`](SECURITY.md) and
[`docs/SECURITY-REVIEW-0.2.md`](docs/SECURITY-REVIEW-0.2.md).

## Development

```sh
python3.12 -m venv .venv && .venv/bin/pip install -e .[dev]
make qa        # ruff + mypy --strict + pytest with coverage
make security  # bandit + pip-audit
make loadtest  # 1000 traps/s for 60 s against a running instance
```

Design rationale in `docs/DESIGN.md`, scope in `docs/SCOPE.md`, every ambiguity call in
`docs/DECISIONS.md`, phase gate evidence in `docs/gates/`.

## Philosophy

- **Zero configuration.** The user provides nothing but a trap destination.
- **Structure emerges from the stream.** Devices, classes, raise/clear pairs, topology
  edges, and precedence statistics are learned, never declared.
- **Explainability over sophistication.** Three numbers explain every link. No black
  boxes.
- **Simplicity is a feature.** No brokers, no ORMs, no plugins, no frontend toolchain.

## License

Apache-2.0 — see [LICENSE](LICENSE).
