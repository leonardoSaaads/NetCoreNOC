"""Request bodies for brokers (ADR #494): create, change, move an element, a proxy's batch.

A broker's intake is chosen when it is created and never changes: an element's identity is
(broker, address), and a network that moves from its own port to a remote proxy is a new broker
rather than an old one whose elements change meaning under it.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from netcorenoc.engine.operate import routing

__all__ = [
    "MAX_PROXY_RECORDS",
    "MAX_RECORD_CHARS",
    "BrokerIn",
    "BrokerUpdateIn",
    "ElementBrokerIn",
    "ProxyBatchIn",
    "check_update",
]

#: Records in one proxy batch. The body limit (1 MiB) bounds it first for large traps; this bounds
#: the work one request may ask for when the traps are small.
MAX_PROXY_RECORDS = 2000
#: One record, base64: a 64 KiB datagram with its header, encoded.
MAX_RECORD_CHARS = 90_000


def _networks(value: list[str]) -> list[str]:
    try:
        return list(routing.canonical_networks(value))
    except routing.RoutingError as exc:
        raise ValueError(str(exc)) from exc


class _BrokerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    #: The customer whose network it is (`GET /api/organizations`).
    organization_id: int = Field(ge=1)
    description: str = Field(default="", max_length=2000)
    #: A port broker's own UDP port; the others arrive by range, or through their proxy.
    udp_port: int | None = Field(default=None, ge=1024, le=65535)
    #: CIDRs that claim sources on the standard port; the longest prefix wins.
    networks: list[str] = Field(default_factory=list, max_length=routing.MAX_NETWORKS)

    @field_validator("name", "description")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        return value.strip()

    _canonical = field_validator("networks")(_networks)


class BrokerIn(_BrokerBody):
    """`POST /api/brokers`. **Own port + ranges is the default**, as the console offers it."""

    intake: Literal["port", "proxy", "cidr"] = "port"

    @model_validator(mode="after")
    def _by_intake(self) -> BrokerIn:
        _check(self.intake, self.udp_port, self.networks)
        return self


class BrokerUpdateIn(_BrokerBody):
    """`POST /api/brokers/{id}`. The whole broker as the admin sees it; the intake is fixed."""

    enabled: bool = True


class ElementBrokerIn(BaseModel):
    """`POST /api/elements/{ne_id}/broker`: put one element in another broker."""

    model_config = ConfigDict(extra="forbid")
    broker_id: int = Field(ge=1)


class ProxyBatchIn(BaseModel):
    """`POST /api/brokers/ingest`: records ``first..last`` a remote proxy forwards, in order.

    Each record is the proxy's own spool record (``R``), base64. The numbers let the server skip
    what it already spooled when the proxy sends a batch again because our answer did not reach it.
    """

    model_config = ConfigDict(extra="forbid")
    #: The proxy's spool: its numbers start at 1 again when a proxy is reinstalled.
    stream: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    first: int = Field(ge=1)
    last: int = Field(ge=0)
    version: str = Field(default="", max_length=40)
    #: Empty is the proxy's heartbeat (``first`` = ``last`` + 1): it is alive, with nothing to send.
    records: list[str] = Field(default_factory=list, max_length=MAX_PROXY_RECORDS)

    @field_validator("records")
    @classmethod
    def _bounded(cls, value: list[str]) -> list[str]:
        if any(len(record) > MAX_RECORD_CHARS for record in value):
            raise ValueError(f"a record is longer than {MAX_RECORD_CHARS} characters")
        return value

    @model_validator(mode="after")
    def _numbered(self) -> ProxyBatchIn:
        if self.last - self.first + 1 != len(self.records):
            raise ValueError("records must number exactly first..last")
        return self


def _check(intake: str, udp_port: int | None, networks: list[str]) -> None:
    """What each intake requires; the same rules the table's CHECKs hold, said in words."""
    if intake == "port" and udp_port is None:
        raise ValueError("a broker on its own port needs udp_port")
    if intake != "port" and udp_port is not None:
        raise ValueError("udp_port belongs to a broker on its own port (intake 'port')")
    if intake == "cidr" and not networks:
        raise ValueError("a ranges-only broker needs at least one network")
    if intake == "proxy" and networks:
        raise ValueError("a proxy broker's traps come through its proxy; it takes no ranges")


def check_update(intake: str, body: BrokerUpdateIn) -> None:
    """The intake's rules for a change, which only the stored broker can say. ValueError if not."""
    _check(intake, body.udp_port, body.networks)


class ScopeSubjectIn(BaseModel):
    """Which customers one person or token serves (ADR #499): `org:<id>` selectors in the
    visibility policy. ``organizations`` None removes them, so the subject sees every customer
    again (unless its other selectors or its role's say otherwise)."""

    subject: str = Field(max_length=20, pattern=r"^(user|token):[1-9][0-9]{0,9}$")
    organizations: list[Annotated[int, Field(ge=1)]] | None = Field(default=None, max_length=200)
    # ADR #508: the brokers and the NOC customers, each None for every one and [] for none. When
    # either is sent, the two say the whole of the subject's reach and `organizations` is ignored.
    brokers: list[Annotated[int, Field(ge=1)]] | None = Field(default=None, max_length=500)
    customers: list[Annotated[int, Field(ge=1)]] | None = Field(default=None, max_length=500)
    note: str = Field(default="", max_length=500)

    def planes_sent(self) -> bool:
        """Whether this body speaks the two planes (ADR #508) rather than customers only."""
        return bool({"brokers", "customers"} & self.model_fields_set)
