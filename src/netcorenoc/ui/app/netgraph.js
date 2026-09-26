/* The network graph, hand-written: one component for the Graph screen and the Overview card
 * (v0.22.0, #383; zoom and focus v0.23.0, #394).
 *
 * Geometry comes from `layout.js` and depends on the topology only, so nothing moves between
 * glances. **Every element is a `<button>`**: Tab walks the estate busiest first, Enter opens the
 * element, and each carries its name and load as its accessible name.
 *
 * ## Zoom spreads the estate; it does not enlarge it (v0.23.0)
 *
 * v0.22.0 had no zoom, so a crowded corner stayed crowded and an element in it could not be
 * reached. The view is a centre and a factor, and every node's position is recomputed from them —
 * nodes and labels keep their pixel size and type, and zooming opens the space between them, which
 * is what "zoom in to see it" means on a topology. Wheel (around the pointer), drag, pinch,
 * double-click, `+`/`-`/`0` and the arrow keys all move it; `focus` centres and zooms onto one
 * element (the find box and the load table use it). From 2.5x every label shows.
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

const MIN_K = 1;
const MAX_K = 12;
const FOCUS_K = 3;
const HOME = { k: 1, cx: 50, cy: 50 };

function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

export class NetGraph extends Component {
  constructor(props) {
    super(props);
    const narrow = globalThis.matchMedia?.("(max-width: 600px)")?.matches;
    this.aspect = props.compact ? (narrow ? 1.2 : 1.8) : (narrow ? 0.9 : 2.2);
    this.state = { view: HOME };
    this.pointers = new Map();
    this.dragged = false;
    this.box = null;
  }

  componentDidUpdate(previous) {
    const { focus } = this.props;
    if (focus && (!previous.focus || focus.seq !== previous.focus.seq)) this.centre(focus.id);
  }

  /** Centre on one element and zoom in enough to separate it from its neighbours. */
  centre(id) {
    const p = this.placed && this.placed[id];
    if (!p) return;
    const k = Math.max(this.state.view.k, FOCUS_K);
    this.setState({ view: this.bounded({ k, cx: p.x, cy: p.y }) });
  }

  bounded({ k, cx, cy }) {
    const kk = clamp(k, MIN_K, MAX_K);
    const half = 50 / kk;
    return { k: kk, cx: clamp(cx, half, 100 - half), cy: clamp(cy, half, 100 - half) };
  }

  /** Zoom by `factor` keeping the stage point under screen percentage (px, py) where it is. */
  zoomAt(factor, px = 50, py = 50) {
    const { k, cx, cy } = this.state.view;
    const next = clamp(k * factor, MIN_K, MAX_K);
    const sx = (px - 50) / k + cx;
    const sy = (py - 50) / k + cy;
    this.setState({ view: this.bounded({ k: next, cx: sx - (px - 50) / next, cy: sy - (py - 50) / next }) });
  }

  pan(dxPct, dyPct) {
    const { k, cx, cy } = this.state.view;
    this.setState({ view: this.bounded({ k, cx: cx - dxPct / k, cy: cy - dyPct / k }) });
  }

  percent(event) {
    const r = this.box.getBoundingClientRect();
    return [((event.clientX - r.left) / r.width) * 100, ((event.clientY - r.top) / r.height) * 100];
  }

  onWheel(event) {
    event.preventDefault();
    const [px, py] = this.percent(event);
    this.zoomAt(event.deltaY < 0 ? 1.25 : 0.8, px, py);
  }

  onDown(event) {
    if (event.button != null && event.button !== 0) return;
    this.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    this.dragged = false;
    this.start = { view: this.state.view, spread: this.spread() };
  }

  spread() {
    const [a, b] = [...this.pointers.values()];
    return a && b ? Math.hypot(a.x - b.x, a.y - b.y) : 0;
  }

  onMove(event) {
    const last = this.pointers.get(event.pointerId);
    if (!last || !this.box) return;
    const r = this.box.getBoundingClientRect();
    if (this.pointers.size === 2) {
      this.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      const now = this.spread();
      if (this.start.spread > 0 && now > 0) {
        const k = clamp(this.start.view.k * (now / this.start.spread), MIN_K, MAX_K);
        this.setState({ view: this.bounded({ ...this.state.view, k }) });
        this.dragged = true;
      }
      return;
    }
    const dx = event.clientX - last.x;
    const dy = event.clientY - last.y;
    if (!this.dragged && Math.hypot(dx, dy) < 4) return;
    // Captured only once it is a drag, so a plain click still lands on the element it was aimed at;
    // from here the drag follows the pointer outside the graph.
    if (!this.dragged) this.box.setPointerCapture?.(event.pointerId);
    this.dragged = true;
    this.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    this.pan((dx / r.width) * 100, (dy / r.height) * 100);
  }

  onUp(event) {
    this.pointers.delete(event.pointerId);
    if (!this.pointers.size) {
      // The click that ends a drag fires right after this; clear the flag once it has passed.
      setTimeout(() => { this.dragged = false; }, 0);
    }
  }

  onKey(event) {
    const moves = { ArrowLeft: [8, 0], ArrowRight: [-8, 0], ArrowUp: [0, 8], ArrowDown: [0, -8] };
    if (event.key === "+" || event.key === "=") this.zoomAt(1.4);
    else if (event.key === "-") this.zoomAt(1 / 1.4);
    else if (event.key === "0") this.setState({ view: HOME });
    else if (moves[event.key] && event.target === event.currentTarget) this.pan(...moves[event.key]);
    else return;
    event.preventDefault();
  }

  pick(id) {
    if (this.dragged) return; // the end of a drag is not a click
    if (this.props.onSelect) this.props.onSelect(id);
  }

  render({ nodes, edges, selected, compact, label }, { view }) {
    const known = new Set(nodes.map((n) => n.id));
    const drawn = edges.filter((e) => known.has(e.a_id) && known.has(e.b_id));
    const placed = placeAll(nodes, drawn, this.aspect).nodes;
    this.placed = placed;
    const shown = labels(nodes, placed, { aspect: this.aspect, radius });
    const { k, cx: vx, cy: vy } = compact ? HOME : view;
    const at = (p) => ({ x: (p.x - vx) * k + 50, y: (p.y - vy) * k + 50 });
    const inside = (s) => s.x > -6 && s.x < 106 && s.y > -6 && s.y < 106;
    const degree = new Map();
    for (const e of drawn) {
      degree.set(e.a_id, (degree.get(e.a_id) || 0) + 1);
      degree.set(e.b_id, (degree.get(e.b_id) || 0) + 1);
    }
    const order = [...nodes].sort((a, b) => (b.active_alarms || 0) - (a.active_alarms || 0)
      || String(a.id).localeCompare(String(b.id), "en", { numeric: true }));
    const zoomed = k >= 2.5;
    return html`<div data-chart="graph"
         class=${cx("netgraph", compact && "netgraph-compact", zoomed && "netgraph-zoomed",
                    !compact && (k > 1 ? "netgraph-pan" : "netgraph-home"))}
         style=${`aspect-ratio:${this.aspect}`} role="group" tabIndex=${compact ? null : 0}
         ref=${(node) => { this.box = node; }}
         aria-label=${label || `Network graph: ${plural(nodes.length, "element")}, ${
           plural(drawn.length, "learned relationship")}. Each element is a button.` +
           (compact ? "" : " Zoom with + and -, move with the arrow keys, 0 to reset.")}
         onWheel=${compact ? null : (e) => this.onWheel(e)}
         onPointerDown=${compact ? null : (e) => this.onDown(e)}
         onPointerMove=${compact ? null : (e) => this.onMove(e)}
         onPointerUp=${compact ? null : (e) => this.onUp(e)}
         onPointerCancel=${compact ? null : (e) => this.onUp(e)}
         onDblClick=${compact ? null : (e) => { const [px, py] = this.percent(e); this.zoomAt(2, px, py); }}
         onKeyDown=${compact ? null : (e) => this.onKey(e)}>
      <svg class="netgraph-edges" viewBox="0 0 100 100" preserveAspectRatio="none"
           aria-hidden="true" focusable="false">
        ${drawn.map((e) => {
          const a = at(placed[e.a_id]);
          const b = at(placed[e.b_id]);
          const lit = selected != null && (e.a_id === selected || e.b_id === selected);
          return html`<line key=${`${e.a_id}-${e.b_id}`} class=${cx("gedge", lit && "gedge-lit")}
            x1=${a.x} y1=${a.y} x2=${b.x} y2=${b.y}
            style=${`stroke-width:${(1 + 2.5 * e.weight).toFixed(2)}px;` +
                    `stroke-opacity:${(0.3 + 0.6 * e.weight).toFixed(2)}`} />`;
        })}
      </svg>
      ${order.map((node) => {
        const s = at(placed[node.id]);
        if (!inside(s)) return null;
        const r = radius(node);
        const show = shown[node.id] || {};
        const alarms = node.active_alarms || 0;
        const links = degree.get(node.id) || 0;
        const pos = `left:${+s.x.toFixed(3)}%;top:${+s.y.toFixed(3)}%;--r:${r.toFixed(1)}px`;
        return html`<button type="button" key=${`n${node.id}`} data-ne=${node.id}
            class=${cx("gnode", alarms > 0 ? "gnode-alarm" : "gnode-quiet",
                       alarms >= URGENT_AT && "gnode-urgent", selected === node.id && "gnode-on")}
            style=${pos} aria-pressed=${selected === node.id ? "true" : "false"}
            aria-label=${`${name(node)}: ${plural(alarms, "active alarm")}, ${
              plural(links, "learned relationship")}`}
            title=${`${name(node)} — ${plural(alarms, "active alarm")}`}
            onClick=${() => this.pick(node.id)}></button>
          <span key=${`l${node.id}`} aria-hidden="true"
            class=${cx("glabel", `glabel-${show.side || "r"}`, show.s && "gl-s", show.m && "gl-m",
                       show.l && "gl-l", selected === node.id && "gl-on")}
            style=${pos}>${name(node)}</span>`;
      })}
      ${compact ? null : html`<div class="netgraph-zoom" role="group" aria-label="Zoom">
        <button type="button" aria-label="Zoom in" onClick=${() => this.zoomAt(1.4)}
                onPointerDown=${(e) => e.stopPropagation()}>+</button>
        <button type="button" aria-label="Zoom out" onClick=${() => this.zoomAt(1 / 1.4)}
                onPointerDown=${(e) => e.stopPropagation()}>−</button>
        <button type="button" aria-label="Show the whole estate" title="Fit (0)"
                onClick=${() => this.setState({ view: HOME })}
                onPointerDown=${(e) => e.stopPropagation()}>⤢</button>
        <span class="netgraph-k" aria-live="polite">${k.toFixed(1)}×</span>
      </div>`}
    </div>`;
  }
}
