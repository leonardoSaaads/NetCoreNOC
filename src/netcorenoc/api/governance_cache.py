"""The per-request cache of the two stored governance documents.

One noun: *the policy the perimeter reads*. It is not itself a decision — capability resolution is
`rbac.resolve_capabilities` and scope resolution is `shaping.visible_nes`, both called from
`perimeter.py` — so it lives beside the boundary rather than inside it
(`MODULE-ARCHITECTURE.md` §2, DECISIONS #85).
"""

from __future__ import annotations

from typing import Any

from netcorenoc import rbac, shaping


class GovernancePolicies:
    """Loads the two stored governance policies for the request path, with change invalidation.

    Both policies are read **per request** so a change lands on the very next request with no
    restart. Only the two-row `governance_active` pointer table is read every time; a document is
    re-parsed only when its id differs from the one already held, so the parse cost is paid once
    per policy version rather than once per request.

    When nothing is configured — the default, and the state of every upgraded appliance — both
    accessors return ``None`` and the resolvers fall through to the compiled ceiling and to full
    visibility. That is v0.6.0 exactly, and it costs one query over an empty table.

    A document that will not parse is **not** an error here. It is recorded as malformed, surfaced
    through :meth:`warnings`, queued once for a ``governance.fallback`` audit row, and handed to the
    resolver, which applies the fail-safe for its kind — the ceiling for capabilities, deny for
    scope (DECISIONS #55). Raising on the authorization path would turn a bad row into an outage.
    """

    @property
    def scope_id(self) -> int | None:
        """The active scope policy's identity, or ``None`` when nothing is configured.

        v0.8.0 records this on every label (§5.5). `None` is meaningful rather than missing: it is
        the unconfigured appliance, where every principal sees everything, and a label made under
        it is a statement about the whole situation.
        """
        return self._scope_id

    def __init__(self, store: Any) -> None:
        self._store = store
        self._capability_id: int | None = None
        self._capability: rbac.CapabilityPolicy | None = None
        self._scope_id: int | None = None
        self._scope: shaping.ScopePolicy | None = None
        # (kind, reason) pairs awaiting a `governance.fallback` audit row. Queued when a *new*
        # malformed policy version is first parsed, so a persistent bad policy is recorded once,
        # not once per request.
        self.pending_fallbacks: list[tuple[str, str]] = []

    async def load(self) -> None:
        """Refresh both policies. Caller holds ``store.lock``."""
        active = await self._store.active_governance_ids()
        await self._load_kind("rbac", active.get("rbac"))
        await self._load_kind("scope", active.get("scope"))

    async def _load_kind(self, kind: str, policy_id: int | None) -> None:
        current = self._capability_id if kind == "rbac" else self._scope_id
        if policy_id == current:
            return  # unchanged since the last parse
        document = ""
        if policy_id is not None:
            row = await self._store.get_governance_policy(policy_id)
            # A pointer to a missing row cannot happen through the FK, but treat it as malformed
            # rather than as "no policy": silently widening on a broken pointer would be fail-open.
            document = str(row["document"]) if row is not None else "<missing>"
        if kind == "rbac":
            self._capability_id = policy_id
            self._capability = rbac.parse_capability_policy(document) if policy_id else None
            malformed = self._capability is not None and self._capability.malformed
            reason = self._capability.reason if self._capability is not None else ""
        else:
            self._scope_id = policy_id
            self._scope = shaping.parse_scope_policy(document) if policy_id else None
            malformed = self._scope is not None and self._scope.malformed
            reason = self._scope.reason if self._scope is not None else ""
        if malformed:
            self.pending_fallbacks.append((kind, reason))

    @property
    def capability(self) -> rbac.CapabilityPolicy | None:
        return self._capability

    @property
    def scope(self) -> shaping.ScopePolicy | None:
        return self._scope

    def warnings(self) -> list[str]:
        """Persistent operator warnings for a policy that is not doing what its author intended."""
        out: list[str] = []
        if self._capability is not None and self._capability.malformed:
            out.append(
                f"The stored capability policy could not be read ({self._capability.reason}); "
                "authorization has fallen back to the built-in role permissions. Fix or clear it "
                "under Governance — no principal has gained anything."
            )
        if self._scope is not None and self._scope.malformed:
            out.append(
                f"The stored visibility scope could not be read ({self._scope.reason}); viewers "
                "and editors are seeing nothing until it is fixed or cleared under Governance. "
                "Admins are never scoped, so this is repairable."
            )
        return out
