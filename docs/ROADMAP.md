# Roadmap

**Open items — one item a bullet, and the measurement that makes it an item.** Anything sequenced
into a release is in [`plans/releases.md`](plans/releases.md); anything that is a defect with a
reproduction is in [`findings.md`](findings.md). This is everything else.

This file used to claim *"one line each"* and run to five lines an item, which is the small version
of the failure this release exists to fix. The claim is corrected rather than the entries truncated:
*"`MIN_INCIDENTS_FOR_INTERVAL = 10` is too permissive"* is an opinion, and the same sentence with
`[33.3, 91.7]` in it is a work item.

v0.15.0 removed the eight *"found while building vX"* sections (#197) — 291 lines of working notes
from v0.9.0 to v0.14.0 — taking this file from 649 lines to 148. What was still live is below; the
rest is at `3ecf237` ([`record.md`](record.md)).

## Product

- **Two-factor authentication, required for admin accounts.** Not scheduled into a release, and
  **the console says so on the sign-in card and the account screen** rather than implying it exists
  (v0.15.3, #238). Today a password is the only factor this appliance has. Whatever ships must not
  reintroduce F79's shape: a second factor an admin can lose is a second way to lock the appliance
  out of itself, so enrolment and recovery are the same design question, not two.
- **SNMPv3** — needs credentials, hence configuration, hence deliberately post-MVP.
- **Export to ticketing systems** — phase 3 of the three-phase design ([`architecture.md`](architecture.md)).
- **Automatic MIB enrichment** — readable names without any user obligation.
- **A precursor/gauge layer** — performance-monitoring signals ahead of alarms.
- **PostgreSQL / NATS if scale ever demands it** — queue and storage already sit behind interfaces.
- **WebSocket push** instead of 2.5 s polling.
- **Replay from pcap**, not only from JSON fixtures.
- **Root-cause confidence on screen** — the precedence margin is computed and not shown.
- **Situation subsumption, impact scope, recurrence fingerprint.**
- **A second read-only SQLite connection** for the API, if console latency under storms ever matters.

## Correlation and the entity model

- **Unlearning / expiry for learned raise-clear pairs** — permanent once promoted, today.
- **Typed relations and device-archetype clustering** (#36) — what v0.17.0 needs and does not have.
- **Finish the `device_id` → `entity_id`/`ne_id` cutover** (#35), forward-only, with a parity re-run.
- **`situation.merged_into` resolves one hop, not transitively.** Two consumers still use a one-hop
  `COALESCE` where the training joins use the transitive walk. On every corpus this project holds all
  four agree at 37 incidents **because every merge chain in it is exactly one hop** — which is
  precisely the condition under which a divergence would first appear silently.
- **X.733 / 3GPP TS 32.111 features** — a minor contract bump (#49); populating them needs MIB
  enrichment.
- **Generalised per-link attribution storage** — a scorer with a different term set needs a child
  table or a JSON column (#50).
- **Per-link scorer provenance**, and an *"effect of the last parameter change"* report derived from
  it — the after-the-fact companion to the before-the-fact preview.

## The evidence chain

- **The champion has never changed on any corpus this project holds** — the approve-and-verify steps
  have never run, in either direction.
- **A behavioural equivalent for the parameter-inspecting degeneracy rules** — a model whose
  parameters cannot be read defeats every one ([`plans/cartridge.md`](plans/cartridge.md) §2.3).
- **No retention tier knows what a citation is** — nothing prevents a promotion citing a run whose
  input rows have been pruned, and nothing warns.
- **The merge graph is unsnapshotted** — an evaluation is *citable* but not *reproducible*, and the
  sealed holdout's own membership is not reproducible either.
- **A quantity that is `not computable` is represented as `0.0`**, so the power trigger fires for the
  wrong reason. Registering a fourth state belongs in a pre-registration.
- **The simulated corpus conflates two measurements** — concurrency measures the appliance under
  load, spacing measures the promotion machinery. A new pre-registration should decide which it asks.
- **`MIN_INCIDENTS_FOR_INTERVAL = 10` is too permissive** — twelve incidents printed `[33.3, 91.7]`,
  a range wide enough to contain any conclusion.
- **The cluster-bootstrap interval arithmetic is exercised by no gate** — every fixture sits below the
  threshold, so the intervals would first appear in production never having been compared.
- **The skew comparison is blind to a feature divergence that does not move the score** — its
  correctness rests on a column-aliasing convention rather than on a check.
- **`client_diverged` compares ordered digests** — legitimate as a measurement *of the client*,
  misleading if read as one of staleness. The repair is a second digest beside it, never in place.
- **One label promotes an entire storm's sink** — 45 050 rows from one verdict, so the corpus is
  bounded by *labels × situation size*. Weighting, capping and sampling are all modelling decisions.

## Guards that do not guard as much as they look like they do

- **The migration files' own SQL text is unguarded** — a test pins one backfill expression as a
  *constant* and nothing ties it to the `.sql` file; deleting the predicate leaves the tree green.
  The repair is a guard for the **class**, and it needs a fixture carrying both member sources.
- **The authorization perimeter fails *open* on an undeclared route, and nothing tests it** —
  inverting the capability check left every test green. It is the second layer of a two-layer
  defence, untested.
- **Three retention behaviours nothing pins** — the row cap's delete order, the audit tier's
  lifecycle clause, and a label-deletion boundary. The tier that could destroy a promoted corpus *is*
  guarded; the ordering and boundaries within each tier are not.
- **The coverage classification is off by one and no fixture notices** — the bias fixture's bags are
  too small for the boundary to land where it matters.
- **Two bounds can be widened by orders of magnitude unnoticed** — the observation buffer and the
  shadow row cap. Both bound memory under storm and no test reaches either.
- **No test exercises a migration that fails half-applied** — whether the implicit transaction saves
  a crash mid-script is unchecked.
- **Nothing runs the ingest batch loop and a stream of API writes against each other** — the property
  at risk is batch latency under API pressure, which an operator experiences as a NOC that stops
  keeping up during an incident.
- **The dead-code allowlist matches by NAME, not by path**, so a moved method keeps its exemption
  while its recorded path decays.
- **One structural test counts mentions, not calls** — naming the function in a docstring makes the
  count wrong. An AST-based caller count would be a few lines and would say what the test means.
- **The route-shape allowlist detects a new shape, not a changed meaning** — if a future route class
  carried its verbs elsewhere, the shape set would be unchanged and the gate would check nothing.
- **A byte-frozen report test flapped once under machine load and the mechanism was not identified** —
  worth either a wider blanking rule or a stated tolerance, and guessing which from one observation
  is how a flake becomes a silently loosened assertion.

## Console

- **The panel loaders have no capability check of their own** — the observed outcome is right and the
  mechanism is a `TypeError` from a removed container.
- **The graph and timeline render paths are uncharacterised** — d3 is a recording double, so two of
  the largest render paths execute against a stub.
- **The unauthenticated boot path is not exercised** — nothing asserts that a failed resume renders
  no panel and, the part that matters, issues no further requests.
- **No shape assertion on the captured fixtures** — a route that dropped a field would render
  `undefined` and every invariant would still pass.
- **`make dom` reports executed DOM tests; nothing reports them in CI** — a step failing when the
  executed count drops to zero is one line of workflow and closes the last anti-skip gap.
- The measured console defects are sequenced into v0.15.2:
  [`plans/v0.15.2-console.md`](plans/v0.15.2-console.md).

## Open after v0.16.1 — asked, and answered with "not here"

- **Per-class alarm statistics.** *"Alarm classes is a list with no statistics"* was v0.16.1's
  decision-4 charge and the screen survived without any: nothing serves a per-class count, and the
  choice was between adding a route for a number **nobody named a question for** and leaving it
  (#271, Part VII rule 4). The question a release should answer first is which class is noisy
  *right now* versus which has been noisy *this week*, because those are different routes.
- **`d3.v7.min.js` is 279 706 bytes for two views**, and v0.16.1 reconsidered it rather than
  re-deferring it silently. It stays, and the reason changed: the graph's two questions are now
  answered in **ordinary DOM tables** the harness executes, so the drawing is the ornament and the
  text is the instrument. That makes removing d3 cheaper than it has ever been — the screen would
  lose a picture and keep every fact — and it is still a release's worth of force-layout work
  nobody has asked for. Reconsider again the first time the graph needs a feature d3 must provide.
- **The hidden members of a scoped label are a count, not a set** (F93). The observable-pair count
  is exact; the pair *selection* is arbitrary on any bag captured under a restriction. Repairing it
  means recording which ids were withheld, which is a change to `0011`'s evidence boundary and an
  analytical decision about whether a redaction may leave a per-member trace — a plan, not a patch.
- **`MIGRATION.md`'s table is not derived from the release chain** (F94). v0.16.0 shipped without a
  row and the prose above the table was arithmetically correct about the rows that were there, so
  nothing looked wrong. `tests/test_documentation.py` already checks release claims against
  `plans/releases.md`; the table is the next thing that could be.

## Deliberately out — rejections, so nobody re-litigates them by accident

- **True multi-tenant isolation** (#59) — the thing visibility scoping explicitly is **not**. It would
  change the engine, the schema and the eval methodology.
- **Custom roles** (#56) — a runtime-defined role has no compiled ceiling, so the escalation-proof
  intersection's first operand would become stored data.
- **Per-field scoping policies** (#59) — field shaping stays compiled.
- **External identity providers, SSO, SCIM, MFA, group provisioning** — principals stay local.
- **An external-API scoring criterion on the correlation hot path** (#44) — advisory or offline if it
  ever exists, never authoritative in `score()`.
- **Splitting the request models per route group** — fragmenting eleven models across nine modules
  would make the request surface harder to audit, not easier.

## Smaller, still open

- **`/openapi.json` is served unauthenticated** — the full API surface is readable without an
  identity. Whether to authenticate or disable it is a public-contract question.
- **`ROUTE_SCOPE` is descriptive, not enforcing** (#80) — every entry is a human judgement checked
  against observed behaviour rather than a check the perimeter injects.
- **One layer-rule violation remains** — `runtime.py` imports the allowlist parser from the ingest
  layer. Either the parser moves to cross-cutting, or the config holder keeps strings.
- **`Capture.warnings()` is never surfaced** — a degraded capture is counted, logged and invisible on
  the stats payload. One line.
- **`eval/corpus_gen.py` and `eval/harness.py` are over the 400-line guard** (457 and 435), outside
  its reach rather than exempted from it. Splitting the harness needs care: its stdout is the frozen
  `c2e8a0ce…` hash.
- **The two `ASSERTING_*_FLOOR` constants are declared twice**, once used and once dead — weight
  rather than hazard.
- **A write path for the pre-registered sufficiency floors** — evidence-chain work rather than
  settings work, because persisting a floor changes the promotion gate's inputs.
- **HTTP routes for the four CLI reports** — the screens exist, the routes do not, so an operator
  reads a verdict's *label* in the browser and its *content* in a terminal.
- **`make security` fails on `pip` itself where the bundled pip is old** — an environment fact, not a
  defect in this project.
