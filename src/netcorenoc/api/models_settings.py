"""The v0.30.0 request surface: the SNMP receiver, outgoing email, and recovery by email.

Split out of `models.py` at the 400-line module guard, as `models_decider.py` was; `models.py`
re-exports every name here by identity, so what a caller can send is still one import list (ADR
#374). Every field is bounded. Protocol names are checked against `ingest/snmpconf.py` and provider
names against `crosscutting/mail.py` by the routes, so those modules stay the one list of each.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from netcorenoc.crosscutting import auth
from netcorenoc.ingest.snmpconf import MAX_PASSPHRASE, MIN_PASSPHRASE

__all__ = [
    "CommunitiesIn",
    "EmailIn",
    "EmailTestIn",
    "ResetConfirmIn",
    "ResetRequestIn",
    "SnmpIn",
    "SnmpUserIn",
]


class SnmpUserIn(BaseModel):
    """One SNMPv3 user. A passphrase left out keeps the stored key while the protocols stay."""

    name: str = Field(min_length=1, max_length=32)
    auth: str = Field(default="SHA", max_length=16)
    priv: str = Field(default="AES-128", max_length=16)
    auth_passphrase: str | None = Field(
        default=None, min_length=MIN_PASSPHRASE, max_length=MAX_PASSPHRASE
    )
    priv_passphrase: str | None = Field(
        default=None, min_length=MIN_PASSPHRASE, max_length=MAX_PASSPHRASE
    )
    engine_id: str | None = Field(default=None, max_length=80)


class CommunitiesIn(BaseModel):
    """`any` accepts every community. Otherwise the kept entries (by id) plus the ones added."""

    any: bool = True
    keep: list[str] = Field(default_factory=list, max_length=64)
    add: list[str] = Field(default_factory=list, max_length=64)


class SnmpIn(BaseModel):
    v1: bool = True
    v2c: bool = True
    v3: bool = True
    time_window: bool = True
    communities: CommunitiesIn = Field(default_factory=CommunitiesIn)
    users: list[SnmpUserIn] = Field(default_factory=list, max_length=64)


class EmailIn(BaseModel):
    """The SMTP server. `password` left out keeps the stored one; an empty string clears it."""

    enabled: bool = True
    provider: str = Field(default="custom", max_length=32)
    host: str = Field(default="", max_length=253)
    port: int = Field(default=587, ge=1, le=65535)
    security: Literal["none", "starttls", "tls"] = "starttls"
    username: str = Field(default="", max_length=254)
    password: str | None = Field(default=None, max_length=512)
    sender: str = Field(default="", max_length=254)
    sender_name: str = Field(default="NetCoreNOC", max_length=80)
    verify_tls: bool = True
    timeout_s: float = Field(default=15.0, ge=3.0, le=60.0)
    public_url: str = Field(default="", max_length=300)
    recovery: bool = True


class EmailTestIn(BaseModel):
    to: str = Field(min_length=3, max_length=254)


class ResetRequestIn(BaseModel):
    """A username or an address; the answer is the same whether or not it names an account."""

    login: str = Field(min_length=1, max_length=254)


class ResetConfirmIn(BaseModel):
    token: str = Field(min_length=20, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    new_password: str = Field(min_length=1, max_length=auth.MAX_PASSWORD)
