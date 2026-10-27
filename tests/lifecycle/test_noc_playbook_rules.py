"""A playbook is data, validated whole, and can only read (ADR #463)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from netcorenoc.engine.noc import commands, playbook
from netcorenoc.engine.noc.playbook import PlaybookError

#: The owner's example: reach it, is it SAOS 10, is the alarm still active — and so on.
SAOS: dict[str, Any] = {
    "name": "Ciena SAOS 10 — LOS that may already have cleared",
    "match": {"vendors": ["ciena"], "trap_oids": ["1.3.6.1.4.1.1271"]},
    "timeout_s": 300,
    "start": "reach",
    "steps": {
        "reach": {
            "do": {"reach": {}},
            "next": [{"if": {"ok": True}, "go": "saos"}, {"go": "down"}],
        },
        "saos": {
            "do": {"snmp_get": {"oids": ["1.3.6.1.2.1.1.1.0"]}},
            "next": [{"if": {"contains": "SAOS 10"}, "go": "alarms"}, {"go": "noc"}],
        },
        "alarms": {
            "do": {"ssh": {"command": "show alarm list active-alarms type ALL"}},
            "next": [{"if": {"contains": "LOS"}, "go": "power"}, {"go": "clear"}],
        },
        "power": {
            "do": {"ssh": {"command": "show xcvr status port {entity}"}},
            "next": [
                {"if": {"number_after": "Rx Power", "below": -25}, "go": "dispatch"},
                {"go": "noc"},
            ],
        },
        "clear": {
            "end": "resolve",
            "cause": "cleared_before_isolation",
            "solution": "Not active on the device; the clear trap never arrived.",
            "clear_alarms": True,
        },
        "dispatch": {
            "end": "pending",
            "status": "pending_dispatch",
            "next_update_in_s": 3600,
            "update": {
                "title": "LOS with no light",
                "description": "LOS active, Rx below -25 dBm.",
                "executive_summary": "Fibre fault — dispatch",
                "current_analysis": "Rx power below threshold on the port.",
                "noc_next_step": "Follow the dispatch.",
                "customer_next_step": "Grant site access to the technician.",
            },
        },
        "down": {"end": "queue", "note": "The element does not answer."},
        "noc": {"end": "queue", "note": "Automated checks did not conclude."},
    },
}


def _with(path: list[str], value: Any) -> dict[str, Any]:
    doc = copy.deepcopy(SAOS)
    node: Any = doc
    for key in path[:-1]:
        node = node[int(key)] if isinstance(node, list) else node[key]
    node[path[-1]] = value
    return doc


def test_the_owners_example_is_a_valid_playbook() -> None:
    assert playbook.validate(copy.deepcopy(SAOS))["start"] == "reach"


@pytest.mark.parametrize(
    ("path", "value", "says"),
    [
        (["steps", "alarms", "do", "ssh", "command"], "configure terminal", "read-only"),
        (["steps", "alarms", "do", "ssh", "command"], "show alarm; reload", "one line"),
        (["steps", "alarms", "do", "ssh", "command"], "show {password}", "placeholders"),
        (["steps", "alarms", "next"], [{"go": "nowhere"}], "no step"),
        (["steps", "noc"], {"do": {"reach": {}}, "next": [{"go": "reach"}]}, "cycle"),
        (["steps", "clear", "cause"], "gremlins", "cause"),
        (["steps", "dispatch", "status"], "resolved", "status"),
        (["timeout_s"], 99999, "timeout_s"),
        (["match"], {}, "at least one"),
        (["steps", "reach", "do"], {"exec": {"cmd": "rm -rf /"}}, "unknown key"),
        (["steps", "reach", "next"], [{"if": {"ok": True}, "go": "saos"}], "default"),
    ],
)
def test_what_a_playbook_may_not_say(path: list[str], value: Any, says: str) -> None:
    with pytest.raises(PlaybookError, match=says):
        playbook.validate(_with(path, value))


def test_an_unreachable_step_is_refused() -> None:
    doc = copy.deepcopy(SAOS)
    doc["steps"]["orphan"] = {"end": "queue"}
    with pytest.raises(PlaybookError, match="not reachable"):
        playbook.validate(doc)


def test_matching() -> None:
    facts = {
        "vendors": ["ciena"],
        "trap_oids": ["1.3.6.1.4.1.1271.3.4.14.0.1"],
        "severity": "major",
    }
    assert playbook.matches(SAOS, facts)
    assert not playbook.matches(SAOS, {**facts, "vendors": ["huawei"]})
    assert not playbook.matches(SAOS, {**facts, "trap_oids": ["1.3.6.1.4.1.12712.1"]}), (
        "a prefix is an arc"
    )
    assert not playbook.matches(_with(["match", "severities"], ["critical"]), facts)


def test_the_four_tests_and_the_substitution() -> None:
    answer = "Port 3/4\n  Rx Power   -31.20 dBm\n  Tx Power -2.1 dBm"
    assert playbook.test(
        {"number_after": "Rx Power", "below": -25}, ok=True, output=answer, vendor=None
    )
    assert not playbook.test(
        {"number_after": "rx power", "above": -25}, ok=True, output=answer, vendor=None
    )
    assert playbook.test({"contains": "rx power", "ok": True}, ok=True, output=answer, vendor="x")
    assert not playbook.test({"ok": True}, ok=False, output="", vendor=None)
    assert playbook.test({"vendor": "Ciena"}, ok=True, output="", vendor="ciena")
    assert playbook.substitute("show xcvr status port {entity}", {"entity": "3/4"}) == (
        "show xcvr status port 3/4"
    )
    with pytest.raises(commands.CommandError):
        playbook.substitute("show port {entity}", {"entity": "3/4; reload"})
    with pytest.raises(commands.CommandError):
        playbook.substitute("show port {entity}", {"entity": None})


def test_a_phrase_names_the_alarm_s_entity_and_never_matches_its_placeholder_literally() -> None:
    """`not_contains: "{entity}"` once compared the braces themselves — always absent, so a tree
    asking "is this port's alarm gone?" answered yes while the device still reported it."""
    active = "Port 3/4 LOS critical\nPort 1/1 OK"
    gone = {"not_contains": "{entity}"}
    slots = {"ip": "10.0.0.1", "entity": "3/4"}
    assert not playbook.test(gone, ok=True, output=active, vendor=None, slots=slots)
    assert playbook.test(gone, ok=True, output="No active alarms", vendor=None, slots=slots)
    power = "Port 1/1 Rx Power -2.0 dBm\nPort 3/4 Rx Power -31.2 dBm"
    low = {"number_after": "port {entity} rx power", "below": -25}
    assert playbook.test(low, ok=True, output=power, vendor=None, slots=slots)
    with pytest.raises(commands.CommandError, match="no plain value"):
        playbook.test(gone, ok=True, output=active, vendor=None, slots={"entity": None})
    with pytest.raises(PlaybookError, match="only placeholders"):
        playbook.validate(_with(["steps", "alarms", "next", "0", "if"], {"contains": "{port}"}))


def test_a_leaf_s_texts_are_filled_and_bounded() -> None:
    leaf = {
        "end": "pending",
        "status": "pending_fiber_provider",
        "update": {"title": "Loss of light on {entity}"},
        "note": "Seen on {ip}.",
    }
    filled = playbook.fill(leaf, {"ip": "10.0.0.1", "entity": "3/4"})
    assert (
        filled["update"]["title"] == "Loss of light on 3/4"
        and filled["note"] == "Seen on 10.0.0.1."
    )
    with pytest.raises(commands.CommandError):
        playbook.fill(leaf, {"ip": "10.0.0.1", "entity": None})
    with pytest.raises(PlaybookError, match="only placeholders"):
        playbook.validate(_with(["steps", "clear", "solution"], "Cleared {port}."))
