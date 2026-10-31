"""The request surface of NOC cases and their feed (ADRs #459-#461).

Every field is bounded here, at the edge, with the same caps the database's CHECKs hold. A time is
accepted as epoch seconds or as an ISO 8601 instant **with** its offset — an agent writes the
second more naturally than the first, and an instant without an offset names no instant.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "CaseFieldsIn",
    "PlaybookEnabledIn",
    "PlaybookIn",
    "PostIn",
    "ReleaseIn",
    "ResolveIn",
    "StatusIn",
]

#: The most organizations one update may set a summary for.
MAX_SUMMARIES = 50


def _instant(value: float | datetime | None) -> float | None:
    if value is None or isinstance(value, float | int):
        return None if value is None else float(value)
    if value.tzinfo is None:
        raise ValueError("an ISO 8601 time needs its offset (Z or +hh:mm)")
    return value.timestamp()


class CaseFieldsIn(BaseModel):
    """Any subset of the customer update; what is left out is left as it is. ``summaries`` maps an
    organization's id to its own executive summary ('' removes it). ``version``, when sent, must be
    the case's current one: a 409 says someone else changed it since you read it."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    executive_summary: str | None = Field(default=None, max_length=200)
    current_analysis: str | None = Field(default=None, max_length=8000)
    noc_next_step: str | None = Field(default=None, max_length=4000)
    customer_next_step: str | None = Field(default=None, max_length=4000)
    next_update_at: float | datetime | None = None
    priority: Literal["critical", "major", "minor"] | None = None
    summaries: dict[int, str] | None = Field(default=None, max_length=MAX_SUMMARIES)
    version: int | None = Field(default=None, ge=1)

    _when = field_validator("next_update_at")(_instant)


#: The pending statuses, spelled out so the schema an agent reads over MCP lists them: with a bare
#: string an agent learnt the vocabulary only from the 400 its first guess earned (ADR #474).
#: `tests/api/test_noc_cases.py` holds this equal to `engine.noc.cases.PENDING`.
PendingStatus = Literal[
    "pending_customer",
    "pending_fiber_provider",
    "pending_maintenance",
    "pending_dispatch",
    "pending_device_access",
    "pending_connectivity",
    "pending_vendor",
    "pending_site_access",
    "pending_field_tech",
    "pending_rca",
    "pending_monitoring",
]


class StatusIn(BaseModel):
    """Move a case to a pending status — with its next update, if not already set."""

    model_config = ConfigDict(extra="forbid")

    status: PendingStatus = Field(
        description=(
            "What the case waits on: the customer, the carrier or fibre provider, a maintenance "
            "window, a dispatch, access to the device, connectivity (no ping, SNMP or SSH), the "
            "equipment vendor, site access, a technician on site, a root cause analysis — or "
            "monitoring: the fault is quiet and the NOC watches it for stability."
        )
    )
    next_update_at: float | datetime | None = None
    note: str = Field(default="", max_length=2000)
    version: int | None = Field(default=None, ge=1)

    _when = field_validator("next_update_at")(_instant)


class ResolveIn(BaseModel):
    """Resolve a case. ``alarms_verified_clear`` states that the alarms were checked on the device
    and are no longer active: only then are the situation's traps cleared (the clear trap never
    arrived). Without it the situation is closed and its alarms are left as they are."""

    model_config = ConfigDict(extra="forbid")

    cause: str = Field(min_length=1, max_length=40)
    solution: str = Field(min_length=1, max_length=8000)
    alarms_verified_clear: bool = False
    version: int | None = Field(default=None, ge=1)


class ReleaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(default="", max_length=2000)


class PostIn(BaseModel):
    """A post on the case feed. ``readers`` names the people a `specified` post is for."""

    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=8000)
    visibility: Literal["internal", "specified", "all"]
    readers: list[int] = Field(default_factory=list, max_length=20)


class SendUpdateIn(BaseModel):
    """Send the saved customer update — as of ``version``, when given (ADR #478)."""

    model_config = ConfigDict(extra="forbid")

    version: int | None = Field(
        default=None,
        description="The case version you read; a newer one is a 409, so what is sent is what "
        "you saw.",
    )


class PlaybookIn(BaseModel):
    """A playbook to store or replace: the tree as a JSON object (`engine/noc/playbook.py` is the
    authority on its shape), and the case it was learned from."""

    model_config = ConfigDict(extra="forbid")

    document: dict[str, Any]
    source_case: int | None = Field(default=None, ge=1)


class PlaybookEnabledIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
