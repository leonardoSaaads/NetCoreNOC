"""The request surface of the NOC's customers: instructions, memory, workbook, circuits (ADR #473).

Every field is bounded here, at the edge, with the database's caps. ``version``, when sent, must
be the current one: a 409 says someone else changed it since it was read.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CircuitImportIn",
    "CircuitIn",
    "CircuitNotesIn",
    "ContactIn",
    "HopIn",
    "InstructionsIn",
    "MemoryIn",
]


class InstructionsIn(BaseModel):
    """The customer's instructions, whole: how it wants the NOC to act."""

    model_config = ConfigDict(extra="forbid")

    instructions: str = Field(max_length=65536)
    version: int | None = Field(default=None, ge=0)


class MemoryIn(BaseModel):
    """What the NOC learned about this customer's network. ``append`` adds one dated line (at
    most 1000 characters) at the end; ``replace`` rewrites the whole memory — to condense it.
    ``version`` is the memory's own (``memory_version``), apart from the instructions'."""

    model_config = ConfigDict(extra="forbid")

    memory: str = Field(max_length=32768)
    mode: Literal["append", "replace"] = "append"
    version: int | None = Field(default=None, ge=0)


class ContactIn(BaseModel):
    """One workbook entry: a person (or a desk) at a company, and when to call them."""

    model_config = ConfigDict(extra="forbid")

    party: str = Field(max_length=120, description="The company: the customer, Zayo, a vendor…")
    party_kind: Literal["customer", "provider", "vendor", "other"] = "customer"
    escalation_level: int = Field(default=1, ge=1, le=9, description="1 is called first")
    name: str = Field(max_length=120)
    role: str = Field(default="", max_length=120)
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=60)
    hours: str = Field(default="", max_length=120, description="When: 24x7, business hours…")
    notes: str = Field(default="", max_length=2000)
    version: int | None = Field(default=None, ge=1)


class HopIn(BaseModel):
    """One hop of a circuit's path: where it is, what it is, and its coordinates."""

    model_config = ConfigDict(extra="forbid")

    site: str = Field(default="", max_length=120, description="SILA PoP, QATAR…")
    segment: str = Field(default="", max_length=120, description="Client Port, MEETS ODF…")
    fields: dict[str, str] = Field(
        default_factory=dict, max_length=30, description="Shelf, Slot, Port, Mode, ODF Tx/Rx…"
    )


class CircuitIn(BaseModel):
    """A circuit, whole: its designation, ends, service and its path hop by hop."""

    model_config = ConfigDict(extra="forbid")

    designation: str = Field(max_length=200)
    a_end: str = Field(default="", max_length=120)
    z_end: str = Field(default="", max_length=120)
    bandwidth: str = Field(default="", max_length=40)
    service: str = Field(default="", max_length=80)
    status: str = Field(default="", max_length=40)
    owner: str = Field(default="", max_length=120)
    provider: str = Field(default="", max_length=120)
    rfs_date: str = Field(default="", max_length=40)
    cease_date: str = Field(default="", max_length=40)
    attributes: dict[str, str] = Field(default_factory=dict, max_length=60)
    path: list[HopIn] = Field(default_factory=list, max_length=60)
    notes: str = Field(default="", max_length=8000)
    version: int | None = Field(default=None, ge=1)


class CircuitNotesIn(BaseModel):
    """What the NOC learned about one circuit. ``append`` adds one dated line; ``replace``
    rewrites the notes."""

    model_config = ConfigDict(extra="forbid")

    notes: str = Field(max_length=8000)
    mode: Literal["append", "replace"] = "append"
    version: int | None = Field(default=None, ge=1)


class CircuitImportIn(BaseModel):
    """A sheet of circuits: pasted as tab- or comma-separated text, or a document already uploaded
    (an Excel workbook, CSV or TSV) and the sheet to read. ``apply`` false (the default) only says
    what the sheet holds; true stores it — a circuit with the same designation is replaced, its
    notes kept."""

    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, max_length=900_000)
    doc_id: int | None = Field(default=None, ge=1)
    sheet: int = Field(default=0, ge=0, le=200)
    apply: bool = False
