"""Autonomy: the model classifying incidents without a human — graded, explained, self-stopping.

v0.26.0 (ADR #412). Part IV.4 of the brief asks for four properties, and each is structural here:

* **Graded.** Four acts, each switched on its own in `autonomy_setting`:

  - ``grouping`` — accept a grouping as an incident (`new` → `open`) when the model's weakest link
    inside it is at least ``confidence_floor`` probable;
  - ``naming`` — give a situation nobody has named a name built from what is in it;
  - ``closing`` — resolve a situation whose remaining alarms have gone quiet and can never clear on
    their own (their class has no clear this appliance knows), by hand-clearing them **as the
    model** — the zombie-clear gesture an operator has, with the model as its actor;
  - ``severity`` — set the situation's X.733 severity from its members and how far it spreads.

* **Attributed and explainable.** Every act writes one `autonomy_decision` row naming the decider
  (`shipped:<sha12>`, `site:<id>`), its confidence, and the evidence as JSON — for a grouping, the
  weakest and strongest links with their three largest terms. The console shows the row.

* **Self-suspending on a measured trigger.** Each act is later judged by what the operators did
  next: a restructuring gesture on an accepted grouping, a rename of a model's name, an operator's
  severity over the model's, or a stale-cleared alarm raising again, is a *disagreement*; an
  operator working the situation without contradicting the act is an *agreement*. When agreement
  over the last ``window`` judged acts falls below ``agreement_floor`` (with at least
  ``min_judged`` judged), autonomy writes a settings row with every grade off, set by
  ``autonomy`` and saying why. Only an admin turns it back on.

* **A kill switch** — `POST /api/autonomy/stop`, one click from the console's top bar on every
  screen, writes the same all-off row set by the person who pressed it.

**Where it runs.** The maintenance loop, after the maintenance pass, under `store.lock` for its own
short transaction — never on the ingest path. At most :data:`MAX_ACTS` acts per sweep.

**What it never does.** It acts only on situations a *model* formed: the additive formula has no
probability to be confident with. It never undoes an operator: a situation an operator has named,
restructured or given a severity is not touched by that grade again.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from netcorenoc.crosscutting import audit
from netcorenoc.engine.dataset import gestures
from netcorenoc.engine.operate import membership
from netcorenoc.engine.operate.autonomy_judge import judge
from netcorenoc.ingest import trappack

if TYPE_CHECKING:  # pragma: no cover - type-only, no runtime edge
    from netcorenoc.engine.operate.engine import Engine

__all__ = ["GRADES", "Settings", "status", "sweep"]

GRADES = ("grouping", "naming", "closing", "severity")
MAX_ACTS = 50
SETTLE_S = 120.0  # a situation this young may still be growing; grouping waits
STALE_S = 1800.0  # quiet this long before closing may act
SPREAD_ELEMENTS = 5  # a situation over this many elements is one level more severe
SEVERITIES = ("critical", "major", "minor", "warning", "indeterminate")


@dataclass(frozen=True)
class Settings:
    grouping: bool = False
    naming: bool = False
    closing: bool = False
    severity: bool = False
    agreement_floor: float = 0.8
    window: int = 30
    min_judged: int = 8
    confidence_floor: float = 0.9
    set_by: str = ""
    reason: str = ""
    set_at: float = 0.0

    @classmethod
    def from_row(cls, row: dict[str, Any] | None) -> Settings:
        if row is None:
            return cls()
        return cls(
            grouping=bool(row["grouping"]),
            naming=bool(row["naming"]),
            closing=bool(row["closing"]),
            severity=bool(row["severity"]),
            agreement_floor=float(row["agreement_floor"]),
            window=int(row["window"]),
            min_judged=int(row["min_judged"]),
            confidence_floor=float(row["confidence_floor"]),
            set_by=str(row["set_by"]),
            reason=str(row["reason"]),
            set_at=float(row["set_at"]),
        )

    @property
    def enabled(self) -> tuple[str, ...]:
        return tuple(g for g in GRADES if getattr(self, g))

    @property
    def suspended(self) -> bool:
        """Stopped by autonomy itself, as opposed to never switched on or stopped by a person."""
        return not self.enabled and self.set_by == "autonomy"

    def values(self, **changes: Any) -> dict[str, Any]:
        base = {
            "grouping": int(self.grouping),
            "naming": int(self.naming),
            "closing": int(self.closing),
            "severity": int(self.severity),
            "agreement_floor": self.agreement_floor,
            "window": self.window,
            "min_judged": self.min_judged,
            "confidence_floor": self.confidence_floor,
        }
        return {**base, **changes}


def _p(score: float, threshold: float) -> float:
    z = max(-700.0, min(700.0, score))
    del threshold  # a model's link score is its logit; the probability needs no threshold
    return 1.0 / (1.0 + math.exp(-z))


async def status(engine: Engine) -> dict[str, Any]:
    """What the console's top bar shows on every screen."""
    settings = Settings.from_row(await engine.store.autonomy_setting())
    verdicts = await engine.store.recent_verdicts(settings.window)
    agreed = verdicts.count("agreed")
    return {
        "active": bool(settings.enabled),
        "grades": list(settings.enabled),
        "suspended": settings.suspended,
        "set_by": settings.set_by,
        "reason": settings.reason,
        "decider": engine.decider_ref,
        "model_decider": not engine.decider_ref.startswith("additive"),
        "agreement": {
            "judged": len(verdicts),
            "agreed": agreed,
            "rate": agreed / len(verdicts) if verdicts else None,
            "floor": settings.agreement_floor,
            "window": settings.window,
            "min_judged": settings.min_judged,
        },
    }


async def sweep(engine: Engine, now: float) -> None:
    """One autonomy pass. Caller holds ``store.lock`` and commits."""
    store = engine.store
    settings = Settings.from_row(await store.autonomy_setting())
    if not settings.enabled:
        return
    await judge(engine, now)
    verdicts = await store.recent_verdicts(settings.window)
    if len(verdicts) >= settings.min_judged:
        rate = verdicts.count("agreed") / len(verdicts)
        if rate < settings.agreement_floor:
            await _suspend(engine, now, settings, rate, len(verdicts))
            return
    if engine.decider_ref.startswith("additive"):
        return  # the formula has no probability to be confident with
    budget = MAX_ACTS
    candidates = await store.autonomy_candidates(now - SETTLE_S, limit=200)
    for row in candidates:
        if budget <= 0:
            break
        budget -= await _act(engine, settings, row, now)


async def _act(engine: Engine, settings: Settings, row: dict[str, Any], now: float) -> int:
    store = engine.store
    sid = int(row["id"])
    decider = str(row["decider"] or "")
    if not decider or decider.startswith("additive"):
        return 0
    done = {d["grade"] for d in await store.autonomy_decisions(situation_id=sid, limit=20)}
    acts = 0
    links = await store.situation_link_scores(sid)
    confidence, explanation = _grouping_evidence(links)
    members = await store.situation_profile(sid)
    ref = f"model:{engine.decider_ref}"
    if (
        settings.grouping
        and "grouping" not in done
        and row["status"] == "new"
        and len(members) >= 2
        and confidence >= settings.confidence_floor
    ):
        await store.promote_situation(sid, now, "autonomy")
        await _record(
            engine, sid, "grouping", "accepted as an incident", confidence, explanation, now
        )
        acts += 1
    if (
        settings.naming
        and "naming" not in done
        and row["operator_name"] is None
        and row["model_name"] is None
        and len(members) >= 2
        and confidence >= settings.confidence_floor
    ):
        name = _name(members)
        if await store.set_situation_name_by_model(sid, name):
            await _record(
                engine,
                sid,
                "naming",
                f"named {name!r}",
                confidence,
                {**explanation, "name": name},
                now,
            )
            acts += 1
    if settings.severity and (
        row["severity_by"] is None or str(row["severity_by"]).startswith("model:")
    ):
        word, why = _severity(members)
        if word is not None and word != row["severity"]:
            placed = sum(1 for m in members if m["severity_rank"] is not None)
            await store.set_situation_severity(sid, word, ref)
            await _record(
                engine,
                sid,
                "severity",
                f"set to {word}",
                placed / len(members),
                {"members": len(members), "placed": placed, "why": why},
                now,
            )
            acts += 1
    if settings.closing and "closing" not in done and float(row["updated_at"]) <= now - STALE_S:
        acts += await _close(engine, sid, members, now)
    return acts


def _grouping_evidence(links: list[dict[str, Any]]) -> tuple[float, dict[str, Any]]:
    """The weakest and strongest link, each with its three largest terms, and the confidence."""
    if not links:
        return 0.0, {"links": 0}
    scored = sorted(links, key=lambda r: float(r["score"]))

    def describe(r: dict[str, Any]) -> dict[str, Any]:
        terms = r.get("terms") or {}
        top = sorted(terms.get("terms", []), key=lambda t: -abs(float(t[2])))[:3]
        return {
            "alarms": [int(r["alarm_a"]), int(r["alarm_b"])],
            "probability": round(_p(float(r["score"]), 0.0), 4),
            "terms": [[t[0], round(float(t[2]), 3)] for t in top],
        }

    weakest = _p(float(scored[0]["score"]), 0.0)
    return weakest, {
        "links": len(links),
        "weakest": describe(scored[0]),
        "strongest": describe(scored[-1]),
    }


def _name(members: list[dict[str, Any]]) -> str:
    """ "<most common trap> xN · <root element> (+k elements)" — from what the situation holds."""
    names = Counter(_class_name(str(m["class_oid"])) for m in members)
    top, count = names.most_common(1)[0]
    devices = sorted({str(m["device_ip"]) for m in members})
    root = next((str(m["device_ip"]) for m in members if m.get("is_root")), devices[0])
    more = f" (+{len(devices) - 1} elements)" if len(devices) > 1 else ""
    return f"{top} x{count} · {root}{more}"[:120]


def _class_name(oid: str) -> str:
    entry = trappack.pack().notifications.get(oid)
    return (
        entry.name if entry is not None else oid.rsplit(".", 2)[-2] + "." + oid.rsplit(".", 1)[-1]
    )


def _severity(members: list[dict[str, Any]]) -> tuple[str | None, str]:
    """The worst member's X.733 severity, one level higher when it spans many elements."""
    ranks = [int(m["severity_rank"]) for m in members if m["severity_rank"] is not None]
    if not ranks:
        return None, "no member carries a severity"
    worst = min(max(0, r) for r in ranks)
    worst = min(worst, len(SEVERITIES) - 1)
    spread = len({str(m["device_ip"]) for m in members})
    if spread >= SPREAD_ELEMENTS and 0 < worst < len(SEVERITIES) - 1:
        return SEVERITIES[worst - 1], (
            f"worst member {SEVERITIES[worst]}, one level higher: it spans {spread} elements"
        )
    return SEVERITIES[worst], f"worst member {SEVERITIES[worst]}"


async def _close(engine: Engine, sid: int, members: list[dict[str, Any]], now: float) -> int:
    """Hand-clear the quiet alarms that can never clear on their own, as the model."""
    active = [m for m in members if m["status"] == "active"]
    cleared = len(members) - len(active)
    if not active or cleared == 0:
        return 0
    learner = engine.learner
    clearable = set(learner.clears.raise_to_clear) | {c for (c, _o) in learner.states.clear_value}
    if any(int(m["class_id"]) in clearable for m in active):
        return 0  # one of them can still clear by itself: wait for the network
    store = engine.store
    confidence = cleared / len(members)
    subject = await gestures.snapshot(store, sid)
    for m in active:
        aid = int(m["alarm_id"])
        if not await store.manual_clear_alarm(aid, now):
            continue
        membership.cleared(engine, aid)
        await gestures.record(
            store,
            gestures.Gesture(
                kind="manual_clear",
                situation_id=sid,
                at=now,
                actor=f"model:{engine.decider_ref}",
                role=None,
                alarm_id=aid,
            ),
            subject,
        )
    if await store.all_cleared(sid):
        await store.resolve_situation(sid, "manual_clear", now)
        engine.forget_situation(sid)
    await _record(
        engine,
        sid,
        "closing",
        "resolved: remaining alarms were quiet and cannot clear themselves",
        confidence,
        {
            "stale_cleared": [int(m["alarm_id"]) for m in active],
            "cleared_by_network": cleared,
            "quiet_s": STALE_S,
        },
        now,
    )
    return 1


async def _record(
    engine: Engine,
    sid: int,
    grade: str,
    action: str,
    confidence: float,
    explanation: dict[str, Any],
    now: float,
) -> None:
    store = engine.store
    await store.add_autonomy_decision(
        situation_id=sid,
        grade=grade,
        action=action,
        decider=engine.decider_ref,
        confidence=max(0.0, min(1.0, confidence)),
        explanation=explanation,
        at=now,
    )
    await audit.write_event(
        store,
        ts=now,
        actor=f"model:{engine.decider_ref}",
        role=None,
        source_ip=None,
        action=f"autonomy.{grade}",
        outcome="ok",
        object_type="situation",
        object_id=str(sid),
        details={"action": action, "confidence": round(confidence, 4)},
    )


async def _suspend(
    engine: Engine, now: float, settings: Settings, rate: float, judged: int
) -> None:
    reason = (
        f"agreement with the operators was {rate:.0%} over the last {judged} judged decisions, "
        f"below the {settings.agreement_floor:.0%} floor"
    )
    off = settings.values(grouping=0, naming=0, closing=0, severity=0)
    await engine.store.set_autonomy(off, "autonomy", now, reason)
    await audit.write_event(
        engine.store,
        ts=now,
        actor="autonomy",
        role=None,
        source_ip=None,
        action="autonomy.suspend",
        outcome="ok",
        object_type="autonomy",
        object_id=None,
        details={"rate": round(rate, 4), "judged": judged, "floor": settings.agreement_floor},
    )
