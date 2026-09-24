"""Rows to predicates: building the per-trap index from what the store read (v0.21.0).

The store returns rows; this gives them meaning. That split is the layer rule rather than a
preference — `store` is the data layer and `engine` is above it, so a store module importing
`engine.mw.rules` would be an upward import, and `tests/test_layers.py` refuses one. The honest
consequence is that the *compile* lives here, with the types it produces, and the store keeps only
the three statements that fetch the rows.

Runs once per maintenance tick, off the ingest path, under the lock `maintenance()` already holds.
Nothing here is reachable from `decide()`.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.engine.mw import index as mw_index
from netcorenoc.engine.mw.rules import MatchOn, OidRule, SeverityRule, SlotRule, TargetRules


def from_rows(rows: list[dict[str, Any]]) -> mw_index.WindowIndex:
    """`store.window_index_rows()` output -> the immutable structure the ingest path reads."""
    return mw_index.compile_windows(
        [
            mw_index.build(
                window_id=int(row["id"]),
                name=str(row["name"]),
                organization_id=int(row["organization_id"]),
                starts_at=float(row["starts_at"]),
                ends_at=float(row["ends_at"]),
                patch_s=float(row["patch_s"]),
                ledger_enabled=bool(row["ledger_enabled"]),
                targets={
                    ne_id: compile_rules([r for r in row["rules"] if int(r["ne_id"]) == ne_id])
                    for ne_id in row["targets"]
                },
            )
            for row in rows
        ]
    )


def compile_rules(rows: list[dict[str, Any]]) -> TargetRules:
    """Stored rows to the frozen predicate objects the check reads.

    A row whose `kind` this release does not know is **dropped, not raised on**. The index is
    rebuilt from the database every five seconds, and a store written by a newer appliance — a
    downgrade, a restored backup — must not be able to stop the check being built at all. A
    dropped rule collects less, which is D4's default and the safe direction.
    """
    severity: list[SeverityRule] = []
    oid: list[OidRule] = []
    slot: list[SlotRule] = []
    for row in rows:
        kind = str(row["kind"])
        if kind == "severity" and row["severity_rank"] is not None:
            severity.append(SeverityRule(at_or_above_rank=int(row["severity_rank"])))
        elif kind == "oid" and row["oid_root"]:
            oid.append(OidRule(root=str(row["oid_root"]), match_on=match_on(row["match_on"])))
        elif kind == "slot" and row["slot_starts_at"] is not None:
            slot.append(
                SlotRule(
                    starts_at=float(row["slot_starts_at"]),
                    ends_at=float(row["slot_ends_at"] or row["slot_starts_at"]),
                )
            )
    return TargetRules(severity=tuple(severity), oid=tuple(oid), slot=tuple(slot))


def match_on(raw: Any) -> MatchOn:
    """`trap` or `varbind`, defaulting to `varbind`. **Never a third thing.**

    The default matters because it decides what an unreadable row does, and `varbind` is the
    narrower reading of the maintainer's own example — his subtree is shaped like a table column
    carried in varbinds rather than like a notification OID (see `MatchOn` in `rules.py`).
    """
    return "trap" if str(raw) == "trap" else "varbind"
