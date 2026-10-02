# NetCoreNOC v0.28.0 — handoff

**A field review**: the product used the way a novice, an experienced operator and a team would —
from the README to a deployment — plus seven new incident scenarios driven through the real parser
and engine. Verified by execution before any change: `__version__` 0.27.0 at `168ac7c`, schema 27.
This hands over **v0.28.0** on `claude-code/trusting-goldberg-j8xpoo`. **No migration**, and
`make eval` is byte-identical (`43328080…`).

## The scenarios, before and after

Each was sent as real SNMP PDUs through the parser and the engine, with the maintenance sweep on the
scenario's clock, under the fail-safe formula and under the shipped champion (random forest).

| Scenario | v0.27.0 | v0.28.0 |
|---|---|---|
| **OTM2 service mismatch, clear lost, raised again 3 h later** | stayed in the old situation, untouched; no new one | old situation resolves *idle* with an event naming the alarm; **a new situation opens** |
| **…then an operator closes it, and the device repeats the raise** | every repeat absorbed into the resolved situation; alarm active and in no live view | **a new situation opens** |
| Optical RX power too high, re-sent every 15 min, ends with severity `cleared` | alarm stayed active with severity `cleared`; situation `new` for good | cleared on the first `cleared`; resolves |
| Site power failure (UPS on battery → neighbours' uplinks down → power back, cold starts) | the cold starts and the UPS alarm kept two situations `new` for good | they end after 5 min of silence; situations resolve |
| OLT power loss (both upstream uplinks down, back 20 min later, OLT cold start) | cold start situation `new` for good | resolves |
| Intermittent port, down/up every 10 min, six times | **5 situations**, then demoted as flapping | **3**: from the second bounce the situation is held while it bounces |
| Access switch, four ports bouncing irregularly | 1 (formula) / 2 (champion) situations, all resolve | unchanged |
| Standing alarm re-sent every 5 min for 2 h | 1 situation | unchanged (the control) |

**Not fixed, and why** — grouping quality on the power scenarios is the model's, not the lifecycle's:
under the champion the UPS alarm, the neighbours' uplink failures and the dying gasp of one site
power failure are **four situations**; under the formula, the two upstream uplinks of one OLT are
two. That is training data (the generator's cross-element storms), not a rule to patch here. And a
vendor whose trap carries its *description* before the port gets the description as the alarm's
instance, so two ports with the same alarm text share one alarm until entity promotion learns the
port (#432 says why skipping prose is riskier).

## Found and fixed

| Area | Defect (measured) | Fix | ADR |
|---|---|---|---|
| lifecycle | the three OTM2 / close / X.733 rows above; a `cleared` with nothing to clear created an active alarm; a severity word first became the instance, so raise and clear never met | `engine/operate/occurrence.py`, `Placement.clears`, `receiver._instance_of`, the maintenance ledger | #431, #432 |
| lifecycle | standard notifications with no clear held situations for good; intermittent ports opened a situation per bounce | `known_oids.OCCURRENCE_NOTIFICATIONS` (cited), `occurrence.settled` | #433 |
| security | the login throttle and the rate limiter cleared their whole table past 4 096 keys: a flood of throwaway usernames reset the lockout on the attacked account | evict harmless or soonest-ending entries first | #434 |
| security | every JSON route read its whole body first, including the unauthenticated login: a few large posts reach the 512 MiB container limit | `api/body_limit.py`: 413 over 1 MiB, inside the perimeter | #434 |
| deployment | `NETCORENOC_TRAP_PORT` in `.env` was neither passed nor mapped by Compose | passed and mapped | #435 |
| deployment | `python -m netcorenoc.main --help` started the server | `--help`, `--version`; other arguments refused | #435 |
| deployment | `audit verify` on a mistyped path created an empty database and said OK | every CLI command refuses a missing database | #435 |
| operations | no way back for an only admin who forgot a password; no backup procedure | `admin reset-password` (audited, sessions revoked), `backup` (online, integrity-checked) | #435 |
| operations | nothing said which model was deciding without signing in | start-up log line | #435 |
| CI | actions on the deprecated Node 20 runtime | checkout v5.0.1, setup-python v6.0.0, action-gh-release v3.0.3, pinned by commit | — |

## Documentation

README, `docs/README.md`, `docs/operate.md` and `docs/troubleshoot.md` rewritten as direct
guidance: a ten-minute quickstart, a test-traffic path that avoids Docker's source rewriting, a team
checklist (allowlist, TLS, roles, second admin, tokens, backup, upgrade), and every alarm rule in one
table. Removed or corrected: the v0.26.0 "ships no model" statements (four places), view and test
counts, the wrong bootstrap log example, "move the database aside" as password recovery, and the
`MIGRATION.md` row-counting preamble. Seven stale release briefs left `docs/plans/`
(`docs/record.md` says where they are); the claim-form convention moved to `CONTRIBUTING.md`.

## Live passes

- **Team, over real sockets** (TLS with a self-signed certificate, allowlist, re-arm window 20 s):
  bootstrap → admin with `Secure` cookie → an editor, a viewer and a second admin; the viewer's
  close refused (403); OTM2 raised, silent 25 s, raised again → situation 1 *resolved idle* with an
  `idle_close` event, situation 2 *new*; the editor closes 2, the device repeats → situation 3;
  the X.733 `cleared` clears the alarm; a 2 MiB login body → 413 with the security headers;
  `backup` while running → integrity ok; `admin reset-password ana` → her session 401, the one-time
  password signs in; `audit verify` → OK.
- **Novice, following the README** without Docker: `make replay` → 2 devices, 8 alarms, 1 situation.
- **Browser** (Chromium, 1440 and 390 px): the history of a situation concluded by recurrence
  explains it; 0 px overflow; the only console error is the pre-sign-in `401` from `/api/me`.
- **Not run**: the Docker image build — this environment's network policy blocks
  `registry-1.docker.io`. Compose changes are covered by `tests/test_deploy.py`, and the same
  process was run directly.

## Verification

| gate | result |
|---|---|
| full suite | see the PR description (re-run after the last change) |
| `mypy --strict`, `ruff check`, `ruff format --check` | clean |
| `vulture`, `bandit`, `pip-audit --skip-editable` | clean; no known vulnerabilities |
| `make eval` | no gated regressions; hash `43328080…` unchanged |
| behaviour-identity record | 12 lines changed, all attributed: the version string in `/healthz` and `/openapi.json`, and `lifecycle.js` (four roles each) |
| injections | `tests/test_occurrences.py`: 9 of 14 tests red on the v0.27.0 source, the 5 controls green on both; `tests/test_body_limit.py` red without the middleware |
