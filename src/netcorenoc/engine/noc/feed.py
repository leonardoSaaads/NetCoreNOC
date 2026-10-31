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

**A sent customer update** (ADR #478) is a post to `all`: the customer update as the customer
reads it (:func:`customer_update`) — the same fields the NOC keeps, under their labels, and when
the NOC will update again. It is what reaches a customer with access to the console; email is not
connected yet.

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
from datetime import UTC, datetime
from typing import Any

from netcorenoc.engine.noc.cases import FIELD_LIMITS, PENDING

__all__ = [
    "MAX_BODY",
    "MAX_READERS",
    "VISIBILITIES",
    "Reader",
    "author_label",
    "customer_update",
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


#: The customer update's fields in the order a customer reads them (`cases.FIELD_LIMITS` labels).
CU_ORDER = (
    "description",
    "executive_summary",
    "current_analysis",
    "noc_next_step",
    "customer_next_step",
)


def customer_update(case: Mapping[str, Any]) -> str:
    """The post a sent customer update is: its title, each field under its label, and when the
    NOC updates again — the longest fields cut first when it would not fit one post."""
    when = datetime.fromtimestamp(float(case["next_update_at"]), UTC)
    head = f"Customer update — {str(case['title']).strip()}"
    tail = f"Next update: {when:%Y-%m-%d %H:%M} UTC"
    parts = {k: str(case.get(k) or "").strip() for k in CU_ORDER}
    excess = sum(len(v) for v in parts.values()) - (MAX_BODY - len(head) - len(tail) - 200)
    for name in sorted(parts, key=lambda k: -len(parts[k])):
        if excess <= 0:
            break
        cut = min(excess + 1, len(parts[name]))
        parts[name] = parts[name][: len(parts[name]) - cut] + "…"
        excess -= cut - 1
    body = [f"{FIELD_LIMITS[k][0]}:\n{v}" for k, v in parts.items() if v]
    return "\n\n".join([head, *body, tail])[:MAX_BODY]
