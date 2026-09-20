"""The replay tool's transport, tested against the corpus it actually ships with (F128).

`tools/trap_replay.py` is the command the README's quickstart prints and `make replay` runs. Until
v0.18.0 it bound each simulated source under `contextlib.suppress(OSError)`, so a source the host
cannot bind became the default local address with nothing said — and **eight of the corpus's
twenty-five source addresses are TEST-NET-3**, which no host has an interface in.

The consequence was not subtle and it was invisible: `make replay SCENARIO=dual_incident`, whose
scenario exists to show two incidents on *disjoint* network elements staying apart, delivered its
four devices as one on every machine, every time.

**Why no test caught it.** The one test that drives that scenario over a real socket,
`tests/test_operation.py`, carried its own address rewrite and bound its own sockets — precisely
so *it* would not hit the fallback. It had worked around the defect, so it could not see it. This
module tests the tool's own rule against the shipped corpus, which is the thing nobody was doing.
"""

from __future__ import annotations

import json
import pathlib

import pytest

import trap_replay

CORPUS = pathlib.Path(__file__).resolve().parent.parent / "eval" / "corpus"


def _sources(path: pathlib.Path) -> list[str]:
    return sorted(
        {str(e["source"]) for e in json.loads(path.read_text(encoding="utf-8"))["events"]}
    )


def _scenarios() -> list[pathlib.Path]:
    paths = sorted(CORPUS.glob("*.json"))
    assert paths, "the corpus is empty, so every test below is asserting nothing"
    return paths


@pytest.mark.parametrize("path", _scenarios(), ids=lambda p: p.stem)
def test_every_corpus_scenario_keeps_all_its_devices_distinct(path: pathlib.Path) -> None:
    """**The defect, stated as the property it violated.**

    For every scenario this repository ships: the number of source addresses that will go on the
    wire equals the number the scenario describes. Derived from the corpus directory rather than
    listed here, so a scenario added tomorrow is covered by the guard today — Appendix B's
    standing complaint about guards that enumerate what they check.
    """
    declared = _sources(path)
    mapping = trap_replay.SourceMap.for_sources(declared)
    wire = {mapping.wire(s) for s in declared}
    assert len(wire) == len(declared), (
        f"{path.name}: {len(declared)} devices collapse onto {len(wire)} source address(es)"
    )
    assert all(trap_replay.bindable(address) for address in wire), (
        f"{path.name}: an address the replay will send from is not bindable on this host"
    )


def test_the_unbindable_half_of_the_corpus_is_the_ordinary_case_not_an_edge_case() -> None:
    """The measurement that makes F128 a defect rather than a nicety.

    If every corpus address happened to be bindable, the suppressed fallback would be unreachable
    and this whole module would be theatre. It is not: this asserts that at least one shipped
    scenario needs the rewrite, so the path under test is a path the product takes.
    """
    needing = [
        p.name for p in _scenarios() if trap_replay.SourceMap.for_sources(_sources(p)).rewritten()
    ]
    assert needing, (
        "no shipped scenario needs the address rewrite any more; if the corpus was re-addressed "
        "into a bindable range, delete this module and the rewrite with it"
    )


def test_a_source_that_cannot_be_bound_raises_instead_of_falling_back() -> None:
    """The fallback itself, at the one place it lived.

    `Sender.socket_for` is the method that suppressed `OSError`. A test that only checked
    `SourceMap` would leave the original defect reachable by any caller that skips the map —
    which is what every caller did.
    """
    sender = trap_replay.Sender(("127.0.0.1", 1))
    try:
        with pytest.raises(trap_replay.SourceBindError, match="cannot bind source address"):
            sender.socket_for("203.0.113.1")
        assert sender.sources_used() == 0, "a refused bind must not leave a socket behind"
    finally:
        sender.close()


def test_the_rewrite_refuses_rather_than_collapsing_two_devices_onto_one_address() -> None:
    """Injectivity is checked, not argued.

    `10.0.0.1` and `192.0.0.1` both rewrite to `127.0.0.1`. That is the exact failure the tool
    exists to prevent, so producing it must be a refusal and not a quieter version of F128.
    """
    with pytest.raises(trap_replay.SourceBindError, match="collapse distinct devices"):
        trap_replay.SourceMap.for_sources(["10.0.0.1", "192.0.0.1"])


def test_no_remap_refuses_instead_of_rewriting() -> None:
    """The opt-out an operator with real interfaces wants, and it is still never silent."""
    with pytest.raises(trap_replay.SourceBindError, match="--no-remap"):
        trap_replay.SourceMap.for_sources(["203.0.113.1"], remap=False)


def test_a_bindable_source_is_left_exactly_as_the_scenario_wrote_it() -> None:
    """The rewrite is a fallback, not a policy: an address that works is not touched."""
    mapping = trap_replay.SourceMap.for_sources(["127.0.0.2", "127.0.0.3"])
    assert mapping.rewritten() == {}
    assert mapping.wire("127.0.0.2") == "127.0.0.2"


def test_the_operator_is_told_which_addresses_moved() -> None:
    """A rewrite nobody is told about is the defect with a different mechanism."""
    described = "\n".join(trap_replay.SourceMap.for_sources(["203.0.113.7"]).describe())
    assert "203.0.113.7" in described and "127.0.113.7" in described
