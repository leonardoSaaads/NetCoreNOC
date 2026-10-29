"""An agent's identity: the service token it acts with, and what the appliance keeps sealed for it
(ADR #465).

Hiring an agent creates a service token (role `editor`, named as the agent, so every act is
recorded under the agent's name) and grants it `agent` — the grant that lets it lease and use
credentials. What happens to the token's value depends on how the agent is woken:

* `webhook` and `external` — the agent runs elsewhere and presents the token itself: it is shown
  **once**, to the admin who hired it, and only its hash is kept. A `webhook` agent also gets a
  signing key, shown once and kept sealed, so it can tell the appliance's wake-ups from anyone
  else's;
* `claude` and `openai` — the appliance acts as the agent, so it keeps the token **sealed** (the
  secret key's AES-256-GCM, bound to the agent) and never shows it to anyone. Its provider's API
  key is a vault credential of that provider's kind a custodian let agents use (`check_key`).

A new key (rotation, or an adapter that changed between "elsewhere" and "here") retires the old
token — revoked, its leases ended — before the new one is made.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from netcorenoc.crosscutting import auth, secretbox
from netcorenoc.engine.noc import agents, providers

if TYPE_CHECKING:
    from netcorenoc.store import Store

__all__ = ["Identity", "binding", "check_key", "issue", "retire"]

ROLE = "editor"


@dataclass(frozen=True)
class Identity:
    token_id: int
    token: str | None  # shown once: None when the appliance keeps it sealed
    webhook_key: str | None  # shown once


def binding(agent_id: int, what: str) -> str:
    """The seal's associated data: a value opens only for the agent and use it was made for."""
    return f"noc-agent:{agent_id}:{what}"


async def issue(
    store: Store, agent_id: int, name: str, adapter: str, now: float, actor: str
) -> Identity:
    """A token, its grant, and the sealed values for ``adapter``; the caller holds the write
    transaction."""
    if await store.noc_token_name_taken(name):
        raise HTTPException(
            status_code=409, detail=f"a service token is already called {name!r}: rename one"
        )
    value = auth.new_session_token()
    token_id = await store.create_token(auth.hash_token(value), name, ROLE, actor, now)
    await store.set_token_purpose(token_id, f"NOC agent #{agent_id}")
    await store.noc_set_grant(f"token:{token_id}", "agent", True, now, actor)
    box = secretbox.current()
    here = adapter in agents.IN_PROCESS
    sealed = box.seal(binding(agent_id, "token"), {"token": value}) if here else None
    key = secrets.token_urlsafe(32) if adapter == "webhook" else None
    key_sealed = None if key is None else box.seal(binding(agent_id, "webhook"), {"key": key})
    await store.noc_set_agent_identity(
        agent_id,
        token_id=token_id,
        token_sealed=sealed,
        webhook_key_sealed=key_sealed,
        now=now,
        actor=actor,
    )
    return Identity(token_id, None if here else value, key)


async def retire(store: Store, token_id: int | None, now: float, actor: str) -> None:
    """The old token: revoked, its grant withdrawn, its leases ended, its name freed."""
    if token_id is None:
        return
    ref = f"token:{token_id}"
    await store.noc_revoke_subject_leases(ref, now, actor)
    await store.noc_set_grant(ref, "agent", False, now, actor)
    await store.noc_retire_token(token_id, now)


async def check_key(store: Store, config: dict[str, Any]) -> None:
    """The provider key an in-process agent is configured with: a credential of its provider's
    kind (a Claude agent also takes an older generic API token) that agents may use."""
    if "secret_id" not in config:
        return
    provider = providers.PROVIDERS.get(str(config.get("provider") or "anthropic"))
    wanted = {"api_token"} if provider is None or provider.id == "anthropic" else set()
    if provider is not None:
        wanted.add(provider.kind)
    secret = await store.noc_get_secret(int(config["secret_id"]))
    if secret is None or secret["kind"] not in wanted:
        label = provider.label if provider else "this provider"
        raise HTTPException(400, f"adapter_config.secret_id: no such {label} key in the vault")
    if not secret["agent_access"]:
        raise HTTPException(
            400, "adapter_config.secret_id: a custodian has not let agents use that credential"
        )
