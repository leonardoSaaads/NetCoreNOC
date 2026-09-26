"""Filters that narrow an activity read, applied in SQL and ANDed with the scope (v0.23.0, #392).

The Timeline gained filters by organization, by trap OID (a branch, on arc boundaries) and by
situation — the last one being how an operator reads a situation's chain of events for an RFO.
Each is a WHERE clause beside the scope predicate, so a filter can only narrow what the caller may
already see (F35, F38); an element or situation outside the scope answers what a nonexistent one
answers: nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: A dotted-number OID, as the catalogue stores them. Anything else is refused by the route.
OID = re.compile(r"^[0-9]+(\.[0-9]+){1,127}$")


@dataclass(frozen=True)
class Narrow:
    """The optional filters. Every field None is the unfiltered read."""

    organization_id: int | None = None
    oid: str | None = None
    situation_id: int | None = None

    def clauses(self) -> tuple[list[str], list[Any]]:
        """WHERE fragments over `alarm a`, and their bound values. Literals only in the SQL."""
        out: list[str] = []
        args: list[Any] = []
        if self.organization_id is not None:
            out.append("a.ne_id IN (SELECT id FROM ne WHERE organization_id=?)")
            args.append(self.organization_id)
        if self.oid is not None:
            if not OID.match(self.oid):
                raise ValueError(f"not a dotted OID: {self.oid!r}")
            # Arc boundaries (ADR #384): the node itself, or anything below `node.`. The OID is
            # digits and dots only, so LIKE has no wildcard to misread in it.
            out.append(
                "a.class_id IN (SELECT id FROM alarm_class WHERE oid=? OR oid LIKE ? || '.%')"
            )
            args.extend((self.oid, self.oid))
        if self.situation_id is not None:
            out.append("a.id IN (SELECT alarm_id FROM situation_alarm WHERE situation_id=?)")
            args.append(self.situation_id)
        return out, args


def narrowed(
    where: str, args: tuple[Any, ...], narrow: Narrow | None
) -> tuple[str, tuple[Any, ...]]:
    """`where`/`args` from `_timeline_scope`, with the filters ANDed on."""
    if narrow is None:
        return where, args
    extra, bound = narrow.clauses()
    if not extra:
        return where, args
    return " AND ".join([where, *extra]), (*args, *bound)
