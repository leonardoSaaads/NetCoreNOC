"""The NOC plane's routes: the capability each requires and its scope posture (ADRs #454-#463).

Split from `route_map.py` at the 400-line guard, on the seam the product now has: the broker's
routes there, the NOC's here. `route_map.py` folds both tables into `ROUTE_PERMISSIONS` and
`ROUTE_SCOPE`, which stay the one authority — nothing reads these two names but it.

**Every vault route is `unscoped`, and the reason is the same for each**: the vault's boundary is
the custodian list, not visibility scoping. A credential names a management address that need not
be an inventoried element, an admin who names a custodian is trusting them with the vault, and a
person who is not a custodian is answered the same — empty, or refused — whatever their scope.
Each entry still carries its own line, because `tests/api/test_declaration.py` reads them.

**Every case route is `scoped`**: a case is its situation, worked, and is visible exactly when the
situation is — the same predicate (`perimeter.situation_in_scope`), the same 404.
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
    ("GET", "/api/noc/grants"): "secrets.grants",
    ("POST", "/api/noc/grants"): "secrets.grants",
    ("GET", "/api/noc/agent"): "secrets.lease",
    ("POST", "/api/noc/secrets/{secret_id}/lease"): "secrets.lease",
    ("POST", "/api/noc/leases/introspect"): "secrets.lease",
    # An agent ends its own lease; a custodian ends anyone's. The handler decides which.
    ("POST", "/api/noc/leases/{lease_id}/revoke"): "secrets.lease",
    # -- cases (ADRs #459-#461) --
    ("GET", "/api/noc/cases"): "cases.read",
    ("GET", "/api/noc/cases/kpis"): "cases.read",
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
    ("GET", "/api/noc/grants"): "admin_only",
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
    ("GET", "/api/noc/playbooks"): "admin_only",
    ("POST", "/api/noc/playbooks"): "admin_only",
    ("GET", "/api/noc/playbooks/{playbook_id}"): "admin_only",
    ("POST", "/api/noc/playbooks/{playbook_id}"): "admin_only",
    ("POST", "/api/noc/playbooks/{playbook_id}/enabled"): "admin_only",
}

assert set(NOC_ROUTE_SCOPE) == set(NOC_ROUTE_PERMISSIONS), "the two NOC tables must agree"
