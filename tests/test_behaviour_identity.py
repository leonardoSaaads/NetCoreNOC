"""The behaviour-identity gate (v0.15.1, DECISIONS #211).

`behaviour_identity.py` records what the appliance answers, route by route, principal by
principal. This drives it and compares the result with the committed record — and, because a
recorder nobody has watched fail is not a gate, it also proves the record moves when a response
does and that the canonicalisation is not wide enough to hide one.

Why this exists at all: v0.15.1 is a release of pure moves, so *"the tests pass"* is a weaker claim
than *"the HTTP surface is unchanged"* — the assertions were written against the same code that
produces the shape, and a package reorganisation is exactly the change that could alter a response
without failing one of them.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

import pytest

from netcorenoc.store import Store

import authutil
import behaviour_identity as bi

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def recorded(tmp_path_factory: pytest.TempPathFactory) -> str:
    """One run of the whole surface. Module-scoped: it seeds four databases and drives 356
    requests, which is worth doing once."""
    record, _ = bi.build(tmp_path_factory.mktemp("behaviour-identity"))
    return record


def test_the_record_is_not_empty_or_trivial(recorded: str) -> None:
    """Guard the guard. A harness that recorded nothing would satisfy every assertion below."""
    rows = [line for line in recorded.splitlines() if not line.startswith("#")]
    assert len(rows) >= 300, f"the record has {len(rows)} rows; the app registers 90 routes"
    for role in bi.ROLES:
        assert sum(1 for row in rows if row.startswith(role)) >= 80, f"{role} drove almost nothing"


def test_every_role_sees_a_different_surface(recorded: str) -> None:
    """The reason four principals are driven rather than one.

    A first version of this harness re-used one client across `POST /api/logout`, which sits second
    in registration order — so every route after it was driven anonymously and all four passes
    produced **identical** records. That bug is invisible unless something asserts the passes
    differ, which is what this is.
    """
    by_role = {
        role: [
            row.split(maxsplit=1)[1]
            for row in recorded.splitlines()
            if row.startswith(role) and not row.startswith("#")
        ]
        for role in bi.ROLES
    }
    seen = {
        role: hashlib.sha256("\n".join(rows).encode()).hexdigest() for role, rows in by_role.items()
    }
    assert len(set(seen.values())) == len(bi.ROLES), (
        f"two principals produced the same record: {seen}. Either the appliance does not "
        "distinguish them, or the harness lost the session it was holding."
    )
    statuses = {
        role: sorted({row.split()[2] for row in rows if row.split()[2].isdigit()})
        for role, rows in by_role.items()
    }
    assert "401" in statuses["anonymous"], "an anonymous principal must be refused somewhere"
    assert "403" in statuses["viewer"], "a viewer must be refused somewhere"
    assert "200" in statuses["admin"] and "403" not in statuses["admin"], (
        f"the admin pass is not authenticated: {statuses['admin']}"
    )


def test_the_record_matches_the_committed_one(recorded: str) -> None:
    """**The gate.** Byte for byte against `fixtures/behaviour-identity.txt`.

    A red here in a release of moves means a response changed, and the move is what changed it.
    Regenerate with `python tests/behaviour_identity.py --write` **only** when the change is
    intended, which makes it a reviewable line in a diff rather than something that happened.
    """
    expected = bi.RECORD.read_text(encoding="utf-8")
    if recorded != expected:
        got = {line for line in recorded.splitlines() if not line.startswith("#")}
        want = {line for line in expected.splitlines() if not line.startswith("#")}
        differing = sorted(got ^ want)[:12]
        pytest.fail(
            "the HTTP surface differs from the committed record:\n  " + "\n  ".join(differing)
        )


def test_two_runs_in_separate_processes_are_identical(recorded: str) -> None:
    """The completeness check on the substitution list, and it has to cross a process boundary.

    Two builds inside one interpreter share `PYTHONHASHSEED`, so a response whose order came from
    iterating a `set` of strings would agree with itself and the list would look complete when it
    was not. A second interpreter is what makes that visible — and it is what proves the list is
    long enough, without which "the record is stable" would only mean "nothing else varied today".
    """
    # Fixed argv, shell=False, and the interpreter running this suite — never a shell lookup.
    import subprocess

    result = subprocess.run(  # nosec B603
        [sys.executable, str(REPO_ROOT / "tests" / "behaviour_identity.py")],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    assert result.stdout == recorded, (
        "the record is not reproducible across processes, so the substitution list is incomplete "
        "— something in a response varies for a reason this harness has not named."
    )


def test_a_changed_response_produces_a_diff(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The demonstration.** A harness nobody has watched fail is not a harness.

    One extra key, added where `GET /api/stats` gets its numbers, must move the record. **The
    control is the first build in this test**, not the module-scoped one: both run over the same
    single principal, so the only difference between them is the injected field, and a diff that
    appeared for any other reason would show up as a control that already differs from itself.

    An over-broad canonicaliser — one pattern over anything that looks numeric — is the way this
    harness could fail silently, and it would fail here first: it would erase the injected key
    along with the noise and leave the two builds equal.
    """
    from netcorenoc.store import Store

    monkeypatch.setattr(bi, "ROLES", ("admin",))
    control, _ = bi.build(tmp_path_factory.mktemp("control"))
    again, _ = bi.build(tmp_path_factory.mktemp("control-again"))
    assert control == again, "the control is not stable, so a diff below would prove nothing"

    original = Store.stats

    async def stats_with_an_extra_field(self: Store) -> dict[str, object]:
        return {**await original(self), "injected_by_the_demonstration": 1}

    monkeypatch.setattr(Store, "stats", stats_with_an_extra_field)
    injected, _ = bi.build(tmp_path_factory.mktemp("injected"))
    assert injected != control, (
        "one extra key in one response left the record unchanged. The canonicalisation is wide "
        "enough to erase a real difference, which is the failure this harness exists to prevent."
    )


# --- what the record does not see, kept honest by derivation (v0.17.0, DECISIONS #325) ----------


def _routes(app: object) -> list[Any]:
    """`app.routes`, read through one typed accessor. `make_env` returns the app as `object`, and
    three call sites reaching into it would each need their own `type: ignore`."""
    return list(getattr(app, "routes", []))


async def test_the_undriven_query_parameters_are_exactly_those_declared(store: Store) -> None:
    """**The blind spot, turned from a fact nobody wrote down into a declaration that is checked.**

    `Recorder._request` builds every URL from the route's path template and sends **no query
    string**, so every route with query parameters is recorded at its defaults only. Measured at
    v0.16.7: nine parameters across four routes, `q` — the v0.16.1 server-side situation search —
    among them. A regression in search or in the timeline window leaves the record byte-identical.

    The live set is **derived** from `route.dependant.query_params` rather than transcribed, so a
    parameter added to a route fails here until someone either drives it in the harness or adds it
    to `UNDRIVEN_QUERY_PARAMS` with a reason. A hand-written list is the guard this repository has
    shipped six times and repaired six times (F92, F98, F112, F113, F114).

    Deleting an entry cannot make this pass: the comparison is equality in both directions.
    """
    _engine, _queue, app = await authutil.make_env(store)
    live: dict[tuple[str, str], tuple[str, ...]] = {}
    for route in _routes(app):
        dependant = getattr(route, "dependant", None)
        path = getattr(route, "path", None)
        if dependant is None or path is None:
            continue
        names = tuple(sorted(param.name for param in getattr(dependant, "query_params", [])))
        if not names:
            continue
        for method in sorted(getattr(route, "methods", set()) or set()):
            if method not in ("HEAD", "OPTIONS"):
                live[(method, path)] = names

    declared = bi.UNDRIVEN_QUERY_PARAMS
    assert live == declared, (
        "the set of query parameters this record does not drive has changed.\n"
        f"  live, undeclared: {sorted(set(live) - set(declared))}\n"
        f"  declared, gone:   {sorted(set(declared) - set(live))}\n"
        f"  differing names:  "
        f"{sorted(k for k in set(live) & set(declared) if live[k] != declared[k])}\n\n"
        "Either drive the parameter in behaviour_identity.py, or declare it undriven with the "
        "reason. What it may not be is undocumented: an undriven parameter is a regression this "
        "record cannot see, and NOT_DRIVEN at least writes a line for the route it skips."
    )


async def test_the_query_parameter_derivation_actually_finds_parameters(store: Store) -> None:
    """Guard the guard: a derivation that returned nothing would make the table above vacuous
    while looking like a clean bill of health — F92's shape exactly."""
    _engine, _queue, app = await authutil.make_env(store)
    found = [
        param.name
        for route in _routes(app)
        for param in getattr(getattr(route, "dependant", None), "query_params", [])
    ]
    assert len(found) >= 5, f"the derivation found {len(found)} query parameters; it is broken"
    assert "q" in found, "the server-side situation search must be among what was found"


def test_the_record_does_not_drive_a_single_query_string() -> None:
    """The premise the declaration rests on, asserted against the committed record itself.

    If the harness ever starts driving query strings, this goes red and the table above becomes a
    claim about nothing — which is how a blind-spot declaration outlives the blind spot.
    """
    record = bi.RECORD.read_text(encoding="utf-8")
    driven = [line for line in record.splitlines() if "?" in line and not line.startswith("#")]
    assert not driven, (
        f"the record now drives query strings; the declaration is stale: {driven[:3]}"
    )
