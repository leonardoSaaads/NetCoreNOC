"""Running a playbook on a case, before the NOC is called (ADR #463).

The case loop sends a new case to `auto` when an enabled playbook matches its situation
(`automation`). This loop picks every `auto` case up and walks its playbook:

1. **Each step's action runs outside the lock** (`executor.py`, #454). The credential is the
   vault's: one whose host is the element's address, whose agent access a custodian switched on —
   the same switch an agent's lease needs — and whose access level is the least that suffices. It
   is opened in this process and never written anywhere. A first SSH connection pins the host key.
2. **Every step is recorded**: an `internal` event in the case feed with what ran, where, how it
   ended and the device's answer (bounded, and said to be the device's text), and an audit row
   (`noc.exec`) under the playbook's name — the bot that used the credential.
3. **The leaf is applied under the lock**, and only while the case is still `auto`: a person who
   took it in the meantime wins, and is left the trace in the feed. `resolve` resolves the case and
   closes its situation, clearing the alarms when the leaf says they were verified clear;
   `pending` writes the leaf's customer update and waits until the next update; `queue` sends it
   to the NOC.
4. **A run that outlives its playbook's `timeout_s`** sends the case to the NOC queue, saying at
   which step it stopped; so does a run that fails, one whose conclusion could not be applied, and
   one a restart interrupted.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from netcorenoc.crosscutting import audit
from netcorenoc.engine.noc import case_ops, commands, executor, playbook, stepact
from netcorenoc.engine.noc.case_ops import Actor

if TYPE_CHECKING:
    from netcorenoc.store import Store

__all__ = ["Broker", "advise", "applies", "automation", "run_case", "run_loop"]

log = logging.getLogger("netcorenoc")

LOOK_EVERY_S = 2.0
#: ADR #492: the longest a run asked for on demand may take — it answers the request that asked.
ON_DEMAND_S = 120
#: How much of each answer an on-demand run hands back to whoever asked (the feed has more).
ADVICE_EXCERPT = 1500


@dataclass(frozen=True)
class Broker:
    """What a resolution needs from the broker, which this domain may not import (#459):
    ``close`` closes the situation — clearing its alarms when asked — inside the caller's
    transaction and says whether it closed; ``forget`` runs after the commit."""

    close: Callable[[int, bool, str], Awaitable[bool]]
    forget: Callable[[int], None]


@dataclass
class _Run:
    sid: int
    run_id: int
    chosen: Mapping[str, Any]
    doc: Mapping[str, Any]
    facts: Mapping[str, Any]
    trace: list[dict[str, Any]]
    #: ADR #492: who asked for an on-demand run — the actor of its checks and its feed lines.
    invoker: Actor | None = None
    #: The automation stops when a person or an agent takes the case; a run asked for does not.
    watch: bool = True
    #: What each step's answer began with, handed back to whoever asked (never stored).
    seen: list[dict[str, Any]] | None = None

    @property
    def actor(self) -> Actor:
        if self.invoker is not None:
            return self.invoker
        return Actor(
            f"playbook:{self.chosen['id']}", f"Routine “{self.chosen['name']}”", "agent", None
        )


def applies(row: Mapping[str, Any], facts: Mapping[str, Any]) -> bool:
    """ADR #492: a routine applies to a case when it is for the case's customer (or for every
    customer) and its match fits the situation."""
    owner = row.get("organization_id")
    if owner is not None and owner != facts.get("organization_id"):
        return False
    return playbook.matches(json.loads(row["document"]), facts)


async def automation(store: Store, sid: int) -> bool:
    """The case loop's hook: does an enabled routine match this situation?"""
    return (await _choose(store, sid)) is not None


async def _choose(store: Store, sid: int) -> dict[str, Any] | None:
    facts = await store.noc_situation_facts(sid)
    # The store lists a customer's own routines before every customer's: the specific one wins.
    for row in await store.noc_playbooks(enabled_only=True):
        if applies(row, facts):
            return row
    return None


async def run_case(
    store: Store, sid: int, broker: Broker, *, now: Callable[[], float] = time.time
) -> str:
    """Walk the matching playbook on one `auto` case; returns how the run ended."""
    async with store.lock:
        case = await store.noc_case(sid)
        if case is None or case["status"] != "auto":
            return "taken"
        chosen = await _choose(store, sid)
        if chosen is None:
            await case_ops.requeue(store, sid, "automation", now(), note="No playbook matches.")
            await store.commit()
            return "queue"
        run_id = await store.noc_run_start(int(chosen["id"]), int(chosen["version"]), sid, now())
        facts = await store.noc_situation_facts(sid)
        await store.commit()
    run = _Run(sid, run_id, chosen, json.loads(chosen["document"]), facts, [])
    try:
        leaf = await _walk(run, store, now)
    except Exception as exc:  # a failed run is the NOC's to finish, never a stuck case
        log.exception("playbook %s on case %s failed", chosen["id"], sid)
        leaf = {"end": "error", "why": type(exc).__name__}
    try:
        return await _finish(run, store, leaf, broker, now)
    except Exception:  # nor is a conclusion that could not be applied: left `auto`, it reruns
        log.exception("playbook %s could not conclude case %s", chosen["id"], sid)
        async with store.lock:
            await store.rollback()
            at = now()
            case = await store.noc_case(sid)
            if case is not None and case["status"] == "auto":
                note = "Automated checks could not apply their conclusion."
                await case_ops.requeue(store, sid, "automation", at, note=note)
            await store.noc_run_end(run.run_id, at, "error", "[]")
            await store.commit()
        return "error"


async def _walk(run: _Run, store: Store, now: Callable[[], float]) -> Mapping[str, Any]:
    deadline = time.monotonic() + int(run.doc.get("timeout_s", 300))
    host = str(run.facts.get("ip") or "")
    slots = {"ip": host or None, "entity": run.facts.get("entity")}
    name = str(run.doc["start"])
    while True:
        step = run.doc["steps"][name]
        if "end" in step:
            try:
                return playbook.fill(step, slots)
            except commands.CommandError as exc:
                return {"end": "queue", "note": f"Automated checks reached “{name}”: {exc}"}
        left = deadline - time.monotonic()
        if left <= 0:
            return {"end": "timeout", "at": name}
        async with store.reading():
            current = await store.noc_case(run.sid)
        if current is None or (run.watch and current["status"] != "auto"):
            return {"end": "taken"}
        outcome = executor.Outcome(True, "decide", "", "no action", 0.0)
        if "do" in step:
            outcome = await stepact.act(store, run.sid, run.facts, run.actor, step["do"], left)
            async with store.lock:
                text = stepact.event_text(name, step["do"], outcome, host)
                await case_ops.event(store, run.sid, text, now(), run.actor)
                await store.commit()
        if run.seen is not None:
            run.seen.append({"step": name, "output": outcome.output[:ADVICE_EXCERPT]})
        run.trace.append(
            {
                "step": name,
                "ok": outcome.ok,
                "detail": outcome.detail[:200],
                "ms": outcome.duration_ms,
                "sha256": hashlib.sha256(outcome.output.encode()).hexdigest()
                if outcome.output
                else None,
            }
        )
        try:
            name = next(
                branch["go"]
                for branch in step["next"]
                if "if" not in branch
                or playbook.test(
                    branch["if"],
                    ok=outcome.ok,
                    output=outcome.output,
                    vendor=run.facts.get("vendor"),
                    slots=slots,
                )
            )
        except commands.CommandError as exc:  # a test that cannot be read is not a decision
            return {"end": "queue", "note": f"Automated checks could not decide at “{name}”: {exc}"}


async def advise(
    store: Store,
    chosen: Mapping[str, Any],
    sid: int,
    invoker: Actor,
    *,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """ADR #492: run a routine on a case because an agent (or a lead) asked, and report.

    The same walk as the automation — the same read-only checks, each audited as `noc.exec`
    under the one who asked, each answer a feed line on the case — but **nothing is applied**:
    the leaf the routine reaches (resolve, wait, the NOC queue) is handed back as advice, and
    whoever asked decides. Bounded by :data:`ON_DEMAND_S`, because the request is waiting.
    """
    async with store.lock:
        run_id = await store.noc_run_start(
            # Who asked, by name for the screen; the audit row below carries the exact ref.
            int(chosen["id"]),
            int(chosen["version"]),
            sid,
            now(),
            invoker.name[:120],
        )
        facts = await store.noc_situation_facts(sid)
        await store.commit()
    doc = dict(json.loads(chosen["document"]))
    doc["timeout_s"] = min(int(doc.get("timeout_s", 300)), ON_DEMAND_S)
    run = _Run(sid, run_id, chosen, doc, facts, [], invoker=invoker, watch=False, seen=[])
    try:
        leaf = dict(await _walk(run, store, now))
    except Exception as exc:  # the report says so; the case is untouched either way
        log.exception("routine %s asked for on case %s failed", chosen["id"], sid)
        leaf = {"end": "error", "why": type(exc).__name__}
    async with store.lock:
        at = now()
        await store.noc_run_end(
            run_id, at, "advised", json.dumps(run.trace, separators=(",", ":"))[:60000]
        )
        await audit.write_event(
            store,
            ts=at,
            actor=invoker.ref,
            role=invoker.role,
            source_ip=None,
            action="noc.playbook.run",
            outcome="ok",
            object_type="noc_case",
            object_id=str(sid),
            details={
                "playbook": chosen["id"],
                "version": chosen["version"],
                "outcome": "advised",
                "advice": str(leaf.get("end")),
                "steps": len(run.trace),
            },
        )
        await store.commit()
    seen = {s["step"]: s["output"] for s in run.seen or []}
    return {
        "run_id": run_id,
        "routine": {"id": chosen["id"], "name": chosen["name"], "version": chosen["version"]},
        "advice": leaf,
        # The device's own words, marked as such — as `/api/noc/exec` answers them (G6).
        "steps": [
            {**s, "output": {"untrusted": True, "text": seen.get(s["step"], "")}} for s in run.trace
        ],
    }


async def _finish(
    run: _Run,
    store: Store,
    leaf: Mapping[str, Any],
    broker: Broker,
    now: Callable[[], float],
) -> str:
    sid, actor, kind = run.sid, run.actor, str(leaf["end"])
    closed = False
    async with store.lock:
        at = now()
        case = await store.noc_case(sid)
        if case is None or case["status"] != "auto":
            outcome = "taken"
        elif kind == "resolve":
            clear = bool(leaf.get("clear_alarms", False))
            await case_ops.resolve(
                store,
                case,
                actor,
                at,
                cause=str(leaf["cause"]),
                solution=str(leaf["solution"]),
                alarms_cleared=clear,
                admin=True,
                resolution="automation",
            )
            closed = await broker.close(sid, clear, actor.ref)
            outcome = "resolved"
        elif kind == "pending":
            done = await case_ops.auto_pend(
                store,
                case,
                actor,
                at,
                status=str(leaf["status"]),
                update=leaf["update"],
                wait_s=int(leaf.get("next_update_in_s", 3600)),
                note=str(leaf.get("note", "")),
            )
            outcome = "pending" if done else "queue"
        else:
            note = {
                "timeout": f"Automated checks timed out at “{leaf.get('at')}”.",
                "error": f"Automated checks failed ({leaf.get('why')}).",
            }.get(kind, str(leaf.get("note", "")))
            await case_ops.requeue(store, sid, "automation", at, note=note)
            outcome = kind if kind in ("timeout", "error") else "queue"
        trace = json.dumps(run.trace, separators=(",", ":"))[:60000]
        await store.noc_run_end(run.run_id, at, outcome, trace)
        await audit.write_event(
            store,
            ts=at,
            actor=actor.ref,
            role=None,
            source_ip=None,
            action="noc.playbook.run",
            outcome="ok",
            object_type="noc_case",
            object_id=str(sid),
            details={
                "playbook": run.chosen["id"],
                "version": run.chosen["version"],
                "outcome": outcome,
                "steps": len(run.trace),
            },
        )
        await store.commit()
    if closed:
        broker.forget(sid)
    return outcome


async def run_loop(store: Store, broker: Broker) -> None:
    """The supervised loop: every `auto` case gets a run, one at a time per case."""
    async with store.lock:
        at = time.time()
        for sid in await store.noc_runs_abandoned(at):
            await case_ops.requeue(store, sid, "automation", at, note="Interrupted by a restart.")
        await store.commit()
    running: dict[int, asyncio.Task[str]] = {}
    while True:
        async with store.reading():
            auto = await store.noc_cases_in("auto")
        for sid in auto:
            if sid not in running:
                running[sid] = asyncio.create_task(run_case(store, sid, broker))
        for sid in [s for s, task in running.items() if task.done()]:
            running.pop(sid)
        await asyncio.sleep(LOOK_EVERY_S)
