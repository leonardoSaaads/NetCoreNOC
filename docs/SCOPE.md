# OptiCorr — MVP v0.1.0 Scope (4 weeks)

This document is the authoritative product scope for OptiCorr v0.1.0.

Principle: **zero configuration**. The user points network equipment at the server (as an
SNMP trap destination) and nothing else. No user-supplied MIBs, no YAML, no inventory, no
NetBox, no external message bus. Structure emerges from the trap stream itself.

## How the system learns on its own (without user MIBs)

An SNMP trap already carries everything the learning needs, even without translation:

1. **Devices** = source IP. Every new IP that sends a trap automatically becomes a graph
   node.
2. **Alarm classes** = the trap OID, treated as an opaque token.
   `1.3.6.1.4.1.1271.x.y.z` is a distinct class from `1.3.6.1.4.1.2011.a.b.c` — the
   system does not need to know the name to learn how it behaves.
3. **Vendor** = the enterprise prefix of the OID, resolved against the public IANA
   Private Enterprise Numbers table (a static file bundled with the binary — public data,
   not user configuration).
4. **Built-in universal standard**: the only OIDs with pre-loaded semantics are the
   SNMPv2/MIB-II standards every device implements (coldStart, linkDown/linkUp
   `1.3.6.1.6.3.1.1.5.*`, ifIndex). An internal table of ~20 entries; also not
   configuration.
5. **Raise/clear pairs**: learned by alternation — if class X on a (device, instance) is
   always followed by class Y that ends it, X/Y become an active/clear pair.
6. **Topology** = the learned graph `E[v_i, v_j]`: devices that repeatedly alarm together
   gain an edge (incremental NPMI with decay). There is no declared source and no
   discovery in the MVP — the living graph **is** the topology.
7. **Readable names**: the operator may rename devices and classes in the UI at any time;
   labels are cosmetic and persisted. Automatic MIB enrichment is a future version —
   never a user obligation.

## Analysis engine (deliberately small)

- **Dedup**: fingerprint (device, class, instance); a repeat bumps the counter/last-seen.
  A simple periodic-flapping detector demotes noise.
- **Link score** between alarms in the sliding window (120 s):

```
s(a_i, a_j) = w_t · e^(−Δt/τ) + w_A · A[c_i, c_j] + w_E · E[v_i, v_j]
```

  with `A` = learned affinity between classes, `E` = learned affinity between devices,
  same device ⇒ E = 1. Defaults: w_t = 0.3, w_A = 0.35, w_E = 0.35, τ = 30 s; link if
  s > 0.5. Three numbers explain every link — auditable by inspection.
- **Situations**: connected components of the link graph.
- **Root cause (heuristic)**: learned temporal precedence — for each class pair the
  system accumulates statistics on which fires first; within a situation, the alarm of
  the most "preceding" class on the most "preceding" device is flagged as the probable
  root. It will be wrong at first; feedback corrects it.
- **Continuous learning**: every closed situation updates `A` and `E` (λ = 0.05, decay,
  10× smaller weight during mass storms so confounders such as a regional power outage
  are not learned). Operator feedback: `confirm` reinforces, `split` penalizes.
- **Honest cold start**: in the first weeks the system is ignorant by definition — there
  is only learning after there are events. Running in parallel with an existing NMS from
  day 1 (it only receives a copy of the traps) solves this without risk.

## Scope cut from the MVP (becomes roadmap, not excuses)

Removed: NATS/Kafka (in-process memory queue), PostgreSQL (SQLite WAL), X.733, domain
packs, mapping YAML, LLDP discovery, NetBox, PM/precursor layer, Salesforce, Grafana,
SNMPv3 (v2c traps require no credentials — consistent with zero configuration; v3
requires keys = configuration, so it comes later), multi-tenant.

Post-MVP roadmap, in order: (1) SNMPv3, (2) ticketing export, (3) automatic MIB
enrichment, (4) precursor/gauge layer, (5) PostgreSQL/NATS if scale demands it — the
internal architecture already isolates queue and storage behind interfaces so that swap
does not hurt.

## Schedule — 4 weeks

| Week | Deliverable | Acceptance criterion |
|------|-------------|----------------------|
| 1 | Core: pysnmp receiver (v2c, port 162, IP allowlist), minimal event model, SQLite, dedup, bundled IANA table + standard OIDs | Real traps from 2+ vendors stored and deduplicated; device and vendor identified without any configuration |
| 2 | Learning + correlation: incremental NPMI for `A` and `E`, link score, connected components, temporal precedence | Fiber-cut scenario replay (8 alarms, 2 NEs) ⇒ 1 situation; OLT storm replay (1 uplink + 500 ONUs) ⇒ 1 situation |
| 3 | API + UI: FastAPI; situation list with explanation (the 3 score terms); **living graph** (force-directed) showing learned nodes and edges growing in near-real-time; rename; confirm/split feedback | Operator watches the network "assemble itself" on screen and corrects a wrong grouping |
| 4 | Hardening: load test (1000 traps/s burst), property tests on parsers, malformed-trap quarantine, Dockerfile + flake.nix, README, tag v0.1.0 | One-command install; 24 h running on real traffic without intervention |

## Repository layout

```
opticorr/
├── LICENSE                # Apache-2.0
├── README.md              # one-command install; zero-config philosophy
├── CHANGELOG.md
├── pyproject.toml
├── flake.nix
├── Dockerfile             # single image, non-root
├── opticorr/
│   ├── main.py            # single asyncio process: receiver + engine + API
│   ├── receiver.py        # pysnmp v2c, allowlist, quarantine
│   ├── events.py          # minimal model: device, class(OID), instance, ts, varbinds
│   ├── known_oids.py      # IANA PEN + standard SNMPv2 traps (bundled data)
│   ├── store.py           # SQLite WAL (aiosqlite), behind an interface
│   ├── learn.py           # incremental NPMI A and E, decay, storm damping
│   ├── correlate.py       # window, score, connected components
│   ├── rootcause.py       # temporal precedence
│   ├── api.py             # FastAPI: situations, feedback, labels
│   └── ui/index.html      # single UI: list + living graph (d3-force)
├── tests/
│   ├── test_learn.py  test_correlate.py  test_receiver.py
│   └── fixtures/{fiber_cut.json, olt_storm.json}
└── tools/trap_replay.py   # injects fixtures and real captures
```

## Security baseline (kept even in the MVP)

Source-IP allowlist in the receiver, non-root container, defensive parsing with
quarantine (a malformed trap must never bring the process down), API with a simple
token, no secrets in code.
