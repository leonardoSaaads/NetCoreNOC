# Phase 2 — the NOC plane: what exists, the API reviewed for AI agents, and what comes next

Phase 2 is the half of the product that works a situation once phase 1 (the **Broker**) has made
one: people and AI agents diagnosing and, later, remediating, with a time budget and an escalation
(`architecture.md`, "The three phases"). This page is the plan for it, written as measurements and
open questions (`CONTRIBUTING.md`). What is built is described by ADRs #454-#461, not here.

## 1. Where it stands

| | State |
|---|---|
| **Broker \| NOC** switch, NOC sidebar | built (#454) |
| **Secrets** — the credential vault: two keys, two ciphers, bound to host and port | built (#455) |
| Custodians, step-up re-authentication, audit of every act on a credential | built (#456) |
| Second factor (TOTP: Google Authenticator, Authy) | **placeholder** — field drawn disabled, audit says `not-enrolled` |
| Status lights from reachability checks | built (#457) — the endpoint, not the credential |
| Leases: what an agent holds instead of a secret | built (#458) |
| **Cases** — a case per opened situation, the NOC queue, the customer update, pending statuses | built (#459) |
| The NOC's clocks: take (25 min / 1 h / 2 h) and customer update (30 min), with the overrun | built (#460) |
| The case feed: Internal / Specified / All, a person's post requeues a pending case | built (#461) |
| **Executor** — something that acts on a device with a lease | not built (§4) |
| **MCP server** — how an agent reaches all of this | not built (§3); the case routes are its first tools |
| Script library, scoring scripts, time budget, escalation | not built |

## 2. The current API, reviewed for AI agents (MCP)

An agent will reach the appliance through the Model Context Protocol: a server exposes **tools**
(actions with a JSON input schema, optional output schema, and behaviour hints), **resources**
(readable data by URI) and **prompts**; the client — the agent's host — calls them on the model's
behalf. Whether the API is ready for that is a question about identity, discoverability, the
shape of answers and errors, safety, and size.

### Measured (this tree)

| Fact | Value |
|---|---|
| `/api` routes | 128 — 54 GET, 66 POST, 8 DELETE; 14 of them the NOC's |
| Routes declaring capability **and** scope posture, in code and in the OpenAPI schema (`x-capability`, `x-scope`) | 128 of 128, enforced at start-up (`api/declare.py`) |
| Routes with a one-line summary for programs (`api/reference.py`) | 132 of 132 (public ones included) |
| Routes with a typed **response** model | 0 — every handler returns `dict[str, Any]` |
| Routes with an explicit `operationId` | 0 — FastAPI derives e.g. `list_secrets_api_noc_secrets_get` |
| Route modules taking `limit`/`offset` | 6 of 31 |
| Writes accepting an idempotency key | 1 (`POST /api/maintenance-windows`) |
| Service-token expiry | none: a token lives until revoked |
| Rate limit | per client address (30 burst, 10/s), not per principal |

### What is already right, and must be kept

* **Per-identity, revocable service tokens**, narrowable one by one (`ceiling ∩ policy`, #53), so
  each agent can hold exactly its capabilities. `Principal.is_token` is the one answer to "is this
  an agent?" (#370), and already marks agent-created maintenance windows and refuses an agent
  confirming its own long one.
* **Deny by default, declared at start-up.** A route that has not declared its capability and scope
  cannot exist (`api/declare.py`) — so a generated tool list can be filtered by what a token may
  actually call, from the schema alone.
* **Every act is attributed** in a hash-chained, sealed audit log, under the actor's name.
* **Inputs are bounded** (1 MiB bodies, `max_length` everywhere) and a 422 never repeats a value.
* **The kill switch is below admin** (#412): a human can always stop autonomy.
* **The NOC API was written for agents from the start**: no response an agent can receive carries a
  secret; a lease is bound to the agent's own token and to one credential (#458); introspection is
  RFC 7662's shape (`200 {"active": false, "reason": …}` rather than an error); the credential
  catalogue (`GET /api/noc/secrets` → `catalogue`) is the schema a tool's input is built from; a
  refusal that is not an authorization failure answers with a sentence that says what to do.

### Gaps, each with what to do about it

| # | Gap | Why it matters to an agent | Proposal |
|---|---|---|---|
| G1 | No response schemas | MCP tools can declare an `outputSchema` and return structured content; without one the model guesses field names | Pydantic response models for the agent-facing subset first (§3's tools), then the rest |
| G2 | Derived operation ids | Tool names are an agent's vocabulary; `list_secrets_api_noc_secrets_get` is unstable and noisy | Explicit `operation_id` on the agent-facing routes (`noc_lease_credential`…) |
| G3 | Errors are prose only | An agent branches on a code, not on a sentence | Keep `detail`; add a machine-readable `code` (`not_custodian`, `step_up_required`, `lease_inactive`, `not_an_agent`) |
| G4 | Unbounded or offset-paged lists; large details | A situation's detail can exceed a context window | Cursor pagination and `brief` projections for agent tools; hard caps stated in each tool's description |
| G5 | Tokens never expire; no audience | The MCP authorization spec asks for audience-bound, short-lived OAuth 2.1 tokens (RFC 8707, RFC 9728) and forbids passing a client's token through to another service | Expiry and an *agent* flag on service tokens now; OAuth 2.1 at the MCP endpoint when it exists. The lease already is audience-bound (`aud: netcorenoc-noc-executor`) |
| G6 | **Untrusted text reaches the model** | SNMPv1/v2c traps are unauthenticated: varbinds, device and alarm names are attacker-controlled strings — the classic indirect prompt injection | Tool results carry device-supplied text in a marked `evidence` block, never in a tool's instructions; no action is ever gated on text alone — a device action needs a lease, an allow-listed command for its access level, and a person's confirmation for root (§4) |
| G7 | Live updates are SSE for the console | Agents read resources and poll tools | Resource URIs (`netcorenoc://situations/{id}`, `netcorenoc://elements/{id}`) with change notifications |
| G8 | One idempotent write | Agents retry on timeouts | An `Idempotency-Key` on agent-facing POSTs (a retried lease request then returns the same lease instead of a second — bounded today by 20 live leases per agent) |
| G9 | Rate limit per address | Every agent behind one MCP server shares an address | A bucket per principal beside the one per address |

None of these blocks the vault: they are the work that makes the **next** tools good ones.

## 3. The MCP server (proposed, not built)

**Where**: a Streamable-HTTP endpoint inside the appliance (`/mcp`), behind the same perimeter, the
same declaration gate and the same audit — so a tool is a route with a different envelope, not a
second API with its own authorization. A stdio adapter that forwards to it serves desktop agents.
**Auth**: the agent's service token now; OAuth 2.1 (G5) when exposed beyond the management LAN.

| Tool | Route | Capability | Hints |
|---|---|---|---|
| `list_situations` | `GET /api/situations` | `situations.read` | read-only |
| `get_situation` | `GET /api/situations/{sid}` (brief, G4) | `situations.read` | read-only |
| `get_element` | `GET /api/elements/{ne_id}` | `entities.read` | read-only |
| `noc_list_cases` | `GET /api/noc/cases` | `cases.read` | read-only |
| `noc_get_case` | `GET /api/noc/cases/{sid}` | `cases.read` | read-only |
| `noc_take_case` | `POST /api/noc/cases/{sid}/take` | `cases.work` | idempotent for its owner |
| `noc_update_case` | `POST /api/noc/cases/{sid}` | `cases.work` | `version` against lost updates |
| `noc_set_case_pending` | `POST /api/noc/cases/{sid}/status` | `cases.work` | — |
| `noc_resolve_case` | `POST /api/noc/cases/{sid}/resolve` | `cases.work` | destructive: closes the situation |
| `noc_post_to_case` | `POST /api/noc/cases/{sid}/feed` | `cases.post` | not idempotent until G8 |
| `list_leasable_credentials` | `GET /api/noc/agent` | `secrets.lease` | read-only |
| `lease_credential` | `POST /api/noc/secrets/{id}/lease` | `secrets.lease` | not destructive; not idempotent until G8 |
| `release_lease` | `POST /api/noc/leases/{id}/revoke` | `secrets.lease` | idempotent |
| `run_diagnostic` | the executor (§4) | `secrets.lease` + the lease | open-world; destructive per access level |
| `escalate` | phase 3 | — | — |

**Never a tool**: anything that returns a secret, reveals a credential, names a custodian or an
agent, or switches agent access on. Those are a person's, behind a step-up; the API refuses a token
on every one of them (#456), so the MCP server could not offer them if it tried.

**Human in the loop**: the MCP *elicitation* request — the server asking the person in the agent's
host to confirm — for any `run_diagnostic` at the `operator` or `root` level.

## 4. The executor (the next step, not built)

What uses a lease on the agent's behalf, keeping the secret on the appliance's side:

1. The agent calls `run_diagnostic(lease, command)`. The executor introspects the lease
   (`POST /api/noc/leases/introspect` semantics, in process), checks the command against the policy
   for the lease's access level, opens the seal itself, connects, runs, and returns **bounded,
   redacted output marked untrusted** (G6). The secret never leaves the process.
2. **Never on the ingest path, never holding `store.lock` across I/O** (#454): a process or a task of
   its own, writing its audit rows in short transactions.
3. **Host-key pinning**: the first accepted SSH host key is recorded per credential; a different
   key later turns the light red and refuses — a credential is never offered to an impostor.
4. **The light learns the credential**: a successful login is the first time green can mean "the
   credential works", and the screen will say which of the two it means (#457).
5. **Every command is audited**: lease, agent, a digest and the first characters of the command, exit
   status, duration, output size.

## 5. Open questions for the owner

1. **The SSH client.** The core has no SSH implementation. `asyncssh` as an optional extra (`noc`),
   imported lazily, with the executor reported unavailable without it — the pattern of the `snmpv3`
   extra (#444) — or another choice?
2. **Command policy.** Per access level, per vendor allow-lists (`view_only`: `show …`;
   `diagnostic`: also `ping`, `traceroute`)? A person's approval for every `operator` and `root`
   command, or a change window?
3. **Two-factor sign-in.** TOTP (RFC 6238) needs only the standard library; enrolment needs a way to
   show the shared secret (an `otpauth://` URI and the secret as text, since drawing a QR code with
   no dependency is its own small project). Next, before the executor, or after?
4. **Custodians and visibility.** Today a custodian sees the whole vault. Should a scoped editor who
   is a custodian see only the credentials of elements in their scope?
5. **What counts as a script having worked** — the architecture's second open question, unchanged.
