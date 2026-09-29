"""Recording a generated stream **through the real appliance**, so training cannot skew from
serving.

The usual way a model trained offline goes wrong in production is that the offline features were
computed by different code, or from different state, than the online ones. This module removes the
possibility rather than testing for it: every generated trap is BER-encoded to a real datagram,
parsed by `netcorenoc.ingest.receiver.parse_trap`, and handed to the real `Engine._process` over an
in-memory store, with the real maintenance sweep running on the stream's own clock (entity and
severity promotion, idle closing, persistence). The recorder only **listens**: it wraps
`Correlator.process` to copy out each activation's candidates and their feature vectors, and
`Store.clear_alarm` to note each clear.

What it writes is a :class:`StreamLog`: an ordered list of activations and clears, each activation
carrying its ground truth. That log is enough to

* derive training rows (each candidate pair, labelled same-incident or not), and
* **re-run the grouping step under any model** without replaying the stream again — because
  candidate recall and every feature are functions of the stream alone (ADR #405, #406), only the
  evidence and the placement depend on the model, and both are cheap to recompute.

The recording scorer is a one-feature *probe* model: its only job is to make the correlator take
the two-stage path. Its decisions are discarded.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "tools"))

from netcorenoc.engine.correlate.correlate import CorrelationResult, WindowAlarm  # noqa: E402
from netcorenoc.engine.correlate.learn import Learner  # noqa: E402
from netcorenoc.engine.correlate.scorer_contract import CONTRACT_VERSION  # noqa: E402
from netcorenoc.engine.model import gam  # noqa: E402
from netcorenoc.engine.operate.engine import Engine  # noqa: E402
from netcorenoc.ingest.events import TrapEvent  # noqa: E402
from netcorenoc.store import Store  # noqa: E402

import harness  # noqa: E402
from synth.compose import EPOCH, Stream  # noqa: E402

__all__ = ["PROBE", "Activation", "StreamLog", "record"]

#: The recording decider: valid, reachable, and deliberately uninformative.
PROBE = json.dumps(
    {
        "features": ["dt"],
        "format": gam.FORMAT,
        "grouping": {"join_bias": 0.0, "merge_bias": 1.0, "merge_min_pairs": 3.0},
        "interactions": [],
        "intercept": 0.0,
        "shapes": [{"edges": [30.0], "feature": "dt", "scores": [1.0, -1.0]}],
        "threshold": 0.0,
    },
    sort_keys=True,
    separators=(",", ":"),
)

#: How often the maintenance sweep runs, in stream seconds. Production runs it every 5 s; the
#: recorder runs it every five minutes. What the sweep does that features can see — entity and
#: severity promotion, persistence, pruning — needs hundreds of observations and changes on a
#: scale of minutes, so a coarser cadence moves *when* a promotion lands by at most a few minutes
#: and costs a sixtieth of the sweeps.
MAINT_EVERY_S = 300.0


@dataclass
class Activation:
    alarm_id: int
    ts: float
    incident: str
    family: str
    teaches: bool
    #: The activating trap was a CLEAR the appliance has not learned to recognise yet, so it became
    #: an alarm of its own. Truthfully part of its incident; reported apart, because grouping it is
    #: the clear-pair learner's job before it is the model's (see `evaluate.situation_metrics`).
    clear: bool = False
    #: (other alarm id, from the window rather than recall, feature vector)
    candidates: list[tuple[int, bool, tuple[float, ...]]] = field(default_factory=list)


@dataclass
class StreamLog:
    name: str
    #: ("a", Activation) | ("c", (alarm_id, ts))
    ops: list[tuple[str, Any]] = field(default_factory=list)
    incidents: dict[str, str] = field(default_factory=dict)
    recurs: dict[str, str] = field(default_factory=dict)
    concurrent: dict[str, tuple[str, str]] = field(default_factory=dict)
    traps: int = 0
    flap_suppressed: int = 0

    def activations(self) -> list[Activation]:
        return [op[1] for op in self.ops if op[0] == "a"]

    def dump(self, path: Path) -> None:
        ops: list[list[Any]] = []
        for kind, payload in self.ops:
            if kind == "a":
                a: Activation = payload
                ops.append(
                    [
                        "a",
                        a.alarm_id,
                        a.ts,
                        a.incident,
                        a.family,
                        a.teaches,
                        int(a.clear),
                        [[c, int(w), list(v)] for c, w, v in a.candidates],
                    ]
                )
            else:
                ops.append(["c", payload[0], payload[1]])
        body = {
            "name": self.name,
            "incidents": self.incidents,
            "recurs": self.recurs,
            "concurrent": self.concurrent,
            "traps": self.traps,
            "flap_suppressed": self.flap_suppressed,
            "ops": ops,
        }
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            json.dump(body, fh, separators=(",", ":"))

    @classmethod
    def load(cls, path: Path) -> StreamLog:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            body = json.load(fh)
        log = cls(
            body["name"],
            incidents=body["incidents"],
            recurs=body["recurs"],
            concurrent={k: (v[0], v[1]) for k, v in body["concurrent"].items()},
            traps=body["traps"],
            flap_suppressed=body["flap_suppressed"],
        )
        for op in body["ops"]:
            if op[0] == "a":
                log.ops.append(
                    (
                        "a",
                        Activation(
                            op[1],
                            op[2],
                            op[3],
                            op[4],
                            op[5],
                            bool(op[6]),
                            [(c, bool(w), tuple(v)) for c, w, v in op[7]],
                        ),
                    )
                )
            else:
                log.ops.append(("c", (op[1], op[2])))
        return log


async def _record(stream: Stream) -> StreamLog:
    store = Store(":memory:")
    await store.open()
    try:
        mv = await store.insert_model_version(
            kind=gam.KIND,
            contract_version=CONTRACT_VERSION,
            params_document=PROBE,
            params_hash=gam.fingerprint(PROBE),
            challenger_run_id=None,
            created_by="recorder",
            created_at=EPOCH,
            note="recording probe",
        )
        await store.set_active_model_version(mv, "recorder", EPOCH)
        # The probe is a site-family model; without this the default decider — the shipped model,
        # or the formula when none is installed — would run, and the formula reads no v2 vector.
        await store.set_decider_mode("site", "recorder", EPOCH, "recording probe")
        await store.commit()
        engine = Engine(store, asyncio.Queue())
        await engine.start()
        log = StreamLog(
            stream.spec.name,
            incidents=dict(stream.incidents),
            recurs=dict(stream.recurs),
            concurrent=dict(stream.concurrent),
        )
        current: dict[str, Any] = {}
        original_process = engine.correlator.process

        def listening_process(
            new: WindowAlarm, learner: Learner, **kwargs: Any
        ) -> CorrelationResult:
            outcome = original_process(new, learner, **kwargs)
            in_window = {c.alarm_id for c in outcome.considered}
            truth = current["truth"]
            act = Activation(
                new.alarm_id,
                new.ts,
                str(truth["situation_key"]),
                str(truth["family"]),
                bool(kwargs.get("teaches", True)),
                bool(truth.get("clear", False)),
                [
                    (p.other.alarm_id, p.other.alarm_id in in_window, p.vector or ())
                    for p in outcome.evaluated
                ],
            )
            log.ops.append(("a", act))
            return outcome

        engine.correlator.process = listening_process  # type: ignore[method-assign]

        async def no_capture(*_args: Any, **_kwargs: Any) -> None:
            return None

        # The feedback-dataset capture writes one row per evaluated pair and reads nothing the
        # features depend on; a recording has no use for it and it is a fifth of the cost.
        engine.capture.record = no_capture  # type: ignore[method-assign]
        original_clear = store.clear_alarm

        async def listening_clear(
            device_id: int, raise_class_id: int, instance: str, ts: float
        ) -> int | None:
            cleared = await original_clear(device_id, raise_class_id, instance, ts)
            if cleared is not None:
                log.ops.append(("c", (cleared, ts)))
            return cleared

        store.clear_alarm = listening_clear  # type: ignore[method-assign]
        next_maint = EPOCH + MAINT_EVERY_S
        cache: dict[str, TrapEvent | None] = {}
        async with store.lock:
            for event in stream.events:
                ts = EPOCH + event.arrival
                while ts >= next_maint:
                    await store.commit()
                    store.lock.release()
                    try:
                        await engine.maintenance(now=next_maint, retention_days=3650.0)
                    finally:
                        await store.lock.acquire()
                    next_maint += MAINT_EVERY_S
                item = _parse(event.as_json(), ts, cache)
                if item is None:
                    continue
                log.traps += 1
                current["truth"] = event.as_json()["truth"]
                await engine._process(item)
            await store.commit()
        log.flap_suppressed = len(engine.flapping)
        return log
    finally:
        await store.close()


def _parse(
    event: dict[str, Any], ts: float, cache: dict[str, TrapEvent | None]
) -> TrapEvent | None:
    """The real encode-and-parse, memoised on the datagram's content.

    Two traps with the same source, OID and varbinds encode to the same bytes (the encoder's uptime
    is fixed) and therefore parse to the same event; only the arrival time differs, and it is
    stamped on a copy. A cache hit is the parser's own earlier answer, never a reconstruction.
    """
    key = json.dumps([event["source"], event["trap_oid"], event["varbinds"]], separators=(",", ":"))
    if key not in cache:
        item, parsed = harness._to_item(event, 0.0)
        cache[key] = item if parsed and isinstance(item, TrapEvent) else None
    hit = cache[key]
    return None if hit is None else hit.model_copy(update={"ts": ts})


def record(stream: Stream) -> StreamLog:
    return asyncio.run(_record(stream))
