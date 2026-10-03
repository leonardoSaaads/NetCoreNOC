"""The deterministic labelled dataset the bias and CLI reports are built over (moved from
`tests/model/test_bias.py`, which still owns the gate itself)."""

from __future__ import annotations

import asyncio

from netcorenoc.engine.correlate.rootcause import Member
from netcorenoc.engine.dataset.labels import ClientFingerprint, Exclusion, LabelContext
from netcorenoc.main import Engine
from netcorenoc.store import Store

TS = 1_000_000.0


async def build_fixture(store: Store) -> None:
    """A deterministic dataset exercising every branch the report has to describe.

    Deliberately includes the awkward cases rather than a clean corpus: a scoped label, a
    zero-member bag, a `legacy_capture` row, a client report that diverges from the server's, and a
    bag whose pairs were only partly evaluated. A gate built on the happy path would not notice
    most of what this release added.

    No wall clock anywhere — every timestamp derives from `TS`.
    """
    engine = Engine(store, asyncio.Queue())
    await engine.start()

    async with store.lock:
        # Three situations with real members, and the pairs among some of them.
        triples: list[tuple[int, int, int]] = []
        for i in range(9):
            cur = await store.conn.execute(
                "INSERT INTO device (ip, first_seen, last_seen) VALUES (?, ?, ?) RETURNING id",
                (f"10.0.0.{i + 1}", TS, TS),
            )
            dev = int((await cur.fetchone())[0])  # type: ignore[index]
            cur = await store.conn.execute(
                "INSERT INTO alarm_class (oid, first_seen, last_seen) VALUES (?, ?, ?) "
                "RETURNING id",
                (f"1.3.6.1.4.1.9.{i + 1}", TS, TS),
            )
            cls = int((await cur.fetchone())[0])  # type: ignore[index]
            cur = await store.conn.execute(
                "INSERT INTO alarm (device_id, class_id, instance, status, first_seen, "
                "last_seen, count) VALUES (?, ?, '', 'active', ?, ?, 1) RETURNING id",
                (dev, cls, TS, TS),
            )
            triples.append((int((await cur.fetchone())[0]), cls, dev))  # type: ignore[index]

        run = engine.capture.run_id
        sid_a = await store.create_situation(TS, None)
        sid_b = await store.create_situation(TS + 1.0, None)
        sid_c = await store.create_situation(TS + 2.0, None)
        # v0.9.1: a fourth situation, so the partial split has a bag of its own — `sid_a` already
        # carries both a `confirm` and the legacy `split`, and `feedback` is UNIQUE on
        # (situation_id, verdict).
        sid_d = await store.create_situation(TS + 3.0, None)
        for t in triples[:3]:
            await store.add_alarm_to_situation(sid_a, t[0])
        for t in triples[3:5]:
            await store.add_alarm_to_situation(sid_b, t[0])
        for t in triples[6:9]:
            await store.add_alarm_to_situation(sid_d, t[0])

        # Observations, one per alarm, and pairs with a deliberate mix of outcomes.
        for idx, t in enumerate(triples):
            await store.add_observation(
                capture_run_id=run,
                alarm_id=t[0],
                ne_id=t[2],
                device_id=t[2],
                entity_id=t[2],
                class_id=t[1],
                observed_at=TS + idx,
                alarm_count=1,
                severity=None,
                severity_rank=None,
                instance="",
                trap_oid="1.3.6.1.4.1.9.1",
                source_address=f"10.0.0.{idx + 1}",
                varbinds="[]",
            )
        pairs = [
            # (situation, a, b, linked, truncated, storm)
            (sid_a, triples[0][0], triples[1][0], 1, 0, 0),
            (sid_a, triples[0][0], triples[2][0], 1, 1, 0),
            (sid_a, triples[1][0], triples[2][0], 0, 0, 1),
            (sid_b, triples[3][0], triples[4][0], 1, 0, 0),
            (sid_c, triples[4][0], triples[5][0], 0, 0, 1),
            # v0.9.1: the partial split's bag, MIXED across the threshold, so the assertion it
            # carries is visible in every cut that needs promoted pairs.
            (sid_d, triples[6][0], triples[7][0], 1, 0, 0),
            (sid_d, triples[6][0], triples[8][0], 0, 0, 0),
            (sid_d, triples[7][0], triples[8][0], 1, 0, 0),
        ]
        await store.add_pairs(
            [
                (run, a, b, None, None, sid, 1.5, 0.25, 0.5, 3, 3, 0.61, linked, storm, trunc, TS)
                for sid, a, b, linked, trunc, storm in pairs
            ]
        )
        await store.commit()

    engine.members[sid_a] = [Member(*t, TS) for t in triples[:3]]
    engine.members[sid_b] = [Member(*t, TS) for t in triples[3:5]]

    async with store.lock:
        # A confirm from an unscoped operator, whose client reported a DIFFERENT bag.
        await engine.apply_feedback(
            sid_a,
            "confirm",
            TS + 30.0,
            principal_ref="alice",
            role="editor",
            label=LabelContext(
                client=ClientFingerprint.accept([triples[0][0], triples[1][0]], TS + 25.0)
            ),
        )
        # A split from a scoped operator who could not see two of the members.
        from netcorenoc.engine.dataset.capture import LabelScope

        await engine.apply_feedback(
            sid_b,
            "split",
            TS + 600.0,
            principal_ref="bob",
            role="editor",
            label=LabelContext(scope=LabelScope(policy_id=7, restricted=True, redacted_members=2)),
        )
        # A verdict on a situation with no members at all — the zero-member bag.
        await engine.apply_feedback(sid_c, "confirm", TS + 90.0, principal_ref="alice")
        # v0.9.1: a PARTIAL split — the operator marked one of the three members of `sid_a` as
        # not belonging. One asserted negative pair per remaining member, and the remainder left
        # UNASSERTED. Without this row the informativeness section would be all zeros and the gate
        # would notice nothing about the feature the release exists for.
        engine.members[sid_d] = [Member(*t, TS) for t in triples[6:9]]
        await engine.apply_feedback(
            sid_d,
            "split",
            TS + 120.0,
            principal_ref="alice",
            role="editor",
            label=LabelContext(exclusion=Exclusion.accept([triples[6][0]], None)),
        )
        # v0.9.1: a verdict acquired through the CLOSE channel rather than on a card. A different
        # population, reported separately and never averaged.
        await engine.apply_feedback(
            sid_b,
            "confirm",
            TS + 130.0,
            principal_ref="bob",
            role="editor",
            label=LabelContext(channel="close"),
        )
        # And a pre-v0.7.5 row, marked as the migration would have marked it.
        await store.conn.execute(
            "INSERT INTO feedback (situation_id, verdict, created_at, capture_provenance) "
            "VALUES (?, 'split', ?, 'legacy_capture')",
            (sid_a, TS + 5.0),
        )
        await store.commit()
