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

import pytest

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
