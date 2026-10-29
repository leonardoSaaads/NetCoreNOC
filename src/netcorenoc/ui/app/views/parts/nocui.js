/* The NOC plane's shared marks (ADR #468): an agent's glyph, its state, a run's, a tier, money.
 *
 * Paperclip draws each agent as an icon on a coloured tile, and the colour is the role's — a
 * supervisor reads as a supervisor across the org chart, the sidebar and the agent's own page. The
 * tile is decoration: the role and the name are always written beside it. A state is a dot AND a
 * word, never the colour alone (the severity rule).
 */

import { html, cx } from "../../dom.js";
import { Icon } from "../../icons.js";

/** What an agent may wear (`engine/noc/agents.py` ICONS) -> the mark `icons.js` draws for it. */
export const AGENT_ICONS = {
  bot: "bot", crown: "crown", shield: "shield", router: "router", network: "network",
  wrench: "wrench", search: "search", chart: "chart", headset: "headset", globe: "globe",
  cpu: "cpu", bolt: "bolt",
};

export function agentIcon(name) { return AGENT_ICONS[name] || AGENT_ICONS.bot; }

/** An agent's mark on its role's tile. `size`: "sm" (lists), "md" (cards), "lg" (its page). */
export function AgentGlyph({ agent, size = "md" }) {
  return html`<span class=${cx("agent-glyph", `role-${agent.role}`, `glyph-${size}`)}>
    <${Icon} name=${agentIcon(agent.icon)} />
  </span>`;
}

/** idle / running / paused / error / terminated, as a dot and its word. */
export function AgentState({ status, label }) {
  return html`<span class=${cx("agent-state", `st-${status}`)}>
    <span class="agent-dot" aria-hidden="true"></span>${label || status}
  </span>`;
}

const RUN_TONE = { queued: "wait", running: "live", succeeded: "ok", failed: "bad", timed_out: "bad",
  cancelled: "off" };

export function RunState({ status, label }) {
  return html`<span class=${cx("run-state", `run-${RUN_TONE[status] || "off"}`)}>${label || status}</span>`;
}

export function Tier({ tier }) {
  return html`<span class="tier-chip" title="The NOC tier this agent works at (1 to 4)">Tier ${tier}</span>`;
}

/** US cents -> "$0.42". */
export function money(cents) {
  const value = Number(cents) || 0;
  return `$${(value / 100).toFixed(value > 0 && value < 1 ? 4 : 2)}`;
}

/** The sidebar's line for each agent, under *Agents* (see `sidebar.js` on extras). */
export function navEntries(agents) {
  return (agents || []).map((a) => ({
    key: `agent-${a.id}`,
    href: `#/agents/${a.id}`,
    label: a.name,
    icon: agentIcon(a.icon),
    tone: `role-${a.role}`,
    state: a.status_label,
    dot: a.status,
    live: a.status === "running",
    badge: a.open_cases,
    noun: "open cases",
  }));
}

/** The four parts of a customer's book, in the order the sidebar and its page show them. */
export const BOOK = [["instructions", "Instructions", "book"], ["workbook", "Workbook", "phone"],
  ["docs", "Additional docs", "doc"], ["circuits", "Circuits", "circuit"]];

/** The sidebar's line for each customer, under *Customers*, and under the one open its book's
 * four parts (ADR #473). */
export function customerEntries(customers, activeHref) {
  const out = [];
  for (const c of customers || []) {
    const href = `#/customers/${c.id}`;
    out.push({ key: `customer-${c.id}`, href, label: c.name, icon: "building",
      badge: c.open_cases, noun: "open cases" });
    if (activeHref === href || String(activeHref || "").startsWith(`${href}/`)) {
      for (const [part, label, icon] of BOOK) {
        out.push({ key: `customer-${c.id}-${part}`, href: `${href}/${part}`, label, icon, deep: true });
      }
    }
  }
  return out;
}
