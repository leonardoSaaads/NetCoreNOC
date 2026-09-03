"""Phase 4.8 / Phase 5 — a populated v0.3.0-shaped database upgrades in place.

The rebrand ships no schema change, so a v0.3.0 database *is* a v0.4.0 database: reopening a
populated file runs no migration, and the learned state (promoted entities + the affinity the
engine reloads), the situations, and the audit chain all survive. This is the "upgrade from a
real v0.3.0 database with learned state" evidence for the release gate.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from netcorenoc.api import create_app
from netcorenoc.crosscutting import audit, shaping
from netcorenoc.main import Engine
from netcorenoc.store import Store

import authutil
import util

BASE = 1_700_000_000.0

#: The columns migration `0014` adds to a situation listing (v0.16.0).
_LIFECYCLE_KEYS = ("resolution", "derived_name", "operator_name")

#: What `0014` writes for each pre-v0.16.0 `status`, and the whole of DECISIONS #253 as a table.
#: `merged` is the one historical value that is knowable exactly, because `merge_situations` is
#: what wrote it; `closed` conflated an operator's close with the idle sweep and nothing recorded
#: which, so it becomes a marker that says so rather than a guess that reads like a fact.
_EXPECTED_RESOLUTION = {"open": None, "merged": "merged", "closed": "unattributed"}


def _without_lifecycle(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A listing projected back to its pre-v0.16.0 shape.

    Used where a guard compares a listing read on an **older schema** with one read after the
    upgrade: the release adds three columns, so the whole-row comparison would fail for exactly the
    reason the guard is not about. The added keys are asserted separately at each call site, so
    nothing is dropped from the claim — it is split into two.
    """
    return [{k: v for k, v in row.items() if k not in _LIFECYCLE_KEYS} for row in rows]


def _through_0014(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A pre-v0.16.0 listing, put through migration `0014`'s **stated** rule (DECISIONS #253).

    The point of asserting against this rather than against the raw `before` list: `0014` genuinely
    rewrites `status` for every situation that had already left, so a guard comparing raw rows
    would go red for the one change the release is *about*. Restating the rule here and comparing
    against its output keeps the guard strict — a migration that mapped `merged` to anything else,
    or that touched an `open` row, still fails — while letting the intended rewrite through.

        open   -> open
        merged -> resolved   (exact: `merge_situations` itself wrote that status)
        closed -> resolved   (and `resolution` becomes `unattributed`, which the caller checks)
    """
    return [
        {**row, "status": "resolved"} if row["status"] in ("merged", "closed") else dict(row)
        for row in rows
    ]


async def test_populated_db_learned_state_and_audit_chain_survive_reopen(tmp_path: Path) -> None:
    db = str(tmp_path / "upgrade.db")

    # 1) Populate: ingest a scenario that learns a device-affinity edge and groups a situation,
    #    persist learned state, and write an audit row. (Promoted-entity reload across a restart
    #    is covered separately by test_promotion; here we exercise the affinity matrix + chain.)
    store1 = Store(db)
    await store1.open()
    engine1 = Engine(store1, asyncio.Queue())
    await engine1.start()
    await util.drive(engine1, engine1.queue, util.fixture_events("fiber_cut.json", BASE))
    await engine1.maintenance(BASE + 10, retention_days=365.0)  # persists learned edges + state
    async with store1.lock:
        await audit.write_event(
            store1,
            ts=BASE,
            actor="system",
            role=None,
            source_ip=None,
            action="ingest.gap",
            outcome="ok",
            object_type="ingest_gap",
            details={},
        )
        await store1.commit()

    nes_before = await store1.list_ne()
    device_a = await store1.device_id("127.0.0.2", BASE)
    device_b = await store1.device_id("127.0.0.3", BASE)
    edges_before = (await store1.graph_snapshot(min_edge_n=0))["edges"]
    # v0.16.0: the correlator creates `new` (DECISIONS #254). `new` rather than `None` so the
    # comparison below still asserts that the *state* survives a reopen and not merely the rows.
    situations_before = await store1.list_situations("new", 100)
    version_before = await store1.schema_version()
    assert edges_before and situations_before  # the scenario actually learned/grouped something
    await store1.close()

    # 2) "Upgrade": reopen the same file with fresh objects — no migration should run.
    store2 = Store(db)
    await store2.open()
    assert await store2.schema_version() == version_before == store2.latest_schema_version()
    assert store2.integrity_warnings == []  # the populated DB is healthy
    engine2 = Engine(store2, asyncio.Queue())
    await engine2.start()  # reloads the affinity matrices

    # 3) Verify survival across the reopen.
    assert await store2.list_ne() == nes_before  # NEs intact
    assert (await store2.graph_snapshot(min_edge_n=0))["edges"] == edges_before  # learned edges
    assert await store2.list_situations("new", 100) == situations_before  # situations survive
    assert engine2.learner.device_affinity(device_a, device_b) > 0  # matrix reloaded into memory
    assert (await audit.verify_chain(store2)).ok  # the chain still verifies across the upgrade
    await store2.close()


async def test_v060_upgrade_preserves_grouping_and_seeds_provenance(tmp_path: Path) -> None:
    """Phase 5 gate: a live v0.5.0-shaped database upgrades in place with **identical grouping**.

    The same fixture is replayed twice: once against a database whose schema is frozen at v4 (a
    v0.5.0 install), once against the same file after migration 0005 has been applied. The
    resulting situation partitions must match member-for-member — the seeded configuration is the
    coded defaults, so v0.6.0 scores exactly as v0.5.0 did. Provenance is seeded and backfilled,
    learned state and the audit chain survive, and the schema advances by exactly one.
    """
    import netcorenoc.store.lifecycle as store_mod
    from netcorenoc.engine.correlate.scoring import AdditiveScorer

    real_dir = store_mod.MIGRATIONS_DIR

    class _FrozenAtV4:
        """The migration directory as a v0.5.0 install saw it: nothing past 0004 exists yet.

        Filtered by migration *number*, not by name, so a later release adding 0006/0007 does not
        silently un-freeze this fixture and turn "a genuine v0.5.0 database" into a current one."""

        def glob(self, pattern: str) -> list[Path]:
            return [p for p in real_dir.glob(pattern) if int(p.name.split("_", 1)[0]) <= 4]

    async def partition_of(store: Store) -> set[frozenset[int]]:
        cur = await store.conn.execute("SELECT situation_id, alarm_id FROM situation_alarm")
        groups: dict[int, set[int]] = {}
        for row in await cur.fetchall():
            groups.setdefault(int(row[0]), set()).add(int(row[1]))
        return {frozenset(members) for members in groups.values()}

    db = str(tmp_path / "v050-live.db")
    store_mod.MIGRATIONS_DIR = _FrozenAtV4()  # type: ignore[assignment]
    try:
        old = Store(db)
        await old.open()
        assert await old.schema_version() == 4  # a genuine v0.5.0 database
        engine_old = Engine(old, asyncio.Queue())
        await engine_old.start()
        assert engine_old.scorer_config_id is None  # no scorer_config table yet
        await util.drive(engine_old, engine_old.queue, util.fixture_events("fiber_cut.json", BASE))
        await engine_old.maintenance(BASE + 10, retention_days=365.0)
        async with old.lock:
            await audit.write_event(
                old,
                ts=BASE,
                actor="admin",
                role="admin",
                source_ip="-",
                action="login.ok",
                outcome="ok",
            )
            await old.commit()
        before_partition = await partition_of(old)
        before_edges = (await old.graph_snapshot(min_edge_n=0))["edges"]
        before_alarms = (await old.stats())["active_alarms"]
        assert before_partition and before_edges
        await old.close()
    finally:
        store_mod.MIGRATIONS_DIR = real_dir

    # The upgrade: same file, migration 0005 now present.
    new = Store(db)
    await new.open()
    try:
        assert await new.schema_version() == Store.latest_schema_version()
        assert new.integrity_warnings == []
        engine_new = Engine(new, asyncio.Queue())
        await engine_new.start()

        # Learned state, grouping and the audit chain all survive.
        assert await partition_of(new) == before_partition
        assert (await new.graph_snapshot(min_edge_n=0))["edges"] == before_edges
        assert (await new.stats())["active_alarms"] == before_alarms
        assert (await audit.verify_chain(new)).ok

        # Provenance is seeded, active, and backfilled onto the pre-existing situations.
        assert engine_new.scorer_config_id == 1
        active = engine_new.correlator.scorer.active
        assert active.params_fingerprint() == AdditiveScorer().params_fingerprint()
        cur = await new.conn.execute(
            "SELECT COUNT(*) FROM situation WHERE scorer_config_id IS NULL"
        )
        row = await cur.fetchone()
        assert row is not None and row[0] == 0

        # And the upgraded engine keeps grouping the same way: replaying the same fixture into a
        # fresh v0.6.0 database reproduces the v0.5.0 partition exactly.
        fresh = Store(str(tmp_path / "fresh-v060.db"))
        await fresh.open()
        engine_fresh = Engine(fresh, asyncio.Queue())
        await engine_fresh.start()
        await util.drive(
            engine_fresh, engine_fresh.queue, util.fixture_events("fiber_cut.json", BASE)
        )
        assert await partition_of(fresh) == before_partition
        await fresh.close()
    finally:
        await new.close()


async def test_v070_upgrade_changes_no_behaviour(tmp_path: Path) -> None:
    """Phase 5 gate: a live v0.6.0 database upgrades in place and **nothing changes**.

    The same fixture is replayed against a database whose schema is frozen at v5 (a v0.6.0
    install), then the file is reopened with migration 0006 present. Grouping, learned state,
    scorer provenance and the audit chain must all survive, and — the point of this release — the
    upgraded appliance must carry **no governance policy**, so every route answers exactly as it
    did before. Governance changes nothing until an admin writes a policy.
    """
    import netcorenoc.store.lifecycle as store_mod
    from netcorenoc.crosscutting import rbac

    real_dir = store_mod.MIGRATIONS_DIR

    class _FrozenAtV5:
        """The migration directory as a v0.6.0 install saw it: nothing past 0005 exists yet."""

        def glob(self, pattern: str) -> list[Path]:
            return [p for p in real_dir.glob(pattern) if int(p.name.split("_", 1)[0]) <= 5]

    async def partition_of(store: Store) -> set[frozenset[int]]:
        cur = await store.conn.execute("SELECT situation_id, alarm_id FROM situation_alarm")
        groups: dict[int, set[int]] = {}
        for row in await cur.fetchall():
            groups.setdefault(int(row[0]), set()).add(int(row[1]))
        return {frozenset(members) for members in groups.values()}

    db = str(tmp_path / "v060-live.db")
    store_mod.MIGRATIONS_DIR = _FrozenAtV5()  # type: ignore[assignment]
    try:
        old = Store(db)
        await old.open()
        assert await old.schema_version() == 5  # a genuine v0.6.0 database
        engine_old = Engine(old, asyncio.Queue())
        await engine_old.start()
        assert engine_old.scorer_config_id == 1  # v0.6.0 seeded its scorer config
        await util.drive(engine_old, engine_old.queue, util.fixture_events("fiber_cut.json", BASE))
        await engine_old.maintenance(BASE + 10, retention_days=365.0)
        async with old.lock:
            await audit.write_event(
                old,
                ts=BASE,
                actor="admin",
                role="admin",
                source_ip="-",
                action="login.ok",
                outcome="ok",
            )
            await old.commit()
        before_partition = await partition_of(old)
        before_edges = (await old.graph_snapshot(min_edge_n=0))["edges"]
        before_stats = await old.stats()
        assert before_partition and before_edges
        await old.close()
    finally:
        store_mod.MIGRATIONS_DIR = real_dir

    # The upgrade: same file, migration 0006 now present.
    new = Store(db)
    await new.open()
    try:
        assert await new.schema_version() == Store.latest_schema_version()
        assert new.integrity_warnings == []
        engine_new = Engine(new, asyncio.Queue())
        await engine_new.start()

        # Learned state, grouping, provenance and the audit chain all survive untouched.
        assert await partition_of(new) == before_partition
        assert (await new.graph_snapshot(min_edge_n=0))["edges"] == before_edges
        assert await new.stats() == before_stats
        assert engine_new.scorer_config_id == 1
        assert (await audit.verify_chain(new)).ok

        # THE v0.7.0 gate: no governance policy exists, so the perimeter is byte-identically
        # v0.6.0 — the resolver returns each role's compiled ceiling for every capability.
        async with new.lock:
            assert await new.active_governance_ids() == {}
        for role in rbac.ROLE_RANK:
            resolved = rbac.resolve_capabilities(role, None, None)
            for capability in rbac.PERMISSIONS:
                assert (capability in resolved) == rbac.role_allows(role, capability)
        # And scoping is inactive, so every principal still sees every NE.
        assert shaping.visible_nes("viewer", "user:1", None, []).unrestricted
    finally:
        await new.close()


async def test_v071_upgrade_changes_no_behaviour_except_the_three_documented_changes(
    tmp_path: Path,
) -> None:
    """Gate 4.2 (v0.7.1): a live v0.7.0 database upgrades in place and, with **no governance
    policy**, every route answers exactly as it did — with three documented exceptions.

    The exceptions are each a defect fix, each has a `DECISIONS.md` entry, and each is asserted
    explicitly below rather than described:

    1. a label write to a target that does not exist now returns **404** (F37, DECISIONS #70);
    2. a repeated **identical** feedback verdict is now a **no-op** (F36, DECISIONS #68);
    3. a list endpoint applies its `LIMIT` after filtering (F38, DECISIONS #72) — invisible here,
       because an unrestricted scope filters nothing, so the unrestricted result set is asserted
       byte-identical to the v0.7.0 query.

    Any fourth difference at empty policy is a defect in this release's work.
    """
    import netcorenoc.store.lifecycle as store_mod

    real_dir = store_mod.MIGRATIONS_DIR

    class _FrozenAtV6:
        """The migration directory as a v0.7.0 install saw it: nothing past 0006 exists yet."""

        def glob(self, pattern: str) -> list[Path]:
            return [p for p in real_dir.glob(pattern) if int(p.name.split("_", 1)[0]) <= 6]

    async def partition_of(store: Store) -> set[frozenset[int]]:
        cur = await store.conn.execute("SELECT situation_id, alarm_id FROM situation_alarm")
        groups: dict[int, set[int]] = {}
        for row in await cur.fetchall():
            groups.setdefault(int(row[0]), set()).add(int(row[1]))
        return {frozenset(members) for members in groups.values()}

    db = str(tmp_path / "v070-live.db")
    store_mod.MIGRATIONS_DIR = _FrozenAtV6()  # type: ignore[assignment]
    try:
        old = Store(db)
        await old.open()
        assert await old.schema_version() == 6  # a genuine v0.7.0 database
        engine_old = Engine(old, asyncio.Queue())
        await engine_old.start()
        await util.drive(engine_old, engine_old.queue, util.fixture_events("fiber_cut.json", BASE))
        await engine_old.maintenance(BASE + 10, retention_days=365.0)
        async with old.lock:
            await audit.write_event(
                old,
                ts=BASE,
                actor="admin",
                role="admin",
                source_ip="-",
                action="login.ok",
                outcome="ok",
            )
            await old.commit()
        before_partition = await partition_of(old)
        before_edges = (await old.graph_snapshot(min_edge_n=0))["edges"]
        before_stats = await old.stats()
        before_situations = await old.list_situations(None, 500)
        before_open = await old.list_situations("new", 500)
        before_marks = await old.timeline_marks(1000)
        before_epoch = (engine_old.learner.A.epoch, engine_old.learner.E.epoch)
        assert before_partition and before_edges and before_marks
        await old.close()
    finally:
        store_mod.MIGRATIONS_DIR = real_dir

    # The upgrade: same file, migration 0007 now present.
    new = Store(db)
    await new.open()
    try:
        assert await new.schema_version() == Store.latest_schema_version()
        assert new.integrity_warnings == []
        engine_new = Engine(new, asyncio.Queue())
        await engine_new.start()

        # Learned state, grouping, provenance and the audit chain all survive.
        assert await partition_of(new) == before_partition
        assert (await new.graph_snapshot(min_edge_n=0))["edges"] == before_edges
        assert await new.stats() == before_stats
        assert (engine_new.learner.A.epoch, engine_new.learner.E.epoch) == before_epoch
        assert (await audit.verify_chain(new)).ok
        assert await new.active_scorer_config() is not None
        assert await new.active_governance_ids() == {}  # still no policy: nothing is scoped

        # Change 3 (F38): the SQL changed, so the UNRESTRICTED result set is asserted unchanged.
        #
        # **v0.16.0 makes the listing wider**, so the comparison is over the columns that existed
        # on both sides rather than over the whole row. That is not a loosening: the three new keys
        # are asserted separately below, and comparing whole rows would have made this guard fail
        # for the one reason it is not about — a column the release deliberately added.
        after = await new.list_situations(None, 500)
        assert _without_lifecycle(after) == _through_0014(before_situations)
        assert _without_lifecycle(await new.list_situations("new", 500)) == before_open
        # The three new keys are present; the two NAMES are empty on a migrated database, because
        # `0014` derives none — a name is written when membership changes, and a migration changes
        # no membership (DECISIONS #257). `resolution` is the decision-1 rewrite, checked by value.
        for listed, was in zip(after, before_situations, strict=True):
            assert set(listed) - set(was) == {"resolution", "derived_name", "operator_name"}
            assert listed["derived_name"] is None and listed["operator_name"] is None
            assert listed["resolution"] == _EXPECTED_RESOLUTION[was["status"]]
        assert await new.timeline_marks(1000) == before_marks
        # …and `ne_id` is used for filtering only; it never reaches a rendered mark.
        assert all(
            set(m) == {"ts", "device", "class", "kind"} for m in await new.timeline_marks(50)
        )

        await authutil.make_users(new)
        app = create_app(engine_new, rate_capacity=100000.0)
        admin = await authutil.client_as(app, "admin")
        try:
            sid = (await admin.get("/api/situations")).json()[0]["id"]
            device_id = (await admin.get("/api/graph")).json()["nodes"][0]["id"]

            # Change 1 (F37): a nonexistent target is now 404; a real one is unchanged at 200.
            assert (
                await admin.post("/api/labels", json={"kind": "device", "id": 900001, "label": "x"})
            ).status_code == 404
            assert (
                await admin.post(
                    "/api/labels", json={"kind": "device", "id": device_id, "label": "core-1"}
                )
            ).status_code == 200

            # Change 2 (F36): the repeat still answers 200 but records and applies nothing.
            pair = next(iter(engine_new.learner.A.pairs), None)
            assert pair is not None
            first = await admin.post(f"/api/situations/{sid}/feedback", json={"verdict": "confirm"})
            assert first.status_code == 200
            after_first = engine_new.learner.A.pair_mass(*pair)
            repeat = await admin.post(
                f"/api/situations/{sid}/feedback", json={"verdict": "confirm"}
            )
            assert repeat.status_code == 200 and repeat.json() == first.json()
            assert engine_new.learner.A.pair_mass(*pair) == after_first
            cur = await new.conn.execute("SELECT COUNT(*) FROM feedback")
            row = await cur.fetchone()
            assert row is not None and row[0] == 1

            # And the epoch did not move: an epoch is a closed situation (F36, DECISIONS #69).
            assert (engine_new.learner.A.epoch, engine_new.learner.E.epoch) == before_epoch

            # Everything else at empty policy is untouched: nothing is scoped, no route 403s.
            me = (await admin.get("/api/me")).json()
            assert me["scope"] == {"scoped": False, "ne_count": None}
            for path in (
                "/api/stats",
                "/api/graph",
                "/api/timeline",
                "/api/entities",
                "/api/scope",
            ):
                assert (await admin.get(path)).status_code == 200, path
        finally:
            await admin.aclose()
    finally:
        await new.close()


async def test_v090_upgrade_applies_0009_and_changes_no_grouping(tmp_path: Path) -> None:
    """Gate 3 (v0.9.0): a live v0.8.1 database upgrades in place, and **shadow mode changes
    nothing**.

    The strongest claim this release makes about the upgrade is a negative one: migration 0009 adds
    two tables, seeds **no rows**, and the appliance's first boot afterwards behaves exactly as it
    did before. A shadow-mode release is structurally tempted to arrive with an opinion already in
    the database; this asserts it does not.

    Compare 0005, which *had* to seed a row because the engine needs scoring parameters. Nothing
    here is needed for the engine to run, because the challenger is never consulted about anything.
    """
    import netcorenoc.store.lifecycle as store_mod

    real_dir = store_mod.MIGRATIONS_DIR

    class _FrozenAtV8:
        """The migration directory as a v0.8.1 install saw it: nothing past 0008 exists yet.

        Filtered by migration *number*, so a later release adding 0010 cannot silently un-freeze
        this fixture and turn a genuine v0.8.1 database into a current one.
        """

        def glob(self, pattern: str) -> list[Path]:
            return [p for p in real_dir.glob(pattern) if int(p.name.split("_", 1)[0]) <= 8]

    async def partition_of(store: Store) -> set[frozenset[int]]:
        cur = await store.conn.execute("SELECT situation_id, alarm_id FROM situation_alarm")
        groups: dict[int, set[int]] = {}
        for row in await cur.fetchall():
            groups.setdefault(int(row[0]), set()).add(int(row[1]))
        return {frozenset(members) for members in groups.values()}

    db = str(tmp_path / "v081-live.db")
    store_mod.MIGRATIONS_DIR = _FrozenAtV8()  # type: ignore[assignment]
    try:
        old = Store(db)
        await old.open()
        assert await old.schema_version() == 8  # a genuine v0.8.1 database
        engine_old = Engine(old, asyncio.Queue())
        await engine_old.start()
        await util.drive(engine_old, engine_old.queue, util.fixture_events("fiber_cut.json", BASE))
        await engine_old.maintenance(BASE + 10, retention_days=365.0)
        sids = [int(r["id"]) for r in await old.list_situations(None, 10)]
        async with old.lock:
            # A real label, so the upgrade carries a promoted corpus rather than an empty one.
            await engine_old.apply_feedback(
                sids[0], "confirm", BASE + 20, principal_ref="alice", role="editor"
            )
            await audit.write_event(
                old,
                ts=BASE,
                actor="admin",
                role="admin",
                source_ip="-",
                action="login.ok",
                outcome="ok",
            )
            await old.commit()
        before_partition = await partition_of(old)
        before_edges = (await old.graph_snapshot(min_edge_n=0))["edges"]
        before_stats = await old.stats()
        before_situations = await old.list_situations(None, 500)
        before_dataset = await old.dataset_stats()
        before_epoch = (engine_old.learner.A.epoch, engine_old.learner.E.epoch)
        before_chain = (await audit.verify_chain(old)).final_hash
        assert before_partition and before_edges
        assert before_dataset["dataset_pair.dataset"] > 0  # a labelled corpus exists
        await old.close()
    finally:
        store_mod.MIGRATIONS_DIR = real_dir

    # The upgrade: same file, every migration past 0008 now present. From v0.9.2 that is 0009,
    # 0010 and 0011 together — the fixture is frozen at 8, so this asserts a v0.8.1 database reaches
    # the CURRENT schema, and the "no rows seeded" claim below is now made about all three.
    new = Store(db)
    await new.open()
    try:
        assert await new.schema_version() == Store.latest_schema_version() == 14
        assert new.integrity_warnings == []

        # Asserted BEFORE the engine starts, because `Engine.start` legitimately opens a new
        # `capture_run` in any new process (v0.8.0 behaviour, unrelated to this migration). The
        # question here is what the MIGRATION did, so it is asked before anything else runs.
        assert await new.dataset_stats() == before_dataset
        # **No rows seeded.** Both new tables are empty immediately after the migration.
        for table in ("challenger_run", "shadow_opinion", "feedback_exclusion"):
            cur = await new.conn.execute(f"SELECT COUNT(*) FROM {table}")  # nosec B608
            row = await cur.fetchone()
            assert row is not None and int(row[0]) == 0, f"{table} was seeded by the migration"
        # The audit chain verifies, and to the SAME final hash — the migration wrote no event.
        result = await audit.verify_chain(new)
        assert result.ok and result.final_hash == before_chain

        engine_new = Engine(new, asyncio.Queue())
        await engine_new.start()

        # Data intact: grouping, learned edges, the corpus, and the epoch.
        assert await partition_of(new) == before_partition
        assert (await new.graph_snapshot(min_edge_n=0))["edges"] == before_edges
        assert await new.stats() == before_stats
        # v0.16.0: three columns wider, and `0014` rewrites the status of everything that had
        # already left. `_through_0014` states that rule so the guard stays strict about
        # everything else (DECISIONS #253); the added keys are dropped by `_without_lifecycle`
        # and checked by value in the v0.15.5 upgrade guard at the bottom of this file.
        assert _without_lifecycle(await new.list_situations(None, 500)) == _through_0014(
            before_situations
        )
        assert (engine_new.learner.A.epoch, engine_new.learner.E.epoch) == before_epoch

        # First boot still writes no shadow row: capture opened a run, the challenger did not.
        cur = await new.conn.execute("SELECT COUNT(*) FROM shadow_opinion")
        row = await cur.fetchone()
        assert row is not None and int(row[0]) == 0

        # The champion is unchanged and still active: shadow mode swapped nothing.
        config = await new.active_scorer_config()
        assert config is not None and config["scorer_id"] == "additive"
        assert engine_new.correlator.scorer.active.scorer_id == "additive"
    finally:
        await new.close()


async def test_v091_upgrade_applies_0010_and_leaves_existing_labels_plain(tmp_path: Path) -> None:
    """Gate 2 (v0.9.1): a live **v0.9.0** database upgrades in place, and every label it already
    holds stays exactly what it was — a **plain** split or confirm.

    The claim this release makes about the upgrade is a negative one, and it is stronger than
    0009's because 0010 touches the table the corpus lives in. `feedback` gains three nullable
    columns and there is **no backfill at all** — not even the marker backfill 0008 permitted
    itself, because 0008 could write `capture_provenance` from a fact knowable at migration time
    (the row existed, so it predated v0.8.0), and **nothing about which members an operator would
    have marked is knowable from anything**. Inventing it would be the fabrication this release
    exists to refuse.

    So: `excluded_count`, `excluded_truncated` and `remainder_together` are NULL on every
    pre-existing row, `feedback_exclusion` is empty, and a reader of an old label sees a bag and a
    verdict — which is precisely what that operator asserted.
    """
    import netcorenoc.store.lifecycle as store_mod

    real_dir = store_mod.MIGRATIONS_DIR

    class _FrozenAtV9:
        """The migration directory as a v0.9.0 install saw it: 0010 does not exist yet.

        Filtered by migration *number*, so a later release adding 0011 cannot silently un-freeze
        this fixture and turn a genuine v0.9.0 database into a current one.
        """

        def glob(self, pattern: str) -> list[Path]:
            return [p for p in real_dir.glob(pattern) if int(p.name.split("_", 1)[0]) <= 9]

    db = str(tmp_path / "v090-live.db")
    store_mod.MIGRATIONS_DIR = _FrozenAtV9()  # type: ignore[assignment]
    try:
        old = Store(db)
        await old.open()
        assert await old.schema_version() == 9  # a genuine v0.9.0 database
        engine_old = Engine(old, asyncio.Queue())
        await engine_old.start()
        await util.drive(engine_old, engine_old.queue, util.fixture_events("fiber_cut.json", BASE))
        await engine_old.maintenance(BASE + 10, retention_days=365.0)
        sids = [int(r["id"]) for r in await old.list_situations(None, 10)]
        async with old.lock:
            # Two real labels, so the upgrade carries a corpus with both verdicts in it.
            await engine_old.apply_feedback(
                sids[0], "confirm", BASE + 20, principal_ref="alice", role="editor"
            )
            await engine_old.apply_feedback(
                sids[0], "split", BASE + 21, principal_ref="bob", role="editor"
            )
            await audit.write_event(
                old,
                ts=BASE,
                actor="admin",
                role="admin",
                source_ip="-",
                action="login.ok",
                outcome="ok",
            )
            await old.commit()
        before_labels = [
            dict(r)
            for r in await (
                await old.conn.execute(
                    "SELECT id, situation_id, verdict, member_count, acquisition_channel "
                    "FROM feedback ORDER BY id"
                )
            ).fetchall()
        ]
        before_members = [
            dict(r)
            for r in await (
                await old.conn.execute(
                    "SELECT feedback_id, source, position, alarm_id FROM feedback_member "
                    "ORDER BY feedback_id, source, position"
                )
            ).fetchall()
        ]
        before_dataset = await old.dataset_stats()
        before_chain = (await audit.verify_chain(old)).final_hash
        assert len(before_labels) == 2, "the fixture must carry both verdicts"
        assert before_members, "the fixture must carry a recorded bag"
        await old.close()
    finally:
        store_mod.MIGRATIONS_DIR = real_dir

    # The upgrade: same file, 0010 now present — and, from v0.9.2, 0011 with it. The claims below
    # are about what 0010 did NOT do to a v0.9.0 corpus, and 0011 does not weaken any of them: it
    # backfills only where `excluded_count IS NOT NULL`, which is exactly the set of rows this
    # fixture proves is empty. `test_v092_upgrade_reconciles_and_leaves_tier_three_null` is the
    # test that exercises 0011 against a corpus that DOES carry assertions.
    new = Store(db)
    await new.open()
    try:
        assert await new.schema_version() == Store.latest_schema_version() == 14
        assert new.integrity_warnings == []

        # Asked BEFORE the engine starts, because `Engine.start` legitimately opens a new
        # `capture_run` in any new process. The question here is what the MIGRATION did.
        assert await new.dataset_stats() == before_dataset

        # **Nothing seeded.** The one new table is empty.
        cur = await new.conn.execute("SELECT COUNT(*) FROM feedback_exclusion")
        row = await cur.fetchone()
        assert row is not None and int(row[0]) == 0, "0010 seeded feedback_exclusion"

        # **Nothing backfilled.** Every pre-existing label reads as a PLAIN verdict: no exclusion
        # was asserted, no truncation was recorded, and the remainder was not spoken about.
        cur = await new.conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE excluded_count IS NOT NULL "
            "OR excluded_truncated IS NOT NULL OR remainder_together IS NOT NULL"
        )
        row = await cur.fetchone()
        assert row is not None and int(row[0]) == 0, (
            "0010 backfilled an assertion onto a label whose operator never made one"
        )

        # The labels and their bags are untouched, value for value.
        after_labels = [
            dict(r)
            for r in await (
                await new.conn.execute(
                    "SELECT id, situation_id, verdict, member_count, acquisition_channel "
                    "FROM feedback ORDER BY id"
                )
            ).fetchall()
        ]
        after_members = [
            dict(r)
            for r in await (
                await new.conn.execute(
                    "SELECT feedback_id, source, position, alarm_id FROM feedback_member "
                    "ORDER BY feedback_id, source, position"
                )
            ).fetchall()
        ]
        assert after_labels == before_labels
        assert after_members == before_members

        # The audit chain verifies, and to the SAME final hash — the migration wrote no event.
        result = await audit.verify_chain(new)
        assert result.ok and result.final_hash == before_chain

        # First boot after the upgrade still writes no exclusion.
        engine_new = Engine(new, asyncio.Queue())
        await engine_new.start()
        cur = await new.conn.execute("SELECT COUNT(*) FROM feedback_exclusion")
        row = await cur.fetchone()
        assert row is not None and int(row[0]) == 0
    finally:
        await new.close()


async def test_v092_upgrade_reconciles_and_leaves_tier_three_null(tmp_path: Path) -> None:
    """Gate 2 (v0.9.2): a live **v0.9.1** database upgrades in place, `0011` **derives** the
    reconciled count from evidence that is already stored, and it seeds **nothing** from tier 3.

    The claim is two-sided, and the two sides come from different rules.

    * **Derivation is permitted.** `feedback_exclusion ∩ feedback_member(source='server')` is
      stored evidence that no retention path removes, so recomputing it is not invention. Every
      backfilled row is marked `excluded_reconciled_source='backfill'`, because *derived by the
      migration* and *written live at the verdict* are two different acts and a column that cannot
      tell them apart will be misread (DECISIONS #131, #133).
    * **Fabrication is not.** `excluded_reconciled_out_of_scope` needs `alarm.ne_id`, which
      `prune()` collects, and the scope policy **as the operator experienced it**, which is not
      reconstructible from what the policy document said. It stays `NULL` on every pre-existing
      row, forever.

    The fixture deliberately carries **a partial split whose marking includes a ghost id and a
    duplicate**, so the backfill has two different things to reconcile away: four ids reported,
    naming two distinct members of the bag, and the migration must write `2` — not `4`, which is
    what the old column says, not `3`, which counting rows instead of distinct marks would give,
    and not `0`.
    """
    import netcorenoc.store.lifecycle as store_mod

    real_dir = store_mod.MIGRATIONS_DIR

    class _FrozenAtV10:
        """The migration directory as a v0.9.1 install saw it: `0011` does not exist yet.

        Filtered by migration *number*, so a later release adding `0012` cannot silently un-freeze
        this fixture and turn a genuine v0.9.1 database into a current one.
        """

        def glob(self, pattern: str) -> list[Path]:
            return [p for p in real_dir.glob(pattern) if int(p.name.split("_", 1)[0]) <= 10]

    ghost = 999_999_999
    db = str(tmp_path / "v091-live.db")
    store_mod.MIGRATIONS_DIR = _FrozenAtV10()  # type: ignore[assignment]
    try:
        old = Store(db)
        await old.open()
        assert await old.schema_version() == 10  # a genuine v0.9.1 database
        engine_old = Engine(old, asyncio.Queue())
        await engine_old.start()
        await util.drive(engine_old, engine_old.queue, util.fixture_events("fiber_cut.json", BASE))
        await engine_old.maintenance(BASE + 10, retention_days=365.0)
        sids = [int(r["id"]) for r in await old.list_situations(None, 10)]
        bag = [int(r["id"]) for r in await old.situation_members(sids[0])]
        assert len(bag) >= 2, "the fixture must carry a bag big enough to mark two of"
        async with old.lock:
            # A partial split, marked with two REAL members and one ghost.
            #
            # **Why the exclusion is written through the store rather than through
            # `LabelContext`.** The migration directory can be frozen; the *code* cannot — this
            # process is always v0.9.2, and `labels._assertion` writes three columns a schema-v10
            # database does not have. Handing `apply_feedback` an exclusion here would therefore
            # exercise v0.9.2's capture fail-safe (it degrades, correctly, on an `OperationalError`)
            # and would leave no exclusion at all, which is the opposite of the fixture this test
            # needs.
            #
            # So the verdict, the bag and every other annotation come from the real engine path, and
            # the two v0.9.1-shaped exclusion facts are written with the **same store methods
            # v0.9.1 used**, carrying **exactly the column set v0.9.1 wrote**. What lands on disk is
            # a v0.9.1 row, which is what "a database written by v0.9.1" has to mean when the only
            # interpreter available is the current one.
            split = await engine_old.apply_feedback(
                sids[0], "split", BASE + 21, principal_ref="bob", role="editor"
            )
            assert split.id is not None
            # `bag[1]` is marked TWICE, on purpose. `feedback_exclusion` is keyed on
            # `(feedback_id, position)`, so a client may legitimately send a duplicate — and the
            # backfill must count DISTINCT marks or a duplicate would inflate the derived count,
            # and on a small enough bag would produce a value the CHECK refuses, turning a hostile
            # label into a failed upgrade (ledger L4).
            await old.add_feedback_exclusion(split.id, [bag[0], bag[1], bag[1], ghost])
            # **THE CLIENT'S FINGERPRINT — F49's second repair (v0.10.1).**
            #
            # v0.9.1 records what the client said its bag was, in `feedback_member(source=
            # 'client')`, separately from what the server's bag was. This fixture wrote only the
            # server side, and v0.10.0 measured the consequence: **deleting `AND m.source =
            # 'server'` from `0011_evidence_boundary.sql` left 1093 tests passing**, because with no
            # rows the predicate had nothing to exclude and an unfiltered join gave the same answer.
            # An upgrade probe that cannot distinguish the two is inert, and this test's docstring
            # claimed a boundary it was not exercising.
            #
            # The client's reported bag contains the GHOST — which is exactly the real case: a
            # browser whose view is stale reports a member the server's bag no longer holds and
            # marks it (F48's premise, and what the v0.7.5 SSE teardown produced routinely). With
            # this row present the predicate has work to do: filtered, the ghost joins nothing and
            # the backfill writes 2; unfiltered, the ghost joins its own client row and it writes 3.
            await old.add_feedback_members(split.id, "client", [bag[0], bag[1], ghost])
            await old.annotate_feedback(
                split.id, excluded_count=4, excluded_truncated=0, remainder_together=None
            )
            # A confirm, which asserts nothing negative and must stay untouched by the backfill.
            await engine_old.apply_feedback(
                sids[0], "confirm", BASE + 20, principal_ref="alice", role="editor"
            )
            await audit.write_event(
                old,
                ts=BASE,
                actor="admin",
                role="admin",
                source_ip="-",
                action="login.ok",
                outcome="ok",
            )
            await old.commit()
        before_labels = [
            dict(r)
            for r in await (
                await old.conn.execute(
                    "SELECT id, situation_id, verdict, member_count, excluded_count, "
                    "excluded_truncated, remainder_together, acquisition_channel "
                    "FROM feedback ORDER BY id"
                )
            ).fetchall()
        ]
        before_exclusion = [
            dict(r)
            for r in await (
                await old.conn.execute(
                    "SELECT feedback_id, position, alarm_id FROM feedback_exclusion "
                    "ORDER BY feedback_id, position"
                )
            ).fetchall()
        ]
        before_dataset = await old.dataset_stats()
        before_chain = (await audit.verify_chain(old)).final_hash
        partial = next(r for r in before_labels if r["excluded_count"] is not None)
        assert partial["excluded_count"] == 4, "v0.9.1 records the CLIENT's list length"
        assert len(before_exclusion) == 4, "all four reported ids are stored as evidence"
        await old.close()
    finally:
        store_mod.MIGRATIONS_DIR = real_dir

    # The upgrade: same file, 0011 now present.
    new = Store(db)
    await new.open()
    try:
        assert await new.schema_version() == Store.latest_schema_version() == 14
        assert new.integrity_warnings == [], "integrity_check / foreign_key_check after 0011"

        # **The derivation.** Three ids reported, two of them members of the server's own bag.
        cur = await new.conn.execute(
            "SELECT id, excluded_count, excluded_reconciled, excluded_reconciled_source, "
            "excluded_reconciled_out_of_scope FROM feedback WHERE excluded_count IS NOT NULL"
        )
        row = dict(next(iter(await cur.fetchall())))
        assert row["excluded_count"] == 4, "tier 1 is unchanged, byte for byte"
        assert row["excluded_reconciled"] == 2, (
            "the ghost asserted nothing and the duplicate is one mark, not two"
        )
        assert row["excluded_reconciled_source"] == "backfill", (
            "a derived count that cannot say it was derived will be read as a live one"
        )
        # **The boundary.** Nothing from tier 3, on any row, ever.
        assert row["excluded_reconciled_out_of_scope"] is None
        cur = await new.conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE excluded_reconciled_out_of_scope IS NOT NULL"
        )
        seeded = await cur.fetchone()
        assert seeded is not None and int(seeded[0]) == 0, (
            "0011 fabricated a scope record for a label whose scope is not reconstructible"
        )

        # A verdict that asserted nothing negative is left entirely alone: NULL means "no
        # assertion was made", and 0 would be an assertion about an empty set.
        cur = await new.conn.execute(
            "SELECT excluded_reconciled, excluded_reconciled_source FROM feedback "
            "WHERE verdict='confirm'"
        )
        confirm = dict(next(iter(await cur.fetchall())))
        assert confirm["excluded_reconciled"] is None
        assert confirm["excluded_reconciled_source"] is None

        # Tier 1 and the evidence rows survive value for value — the migration reads them and
        # writes nothing back.
        after_labels = [
            dict(r)
            for r in await (
                await new.conn.execute(
                    "SELECT id, situation_id, verdict, member_count, excluded_count, "
                    "excluded_truncated, remainder_together, acquisition_channel "
                    "FROM feedback ORDER BY id"
                )
            ).fetchall()
        ]
        after_exclusion = [
            dict(r)
            for r in await (
                await new.conn.execute(
                    "SELECT feedback_id, position, alarm_id FROM feedback_exclusion "
                    "ORDER BY feedback_id, position"
                )
            ).fetchall()
        ]
        assert after_labels == before_labels
        assert after_exclusion == before_exclusion
        assert ghost in {r["alarm_id"] for r in after_exclusion}, (
            "the ghost is still recorded verbatim: reconciliation is an additional quantity, "
            "never a filter on what is stored"
        )
        assert await new.dataset_stats() == before_dataset

        # The audit chain verifies, and to the SAME final hash — the migration wrote no event.
        result = await audit.verify_chain(new)
        assert result.ok and result.final_hash == before_chain

        # First boot after the upgrade seeds nothing further.
        engine_new = Engine(new, asyncio.Queue())
        await engine_new.start()
        cur = await new.conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE excluded_reconciled_out_of_scope IS NOT NULL"
        )
        booted = await cur.fetchone()
        assert booted is not None and int(booted[0]) == 0
    finally:
        await new.close()


async def test_v0100_upgrade_applies_0012_and_seeds_nothing(tmp_path: Path) -> None:
    """Gate 3 (v0.10.0): a live **v0.9.2** database upgrades in place and gains an EMPTY seal.

    The distinction this test exists for is `0012`'s own: `0011` backfilled a column because
    *recomputing a quantity from evidence already stored is derivation*. **A seal is not a
    derivation.** It is a choice about which evidence will be allowed to decide something later, and
    manufacturing one during an upgrade would make that choice on behalf of an operator who was
    never asked — and would do it at the moment the corpus happened to be whatever size it was.

    So the assertion is the opposite of `0011`'s: three new tables, and **nothing in them**.
    """
    import netcorenoc.store.lifecycle as store_mod

    real_dir = store_mod.MIGRATIONS_DIR

    class _FrozenAtV11:
        """The migration directory as a v0.9.2 install saw it: `0012` does not exist yet."""

        def glob(self, pattern: str) -> list[Path]:
            return [p for p in real_dir.glob(pattern) if int(p.name.split("_", 1)[0]) <= 11]

    db = str(tmp_path / "v092-live.db")
    store_mod.MIGRATIONS_DIR = _FrozenAtV11()  # type: ignore[assignment]
    try:
        old = Store(db)
        await old.open()
        assert await old.schema_version() == 11, "the fixture is not a genuine v0.9.2 database"
        engine_old = Engine(old, asyncio.Queue())
        await engine_old.start()
        await util.drive(engine_old, engine_old.queue, util.fixture_events("fiber_cut.json", BASE))
        await engine_old.maintenance(BASE + 10, retention_days=365.0)
        sids = [int(r["id"]) for r in await old.list_situations(None, 10)]
        async with old.lock:
            await engine_old.apply_feedback(
                sids[0], "confirm", BASE + 20, principal_ref="alice", role="editor"
            )
            await audit.write_event(
                old,
                ts=BASE,
                actor="admin",
                role="admin",
                source_ip="-",
                action="login.ok",
                outcome="ok",
            )
            await old.commit()
        before_labels = [
            dict(r)
            for r in await (
                await old.conn.execute(
                    "SELECT id, situation_id, verdict, member_count, coverage FROM feedback "
                    "ORDER BY id"
                )
            ).fetchall()
        ]
        before_dataset = await old.dataset_stats()
        before_chain = (await audit.verify_chain(old)).final_hash
        assert before_labels, "the fixture must carry at least one label"
        await old.close()
    finally:
        store_mod.MIGRATIONS_DIR = real_dir

    new = Store(db)
    await new.open()
    try:
        assert await new.schema_version() == Store.latest_schema_version() == 14
        assert new.integrity_warnings == [], "integrity_check / foreign_key_check after 0012"

        # **Nothing is seeded.** Three empty tables, and that is the claim.
        for table in ("holdout_seal", "holdout_seal_member", "holdout_access"):
            cur = await new.conn.execute(f"SELECT COUNT(*) FROM {table}")  # nosec B608
            row = await cur.fetchone()
            assert row is not None and int(row[0]) == 0, f"0012 seeded {table}"

        # The data is untouched, byte for byte.
        after_labels = [
            dict(r)
            for r in await (
                await new.conn.execute(
                    "SELECT id, situation_id, verdict, member_count, coverage FROM feedback "
                    "ORDER BY id"
                )
            ).fetchall()
        ]
        assert after_labels == before_labels, "0012 altered a label"
        assert await new.dataset_stats() == before_dataset, "0012 altered the captured dataset"

        chain = await audit.verify_chain(new)
        assert chain.ok, "the audit chain broke across the upgrade"
        assert chain.final_hash == before_chain, "the audit chain's final hash moved"
    finally:
        await new.close()


async def test_the_seal_schema_refuses_a_second_construction_and_every_edit(store: Store) -> None:
    """`0012`'s refusals, exercised against the database rather than against the layer above.

    **A seal that can be rebuilt is a seal that can be rebuilt *after* seeing a result**, so the
    refusal is structural: a constant `singleton` column with a UNIQUE constraint means the second
    INSERT fails at SQLite, not at whichever caller remembered to look first.

    **The controls come first, and they are not decoration.** A `BEFORE UPDATE` trigger on an
    *empty* table fires on nothing, so a refusal test that ran before any row existed would pass
    while proving nothing whatever — which is exactly the "measuring nothing and concluding CLOSED"
    failure this repository has on its record. So every table is populated and the insert asserted
    to have worked before a single refusal is checked.
    """
    seal = (
        "INSERT INTO holdout_seal (release, plan_sha256, created_at, digest, incident_count, "
        "corpus_incidents) VALUES (?, 'abc', 1.0, 'd', 3, 9)"
    )
    # CONTROLS. Each table must ACCEPT a legal row, or every refusal below is vacuous.
    await store.conn.execute(seal, ("v0.10.0",))
    await store.conn.execute(
        "INSERT INTO holdout_seal_member (seal_id, position, incident_id) VALUES (1, 0, 7)"
    )
    await store.conn.execute(
        "INSERT INTO holdout_access (at, release, plan_sha256, purpose, granted, outcome) "
        "VALUES (1.0, 'v0.10.0', 'abc', 'probe', 0, 'refused')"
    )
    cur = await store.conn.execute(
        "SELECT (SELECT COUNT(*) FROM holdout_seal), (SELECT COUNT(*) FROM holdout_seal_member), "
        "       (SELECT COUNT(*) FROM holdout_access)"
    )
    row = await cur.fetchone()
    assert row is not None and tuple(row) == (1, 1, 1), (
        "a table refused a legal insert, so every refusal asserted below would be meaningless"
    )

    for label, sql, params in (
        ("a second seal", seal, ("v0.11.0",)),
        ("an update to the seal", "UPDATE holdout_seal SET digest = 'x'", ()),
        ("a delete of the seal", "DELETE FROM holdout_seal", ()),
        ("an update to the membership", "UPDATE holdout_seal_member SET incident_id = 1", ()),
        ("a delete of the membership", "DELETE FROM holdout_seal_member", ()),
        ("an update to the access log", "UPDATE holdout_access SET outcome = 'x'", ()),
        ("a delete of the access log", "DELETE FROM holdout_access", ()),
    ):
        try:
            await store.conn.execute(sql, params)
        except sqlite3.IntegrityError:
            continue
        raise AssertionError(f"the schema permitted {label}")


async def test_v0101_upgrade_applies_0013_and_seeds_nothing(tmp_path: Path) -> None:
    """Gate 2 (v0.11.0): a live **v0.10.1** database upgrades in place and gains empty tables.

    `0012`'s claim repeated for the same reason and one more. A seal is not a derivation; **neither
    is a model version, and neither is a promotion**. Manufacturing either during an upgrade would
    put a row in an audit trail that no operator wrote, in the one table whose whole purpose is to
    answer *"what has this appliance been asked to deploy"*.

    The additional claim, and it is this migration's riskiest step: **`scorer_active` is rebuilt**,
    because `config_id` has to become nullable and SQLite cannot drop a `NOT NULL` in place. A
    rebuild that lost the pointer would leave the correlator loading the coded defaults on every
    upgraded appliance, silently — so the row is compared field by field, not merely counted.
    """
    import netcorenoc.store.lifecycle as store_mod

    real_dir = store_mod.MIGRATIONS_DIR

    class _FrozenAtV12:
        """The migration directory as a v0.10.1 install saw it: `0013` does not exist yet."""

        def glob(self, pattern: str) -> list[Path]:
            return [p for p in real_dir.glob(pattern) if int(p.name.split("_", 1)[0]) <= 12]

    db = str(tmp_path / "v0101-live.db")
    store_mod.MIGRATIONS_DIR = _FrozenAtV12()  # type: ignore[assignment]
    try:
        old = Store(db)
        await old.open()
        assert await old.schema_version() == 12, "the fixture is not a genuine v0.10.1 database"
        engine_old = Engine(old, asyncio.Queue())
        await engine_old.start()
        await util.drive(engine_old, engine_old.queue, util.fixture_events("fiber_cut.json", BASE))
        await engine_old.maintenance(BASE + 10, retention_days=365.0)
        sids = [int(r["id"]) for r in await old.list_situations(None, 10)]
        async with old.lock:
            await engine_old.apply_feedback(
                sids[0], "confirm", BASE + 20, principal_ref="alice", role="editor"
            )
            await audit.write_event(
                old,
                ts=BASE,
                actor="admin",
                role="admin",
                source_ip="-",
                action="scorer.config.update",
                outcome="ok",
                object_type="scorer_config",
                object_id="1",
                details={"action": "apply"},
            )
            await old.commit()
        before_labels = [
            dict(r)
            for r in await (
                await old.conn.execute(
                    "SELECT id, situation_id, verdict, member_count, coverage FROM feedback "
                    "ORDER BY id"
                )
            ).fetchall()
        ]
        before_dataset = await old.dataset_stats()
        before_chain = (await audit.verify_chain(old)).final_hash
        before_pointer = dict(
            await (await old.conn.execute("SELECT * FROM scorer_active WHERE id=1")).fetchone()  # type: ignore[arg-type]
        )
        before_configs = [
            dict(r)
            for r in await (
                await old.conn.execute("SELECT * FROM scorer_config ORDER BY id")
            ).fetchall()
        ]
        assert before_labels, "the fixture must carry at least one label"
        assert before_pointer["config_id"] is not None, "the fixture has no active pointer to keep"
        await old.close()
    finally:
        store_mod.MIGRATIONS_DIR = real_dir

    new = Store(db)
    await new.open()
    try:
        assert await new.schema_version() == Store.latest_schema_version() == 14
        assert new.integrity_warnings == [], "integrity_check / foreign_key_check after 0013"

        # **Nothing is seeded.** Three empty tables, and that is the claim.
        for table in ("model_version", "promotion", "evaluation_fold"):
            cur = await new.conn.execute(f"SELECT COUNT(*) FROM {table}")  # nosec B608
            row = await cur.fetchone()
            assert row is not None and int(row[0]) == 0, f"0013 seeded {table}"

        # The rebuilt pointer kept every field, and gained exactly one NULL.
        after_pointer = dict(
            await (await new.conn.execute("SELECT * FROM scorer_active WHERE id=1")).fetchone()  # type: ignore[arg-type]
        )
        assert after_pointer["model_version_id"] is None, "0013 activated a model version"
        assert {k: v for k, v in after_pointer.items() if k != "model_version_id"} == before_pointer

        # The history the pointer points into is untouched — the rebuild was of the pointer only.
        after_configs = [
            dict(r)
            for r in await (
                await new.conn.execute("SELECT * FROM scorer_config ORDER BY id")
            ).fetchall()
        ]
        assert after_configs == before_configs, "0013 altered the scorer configuration history"

        # The data is untouched, byte for byte.
        after_labels = [
            dict(r)
            for r in await (
                await new.conn.execute(
                    "SELECT id, situation_id, verdict, member_count, coverage FROM feedback "
                    "ORDER BY id"
                )
            ).fetchall()
        ]
        assert after_labels == before_labels, "0013 altered a label"
        assert await new.dataset_stats() == before_dataset, "0013 altered the captured dataset"

        chain = await audit.verify_chain(new)
        assert chain.ok, "the audit chain broke across the upgrade"
        assert chain.final_hash == before_chain, "the audit chain's final hash moved"

        # The access log gained its chain columns and no rows. The prefix is empty on every real
        # upgrade because nothing in v0.10.x writes `holdout_access` at all, which is measured here
        # rather than asserted in a comment.
        cur = await new.conn.execute("PRAGMA table_info(holdout_access)")
        columns = {str(r[1]) for r in await cur.fetchall()}
        assert {"prev_hash", "entry_hash", "chain_source"} <= columns
        cur = await new.conn.execute("SELECT COUNT(*) FROM holdout_access")
        row = await cur.fetchone()
        assert row is not None and int(row[0]) == 0
    finally:
        await new.close()


async def test_the_active_pointer_admits_exactly_one_of_the_two(store: Store) -> None:
    """`0013`'s `CHECK`, exercised against the database rather than against the layer above.

    **Two sources of truth about what is running is the one place where getting it wrong would be
    silent**: the engine would load one pointer, the API would report the other, and every grouping
    decision in between would be attributed to the wrong parameters. So the refusal is the
    database's, and it holds against a future method and against anyone with a SQLite prompt.

    **The control comes first and it is not decoration.** A `CHECK` that refused *everything* would
    pass both refusal assertions below while making the pointer unmovable — a far worse defect than
    the one being guarded against, and invisible to a test that only checks that bad input fails.
    """
    cur = await store.conn.execute(
        "INSERT INTO model_version (kind, contract_version, params_document, params_hash, "
        "created_at) VALUES ('additive', '1.0', '{}', 'deadbeef', 0.0) RETURNING id"
    )
    row = await cur.fetchone()
    assert row is not None
    model_version_id = int(row[0])

    # THE CONTROL, first: a legitimate single-pointer move in each direction must be ACCEPTED.
    await store.conn.execute(
        "UPDATE scorer_active SET config_id=NULL, model_version_id=? WHERE id=1",
        (model_version_id,),
    )
    pointer = "SELECT config_id, model_version_id FROM scorer_active WHERE id=1"
    cur = await store.conn.execute(pointer)
    assert tuple(await cur.fetchone()) == (None, model_version_id)  # type: ignore[arg-type]

    await store.conn.execute(
        "UPDATE scorer_active SET config_id=1, model_version_id=NULL WHERE id=1"
    )
    cur = await store.conn.execute(pointer)
    assert tuple(await cur.fetchone()) == (1, None)  # type: ignore[arg-type]

    # Now the two refusals.
    for label, sql, params in (
        (
            "both pointers set at once",
            "UPDATE scorer_active SET config_id=1, model_version_id=? WHERE id=1",
            (model_version_id,),
        ),
        (
            "neither pointer set",
            "UPDATE scorer_active SET config_id=NULL, model_version_id=NULL WHERE id=1",
            (),
        ),
    ):
        try:
            await store.conn.execute(sql, params)
        except sqlite3.IntegrityError:
            continue
        raise AssertionError(f"the schema permitted {label}")


async def test_v0155_upgrade_applies_0014_and_attributes_nothing_it_cannot(tmp_path: Path) -> None:
    """Phase 1 (v0.16.0): a live **v0.15.5** database upgrades in place and gains two empty tables.

    Three claims, and the third is this migration's own.

    **Nothing is seeded.** `situation_event` and `situation_event_member` are empty afterwards on
    every database, and a migrated appliance behaves at first boot exactly as it did before: capture
    of an operator gesture begins when an operator makes one, never when a script runs.

    **The data survives.** Grouping, learned edges, the label corpus and the audit chain are
    compared across the migration rather than counted, because a migration that dropped a row while
    keeping the count would pass a count.

    **And the part with no precedent: `status` is REWRITTEN, and the rewrite may not invent a
    reason.** `merged` becomes `resolved` + `resolution='merged'`, which is exact — that statement
    is what wrote the value being replaced. `closed` becomes `resolved` + `resolution =
    'unattributed'`, because before this release `closed` meant an operator's close **or** the idle
    sweep and no column distinguished them (DECISIONS #253). Writing `idle` there would be a guess
    about content where `0008`'s one permitted data write is explicitly a marker about provenance.
    The assertion below is that **no row gained a `resolution` the old schema could have
    justified**,
    which is the claim, rather than that some rows changed.
    """
    import netcorenoc.store.lifecycle as store_mod

    real_dir = store_mod.MIGRATIONS_DIR

    class _FrozenAtV13:
        """The migration directory as a v0.15.5 install saw it: `0014` does not exist yet."""

        def glob(self, pattern: str) -> list[Path]:
            return [p for p in real_dir.glob(pattern) if int(p.name.split("_", 1)[0]) <= 13]

    db = str(tmp_path / "v0155-live.db")
    store_mod.MIGRATIONS_DIR = _FrozenAtV13()  # type: ignore[assignment]
    try:
        old = Store(db)
        await old.open()
        assert await old.schema_version() == 13, "the fixture is not a genuine v0.15.5 database"
        engine_old = Engine(old, asyncio.Queue())
        await engine_old.start()
        await util.drive(engine_old, engine_old.queue, util.fixture_events("fiber_cut.json", BASE))
        await engine_old.maintenance(BASE + 10, retention_days=365.0)
        sids = [int(row["id"]) for row in await old.list_situations(None, 500)]
        assert sids, "the fixture formed no situation to migrate"
        async with old.lock:
            # One of each pre-v0.16.0 status, so the migration has all three cases to answer.
            # Written through the store's own methods where one exists, and by hand where the
            # release removed the only writer of the value: nothing writes `status='closed'` any
            # more, and a fixture that used the new writer would be testing the new schema against
            # itself rather than an upgrade.
            await old.conn.execute(
                "UPDATE situation SET status='closed', closed_at=? WHERE id=?", (BASE + 5, sids[0])
            )
            if len(sids) > 1:
                await old.conn.execute(
                    "UPDATE situation SET status='merged', closed_at=?, merged_into=? WHERE id=?",
                    (BASE + 5, sids[0], sids[1]),
                )
            await engine_old.apply_feedback(
                sids[0], "confirm", BASE + 20, principal_ref="alice", role="editor"
            )
            await old.commit()
        before_status = await _status_counts(old)
        before_labels = [
            dict(r)
            for r in await (
                await old.conn.execute(
                    "SELECT id, situation_id, verdict, member_count, coverage FROM feedback "
                    "ORDER BY id"
                )
            ).fetchall()
        ]
        before_partition = await _partition(old)
        before_edges = (await old.graph_snapshot(min_edge_n=0))["edges"]
        before_chain = (await audit.verify_chain(old)).final_hash
        assert before_labels and before_partition and before_edges
        assert before_status["closed"] >= 1, "the fixture has no unattributable close to migrate"
        await old.close()
    finally:
        store_mod.MIGRATIONS_DIR = real_dir

    new = Store(db)
    await new.open()
    try:
        assert await new.schema_version() == Store.latest_schema_version() == 14
        assert new.integrity_warnings == [], "integrity_check / foreign_key_check after 0014"

        # **Nothing is seeded.** Two empty tables, and that is the claim.
        for table in ("situation_event", "situation_event_member"):
            cur = await new.conn.execute(f"SELECT COUNT(*) FROM {table}")  # nosec B608
            row = await cur.fetchone()
            assert row is not None and int(row[0]) == 0, f"0014 seeded {table}"

        # The decision-1 rewrite, counted on both sides. Every `open` stays `open` and gains no
        # reason; every row that had left becomes `resolved` and gains exactly one.
        after = await _status_counts(new)
        assert after.get("open", 0) == before_status.get("open", 0)
        assert after.get("new", 0) == 0, "0014 invented a `new` situation"
        assert after.get("resolved", 0) == before_status.get("closed", 0) + before_status.get(
            "merged", 0
        )
        cur = await new.conn.execute(
            "SELECT COALESCE(resolution, '(none)'), COUNT(*) FROM situation GROUP BY 1 ORDER BY 1"
        )
        resolutions = {str(r[0]): int(r[1]) for r in await cur.fetchall()}
        assert resolutions.get("merged", 0) == before_status.get("merged", 0)
        assert resolutions.get("unattributed", 0) == before_status.get("closed", 0)
        # **The claim, stated as a prohibition.** Only two values may appear on a migrated
        # database, and neither is one the old schema could have justified: `idle`, `operator`,
        # `self_cleared` and `manual_clear` are facts nothing recorded before this release, so a
        # migration that wrote one would have invented it.
        assert set(resolutions) <= {"(none)", "merged", "unattributed"}, resolutions
        # No name is derived by a migration: a name is written where membership changes, and this
        # changed none (DECISIONS #257).
        cur = await new.conn.execute(
            "SELECT COUNT(*) FROM situation WHERE derived_name IS NOT NULL "
            "OR operator_name IS NOT NULL"
        )
        row = await cur.fetchone()
        assert row is not None and int(row[0]) == 0, "0014 wrote a name"

        # The data is intact.
        assert await _partition(new) == before_partition
        assert (await new.graph_snapshot(min_edge_n=0))["edges"] == before_edges
        assert [
            dict(r)
            for r in await (
                await new.conn.execute(
                    "SELECT id, situation_id, verdict, member_count, coverage FROM feedback "
                    "ORDER BY id"
                )
            ).fetchall()
        ] == before_labels
        verified = await audit.verify_chain(new)
        assert verified.ok and verified.final_hash == before_chain
    finally:
        await new.close()


async def _partition(store: Store) -> set[frozenset[int]]:
    """Every situation's membership, as a set of member sets — grouping without the ids.

    The module-level twin of the nested `partition_of` the earlier upgrade guards define inside
    themselves. Written here rather than reached into, because a helper defined inside another
    test's body is that test's, and importing it would couple two guards that are meant to be
    independent readings of the same property.
    """
    cur = await store.conn.execute(
        "SELECT situation_id, alarm_id FROM situation_alarm ORDER BY situation_id, alarm_id"
    )
    groups: dict[int, set[int]] = {}
    for sid, alarm in await cur.fetchall():
        groups.setdefault(int(sid), set()).add(int(alarm))
    return {frozenset(members) for members in groups.values()}


async def _status_counts(store: Store) -> dict[str, int]:
    cur = await store.conn.execute(
        "SELECT status, COUNT(*) FROM situation GROUP BY status ORDER BY status"
    )
    return {str(row[0]): int(row[1]) for row in await cur.fetchall()}
