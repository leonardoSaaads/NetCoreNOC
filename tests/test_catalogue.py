"""The Trap catalogue: which rule names or grades a class, and what a rule may never do (v0.22.0).

ADR #385 fixes the precedence — the per-class declaration, then rules most specific first (exact,
deeper subtree, shallower; declared before imported at one node), then what the trap carried or the
appliance learned, then *unplaced*. Everything here drives `Catalogue` directly or the real routes;
nothing asserts a string of copy.

The two properties the brief names as lines that must stay:

* **Matching is on arc boundaries.** A rule on `…2011.1.2` covers `…2011.1.2.1` and never
  `…2011.1.12`. `test_a_branch_rule_stops_at_an_arc_boundary` is the guard, and its injection
  (a `startswith(root)` without the dot) turns it red.
* **Imported data is not evidence.** A rule changes what a class is CALLED and how it is GRADED on
  screen; it never reaches a `dataset_*` table, a promotion record or a grouping.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.ingest import known_oids
from netcorenoc.store import Store
from netcorenoc.store.class_rules import Catalogue, Rule

import authutil
import util

HUAWEI = "1.3.6.1.4.1.2011"
BASE = 1_700_000_000.0


def _rule(
    rule_id: int,
    oid: str,
    *,
    subtree: bool = True,
    name: str | None = None,
    severity: str | None = None,
    source: str = "declared",
) -> Rule:
    return Rule(
        id=rule_id,
        oid=oid,
        subtree=subtree,
        name=name,
        severity=severity,
        vendor=None,
        source=source,
    )


# --- the matching rule, pure ---------------------------------------------------------------------


def test_a_branch_rule_stops_at_an_arc_boundary() -> None:
    """`…1.2` covers `…1.2` and `…1.2.x`; it does not cover `…1.12` or `…1.20.3`.

    The control is the first pair: without it, a matcher that matched nothing would pass the rest.
    """
    catalogue = Catalogue([_rule(1, f"{HUAWEI}.1.2", name="branch")])
    assert catalogue.resolve(f"{HUAWEI}.1.2").name == "branch"
    assert catalogue.resolve(f"{HUAWEI}.1.2.1.7").name == "branch"
    for outside in (f"{HUAWEI}.1.12", f"{HUAWEI}.1.20.3", f"{HUAWEI}.1", f"{HUAWEI}.1.21"):
        assert catalogue.resolve(outside).name is None, outside


def test_under_subtree_and_ancestors_agree_on_arcs() -> None:
    """The two helpers every matcher in the tree uses, pinned on the same boundary."""
    assert known_oids.under_subtree("1.3.6.1.4.1.9.1", "1.3.6.1.4.1.9")
    assert known_oids.under_subtree("1.3.6.1.4.1.9", "1.3.6.1.4.1.9")
    assert not known_oids.under_subtree("1.3.6.1.4.1.99", "1.3.6.1.4.1.9")
    assert list(known_oids.ancestors("1.3.6.1.4")) == ["1.3.6.1.4", "1.3.6.1", "1.3.6", "1.3", "1"]


def test_an_exact_rule_only_matches_its_own_oid() -> None:
    catalogue = Catalogue([_rule(1, f"{HUAWEI}.5", subtree=False, name="exact")])
    assert catalogue.resolve(f"{HUAWEI}.5").name == "exact"
    assert catalogue.resolve(f"{HUAWEI}.5.1").name is None


def test_the_most_specific_rule_wins() -> None:
    """Exact beats a subtree at the same node; a deeper subtree beats a shallower one."""
    catalogue = Catalogue(
        [
            _rule(1, HUAWEI, name="vendor", severity="minor"),
            _rule(2, f"{HUAWEI}.2", name="branch"),
            _rule(3, f"{HUAWEI}.2.7", subtree=False, name="exact"),
            _rule(4, f"{HUAWEI}.2.7", name="at the node"),
        ]
    )
    assert catalogue.resolve(f"{HUAWEI}.2.7").name == "exact"
    assert catalogue.resolve(f"{HUAWEI}.2.7.1").name == "at the node"
    assert catalogue.resolve(f"{HUAWEI}.2.8").name == "branch"
    assert catalogue.resolve(f"{HUAWEI}.9").name == "vendor"
    # A severity nobody closer gave comes from further up — resolved independently of the name.
    assert catalogue.resolve(f"{HUAWEI}.2.7").severity == "minor"


def test_declared_beats_imported_at_the_same_node_and_imported_fills_gaps() -> None:
    catalogue = Catalogue(
        [
            _rule(1, f"{HUAWEI}.3", subtree=False, name="imported", source="imported"),
            _rule(2, f"{HUAWEI}.3", subtree=False, severity="major", source="declared"),
        ]
    )
    resolved = catalogue.resolve(f"{HUAWEI}.3")
    assert resolved.severity == "major" and resolved.severity_rule is not None
    assert resolved.severity_rule.source == "declared"
    # The declared rule named nothing, so the imported name stands.
    assert resolved.name == "imported"


def test_an_imported_exact_row_beats_a_declared_branch_rule() -> None:
    """Specificity first, source second — measured live on the lab and pinned here (ADR #385)."""
    catalogue = Catalogue(
        [
            _rule(1, f"{HUAWEI}.6", name="branch", source="declared"),
            _rule(2, f"{HUAWEI}.6.1", subtree=False, name="row", source="imported"),
        ]
    )
    assert catalogue.resolve(f"{HUAWEI}.6.1").name == "row"


def test_nothing_matching_resolves_to_nothing() -> None:
    resolved = Catalogue([_rule(1, HUAWEI, name="x")]).resolve("1.3.6.1.4.1.9.9.1")
    assert resolved.name is None and resolved.severity is None and resolved.severity_rank is None


# --- the routes ---------------------------------------------------------------------------------


async def _tables(store: Store, prefix: str) -> dict[str, int]:
    async with store.lock:
        cur = await store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE ?", (f"{prefix}%",)
        )
        names = [str(r[0]) for r in await cur.fetchall()]
        counts = {}
        for name in names:
            cur = await store.conn.execute(f'SELECT COUNT(*) FROM "{name}"')  # nosec B608
            row = await cur.fetchone()
            counts[name] = int(row[0]) if row else 0
    return counts


async def _situations(store: Store) -> list[dict[str, Any]]:
    async with store.lock:
        cur = await store.conn.execute("SELECT id, status FROM situation ORDER BY id")
        return [dict(r) for r in await cur.fetchall()]


async def test_a_rule_names_every_class_beneath_it_and_withdrawing_it_undoes_that(
    store: Store,
) -> None:
    engine, queue, app = await authutil.make_env(store)
    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", BASE))
    editor = await authutil.client_as(app, "editor")
    try:
        page = (await editor.get("/api/catalogue", params={"limit": 200})).json()
        assert page["classes"], "the corpus produced no classes"
        oid = page["classes"][0]["oid"]
        parent = oid.rsplit(".", 1)[0]
        made = await editor.post(
            "/api/catalogue/rules",
            json={"oid": parent, "subtree": True, "name": "Fibre cut", "severity": "critical"},
        )
        assert made.status_code == 200, made.text
        rule_id = made.json()["id"]
        after = (await editor.get("/api/catalogue", params={"under": parent})).json()
        assert after["classes"] and all(c["name"] == "Fibre cut" for c in after["classes"]), after
        assert all(c["severity"] == "critical" for c in after["classes"]), after

        gone = await editor.delete(f"/api/catalogue/rules/{rule_id}")
        assert gone.status_code == 200, gone.text
        again = (await editor.get("/api/catalogue", params={"under": parent})).json()
        assert all(c["name"] != "Fibre cut" for c in again["classes"]), again
        assert (await editor.delete(f"/api/catalogue/rules/{rule_id}")).status_code == 404
    finally:
        await editor.aclose()

    async with store.lock:
        cur = await store.conn.execute(
            "SELECT action FROM audit_log WHERE action LIKE 'catalogue.rule.%' ORDER BY id"
        )
        actions = [str(r[0]) for r in await cur.fetchall()]
    assert actions == ["catalogue.rule.set", "catalogue.rule.delete"], actions


async def test_a_viewer_reads_the_catalogue_and_cannot_write_it(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    viewer = await authutil.client_as(app, "viewer")
    try:
        assert (await viewer.get("/api/catalogue")).status_code == 200
        refused = await viewer.post("/api/catalogue/rules", json={"oid": HUAWEI, "name": "mine"})
        assert refused.status_code == 403, refused.text
    finally:
        await viewer.aclose()


async def test_a_rule_that_is_not_an_oid_is_refused_by_name(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    editor = await authutil.client_as(app, "editor")
    try:
        for bad in ("1.3.6.x", "1.3..6", "iso.org", "1.3.6.1 OR 1=1"):
            resp = await editor.post("/api/catalogue/rules", json={"oid": bad, "name": "x"})
            assert resp.status_code == 422, (bad, resp.text)
        empty = await editor.post("/api/catalogue/rules", json={"oid": HUAWEI})
        assert empty.status_code == 422, "a rule that names nothing and grades nothing was stored"
    finally:
        await editor.aclose()


async def test_the_root_of_the_tree_lists_the_vendors_and_a_node_its_children(
    store: Store,
) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    viewer = await authutil.client_as(app, "viewer")
    try:
        root = (await viewer.get("/api/catalogue/tree", params={"node": "1.3.6.1.4.1"})).json()
        arcs = {child["oid"] for child in root["children"]}
        assert f"{HUAWEI}" in arcs and "1.3.6.1.4.1.9" in arcs, sorted(arcs)[:10]
        huawei = next(c for c in root["children"] if c["oid"] == HUAWEI)
        assert huawei["vendor"], huawei
        bad = await viewer.get("/api/catalogue/tree", params={"node": "not.an.oid"})
        assert bad.status_code == 422, bad.text
    finally:
        await viewer.aclose()


async def test_an_imported_rule_never_reaches_evidence(store: Store) -> None:
    """**Imported data is not evidence** — the line the brief draws, asserted as a count.

    A file that grades every class `critical` is imported over a corpus. Every `dataset_*` table,
    the promotion record and the situations are counted before and after: a rule is what a class is
    called and how it is drawn, and nothing that trains or groups may move.
    """
    engine, queue, app = await authutil.make_env(store)
    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", BASE))
    before_data = await _tables(store, "dataset_")
    before_promotion = await _tables(store, "promotion")
    before_situations = await _situations(store)

    editor = await authutil.client_as(app, "editor")
    try:
        classes = (await editor.get("/api/catalogue", params={"limit": 200})).json()["classes"]
        text = "trap_oid,class,severity\n" + "\n".join(
            f"{c['oid']},Imported {i},critical" for i, c in enumerate(classes)
        )
        done = await editor.post(
            "/api/catalogue/import", json={"filename": "all-critical.csv", "text": text}
        )
        assert done.status_code == 200 and done.json()["outcome"] == "imported", done.text
        census = (await editor.get("/api/stats")).json()["severity"]
        assert census["provenance"]["imported"] > 0, census
    finally:
        await editor.aclose()

    assert await _tables(store, "dataset_") == before_data
    assert await _tables(store, "promotion") == before_promotion
    assert await _situations(store) == before_situations
