"""Learned severity (S8, §5.3): a varbind becomes severity only when it is severity-shaped
*and* its candidate ordering is borne out by observed alarm lifetimes. When it cannot be
validated, severity stays unknown (NULL) — a fabricated severity is worse than none."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import domdriver
import uifixtures
from netcorenoc.engine.correlate import severity
from netcorenoc.engine.correlate.varbind_profile import VarbindProfiler
from netcorenoc.ingest.events import TrapEvent, Varbind
from netcorenoc.main import Engine
from netcorenoc.store import Store
from test_dom_harness import dom_test

import authutil

DEV = "10.30.0.1"
CLS_A = "1.3.6.1.4.1.9.9.999.0.1"
CLS_B = "1.3.6.1.4.1.9.9.999.0.2"
SEV_OID = "1.3.6.1.4.1.9.9.999.1.1.5"  # a vendor perceived-severity varbind
ID_OID = "1.3.6.1.4.1.9.9.999.1.1.1"  # a per-port identifier (never a severity)
BASE = 6_000_000.0
_SEV_CYCLE = ("critical", "major", "minor")
_LIFETIME = {"critical": 300.0, "major": 120.0, "minor": 30.0}  # monotone in rank


def _event(cls: str, inst: str, sev: str, ts: float) -> TrapEvent:
    return TrapEvent(
        device=DEV,
        trap_oid=cls,
        instance=inst,
        ts=ts,
        varbinds=[
            Varbind(oid=ID_OID, kind="str", value=inst),
            Varbind(oid=SEV_OID, kind="str", value=sev),
        ],
    )


async def _seed_history(engine: Engine, store: Store, n: int = 240) -> int:
    """Feed the profiler cross-class observations and build closed alarms whose lifetimes are
    ordered by severity (critical lives longest here). Returns the NE id."""
    ne_id = await store.ne_id(DEV, BASE)
    class_a = await store.class_id(CLS_A, BASE)
    class_b = await store.class_id(CLS_B, BASE)
    device_id = await store.device_id(DEV, BASE)
    async with store.lock:
        for i in range(n):
            sev = _SEV_CYCLE[i % 3]
            cls_oid, cls_id = (CLS_A, class_a) if i % 2 == 0 else (CLS_B, class_b)
            inst = f"port-{i}"
            ev = _event(cls_oid, inst, sev, BASE + i)
            engine.profiler.observe(ne_id, cls_id, [(v.oid, v.value) for v in ev.varbinds], ev.ts)
            await store.ingest(ev)
            await store.clear_alarm(device_id, cls_id, inst, ev.ts + _LIFETIME[sev])
        await store.commit()
    return ne_id


async def _alarm_severity(store: Store, instance: str) -> tuple[Any, Any]:
    async with store.lock:
        cur = await store.conn.execute(
            "SELECT severity, severity_rank FROM alarm WHERE instance=?", (instance,)
        )
        row = await cur.fetchone()
    assert row is not None
    return row["severity"], row["severity_rank"]


async def test_severity_is_confirmed_against_lifetimes_and_set_forward_only(store: Store) -> None:
    engine, _queue, _app = await authutil.make_env(store)
    ne_id = await _seed_history(engine, store)

    await engine._maybe_confirm_severity(ne_id, BASE + 10_000)
    assert engine.ne_severity.get(ne_id) == SEV_OID  # the varbind was confirmed as severity

    async with store.lock:
        cur = await store.conn.execute(
            "SELECT outcome, details FROM audit_log WHERE action='severity.confirm'"
        )
        rows = [dict(r) for r in await cur.fetchall()]
    assert rows and rows[0]["outcome"] == "ok"
    assert SEV_OID in rows[0]["details"]

    # A new raise carrying the severity varbind is now labelled; rank follows the vocabulary.
    async with store.lock:
        await engine._process(_event(CLS_A, "new-major", "major", BASE + 20_000))
        await engine._process(_event(CLS_B, "new-crit", "critical", BASE + 20_001))
        await store.commit()
    assert await _alarm_severity(store, "new-major") == ("major", 1)
    assert await _alarm_severity(store, "new-crit") == ("critical", 0)


async def test_unrecognised_or_absent_value_stays_unknown(store: Store) -> None:
    """Honest fallback: on a confirmed severity field, a value outside the vocabulary and a
    trap that omits the field both leave severity NULL — never a fabricated default."""
    engine, _queue, _app = await authutil.make_env(store)
    ne_id = await _seed_history(engine, store)
    await engine._maybe_confirm_severity(ne_id, BASE + 10_000)
    assert engine.ne_severity.get(ne_id) == SEV_OID

    async with store.lock:
        bogus = _event(CLS_A, "weird", "chartreuse", BASE + 30_000)
        await engine._process(bogus)
        missing = TrapEvent(
            device=DEV,
            trap_oid=CLS_A,
            instance="no-sev",
            ts=BASE + 30_001,
            varbinds=[Varbind(oid=ID_OID, kind="str", value="no-sev")],
        )
        await engine._process(missing)
        await store.commit()
    assert await _alarm_severity(store, "weird") == (None, None)
    assert await _alarm_severity(store, "no-sev") == (None, None)


async def test_severity_survives_restart(store: Store) -> None:
    engine, _queue, _app = await authutil.make_env(store)
    ne_id = await _seed_history(engine, store)
    await engine._maybe_confirm_severity(ne_id, BASE + 10_000)
    await engine.maintenance(now=BASE + 10_001, retention_days=365.0)  # persists the role

    fresh = Engine(store, asyncio.Queue())
    await fresh.start()
    assert fresh.ne_severity.get(ne_id) == SEV_OID  # reloaded from the persisted 'severity' role


# -- pure detection / validation ---------------------------------------------------------


def _profiler_with(values_a: list[str], values_b: list[str]) -> VarbindProfiler:
    prof = VarbindProfiler()
    for i, val in enumerate(values_a):
        prof.observe(1, 10, [(SEV_OID, val), (ID_OID, f"p{i}")], float(i))
    for i, val in enumerate(values_b):
        prof.observe(1, 11, [(SEV_OID, val), (ID_OID, f"q{i}")], 1000.0 + i)
    return prof


def test_candidate_picks_the_small_ordinal_cross_class_field() -> None:
    prof = _profiler_with(list(_SEV_CYCLE) * 60, list(_SEV_CYCLE) * 60)  # 180 + 180 obs, 2 classes
    cand = severity.severity_candidate(prof, 1, entity_oids=set())
    assert cand is not None
    assert cand.varbind_oid == SEV_OID and cand.kind == "vocab"
    assert set(cand.ranks) == {"critical", "major", "minor"}


def test_candidate_ignores_the_entity_discriminator_and_high_cardinality() -> None:
    prof = _profiler_with(list(_SEV_CYCLE) * 60, list(_SEV_CYCLE) * 60)
    # The identifier is high-cardinality (never a severity); excluding SEV_OID leaves nothing.
    assert severity.severity_candidate(prof, 1, entity_oids={SEV_OID}) is None


def test_candidate_accepts_integer_severities() -> None:
    """Ordinality can also come from raw integers, not just the bundled vocabulary (§5.3)."""
    prof = _profiler_with(["1", "2", "3"] * 60, ["1", "2", "3"] * 60)
    cand = severity.severity_candidate(prof, 1, entity_oids=set())
    assert cand is not None
    assert cand.kind == "int" and cand.ranks == {"1": 1, "2": 2, "3": 3}


def test_confirm_ordinality_requires_monotone_lifetimes_with_spread() -> None:
    cand = severity.SeverityCandidate(
        SEV_OID, "vocab", {"critical": 0, "major": 1, "minor": 2}, 240, 2
    )
    monotone = [("critical", 300.0)] * 20 + [("major", 120.0)] * 20 + [("minor", 30.0)] * 20
    assert severity.confirm_ordinality(cand, monotone)

    scrambled = [("critical", 30.0)] * 20 + [("major", 300.0)] * 20 + [("minor", 120.0)] * 20
    assert not severity.confirm_ordinality(cand, scrambled)  # not monotone in rank

    flat = [("critical", 100.0)] * 20 + [("major", 100.0)] * 20 + [("minor", 100.0)] * 20
    assert not severity.confirm_ordinality(cand, flat)  # no spread: does not stratify

    too_few = [("critical", 300.0)] * 10 + [("minor", 30.0)] * 10  # < SEVERITY_MIN_CLOSED
    assert not severity.confirm_ordinality(cand, too_few)


def test_normalize_maps_vocab_and_integers_and_rejects_junk() -> None:
    assert severity.normalize("Critical") == ("critical", 0)
    assert severity.normalize("minor") == ("minor", 2)
    assert severity.normalize("3") == ("3", 3)
    assert severity.normalize("not-a-severity") == (None, None)


# --- v0.16.2: what the CONSOLE does with what the appliance learned ------------------------

#: One alarm per band, in `SEVERITY_VOCAB`'s own tokens plus the two the console must not place.
#: `warning` is rank 3 and IS a placement; `indeterminate` is rank 4 and is not; a `None` severity
#: is the never-learned case, which a zero-config appliance is in on its first day.
BANDS: list[tuple[str | None, int | None, str]] = [
    ("critical", 0, "sev-crit"),
    ("major", 1, "sev-major"),
    ("minor", 2, "sev-minor"),
    ("warning", 3, "sev-low"),
    ("indeterminate", 4, "sev-unknown"),
    (None, None, "sev-unknown"),
]


def _with_bands(routes: dict[str, Any], sid: int) -> dict[str, Any]:
    """The captured payload with one alarm per band, so every band is on screen at once.

    A real corpus resolves **no** severity — 0 of 2 252 alarms, measured by
    `tools/evidence/severity_census.py` — which is a fact about the corpus and about the floors
    `severity.py` refuses below. It is not a reason to leave four of the five bands unrendered by
    any test in this repository.
    """
    import copy

    doctored = copy.deepcopy(routes)
    alarms = doctored[f"/api/situations/{sid}"]["json"]["alarms"]
    assert len(alarms) >= len(BANDS), f"the corpus situation has {len(alarms)} members"
    for alarm, (value, rank, _band) in zip(alarms, BANDS, strict=False):
        alarm["severity"] = value
        alarm["severity_rank"] = rank
    return doctored


@dom_test
async def test_every_severity_band_carries_a_glyph_and_text_not_only_colour(
    store: Store,
) -> None:
    """**The accessibility rule, at the DOM** (DECISIONS #276, #277).

    `format.js` has documented since v0.13.0 that every severity carries a colour AND a glyph AND
    its text, *"because colour alone fails for a colour-blind operator and on a bad monitor at
    3 a.m."* Nothing checked it. This drives the real console over a real capture and reads back
    what the members table rendered, for **every band including `unknown`** — a badge that carries
    colour alone fails here, which is the whole reason it exists.

    Three properties, and each is a different way the pill could quietly stop being three
    encodings:

    * every pill carries a non-empty **glyph** and a non-empty **word**;
    * the glyphs are distinct **shapes** — `critical` and `major` both drew `▲` until this release,
      which made two adjacent bands one encoding rather than three, and they are the pair that also
      collides on hue under deuteranopia;
    * the word is in the cell's own **text**, not only in a `title=`, because a tooltip is not a
      rendering.
    """
    routes = await uifixtures.all_routes(store)
    sid, count = uifixtures.largest_situation(routes["editor"])
    assert count >= len(BANDS)
    result = domdriver.run_scenario(
        "severityBands", {"routes": _with_bands(routes["editor"], sid), "sid": sid}
    )
    cells = result["cells"][: len(BANDS)]
    assert len(cells) == len(BANDS), f"the members table rendered {len(cells)} severity cells"

    glyphs: list[str] = []
    for cell, (value, _rank, band) in zip(cells, BANDS, strict=True):
        assert band in cell["classes"].split(), f"{value!r} rendered as {cell['classes']!r}"
        assert cell["glyph"].strip(), f"{value!r} rendered no glyph — colour alone"
        assert cell["text"].strip(), f"{value!r} rendered no text — colour alone"
        assert cell["text"].strip() in cell["cellText"], (
            f"{value!r}'s word is not in the cell's text; a title= is not a rendering"
        )
        glyphs.append(cell["glyph"].strip())

    # `warning` is a placement on the scale and `indeterminate` is not, and they must not look the
    # same: rendering the vocabulary's own word for "I do not know" as *low* is a claim about
    # seriousness the element never made (DECISIONS #276).
    assert cells[3]["classes"] != cells[4]["classes"], (
        "`warning` and `indeterminate` render as the same band"
    )
    # The never-learned case shares `unknown`'s band and must still be distinguishable by its word.
    assert cells[4]["text"].strip() != cells[5]["text"].strip(), (
        "a learned `indeterminate` and a never-learned severity render identically"
    )
    assert len(set(glyphs[:4])) == 4, f"two placed bands share a glyph: {glyphs[:4]}"


def test_only_one_module_renders_a_severity() -> None:
    """**One component, every severity surface** (DECISIONS #277), enforced rather than intended.

    V.3's reason for the component is that *two screens disagreeing about what `major` looks like*
    is the defect it exists to prevent — and today there is exactly one consumer, which is when a
    rule like this is cheapest to install and most likely to be forgotten. A second screen that
    wanted a severity would reach for the class, not the component, because the class is what a
    reader sees in the stylesheet.

    The stylesheet is where the bands are *defined* and is not scanned; the assertion is that no
    module **emits** one except the module that owns the pill.
    """
    import netcorenoc

    root = Path(netcorenoc.__file__).resolve().parent / "ui" / "app"
    emitters = sorted(
        str(path.relative_to(root))
        for path in root.rglob("*.js")
        if "sev-" in path.read_text(encoding="utf-8")
    )
    assert emitters == ["widgets.js"], (
        f"modules other than widgets.js emit a severity class: {emitters}. The pill is one "
        "component so that two screens cannot disagree about what `major` looks like."
    )
