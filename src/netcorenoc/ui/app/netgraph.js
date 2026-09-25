/* The network graph, hand-written: one component for the Graph screen and the Overview card
 * (v0.22.0, #383, items 6 and 11).
 *
 * Geometry comes from `layout.js` and depends on the topology only, so nothing moves between
 * glances. **Every element is a `<button>`**: Tab walks the estate busiest first, Enter opens the
 * element, and each carries its name and load as its accessible name — the drawing is operable
 * without a pointer and readable without sight, which the d3 scene never was.
 *
 * Edges are one SVG layer beneath the buttons (thickness and opacity follow the learned affinity);
 * labels are HTML so they stay at the stylesheet's size, and a container query shows the ones
 * `layout.labels` found room for at the width the drawing actually has.
 */

import { html, Component, cx } from "./dom.js";
import { layout, labels } from "./layout.js";
import { plural } from "./format.js";

/** The load at which an element is urgent: pulsing ring, and it survives reduced motion as a ring. */
export const URGENT_AT = 47;

/** A node's radius in CSS pixels. Grows with load and stops at 15 — the count is in the name. */
export function radius(node) {
  return Math.min(15, 5 + 1.6 * Math.sqrt(node.active_alarms || 0));
}

function name(node) { return String(node.label || node.ip || node.id); }

/** Memoised on the topology, so a poll that changes only alarm counts costs no layout. */
const memo = { key: null, value: null };

function topologyKey(nodes, edges, aspect) {
  return `${aspect}|${nodes.map((n) => n.id).sort().join(",")}|${
    edges.map((e) => `${e.a_id}-${e.b_id}`).sort().join(",")}`;
}

export function placeAll(nodes, edges, aspect) {
  const key = topologyKey(nodes, edges, aspect);
  if (memo.key !== key) memo.value = { key, ...layout(nodes, edges, { aspect }) };
  memo.key = key;
  return memo.value;
}

export class NetGraph extends Component {
  constructor(props) {
    super(props);
    const narrow = globalThis.matchMedia?.("(max-width: 600px)")?.matches;
    this.aspect = props.compact ? (narrow ? 1.2 : 1.8) : (narrow ? 0.9 : 2.2);
  }

  render({ nodes, edges, selected, onSelect, compact, label }) {
    const known = new Set(nodes.map((n) => n.id));
    const drawn = edges.filter((e) => known.has(e.a_id) && known.has(e.b_id));
    const placed = placeAll(nodes, drawn, this.aspect).nodes;
    const shown = labels(nodes, placed, { aspect: this.aspect, radius });
    const degree = new Map();
    for (const e of drawn) {
      degree.set(e.a_id, (degree.get(e.a_id) || 0) + 1);
      degree.set(e.b_id, (degree.get(e.b_id) || 0) + 1);
    }
    const order = [...nodes].sort((a, b) => (b.active_alarms || 0) - (a.active_alarms || 0)
      || String(a.id).localeCompare(String(b.id), "en", { numeric: true }));
    return html`<div data-chart="graph" class=${cx("netgraph", compact && "netgraph-compact")}
         style=${`aspect-ratio:${this.aspect}`} role="group"
         aria-label=${label || `Network graph: ${plural(nodes.length, "element")}, ${
           plural(drawn.length, "learned relationship")}. Each element is a button.`}>
      <svg class="netgraph-edges" viewBox="0 0 100 100" preserveAspectRatio="none"
           aria-hidden="true" focusable="false">
        ${drawn.map((e) => {
          const a = placed[e.a_id];
          const b = placed[e.b_id];
          const lit = selected != null && (e.a_id === selected || e.b_id === selected);
          return html`<line key=${`${e.a_id}-${e.b_id}`} class=${cx("gedge", lit && "gedge-lit")}
            x1=${a.x} y1=${a.y} x2=${b.x} y2=${b.y}
            style=${`stroke-width:${(1 + 2.5 * e.weight).toFixed(2)}px;` +
                    `stroke-opacity:${(0.3 + 0.6 * e.weight).toFixed(2)}`} />`;
        })}
      </svg>
      ${order.map((node) => {
        const p = placed[node.id];
        const r = radius(node);
        const show = shown[node.id] || {};
        const alarms = node.active_alarms || 0;
        const links = degree.get(node.id) || 0;
        return html`<button type="button" key=${`n${node.id}`} data-ne=${node.id}
            class=${cx("gnode", alarms > 0 ? "gnode-alarm" : "gnode-quiet",
                       alarms >= URGENT_AT && "gnode-urgent", selected === node.id && "gnode-on")}
            style=${`left:${p.x}%;top:${p.y}%;--r:${r.toFixed(1)}px`}
            aria-pressed=${selected === node.id ? "true" : "false"}
            aria-label=${`${name(node)}: ${plural(alarms, "active alarm")}, ${
              plural(links, "learned relationship")}`}
            title=${`${name(node)} — ${plural(alarms, "active alarm")}`}
            onClick=${() => onSelect && onSelect(node.id)}></button>
          <span key=${`l${node.id}`} aria-hidden="true"
            class=${cx("glabel", `glabel-${show.side || "r"}`, show.s && "gl-s", show.m && "gl-m",
                       show.l && "gl-l", selected === node.id && "gl-on")}
            style=${`left:${p.x}%;top:${p.y}%;--r:${r.toFixed(1)}px`}>${name(node)}</span>`;
      })}
    </div>`;
  }
}
