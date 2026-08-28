/* The network graph — **the one screen in this console that no test executes.**
 *
 * ## Say this plainly, because a green suite would otherwise imply otherwise
 *
 * d3 is kept (Part III, closing draft §12.2): replacing it means writing a force layout inside
 * the release that rewrites everything else. Two costs are real and are recorded rather than
 * absorbed:
 *
 *   * **279 706 bytes serving one view** — twenty-two times the two framework assets combined,
 *     and this is the only screen that uses it.
 *   * **The harness does not execute it.** `tests/domharness/env.mjs` substitutes d3 with a
 *     recording double, so every line below that touches `d3.*` runs against a proxy that
 *     records the call and returns another proxy. Nothing about node placement, edge rendering,
 *     zoom, drag or the simulation is asserted by any test in this repository.
 *
 * What the double DOES buy: it throws on any d3 API it has not been told about, so the day this
 * file reaches for a new one the harness says so instead of silently returning undefined. That is
 * a drift alarm, not coverage, and calling it coverage would be the exact dishonesty Part XI
 * warns about.
 *
 * The escaping property still holds here, and for the same reason as everywhere else: every
 * operator-supplied string reaches the document through `.text()`, which is `textContent`
 * underneath, never through markup.
 *
 * The accessibility limit below used to be rendered to the operator with a citation —
 * `docs/gates/v0.13.0-phase-6.md` — which v0.15.0 deleted. `record.md`'s reading rule resolves such
 * a path for someone holding the repository; it cannot help someone holding a screen (F71). The
 * citation is here now, where a reader can use it, and the sentence on screen says what an
 * operator can do instead.
 */

import { html, Component } from "../dom.js";
import { Empty } from "../widgets.js";
import { plural } from "../format.js";
import { canEdit } from "../session.js";
import { post } from "../api.js";
import * as store from "../store.js";
import { d3Ready } from "../vendor.js";

const NODE_BASE_RADIUS = 7;
/* **The radius is capped at the collision radius** (F77, v0.15.2).
 *
 * `r = 7 + 2.5 * sqrt(active_alarms)` had no ceiling, so a device carrying a storm grew without
 * bound. Measured against a live appliance on a 1172x460 canvas: radii of 12, 12, **62.96** and
 * **80.70** px — two of the four nodes larger than `forceCollide(26)`'s own radius by 2.4x and
 * 3.1x, and three of the four pushed off the canvas entirely. An operator opening the screen whose
 * whole purpose is the relationships between elements saw one circle and nothing else.
 *
 * Saturating just under the collision radius keeps the encoding monotone where it can be honest
 * and stops it where the layout stops agreeing with it. Nothing is lost: the exact count is in the
 * node's `<title>` and on the Entities screen as text, which is the pairing this console uses
 * everywhere a drawing carries a number. */
const NODE_MAX_RADIUS = 24;
const LINK_CAP = 30;

export class GraphView extends Component {
  constructor(props) {
    super(props);
    this.state = { live: store.get() };
    this.svgRef = null;
    this.built = false;
  }

  componentDidMount() {
    this.unsubscribe = store.subscribe((live) => {
      this.setState({ live: { ...live } });
      this.draw();
    });
    // d3 arrives with this screen rather than with the console (DECISIONS #228). `draw()` already
    // returned early when `globalThis.d3` was absent, so the only change is that the moment it
    // becomes present is now here.
    d3Ready().then(() => this.draw());
  }

  componentWillUnmount() {
    if (this.unsubscribe) this.unsubscribe();
    if (this.sim) this.sim.stop();
  }

  /** Build the d3 scene once, then update it. Everything below is unexecuted by any test. */
  draw() {
    const graph = this.state.live.graph;
    if (!graph || !this.svgRef) return;
    const d3 = globalThis.d3;
    if (!d3) return;

    if (!this.built) {
      this.built = true;
      this.svg = d3.select("#graph");
      this.zoomLayer = this.svg.append("g");
      this.edgeLayer = this.zoomLayer.append("g");
      this.nodeLayer = this.zoomLayer.append("g");
      this.labelLayer = this.zoomLayer.append("g");
      this.svg.call(d3.zoom().scaleExtent([0.3, 4])
        .on("zoom", (event) => this.zoomLayer.attr("transform", event.transform)));
      this.nodesById = new Map();
      this.sim = d3.forceSimulation()
        .force("charge", d3.forceManyBody().strength(-220))
        .force("link", d3.forceLink().id((d) => d.id).distance(90)
          .strength((l) => 0.2 + 0.5 * l.weight))
        .force("collide", d3.forceCollide(26))
        // **A centring force, added in v0.15.2 (F77).** There was none: charge repels at -220,
        // link pulls only where an edge exists, collide only pushes apart, and nothing at all
        // pulled toward the middle — so an unlinked or weakly linked node drifted outward until it
        // left the box, and the SVG has no `viewBox` to scale it back in. Measured against a live
        // appliance: **three of four nodes outside the canvas**, on the one screen whose entire
        // purpose is the relationships between elements, and the one screen no test executes.
        .force("centre", d3.forceCenter())
        .on("tick", () => this.tick());
    }
    // Re-centred on every draw rather than once at build: the panel is a grid cell, so its width
    // changes with the window and with the sidebar, and a centre fixed at first paint is wrong
    // from the first resize.
    const box = this.svgRef.getBoundingClientRect();
    this.sim.force("centre").x((box.width || 640) / 2).y((box.height || 460) / 2);

    for (const raw of graph.nodes) {
      const existing = this.nodesById.get(raw.id);
      if (existing) Object.assign(existing, raw);
      else this.nodesById.set(raw.id, { ...raw });
    }
    const links = graph.edges
      .map((e) => ({ source: e.a_id, target: e.b_id, weight: e.weight, n: e.n }))
      .filter((l) => this.nodesById.has(l.source) && this.nodesById.has(l.target));
    const nodes = [...this.nodesById.values()];

    const selection = this.nodeLayer.selectAll("circle").data(nodes, (d) => d.id)
      .join((enter) => enter.append("circle")
        .call(globalThis.d3.drag()
          .on("start", (_e, d) => { this.sim.alphaTarget(0.2).restart(); d.fx = d.x; d.fy = d.y; })
          .on("drag", (e, d) => { d.fx = e.x; d.fy = e.y; })
          .on("end", (_e, d) => { this.sim.alphaTarget(0); d.fx = null; d.fy = null; }))
        .on("dblclick", (_e, d) => { if (canEdit()) this.rename(d); }));
    selection
      .attr("r", (d) => Math.min(NODE_MAX_RADIUS, NODE_BASE_RADIUS + 2.5 * Math.sqrt(d.active_alarms)))
      .attr("class", (d) => `node ${d.active_alarms > 0 ? "alarm" : "ok"}`);
    selection.selectAll("title").remove();
    selection.append("title").text((d) =>
      `${displayName(d)}\n${d.vendor || "unknown vendor"}\n${plural(d.active_alarms, "active alarm")}`
      + (canEdit() ? "\ndouble-click to rename" : ""));

    this.labelLayer.selectAll("text").data(nodes, (d) => d.id)
      .join("text").attr("class", "node-label").text(displayName);

    this.edgeLayer.selectAll("line")
      .data(links.slice(0, LINK_CAP * 10), (d) => `${d.source.id ?? d.source}-${d.target.id ?? d.target}`)
      .join("line").attr("class", "edge")
      .attr("stroke-opacity", (d) => 0.25 + 0.6 * d.weight)
      .attr("stroke-width", (d) => 1 + 2 * d.weight)
      .selectAll("title").data((d) => [d]).join("title")
      .text((d) => `affinity ${d.weight.toFixed(2)} (n=${d.n.toFixed(1)})`);

    this.sim.nodes(nodes);
    this.sim.force("link").links(links);
    this.sim.alpha(0.6).restart();
  }

  /**
   * Keep every node inside the box, on every tick (F77).
   *
   * A centring force moves the centre of mass; it does not constrain anything, and on a panel that
   * is 1 172 x 460 — wide and short — charge at -220 still pushes nodes past the short edge. So the
   * property is asserted rather than hoped for: a node's position is clamped to the box, inset by
   * its own radius so the circle is whole rather than half-drawn at the edge. Zoom and drag are
   * unaffected: zoom transforms the layer and drag writes `fx`/`fy`, both of which this reads
   * after the simulation has resolved them.
   */
  clamp(d) {
    const box = this.svgRef ? this.svgRef.getBoundingClientRect() : null;
    const width = (box && box.width) || 640;
    const height = (box && box.height) || 460;
    const r = Math.min(NODE_MAX_RADIUS, NODE_BASE_RADIUS + 2.5 * Math.sqrt(d.active_alarms));
    d.x = Math.max(r, Math.min(width - r, d.x));
    d.y = Math.max(r, Math.min(height - r, d.y));
    return d;
  }

  tick() {
    for (const node of this.nodesById.values()) this.clamp(node);
    this.nodeLayer.selectAll("circle").attr("cx", (d) => d.x).attr("cy", (d) => d.y);
    this.labelLayer.selectAll("text").attr("x", (d) => d.x + 12).attr("y", (d) => d.y + 4);
    this.edgeLayer.selectAll("line")
      .attr("x1", (d) => d.source.x).attr("y1", (d) => d.source.y)
      .attr("x2", (d) => d.target.x).attr("y2", (d) => d.target.y);
  }

  async rename(node) {
    const label = globalThis.prompt("Rename device (cosmetic label):", displayName(node));
    if (label === null || !label.trim()) return;
    try { await post("/api/labels", { kind: "device", id: node.id, label: label.trim() }); }
    catch (error) { globalThis.alert(`Rename failed — ${error.detail || error.message}`); }
  }

  render(_props, { live }) {
    const graph = live.graph;
    const empty = !graph || !graph.nodes.length;
    return html`<div class="graphview">
      <p class="hint">Every edge is a learned affinity between two network elements: how often
        their alarms have appeared together. Nothing here was configured — the topology is
        inferred from the trap stream. Opacity and thickness both encode the weight; a node's size
        follows its active alarm count and stops growing at ${NODE_MAX_RADIUS} px, so one busy
        device cannot hide the rest. Hover a node for its exact count.</p>
      ${empty ? html`<${Empty}
          title="No network elements yet."
          will=${"A node appears for each device the appliance hears from, and an edge appears " +
                 "once two devices' alarms have co-occurred often enough to be worth drawing."}
          meanwhile=${"Send a trap. The first node appears immediately; edges need correlated " +
                      "activity across at least two devices."} />` : null}
      <div id="graphwrap" class=${empty ? "hidden-visually" : ""}>
        <svg id="graph" role="img"
             aria-label=${graph
               ? `Network affinity graph: ${plural(graph.nodes.length, "device")}, ${plural(graph.edges.length, "learned edge")}. A text equivalent is on the Entities screen.`
               : "Network affinity graph, empty"}
             ref=${(node) => { this.svgRef = node; }}></svg>
        <div class="legend">
          <span><i class="alarm"></i>alarming device</span>
          <span><i class="ok"></i>quiet device</span>
          <span><i class="edge"></i>learned edge (opacity = affinity)</span>
        </div>
      </div>
      ${!empty ? html`<p class="hint">
        <b>This drawing is not keyboard-operable and has no screen-reader equivalent beyond its
        label.</b> Everything it shows is also on the <a href="#/entities">Entities</a> screen as
        text, which is where an operator who cannot use it should go. Saying so is deliberate: a
        limit an operator can read is one they can work around.</p>` : null}
    </div>`;
  }
}

function displayName(node) { return node.label || node.ip; }
