"""The console's stylesheets, in the order `index.html` links them — the cascade (ADR #504).

`ui/style.css` was cut at its own section marks into `ui/styles/<area>/<topic>.css`; the cut kept
every rule in its place, so this order IS the cascade the single file had. `routes/static.py`
serves exactly these (nothing is served from a directory, ADR #175), and
`tests/ui/test_stylesheet.py` checks this list, `index.html` and the files on disk agree.
"""

from __future__ import annotations

STYLESHEETS: tuple[str, ...] = (
    "styles/core/tokens.css",
    "styles/core/base.css",
    "styles/core/shell.css",
    "styles/core/charts.css",
    "styles/core/components.css",
    "styles/broker/situations.css",
    "styles/broker/graph.css",
    "styles/core/panels.css",
    "styles/core/sign-in.css",
    "styles/core/widths.css",
    "styles/broker/health.css",
    "styles/broker/maintenance.css",
    "styles/broker/timeline.css",
    "styles/broker/catalogue.css",
    "styles/broker/overview.css",
    "styles/broker/entities.css",
    "styles/admin/people.css",
    "styles/broker/learning.css",
    "styles/admin/settings.css",
    "styles/noc/plane.css",
    "styles/noc/secrets.css",
    "styles/noc/cases.css",
    "styles/noc/overview.css",
    "styles/noc/routines.css",
    "styles/noc/agents.css",
    "styles/noc/customers.css",
    "styles/core/marks.css",
    "styles/broker/first-run.css",
    "styles/broker/learning-screens.css",
    "styles/admin/audit.css",
    "styles/noc/ollama.css",
    "styles/broker/brokers.css",
)
