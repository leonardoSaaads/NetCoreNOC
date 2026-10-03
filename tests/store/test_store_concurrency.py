"""The one-`Store` / one-connection / one-lock invariant (v0.7.3, §4.4).

**This is the highest-value test in v0.7.3 and the reason it is written before anything moves.**

`store.py` becomes a package of sixteen domain modules in this release. The invariant that split
may not break is not a style preference:

> One `Store` class is preserved, with **one** `aiosqlite` connection and **one** `store.lock`.

The single connection and the single lock are load-bearing. F39 (`SECURITY-REVIEW-0.7.1.md` §4.2)
exists precisely *because* one connection is shared by the engine task and every API request, and
v0.7.1's `write_txn` discipline is built on `store.lock` being the one mutual exclusion. Splitting
`Store` into several objects with several connections, or several locks, is a **behaviour change of
the worst kind — one whose failure mode is data corruption under concurrency, and which every other
test in this suite would pass straight through**. Nothing else here drives three domains at once.

Two properties make these assertions worth something rather than decorative:

* **They are structural, not `isinstance` theatre.** `assert isinstance(store, Store)` proves
  nothing about how many connections exist. These tests count the distinct connection and lock
  *identities* reachable from the object, and drive real concurrent writes from three different
  domain modules through `asyncio.gather`.
* **They are installed against the pre-split tree** (build prompt §11 Phase 2, the same discipline
  DECISIONS #81 used for the v0.7.2 guards), so every later step is measured by a rule that predates
  it and cannot have been shaped to fit the outcome.

The end-to-end half of the `write_txn` story is already covered by
`test_api.py::test_f39_a_failed_write_leaves_nothing_to_commit`, which drives it through a real HTTP
request. What is added here is the store-side contract that test depends on.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import aiosqlite
import pytest

from netcorenoc.api.perimeter import Perimeter
from netcorenoc.crosscutting import audit
from netcorenoc.store import Store

BASE = 1_700_000_000.0


# --- the identities ------------------------------------------------------------------------


async def test_the_connection_property_is_stable(store: Store) -> None:
    """`store.conn is store.conn`. A property that built a connection per access — or per mixin —
    would hand each caller its own transaction, silently."""
    first = store.conn
    for _ in range(5):
        assert store.conn is first
    assert isinstance(first, aiosqlite.Connection)


async def test_exactly_one_connection_is_reachable_from_the_store(store: Store) -> None:
    """Count the distinct `aiosqlite.Connection` objects the store can reach. Must be one.

    This is the assertion that would actually fail if `Store` were split into several objects each
    opening its own connection — the shape the v0.7.3 split most plausibly goes wrong in. Scanning
    the instance's own `__dict__` one level deep catches both the direct case (a second `_conn`) and
    the delegating case (an attribute that is itself a store-like object holding a connection).
    """
    seen: set[int] = {id(store.conn)}
    nested: list[str] = []
    for name, value in vars(store).items():
        if isinstance(value, aiosqlite.Connection):
            seen.add(id(value))
        inner = getattr(value, "_conn", None)
        if isinstance(inner, aiosqlite.Connection):
            seen.add(id(inner))
            nested.append(name)
        if isinstance(value, Store):
            nested.append(name)
    assert not nested, (
        f"the Store delegates to nested store-like object(s) {nested}; the release invariant is "
        "one Store with one connection, not a façade over several"
    )
    assert len(seen) == 1, f"{len(seen)} distinct aiosqlite connections reachable from one Store"


async def test_exactly_one_lock_is_reachable_from_the_store(store: Store) -> None:
    """`store.lock` is one object, stable across accesses, and the only lock on the store.

    Several locks is the same defect as several connections wearing a different hat: two callers
    each holding "the" lock would both believe they had exclusive use of the one connection.
    """
    first = store.lock
    assert store.lock is first
    assert isinstance(first, asyncio.Lock)
    locks = {id(first)} | {id(v) for v in vars(store).values() if isinstance(v, asyncio.Lock)}
    assert len(locks) == 1, f"{len(locks)} distinct asyncio.Lock objects reachable from one Store"


async def test_no_store_method_acquires_the_store_lock(store: Store) -> None:
    """`store.lock` is a contract for *callers*, never taken inside a `Store` method.

    Measured in Gate 0: no `Store` method acquires it — `Engine._commit_batch`,
    `Engine.maintenance` and `Perimeter.write_txn` do. A `Store` method that took it would deadlock
    against every one of those callers, all of which already hold it. Asserted here because the
    split is exactly when someone might "helpfully" add one to a new mixin.
    """
    import inspect

    offenders: list[str] = []
    for klass in type(store).__mro__:
        if klass is object:
            continue
        for name, member in vars(klass).items():
            target = member.fget if isinstance(member, property) else member
            if not callable(target) or not hasattr(target, "__code__"):
                continue
            with contextlib.suppress(OSError, TypeError):
                source = inspect.getsource(target)
                # `__init__` *assigns* the lock, which is the one legitimate mention. What must
                # not appear anywhere is an *acquisition*.
                if "with self.lock" in source or "self.lock.acquire" in source:
                    offenders.append(f"{klass.__name__}.{name}")
    assert not offenders, (
        f"Store method(s) acquire self.lock: {offenders}. The lock is acquired by callers "
        "(Engine._commit_batch, Engine.maintenance, Perimeter.write_txn); a Store method that "
        "takes it deadlocks against all three."
    )


# --- concurrent writes from three different domain modules ---------------------------------


async def _device_write(store: Store, ip: str) -> int:
    """`store/devices.py` — a device upsert."""
    async with store.lock:
        return await store.device_id(ip, BASE)


async def _feedback_write(store: Store, sid: int) -> Any:
    """`store/feedback.py` — a feedback insert."""
    async with store.lock:
        return await store.add_feedback(sid, "confirm", BASE, principal_ref="t", role="editor")


async def _audit_write(store: Store, n: int) -> None:
    """`store/audit_log.py` — an audit-chain append (reserve id, hash, insert)."""
    async with store.lock:
        await audit.write_event(
            store,
            ts=BASE + n,
            actor="system",
            role=None,
            source_ip=None,
            action="config.change",
            outcome="ok",
            object_type="config",
            object_id=str(n),
            details={"n": n},
        )


async def test_concurrent_writes_from_three_domains_serialise_and_land(store: Store) -> None:
    """Three domain modules writing at once, through `asyncio.gather`, on one connection.

    The assertion that makes this catch a broken invariant is the **single commit at the end,
    issued by one caller**: with one shared connection every pending statement belongs to that one
    transaction, so one `commit()` makes all three domains' writes durable. If the split gave each
    mixin its own connection, the other two domains' writes would still be pending on connections
    nobody committed, and the counts below would come back short.
    """
    async with store.lock:
        sid = await store.create_situation(BASE)
        await store.commit()

    await asyncio.gather(
        *(_device_write(store, f"10.1.0.{n}") for n in range(1, 9)),
        *(_feedback_write(store, sid) for _ in range(4)),
        *(_audit_write(store, n) for n in range(8)),
    )

    # One caller, one commit — for everything all three domains just wrote.
    async with store.lock:
        await store.commit()

    async with store.lock:
        cur = await store.conn.execute("SELECT COUNT(*) FROM device")
        devices = int((await cur.fetchone())[0])  # type: ignore[index]
        cur = await store.conn.execute("SELECT COUNT(*) FROM feedback")
        feedback = int((await cur.fetchone())[0])  # type: ignore[index]
        cur = await store.conn.execute("SELECT COUNT(*) FROM audit_log")
        audits = int((await cur.fetchone())[0])  # type: ignore[index]

    assert devices == 8, f"8 concurrent device upserts left {devices} rows"
    # F36: `(situation, verdict)` is unique, so four identical posts are one row. That the repeat
    # is *seen* by the other three tasks is itself proof they shared the transaction.
    assert feedback == 1, f"4 concurrent identical verdicts left {feedback} rows, expected 1"
    assert audits == 8, f"8 concurrent audit appends left {audits} rows"


async def test_the_audit_chain_survives_concurrent_appends(store: Store) -> None:
    """The chain is the sharpest probe available for a fragmented lock.

    Every append reads `MAX(id)+1` and the previous `entry_hash`, then inserts. Two appenders that
    are not mutually excluded read the same predecessor and produce a fork: duplicate ids, or a
    `prev_hash` that names a row that is no longer the previous one. `verify_chain` walks the whole
    chain and recomputes every link, so it fails on either. Under one lock this is impossible —
    which is exactly the property being protected.
    """
    await asyncio.gather(*(_audit_write(store, n) for n in range(24)))
    async with store.lock:
        await store.commit()
        rows = await store.audit_all()
        result = await audit.verify_chain(store)

    assert len(rows) == 24, f"24 concurrent appends produced {len(rows)} rows"
    ids = [int(r["id"]) for r in rows]
    assert ids == sorted(set(ids)), f"duplicate or out-of-order audit ids under concurrency: {ids}"
    assert ids == list(range(1, 25)), f"audit ids are not contiguous 1..24: {ids}"
    assert result.ok, f"the audit chain broke under concurrent appends: {result.render()}"


# --- the write-transaction discipline (F39), store-side --------------------------------------


def _perimeter(store: Store) -> Perimeter:
    return Perimeter(store, rate_capacity=1000.0, rate_refill=1000.0, warnings=None)


async def test_write_txn_rolls_back_and_leaves_nothing_for_another_caller(store: Store) -> None:
    """v0.7.1's F39 contract, asserted against the `Store` this release rebuilds.

    A handler that mutates and then raises must leave **nothing** pending on the shared connection.
    The probe is the one that actually matters: after the failure, an *unrelated* caller commits,
    and the abandoned mutation must not ride along on that commit.
    """
    async with store.lock:
        uid = await store.create_user("victim", "hash", "viewer", False, BASE)
        await store.commit()

    perimeter = _perimeter(store)
    with contextlib.suppress(RuntimeError):
        async with perimeter.write_txn():
            await store.set_user_role(uid, "admin", BASE)
            raise RuntimeError("the audit backend fell over mid-request")

    # An unrelated caller commits. The abandoned role change must not be adopted by it.
    async with store.lock:
        await store.commit()
        user = await store.get_user(uid)

    assert user is not None and user["role"] == "viewer", (
        "an uncommitted, unaudited role change survived a failed write_txn and was committed by an "
        f"unrelated caller: role is now {user['role'] if user else None!r}"
    )


async def test_write_txn_commits_on_success(store: Store) -> None:
    """The other half: the discipline must not be a rollback that never commits."""
    perimeter = _perimeter(store)
    async with perimeter.write_txn():
        uid = await store.create_user("kept", "hash", "editor", False, BASE)

    async with store.lock:
        await store.rollback()  # anything still pending would be discarded here
        user = await store.get_user(uid)
    assert user is not None and user["username"] == "kept"


async def test_write_txn_takes_the_same_lock_the_engine_takes(store: Store) -> None:
    """The perimeter and the engine must contend on **one** lock, not two that happen to agree.

    Holding `store.lock` outside must make `write_txn` wait. If the perimeter ever acquired its own
    lock, this would sail straight through — and the two would interleave on one connection, which
    is the precise defect F39 describes.
    """
    perimeter = _perimeter(store)
    entered = asyncio.Event()

    async def contender() -> None:
        async with perimeter.write_txn():
            entered.set()

    async with store.lock:
        task = asyncio.create_task(contender())
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(entered.wait()), timeout=0.25)
        assert not entered.is_set(), "write_txn entered while another caller held store.lock"
    await task
    assert entered.is_set()
