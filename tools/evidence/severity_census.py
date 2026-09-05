"""How much severity a real corpus resolves, and what stands between it and the rest.

**The measurement v0.16.3 needs, taken by v0.16.2 because taking it now costs nothing.** The
console's severity pill renders whatever the appliance learned, and `unknown` is a first-class
outcome — so the question the next release has to answer is not *"is the badge pretty"* but *"how
often is there anything to badge"*.

`engine/correlate/severity.py` will not commit to a severity field for an NE until **two
independent tests agree**, and each carries a floor:

    SEVERITY_MIN_OBS       = 200   observations of the varbind on that NE
    SEVERITY_MIN_CLOSED    = 50    closed alarms, to validate ordinality against lifetimes
    SEVERITY_MIN_PER_VALUE = 5     closed alarms per value before its median lifetime is trusted

Those are per **network element**, not per appliance, and the whole labelled corpus is 3 159 events
across ten scenarios. This tool replays every one of them through a live `Engine` — wire encoding,
the real parser, the real batch loop, the real maintenance sweep — and reports what came out:
alarms with a resolved severity, alarms with none, and how close the best NE got to each floor.

**The controls.** A zero here could be a property of the query rather than of the corpus, which is
the failure `tools/corpus_census.py` was built to refuse. Two guards:

  * `varbind_observations` is reported per NE, so a zero severity count beside a zero observation
    count says *"the corpus carries no severity-shaped varbind"* and a zero beside 40 000 says
    *"the appliance refused one it saw"*. Those are different findings.
  * the vocabulary itself is reported, resolved through `known_oids.severity_rank`, so a corpus
    whose severity tokens are not in the bundled vocabulary is visible as such rather than as an
    unexplained refusal.

Run it from the repository root:

    python tools/evidence/severity_census.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
for extra in ("tests", "src", "tools"):
    sys.path.insert(0, str(ROOT / extra))

CORPUS = ROOT / "eval" / "corpus"

#: One clock. Scenarios are separated by an hour of synthetic time, exactly as the corpus census
#: separates them, so one scenario's window cannot correlate with the next one's.
BASE = 1_700_000_000.0
HOUR = 3600.0
NO_PRUNE_DAYS = 3650.0


async def census() -> dict[str, Any]:
    import corpus_census
    from netcorenoc.engine.correlate import severity as severity_rules
    from netcorenoc.ingest import known_oids
    from netcorenoc.store import Store

    import authutil
    import util

    db = ROOT / ".demos" / "severity_census.db"
    db.parent.mkdir(exist_ok=True)
    for stale in db.parent.glob("severity_census.db*"):
        stale.unlink()
    store = Store(str(db))
    await store.open()
    try:
        engine, queue, _app = await authutil.make_env(store)
        scenarios = sorted(p.name for p in CORPUS.glob("*.json"))
        for index, name in enumerate(scenarios):
            events = corpus_census.scenario_events(CORPUS / name, BASE + index * HOUR)
            await util.drive(engine, queue, events)
            # The sweep is where a severity field is confirmed (`_maybe_confirm_severity`), so a
            # census that never ran maintenance would be measuring a half-run appliance.
            await engine.maintenance(BASE + (index + 1) * HOUR, retention_days=NO_PRUNE_DAYS)
        async with store.lock:
            cur = await store.conn.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(severity IS NOT NULL) AS with_severity, "
                "SUM(severity_rank IS NOT NULL) AS with_rank FROM alarm"
            )
            row = await cur.fetchone()
            assert row is not None
            cur = await store.conn.execute(
                "SELECT severity, COUNT(*) AS n FROM alarm GROUP BY severity ORDER BY n DESC"
            )
            by_value = [(r["severity"], int(r["n"])) for r in await cur.fetchall()]
            cur = await store.conn.execute(
                "SELECT ne_id, COUNT(*) AS n FROM alarm GROUP BY ne_id ORDER BY n DESC"
            )
            per_ne = {int(r["ne_id"]): int(r["n"]) for r in await cur.fetchall()}
            cur = await store.conn.execute("SELECT COUNT(*) AS n FROM alarm WHERE status='cleared'")
            closed_row = await cur.fetchone()
            assert closed_row is not None
        # What the profiler actually accumulated, per NE — the control that tells a corpus with no
        # severity-shaped varbind apart from an appliance that refused one it saw.
        observations = {
            ne_id: max((c.n_obs for c in engine.profiler.candidates(ne_id)), default=0)
            for ne_id in sorted(per_ne)
        }
        return {
            "scenarios": scenarios,
            "alarms_total": int(row["total"]),
            "alarms_with_severity": int(row["with_severity"] or 0),
            "alarms_with_rank": int(row["with_rank"] or 0),
            "alarms_cleared": int(closed_row["n"]),
            "severity_values": by_value,
            "alarms_per_ne": per_ne,
            "best_varbind_observations_per_ne": observations,
            "nes_with_a_confirmed_severity_field": sorted(engine.ne_severity),
            # The two floors, compared against what the corpus reached, so the tool states which
            # one binds rather than leaving a reader to divide two numbers and hope.
            "nes_clearing_the_observation_floor": sorted(
                ne for ne, n in observations.items() if n >= severity_rules.SEVERITY_MIN_OBS
            ),
            "closed_alarms_against_the_ordinality_floor": [
                int(closed_row["n"]),
                severity_rules.SEVERITY_MIN_CLOSED,
            ],
            "floors": {
                "SEVERITY_MIN_OBS": severity_rules.SEVERITY_MIN_OBS,
                "SEVERITY_MIN_CLOSED": severity_rules.SEVERITY_MIN_CLOSED,
                "SEVERITY_MIN_PER_VALUE": severity_rules.SEVERITY_MIN_PER_VALUE,
                "SEVERITY_MAX_DISTINCT": severity_rules.SEVERITY_MAX_DISTINCT,
            },
            "bundled_vocabulary": known_oids.SEVERITY_VOCAB,
        }
    finally:
        await store.close()


async def main() -> None:
    print(json.dumps(await census(), indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    asyncio.run(main())
