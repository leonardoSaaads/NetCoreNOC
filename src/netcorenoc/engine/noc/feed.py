"""Who may read a case feed's post, and how a case reads to someone below editor (ADR #461).

Three visibilities, chosen per post by its author:

* **`internal`** — the NOC: whoever may work cases (`cases.work`), people and agents alike. What an
  agent found on a device, a hypothesis, a hand-off note. Never a customer.
* **`specified`** — its author, the people it names, and admins. A word to one person.
* **`all`** — everyone who may read the case, the customer included. Written to be read outside.

**What a reader below editor is shown** follows the product's two rules for that role
(`crosscutting/shaping/fields.py`): a device address is shown as its network, and another person
is not named — a post reads as from "NOC engineer", "NOC agent" or "Operator", never from a
username. Free text is coarsened by pattern, not by word, because a person writes `10.0.0.1,` and
`(10.0.0.1)` as readily as `10.0.0.1`.

**A person posting on a pending case puts it back in the NOC queue** (`requeues`): the customer
answered, or someone told the NOC something it has to read, and nobody would otherwise look until
the next update. An agent's post does not — an agent writing is the NOC itself.

Pure.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from netcorenoc.engine.noc.cases import PENDING

__all__ = [
    "MAX_BODY",
    "MAX_READERS",
    "VISIBILITIES",
    "Reader",
    "author_label",
    "mask_addresses",
    "requeues",
    "unknown_readers",
    "visible",
]

VISIBILITIES: dict[str, str] = {
    "internal": "Internal — the NOC only (people and agents who work cases)",
    "specified": "Specified people (and admins)",
    "all": "All with access to the case, the customer included",
}
MAX_BODY = 8000
#: The most people one `specified` post may name.
MAX_READERS = 20

#: Candidates only; each is confirmed by `ipaddress` before anything is replaced. Both patterns are
#: single character classes under a bounded repeat: linear, whatever the input.
_V4 = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])")
_V6 = re.compile(r"(?<![0-9A-Fa-f:])[0-9A-Fa-f:]{2,39}(?![0-9A-Fa-f:])")


@dataclass(frozen=True)
class Reader:
    """Who is reading: enough to decide each post without another query."""

    ref: str | None
    user_id: int | None
    role: str
    works: bool  # holds `cases.work`: the NOC
    admin: bool


def visible(post: Mapping[str, Any], reader: Reader) -> bool:
    visibility = post["visibility"]
    if visibility == "all":
        return True
    if visibility == "internal":
        return reader.works
    return (
        reader.admin
        or (reader.ref is not None and post["author_ref"] == reader.ref)
        or (reader.user_id is not None and reader.user_id in post.get("readers", ()))
    )


def author_label(kind: str, role: str | None) -> str:
    """What a reader who may not see people is told about an author."""
    if kind == "system":
        return "NetCoreNOC"
    if kind == "agent":
        return "NOC agent"
    return "NOC engineer" if role in ("editor", "admin") else "Operator"


def requeues(case: Mapping[str, Any], *, author_kind: str, author_ref: str | None) -> bool:
    """Does this post send the case back to the NOC queue? A person's post on a pending case, unless
    the person is its owner."""
    return (
        author_kind == "person"
        and case["status"] in PENDING
        and (author_ref is None or author_ref != case.get("owner_ref"))
    )


def _coarsen(match: re.Match[str]) -> str:
    text = match.group(0)
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return text
    prefix = 24 if address.version == 4 else 48
    return str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))


def mask_addresses(text: str) -> str:
    """Every IPv4 or IPv6 address in ``text`` replaced by its network (/24, /48)."""
    if not text:
        return text
    text = _V4.sub(_coarsen, text)
    return _V6.sub(lambda m: _coarsen(m) if m.group(0).count(":") >= 2 else m.group(0), text)


def unknown_readers(readers: Collection[int], known: Collection[int]) -> list[int]:
    """The named ids that are not an enabled account (a 400 for the caller)."""
    return sorted(set(readers) - set(known))
