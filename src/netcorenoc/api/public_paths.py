"""The paths this appliance serves without resolving an identity (F41).

Split out of `declare.py` in v0.26.0 at the 400-line module guard, and re-exported there by
identity: `declare.UNAUTHENTICATED_PATHS` is still the name the route gate and every test read.
"""

from __future__ import annotations

__all__ = ["UNAUTHENTICATED_PATHS"]

# Every path this appliance serves **without resolving an identity** (F41). Membership here is the
# claim "no capability is required to fetch this", and it is the whole of that claim: a non-`/api`
# path that is not listed cannot be registered.
#
# Deliberately a literal set rather than an import from `routes_static`, which would be a circular
# import — `routes_static` registers through this module. The derivation is asserted instead of
# performed, by `tests/test_declaration.py::test_f41_the_allowlist_matches_what_is_actually_served`:
#
#     UNAUTHENTICATED_PATHS == {"/healthz", "/readyz", "/", OPENAPI}
#                              | {"/" + asset for asset in STATIC_ASSETS}
#
# so adding a static asset without listing it here fails, and listing one that is not served fails
# too. `/openapi.json` is FastAPI's own registration, not one of ours; it is public on this surface
# and is listed because this set is a statement about what is served, not about who registered it.
UNAUTHENTICATED_PATHS: frozenset[str] = frozenset(
    {
        "/healthz",  # liveness
        "/readyz",  # orchestrator readiness; ok/not-ok only, no detail
        "/",  # the single-page UI
        "/openapi.json",  # the schema; docs_url and redoc_url are disabled
        "/style.css",
        "/.well-known/security.txt",  # RFC 9116
        # v0.13.0: the console is an ES module graph (ADR #175). Every module is listed
        # individually, and that verbosity is the point: each line is the reviewable claim
        # "fetching this needs no capability". A glob here would make the claim unreadable
        # and would re-open F41 — an exemption that is accidentally true of the next path
        # somebody adds.
        "/app.js",
        "/app/api.js",
        "/app/destructive.js",
        "/app/dom.js",
        "/app/format.js",
        # v0.16.6: the chart vocabulary and the arithmetic behind it (DECISIONS #305, #307).
        "/app/charts.js",
        "/app/compare.js",
        "/app/chartdata.js",
        # v0.15.3: the drawn icon family (#236) and the shared password surface (V.2). Both are
        # fetched before any identity exists — the sign-in card imports them — so both are
        # unauthenticated by necessity as well as by claim.
        "/app/icons.js",
        "/app/login.js",
        "/app/parameters.js",
        "/app/password.js",
        "/app/registry.js",
        "/app/router.js",
        "/app/session.js",
        "/app/shell.js",
        "/app/sidebar.js",
        "/app/store.js",
        "/app/theme.js",
        "/app/views/account.js",
        "/app/views/audit.js",
        "/app/views/classes.js",
        "/app/views/corpus.js",
        "/app/views/entities.js",
        "/app/views/graph.js",
        "/app/views/labelling.js",
        "/app/views/overview.js",
        "/app/views/promotion.js",
        "/app/views/maintenance.js",
        "/app/views/quarantine.js",
        "/app/views/scorer.js",
        "/app/views/settings.js",
        "/app/views/situations.js",
        "/app/views/timeline.js",
        # v0.15.3: the four modules under `views/` that were never views (#239).
        "/app/views/parts/facts.js",
        "/app/views/parts/model.js",
        "/app/views/parts/retention.js",
        "/app/views/parts/verdict.js",
        # v0.16.0: the operator gestures, their confidence control and the history list.
        "/app/views/parts/lifecycle.js",
        "/app/views/parts/restructure.js",
        "/app/views/parts/declare.js",
        "/app/views/parts/members.js",
        "/app/views/parts/why.js",
        # v0.16.1: the situation card, split out when the server-side search pushed
        # `views/situations.js` over the module-graph guard (DECISIONS #265).
        "/app/views/parts/card.js",
        "/app/views/parts/correlation.js",
        # v0.19.0: the Overview's model line. A static module like every other one — it resolves
        # no identity; the two routes it calls do.
        "/app/views/parts/models.js",
        # v0.16.4: the judgement surface, split out when the state-dependent action surface pushed
        # `views/parts/card.js` over the same guard (DECISIONS #291, #293).
        "/app/views/parts/judge.js",
        "/app/views/parts/bulkclear.js",
        "/app/views/parts/decide.js",
        # v0.16.4: the controls that narrow the situation list — the count cards, the search box
        # and the tabs — split out when they became a block (DECISIONS #288, item 3, item 5).
        "/app/views/parts/finder.js",
        # v0.16.6: the Overview's five chart bands (DECISIONS #304).
        "/app/views/parts/pulse.js",
        # v0.16.7: the Overview's appliance half (DECISIONS #318).
        "/app/views/parts/keeping.js",
        # v0.16.7: active alarms by band, and what is on no band (DECISIONS #312).
        "/app/views/parts/severity.js",
        # v0.21.0: the maintenance window. Four modules, on the seams Part IV draws — the
        # stepper, the per-target rule chips, the two time controls, and the marker every
        # role sees. Each is listed individually for F41's reason: a line here is the
        # reviewable claim "fetching this needs no capability", and a glob would make that
        # claim unreadable.
        "/app/views/parts/mwform.js",
        "/app/views/parts/mwdraft.js",
        "/app/views/parts/mwrules.js",
        "/app/views/parts/mwtime.js",
        "/app/views/parts/mwmarker.js",
        "/app/views/parts/mwsummary.js",
        "/app/views/parts/mwreview.js",
        # v0.16.6: the Graph screen's second projection and its tables (DECISIONS #307, #310).
        # v0.16.6: the timeline's controls, column chart and summary (DECISIONS #311).
        "/app/views/parts/marks.js",
        # v0.16.6: evidence over time, and what cannot be drawn (DECISIONS #308).
        "/app/views/parts/evidence.js",
        # v0.16.1: the console icon (F96). `img-src 'self'` forbids a data: URI.
        "/favicon.svg",
        # v0.16.4: the two disclosures in the top bar — the bell and the health control — split out
        # of `shell.js` (DECISIONS #288, #289).
        "/app/notices.js",
        "/app/health.js",
        "/app/info.js",
        "/app/layout.js",
        "/app/netgraph.js",
        "/app/views/parts/element.js",
        "/app/views/parts/oidtree.js",
        "/app/views/parts/importbox.js",
        "/app/views/parts/mwdetail.js",
        "/app/stack.js",
        "/app/views/parts/sitsummary.js",
        "/app/views/parts/topassets.js",
        "/app/views/parts/tlfilters.js",
        "/app/views/parts/sequence.js",
        "/app/views/parts/nedetail.js",
        "/app/avatar.js",
        "/app/views/access.js",
        "/app/views/parts/capgrid.js",
        "/app/views/parts/people.js",
        "/app/views/parts/roles.js",
        "/app/views/parts/tokenspanel.js",
        "/app/views/parts/visibility.js",
        "/app/widgets.js",
        # v0.26.0 (ADR #414): the model's charts, Settings' Correlation, Autonomy and Search tabs,
        # and the Judge dashboard's two blocks.
        "/app/modelcharts.js",
        "/app/views/parts/decider.js",
        "/app/views/parts/autonomy.js",
        "/app/views/parts/searchpanel.js",
        "/app/views/parts/sitejudge.js",
        # v0.27.0 (ADRs #423, #427): the league on the Judge screen.
        "/app/views/parts/league.js",
        "/app/views/parts/leaguecharts.js",
        "/app/views/parts/leaguecompare.js",
        # Vendored third-party assets, pinned by CHECKSUMS.txt.
        "/vendor/htm-3.1.1.module.js",
        "/vendor/preact-10.29.8.module.js",
    }
)
