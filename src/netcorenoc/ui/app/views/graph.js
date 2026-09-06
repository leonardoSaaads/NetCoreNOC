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
import { Empty, DataTable, SectionHeading } from "../widgets.js";
import { count, plural, score } from "../format.js";
import * as store from "../store.js";
import { d3Ready } from "../vendor.js";

/** How many rows each derived table shows. Enough to answer the question, short enough to read. */
const TOP_N = 10;

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
          .on("end", (_e, d) => { this.sim.alphaTarget(0); d.fx = null; d.fy = null; })));
    selection
      .attr("r", (d) => Math.min(NODE_MAX_RADIUS, NODE_BASE_RADIUS + 2.5 * Math.sqrt(d.active_alarms)))
      .attr("class", (d) => `node ${d.active_alarms > 0 ? "alarm" : "ok"}`);
    selection.selectAll("title").remove();
    // v0.16.4 (F105): the middle line read `unknown vendor` for every node ever drawn, because
    // `ne.vendor` has no writer. A tooltip that says the appliance could not identify a device,
    // when the truth is that nothing ever tried, is worse than one line shorter.
    selection.append("title").text((d) =>
      `${displayName(d)}\n${plural(d.active_alarms, "active alarm")}`);

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

  render(_props, { live }) {
    const graph = live.graph;
    const empty = !graph || !graph.nodes.length;
    return html`<div class="graphview">
      <p class="hint">An edge is a learned affinity — how often two elements' alarms appeared
        together. Opacity and thickness both encode it; node size follows active alarms and stops
        at ${NODE_MAX_RADIUS} px. Hover for exact counts.</p>
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
        <b>The drawing itself is not keyboard-operable and has no screen-reader equivalent beyond
        its label.</b> The two tables below carry what it encodes, as text. Everything about an
        element is on <a class="tap" href="#/entities">Entities</a>, and an element is named from
        the situation it appears in.</p>` : null}

      ${!empty ? html`<${Busiest} nodes=${graph.nodes} />` : null}
      ${!empty ? html`<${Strongest} graph=${graph} />` : null}
    </div>`;
  }
}

function displayName(node) { return node.label || node.ip; }

/**
 * Which elements are alarming most. **Derived from the payload this screen already had.**
 *
 * `/api/graph` has served `active_alarms` on every node since v0.13.0 and the drawing has encoded
 * it in a radius ever since — a radius that is *capped* at 24 px (F77), so the one screen that
 * answers "which host is worst" answered it in a quantity that saturates. The number was on the
 * wire and thrown away, which is exactly what v0.15.2 found on `/api/stats`: eleven keys served,
 * five rendered. **No new route was needed and none was added** (Part VII rule 2).
 *
 * **v0.16.3 removed the rename from this screen**, and that is a repair rather than a loss. It was
 * here because there was nowhere else — a `globalThis.prompt` on double-click, then a button in
 * this table — and it wrote `label(kind='device', target_id=node.id)` while the Entities screen
 * read the `ne` table, so the name an operator gave a host was invisible on the screen built to
 * describe that host. An element is now named where the operator already is: the row in a
 * situation's member table, the same place its class and its severity are declared. This screen
 * **reads** what was declared there, which is the propagation the release exists to build.
 *
 * **No new gesture is invented here** (Part VII rule 5): the graph shows learned affinities between
 * elements, and an assertion that two elements are unrelated is not in
 * `PREREGISTRATION-0.16.0.md` §2's registered map. The grouping an operator can correct is a
 * *situation*, so each row links to the search that finds this element's situations, where the
 * five registered gestures — and now the three declarations — live.
 */
export function Busiest({ nodes }) {
  const rows = [...nodes]
    .filter((node) => node.active_alarms > 0)
    .sort((a, b) => b.active_alarms - a.active_alarms || String(a.id).localeCompare(String(b.id)))
    .slice(0, TOP_N);
  if (!rows.length) return null;
  return html`<section class="panel-block">
    <${SectionHeading} title="Elements alarming most"
      hint=${"The exact counts the drawing can only approximate: a node's radius stops growing " +
             "at 24 px, so a storm and a busy hour look alike there and do not here."} />
    <${DataTable} columns=${[
      { key: "device", label: "element" },
      // v0.16.4 (F105, DECISIONS #292): the **vendor** column is gone. Nothing has ever written
      // `ne.vendor` — 25 rows, 0 vendors, after 2 252 alarms — and its tooltip, *"inferred from
      // the enterprise arc of the OID"*, described `alarm_class`, a different table. An operator
      // read `unknown` as "the appliance could not identify this device"; the truth was that
      // nothing ever tried.
      { key: "alarms", label: "active alarms", numeric: true },
      { key: "act", label: "" },
    ]} rows=${rows.map((node) => ({
      key: node.id,
      tone: "alarm",
      cells: {
        device: displayName(node),
        alarms: count(node.active_alarms),
        act: html`<a class="tap" href=${`#/situations?q=${encodeURIComponent(displayName(node))}`}
                     title="Find this element's situations, where it is named and its grouping corrected"
                  >situations</a>`,
      },
    }))} />
  </section>`;
}

/**
 * Which relationships are strongest — the maintainer's second question, in the words they used:
 * *"if a host's alarms always affect another, show that relationship."*
 *
 * `weight` is the learned affinity and `n` is the co-occurrence mass behind it, and **both were
 * already on every edge** the drawing renders as opacity and thickness. Two encodings of one
 * number, and no way to read the number.
 *
 * `n` is shown beside the weight rather than folded into it, and that is not decoration: a pair
 * seen six times can reach an affinity of 0.83 (F61, measured), so a strong-looking edge with a
 * small `n` is a claim from very little evidence. A table that printed the affinity alone would
 * present those two as the same fact.
 */
export function Strongest({ graph }) {
  const named = new Map(graph.nodes.map((node) => [node.id, displayName(node)]));
  const rows = [...graph.edges]
    .filter((edge) => named.has(edge.a_id) && named.has(edge.b_id))
    .sort((a, b) => b.weight - a.weight || b.n - a.n)
    .slice(0, TOP_N);
  if (!rows.length) return null;
  return html`<section class="panel-block">
    <${SectionHeading} title="Strongest learned relationships"
      hint=${"How often two elements' alarms have appeared together, as a number rather than as " +
             "an opacity. Evidence is the second column and it is not optional: a pair seen a " +
             "handful of times can already score highly, and that is a weaker claim than the " +
             "same score over hundreds of observations."} />
    <${DataTable} columns=${[
      { key: "pair", label: "pair" },
      { key: "weight", label: "affinity", numeric: true },
      { key: "n", label: "evidence (n)", numeric: true,
        title: "co-occurrence mass; an edge is not drawn at all below the learned minimum" },
    ]} rows=${rows.map((edge) => ({
      key: `${edge.a_id}-${edge.b_id}`,
      cells: {
        pair: `${named.get(edge.a_id)} ↔ ${named.get(edge.b_id)}`,
        weight: score(edge.weight),
        n: score(edge.n),
      },
    }))} />
  </section>`;
}
