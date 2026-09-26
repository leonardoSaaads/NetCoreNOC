"""The alarm-class catalogue: rules on an OID or a branch, resolved at read time (v0.22.0).

`0023` explains why the table exists. This module owns the one decision that matters — **which rule
wins for a given class** — as a pure object, so every read model asks the same question the same
way and a test can drive the precedence without a database (ADR #385):

  1. the per-class declaration (`label` kind 'class' / 'severity') — resolved by the caller, which
     already joins it;
  2. the rules matching the class's OID, **most specific first**: an exact rule, then the deepest
     subtree, then shallower ones; at the same node `declared` before `imported`;
  3. **the built-in trap pack** (v0.24.0, ADR #400) — the vendor's own name for the exact OID and
     a published default severity (`ingest/trappack.py`). Consulted only when no declared or
     imported rule at ANY depth answers, so an operator's branch rule always beats it;
  4. what the appliance knows without being told — a standard trap's bundled name, the severity
     the trap carried or learned;
  5. nothing.

A name and a severity are resolved independently: a branch rule that grades without naming leaves
the name to whatever comes next, which is what an operator writing *"everything under here is
critical"* means.

**Matching is on arc boundaries.** `known_oids.ancestors` walks a class's OID by dropping whole
arcs, so a rule on `…2011.1.2` is found for `…2011.1.2.1` and can never be found for `…2011.1.12`
— the string-prefix mistake has no code path to happen on.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal

from netcorenoc.ingest import known_oids, trappack
from netcorenoc.store.base import StoreBase

RuleSource = Literal["declared", "imported"]

#: The source a built-in pack entry is reported under. Never a row of `class_rule`: it cannot be
#: listed, counted, withdrawn or written, only outranked.
BUILTIN = "builtin"

#: The two sources, in the order they win at the same node.
SOURCES: tuple[RuleSource, ...] = ("declared", "imported")


@dataclass(frozen=True)
class Rule:
    """One row of `class_rule`."""

    id: int
    oid: str
    subtree: bool
    name: str | None
    severity: str | None
    vendor: str | None
    source: str
    origin: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "oid": self.oid,
            "subtree": self.subtree,
            "name": self.name,
            "severity": self.severity,
            "severity_rank": (
                known_oids.severity_rank(self.severity) if self.severity is not None else None
            ),
            "vendor": self.vendor,
            "source": self.source,
            "origin": self.origin,
        }


@dataclass(frozen=True)
class Resolved:
    """What the rules say about one OID: the winning name and severity, and which rule gave each."""

    name: str | None = None
    name_rule: Rule | None = None
    severity: str | None = None
    severity_rule: Rule | None = None
    vendor: str | None = None

    @property
    def severity_rank(self) -> int | None:
        return known_oids.severity_rank(self.severity) if self.severity is not None else None

    @property
    def is_default(self) -> bool:
        """The severity is the built-in pack's default — the lowest rung of all."""
        return self.severity_rule is not None and self.severity_rule.source == BUILTIN

    def rank_over(self, placed: int | None) -> int | None:
        """The rank to use given what the ingest path placed. A rule wins; the built-in default
        yields to the trap's own word or a learned field, and fills in only when neither exists."""
        if self.severity_rule is None or (self.is_default and placed is not None):
            return placed
        return self.severity_rank


NOTHING = Resolved()


class Catalogue:
    """Every rule, indexed by node, and the precedence that picks among the ones that match."""

    def __init__(self, rules: list[Rule], builtin: trappack.Pack | None = None) -> None:
        self.rules = rules
        self.builtin = builtin
        self._at: dict[str, list[Rule]] = {}
        for rule in rules:
            self._at.setdefault(rule.oid, []).append(rule)
        self._memo: dict[str, Resolved] = {}

    def matching(self, oid: str) -> list[Rule]:
        """Every rule that applies to `oid`, most specific first — the order they win in."""
        out: list[Rule] = []
        for depth, node in enumerate(known_oids.ancestors(oid)):
            here = [r for r in self._at.get(node, ()) if r.subtree or depth == 0]
            # At one node: exact before subtree, declared before imported.
            here.sort(key=lambda r: (r.subtree, SOURCES.index(r.source)))
            out.extend(here)
        return out

    def resolve(self, oid: str) -> Resolved:
        """The winning name, severity and vendor for `oid`. Memoised: classes repeat per request."""
        hit = self._memo.get(oid)
        if hit is not None:
            return hit
        rules = self.matching(oid)
        named = next((r for r in rules if r.name), None)
        graded = next((r for r in rules if r.severity), None)
        vendor = next((r.vendor for r in rules if r.vendor), None)
        entry = self.builtin.notifications.get(oid) if self.builtin is not None else None
        if entry is not None and (named is None or graded is None):
            # The pack answers only what no rule did, and only for its exact OID.
            stub = Rule(
                id=0,
                oid=oid,
                subtree=False,
                name=entry.name,
                severity=entry.severity,
                vendor=None,
                source=BUILTIN,
                origin=entry.module,
            )
            named = named or stub
            graded = graded or (stub if entry.severity else None)
        out = (
            NOTHING
            if named is None and graded is None and vendor is None
            else Resolved(
                name=named.name if named else None,
                name_rule=named,
                severity=graded.severity if graded else None,
                severity_rule=graded,
                vendor=vendor,
            )
        )
        self._memo[oid] = out
        return out


def rule_ref(rule: Rule | None) -> dict[str, Any] | None:
    """The part of a winning rule a screen shows: which node, how, and on whose word."""
    if rule is None:
        return None
    if rule.source == BUILTIN:
        # No row to open or withdraw: the reference says where the name came from instead.
        return {
            "id": None,
            "oid": rule.oid,
            "subtree": False,
            "source": BUILTIN,
            "origin": rule.origin,
        }
    return {"id": rule.id, "oid": rule.oid, "subtree": rule.subtree, "source": rule.source}


class ClassRuleMixin(StoreBase):
    """Reads and writes of `class_rule`. The catalogue is cached until the next write."""

    _catalogue_cache: Catalogue | None = None

    async def catalogue(self) -> Catalogue:
        """The current catalogue, read once and reused until a rule changes."""
        if not self._has_class_rules:
            return Catalogue([], trappack.pack())
        if self._catalogue_cache is None:
            cur = await self.conn.execute(
                "SELECT id, oid, subtree, name, severity, vendor, source, origin FROM class_rule "
                "ORDER BY id"
            )
            self._catalogue_cache = Catalogue(
                [
                    Rule(
                        id=int(r[0]),
                        oid=str(r[1]),
                        subtree=bool(r[2]),
                        name=r[3],
                        severity=r[4],
                        vendor=r[5],
                        source=str(r[6]),
                        origin=r[7],
                    )
                    for r in await cur.fetchall()
                ],
                trappack.pack(),
            )
        return self._catalogue_cache

    def _forget_catalogue(self) -> None:
        self._catalogue_cache = None

    async def put_class_rule(
        self,
        *,
        oid: str,
        subtree: bool,
        name: str | None,
        severity: str | None,
        vendor: str | None,
        source: RuleSource,
        origin: str | None,
        actor: str | None,
        now: float | None = None,
    ) -> tuple[int, bool]:
        """Insert or replace the rule at `(oid, subtree, source)`. Returns `(id, created)`."""
        at = time.time() if now is None else now
        cur = await self.conn.execute(
            "SELECT id FROM class_rule WHERE oid=? AND subtree=? AND source=?",
            (oid, int(subtree), source),
        )
        row = await cur.fetchone()
        self._forget_catalogue()
        if row is not None:
            await self.conn.execute(
                "UPDATE class_rule SET name=?, severity=?, vendor=?, origin=?, created_by=?, "
                "updated_at=? WHERE id=?",
                (name, severity, vendor, origin, actor, at, int(row[0])),
            )
            return int(row[0]), False
        cur = await self.conn.execute(
            "INSERT INTO class_rule (oid, subtree, name, severity, vendor, source, origin, "
            "created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "RETURNING id",
            (oid, int(subtree), name, severity, vendor, source, origin, actor, at, at),
        )
        created = await cur.fetchone()
        assert created is not None
        return int(created[0]), True

    async def delete_class_rule(self, rule_id: int) -> Rule | None:
        """Withdraw one rule. Returns what it was, or None if there was no such rule."""
        catalogue = await self.catalogue()
        found = next((r for r in catalogue.rules if r.id == rule_id), None)
        if found is None:
            return None
        await self.conn.execute("DELETE FROM class_rule WHERE id=?", (rule_id,))
        self._forget_catalogue()
        return found

    async def delete_imported_rules(self) -> int:
        """Withdraw every imported row — the undo for an import, which declared rules survive."""
        cur = await self.conn.execute("DELETE FROM class_rule WHERE source='imported'")
        self._forget_catalogue()
        return int(cur.rowcount or 0)

    async def class_severity_declarations(self) -> dict[int, str]:
        """`{class_id: token}`: the per-class severity declarations, the rung above every rule."""
        cur = await self.conn.execute(
            "SELECT c.id, s.label FROM alarm_class c "  # nosec B608 - `_label_join` literal
            + self._label_join("s", "severity", "c.id")
            + "WHERE s.label IS NOT NULL"
        )
        return {int(r[0]): str(r[1]) for r in await cur.fetchall()}
