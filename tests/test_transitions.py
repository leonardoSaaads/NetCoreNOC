"""The situation lifecycle as a table, and every edge in it asserted (v0.22.0, item 9, ADR #382).

Measured before the repair: renaming a situation promoted it `new -> open`, so a label an operator
typed in passing was recorded as *"somebody has worked this"*; and an operator's split created its
new situation as `new`, so what an operator had just built was shown as *"nobody has looked"*.
Both came from promotion being a call each route made or forgot. `TRANSITIONS` makes it a table,
`promote_situation` refuses an act that has no `new -> open` edge, and this file walks the table.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from netcorenoc.store import Store
from netcorenoc.store.situations import ATTENTION, TRANSITIONS

TS = 1_700_000_000.0


def test_attention_is_derived_from_the_table_and_rename_is_not_in_it() -> None:
    # v0.26.0 (ADR #412): `autonomy` — the model accepting a grouping, when an admin has switched
    # the grouping grade on. Attention by the model, attributed in `autonomy_decision`.
    assert (
        frozenset({"promote", "feedback", "move", "merge", "split", "hand_clear", "autonomy"})
        == ATTENTION
    )
    assert TRANSITIONS["rename"] == {}, "a rename acquired a state edge"
    assert TRANSITIONS["operator_split"] == {None: "open"}
    assert TRANSITIONS["correlate"] == {None: "new"}
    assert TRANSITIONS["surface"] == {None: "new"}
    # v0.27.0 (ADR #428): the model's proposal to grow a confirmed situation, and its answers.
    assert TRANSITIONS["propose"] == {None: "pending"}
    assert TRANSITIONS["accept"] == {"pending": "resolved"}
    assert TRANSITIONS["reject"] == {"pending": "new"}
    assert TRANSITIONS["lapse"] == {"pending": "new"}
    assert all("open" not in edges.values() or None not in edges or edges[None] != "pending"
               for edges in TRANSITIONS.values())


@pytest.mark.parametrize("act", sorted(a for a, e in TRANSITIONS.items() if None in e))
async def test_every_creation_edge(store: Store, act: str) -> None:
    async with store.lock:
        sid = await store.create_situation(TS, act=act)
        cur = await store.conn.execute("SELECT status FROM situation WHERE id=?", (sid,))
        row = await cur.fetchone()
    # Pinned literally, not read back from the table: comparing with `TRANSITIONS` would move both
    # sides of the assertion when the table moved (the X2 injection found exactly that).
    expected = {
        "correlate": "new",
        "surface": "new",
        "operator_split": "open",
        "propose": "pending",
    }
    assert row is not None and row[0] == expected[act], (act, row)


@pytest.mark.parametrize("act", sorted(ATTENTION))
async def test_every_attention_edge_moves_new_to_open_and_leaves_open_alone(
    store: Store, act: str
) -> None:
    async with store.lock:
        fresh = await store.create_situation(TS, act="correlate")
        worked = await store.create_situation(TS, act="operator_split")
        await store.promote_situation(fresh, TS + 1, act)
        await store.promote_situation(worked, TS + 1, act)
        cur = await store.conn.execute(
            "SELECT id, status FROM situation WHERE id IN (?, ?)", (fresh, worked)
        )
        status = {int(r[0]): str(r[1]) for r in await cur.fetchall()}
    assert status == {fresh: "open", worked: "open"}, (act, status)


@pytest.mark.parametrize("act", sorted(ATTENTION))
async def test_attention_on_a_proposal_makes_it_an_open_situation_of_its_own(
    store: Store, act: str
) -> None:
    """v0.27.0 (ADR #428): an operator who works a pending bag on its own terms has judged it a
    situation; the proposal lapses with the promotion, and the target is untouched."""
    async with store.lock:
        target = await store.create_situation(TS, act="operator_split")
        proposal = await store.create_situation(TS, act="propose")
        await store.set_proposal(proposal, target, 0.9)
        await store.promote_situation(proposal, TS + 1, act)
        states = await store.lifecycle_states([target, proposal])
    assert states == {target: ("open", None), proposal: ("open", None)}, (act, states)


@pytest.mark.parametrize("act", ["rename", "close", "idle", "correlate", "no-such-act"])
async def test_an_act_without_an_attention_edge_cannot_promote(store: Store, act: str) -> None:
    async with store.lock:
        sid = await store.create_situation(TS, act="correlate")
        with pytest.raises(ValueError):
            await store.promote_situation(sid, TS + 1, act)
        cur = await store.conn.execute("SELECT status FROM situation WHERE id=?", (sid,))
        row = await cur.fetchone()
    assert row is not None and row[0] == "new"


def test_every_promotion_in_the_api_names_an_attention_act() -> None:
    """Structural: a route that promotes says which act it is, and the act has the edge."""
    import netcorenoc

    root = pathlib.Path(netcorenoc.__file__).resolve().parent / "api" / "routes"
    found: list[tuple[str, str]] = []
    for path in sorted(root.glob("*.py")):
        for act in re.findall(r"promote_situation\(.*\"([a-z_]+)\"\)", path.read_text("utf-8")):
            found.append((path.name, act))
    assert len(found) >= 7, found
    assert all(act in ATTENTION for _name, act in found), found
    names = {name for name, _act in found}
    source = (root / "annotate.py").read_text("utf-8")
    rename = source[source.index('"/api/situations/{sid}/name"') :]
    rename = rename[: rename.index("@route.")] if "@route." in rename else rename
    assert "promote_situation" not in rename, "the rename route promotes again"
    assert "annotate.py" in names
