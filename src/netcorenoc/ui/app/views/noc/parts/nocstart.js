/* The NOC before it can work: three pictures of what to set up, in order (ADR #490).
 *
 * The Broker's Overview greets a new appliance with three panels (ADR #483); the NOC's does the
 * same, in place of the checklist card it had: (1) **a customer** — who the NOC serves, its
 * contacts and instructions, and in its Access the device credentials the agents may use; (2) **the
 * agents** — an AI provider, then an agent (or a team of them) with one entry point; (3) **work the
 * cases**. Each panel says done or to do in words and a mark, never by colour alone, and the first
 * one left to do is the highlighted one. The panels disappear when every step is done or the
 * first case reaches the NOC (ADR #491).
 *
 * The facts are the overview's own `setup` block (`engine/noc/overview.setup`); the pictures are
 * inline SVG in the console's classes (`.fr-*`), so they follow light and dark.
 */

import { html, cx } from "../../../dom.js";
import { Icon } from "../../../icons.js";

function CustomerArt() {
  return html`<svg class="fr-art" viewBox="0 0 300 150" role="img"
    aria-label="A customer's book: its name, contacts, instructions and device access">
    <rect x="40" y="18" width="150" height="114" rx="12" class="fr-node fr-card" />
    <rect x="54" y="32" width="26" height="26" rx="6" class="fr-core" />
    <path d="M61 52V40h12v12M64 44h2M68 44h2M64 48h2M68 48h2" class="fr-glyph" />
    <text x="88" y="44" class="fr-strong">Acme Telecom</text>
    <text x="88" y="56" class="fr-label">customer</text>
    ${[["Contacts", 76], ["Instructions", 94], ["Circuits", 112]].map(([word, y]) => html`<g key=${word}>
      <circle cx="60" cy=${y - 3} r="3" class="fr-dot" /><text x="70" y=${y} class="fr-label">${word}</text>
      <rect x="128" y=${y - 7} width="48" height="6" rx="3" class="fr-bar" /></g>`)}
    <path d="M192 75 H 214" class="fr-arrow" marker-end="url(#ns-head)" />
    <defs><marker id="ns-head" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
      <path d="M0 0 8 4 0 8z" class="fr-arrowhead" /></marker></defs>
    <rect x="216" y="50" width="70" height="50" rx="10" class="fr-core" />
    <circle cx="238" cy="75" r="7" class="fr-key" /><path d="M245 75 H 266 M260 75 v6 M266 75 v4" class="fr-key-line" />
    <text x="251" y="94" class="fr-label" text-anchor="middle">Access</text>
  </svg>`;
}

function AgentsArt() {
  return html`<svg class="fr-art" viewBox="0 0 300 150" role="img"
    aria-label="An AI provider powering a team of agents, tier 1 and tier 2">
    <rect x="10" y="52" width="74" height="46" rx="10" class="fr-core" />
    <text x="47" y="72" class="fr-core-name" text-anchor="middle">AI</text>
    <text x="47" y="87" class="fr-label" text-anchor="middle">provider</text>
    <path d="M86 75 C 110 75, 110 40, 132 40 M86 75 H 132 M86 75 C 110 75, 110 110, 132 110" class="fr-flow" />
    ${[[40, "Tier 1", true], [75, "Tier 1", false], [110, "Tier 2", false]].map(([y, tier, entry]) => html`<g key=${y}>
      <rect x="134" y=${y - 13} width="104" height="26" rx="13" class=${entry ? "fr-node fr-entry" : "fr-node"} />
      <circle cx="150" cy=${y} r="8" class="fr-dot" />
      <path d=${`M146 ${y - 1}h8M147 ${y + 3}h6`} class="fr-glyph" />
      <text x="164" y=${y + 4} class="fr-label">${tier}${entry ? " · entry" : ""}</text></g>`)}
    <text x="258" y="79" class="fr-label" text-anchor="middle">a team</text>
  </svg>`;
}

function WorkArt() {
  return html`<svg class="fr-art" viewBox="0 0 300 150" role="img"
    aria-label="Cases moving from the queue to working to resolved">
    ${[["Queue", 20, ""], ["Working", 112, ""], ["Resolved", 204, "fr-done"]].map(([word, x, tone], i) => html`<g key=${word}>
      <rect x=${x} y="52" width="78" height="46" rx="10" class=${cx("fr-node", tone)} />
      <text x=${x + 39} y="72" class="fr-strong" text-anchor="middle">${word}</text>
      <text x=${x + 39} y="87" class="fr-label" text-anchor="middle">${["3 cases", "2 cases", "today 9"][i]}</text>
      ${i < 2 ? html`<path d=${`M${x + 80} 75 H ${x + 90}`} class="fr-arrow" marker-end="url(#ns-head2)" />` : null}</g>`)}
    <defs><marker id="ns-head2" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
      <path d="M0 0 8 4 0 8z" class="fr-arrowhead" /></marker></defs>
    <circle cx="243" cy="118" r="11" class="fr-core" /><path d="M237 118l4 4 8-8" class="fr-check" />
  </svg>`;
}

/** One line of a step: done or to do, in a word and a mark. */
function Check({ done, children }) {
  return html`<li class=${cx("ns-check", done && "done")}>
    <span class="ns-mark" aria-hidden="true"><${Icon} name=${done ? "check" : "info"} /></span>
    <span>${children}${done ? html` <span class="visually-hidden">— done</span>` : null}</span>
  </li>`;
}

export function NocStart({ s }) {
  // ADR #491, #502: the panels are what to do after the deploy, and the whole of the overview
  // until the first case reaches the NOC — set up or not, as the Broker's before its first trap.
  if (!s || s.first_case) return null;
  const agents = s.ai_provider && s.agent && s.entry;
  const customer = s.customer && s.device_access;
  const steps = [
    { key: "customer", done: customer, art: CustomerArt, title: "Add your first customer",
      text: "Who the NOC serves: its name, contacts and instructions — what the agents read before they act.",
      checks: [[s.customer, "A customer, with its basic information"],
        [s.device_access, "In its Access, a device credential the agents may use"]],
      cta: ["#/customers", "Customers"] },
    { key: "agents", done: agents, art: AgentsArt, title: "Set up the agents",
      text: "Connect an AI provider — a local Ollama needs no key — then hire an agent, or a team of them.",
      checks: [[s.ai_provider, "An AI provider"], [s.agent, "An agent hired"], [s.entry, "One agent is the entry point"]],
      cta: ["#/agents?tab=providers", "Agents"] },
    { key: "work", done: false, art: WorkArt, title: "All set — work the cases",
      text: "A case opens when someone presses Start working this on an open situation; the agents take it from the queue.",
      checks: [], cta: ["#/cases", "Cases"] },
  ];
  const now = steps.findIndex((step) => !step.done);
  return html`<section class="first-run noc-start" aria-labelledby="ns-title">
    <header class="fr-head">
      <p class="fr-eyebrow"><span class="fr-live" aria-hidden="true"></span> ${s.ready
        ? "Ready — waiting for the first case" : "Setting up the NOC"}</p>
      <h2 id="ns-title">Get the NOC working, in three steps</h2>
    </header>
    <ol class="fr-steps">
      ${steps.map(({ key, done, art: Art, title, text, checks, cta }, i) => html`<li key=${key}
          class=${cx("fr-step", i === now && "is-now", done && "is-done")}>
        <div class="fr-figure"><${Art} /></div>
        <div class="fr-body">
          <p class="fr-num"><span>${done ? html`<${Icon} name="check" />` : i + 1}</span>${done ? "Done" : i === now ? "Now" : "Then"}</p>
          <h3>${title}</h3>
          <p class="muted">${text}</p>
          ${checks.length ? html`<ul class="ns-checks">${checks.map(([ok, words]) => html`<${Check} key=${words} done=${ok}>${words}<//>`)}</ul>` : null}
          <a class="fr-cta" href=${cta[0]}>${cta[1]} <${Icon} name="chevron" /></a>
        </div>
      </li>`)}
    </ol>
  </section>`;
}
