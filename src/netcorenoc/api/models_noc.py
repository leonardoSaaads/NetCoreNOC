"""The NOC plane's request surface: the credential vault, its custodians and agents, and leases.

Every field is bounded. Which kinds, fields, access levels and protocol versions exist is
`engine/noc/kinds.py`'s to say, and the routes check them there, so the catalogue the console and
agents read is the one the server enforces (ADR #455). A step-up rides inside the request it
authorises rather than opening a window of its own: nothing outlives the act it was given for.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from netcorenoc.crosscutting import auth
from netcorenoc.engine.noc import lease

__all__ = [
    "AgentAccessIn",
    "GrantIn",
    "IntrospectIn",
    "LeaseIn",
    "SecretIn",
    "StepUpIn",
    "StepUpOnly",
]


class StepUpIn(BaseModel):
    """The person's own username and password again, and — once two-factor sign-in exists — a
    one-time code from their authenticator app (ADR #456). Never stored, logged or audited."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=auth.MAX_PASSWORD)
    otp: str | None = Field(default=None, max_length=12)


class SecretIn(BaseModel):
    """A credential to create or replace. ``fields`` is sealed whole; everything else is the
    metadata it is bound to. ``fields`` left out on an edit keeps the sealed credential."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    kind: str = Field(min_length=1, max_length=32)
    access_level: Literal["view_only", "diagnostic", "operator", "root"]
    host: str = Field(min_length=1, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    protocol_version: str = Field(default="", max_length=32)
    monitor: bool = True
    fields: dict[str, str] | None = Field(default=None, max_length=8)
    stepup: StepUpIn | None = None


class StepUpOnly(BaseModel):
    """An act whose only input is the person's confirmation: reveal, delete."""

    model_config = ConfigDict(extra="forbid")

    stepup: StepUpIn


class AgentAccessIn(BaseModel):
    """Agents on or off for one credential. Switching on takes a step-up; off never does — the
    off switch is the one a worried operator must reach without remembering a password."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    stepup: StepUpIn | None = None


class GrantIn(BaseModel):
    """Name a custodian (``user:<id>``, an editor) or an agent (``token:<id>``), or withdraw one."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(pattern=r"^(user|token):[0-9]{1,12}$")
    role: Literal["custodian", "agent"]
    enabled: bool
    stepup: StepUpIn | None = None


class LeaseIn(BaseModel):
    """How long the agent needs the credential for, within the bounds `lease.py` sets."""

    model_config = ConfigDict(extra="forbid")

    ttl_s: int = Field(default=lease.DEFAULT_TTL_S, ge=lease.MIN_TTL_S, le=lease.MAX_TTL_S)
    purpose: str = Field(default="", max_length=200)


class IntrospectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lease: str = Field(min_length=1, max_length=lease.MAX_LEASE_CHARS)
