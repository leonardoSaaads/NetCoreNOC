/* **The view table.** One list, read by the router (to authorise) and by the sidebar (to offer).
 *
 * ## Why one table and not two
 *
 * v0.12.0 had `TABS` for navigation and `prunePanels` for the DOM, and their agreeing was a
 * convention nothing enforced. `tests/ui/test_security_ui.py` guarded it by parsing the source text
 * of `TABS`, which v0.12.0's Phase 0 measured as unable to fail for the thing it guarded. Here
 * the sidebar renders `reachableViews(capabilities)` and the router calls `resolve()`, and both
 * read the `capability` field on the same object. There is nothing for them to disagree about.
 *
 * ## Every `capability` here names a capability the SERVER enforces
 *
 * Not a role, not a rank, not a UI-local notion of "admin screen". `tests/ui/test_ui_invariants.py`
 * checks each entry against `rbac.PERMISSIONS`, and checks by EXECUTION that a view which reads a
 * route does not declare a weaker capability than the route requires. A view gated on something
 * `rbac` has never heard of is a bug this table cannot hide.
 *
 * ## Nothing here is a placeholder
 *
 * Every entry renders a screen backed by a route that exists today. There is no *Diagnose*, no
 * *Escalate*, no greyed-out *Troubleshooting* (draft §2, §11.7). Adding Phase 2's group was
 * adding entries to this list and a heading to `sidebar.GROUPS` — which is precisely the
 * obligation §2 said v0.13.0 owed the future, and precisely the whole of it.
 *
 * ## Two planes: the Broker and the NOC (ADR #454)
 *
 * `plane` says which half of the product a screen belongs to. The **Broker** (phase 1, every
 * screen without a `plane`) receives traps, correlates them into situations, trains the models
 * that do it and keeps the inventory. The **NOC** (phase 2) works the situations that reach the
 * operations centre — beginning with the credentials people and agents use to reach equipment.
 * The top bar switches between them (`plane.js`) and the sidebar shows one plane at a time; the
 * router and the sidebar still read this one table, so a plane cannot offer what the router would
 * refuse.
 */

import { Overview } from "./views/overview.js";
import { Situations } from "./views/situations.js";
import { GraphView } from "./views/graph.js";
import { Timeline } from "./views/timeline.js";
import { Entities } from "./views/entities.js";
import { Classes } from "./views/classes.js";
import { Maintenance } from "./views/maintenance.js";
import { Labelling } from "./views/labelling.js";
import { Corpus } from "./views/corpus.js";
import { Promotion } from "./views/promotion.js";
import { Access } from "./views/access.js";
import { html } from "./dom.js";
import { Settings } from "./views/settings.js";
import { Quarantine } from "./views/quarantine.js";
import { Audit } from "./views/audit.js";
import { Account } from "./views/account.js";
import { Secrets } from "./views/secrets.js";
import { Cases } from "./views/cases.js";
import { NocKpis } from "./views/nockpis.js";

/**
 * `id`         the URL fragment and the DOM's `data-view`
 * `capability` what the server requires for the route this view reads; a list when several
 * `group`      which sidebar group, or null for the item above the first heading
 * `hidden`     reachable by address but not offered in navigation (the account screen)
 * `plane`      "noc" for a phase 2 screen; absent is the Broker (see `PLANES`)
 * `icon`       the name of a mark in `icons.js`, drawn beside the label. Never the only signal:
 *              every call site renders it `aria-hidden` next to the text label
 */
export const VIEWS = [
  {
    id: "overview", label: "Overview", icon: "overview", group: null,
    capability: "stats.read", component: Overview,
    summary: "What is happening now, shaped by what your role can act on.",
  },

  /* ---------- Operations: what is broken now ---------- */
  {
    id: "situations", label: "Situations", icon: "situations", group: "operations",
    capability: "situations.read", component: Situations, countNoun: "open situations",
    summary: "Correlated groups of alarms, and why each alarm was grouped.",
  },
  {
    id: "graph", label: "Network graph", icon: "graph", group: "operations",
    capability: "graph.read", component: GraphView,
    summary: "Learned affinity between network elements.",
  },
  {
    id: "timeline", label: "Timeline", icon: "timeline", group: "operations",
    capability: "timeline.read", component: Timeline,
    summary: "Raises and clears over time, per device.",
  },
  {
    id: "entities", label: "Entities", icon: "entities", group: "operations",
    capability: "entities.read", component: Entities,
    summary: "Every network element: its alarms, its components, and where it stands.",
  },
  {
    // v0.22.0 (item 16): named for what it holds — trap OIDs, their names and severities, whole
    // vendor branches and imported lists — not "Alarm classes". The address stays `#/classes`.
    id: "classes", label: "Trap catalogue", icon: "classes", group: "operations",
    capability: "classes.read", component: Classes,
    summary: "Trap OIDs by name and severity.",
  },
  {
    // v0.21.0. `mw.read` is `viewer` and that is prime directive 4 rather than a convenience: a
    // viewer who cannot see that planned work exists reads a quiet estate as a healthy one. The
    // screen offers the form only where `mw.write` is held, which `can()` decides on the control.
    id: "maintenance", label: "Maintenance", icon: "maintenance", group: "operations",
    capability: "mw.read", component: Maintenance,
    summary: "Planned work: what is scheduled, what is running, and what it still collects.",
  },

  /* ---------- Evidence: what has been learned, and what is refused ---------- */
  {
    id: "labelling", label: "Labelling", icon: "labelling", group: "evidence",
    capability: ["situations.read", "feedback.write"], component: Labelling,
    summary: "Confirm or split a grouping, and see what your labels have produced.",
  },
  {
    id: "corpus", label: "Corpus", icon: "corpus", group: "evidence",
    capability: "config.read", component: Corpus,
    summary: "What capture costs in rows, and the three retention tiers.",
  },
  {
    id: "promotion", label: "Judge & promotion", icon: "promotion", group: "evidence",
    // Both reads, because the screen issues both on mount (v0.22.0: the running scorer's panel
    // moved here from Situations) — the rule the Governance entry states.
    // v0.26.0 (ADR #414): chart-first; it reads `/api/judge` (`model.read`) as well.
    capability: ["promotion.read", "correlation.read", "model.read"], component: Promotion,
    summary: "How good the model deciding links is, measured, and what the site's labels say.",
  },

  /* ---------- Administer: the machine itself ---------- */
  {
    // v0.25.0 (ADR #403): Users, Service tokens and Governance, as the tabs of one screen. Mounting
    // reads the accounts; every other tab reads only when opened, and only if the session may.
    id: "access", label: "People & access", icon: "users", group: "administer",
    capability: "users.manage", component: Access,
    summary: "Who can sign in, what each person and program may do, and what they see.",
  },
  {
    id: "settings", label: "Settings", icon: "settings", group: "administer",
    // v0.26.0 (ADR #414): Link scorer folded in as the Correlation tab, beside Autonomy and Search.
    capability: "config.read", component: Settings,
    summary: "What decides links, autonomy, what the receiver accepts, email, and every parameter.",
  },
  {
    id: "quarantine", label: "Quarantine", icon: "quarantine", group: "administer",
    capability: "quarantine.read", component: Quarantine,
    summary: "Datagrams the parser refused. Reading this list is audited.",
  },
  {
    id: "audit", label: "Audit log", icon: "audit", group: "administer",
    capability: "audit.read", component: Audit,
    summary: "The hash-chained record of every change, and its verification state.",
  },

  /* ---------- NOC (phase 2): working situations, and reaching equipment ---------- */
  {
    // ADRs #459-#461. Every principal who may read a situation may read its case and post on its
    // feed; taking and working one is `cases.work`, offered in the screen from the server's
    // `actions`. First in the NOC plane: it is where the switch lands.
    id: "cases", label: "Cases", icon: "cases", group: "cases", plane: "noc",
    capability: "cases.read", component: Cases,
    summary: "The NOC queue: situations to take, work, wait on, and resolve — with their clocks.",
  },
  {
    id: "kpis", label: "KPIs", icon: "kpi", group: "cases", plane: "noc",
    capability: "cases.read", component: NocKpis,
    summary: "How fast the NOC takes a case and documents it, against the targets.",
  },
  {
    // ADRs #454-#458. `secrets.read` is editor, and the screen still opens the vault only for a
    // custodian — an admin, or an editor an admin named; anyone else is told why, and offered
    // nothing. Every write below it is gated in the screen on its own capability.
    id: "secrets", label: "Secrets", icon: "vault", group: "access", plane: "noc",
    capability: "secrets.read", component: Secrets,
    summary: "Credentials for equipment, sealed twice. Agents get a lease, never the secret.",
  },

  /* ---------- reachable by address, not offered in navigation ---------- */
  {
    id: "account", label: "Your account", icon: "account", group: null, hidden: true,
    capability: "self.read", component: Account,
    summary: "Your name, photo, password and what you can do.",
  },
  // v0.25.0's Link scorer address, kept so links and bookmarks still land (v0.26.0).
  {
    id: "scorer", label: "Link scorer", icon: "scorer", group: null, hidden: true,
    capability: ["scorer.read", "scorer.write"],
    component: (p) => html`<${Settings} ...${p} tab="correlation" />`,
    summary: "The formula that decides which alarms group, when the formula is chosen.",
  },
  // The three addresses v0.24.0 had, kept so links and bookmarks still land (v0.25.0).
  {
    id: "users", label: "People", icon: "users", group: null, hidden: true,
    capability: "users.manage", component: (p) => html`<${Access} ...${p} tab="people" />`,
    summary: "Who can sign in, what each person and program may do, and what they see.",
  },
  {
    id: "tokens", label: "Service tokens", icon: "tokens", group: null, hidden: true,
    capability: "tokens.manage", component: (p) => html`<${Access} ...${p} tab="tokens" />`,
    summary: "Credentials for programs, shown once.",
  },
  {
    id: "governance", label: "Roles", icon: "governance", group: null, hidden: true,
    capability: ["rbac.read", "scope.read"], component: (p) => html`<${Access} ...${p} tab="roles" />`,
    summary: "What each role holds, and what it sees.",
  },
];

export const DEFAULT_VIEW = "overview";

/** Which plane a view belongs to. */
export function planeOf(view) { return view.plane || "broker"; }
