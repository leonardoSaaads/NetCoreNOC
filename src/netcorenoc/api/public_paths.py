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
# performed, by
# `tests/api/test_declaration.py::test_f41_the_allowlist_matches_what_is_actually_served`:
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
        "/styles/core/tokens.css",
        "/styles/core/base.css",
        "/styles/core/shell.css",
        "/styles/core/charts.css",
        "/styles/core/components.css",
        "/styles/broker/situations.css",
        "/styles/broker/graph.css",
        "/styles/core/panels.css",
        "/styles/core/sign-in.css",
        "/styles/core/widths.css",
        "/styles/broker/health.css",
        "/styles/broker/maintenance.css",
        "/styles/broker/timeline.css",
        "/styles/broker/catalogue.css",
        "/styles/broker/overview.css",
        "/styles/broker/entities.css",
        "/styles/admin/people.css",
        "/styles/broker/learning.css",
        "/styles/admin/settings.css",
        "/styles/noc/plane.css",
        "/styles/noc/secrets.css",
        "/styles/noc/cases.css",
        "/styles/noc/overview.css",
        "/styles/noc/routines.css",
        "/styles/noc/agents.css",
        "/styles/noc/customers.css",
        "/styles/core/marks.css",
        "/styles/broker/first-run.css",
        "/styles/broker/learning-screens.css",
        "/styles/admin/audit.css",
        "/styles/noc/ollama.css",
        "/styles/broker/brokers.css",
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
        "/app/markdown.js",
        "/app/mailwizard.js",
        "/app/mssignin.js",
        # v0.16.6: the chart vocabulary and the arithmetic behind it (DECISIONS #305, #307).
        "/app/charts.js",
        "/app/compare.js",
        "/app/chartdata.js",
        # v0.15.3: the drawn icon family (#236) and the shared password surface (V.2). Both are
        # fetched before any identity exists — the sign-in card imports them — so both are
        # unauthenticated by necessity as well as by claim.
        "/app/icons.js",
        "/app/logo.js",
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
        "/app/views/admin/account.js",
        "/app/views/admin/audit.js",
        "/app/views/broker/classes.js",
        "/app/views/broker/entities.js",
        "/app/views/broker/graph.js",
        "/app/views/broker/learning.js",
        "/app/views/broker/parts/learnuml.js",
        "/app/views/broker/overview.js",
        "/app/views/broker/promotion.js",
        "/app/views/broker/maintenance.js",
        "/app/views/broker/quarantine.js",
        "/app/views/broker/scorer.js",
        "/app/views/admin/settings.js",
        "/app/views/broker/situations.js",
        "/app/views/broker/timeline.js",
        # v0.15.3: the four modules under `views/` that were never views (#239).
        "/app/views/admin/parts/facts.js",
        "/app/views/broker/parts/model.js",
        "/app/views/admin/parts/retention.js",
        "/app/views/broker/parts/verdict.js",
        # v0.16.0: the operator gestures, their confidence control and the history list.
        "/app/views/broker/parts/lifecycle.js",
        "/app/views/broker/parts/restructure.js",
        "/app/views/broker/parts/declare.js",
        "/app/views/broker/parts/members.js",
        "/app/views/broker/parts/why.js",
        # v0.16.1: the situation card, split out when the server-side search pushed
        # `views/broker/situations.js` over the module-graph guard (DECISIONS #265).
        "/app/views/broker/parts/card.js",
        "/app/views/broker/parts/correlation.js",
        # v0.19.0: the Overview's model line. A static module like every other one — it resolves
        # no identity; the two routes it calls do.
        "/app/views/broker/parts/models.js",
        # v0.16.4: the judgement surface, split out when the state-dependent action surface pushed
        # `views/broker/parts/card.js` over the same guard (DECISIONS #291, #293).
        "/app/views/broker/parts/judge.js",
        "/app/views/broker/parts/bulkclear.js",
        "/app/views/broker/parts/decide.js",
        # v0.16.4: the controls that narrow the situation list — the count cards, the search box
        # and the tabs — split out when they became a block (DECISIONS #288, item 3, item 5).
        "/app/views/broker/parts/finder.js",
        # v0.16.6: the Overview's five chart bands (DECISIONS #304).
        "/app/views/broker/parts/pulse.js",
        # v0.16.7: the Overview's appliance half (DECISIONS #318).
        "/app/views/broker/parts/keeping.js",
        "/app/views/broker/parts/pipeline.js",
        # v0.16.7: active alarms by band, and what is on no band (DECISIONS #312).
        "/app/views/broker/parts/severity.js",
        # v0.21.0: the maintenance window. Four modules, on the seams Part IV draws — the
        # stepper, the per-target rule chips, the two time controls, and the marker every
        # role sees. Each is listed individually for F41's reason: a line here is the
        # reviewable claim "fetching this needs no capability", and a glob would make that
        # claim unreadable.
        "/app/views/broker/parts/mwform.js",
        "/app/views/broker/parts/mwdraft.js",
        "/app/views/broker/parts/mwrules.js",
        "/app/views/broker/parts/mwtime.js",
        "/app/views/broker/parts/mwmarker.js",
        "/app/views/broker/parts/mwsummary.js",
        "/app/views/broker/parts/mwreview.js",
        "/app/views/broker/parts/mwwhen.js",
        # v0.16.6: the Graph screen's second projection and its tables (DECISIONS #307, #310).
        # v0.16.6: the timeline's controls, column chart and summary (DECISIONS #311).
        "/app/views/broker/parts/marks.js",
        # v0.16.6: evidence over time, and what cannot be drawn (DECISIONS #308).
        "/app/views/broker/parts/evidence.js",
        # v0.16.1: the console icon (F96). `img-src 'self'` forbids a data: URI.
        "/favicon.svg",
        # v0.16.4: the two disclosures in the top bar — the bell and the health control — split out
        # of `shell.js` (DECISIONS #288, #289).
        "/app/notices.js",
        "/app/health.js",
        "/app/info.js",
        "/app/layout.js",
        "/app/netgraph.js",
        "/app/views/broker/parts/element.js",
        "/app/views/broker/parts/oidtree.js",
        "/app/views/broker/parts/importbox.js",
        "/app/views/broker/parts/mwdetail.js",
        "/app/views/broker/parts/mwboard.js",
        "/app/stack.js",
        "/app/views/broker/parts/sitsummary.js",
        "/app/views/broker/parts/topassets.js",
        "/app/views/broker/parts/tlfilters.js",
        "/app/views/broker/parts/sequence.js",
        "/app/views/broker/parts/nedetail.js",
        "/app/avatar.js",
        "/app/views/admin/access.js",
        "/app/views/admin/parts/capgrid.js",
        "/app/views/admin/parts/people.js",
        "/app/views/admin/parts/custscope.js",
        "/app/views/admin/parts/roles.js",
        "/app/views/admin/parts/tokenspanel.js",
        "/app/views/admin/parts/visibility.js",
        "/app/widgets.js",
        # v0.26.0 (ADR #414): the model's charts, Settings' Correlation, Autonomy and Search tabs,
        # and the Judge dashboard's two blocks.
        "/app/modelcharts.js",
        "/app/views/broker/parts/decider.js",
        "/app/views/admin/parts/autonomy.js",
        "/app/views/broker/parts/searchpanel.js",
        "/app/views/broker/parts/sitejudge.js",
        # v0.27.0 (ADRs #423, #427): the league on the Judge screen.
        "/app/views/broker/parts/league.js",
        "/app/views/broker/parts/leaguecharts.js",
        "/app/views/broker/parts/leaguecompare.js",
        # v0.30.0 (ADRs #444-#446): recovery on the sign-in card — fetched before any identity
        # exists — Settings → SNMP and Email, the API reference, the visibility examples, and the
        # sign-in artwork.
        "/app/recover.js",
        # ADR #482: the sign-in's second step — the QR code, the code field, recovery codes. The
        # sign-in card imports it, so it is fetched before any identity exists.
        "/app/twofactor.js",
        "/app/views/admin/parts/snmp.js",
        "/app/views/admin/parts/snmpuser.js",
        "/app/views/admin/parts/email.js",
        "/app/views/admin/parts/apiref.js",
        "/app/views/admin/parts/scopeexamples.js",
        # The NOC plane (ADRs #454-#458): the plane switch and the Secrets screen's modules.
        "/app/plane.js",
        "/app/views/noc/secrets.js",
        "/app/views/noc/parts/secretform.js",
        "/app/views/noc/parts/stepup.js",
        "/app/views/noc/parts/secretdetail.js",
        "/app/views/noc/parts/nocgrants.js",
        # Cases (ADRs #459-#461): the queue, the KPIs and one case's parts.
        "/app/views/noc/cases.js",
        "/app/views/noc/nockpis.js",
        "/app/views/noc/parts/caseclock.js",
        "/app/views/noc/parts/caseform.js",
        "/app/views/noc/parts/caseacts.js",
        "/app/views/noc/parts/casefeed.js",
        "/app/views/noc/parts/caseemail.js",
        "/app/views/noc/parts/casedetail.js",
        "/app/views/noc/routines.js",
        # ADR #468: the agents, the organization, the roster, a case's milestones and assignment.
        "/app/roster.js",
        "/app/views/noc/agents.js",
        "/app/views/noc/org.js",
        "/app/views/noc/parts/nocui.js",
        "/app/views/noc/parts/agentkey.js",
        "/app/views/noc/parts/agentform.js",
        "/app/views/noc/parts/agentdetail.js",
        "/app/views/noc/parts/agentdash.js",
        "/app/views/noc/parts/agentruns.js",
        "/app/views/noc/parts/agentadmin.js",
        "/app/views/noc/parts/casemilestones.js",
        "/app/views/noc/parts/caseassign.js",
        # ADR #470: the NOC's overview, the caller's NOC grants, the AI providers' marks.
        "/app/views/noc/nocoverview.js",
        "/app/brands.js",
        "/app/views/noc/parts/vaultui.js",
        "/app/views/noc/parts/aiproviders.js",
        "/app/views/noc/parts/credlist.js",
        "/app/views/noc/parts/custaccess.js",
        "/app/views/noc/parts/custmail.js",
        "/app/views/noc/parts/custroutines.js",
        "/app/views/noc/parts/custhead.js",
        "/app/views/noc/parts/agentrun.js",
        # ADR #477: what a local Ollama has, and moving an agent in the chart.
        "/app/views/noc/parts/ollamamodels.js",
        "/app/views/noc/parts/orgmove.js",
        # ADR #483: the Overview before the first trap.
        "/app/views/broker/parts/firstrun.js",
        "/app/views/noc/parts/routineform.js",
        "/app/views/noc/parts/nocstart.js",
        "/app/views/admin/parts/personsecurity.js",
        # ADR #473: the customers' books.
        "/app/views/noc/customers.js",
        "/app/views/noc/parts/custbook.js",
        "/app/views/noc/parts/custcontacts.js",
        "/app/views/noc/parts/custdocs.js",
        "/app/views/noc/parts/circuits.js",
        "/app/views/noc/parts/circuitedit.js",
        # ADR #494: the brokers, and the work area's heading.
        "/app/brokers.js",
        "/app/heading.js",
        "/app/views/broker/brokers.js",
        "/app/views/broker/parts/brokerhome.js",
        "/app/views/broker/parts/brokerlearn.js",
        "/app/views/broker/parts/brokerpick.js",
        "/app/views/broker/parts/brokerwizard.js",
        "/login-bg.svg",
        # Vendored third-party assets, pinned by CHECKSUMS.txt.
        "/vendor/htm-3.1.1.module.js",
        "/vendor/inter-5.3.0.latin.woff2",
        "/vendor/preact-10.29.8.module.js",
    }
)
