# Gate 3 — API and living-graph UI (self-review)

Date: 2026-07-19. Verdict: **PASS**.

## Criteria and evidence

Full `make qa` after this phase:

```
106 passed in 11.93s
opticorr/api.py             94      2     14      1    97%
TOTAL                     1004     23    224      9    97%
Required test coverage of 85.0% reached. Total coverage: 97.23%
```

### End-to-end demo over HTTP (`tests/test_api.py`)

- `test_end_to_end_udp_replay_to_http`: real trap PDUs over UDP → engine → HTTP API
  reports the alarms and their situations.
- `test_situations_with_explanations_and_root`: the situation detail carries every link
  with its three score terms (`term_t`, `term_a`, `term_e`) and the root-cause hint.
- `test_split_feedback_via_http_measurably_reduces_affinity`: a `split` POST halves the
  learned device-pair mass (asserted numerically) and is recorded in `feedback`.
- `test_labels_are_cosmetic_and_persisted`: renames for devices and classes persist and
  surface in situation details and the graph; invalid kinds/empty labels are 422.
- `test_graph_exposes_learned_topology`: `/api/graph` returns devices plus trusted
  learned edges (weight, n) for the living graph.
- `test_main_run_serves_real_sockets`: the single-process wiring (receiver + engine +
  uvicorn) smoke-tested over real UDP and TCP sockets.

### Security behaviors

Static bearer token required on every `/api` route (`compare_digest`), 401 otherwise;
`/healthz` and the UI shell are the only open paths. Token-bucket rate limiting per
client returns 429 (`test_rate_limit_returns_429`). When `OPTICORR_API_TOKEN` is unset a
random token is generated and logged once at startup — zero configuration preserved,
auth still on by default.

### UI (single file, no build step)

`opticorr/ui/index.html` — d3-force from a pinned CDN URL (`d3@7.9.0`), polling every
2.5 s. Living graph (nodes = devices sized by active alarms, red = alarming; edges =
trusted learned affinity, opacity/width = weight), stat chips, situation cards with
expandable alarm tables, per-link three-term score bars (colors validated for the dark
surface with the palette validator: CVD ΔE 8.4, contrast ≥ 3:1, all checks pass),
rename-on-click, and confirm/split buttons.

Rendered check (app + fixture replay + headless Chromium): `phase-3-ui.png` — graph,
learned edge between the fiber-cut NEs, expanded situation with root hint, term bars,
and feedback controls all visible; no console errors.
