"""The route-declaration gate: a route that has not declared itself cannot be registered.

Before v0.7.2 a route was registered with ``@app.get("/api/x")`` and its capability lived in a
separate dict in `rbac.py`, joined by a path string. The join was invisible at the point of
registration, which is exactly why the project had to invent a runtime fail-closed **and** a CI
completeness test to catch what the code could not express. F34 was the same shape one level down:
a route's *scope posture* was not expressed anywhere at all, so three write routes simply did not
have one.

This module moves the first failure forward and makes the second one exist. Registration goes
through :class:`DeclaredRoutes`, which consults `rbac.ROUTE_PERMISSIONS` and `rbac.ROUTE_SCOPE`
**as the route is registered** — that is, while `create_app` is building the application, before
the process can serve a single request. Not per request, and not only in CI.

`rbac.py` remains the single source of authority: nothing here decides anything, it only refuses
to register a route the authority has not been told about.

Two exemptions, both by explicit consultation rather than by omission:

* **Non-``/api`` paths** — ``/healthz``, ``/readyz``, ``/`` and the static assets. They resolve no
  identity and require no capability, so they are not in the authorization map by construction and
  there is nothing for them to declare.
* **`rbac.PUBLIC_ROUTES`** — currently ``POST /api/login`` alone. Exempt from both tables, as it
  has been since v0.2.0.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from fastapi.types import DecoratedCallable

from netcorenoc import rbac


class UndeclaredRouteError(RuntimeError):
    """A route was registered without declaring its capability, its scope posture, or both.

    Raised while the application is being built, so an appliance carrying an undeclared route does
    not start — the same class of guarantee as the capability ceiling: structural, not checked.
    """


def require_declaration(method: str, path: str) -> None:
    """Refuse a route that `rbac.py` has not been told about. The gate itself."""
    if not path.startswith("/api"):
        return  # static / health surface: no identity, no capability, nothing to declare
    key = (method, path)
    if key in rbac.PUBLIC_ROUTES:
        return  # deliberately public: exempt from both tables
    missing = [
        name
        for name, table in (
            ("rbac.ROUTE_PERMISSIONS", rbac.ROUTE_PERMISSIONS),
            ("rbac.ROUTE_SCOPE", rbac.ROUTE_SCOPE),
        )
        if key not in table
    ]
    if missing:
        raise UndeclaredRouteError(
            f"{method} {path} is not declared in {' and '.join(missing)}. Every /api route must "
            "state the capability it requires and the visibility-scope posture it has, or be "
            "listed in rbac.PUBLIC_ROUTES. Declare it there — never here."
        )


class DeclaredRoutes:
    """`app.get`/`app.post`/`app.delete`, gated. **The** registration path for the API.

    Route modules hold one of these as ``route`` and register through it, so a reader sees the
    declaration requirement at the point of registration rather than three files away. A test
    (`tests/test_declaration.py`) asserts that no ``@app.<verb>`` decorator survives anywhere
    under `netcorenoc/api/`, so the discipline cannot be bypassed by a contributor in a hurry.
    """

    def __init__(self, app: FastAPI) -> None:
        self._app = app

    def get(self, path: str, **kwargs: Any) -> Callable[[DecoratedCallable], DecoratedCallable]:
        require_declaration("GET", path)
        return self._app.get(path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Callable[[DecoratedCallable], DecoratedCallable]:
        require_declaration("POST", path)
        return self._app.post(path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Callable[[DecoratedCallable], DecoratedCallable]:
        require_declaration("DELETE", path)
        return self._app.delete(path, **kwargs)
