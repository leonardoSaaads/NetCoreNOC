"""Minimal event model.

A trap is reduced to the only facts the learning needs: which device spoke (source IP),
which alarm class fired (trap OID as an opaque token), which instance it concerns, when,
and the raw varbinds for the operator's benefit.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

Fingerprint = tuple[str, str, str]
"""(device IP, trap OID, instance) — the deduplication identity of an alarm."""


class Varbind(BaseModel):
    """One decoded varbind, pretty-printed; kept verbatim for auditability."""

    oid: str
    kind: str
    value: str


class TrapEvent(BaseModel):
    """A successfully parsed SNMPv2c trap.

    ``community_tag`` is an HMAC of the SNMPv2c community string (F4): the raw community —
    functionally a password — is never carried, stored, or logged; only this opaque
    grouping tag survives parsing.
    """

    device: str
    trap_oid: str
    instance: str = ""
    ts: float
    varbinds: list[Varbind] = Field(default_factory=list)
    community_tag: str = ""

    @property
    def fingerprint(self) -> Fingerprint:
        return (self.device, self.trap_oid, self.instance)


class QuarantinedPacket(BaseModel):
    """A packet that failed defensive parsing; stored for inspection, never fatal.

    F4: ``raw`` holds the packet with the community octets blanked (``sanitized=True``);
    if the community could not be located in a malformed packet, ``raw`` is empty and only
    the metadata (``sha256``, ``length``, ``first8``) is kept — never the raw payload.
    """

    source: str
    raw: bytes
    reason: str
    ts: float
    sha256: str = ""
    length: int = 0
    first8: str = ""
    sanitized: bool = False
