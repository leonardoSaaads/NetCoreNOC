"""Playbooks: troubleshooting trees an agent writes once, so the NOC is not called for the next one
(ADR #463).

After a case is resolved, the agent that worked it can write down what it did as a tree: check
this, then — depending on the answer — check that, until a leaf says how it ends. The next case
that matches runs the tree first, before anyone is called; only when the tree does not conclude
within its time does the case reach the NOC queue. The owner's example, as a playbook:

    reach the device            — answered? → is it Ciena SAOS 10 (SNMP sysDescr)?
      SAOS 10?                  → `show alarm list active-alarms type ALL` over SSH
        alarm still active?     → read the port's Rx power …
        not active?             → resolve, and clear the traps (the clear trap never arrived)

**A playbook is data, never code** — the same rule as the model file (#405). It is a JSON
document validated field by field when it is stored: known keys only, bounded sizes, every
branch reaching a leaf, no cycle, and **every command read-only** (`commands.check`). Nothing in
it is evaluated: conditions are four fixed tests (did the step succeed; does the output contain a
phrase; is the number after a label above or below a value; is the element of a vendor), and the
only values substituted into a command, a test's phrase or a leaf's text are the element's address
and the root alarm's entity, each refused unless it is plain (`[A-Za-z0-9./:_-]`) — a test that
names one the situation lacks cannot decide, and the case goes to the NOC. A device's own text
decides branches; it can never become a command.

Shape (`validate` is the authority; `docs/console.md` shows it whole):

    {"name": "...", "description": "...",
     "match": {"vendors": ["ciena"], "trap_oids": ["1.3.6.1.4.1.1271"], "severities": ["critical"]},
     "timeout_s": 300, "start": "reach",
     "steps": {
        "reach": {"do": {"reach": {}},
                  "next": [{"if": {"ok": true}, "go": "saos"}, {"go": "down"}]},
        "saos":  {"do": {"snmp_get": {"oids": ["1.3.6.1.2.1.1.1.0"]}},
                  "next": [{"if": {"contains": "SAOS 10"}, "go": "alarms"}, {"go": "noc"}]},
        "alarms": {"do": {"ssh": {"command": "show alarm list active-alarms type ALL"}},
                   "next": [{"if": {"contains": "LOS"}, "go": "noc"}, {"go": "clear"}]},
        "clear": {"end": "resolve", "cause": "cleared_before_isolation", "clear_alarms": true,
                  "solution": "Not active on the device; the clear trap never arrived."},
        "down":  {"end": "pending", "status": "pending_connectivity", "next_update_in_s": 1800,
                  "update": {...the customer update...}},
        "noc":   {"end": "queue", "note": "Automated checks did not conclude."}}}

Pure.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from netcorenoc.engine.noc import cases, commands

__all__ = [
    "ACTIONS",
    "ENDS",
    "MAX_DOCUMENT",
    "MAX_STEPS",
    "PlaybookError",
    "canonical",
    "fill",
    "matches",
    "substitute",
    "test",
    "validate",
]

MAX_DOCUMENT = 64 * 1024
MAX_STEPS = 32
TIMEOUT_RANGE = (30, 1800)
ACTIONS = ("reach", "ssh", "snmp_get")
ENDS = ("resolve", "pending", "queue")
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_PLAIN = re.compile(r"^[A-Za-z0-9./:_-]{1,64}$")
_SLOTS = ("ip", "entity")
_CONDITIONS = frozenset(
    {"ok", "contains", "not_contains", "number_after", "above", "below", "vendor"}
)
_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")


class PlaybookError(ValueError):
    """A playbook this appliance will not store or run, with the path to what is wrong."""


def _keys(
    where: str, value: Any, allowed: set[str], required: frozenset[str] | set[str] = frozenset()
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PlaybookError(f"{where}: an object")
    extra = sorted(set(value) - allowed)
    if extra:
        raise PlaybookError(f"{where}: unknown key {extra[0]!r}")
    missing = sorted(required - set(value))
    if missing:
        raise PlaybookError(f"{where}: {missing[0]!r} is required")
    return value


def _strings(where: str, value: Any, *, most: int, longest: int) -> list[str]:
    if not isinstance(value, list) or len(value) > most:
        raise PlaybookError(f"{where}: a list of at most {most}")
    if not all(isinstance(v, str) and 0 < len(v) <= longest for v in value):
        raise PlaybookError(f"{where}: each a non-empty text of at most {longest} characters")
    return value


def _slots(where: str, text: str) -> str:
    """``text`` with each placeholder replaced by a plain stand-in; refused if it names another."""
    probe = text
    for slot in _SLOTS:
        probe = probe.replace("{" + slot + "}", "x")
    if "{" in probe or "}" in probe:
        raise PlaybookError(f"{where}: the only placeholders are {{ip}} and {{entity}}")
    return probe


def _condition(where: str, cond: Any) -> None:
    cond = _keys(where, cond, set(_CONDITIONS))
    if "ok" in cond and not isinstance(cond["ok"], bool):
        raise PlaybookError(f"{where}.ok: true or false")
    for key in ("contains", "not_contains", "number_after", "vendor"):
        if key in cond and not (isinstance(cond[key], str) and 0 < len(cond[key]) <= 200):
            raise PlaybookError(f"{where}.{key}: a text of at most 200 characters")
    for key in ("contains", "not_contains", "number_after"):
        if key in cond:
            _slots(f"{where}.{key}", cond[key])
    for key in ("above", "below"):
        if key in cond and not isinstance(cond[key], int | float):
            raise PlaybookError(f"{where}.{key}: a number")
    if ("above" in cond or "below" in cond) != ("number_after" in cond):
        raise PlaybookError(f"{where}: number_after goes with above or below")


def _command(where: str, command: Any) -> None:
    if not isinstance(command, str):
        raise PlaybookError(f"{where}: a text")
    try:
        commands.check(_slots(where, command), "root")
    except commands.CommandError as exc:
        raise PlaybookError(f"{where}: {exc}") from exc


def _step(name: str, step: Any, names: set[str]) -> list[str]:
    where = f"steps.{name}"
    if not isinstance(step, dict):
        raise PlaybookError(f"{where}: an object")
    if "end" in step:
        return _end(where, step)
    step = _keys(where, step, {"do", "next", "note"}, {"next"})
    if "do" in step:
        do = _keys(f"{where}.do", step["do"], set(ACTIONS))
        if len(do) != 1:
            raise PlaybookError(f"{where}.do: exactly one of {', '.join(ACTIONS)}")
        action, args = next(iter(do.items()))
        if action == "reach":
            _keys(f"{where}.do.reach", args, set())
        elif action == "ssh":
            args = _keys(f"{where}.do.ssh", args, {"command", "mode"}, {"command"})
            _command(f"{where}.do.ssh.command", args["command"])
            if args.get("mode", "exec") not in ("exec", "shell"):
                raise PlaybookError(f"{where}.do.ssh.mode: exec or shell")
        else:
            args = _keys(f"{where}.do.snmp_get", args, {"oids"}, {"oids"})
            oids = _strings(f"{where}.do.snmp_get.oids", args["oids"], most=20, longest=200)
            if not oids or not all(re.fullmatch(r"\.?\d+(\.\d+){1,127}", o) for o in oids):
                raise PlaybookError(f"{where}.do.snmp_get.oids: numeric OIDs")
    branches = step["next"]
    if not isinstance(branches, list) or not 1 <= len(branches) <= 8:
        raise PlaybookError(f"{where}.next: one to eight branches")
    targets = []
    for i, branch in enumerate(branches):
        branch = _keys(f"{where}.next[{i}]", branch, {"if", "go"}, {"go"})
        if "if" in branch:
            _condition(f"{where}.next[{i}].if", branch["if"])
        elif i != len(branches) - 1:
            raise PlaybookError(f"{where}.next[{i}]: only the last branch may have no `if`")
        if branch["go"] not in names:
            raise PlaybookError(f"{where}.next[{i}].go: no step {branch['go']!r}")
        targets.append(branch["go"])
    if "if" in branches[-1]:
        raise PlaybookError(f"{where}.next: the last branch is the default, with no `if`")
    return targets


def _end(where: str, step: dict[str, Any]) -> list[str]:
    kind = step["end"]
    if kind == "resolve":
        _keys(
            where, step, {"end", "cause", "solution", "clear_alarms", "note"}, {"cause", "solution"}
        )
        if step["cause"] not in cases.CAUSES:
            raise PlaybookError(f"{where}.cause: one of {', '.join(cases.CAUSES)}")
        if not isinstance(step.get("clear_alarms", False), bool):
            raise PlaybookError(f"{where}.clear_alarms: true or false")
    elif kind == "pending":
        _keys(
            where,
            step,
            {"end", "status", "next_update_in_s", "update", "note"},
            {"status", "update"},
        )
        if step["status"] not in cases.PENDING:
            raise PlaybookError(f"{where}.status: one of {', '.join(sorted(cases.PENDING))}")
        wait = step.get("next_update_in_s", 3600)
        if not isinstance(wait, int) or not 300 <= wait <= 7 * 86400:
            raise PlaybookError(f"{where}.next_update_in_s: 300 to 604800")
        update = _keys(f"{where}.update", step["update"], set(cases.TEXT_FIELDS) - {"solution"})
        for name, text in update.items():
            if not isinstance(text, str) or len(text) > cases.FIELD_LIMITS[name][1]:
                raise PlaybookError(
                    f"{where}.update.{name}: a text of at most {cases.FIELD_LIMITS[name][1]}"
                )
            _slots(f"{where}.update.{name}", text)
    elif kind == "queue":
        _keys(where, step, {"end", "note"})
    else:
        raise PlaybookError(f"{where}.end: one of {', '.join(ENDS)}")
    for key in ("note", "solution"):
        if key in step and not (isinstance(step[key], str) and len(step[key]) <= 2000):
            raise PlaybookError(f"{where}.{key}: a text of at most 2000 characters")
        if key in step:
            _slots(f"{where}.{key}", step[key])
    return []


def validate(document: Any) -> dict[str, Any]:
    """The playbook, checked whole; a :class:`PlaybookError` naming the first thing wrong."""
    doc = _keys(
        "playbook",
        document,
        {"name", "description", "match", "timeout_s", "start", "steps"},
        {"name", "match", "start", "steps"},
    )
    if not (isinstance(doc["name"], str) and 0 < len(doc["name"].strip()) <= 80):
        raise PlaybookError("name: 1 to 80 characters")
    if not isinstance(doc.get("description", ""), str) or len(doc.get("description", "")) > 2000:
        raise PlaybookError("description: at most 2000 characters")
    match = _keys("match", doc["match"], {"vendors", "trap_oids", "severities"})
    if not match:
        raise PlaybookError("match: at least one of vendors, trap_oids, severities")
    _strings("match.vendors", match.get("vendors", []), most=20, longest=64)
    oids = _strings("match.trap_oids", match.get("trap_oids", []), most=50, longest=200)
    if not all(re.fullmatch(r"\.?\d+(\.\d+)*", o) for o in oids):
        raise PlaybookError("match.trap_oids: numeric OIDs (a prefix matches its subtree)")
    severities = _strings("match.severities", match.get("severities", []), most=5, longest=16)
    if set(severities) - {"critical", "major", "minor", "warning", "indeterminate"}:
        raise PlaybookError("match.severities: critical, major, minor, warning, indeterminate")
    timeout = doc.get("timeout_s", 300)
    if not isinstance(timeout, int) or not TIMEOUT_RANGE[0] <= timeout <= TIMEOUT_RANGE[1]:
        raise PlaybookError(f"timeout_s: {TIMEOUT_RANGE[0]} to {TIMEOUT_RANGE[1]} seconds")
    steps = doc["steps"]
    if not isinstance(steps, dict) or not 1 <= len(steps) <= MAX_STEPS:
        raise PlaybookError(f"steps: one to {MAX_STEPS}")
    names = set(steps)
    bad = sorted(n for n in names if not _NAME.match(n))
    if bad:
        raise PlaybookError(f"steps: {bad[0]!r} — lower-case letters, digits and _")
    if doc["start"] not in names:
        raise PlaybookError(f"start: no step {doc['start']!r}")
    graph = {name: _step(name, step, names) for name, step in steps.items()}
    _acyclic(graph, doc["start"])
    if len(canonical(doc).encode()) > MAX_DOCUMENT:
        raise PlaybookError("playbook: at most 64 KiB")
    return doc


def _acyclic(graph: Mapping[str, Sequence[str]], start: str) -> None:
    state: dict[str, int] = {}

    def visit(node: str, depth: int) -> None:
        if state.get(node) == 1:
            raise PlaybookError(f"steps.{node}: a cycle — every path must reach an end")
        if state.get(node) == 2:
            return
        state[node] = 1
        for target in graph[node]:
            visit(target, depth + 1)
        state[node] = 2

    visit(start, 0)
    unreachable = sorted(set(graph) - set(state))
    if unreachable:
        raise PlaybookError(f"steps.{unreachable[0]}: not reachable from start")


def canonical(document: Mapping[str, Any]) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def matches(document: Mapping[str, Any], facts: Mapping[str, Any]) -> bool:
    """Does a situation, described by ``facts`` (vendors, trap_oids, severity), match?"""
    match = document["match"]
    vendors = {v.lower() for v in match.get("vendors", [])}
    if vendors and not vendors & {str(v).lower() for v in facts.get("vendors", ())}:
        return False
    prefixes = [o.lstrip(".") for o in match.get("trap_oids", [])]
    oids = [str(o).lstrip(".") for o in facts.get("trap_oids", ())]
    if prefixes and not any(o == p or o.startswith(p + ".") for o in oids for p in prefixes):
        return False
    severities = set(match.get("severities", []))
    return not (severities and facts.get("severity") not in severities)


def substitute(command: str, slots: Mapping[str, str | None]) -> str:
    """A command or a test's phrase with {ip} and {entity} filled — each refused unless plain."""
    out = command
    for slot in _SLOTS:
        token = "{" + slot + "}"
        if token not in out:
            continue
        value = slots.get(slot)
        if not value or not _PLAIN.match(value):
            raise commands.CommandError(f"{{{slot}}}: the situation has no plain value for it")
        out = out.replace(token, value)
    return out


def fill(leaf: Mapping[str, Any], slots: Mapping[str, str | None]) -> dict[str, Any]:
    """A leaf with {ip} and {entity} filled in its texts — the customer update, the solution, the
    note. A :class:`commands.CommandError` when one cannot be, or a text outgrows its field."""
    out = dict(leaf)
    for key in ("solution", "note"):
        if key in out:
            out[key] = substitute(out[key], slots)
    if "update" in out:
        out["update"] = {k: substitute(v, slots) for k, v in out["update"].items()}
        for name, text in out["update"].items():
            if len(text) > cases.FIELD_LIMITS[name][1]:
                raise commands.CommandError(f"update.{name}: longer than its field once filled")
    if len(out.get("solution", "")) > cases.FIELD_LIMITS["solution"][1]:
        raise commands.CommandError("solution: longer than its field once filled")
    return out


def test(
    cond: Mapping[str, Any],
    *,
    ok: bool,
    output: str,
    vendor: str | None,
    slots: Mapping[str, str | None] | None = None,
) -> bool:
    """One `if`: every key in it must hold. A phrase naming a slot ``slots`` cannot fill plainly
    raises :class:`commands.CommandError`: a test that cannot be read is not a decision."""
    text = output.lower()

    def phrase(key: str) -> str:
        return substitute(cond[key], slots or {}).lower()

    if "ok" in cond and cond["ok"] != ok:
        return False
    if "contains" in cond and phrase("contains") not in text:
        return False
    if "not_contains" in cond and phrase("not_contains") in text:
        return False
    if "vendor" in cond and (vendor or "").lower() != cond["vendor"].lower():
        return False
    if "number_after" in cond:
        label = phrase("number_after")
        at = text.find(label)
        if at < 0:
            return False
        found = _NUMBER.search(text, at + len(label))
        if found is None:
            return False
        number = float(found.group(0))
        if "above" in cond and not number > cond["above"]:
            return False
        if "below" in cond and not number < cond["below"]:
            return False
    return True
