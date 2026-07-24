"""Deterministic offline replay of the labelled corpus into a metrics JSON.

The harness reproduces the real ingestion path — it BER-encodes each corpus event to a
genuine trap datagram, runs it through :func:`netcorenoc.receiver.parse_trap` (v1 traps that
the running version cannot decode are quarantined exactly as they would be on the wire),
and drives the resulting item through the engine. It then aligns every predicted alarm to
ground truth and computes the metrics in :mod:`metrics`.

Determinism is a hard requirement (a non-deterministic gate is worthless): a fixed base
clock, event ordering by ``delay``, no ``time.time()`` in the scored path, and a synthetic
queueing model for latency. ``test_eval.py`` runs the harness twice and asserts identical
output.

Usage::

    python eval/harness.py                       # replay, print delta vs the frozen baseline
    python eval/harness.py --json                # emit the full metrics JSON
    python eval/harness.py --write-baseline P     # freeze the current metrics to path P
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from pyasn1.codec.ber import encoder
from pysnmp.proto import api

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "tools"))
sys.path.insert(0, str(HERE))

from netcorenoc.events import QuarantinedPacket, TrapEvent  # noqa: E402
from netcorenoc.main import Engine  # noqa: E402
from netcorenoc.receiver import (  # noqa: E402
    QueueItem,
    TrapParseError,
    parse_trap,
    quarantine_packet,
)
from netcorenoc.store import Store  # noqa: E402

import metrics  # noqa: E402
import trap_replay  # noqa: E402  (v2c encoder, shared with the replay tool)

CORPUS_DIR = HERE / "corpus"
BASELINE = HERE / "baselines" / "v0.2.0.json"
BASE_TS = 1_000_000.0
SERVICE_S = 0.0003  # synthetic per-trap service time for the deterministic latency model
GATE_METRICS = ("pairwise_f1", "ari", "entity_accuracy")
GATE_TOLERANCE = 0.01
HARNESS_VERSION = 1

_V1 = api.PROTOCOL_MODULES[api.SNMP_VERSION_1]


def _encode_v1(event: dict[str, Any]) -> bytes:
    """BER-encode an SNMPv1 enterprise-specific trap for the camera archetype."""
    pdu = _V1.TrapPDU()
    _V1.apiTrapPDU.set_defaults(pdu)
    _V1.apiTrapPDU.set_enterprise(pdu, event["enterprise"])
    _V1.apiTrapPDU.set_generic_trap(pdu, 6)  # enterpriseSpecific
    _V1.apiTrapPDU.set_specific_trap(pdu, int(event["specific"]))
    _V1.apiTrapPDU.set_agent_address(pdu, event.get("agent_addr", event["source"]))
    bound = [(vb["oid"], _V1.OctetString(vb["value"])) for vb in event.get("varbinds", [])]
    _V1.apiTrapPDU.set_varbinds(pdu, bound)
    message = _V1.Message()
    _V1.apiMessage.set_defaults(message)
    _V1.apiMessage.set_community(message, "public")
    _V1.apiMessage.set_pdu(message, pdu)
    return bytes(encoder.encode(message))


def _encode(event: dict[str, Any]) -> bytes:
    if int(event.get("version", 2)) == 1:
        return _encode_v1(event)
    # v2c: the standard varbinds are prepended by the encoder; skip a caller-supplied uptime.
    varbinds = [vb for vb in event.get("varbinds", []) if vb["oid"] != trap_replay.SYS_UPTIME_OID]
    return trap_replay.encode_trap(event["trap_oid"], varbinds, "public", 1)


def _to_item(event: dict[str, Any], ts: float) -> tuple[QueueItem, bool]:
    """Encode and parse one event exactly as the receiver would; returns (item, parsed)."""
    wire = _encode(event)
    try:
        return parse_trap(event["source"], wire, ts), True
    except TrapParseError as exc:
        return quarantine_packet(event["source"], wire, exc.reason, ts), False


MAINT_EVERY = 50  # run a maintenance sweep (promotion, flush) this often during replay


async def _drive(engine: Engine, items: list[QueueItem], promote: bool = True) -> int:
    """Replay items through the engine. With ``promote`` (learning mode, ``make eval``) a
    maintenance sweep runs between chunks so promotions fire during the replay exactly as they
    would every few seconds in production. Without it (cold/parity mode) no sweep runs, so the
    output is byte-identical to v0.2.0 — this is how the frozen baseline was produced and how
    the parity gate is checked. Returns peak tracked objects over the run."""
    peak = 0
    last_ts = BASE_TS
    for start in range(0, len(items), MAINT_EVERY):
        chunk = items[start : start + MAINT_EVERY]
        async with engine.store.lock:
            for item in chunk:
                await engine._process(item)
                if isinstance(item, TrapEvent):
                    last_ts = max(last_ts, item.ts)
                live = len(engine.correlator.index) + sum(len(m) for m in engine.members.values())
                peak = max(peak, live)
            await engine.store.commit()
        if promote:
            # A promotion sweep at the synthetic chunk time (no wall clock — deterministic).
            await engine.maintenance(now=last_ts + 0.001, retention_days=365.0)
    return peak


def _synthetic_latencies(event_ts: list[float]) -> list[float]:
    """Deterministic queueing model: each trap takes SERVICE_S to process; a burst that
    arrives faster than it drains accrues latency. No wall clock — reproducible."""
    clock = 0.0
    out: list[float] = []
    for ts in sorted(event_ts):
        start = max(ts, clock)
        finish = start + SERVICE_S
        out.append(finish - ts)
        clock = finish
    return out


async def _read_alarms(store: Store) -> tuple[list[dict[str, Any]], bool]:
    """All alarms with their reporting NE ip, class oid, instance, predicted entity id and
    predicted situation id. ``has_entity`` reports whether the entity model is present."""
    cur = await store.conn.execute("PRAGMA table_info(alarm)")
    cols = {str(r[1]) for r in await cur.fetchall()}
    has_entity = "entity_id" in cols
    entity_col = "a.entity_id" if has_entity else "a.device_id"
    cur = await store.conn.execute(
        f"SELECT a.id, d.ip AS device_ip, c.oid AS class_oid, a.instance, "
        f"{entity_col} AS entity, sa.situation_id "  # nosec B608 - entity_col is a fixed literal
        "FROM alarm a JOIN device d ON d.id=a.device_id JOIN alarm_class c ON c.id=a.class_id "
        "LEFT JOIN situation_alarm sa ON sa.alarm_id=a.id ORDER BY a.id, sa.situation_id"
    )
    return [dict(r) for r in await cur.fetchall()], has_entity


async def _situation_roots(store: Store) -> dict[int, int | None]:
    cur = await store.conn.execute("SELECT id, root_alarm_id FROM situation ORDER BY id")
    return {
        int(r["id"]): (int(r["root_alarm_id"]) if r["root_alarm_id"] is not None else None)
        for r in await cur.fetchall()
    }


class _AlarmScore:
    """One predicted alarm annotated with ground truth, ready for scoring."""

    __slots__ = ("pred_entity", "pred_root", "pred_sit", "truth_entity", "truth_root", "truth_sit")

    def __init__(
        self,
        pred_sit: str,
        truth_sit: str,
        pred_entity: str,
        truth_entity: str,
        pred_root: bool,
        truth_root: bool,
    ) -> None:
        self.pred_sit = pred_sit
        self.truth_sit = truth_sit
        self.pred_entity = pred_entity
        self.truth_entity = truth_entity
        self.pred_root = pred_root
        self.truth_root = truth_root


async def run_scenario(path: Path, promote: bool = True) -> dict[str, Any]:
    """Replay one corpus file and return its scored alarms plus scalar counters."""
    scenario = json.loads(path.read_text())
    name = scenario["name"]
    events = sorted(scenario["events"], key=lambda e: float(e["delay"]))

    store = Store(":memory:")
    await store.open()
    engine = Engine(store, asyncio.Queue())
    await engine.start()

    items: list[QueueItem] = []
    parsed_events: list[tuple[dict[str, Any], TrapEvent]] = []
    ingest_ts: list[float] = []
    quarantined = 0
    for event in events:
        ts = BASE_TS + float(event["delay"])
        item, parsed = _to_item(event, ts)
        items.append(item)
        if parsed and isinstance(item, TrapEvent):
            parsed_events.append((event, item))
            ingest_ts.append(ts)
        elif isinstance(item, QuarantinedPacket):
            quarantined += 1

    peak = await _drive(engine, items, promote=promote)
    alarm_rows, _ = await _read_alarms(store)
    roots = await _situation_roots(store)

    # An alarm that re-activates after its situation closes accumulates several
    # situation_alarm rows; count it once, in its most recent situation.
    collapsed: dict[int, dict[str, Any]] = {}
    for r in alarm_rows:
        aid = int(r["id"])
        prev = collapsed.get(aid)
        sid = -1 if r["situation_id"] is None else int(r["situation_id"])
        prev_sid = -1 if prev is None or prev["situation_id"] is None else int(prev["situation_id"])
        if prev is None or sid > prev_sid:
            collapsed[aid] = r
    alarm_rows = list(collapsed.values())

    # Map (device_ip, class_oid, instance) -> alarm row. The engine stores the *heuristic*
    # instance at level 0 and the *discriminator value* once an entity is promoted; by corpus
    # design the discriminator value equals the event's truth entity_key, so an event is
    # matched by its parsed instance first (level 0), then by its truth entity_key (promoted).
    by_fp: dict[tuple[str, str, str], dict[str, Any]] = {
        (r["device_ip"], r["class_oid"], r["instance"]): r for r in alarm_rows
    }
    # Attach each parsed event's truth to the alarm it landed on.
    alarm_truth: dict[int, dict[str, Any]] = {}  # alarm id -> aggregated truth
    matched_traps = 0
    for event, te in parsed_events:
        truth = event["truth"]
        row = by_fp.get((te.device, te.trap_oid, te.instance)) or by_fp.get(
            (te.device, te.trap_oid, str(truth["entity_key"]))
        )
        if row is None:
            continue  # a clear event that closed an alarm of another class: not a raise
        matched_traps += 1
        bucket = alarm_truth.setdefault(int(row["id"]), {"sit": [], "entity": [], "root": False})
        bucket["sit"].append(truth["situation_key"])
        bucket["entity"].append(truth["entity_key"])
        bucket["root"] = bucket["root"] or bool(truth.get("is_root"))

    scored: list[_AlarmScore] = []
    for row in alarm_rows:
        aid = int(row["id"])
        if aid not in alarm_truth:
            continue
        t = alarm_truth[aid]
        truth_sit = _majority(t["sit"])
        truth_entity = _majority(t["entity"])
        sid = row["situation_id"]
        pred_sit = f"{name}:sit{sid}" if sid is not None else f"{name}:solo{aid}"
        pred_root = sid is not None and roots.get(int(sid)) == aid
        scored.append(
            _AlarmScore(
                pred_sit=pred_sit,
                truth_sit=f"{name}:{truth_sit}",
                pred_entity=f"{name}:e{row['entity']}",
                truth_entity=f"{name}:{truth_entity}",
                pred_root=pred_root,
                truth_root=t["root"],
            )
        )

    await store.close()
    return {
        "name": name,
        "scored": scored,
        "distinct_alarms": len(alarm_truth),
        "traps_ingested": matched_traps,
        "quarantined": quarantined,
        "latencies": _synthetic_latencies(ingest_ts),
        "peak_tracked_objects": peak,
    }


def _majority(values: list[str]) -> str:
    from collections import Counter

    return Counter(values).most_common(1)[0][0]


def _score(scored: list[_AlarmScore]) -> dict[str, float]:
    """Grouping, entity and root metrics over a set of scored alarms."""
    pred_sit = [a.pred_sit for a in scored]
    truth_sit = [a.truth_sit for a in scored]
    pred_entity = [a.pred_entity for a in scored]
    truth_entity = [a.truth_entity for a in scored]
    considered, correct = _root_counts(scored)
    return {
        "pairwise_f1": metrics.pairwise_f1(pred_sit, truth_sit),
        "ari": metrics.adjusted_rand_index(pred_sit, truth_sit),
        "over_merge_rate": metrics.over_merge_rate(pred_sit, truth_sit),
        "under_merge_rate": metrics.under_merge_rate(pred_sit, truth_sit),
        "entity_accuracy": metrics.entity_accuracy(pred_entity, truth_entity),
        "root_top1": metrics.root_top1(considered, correct),
    }


def _root_counts(scored: list[_AlarmScore]) -> tuple[int, int]:
    """Per predicted situation: it is 'considered' if it contains a true root; 'correct' if
    the alarm it flags as root is a true root."""
    sits: dict[str, dict[str, bool]] = {}
    for a in scored:
        s = sits.setdefault(a.pred_sit, {"has_truth_root": False, "pred_root_is_true": False})
        s["has_truth_root"] = s["has_truth_root"] or a.truth_root
        if a.pred_root and a.truth_root:
            s["pred_root_is_true"] = True
    considered = sum(1 for s in sits.values() if s["has_truth_root"])
    correct = sum(1 for s in sits.values() if s["pred_root_is_true"])
    return considered, correct


def _round(d: dict[str, float]) -> dict[str, float]:
    return {k: round(v, 6) for k, v in d.items()}


async def run_all(promote: bool = True) -> dict[str, Any]:
    """Replay every corpus file (fixed order) and return the full metrics document."""
    results = [await run_scenario(p, promote=promote) for p in sorted(CORPUS_DIR.glob("*.json"))]

    scenarios: dict[str, Any] = {}
    pooled: list[_AlarmScore] = []
    pooled_latencies: list[float] = []
    tot_distinct = tot_traps = tot_quar = 0
    peak = 0
    for res in results:
        scored: list[_AlarmScore] = res["scored"]
        scen_metrics = _round(_score(scored))
        scen_metrics["dedup_ratio"] = round(
            metrics.dedup_ratio(res["distinct_alarms"], res["traps_ingested"]), 6
        )
        scen_metrics["distinct_alarms"] = res["distinct_alarms"]
        scen_metrics["traps_ingested"] = res["traps_ingested"]
        scen_metrics["quarantined"] = res["quarantined"]
        scenarios[res["name"]] = scen_metrics
        pooled.extend(scored)
        pooled_latencies.extend(res["latencies"])
        tot_distinct += res["distinct_alarms"]
        tot_traps += res["traps_ingested"]
        tot_quar += res["quarantined"]
        peak = max(peak, res["peak_tracked_objects"])

    aggregate = _round(_score(pooled))
    aggregate["dedup_ratio"] = round(metrics.dedup_ratio(tot_distinct, tot_traps), 6)
    aggregate["p95_ingest_latency_s"] = round(metrics.percentile(pooled_latencies, 0.95), 6)
    aggregate["peak_tracked_objects"] = peak
    aggregate["distinct_alarms"] = tot_distinct
    aggregate["traps_ingested"] = tot_traps
    aggregate["quarantined"] = tot_quar

    return {
        "harness_version": HARNESS_VERSION,
        "aggregate": aggregate,
        "scenarios": dict(sorted(scenarios.items())),
    }


def _fmt(v: Any) -> str:
    return f"{v:.4f}" if isinstance(v, float) else str(v)


def _delta_table(current: dict[str, Any], baseline: dict[str, Any]) -> str:
    lines = [
        "",
        "NetCoreNOC evaluation — aggregate metrics vs baseline",
        "=" * 62,
        f"{'metric':<26}{'baseline':>12}{'current':>12}{'delta':>12}",
    ]
    base_agg = baseline.get("aggregate", {})
    for key in current["aggregate"]:
        cur = current["aggregate"][key]
        base = base_agg.get(key)
        delta = ""
        if isinstance(cur, (int, float)) and isinstance(base, (int, float)):
            delta = f"{cur - base:+.4f}"
        gate = "  <- GATE" if key in GATE_METRICS else ""
        lines.append(f"{key:<26}{_fmt(base):>12}{_fmt(cur):>12}{delta:>12}{gate}")
    regressions = _regressions(current, baseline)
    lines.append("=" * 62)
    if regressions:
        lines.append("REGRESSIONS (beyond tolerance): " + ", ".join(regressions))
    else:
        lines.append("no gated regressions")
    return "\n".join(lines)


def _regressions(current: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    base_agg = baseline.get("aggregate", {})
    out = []
    for key in GATE_METRICS:
        cur = current["aggregate"].get(key)
        base = base_agg.get(key)
        if (
            isinstance(cur, int | float)
            and isinstance(base, int | float)
            and cur < base - GATE_TOLERANCE
        ):
            out.append(f"{key} {cur:.4f} < {base:.4f}-{GATE_TOLERANCE}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-baseline", type=Path, help="freeze current metrics to this path")
    parser.add_argument("--json", action="store_true", help="print the full metrics JSON")
    parser.add_argument("--baseline", type=Path, default=BASELINE, help="baseline to diff against")
    parser.add_argument(
        "--cold", action="store_true", help="cold/parity mode: no promotion (reproduces v0.2.0)"
    )
    args = parser.parse_args(argv)

    current = asyncio.run(run_all(promote=not args.cold))
    payload = json.dumps(current, indent=2, sort_keys=True) + "\n"

    if args.write_baseline:
        args.write_baseline.write_text(payload)
        print(f"wrote baseline to {args.write_baseline}")  # noqa: T201
        return 0
    if args.json:
        print(payload)  # noqa: T201
        return 0
    if args.baseline.exists():
        baseline = json.loads(args.baseline.read_text())
        print(_delta_table(current, baseline))  # noqa: T201
        return 1 if _regressions(current, baseline) else 0
    print(payload)  # noqa: T201
    print("(no baseline found; printed current metrics)")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
