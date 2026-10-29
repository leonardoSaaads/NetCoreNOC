"""The NOC plane's routes: the capability each requires and its scope posture (ADRs #454-#467).

Split from `route_map.py` at the 400-line guard, on the seam the product now has: the broker's
routes there, the NOC's here. `route_map.py` folds both tables into `ROUTE_PERMISSIONS` and
`ROUTE_SCOPE`, which stay the one authority — nothing reads these two names but it.

**Every vault route is `unscoped`, and the reason is the same for each**: the vault's boundary is
the custodian list, not visibility scoping. A credential names a management address that need not
be an inventoried element, an admin who names a custodian is trusting them with the vault, and a
person who is not a custodian is answered the same — empty, or refused — whatever their scope.
Each entry still carries its own line, because `tests/api/test_declaration.py` reads them.

**Every case route is `scoped`**: a case is its situation, worked, and is visible exactly when the
situation is — the same predicate (`perimeter.situation_in_scope`), the same 404. **So is every
customer route** (ADR #473): a customer is visible when one of its elements is.
"""

from __future__ import annotations

from typing import Literal

NOC_ROUTE_PERMISSIONS: dict[tuple[str, str], str] = {
    ("GET", "/api/noc/secrets"): "secrets.read",
    ("POST", "/api/noc/secrets"): "secrets.write",
    ("GET", "/api/noc/secrets/{secret_id}"): "secrets.read",
    ("POST", "/api/noc/secrets/{secret_id}"): "secrets.write",
    ("POST", "/api/noc/secrets/{secret_id}/delete"): "secrets.write",
    ("POST", "/api/noc/secrets/{secret_id}/reveal"): "secrets.reveal",
    # Switching agents on or off for one credential is a change to what it may be used for.
    ("POST", "/api/noc/secrets/{secret_id}/agents"): "secrets.write",
    # A check reads no secret: it connects to the host and port the list already shows.
    ("POST", "/api/noc/secrets/{secret_id}/check"): "secrets.read",
    # The models an AI provider key may use: custodian-gated; the handler opens the key.
    ("POST", "/api/noc/secrets/{secret_id}/models"): "secrets.read",
    ("GET", "/api/noc/grants"): "secrets.grants",
    # What the caller holds in the NOC: every principal who reads cases may ask about themselves.
    ("GET", "/api/noc/access"): "cases.read",
    ("POST", "/api/noc/grants"): "secrets.grants",
    ("GET", "/api/noc/agent"): "secrets.lease",
    ("POST", "/api/noc/secrets/{secret_id}/lease"): "secrets.lease",
    ("POST", "/api/noc/leases/introspect"): "secrets.lease",
    # An agent ends its own lease; a custodian ends anyone's. The handler decides which.
    ("POST", "/api/noc/leases/{lease_id}/revoke"): "secrets.lease",
    # -- cases (ADRs #459-#461) --
    ("GET", "/api/noc/cases"): "cases.read",
    ("GET", "/api/noc/cases/kpis"): "cases.read",
    ("GET", "/api/noc/overview"): "cases.read",
    ("GET", "/api/noc/cases/{sid}"): "cases.read",
    ("POST", "/api/noc/cases/{sid}"): "cases.work",
    ("POST", "/api/noc/cases/{sid}/take"): "cases.work",
    ("POST", "/api/noc/cases/{sid}/release"): "cases.work",
    ("POST", "/api/noc/cases/{sid}/status"): "cases.work",
    ("POST", "/api/noc/cases/{sid}/resolve"): "cases.work",
    ("GET", "/api/noc/cases/{sid}/feed"): "cases.read",
    ("POST", "/api/noc/cases/{sid}/feed"): "cases.post",
    ("GET", "/api/noc/people"): "cases.work",
    # -- the executor and playbooks (ADRs #462, #463) --
    # An act on a device is a use of a lease; the handler checks the lease as introspection does.
    ("POST", "/api/noc/exec"): "secrets.lease",
    ("POST", "/api/noc/secrets/{secret_id}/hostkey"): "secrets.write",
    ("GET", "/api/noc/playbooks"): "playbooks.read",
    ("POST", "/api/noc/playbooks"): "playbooks.write",
    ("GET", "/api/noc/playbooks/{playbook_id}"): "playbooks.read",
    ("POST", "/api/noc/playbooks/{playbook_id}"): "playbooks.write",
    ("POST", "/api/noc/playbooks/{playbook_id}/enabled"): "playbooks.write",
    # -- the NOC's agents and their runs (ADRs #465-#467) --
    ("GET", "/api/noc/agents"): "agents.read",
    ("POST", "/api/noc/agents"): "agents.manage",
    ("GET", "/api/noc/agents/me"): "agents.read",
    ("GET", "/api/noc/agents/{agent_id}"): "agents.read",
    ("POST", "/api/noc/agents/{agent_id}"): "agents.manage",
    ("POST", "/api/noc/agents/{agent_id}/invoke"): "agents.run",
    ("POST", "/api/noc/agents/{agent_id}/pause"): "agents.run",
    ("POST", "/api/noc/agents/{agent_id}/resume"): "agents.run",
    ("POST", "/api/noc/agents/{agent_id}/terminate"): "agents.manage",
    ("POST", "/api/noc/agents/{agent_id}/key"): "agents.manage",
    ("GET", "/api/noc/runs/{run_id}"): "agents.read",
    # An agent reports its own run; a person with the capability ends anyone's. The handler
    # decides which.
    ("POST", "/api/noc/runs/{run_id}/finish"): "agents.run",
    ("POST", "/api/noc/cases/{sid}/assign"): "cases.work",
    ("POST", "/api/noc/cases/{sid}/escalate"): "cases.work",
    # -- the customers' books (ADR #473): read by whoever reads cases, kept by who works them;
    # the handlers decide what is a person's and what an agent's (`api/customer_view.py`). --
    ("GET", "/api/noc/customers"): "cases.read",
    ("GET", "/api/noc/customers/{customer_id}"): "cases.read",
    ("POST", "/api/noc/customers/{customer_id}"): "cases.work",
    ("POST", "/api/noc/customers/{customer_id}/memory"): "cases.work",
    ("POST", "/api/noc/customers/{customer_id}/contacts"): "cases.work",
    ("POST", "/api/noc/customers/{customer_id}/contacts/{contact_id}"): "cases.work",
    ("POST", "/api/noc/customers/{customer_id}/contacts/{contact_id}/delete"): "cases.work",
    ("POST", "/api/noc/customers/{customer_id}/docs"): "cases.work",
    ("GET", "/api/noc/customers/{customer_id}/docs/{doc_id}"): "cases.read",
    ("GET", "/api/noc/customers/{customer_id}/docs/{doc_id}/file"): "cases.read",
    ("POST", "/api/noc/customers/{customer_id}/docs/{doc_id}/delete"): "cases.work",
    ("GET", "/api/noc/circuits"): "cases.read",
    ("GET", "/api/noc/customers/{customer_id}/circuits"): "cases.read",
    ("POST", "/api/noc/customers/{customer_id}/circuits/import"): "cases.work",
    ("POST", "/api/noc/customers/{customer_id}/circuits"): "cases.work",
    ("GET", "/api/noc/customers/{customer_id}/circuits/{circuit_id}"): "cases.read",
    ("POST", "/api/noc/customers/{customer_id}/circuits/{circuit_id}"): "cases.work",
    ("POST", "/api/noc/customers/{customer_id}/circuits/{circuit_id}/notes"): "cases.work",
    ("POST", "/api/noc/customers/{customer_id}/circuits/{circuit_id}/delete"): "cases.work",
    # The MCP endpoint (ADR #466): every tool behind it is checked as its own route.
    ("POST", "/api/mcp"): "mcp.use",
}

NOC_ROUTE_SCOPE: dict[tuple[str, str], Literal["scoped", "unscoped", "admin_only"]] = {
    # The vault's list: custodian-gated, not scope-gated (module docstring); a non-custodian gets
    # an empty list and the sentence why, under any scope.
    ("GET", "/api/noc/secrets"): "unscoped",
    # Creating a credential: custodian-gated; it names a host, not an inventoried element.
    ("POST", "/api/noc/secrets"): "unscoped",
    # One credential's metadata and history: custodian-gated, the same for any scope.
    ("GET", "/api/noc/secrets/{secret_id}"): "unscoped",
    # Changing one: custodian-gated and stepped up; scope does not reach the vault.
    ("POST", "/api/noc/secrets/{secret_id}"): "unscoped",
    # Deleting one: custodian-gated and stepped up, for the same reason.
    ("POST", "/api/noc/secrets/{secret_id}/delete"): "unscoped",
    # Reading the secret: custodian-gated and stepped up; scope does not reach the vault.
    ("POST", "/api/noc/secrets/{secret_id}/reveal"): "unscoped",
    # The agent switch: custodian-gated, and stepped up to switch on.
    ("POST", "/api/noc/secrets/{secret_id}/agents"): "unscoped",
    # A reachability check: custodian-gated; it reveals what the list already shows.
    ("POST", "/api/noc/secrets/{secret_id}/check"): "unscoped",
    # An AI provider's model list: custodian-gated, asked of the provider; scope does not reach
    # the vault.
    ("POST", "/api/noc/secrets/{secret_id}/models"): "unscoped",
    ("GET", "/api/noc/grants"): "admin_only",
    # The caller's own grants: a statement about them, naming no element.
    ("GET", "/api/noc/access"): "unscoped",
    ("POST", "/api/noc/grants"): "admin_only",
    # An agent's own view: the credentials it may lease and its leases, decided by its grant and
    # each credential's switch; a caller who is not an agent gets the same empty answer anywhere.
    ("GET", "/api/noc/agent"): "unscoped",
    # Asking for a lease: decided by the agent grant and the credential's switch, never by scope.
    ("POST", "/api/noc/secrets/{secret_id}/lease"): "unscoped",
    # Checking a lease: a statement about a signed token and its row, naming no element.
    ("POST", "/api/noc/leases/introspect"): "unscoped",
    # Ending a lease: the lease's holder or a custodian, whatever their scope.
    ("POST", "/api/noc/leases/{lease_id}/revoke"): "unscoped",
    ("GET", "/api/noc/cases"): "scoped",
    ("GET", "/api/noc/cases/kpis"): "scoped",
    ("GET", "/api/noc/overview"): "scoped",
    ("GET", "/api/noc/cases/{sid}"): "scoped",
    ("POST", "/api/noc/cases/{sid}"): "scoped",
    ("POST", "/api/noc/cases/{sid}/take"): "scoped",
    ("POST", "/api/noc/cases/{sid}/release"): "scoped",
    ("POST", "/api/noc/cases/{sid}/status"): "scoped",
    ("POST", "/api/noc/cases/{sid}/resolve"): "scoped",
    ("GET", "/api/noc/cases/{sid}/feed"): "scoped",
    ("POST", "/api/noc/cases/{sid}/feed"): "scoped",
    # The accounts a `specified` post may name: people, not elements; every scope reads the same.
    ("GET", "/api/noc/people"): "unscoped",
    # Acting on a device with a lease: decided by the lease, its agent grant and the credential's
    # switch, never by scope — the same gate as asking for the lease.
    ("POST", "/api/noc/exec"): "unscoped",
    # Forgetting a pinned host key: custodian-gated and stepped up; scope does not reach the vault.
    ("POST", "/api/noc/secrets/{secret_id}/hostkey"): "unscoped",
    # Playbooks (ADR #465): a playbook names no element — it matches by vendor, trap and severity
    # and acts with the credentials agents may use — so every scope reads the same list.
    ("GET", "/api/noc/playbooks"): "unscoped",
    # Writing one: the agent grant or `secrets.grants` on top of the capability; scope does not
    # reach a tree that names no element.
    ("POST", "/api/noc/playbooks"): "unscoped",
    # One playbook and its runs: the runs name cases, each a number the reader may not open.
    ("GET", "/api/noc/playbooks/{playbook_id}"): "unscoped",
    # Replacing one: the same gate as writing it.
    ("POST", "/api/noc/playbooks/{playbook_id}"): "unscoped",
    # Switching one off is a kill switch; on, the same gate as writing it.
    ("POST", "/api/noc/playbooks/{playbook_id}/enabled"): "unscoped",
    # The agents (ADRs #465-#467): an agent is a member of the NOC's staff, not an element; every
    # scope reads the same organization. The cases each one has are listed by number.
    ("GET", "/api/noc/agents"): "unscoped",
    ("POST", "/api/noc/agents"): "admin_only",
    # An agent's own record: its identity, decided by its token, never by scope.
    ("GET", "/api/noc/agents/me"): "unscoped",
    # One agent's page: the organization's, the same for every scope.
    ("GET", "/api/noc/agents/{agent_id}"): "unscoped",
    ("POST", "/api/noc/agents/{agent_id}"): "admin_only",
    # Waking one: an agent is staff, not an element; an agent caller only below itself.
    ("POST", "/api/noc/agents/{agent_id}/invoke"): "unscoped",
    # Pausing one is a kill switch: reachable from any scope.
    ("POST", "/api/noc/agents/{agent_id}/pause"): "unscoped",
    # Resuming one: the same switch, the other way.
    ("POST", "/api/noc/agents/{agent_id}/resume"): "unscoped",
    ("POST", "/api/noc/agents/{agent_id}/terminate"): "admin_only",
    ("POST", "/api/noc/agents/{agent_id}/key"): "admin_only",
    # A run's steps: what an agent did, naming cases by number, each opened under its own scope.
    ("GET", "/api/noc/runs/{run_id}"): "unscoped",
    # Reporting a run: the agent's own, decided by its token.
    ("POST", "/api/noc/runs/{run_id}/finish"): "unscoped",
    ("POST", "/api/noc/cases/{sid}/assign"): "scoped",
    ("POST", "/api/noc/cases/{sid}/escalate"): "scoped",
    # The customers' books (ADR #473): a customer is visible when one of its elements is in the
    # caller's scope; one that is not is the same 404 as one that does not exist, and the lists
    # (customers, circuits found across them) narrow to the customers the caller may see.
    ("GET", "/api/noc/customers"): "scoped",
    ("GET", "/api/noc/customers/{customer_id}"): "scoped",
    ("POST", "/api/noc/customers/{customer_id}"): "scoped",
    ("POST", "/api/noc/customers/{customer_id}/memory"): "scoped",
    ("POST", "/api/noc/customers/{customer_id}/contacts"): "scoped",
    ("POST", "/api/noc/customers/{customer_id}/contacts/{contact_id}"): "scoped",
    ("POST", "/api/noc/customers/{customer_id}/contacts/{contact_id}/delete"): "scoped",
    ("POST", "/api/noc/customers/{customer_id}/docs"): "scoped",
    ("GET", "/api/noc/customers/{customer_id}/docs/{doc_id}"): "scoped",
    ("GET", "/api/noc/customers/{customer_id}/docs/{doc_id}/file"): "scoped",
    ("POST", "/api/noc/customers/{customer_id}/docs/{doc_id}/delete"): "scoped",
    ("GET", "/api/noc/circuits"): "scoped",
    ("GET", "/api/noc/customers/{customer_id}/circuits"): "scoped",
    ("POST", "/api/noc/customers/{customer_id}/circuits/import"): "scoped",
    ("POST", "/api/noc/customers/{customer_id}/circuits"): "scoped",
    ("GET", "/api/noc/customers/{customer_id}/circuits/{circuit_id}"): "scoped",
    ("POST", "/api/noc/customers/{customer_id}/circuits/{circuit_id}"): "scoped",
    ("POST", "/api/noc/customers/{customer_id}/circuits/{circuit_id}/notes"): "scoped",
    ("POST", "/api/noc/customers/{customer_id}/circuits/{circuit_id}/delete"): "scoped",
    # The MCP door: each tool behind it is a route answered under the caller's own scope.
    ("POST", "/api/mcp"): "unscoped",
}

assert set(NOC_ROUTE_SCOPE) == set(NOC_ROUTE_PERMISSIONS), "the two NOC tables must agree"
