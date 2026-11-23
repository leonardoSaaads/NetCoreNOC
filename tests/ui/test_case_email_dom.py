"""ADR #505 — the email editor reads a list the way Chromium writes it.

In the lab, a bulleted list typed in the feed's editor reached the customer as one paragraph, its
items run together ("racks 3 and 4Duration: about 1 hour"): Chromium makes the list INSIDE the
line's `<div>`, and the reader flattened every `<div>` it met. A block container is now walked.
"""

from __future__ import annotations

from netcorenoc.store import Store

import domdriver
import uifixtures
from domdriver import dom_test


@dom_test
async def test_a_list_made_inside_a_line_stays_a_list(store: Store) -> None:
    routes = await uifixtures.all_routes(store)
    # What Chromium's contenteditable holds after: a line, Enter, the list button, two items.
    tree = [
        "div",
        "Which date suits you?",
        ["div"],
        ["div", ["ul", ["li", "Site: Natal edge"], ["li", "Duration: about 1 hour"]]],
        ["div", "Thanks, ", ["b", "the NOC"]],
    ]
    out = domdriver.run_scenario("editorDoc", {"routes": routes["editor"], "tree": tree})
    assert out["doc"] == [
        {"p": [{"t": "Which date suits you?"}]},
        {"ul": [[{"t": "Site: Natal edge"}], [{"t": "Duration: about 1 hour"}]]},
        {"p": [{"t": "Thanks, "}, {"t": "the NOC", "b": True}]},
    ], out["doc"]
