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

[![CI](https://github.com/leonardoSaaads/NetCoreNOC/actions/workflows/ci.yml/badge.svg)](https://github.com/leonardoSaaads/NetCoreNOC/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/leonardoSaaads/NetCoreNOC?sort=semver)](https://github.com/leonardoSaaads/NetCoreNOC/releases)

## Quickstart (5 minutes)

### Docker Compose (one command)

```sh
docker compose up --build
# shows the one-time bootstrap admin password on first start
```

That's it — a hardened run (read-only root filesystem, all capabilities dropped except the one
needed to bind UDP 162, no privilege escalation, the SQLite DB on a named volume). Optional
configuration lives in `.env` (copy `.env.example` and edit; it's git-ignored). See
[`docker-compose.yml`](docker-compose.yml) and the operator guide,
[`docs/security/operations.md`](docs/security/operations.md).

Point your devices' SNMP trap destination (v2c) at the host's IP, open
`http://<host>:8080/`, and sign in as `admin` with the bootstrap password from the logs
(you'll be asked to change it on first login). Watch the network assemble itself as traps
arrive. Create per-operator accounts (viewer / editor / admin) under **Users**, and issue
**service tokens** for API clients.

### Docker (without Compose)

```sh
docker build -t netcorenoc .
docker run -d --name netcorenoc --read-only --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /tmp --cap-add CAP_NET_BIND_SERVICE \
  -p 162:162/udp -p 8080:8080 -v netcorenoc-data:/home/netcorenoc netcorenoc
docker logs netcorenoc   # shows the one-time bootstrap admin password
```

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
| `NETCORENOC_API_TOKEN` | *(unset)* | **Removed in v0.3.0**: setting it is a hard startup error naming the migration path — use service tokens |
| `NETCORENOC_RETENTION_DAYS` | `7` | Pruning horizon for cleared/closed history |
| `NETCORENOC_AUDIT_RETENTION_DAYS` | `365` | Retention for the audit log (admin-triggered prune only) |
| `NETCORENOC_TLS_CERT` / `NETCORENOC_TLS_KEY` | *(unset)* | Enable built-in TLS; the session cookie then gains `Secure` |
| `NETCORENOC_LOG_JSON` | *(off)* | Structured JSON logging when set |

> **Rebrand (v0.4.0), aliases removed (v0.6.0):** the project was renamed from *OptiCorr* to
> **NetCoreNOC**. The legacy `OPTICORR_*` variable names were honoured with a deprecation warning
> through v0.5.0 and are **removed in v0.6.0** (DECISIONS #45). Setting any of them is now a hard
> startup error naming each variable and its `NETCORENOC_*` replacement — never a silent no-op,
> because an ignored `OPTICORR_ALLOWLIST` would mean every trap source is accepted. See
> [`MIGRATION.md`](MIGRATION.md).

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
poisoned one. See [`docs/architecture/DESIGN.md`](docs/architecture/DESIGN.md).

**The formula is a seam, not a constant (v0.6.0).** The three-term score above is the *default
implementation of an interface* (`LinkScorer` in `src/netcorenoc/scoring.py`), not a hard-coded
expression. Nothing about the zero-configuration experience changes: the defaults are what you
get, they are what every number on this page describes, and **most operators never open the
scorer panel**. What changed is that the numbers are now reachable when you need them —

- an **admin** (and only an admin — there is no editor delegation) can retune `w_t`, `w_a`,
  `w_e`, `τ` and the threshold from the **Scorer** tab;
- **preview before you apply**: a read-only what-if re-partitions your own recent alarms under
  the candidate parameters and shows what would merge and what would split, before anything is
  committed. It is directional, not exhaustive — it uses a bounded recent window and holds the
  learned matrices fixed, and it says so;
- values that would collapse or shatter every incident are **rejected**, not merely warned about;
- every change is **audited**, the configuration history is **immutable and append-only**, and
  **rollback is one click** — it moves a pointer, it never edits history;
- every situation records **which configuration formed it**, so a grouping stays explainable
  months later;
- if a scorer ever fails, the engine **falls back to the built-in defaults** and says so, rather
  than stalling or grouping wrongly in silence.

At the default parameters v0.6.0 produces byte-identical grouping to v0.5.0 — that parity is a
release gate, not a claim.

**Governance is optional, and off by default (v0.7.0).** An admin can restrict what each role — or
an individual operator — may **do** and may **see**. As with the scorer, nothing about the
zero-configuration experience changes: **with no policy stored the appliance behaves exactly as it
did before**, the built-in role permissions and full visibility are what you get, and most
operators never open the Governance panel. When you do need it —

- **capabilities** can be taken away from a role or a single principal. The built-in permission map
  is a **ceiling**, not a starting point: the resolved set is `ceiling ∩ policy`, so a policy can
  only ever *narrow*. There is no configuration that gives a viewer an admin capability, and an
  admin can never be locked out of repairing the policy;
- **visibility scoping** limits which network elements a viewer or editor sees, by NE id, address,
  CIDR, or address glob. Out-of-scope elements are absent from every list, a directly-requested one
  returns **404, not 403** — existence is not disclosed — and a **write** to one returns the same
  404. A selector never resolves against the operator label, because the label is written by the
  very role being scoped (v0.7.1, F35). **Admins are never scoped;**
- every change is **audited** with before and after, the policy history is **immutable and
  append-only**, and **rollback and clearing are one click**;
- if a policy ever becomes unreadable, capabilities fall back to the built-in permissions (nobody
  gains anything) and scoping shows nothing to viewers and editors (nobody sees anything new) —
  with a warning and an audit row, never a silent change.

> **⚠ Visibility scoping is a presentation control and is _not tenant isolation_.** Correlation
> still learns across every network element, and a situation may still *form* across a boundary a
> principal cannot see — its members are then shown to them as a redacted count and alarm class,
> never as identifiers. A scoped operator sees a partial picture, which is precisely why the
> redacted count is shown rather than the members being silently dropped. True multi-tenant
> isolation is a larger, separate feature on the [roadmap](docs/ROADMAP.md); NetCoreNOC does not
> claim it today. See [`MIGRATION.md`](MIGRATION.md).

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
[`docs/security/SECURITY-REVIEW-0.2.md`](docs/security/SECURITY-REVIEW-0.2.md).

## Development

```sh
python3.12 -m venv .venv && .venv/bin/pip install -e .[dev]
make qa        # ruff + mypy --strict + pytest with coverage
make security  # bandit + pip-audit
make loadtest  # 1000 traps/s for 60 s against a running instance
```

Start at the documentation index [`docs/README.md`](docs/README.md): design rationale in
`docs/architecture/`, scope in `docs/scope/`, every ambiguity call in
`docs/adr/DECISIONS.md`, phase gate evidence in `docs/gates/`. A newcomer's one-screen tour of
the tree is [`docs/architecture/repo-map.md`](docs/architecture/repo-map.md).

## Community

- **Contributing:** [`CONTRIBUTING.md`](CONTRIBUTING.md) — dev setup, the quality bar, and the
  hard constraints (zero new runtime deps, one process/file/UI).
- **Code of conduct:** [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) (Contributor Covenant v2.1).
- **Security:** report vulnerabilities privately per [`SECURITY.md`](SECURITY.md); operators see
  [`docs/security/operations.md`](docs/security/operations.md).
- **Roadmap:** [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Philosophy

- **Zero configuration.** The user provides nothing but a trap destination.
- **Structure emerges from the stream.** Devices, classes, raise/clear pairs, topology
  edges, and precedence statistics are learned, never declared.
- **Explainability over sophistication.** Three numbers explain every link. No black
  boxes.
- **Simplicity is a feature.** No brokers, no ORMs, no plugins, no frontend toolchain.

## License

Apache-2.0 — see [LICENSE](LICENSE).
