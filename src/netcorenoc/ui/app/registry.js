/* **The view table.** One list, read by the router (to authorise) and by the sidebar (to offer).
 *
 * ## Why one table and not two
 *
 * v0.12.0 had `TABS` for navigation and `prunePanels` for the DOM, and their agreeing was a
 * convention nothing enforced. `tests/test_security_ui.py` guarded it by parsing the source text
 * of `TABS`, which v0.12.0's Phase 0 measured as unable to fail for the thing it guarded. Here
 * the sidebar renders `reachableViews(capabilities)` and the router calls `resolve()`, and both
 * read the `capability` field on the same object. There is nothing for them to disagree about.
 *
 * ## Every `capability` here names a capability the SERVER enforces
 *
 * Not a role, not a rank, not a UI-local notion of "admin screen". `tests/test_ui_invariants.py`
 * checks each entry against `rbac.PERMISSIONS`, and checks by EXECUTION that a view which reads a
 * route does not declare a weaker capability than the route requires. A view gated on something
 * `rbac` has never heard of is a bug this table cannot hide.
 *
 * ## Nothing here is a placeholder
 *
 * Every entry renders a screen backed by a route that exists today. There is no *Diagnose*, no
 * *Escalate*, no greyed-out *Troubleshooting* (draft §2, §11.7). Adding Phase 2's group later is
 * adding entries to this list and a heading to `sidebar.GROUPS` — which is precisely the
 * obligation §2 says v0.13.0 owes the future, and precisely the whole of it.
 */

import { Overview } from "./views/overview.js";
import { Situations } from "./views/situations.js";
import { GraphView } from "./views/graph.js";
import { Timeline } from "./views/timeline.js";
import { Entities } from "./views/entities.js";
import { Classes } from "./views/classes.js";
import { Labelling } from "./views/labelling.js";
import { Corpus } from "./views/corpus.js";
import { Promotion } from "./views/promotion.js";
import { Users } from "./views/users.js";
import { Tokens } from "./views/tokens.js";
import { Settings } from "./views/settings.js";
import { Scorer } from "./views/scorer.js";
import { Governance } from "./views/governance.js";
import { Quarantine } from "./views/quarantine.js";
import { Audit } from "./views/audit.js";
import { Account } from "./views/account.js";

/**
 * `id`         the URL fragment and the DOM's `data-view`
 * `capability` what the server requires for the route this view reads; a list when several
 * `group`      which sidebar group, or null for the item above the first heading
 * `hidden`     reachable by address but not offered in navigation (the account screen)
 * `glyph`      a text mark beside the label — severity and section are encoded more than once
 */
export const VIEWS = [
  {
    id: "overview", label: "Overview", glyph: "◎", group: null,
    capability: "stats.read", component: Overview,
    summary: "What is happening now, shaped by what your role can act on.",
  },

  /* ---------- Operations: what is broken now ---------- */
  {
    id: "situations", label: "Situations", glyph: "◉", group: "operations",
    capability: "situations.read", component: Situations, countNoun: "open situations",
    summary: "Correlated groups of alarms, and why each alarm was grouped.",
  },
  {
    id: "graph", label: "Network graph", glyph: "⬡", group: "operations",
    capability: "graph.read", component: GraphView,
    summary: "Learned affinity between network elements.",
  },
  {
    id: "timeline", label: "Timeline", glyph: "▤", group: "operations",
    capability: "timeline.read", component: Timeline,
    summary: "Raises and clears over time, per device.",
  },
  {
    id: "entities", label: "Entities", glyph: "▢", group: "operations",
    capability: "entities.read", component: Entities,
    summary: "What the appliance has learned about each network element, and the evidence.",
  },
  {
    id: "classes", label: "Alarm classes", glyph: "≡", group: "operations",
    capability: "classes.read", component: Classes,
    summary: "Every trap type the appliance has learned, with no configuration.",
  },

  /* ---------- Evidence: what has been learned, and what is refused ---------- */
  {
    id: "labelling", label: "Labelling", glyph: "✓", group: "evidence",
    capability: ["situations.read", "feedback.write"], component: Labelling,
    summary: "Confirm or split a grouping, and see what your labels have produced.",
  },
  {
    id: "corpus", label: "Corpus", glyph: "▦", group: "evidence",
    capability: "config.read", component: Corpus,
    summary: "What capture costs in rows, and the three retention tiers.",
  },
  {
    id: "promotion", label: "Judge & promotion", glyph: "⚖", group: "evidence",
    capability: "promotion.read", component: Promotion,
    summary: "What the gate decided, why it refused, and the seal's query count.",
  },

  /* ---------- Administer: the machine itself ---------- */
  {
    id: "users", label: "Users", glyph: "◍", group: "administer",
    capability: "users.manage", component: Users,
    summary: "Accounts and their roles.",
  },
  {
    id: "tokens", label: "Service tokens", glyph: "⚿", group: "administer",
    capability: "tokens.manage", component: Tokens,
    summary: "Non-interactive credentials, shown once.",
  },
  {
    id: "settings", label: "Settings", glyph: "⚙", group: "administer",
    capability: "config.read", component: Settings,
    summary: "Every parameter, in three classes, with its precedence and its impact.",
  },
  {
    id: "scorer", label: "Link scorer", glyph: "∿", group: "administer",
    // What MOUNTING costs: the read, plus the write this screen exists for. The preview is a
    // separate capability (`scorer.preview`) and is gated on the control rather than here — a
    // stored policy can narrow one without the other, and a screen that demanded both to open
    // would hide the history from an admin who may still roll back.
    capability: ["scorer.read", "scorer.write"], component: Scorer,
    summary: "The formula that decides which alarms group. Preview before you apply.",
  },
  {
    id: "governance", label: "Governance", glyph: "⛨", group: "administer",
    // BOTH reads, because the screen issues both on mount. Declaring only `rbac.read` would let a
    // principal narrowed to it alone mount the screen and then request `/api/scope`, which the
    // appliance refuses — a client leaking a 403 into every such operator's access log.
    capability: ["rbac.read", "scope.read"], component: Governance,
    summary: "Who may do what, and who may see which network elements.",
  },
  {
    id: "quarantine", label: "Quarantine", glyph: "⚑", group: "administer",
    capability: "quarantine.read", component: Quarantine,
    summary: "Datagrams the parser refused. Reading this list is audited.",
  },
  {
    id: "audit", label: "Audit log", glyph: "⛓", group: "administer",
    capability: "audit.read", component: Audit,
    summary: "The hash-chained record of every change, and its verification state.",
  },

  /* ---------- reachable by address, not offered in navigation ---------- */
  {
    id: "account", label: "Your account", glyph: "◐", group: null, hidden: true,
    capability: "self.read", component: Account,
    summary: "Change your own password.",
  },
];

export const DEFAULT_VIEW = "overview";
