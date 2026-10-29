"""The NOC agents' request surface: hiring and changing an agent, waking it, and routing cases to
it (ADRs #465-#467).

Every field is bounded here, at the edge, with the caps `migrations/0037_noc_agents.sql` holds in
its CHECKs. Which roles, icons, adapters and models exist is `engine/noc/agents.py`'s to say, and
the routes check them there — the catalogue the console and agents read is the one enforced.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AgentIn",
    "AgentPatchIn",
    "AssignIn",
    "EscalateIn",
    "FinishIn",
    "InvokeIn",
    "PauseIn",
]


class AgentIn(BaseModel):
    """An agent to hire. ``reports_to`` None: it reports to the NOC team, the people at the top."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    role: str = Field(min_length=1, max_length=20)
    title: str = Field(default="", max_length=80)
    icon: str = Field(default="bot", max_length=20)
    tier: int = Field(default=1, ge=1, le=4)
    reports_to: int | None = Field(default=None, ge=1)
    capabilities: str = Field(default="", max_length=2000)
    instructions: str = Field(default="", max_length=65536)
    adapter: Literal["webhook", "claude", "openai", "external"]
    adapter_config: dict[str, Any] = Field(default_factory=dict)
    entry: bool = False
    wake_on_assignment: bool = True
    heartbeat_s: int = Field(default=0, ge=0, le=86400)
    escalate_after_s: int | None = Field(default=None, ge=60, le=86400)
    run_timeout_s: int = Field(default=900, ge=60, le=7200)
    budget_month_cents: int = Field(default=0, ge=0, le=100_000_000)


class AgentPatchIn(BaseModel):
    """Any subset of an agent's settings, against the ``version`` the caller read."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=60)
    role: str | None = Field(default=None, min_length=1, max_length=20)
    title: str | None = Field(default=None, max_length=80)
    icon: str | None = Field(default=None, max_length=20)
    tier: int | None = Field(default=None, ge=1, le=4)
    reports_to: int | None = Field(default=None, ge=0)
    capabilities: str | None = Field(default=None, max_length=2000)
    instructions: str | None = Field(default=None, max_length=65536)
    adapter: Literal["webhook", "claude", "openai", "external"] | None = None
    adapter_config: dict[str, Any] | None = None
    entry: bool | None = None
    wake_on_assignment: bool | None = None
    heartbeat_s: int | None = Field(default=None, ge=0, le=86400)
    escalate_after_s: int | None = Field(default=None, ge=0, le=86400)
    run_timeout_s: int | None = Field(default=None, ge=60, le=7200)
    budget_month_cents: int | None = Field(default=None, ge=0, le=100_000_000)


class InvokeIn(BaseModel):
    """Wake an agent now, optionally about one case."""

    model_config = ConfigDict(extra="forbid")

    situation_id: int | None = Field(default=None, ge=1)


class PauseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=200)


class FinishIn(BaseModel):
    """An agent's report that its run is over: what it did, and whether it worked."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=8000)
    outcome: Literal["succeeded", "failed"] = "succeeded"


class AssignIn(BaseModel):
    """Route a case to an agent — or, ``agent_id`` None, to the NOC team."""

    model_config = ConfigDict(extra="forbid")

    agent_id: int | None = Field(default=None, ge=1)
    note: str = Field(default="", max_length=500)


class EscalateIn(BaseModel):
    """Send a case up the chain now, saying why."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)
