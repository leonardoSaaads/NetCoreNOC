# OptiCorr v0.1.0 — Build Report

Built autonomously, empty repository → tagged release, following the four-phase
waterfall in the build brief with `docs/SCOPE.md` as the authoritative scope.
Date: 2026-07-19.

## What was built

A zero-configuration NOC trap correlator: one Python 3.12 asyncio process, one SQLite
(WAL) file, one static web UI.

- **Ingestion** — SNMPv2c UDP receiver with source allowlist, defensive parsing, and
  raw-packet quarantine; fingerprint dedup; periodic-flapping demotion; bounded
  in-process queue; batched single-transaction persistence.
- **Learning** — class matrix A and device matrix E as evidence-discounted normalized
  PMI over co-occurrence, exponential forgetting per closed situation (λ = 0.05),
  n ≥ 5 trust gate on device edges, 10× storm damping (matrices *and* raise/clear
  alternation learning). The learned device graph is the topology.
- **Correlation** — three-term explainable score
  `s = 0.3·e^(−Δt/30) + 0.35·A + 0.35·E` over a 120 s window, links > 0.5, situations
  as connected components with merge-on-bridge; the three terms stored per link.
- **Root cause** — learned class/device lead-lag precedence; earliest-first tie-break.
- **Raise/clear** — learned by strict alternation per (device, instance); seeded with
  linkDown/linkUp; auto-close on full clear triggers a learning epoch.
- **Feedback** — confirm reinforces, split halves the grouped pair masses (verified
  measurable through the HTTP API).
- **API/UI** — FastAPI (token auth, rate limiting) + one d3-force HTML file: living
  graph, situation explanations, renames, feedback.
- **Operations** — retention pruning, versioned learned-state persistence, restart
  recovery, Dockerfile (non-root), flake.nix, Makefile, CI (ruff, mypy --strict,
  pytest + coverage ≥ 85%, bandit, pip-audit).

## Quality results

| Check | Result |
|---|---|
| Tests | 107 passed (unit, property-based, scenario, HTTP, concurrency, e2e UDP) |
| Coverage on `opticorr/` | **97.22%** (gate ≥ 85%) |
| ruff check + format | clean |
| mypy --strict | clean (26 files) |
| bandit / pip-audit | 0 findings / no known vulnerabilities |

## Load and soak numbers

- **Burst**: 1000 traps/s × 60 s → 60,001 sent, 60,001 accepted, **0 dropped**; API
  responsive throughout (12–786 ms); p95 trap→situation latency ~4.8 s during the
  initial 1600-alarm activation wave, **0.02–0.6 s steady-state**; ~69 MB RSS.
- **Soak**: 120 s mixed waves (storm slices + noise + flapping + malformed) with 60 s
  retention → RSS +0.4%; alarm rows capped by dedup; situations 82→36 and quarantine
  140→80 across prune passes; learned edges stable.

Two real defects were found by the load test and fixed with regression tests: false
raise/clear pairs learned from storm interleavings (alternation learning now pauses in
storm state) and an engine-killing commit/cursor interleave on the shared SQLite
connection (all store access now serializes under one asyncio lock).

## Gate evidence

`docs/gates/phase-1.md` … `phase-4.md` (with a rendered UI screenshot in
`phase-3-ui.png`). All four gates passed; scenario acceptance criteria
(fiber cut ⇒ 1 situation of 8 with a plausible root; OLT storm ⇒ 1 situation of 501
rooted at the uplink; background noise isolated; matrices demonstrably change) are
asserted in `tests/test_scenarios.py`.

## Decisions

Thirteen numbered entries in `docs/DECISIONS.md`. The consequential ones: authored
SCOPE.md from the supplied material (1); curated IANA subset (2); instance heuristic
(3); allow-all default for the allowlist (4); three extra tables for links, quarantine
and meta (5); CV-based flapping detector (6); hybrid learning trigger — window
co-occurrence + closed-situation epochs (7); NPMI evidence discount (8); one
observation per activation (9); bounded work caps (10); lone clears are not alarms
(11); store lock (12); storm-gated alternation learning (13).

## Deferred (ordered), from SCOPE plus build notes

1. SNMPv3 · 2. ticketing export · 3. automatic MIB enrichment · 4. precursor/gauge
layer · 5. PostgreSQL/NATS if scale demands. Build-noted: clear-pair unlearning,
read-only API DB connection, WebSocket push, pcap replay, root-confidence in UI,
situation timeline view (`docs/ROADMAP.md`).

## Honest caveats

- The Docker image could not be built inside the build sandbox (registry blocked by
  network policy); the Dockerfile is stock two-stage `python:3.12-slim`, non-root by
  construction, and the equivalent install path was verified in a clean venv.
- The flake.nix is unexercised here for the same reason (no nix in the sandbox).
- Cold start is honest by design: cross-device grouping needs n ≥ 5 co-occurrence
  observations, so the very first cross-device incident may briefly show per-device
  situations before merging (asserted and documented).
