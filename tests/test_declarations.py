"""The operator's declaration (v0.16.3): the name, the class, the severity, and the revert.

The appliance starts knowing nothing about the customer's network and becomes intelligent from the
trap stream. Three complaints arrived from three screens — *"I renamed the host and nothing changed
in Entities"*, *"what is Alarm Classes for if it changes nothing?"*, *"where is the severity?"* —
and they are one gap: nowhere for an operator to write down what they already knew.

Every test here drives the real routes over the real engine. The two properties that matter most
are the ones a release could break without noticing:

* **one write, three screens.** A name declared once must appear on the situation row, the Entities
  screen and the graph, because all three read one table. A screen that reads its own copy is the
  defect this release exists to end.
* **the learned value is never overwritten.** Precedence is a read-time decision, so both values
  are readable after a declaration and the appliance's own judgement comes back when it is
  withdrawn. An operator and 200 observations disagreeing is the cheapest evidence this project
  can buy, and an overwrite spends it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from netcorenoc.crosscutting import shaping
from netcorenoc.ingest import known_oids
from netcorenoc.ingest.receiver import QueueItem
from netcorenoc.main import Engine
from netcorenoc.store import Store

import authutil
import util

BASE = 2_000_000.0


@pytest.fixture
async def env(store: Store) -> tuple[Engine, asyncio.Queue[QueueItem], Any]:
    engine, queue, app = await authutil.make_env(store)
    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", BASE))
    return engine, queue, app


async def _one_alarm(client: Any) -> tuple[int, dict[str, Any]]:
    sid = (await client.get("/api/situations")).json()[0]["id"]
    detail = (await client.get(f"/api/situations/{sid}")).json()
    return int(sid), detail["alarms"][0]


# --- the naming, and the propagation it exists for --------------------------------------------


async def test_one_name_reaches_the_situation_row_the_entity_list_and_the_graph(
    env: tuple[Engine, asyncio.Queue[QueueItem], Any],
) -> None:
    """**The release's central claim, demonstrated rather than described.**

    Before v0.16.3 exactly one of these three resolved. `store/entities.py::list_ne` selected five
    columns from `ne` and joined no label while `views/entities.js` rendered `${ne.label || ne.ip}`,
    so the fallback to the address was permanent — and the label was not missing, because the alarm
    projection resolved the identical row. That asymmetry is what made it a broken join rather than
    a broken write, and it is why all three are asserted here from **one** POST.
    """
    _engine, _queue, app = env
    client = await authutil.client_as(app, "editor")
    try:
        sid, alarm = await _one_alarm(client)
        ne_id = int(alarm["ne_id"])
        assert alarm["device_label"] is None, "the fixture already carries a declaration"

        posted = await client.post(
            "/api/labels", json={"kind": "ne", "id": ne_id, "label": "CORE-SW-01"}
        )
        assert posted.status_code == 200, posted.text

        detail = (await client.get(f"/api/situations/{sid}")).json()
        row = next(a for a in detail["alarms"] if int(a["ne_id"]) == ne_id)
        assert row["device_label"] == "CORE-SW-01", "the situation row does not carry the name"

        entities = (await client.get("/api/entities")).json()
        entity = next(e for e in entities if int(e["id"]) == ne_id)
        assert "label" in entity, "/api/entities serves no label field at all"
        assert entity["label"] == "CORE-SW-01", "the Entities screen still shows the address"

        graph = (await client.get("/api/graph")).json()
        named = [n for n in graph["nodes"] if n.get("label") == "CORE-SW-01"]
        assert len(named) == 1, f"the graph shows {len(named)} nodes with the declared name"
    finally:
        await client.aclose()


async def test_a_label_written_against_the_losing_home_is_read_by_nothing(
    env: tuple[Engine, asyncio.Queue[QueueItem], Any],
) -> None:
    """`kind='device'` is **gone**, not aliased (DECISIONS #281).

    The request is refused at the model, and a row inserted behind the route's back — which is what
    a stale client or an old backup would leave — reaches no reader. Both halves are needed: the
    422 alone would pass against a reader that still joined `kind='device'`, and the read alone
    would pass against a route that quietly rewrote the kind.
    """
    engine, _queue, app = env
    client = await authutil.client_as(app, "editor")
    try:
        sid, alarm = await _one_alarm(client)
        ne_id = int(alarm["ne_id"])

        refused = await client.post(
            "/api/labels", json={"kind": "device", "id": ne_id, "label": "ghost"}
        )
        assert refused.status_code == 422, refused.text

        async with engine.store.lock:
            await engine.store.conn.execute(
                "INSERT INTO label (kind, target_id, qualifier, label, updated_at) "
                "VALUES ('device', ?, '', 'ghost', ?)",
                (ne_id, BASE),
            )
            await engine.store.commit()

        detail = (await client.get(f"/api/situations/{sid}")).json()
        assert all(a["device_label"] != "ghost" for a in detail["alarms"])
        assert all(e.get("label") != "ghost" for e in (await client.get("/api/entities")).json())
        nodes = (await client.get("/api/graph")).json()["nodes"]
        assert all(n.get("label") != "ghost" for n in nodes)
    finally:
        await client.aclose()


async def test_the_class_name_is_declared_once_and_the_vendor_never_becomes_it(
    env: tuple[Engine, asyncio.Queue[QueueItem], Any],
) -> None:
    """A vendor is served beside the name, never as one (DECISIONS #282).

    46 of 48 classes on a real corpus resolve a vendor and have no name at all, which is why the
    vendor is worth showing; `alarmName`'s chain is still `label || name || oid`, which is why it
    is not shown *there*. The control is the declaration: once a class has a name, the row's
    identity is that name and not the manufacturer.
    """
    _engine, _queue, app = env
    client = await authutil.client_as(app, "editor")
    try:
        sid, alarm = await _one_alarm(client)
        class_id = int(alarm["class_id"])
        assert alarm["class_label"] is None
        assert alarm["class_vendor"] == known_oids.vendor_of(alarm["class_oid"])
        assert alarm["class_name"] == known_oids.trap_name(alarm["class_oid"])

        await client.post("/api/labels", json={"kind": "class", "id": class_id, "label": "LOS"})
        detail = (await client.get(f"/api/situations/{sid}")).json()
        row = next(a for a in detail["alarms"] if int(a["class_id"]) == class_id)
        assert row["class_label"] == "LOS"
        # The vendor is still served — it is a fact — and the name chain now resolves before it.
        assert row["class_vendor"] == alarm["class_vendor"]

        listed = {int(c["id"]): c for c in (await client.get("/api/classes")).json()}
        assert listed[class_id]["label"] == "LOS"
        assert listed[class_id]["vendor"] == alarm["class_vendor"]
    finally:
        await client.aclose()


# --- the severity, and the two values kept side by side ---------------------------------------


async def test_a_declared_severity_wins_while_the_learned_one_stays_readable(
    env: tuple[Engine, asyncio.Queue[QueueItem], Any],
) -> None:
    """**Precedence, demonstrated both ways** (directive 4, DECISIONS #284).

    The corpus resolves no severity at all — 0 of 2 252 alarms — so the learned value is written
    directly here. That is not a shortcut around the gates: `severity.py` is untouched and this
    test asserts nothing about how a severity is learned. It asserts what a *reader* gets when
    both exist, which is the only thing a declaration changes.
    """
    engine, _queue, app = env
    client = await authutil.client_as(app, "editor")
    try:
        sid, alarm = await _one_alarm(client)
        class_id = int(alarm["class_id"])
        async with engine.store.lock:
            await engine.store.conn.execute(
                "UPDATE alarm SET severity='minor', severity_rank=2 WHERE class_id=?", (class_id,)
            )
            await engine.store.commit()

        before = next(
            a
            for a in (await client.get(f"/api/situations/{sid}")).json()["alarms"]
            if int(a["class_id"]) == class_id
        )
        assert (before["severity"], before["severity_rank"]) == ("minor", 2)
        assert before["declared_severity"] is None

        await client.post(
            "/api/labels", json={"kind": "severity", "id": class_id, "label": "critical"}
        )
        after = next(
            a
            for a in (await client.get(f"/api/situations/{sid}")).json()["alarms"]
            if int(a["class_id"]) == class_id
        )
        assert (after["declared_severity"], after["declared_severity_rank"]) == ("critical", 0)
        # **The learned value is untouched.** Not "still correct" — byte-identical to before.
        assert (after["severity"], after["severity_rank"]) == ("minor", 2)
        async with engine.store.lock:
            cur = await engine.store.conn.execute(
                "SELECT DISTINCT severity, severity_rank FROM alarm WHERE class_id=?", (class_id,)
            )
            assert [tuple(r) for r in await cur.fetchall()] == [("minor", 2)]

        # …and withdrawing it brings the appliance's own judgement back, unchanged.
        cleared = await client.delete(f"/api/labels/severity/{class_id}")
        assert cleared.status_code == 200, cleared.text
        reverted = next(
            a
            for a in (await client.get(f"/api/situations/{sid}")).json()["alarms"]
            if int(a["class_id"]) == class_id
        )
        assert reverted["declared_severity"] is None
        assert (reverted["severity"], reverted["severity_rank"]) == ("minor", 2)
    finally:
        await client.aclose()


async def test_a_severity_outside_the_bundled_vocabulary_is_refused(
    env: tuple[Engine, asyncio.Queue[QueueItem], Any],
) -> None:
    """A declared severity must land on one of the rendered bands (DECISIONS #283).

    `severity.py` refuses to fabricate a severity it cannot place — *"a fabricated severity is
    worse than none"* — and a declaration that could name a token nothing renders would be that
    same fabrication arriving through the front door. Every bundled token is accepted, which is
    the control: a route that refused everything would pass the first assertion alone.
    """
    _engine, _queue, app = env
    client = await authutil.client_as(app, "editor")
    try:
        _sid, alarm = await _one_alarm(client)
        class_id = int(alarm["class_id"])
        for bogus in ("severe", "MEDIUM", "7", ""):
            resp = await client.post(
                "/api/labels", json={"kind": "severity", "id": class_id, "label": bogus}
            )
            assert resp.status_code == 422, (bogus, resp.status_code, resp.text)
        for token in known_oids.SEVERITY_VOCAB:
            resp = await client.post(
                "/api/labels", json={"kind": "severity", "id": class_id, "label": token}
            )
            assert resp.status_code == 200, (token, resp.text)
    finally:
        await client.aclose()


async def test_a_declaration_that_contradicts_the_appliance_is_recorded_not_consumed(
    env: tuple[Engine, asyncio.Queue[QueueItem], Any],
) -> None:
    """The disagreement is a **record**, written server-side and without trusting the client.

    The console interrupts an operator whose declaration is two or more steps from what the alarm's
    own learned severity says; that governs a dialog and is measured in the DOM harness. What is
    worth keeping is the fact — an operator overriding a judgement two independent gates produced —
    so the audit row carries the declared rank beside every learned rank the class actually holds
    (DECISIONS #285). Nothing consumes it, and it produces no training row (#286).
    """
    engine, _queue, app = env
    client = await authutil.client_as(app, "editor")
    try:
        _sid, alarm = await _one_alarm(client)
        class_id = int(alarm["class_id"])
        async with engine.store.lock:
            await engine.store.conn.execute(
                "UPDATE alarm SET severity='minor', severity_rank=2 WHERE class_id=?", (class_id,)
            )
            await engine.store.commit()
        await client.post(
            "/api/labels", json={"kind": "severity", "id": class_id, "label": "critical"}
        )
        async with engine.store.lock:
            cur = await engine.store.conn.execute(
                "SELECT details FROM audit_log WHERE action='label.set' ORDER BY id DESC LIMIT 1"
            )
            row = await cur.fetchone()
        assert row is not None
        import json

        details = json.loads(str(row[0]))
        assert details["declared_rank"] == 0, details
        assert details["learned_ranks"] == [2], details
    finally:
        await client.aclose()


async def test_no_declaration_produces_a_training_row(
    env: tuple[Engine, asyncio.Queue[QueueItem], Any],
) -> None:
    """**Directive 6, and `PREREGISTRATION-0.16.0.md` §2's map is unamended** (DECISIONS #286).

    A name is not a claim about which alarms belong together, and a severity is a claim about a
    kind of trap rather than about a link. All three declarations are made, and every table the
    evidence chain reads is counted before and after — as a **set of tables derived from the
    schema**, not a list somebody remembered to keep current, because a list is exactly how F92
    and F98 happened.
    """
    engine, _queue, app = env
    client = await authutil.client_as(app, "editor")

    async def census() -> dict[str, int]:
        async with engine.store.lock:
            cur = await engine.store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND ("
                "name LIKE 'feedback%' OR name LIKE 'dataset%' OR name LIKE 'situation_event%')"
            )
            tables = sorted(str(r[0]) for r in await cur.fetchall())
            out: dict[str, int] = {}
            for table in tables:
                cur = await engine.store.conn.execute(f"SELECT COUNT(*) FROM {table}")  # nosec B608
                out[table] = int((await cur.fetchone())[0])  # type: ignore[index]
            return out

    try:
        _sid, alarm = await _one_alarm(client)
        assert alarm["ne_id"] and alarm["class_id"]
        before = await census()
        assert before, "no evidence tables were found; this test would pass vacuously"
        for body in (
            {"kind": "ne", "id": alarm["ne_id"], "label": "CORE-SW-01"},
            {"kind": "class", "id": alarm["class_id"], "label": "LOS"},
            {"kind": "severity", "id": alarm["class_id"], "label": "critical"},
        ):
            assert (await client.post("/api/labels", json=body)).status_code == 200
        await client.delete(f"/api/labels/severity/{alarm['class_id']}")
        assert await census() == before, "a declaration wrote into the evidence chain"
    finally:
        await client.aclose()


# --- the shaping axis, which declared text does not escape (F104) -------------------------------


@pytest.mark.parametrize("field", ["ne", "class"])
async def test_a_declared_name_cannot_reveal_an_address_shaping_hides(
    env: tuple[Engine, asyncio.Queue[QueueItem], Any], field: str
) -> None:
    """F104, with its control (DECISIONS #287).

    `FIELD_RULES` reasoned that a label *"is free text a person typed, and a label passes through"*.
    That is true of the string's origin and false of its content: an editor may type an address the
    same response coarsens two fields away. It is commit `8609962`'s defect in the declared
    register, on a release that puts declared text on four screens.

    The control is the same body with the declaration withdrawn — without it, a coarsener that had
    simply stopped serving the field would score green.
    """
    _engine, _queue, app = env
    editor = await authutil.client_as(app, "editor")
    viewer = await authutil.client_as(app, "viewer")
    try:
        sid, alarm = await _one_alarm(editor)
        address = str(alarm["device_ip"])
        target = int(alarm["ne_id"] if field == "ne" else alarm["class_id"])
        await editor.post(
            "/api/labels", json={"kind": field, "id": target, "label": f"core-sw at {address}"}
        )

        for path in (f"/api/situations/{sid}", "/api/graph", "/api/entities", "/api/classes"):
            body = (await viewer.get(path)).text
            assert address not in body, f"a viewer received the raw address through {path}"
        # …and an editor, who may see addresses, still gets the name they typed.
        assert address in (await editor.get(f"/api/situations/{sid}")).text

        await editor.delete(f"/api/labels/{field}/{target}")
        control = (await viewer.get(f"/api/situations/{sid}")).text
        assert address not in control, "the control leaks, so the probe proves nothing"
        assert shaping.coarsen_ip(address) in control, "the coarsened form is not served either"
    finally:
        await editor.aclose()
        await viewer.aclose()


async def test_an_operator_name_cannot_reveal_an_address_shaping_hides(
    env: tuple[Engine, asyncio.Queue[QueueItem], Any],
) -> None:
    """The same hole in the field it already existed in, before this release added any (F104).

    `operator_name` was exempt from `FIELD_RULES` on the same reasoning as a label, and it leaked
    on both the list and the detail. Repairing only the fields v0.16.3 introduces would have left
    the one place the defect was already reachable.
    """
    _engine, _queue, app = env
    editor = await authutil.client_as(app, "editor")
    viewer = await authutil.client_as(app, "viewer")
    try:
        sid, alarm = await _one_alarm(editor)
        address = str(alarm["device_ip"])
        named = await editor.post(f"/api/situations/{sid}/name", json={"name": f"storm {address}"})
        assert named.status_code == 200, named.text
        for path in ("/api/situations", f"/api/situations/{sid}"):
            assert address not in (await viewer.get(path)).text, path
        assert address in (await editor.get(f"/api/situations/{sid}")).text
    finally:
        await editor.aclose()
        await viewer.aclose()


# --- what a declaration is not allowed to change ------------------------------------------------


def test_the_severity_gates_are_unchanged_by_this_release() -> None:
    """**Directive 5.** A declaration is a separate source, never a lowered threshold.

    The three thresholds are read from the module rather than restated, and the two gate functions
    are hashed, so loosening one to make declarations easier to reach is a visible diff here.
    Softening the standard of evidence because a new source of it arrived is exactly the failure
    principle 9 forbids.
    """
    import hashlib
    import inspect

    from netcorenoc.engine.correlate import severity as sev

    assert (sev.SEVERITY_MIN_OBS, sev.SEVERITY_MIN_CLOSED, sev.SEVERITY_MIN_PER_VALUE) == (
        200,
        50,
        5,
    )
    assert sev.SEVERITY_MAX_DISTINCT == 8
    digest = hashlib.sha256(
        (
            inspect.getsource(sev.severity_candidate) + inspect.getsource(sev.confirm_ordinality)
        ).encode()
    ).hexdigest()
    assert digest == "55da9f5fede252e17595156c2a9d9cb0ddaa796c278156e538d1c90e3e9a7969", (
        "the two gate functions changed. A declaration is a separate source of severity, not a "
        "reason to lower the evidence the appliance requires of itself."
    )


def test_the_console_and_the_bundled_vocabulary_agree_about_where_the_scale_ends() -> None:
    """`format.js`'s `VOCAB_MAX_RANK` against `known_oids.SEVERITY_VOCAB` itself.

    The console decides whether a rank came from a bundled token or from a vendor's own numbering
    (F99) by comparing against the top of the vocabulary. That is one constant in two languages,
    and this reads the JavaScript rather than restating the number — so the day a sixth token is
    bundled, the console's placement rule fails here instead of silently placing `indeterminate`
    on a scale it does not belong to.
    """
    import re

    source = (
        Path(__file__).resolve().parent.parent / "src" / "netcorenoc" / "ui" / "app" / "format.js"
    ).read_text(encoding="utf-8")
    found = re.search(r"const VOCAB_MAX_RANK = (\d+);", source)
    assert found is not None, "format.js no longer names the top of the vocabulary scale"
    assert int(found.group(1)) == max(known_oids.SEVERITY_VOCAB.values())


def test_the_gesture_to_assertion_map_is_unamended() -> None:
    """**Directive 6.** The preregistration is a claim made before the data was seen.

    Amending it in the release that adds a new operator action would make it a description of what
    was built rather than a commitment made in advance, which is the whole of its value. This
    asserts the file names no declaration at all.
    """
    doc = Path(__file__).resolve().parent.parent / "docs" / "analysis" / "PREREGISTRATION-0.16.0.md"
    text = doc.read_text(encoding="utf-8")
    for word in ("declare", "declaration", "kind='severity'", 'kind="severity"'):
        assert word not in text.lower().replace("declared", ""), (
            f"`{word}` appears in the registered gesture-to-assertion map. A declaration asserts "
            "nothing about a grouping, and the map is not amended by the release that adds one."
        )
