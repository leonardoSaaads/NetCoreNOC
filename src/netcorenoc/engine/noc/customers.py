"""What a customer's book may hold: its workbook's contacts, its memory, its limits (ADR #473).

A NOC serves several customers, and each wants to be served its own way. What the NOC keeps about
one is four things, each kept by whoever knows it:

* **Instructions** — how the customer wants the NOC to act: how a case is named, who is called and
  when, what is never touched without its word. The customer's words, so **people** keep them;
  an agent reads them before it acts on the customer's network and never rewrites them.
* **Memory** — what the NOC learned working that network: the port that flaps every winter, the
  provider that answers only by phone at night. **Agents keep it current** as they work, and may
  condense it when it grows (``replace``), or add one dated line (``append``).
* **The workbook** — who to call, company by company (the customer, its fibre provider, a vendor),
  each company's people in the order they are called (``escalation_level``, 1 first).
* **Documents and circuits** — what the customer handed over for troubleshooting
  (`doctext.py`) and how each of its circuits runs, hop by hop (`circuits.py`).

Pure: no I/O.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from typing import Any

__all__ = [
    "DOC_CATEGORIES",
    "MAX_CONTACTS",
    "MAX_DOCS",
    "MAX_DOCS_BYTES",
    "MAX_INSTRUCTIONS",
    "MAX_MEMORY",
    "PARTY_KINDS",
    "CustomerError",
    "appended",
    "contact",
]

PARTY_KINDS: dict[str, str] = {
    "customer": "The customer",
    "provider": "Provider",
    "vendor": "Vendor",
    "other": "Other",
}
DOC_CATEGORIES: dict[str, str] = {
    "topology": "Topology",
    "site": "Sites and shelves",
    "equipment": "Equipment",
    "procedure": "Procedures",
    "other": "Other",
}
MAX_INSTRUCTIONS = 65536
MAX_MEMORY = 32768
MAX_CONTACTS = 300
MAX_DOCS = 100
MAX_DOCS_BYTES = 256 * 1024 * 1024
#: The longest line an agent may append to a memory.
MAX_MEMORY_LINE = 1000

_LIMITS: dict[str, int] = {
    "party": 120,
    "name": 120,
    "role": 120,
    "email": 254,
    "phone": 60,
    "hours": 120,
    "notes": 2000,
}
_EMAIL = re.compile(r"^[^@\s<>]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
_PHONE = re.compile(r"^[0-9+()./ \-]{3,40}((x|ext\.?)\s?[0-9]{1,6})?$", re.I)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class CustomerError(ValueError):
    """A write to a customer's book that is refused; the message names the field."""


def _line(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if _CONTROL.search(text) or (field != "notes" and "\n" in text):
        raise CustomerError(f"{field}: one line of text")
    if len(text) > _LIMITS[field]:
        raise CustomerError(f"{field}: at most {_LIMITS[field]} characters")
    return text


def contact(fields: Mapping[str, Any]) -> dict[str, Any]:
    """A workbook entry as it is stored, or the first thing wrong with it."""
    out = {name: _line(fields.get(name), name) for name in _LIMITS}
    if not out["party"]:
        raise CustomerError("party: the company this person is at (the customer, Zayo…)")
    if not out["name"]:
        raise CustomerError("name: required (a person, or a desk such as 'NOC hotline')")
    kind = str(fields.get("party_kind") or "customer")
    if kind not in PARTY_KINDS:
        raise CustomerError(f"party_kind: one of {', '.join(PARTY_KINDS)}")
    level = fields.get("escalation_level", 1)
    if not isinstance(level, int) or isinstance(level, bool) or not 1 <= level <= 9:
        raise CustomerError("escalation_level: 1 (called first) to 9")
    if out["email"] and not _EMAIL.fullmatch(out["email"]):
        raise CustomerError("email: an address such as noc@example.com")
    if out["phone"] and not _PHONE.fullmatch(out["phone"]):
        raise CustomerError("phone: digits, spaces, + ( ) - . and an optional extension")
    if not out["email"] and not out["phone"]:
        raise CustomerError("email or phone: a contact needs one way to be reached")
    return {**out, "party_kind": kind, "escalation_level": level}


def appended(memory: str, line: str, actor: str, now: float | None = None) -> str:
    """The memory with one dated line added at its end, or the refusal when it would not fit."""
    text = " ".join(str(line or "").split())
    if not text:
        raise CustomerError("memory: the line to add is empty")
    if len(text) > MAX_MEMORY_LINE:
        raise CustomerError(f"memory: one line is at most {MAX_MEMORY_LINE} characters")
    day = time.strftime("%Y-%m-%d", time.gmtime(time.time() if now is None else now))
    entry = f"- {day} ({actor}): {text}"
    out = f"{memory.rstrip()}\n{entry}" if memory.strip() else entry
    if len(out) > MAX_MEMORY:
        raise CustomerError(
            f"memory: full ({MAX_MEMORY} characters) — condense it (mode replace) before adding"
        )
    return out
