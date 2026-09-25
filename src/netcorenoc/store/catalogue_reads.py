"""The trap catalogue's reads: every class with the rule that wins for it, the OID tree, the rules
(v0.22.0, items 16-18, ADR #384, #385).

The screen these serve used to be one unbounded list of OIDs. Now it is three bounded reads:

* :meth:`catalogue_classes` — a page of classes, searched and filtered **before** the page is cut,
  each with its effective name and severity and *which rule gave each* — so an operator can see
  what is winning for any class, which the brief makes a requirement.
* :meth:`catalogue_tree` — the children of one OID node, built from the classes the appliance has
  seen and the rules anyone wrote, by ARC (never by string prefix), with how many classes sit
  beneath each child. Naming a vendor branch is: pick the vendor, click down, name the node.
* :meth:`catalogue_rules` — the rules, each with how many classes it currently wins for.

Resolution is per class, not per alarm, and the class table is small (tens to low thousands), so
these resolve every class in Python and filter before paging — the filter is still the server's,
applied before anything is cut, which is the rule (F35, F38).
"""

from __future__ import annotations

from typing import Any

from netcorenoc.ingest import known_oids
from netcorenoc.store.class_rules import ClassRuleMixin, rule_ref

#: The most children a tree node answers with, busiest first. A node with more is a table column,
#: not a branch, and the search box is the better tool for it.
MAX_CHILDREN = 200


class CatalogueReadMixin(ClassRuleMixin):
    async def _classes_resolved(self) -> list[dict[str, Any]]:
        """Every class with its effective name, severity, vendor and the rule behind each."""
        cur = await self.conn.execute(
            "SELECT c.id, c.oid, l.label AS label, s.label AS declared_severity "  # nosec B608
            "FROM alarm_class c "
            + self._label_join("l", "class", "c.id")
            + self._label_join("s", "severity", "c.id")
        )
        rows = list(await cur.fetchall())
        # One GROUP BY rather than a subquery per class: `alarm` has no index on `class_id`, so a
        # correlated count would scan the active alarms once per class.
        cur = await self.conn.execute(
            "SELECT class_id, COUNT(*) FROM alarm WHERE status='active' GROUP BY class_id"
        )
        active = {int(r[0]): int(r[1]) for r in await cur.fetchall()}
        catalogue = await self.catalogue()
        out: list[dict[str, Any]] = []
        for r in rows:
            oid = str(r["oid"])
            resolved = catalogue.resolve(oid)
            standard = known_oids.trap_name(oid)
            if r["label"]:
                name, name_source = r["label"], "declared"
            elif resolved.name:
                name, name_source = resolved.name, resolved.name_rule.source  # type: ignore[union-attr]
            elif standard:
                name, name_source = standard, "standard"
            else:
                name, name_source = None, None
            if r["declared_severity"]:
                severity, severity_source = str(r["declared_severity"]), "declared"
            elif resolved.severity:
                severity, severity_source = resolved.severity, resolved.severity_rule.source  # type: ignore[union-attr]
            else:
                severity, severity_source = None, None
            out.append(
                {
                    "id": int(r["id"]),
                    "oid": oid,
                    "label": r["label"],
                    "declared_severity": r["declared_severity"],
                    "name": name,
                    "name_source": name_source,
                    "name_rule": None if r["label"] else rule_ref(resolved.name_rule),
                    "severity": severity,
                    "severity_rank": (
                        known_oids.severity_rank(severity) if severity is not None else None
                    ),
                    "severity_source": severity_source,
                    "severity_rule": (
                        None if r["declared_severity"] else rule_ref(resolved.severity_rule)
                    ),
                    "vendor": resolved.vendor or known_oids.vendor_of(oid),
                    "active": active.get(int(r["id"]), 0),
                }
            )
        return out

    async def catalogue_classes(
        self, *, q: str | None, under: str | None, limit: int, offset: int
    ) -> dict[str, Any]:
        """A page of classes, busiest first, narrowed by text and by branch before it is cut."""
        rows = await self._classes_resolved()
        if under:
            rows = [r for r in rows if known_oids.under_subtree(r["oid"], under)]
        if q:
            needle = q.lower()
            rows = [
                r
                for r in rows
                if needle in r["oid"]
                or needle in (r["name"] or "").lower()
                or needle in (r["vendor"] or "").lower()
            ]
        rows.sort(key=lambda r: (-r["active"], r["oid"]))
        return {"total": len(rows), "offset": offset, "classes": rows[offset : offset + limit]}

    async def catalogue_tree(self, node: str) -> dict[str, Any]:
        """The children of `node`, by the next ARC, with how much sits under each."""
        catalogue = await self.catalogue()
        cur = await self.conn.execute("SELECT oid FROM alarm_class")
        class_oids = [str(r[0]) for r in await cur.fetchall()]
        known = set(class_oids)
        depth = len(node.split("."))
        children: dict[str, dict[str, Any]] = {}

        def child_of(oid: str) -> str | None:
            if oid == node or not known_oids.under_subtree(oid, node):
                return None
            return ".".join(oid.split(".")[: depth + 1])

        for oid in class_oids:
            child = child_of(oid)
            if child is not None:
                entry = children.setdefault(child, {"oid": child, "classes": 0, "rules": 0})
                entry["classes"] += 1
        for rule in catalogue.rules:
            child = child_of(rule.oid)
            if child is not None:
                entry = children.setdefault(child, {"oid": child, "classes": 0, "rules": 0})
                entry["rules"] += 1
        at_root = node + "." == known_oids.ENTERPRISE_PREFIX
        if at_root:
            # Every vendor the appliance can name, not only the ones it has heard from: a customer
            # with a MIB list names a vendor's branch before its first trap arrives (item 18).
            for number in known_oids.IANA_ENTERPRISES:
                children.setdefault(
                    f"{node}.{number}", {"oid": f"{node}.{number}", "classes": 0, "rules": 0}
                )
        for entry in children.values():
            resolved = catalogue.resolve(entry["oid"])
            entry["vendor"] = known_oids.vendor_of(entry["oid"]) if at_root else None
            entry["arc"] = entry["oid"].rsplit(".", 1)[1]
            entry["name"] = resolved.name or known_oids.trap_name(entry["oid"])
            entry["is_class"] = entry["oid"] in known
        ranked = sorted(
            children.values(),
            key=lambda e: (-e["classes"], -e["rules"], e["vendor"] or "", int(e["arc"])),
        )
        here = [rule.public() for rule in catalogue.rules if rule.oid == node]
        resolved_here = catalogue.resolve(node)
        return {
            "node": node,
            "vendor": known_oids.vendor_of(node),
            "name": resolved_here.name or known_oids.trap_name(node),
            "severity": resolved_here.severity,
            "rules": here,
            "classes_beneath": sum(1 for oid in class_oids if known_oids.under_subtree(oid, node)),
            "children": ranked[:MAX_CHILDREN],
            "more_children": max(0, len(ranked) - MAX_CHILDREN),
        }

    async def catalogue_rules(
        self, *, source: str | None, limit: int, offset: int
    ) -> dict[str, Any]:
        """The rules, most specific first, each with how many classes it currently wins for."""
        catalogue = await self.catalogue()
        classes = await self._classes_resolved()
        wins: dict[int, int] = {}
        for row in classes:
            for key in ("name_rule", "severity_rule"):
                ref = row[key]
                if ref is not None:
                    wins[ref["id"]] = wins.get(ref["id"], 0) + 1
        rules = [r for r in catalogue.rules if source is None or r.source == source]
        rules.sort(key=lambda r: (r.source, -len(r.oid.split(".")), r.oid))
        page = [{**r.public(), "wins": wins.get(r.id, 0)} for r in rules[offset : offset + limit]]
        return {"total": len(rules), "offset": offset, "rules": page}
