"""What a credential is: its kinds, fields, access levels and binding (ADRs #455, #471).

A credential is a **kind** (SSH with a password, SSH with a key, SFTP, SNMP v1/v2c or v3, an API
token, SMTP, or something else — or, in the ``ai`` group, one AI provider's API key,
`providers.py`), a set of **fields** — all sealed, some masked even after a step-up
because they are the secret itself — a **host and port**, a **protocol version**, and the **access
level** it grants on the device. The catalogue is served to the console and to agents
(:func:`describe`) rather than restated in either, so the form and the validation cannot disagree.

**The binding** (:func:`binding`) is the plain metadata a credential is only valid for: reference,
kind, host, port, protocol version, access level. It is the associated data of both layers of the
seal (`crosscutting/secretbox.py`), so changing any of them is a re-seal under step-up, never a row
edit — a credential cannot be pointed at another machine by rewriting its host.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass

from netcorenoc.engine.noc import providers

__all__ = [
    "ACCESS_LEVELS",
    "KINDS",
    "CredentialError",
    "Field",
    "Kind",
    "binding",
    "clean_fields",
    "clean_host",
    "describe",
]


class CredentialError(ValueError):
    """A credential that is not one this catalogue accepts. The message names the field, never
    its value."""


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    secret: bool = False  # the secret itself: masked even in a revealed credential until shown
    required: bool = True
    multiline: bool = False
    choices: tuple[str, ...] = ()
    max_len: int = 256


@dataclass(frozen=True)
class Kind:
    id: str
    label: str
    port: int
    transport: str  # "tcp" | "udp"
    #: How reachability is checked (`probe.py`): "ssh" and "smtp" read the protocol's greeting,
    #: "tcp" connects, None cannot be checked without using the credential (SNMP is UDP).
    check: str | None
    fields: tuple[Field, ...]
    versions: tuple[str, ...] = ()
    #: At least one of these fields must be given (SFTP: a password or a key).
    one_of: tuple[str, ...] = ()
    #: ``device`` — what reaches equipment; ``ai`` — an AI provider's key (ADR #471).
    group: str = "device"


_USER = Field("username", "Username", max_len=128)
_PASSWORD = Field("password", "Password", secret=True)
_KEY = Field(
    "private_key", "Private key (PEM or OpenSSH)", secret=True, multiline=True, max_len=16_384
)
_AUTH = ("MD5", "SHA", "SHA-224", "SHA-256", "SHA-384", "SHA-512")
_PRIV = ("none", "DES", "3DES", "AES-128", "AES-192", "AES-256")

KINDS: dict[str, Kind] = {
    k.id: k
    for k in (
        Kind(
            "ssh_password",
            "SSH — username and password",
            22,
            "tcp",
            "ssh",
            (_USER, _PASSWORD),
            ("SSH-2",),
        ),
        Kind(
            "ssh_key",
            "SSH — private key",
            22,
            "tcp",
            "ssh",
            (_USER, _KEY, Field("passphrase", "Key passphrase", secret=True, required=False)),
            ("SSH-2",),
        ),
        Kind(
            "sftp",
            "SFTP",
            22,
            "tcp",
            "ssh",
            (
                _USER,
                Field("password", "Password", secret=True, required=False),
                Field(
                    "private_key",
                    _KEY.label,
                    secret=True,
                    required=False,
                    multiline=True,
                    max_len=16_384,
                ),
            ),
            ("SSH-2",),
            one_of=("password", "private_key"),
        ),
        Kind(
            "snmp_community",
            "SNMP v1/v2c — community",
            161,
            "udp",
            None,
            (Field("community", "Community", secret=True, max_len=64),),
            ("v2c", "v1"),
        ),
        Kind(
            "snmp_v3",
            "SNMPv3 — USM user",
            161,
            "udp",
            None,
            (
                Field("username", "USM user", max_len=32),
                Field("auth_protocol", "Authentication", choices=_AUTH),
                Field("auth_key", "Authentication key", secret=True),
                Field("priv_protocol", "Privacy", choices=_PRIV),
                Field("priv_key", "Privacy key", secret=True, required=False),
            ),
            ("v3",),
        ),
        Kind(
            "api_token",
            "API token",
            443,
            "tcp",
            "tcp",
            (
                Field("token", "Token", secret=True, max_len=4096),
                Field("header", "Header", required=False, max_len=64),
            ),
            ("HTTPS", "HTTP"),
        ),
        Kind(
            "smtp", "SMTP", 587, "tcp", "smtp", (_USER, _PASSWORD), ("STARTTLS", "SMTPS", "plain")
        ),
        Kind(
            "other",
            "Other",
            22,
            "tcp",
            "tcp",
            (
                Field("username", "Username", required=False, max_len=128),
                Field("secret", "Secret", secret=True, max_len=4096),
                Field("notes", "Notes", required=False, multiline=True, max_len=2000),
            ),
        ),
        # ADR #471: one kind per AI provider, its one field the API key. Checked by connecting to
        # its API's port, which says the endpoint answers, not that the key works.
        *(
            Kind(
                p.kind,
                f"{p.label} — {p.product}",
                p.port,
                "tcp",
                "tcp",
                (Field("api_key", "API key", secret=True, required=p.key_required, max_len=4096),),
                ("HTTP", "HTTPS") if p.own_host else (p.scheme,),
                group="ai",
            )
            for p in providers.PROVIDERS.values()
        ),
    )
}

#: What a credential lets its holder do on the device, least first. An agent's lease carries it,
#: so whatever executes on the device later can refuse a command the level does not allow.
ACCESS_LEVELS: dict[str, str] = {
    "view_only": "View only — read state, change nothing",
    "diagnostic": "Diagnostic — read state and run tests (ping, traceroute, show)",
    "operator": "Operator — change service configuration",
    "root": "Root / admin — everything, including the device's own access",
}

_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?$"
)


def clean_host(host: str) -> str:
    """An address or a hostname, nothing else: no scheme, no path, no port, no spaces."""
    host = host.strip()
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    if not _HOSTNAME.fullmatch(host):
        raise CredentialError("host: an IP address or a hostname, without a scheme or a port")
    return host.lower().rstrip(".")


def clean_fields(
    kind_id: str, fields: dict[str, str], *, version: str
) -> tuple[Kind, dict[str, str], str]:
    """The kind, its fields as they will be sealed, and the protocol version — or the first thing
    wrong with them. Unknown fields are refused rather than kept: nothing is sealed that the form
    and the reveal do not know how to show."""
    kind = KINDS.get(kind_id)
    if kind is None:
        raise CredentialError(f"kind: one of {', '.join(KINDS)}")
    known = {f.name for f in kind.fields}
    extra = sorted(set(fields) - known)
    if extra:
        raise CredentialError(f"{extra[0]}: not a field of {kind.label}")
    cleaned: dict[str, str] = {}
    for field in kind.fields:
        value = str(fields.get(field.name) or "")
        value = value if field.multiline or field.secret else value.strip()
        if not value:
            if field.required:
                raise CredentialError(f"{field.name}: required for {kind.label}")
            continue
        if len(value) > field.max_len:
            raise CredentialError(f"{field.name}: at most {field.max_len} characters")
        if "\x00" in value:
            raise CredentialError(f"{field.name}: may not contain a NUL character")
        if field.choices and value not in field.choices:
            raise CredentialError(f"{field.name}: one of {', '.join(field.choices)}")
        cleaned[field.name] = value
    if kind.one_of and not any(cleaned.get(name) for name in kind.one_of):
        raise CredentialError(f"{kind.one_of[0]}: give {' or '.join(kind.one_of)}")
    if kind.id == "snmp_v3" and cleaned["priv_protocol"] != "none" and not cleaned.get("priv_key"):
        raise CredentialError("priv_key: required when privacy is on")
    version = version.strip() or (kind.versions[0] if kind.versions else "")
    if kind.versions and version not in kind.versions:
        raise CredentialError(f"protocol_version: one of {', '.join(kind.versions)}")
    if len(version) > 32:
        raise CredentialError("protocol_version: at most 32 characters")
    return kind, cleaned, version


def binding(
    ref: str, kind: str, host: str, port: int, protocol_version: str, access_level: str
) -> str:
    """The metadata a credential is valid for, canonical, as the seal's associated data."""
    return json.dumps(
        {
            "access": access_level,
            "host": host,
            "kind": kind,
            "port": int(port),
            "ref": ref,
            "v": protocol_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def describe() -> dict[str, object]:
    """The catalogue as the console's form and an agent's tool schema read it."""
    return {
        "kinds": [
            {
                "id": k.id,
                "label": k.label,
                "port": k.port,
                "transport": k.transport,
                "checkable": k.check is not None,
                "versions": list(k.versions),
                "one_of": list(k.one_of),
                "group": k.group,
                "fields": [
                    {
                        "name": f.name,
                        "label": f.label,
                        "secret": f.secret,
                        "required": f.required,
                        "multiline": f.multiline,
                        "choices": list(f.choices),
                        "max_len": f.max_len,
                    }
                    for f in k.fields
                ],
            }
            for k in KINDS.values()
        ],
        "access_levels": [{"id": key, "label": label} for key, label in ACCESS_LEVELS.items()],
        "providers": providers.describe(),
    }
