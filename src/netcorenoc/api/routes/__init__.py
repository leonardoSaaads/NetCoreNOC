"""The twelve route modules. The package above is the machinery they all consume.

**Split out in v0.16.2 (DECISIONS #278), and the boundary is derived rather than chosen.**
v0.15.1's method is to read the import graph, and here it gives exactly one edge: every module in
this package imports `api.context`, `api.declare` and usually `api.models`; the machinery imports
no route module except `app.py`, which assembles them; and **no route module imports another** —
measured, twelve of twelve. A functional grouping (read / operate / model / administer / public)
would be a shape the code does not exhibit, and five directories averaging 2.4 files is worse
navigation than one list of twelve.

The `routes_` prefix went with the move: `api/routes/read.py` says what `api/routes_read.py` said,
seven characters shorter, and the directory now carries the distinction the prefix was carrying.

## Why the twelve are imported here

`create_app` needs all twelve in one namespace, and dropping the `routes_` prefix put twelve
**generic words** into it — `auth`, `audit`, `read`, `operate`, `promotion`, `governance` — beside
a `crosscutting.auth` that module already imports. Absolute imports keep the *paths* unambiguous;
a single namespace holding both `auth` names is not. Importing the submodules here means the
assembly site writes `routes.auth.register(app, ctx)`, which says which `auth` it means in the
line that uses it rather than in an alias twenty lines above.

It is the collision `ruff` found during the move, kept visible rather than aliased away.
"""

from netcorenoc.api.routes import (
    admin,
    annotate,
    audit,
    auth,
    events,
    governance,
    lifecycle,
    operate,
    promotion,
    read,
    scorer,
    static,
)

__all__ = [
    "admin",
    "annotate",
    "audit",
    "auth",
    "events",
    "governance",
    "lifecycle",
    "operate",
    "promotion",
    "read",
    "scorer",
    "static",
]
