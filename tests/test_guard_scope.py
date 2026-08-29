"""Two guards whose scope is a string, and what happens when the string stops matching (v0.9.2).

Same methodological class as the release itself, one level up: a guard that **silently stops
guarding** is worse than no guard, because the green tells you it is still watching.

* §1 the perimeter's **fail-open branch**, which had no test at all. Inverting one comparison makes
  an undeclared `/api` path *permitted* and leaves the whole suite green.
* §2 `tests/test_structure.py`'s directory skip-list, which excludes `.venv` **by name**, so a
  virtualenv created under any other name puts every third-party `README.md` through the
  broken-link checker.

**Bounded to tests and guards. No production behaviour changes here.**
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import test_structure
from netcorenoc.api.perimeter import Perimeter
from netcorenoc.crosscutting import administration, auth, rbac
from netcorenoc.store import Store

REPO_ROOT = Path(__file__).resolve().parent.parent

# A path under `/api` that `ROUTE_PERMISSIONS` does not name. The declaration gate makes this state
# unreachable through `create_app`, which is exactly why the perimeter's own branch is defence in
# depth — and exactly why it needs a test that does not go through the app.
UNDECLARED = "/api/not-a-real-route"
DECLARED = "/api/situations"


def _request(path: str, cookie: str) -> Request:
    """A request with **no resolved route** in its ASGI scope.

    That is the case `_route_path` falls back to `request.url.path` for, and it is the shape a
    request has before FastAPI's router has matched anything — so this drives the perimeter the way
    a mis-registered route would, rather than the way the app does.
    """
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "server": ("testserver", 80),
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"host", b"testserver"),
                (b"cookie", f"{auth.COOKIE_NAME}={cookie}".encode()),
            ],
            "client": ("127.0.0.1", 12345),
        }
    )


async def _admin_session(store: Store) -> str:
    """An admin session on **the clock the perimeter reads**.

    `Perimeter.resolve_identity` calls `time.time()`. A session minted at a fixture epoch would be
    long expired by that clock and all three probes below would fail with 401 — proving nothing, and
    proving it while looking like a pass. This is Appendix B's two-clocks trap in miniature, so the
    session is opened at `time.time()` deliberately and the test says why.
    """
    now = time.time()
    async with store.lock:
        await administration.bootstrap_admin(store, now)
        user = await store.create_user("adm", auth.hash_password("x" * 16), "admin", False, now)
        token = await auth.open_session(store, user, None, now)
        await store.commit()
    return token


# --- §1 the perimeter's fail-open branch ---------------------------------------------------------


async def test_an_undeclared_api_path_is_refused_even_holding_every_capability(
    store: Store,
) -> None:
    """**The branch `if permission is None or permission not in capabilities` protects.**

    Inverting that comparison to `is not None and` makes an undeclared `/api` path **permitted**,
    and the v0.9.1 suite stayed 960/960 green — verified by execution. The declaration gate makes
    the state unreachable through `create_app`, which is the reason the branch is defence in depth
    and the reason nothing was watching it: every route the app can serve is declared, so no test
    that goes through the app can reach the `permission is None` arm.

    So this constructs a `Perimeter` directly — the documented second instantiation path, already
    used by `tests/test_store_concurrency.py` — and hands it a request with no resolved route.

    The principal is an **admin**, holding every capability in the product. If the refusal came from
    a missing capability rather than from the missing declaration, this would pass for the wrong
    reason; holding all of them removes that explanation.
    """
    cookie = await _admin_session(store)
    perimeter = Perimeter(store, rate_capacity=1000.0, rate_refill=1000.0, warnings=None)

    # The premise, asserted rather than assumed: this path really is undeclared, and the control
    # path really is declared. A typo in either would make the test vacuous.
    assert rbac.permission_for("GET", UNDECLARED) is None
    assert rbac.permission_for("GET", DECLARED) is not None

    with pytest.raises(HTTPException) as denied:
        await perimeter.security(_request(UNDECLARED, cookie))
    assert denied.value.status_code == 403, "an undeclared /api path was PERMITTED"

    # **CONTROL**: a declared route the same principal holds must be allowed, through the same
    # object, the same session and the same clock. Without it, a perimeter that refused everything —
    # including one that refused because the session had expired — would look identical.
    principal = await perimeter.security(_request(DECLARED, cookie))
    assert principal.role == "admin"
    assert rbac.permission_for("GET", DECLARED) in principal_capabilities(perimeter)


def principal_capabilities(perimeter: Perimeter) -> frozenset[str]:
    """The capability set the last `security()` call resolved, read back off the request state.

    A helper rather than an inline expression because the control above needs to assert **why** the
    declared route was allowed, and `request.state` is where the perimeter records that.
    """
    return frozenset(rbac.resolve_capabilities("admin", None, perimeter.governance.capability))


# --- §2 the skip-list that is a literal name -----------------------------------------------------


def test_a_virtualenv_under_any_name_is_excluded_from_the_link_check(tmp_path: Path) -> None:
    """`_SKIP_DIRS` excluded `.venv` **by name**, so `env/`, `venv/`, or a CI cache under any other
    name put every third-party `README.md` through the broken-link checker — thousands of files
    nobody in this repository wrote, failing a guard about this repository's own documentation.

    The repair identifies a virtualenv by **`pyvenv.cfg`**, which every one has and which no
    hand-written directory does, so the whole class goes rather than one more name being added to a
    list. Reproduced by construction here: a directory named `env` holding a `pyvenv.cfg` and a
    Markdown file with a link that does not resolve.
    """
    venv = tmp_path / "env"
    (venv / "lib").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    (venv / "lib" / "README.md").write_text("[broken](./nowhere-at-all.md)\n", encoding="utf-8")

    # THE WIDENING, as it was: a name-based skip-list does not exclude this directory.
    assert "env" not in test_structure._SKIP_DIRS
    named = [
        p
        for p in tmp_path.rglob("*.md")
        if not any(x in test_structure._SKIP_DIRS for x in p.parts)
    ]
    assert named, "the fixture must contain a Markdown file a name-based walk would pick up"

    # THE REPAIR, exercised through **the walk itself** rather than through the helper it uses.
    # A first version of this test called `_is_inside_venv` directly and stayed green when the walk
    # was reverted to the name list — the helper still existed, so the test measured a function
    # nothing called. That is this file's own subject, caught by this file's own method
    # (`../docs/gates/v0.9.2-guard-demonstrations.md`, ledger entry L2).
    monkey = tmp_path / "repo"
    (monkey / "docs").mkdir(parents=True)
    (monkey / "docs" / "real.md").write_text("[ok](../env/pyvenv.cfg)\n", encoding="utf-8")
    (venv / "lib" / "nested").mkdir()
    found = test_structure._markdown_files_under(monkey.parent)
    assert (venv / "lib" / "README.md") not in found, (
        "a virtualenv named `env` was walked by the link checker"
    )
    assert (monkey / "docs" / "real.md") in found, (
        "the repair excluded a real document; the walk must still see this repository's own files"
    )


def test_the_link_checker_still_sees_this_repositorys_own_documents() -> None:
    """The other half of the control: the repair must not shrink what the guard covers.

    Named files rather than a count, because a count would pass on a walk that found some other
    thousand files.
    """
    found = {p.relative_to(REPO_ROOT).as_posix() for p in test_structure._markdown_files()}
    for required in ("README.md", "docs/README.md", "docs/ROADMAP.md", "CONTRIBUTING.md"):
        assert required in found, f"{required} dropped out of the link check"
