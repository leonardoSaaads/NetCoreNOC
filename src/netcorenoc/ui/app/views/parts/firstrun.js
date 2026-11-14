/* The Overview before the first trap: three pictures of what happens next (ADR #483).
 *
 * A first-time operator lands here with nothing to look at. Three panels say, mostly in pictures,
 * what to do and what follows: (1) point the equipment's traps at this appliance, (2) related alarms
 * become a situation — confirm it and press *Start working this* to open a NOC case, (3) the NOC
 * works the case to the end. The first panel carries the one thing to type (the trap destination,
 * copyable) and a live "waiting" mark; the screen turns into the real Overview by itself the moment
 * a trap arrives, because the Overview re-reads the live counters.
 *
 * The pictures are inline SVG drawn in `currentColor` and the theme's accent, so they follow light
 * and dark; their motion (the traps flowing in) stops under `prefers-reduced-motion`.
 */

import { html, Component } from "../../dom.js";
import { Icon } from "../../icons.js";
import { can } from "../../session.js";

function Devices() {
  const rows = [["Router", 18], ["Switch", 52], ["OLT", 86], ["Firewall", 120]];
  return html`<svg class="fr-art" viewBox="0 0 300 150" role="img"
    aria-label="Four devices sending SNMP traps to NetCoreNOC">
    ${rows.map(([name, y], i) => html`<g key=${name}>
      <rect x="8" y=${y - 12} width="78" height="24" rx="6" class="fr-node" />
      <circle cx="20" cy=${y} r="3" class="fr-led" style=${`animation-delay:${i * 0.4}s`} />
      <text x="30" y=${y + 4} class="fr-label">${name}</text>
      <path d=${`M88 ${y} C 140 ${y}, 150 75, 196 75`} class="fr-flow" style=${`animation-delay:${i * 0.35}s`} />
    </g>`)}
    <rect x="196" y="47" width="96" height="56" rx="12" class="fr-core" />
    <text x="244" y="72" class="fr-core-name" text-anchor="middle">NetCoreNOC</text>
    <text x="244" y="89" class="fr-label" text-anchor="middle">UDP 162</text>
  </svg>`;
}

function Grouping() {
  return html`<svg class="fr-art" viewBox="0 0 300 150" role="img"
    aria-label="Three alarms grouped into one situation, which becomes a NOC case">
    ${[["LOS", 34, "fr-crit"], ["Link down", 68, "fr-major"], ["BGP down", 102, "fr-minor"]].map(([name, y, tone]) => html`
      <g key=${name}><rect x="6" y=${y - 11} width="74" height="22" rx="11" class=${`fr-pill ${tone}`} />
      <text x="43" y=${y + 4} class="fr-label" text-anchor="middle">${name}</text>
      <path d=${`M82 ${y} C 94 ${y}, 96 70, 104 70`} class="fr-link" /></g>`)}
    <rect x="104" y="38" width="106" height="66" rx="10" class="fr-node fr-card" />
    <text x="114" y="56" class="fr-strong">Situation</text>
    <text x="114" y="71" class="fr-label">3 alarms · 1 cause</text>
    <rect x="114" y="80" width="56" height="16" rx="5" class="fr-btn" />
    <text x="142" y="91.5" class="fr-btn-text" text-anchor="middle">Confirm</text>
    <path d="M212 71 H 228" class="fr-arrow" marker-end="url(#fr-head)" />
    <defs><marker id="fr-head" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
      <path d="M0 0 8 4 0 8z" class="fr-arrowhead" /></marker></defs>
    <rect x="230" y="38" width="64" height="66" rx="10" class="fr-core" />
    <circle cx="262" cy="62" r="11" class="fr-dot" />
    <path d="M256 64v-2a6 6 0 0 1 12 0v2M255 64h2v4h-2zM267 64h2v4h-2z" class="fr-glyph" />
    <text x="262" y="91" class="fr-label" text-anchor="middle">NOC case</text>
  </svg>`;
}

function Done() {
  return html`<svg class="fr-art" viewBox="0 0 300 150" role="img"
    aria-label="The NOC works the case until it is resolved">
    <circle cx="150" cy="72" r="46" class="fr-ring" />
    <circle cx="150" cy="72" r="32" class="fr-core" />
    <path d="M136 72l10 10 18-20" class="fr-check" />
    ${[[78, 40, "AI"], [222, 40, "AI"], [78, 108, "You"], [222, 108, "Team"]].map(([x, y, who]) => html`
      <g key=${`${x}-${y}`}><circle cx=${x} cy=${y} r="16" class="fr-node" />
      <text x=${x} y=${y + 4} class="fr-label" text-anchor="middle">${who}</text>
      <path d=${`M${x + (x < 150 ? 17 : -17)} ${y} L ${x < 150 ? 108 : 192} ${y < 72 ? 58 : 86}`} class="fr-link" /></g>`)}
    <text x="150" y="140" class="fr-label" text-anchor="middle">customer updated · case resolved</text>
  </svg>`;
}

const STEPS = [
  { art: Devices, title: "Point your equipment here",
    text: "Set each device's SNMP trap destination to this appliance. No MIBs, no inventory, no topology files." },
  { art: Grouping, title: "Confirm the situation, open a case",
    text: "Related alarms become one situation with its probable cause. Confirm it, then press Start working this." },
  { art: Done, title: "Done — the NOC works it",
    text: "AI agents and your team work the case, keep the customer updated and resolve it." },
];

/** The trap destination, copyable. */
class Destination extends Component {
  constructor(props) {
    super(props);
    this.state = { copied: false };
  }

  async copy(text) {
    try { await globalThis.navigator.clipboard.writeText(text); this.setState({ copied: true }); } catch { /* selectable */ }
  }

  render(_props, { copied }) {
    const host = globalThis.location ? globalThis.location.hostname : "this-appliance";
    const text = `${host}:162`;
    return html`<div class="fr-dest">
      <span class="fr-dest-label">Trap destination</span>
      <code class="mono">${host}<span class="muted">:162/udp</span></code>
      <button type="button" class="btn-ghost" aria-label="Copy the trap destination" onClick=${() => this.copy(text)}>
        <${Icon} name=${copied ? "check" : "copy"} /></button>
    </div>`;
  }
}

export function FirstRun() {
  return html`<section class="first-run" aria-labelledby="fr-title">
    <header class="fr-head">
      <p class="fr-eyebrow"><span class="fr-live" aria-hidden="true"></span> Waiting for the first trap</p>
      <h2 id="fr-title">Your network, in three steps</h2>
    </header>
    <ol class="fr-steps">
      ${STEPS.map(({ art: Art, title, text }, i) => html`<li key=${title} class=${i === 0 ? "fr-step is-now" : "fr-step"}>
        <div class="fr-figure"><${Art} /></div>
        <div class="fr-body">
          <p class="fr-num"><span>${i + 1}</span>${i === 0 ? "Now" : i === 1 ? "Next" : "Then"}</p>
          <h3>${title}</h3>
          <p class="muted">${text}</p>
          ${i === 0 ? html`<${Destination} />` : null}
          ${i === 1 ? html`<a class="fr-cta" href="#/situations">Situations <${Icon} name="chevron" /></a>` : null}
          ${i === 2 && can("cases.read") ? html`<a class="fr-cta" href="#/noc">The NOC <${Icon} name="chevron" /></a>` : null}
        </div>
      </li>`)}
    </ol>
    <details class="fr-test">
      <summary>Send a test trap from a Linux machine</summary>
      <pre class="mono">snmptrap -v 2c -c public ${globalThis.location ? globalThis.location.hostname : "<appliance>"}:162 '' 1.3.6.1.6.3.1.1.5.3 ifIndex i 3</pre>
      <p class="muted">The screen changes by itself as soon as it arrives. Nothing arriving? Check Settings → SNMP (which sources and communities are accepted) and that UDP 162 reaches this host.</p>
    </details>
  </section>`;
}
