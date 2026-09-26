/* The network graph (v0.22.0, #383; items 10, 11, 12).
 *
 * ## What changed
 *
 * The d3 force scene re-laid out between glances, clamped nodes to its box edges, set 11 px labels
 * over each other, did nothing when clicked, and ran against a recording double in the harness —
 * asserted by nothing, and served 279 706 bytes to do it. It is replaced by `app/netgraph.js`:
 * hand-written, deterministic (positions follow the topology, never the load), every element a
 * button, labels shown where they fit at the width the drawing has.
 *
 * Selecting an element — click, tap, or Enter — opens its panel (`parts/element.js`) and writes
 * `?ne=` into the address, so a selection is a link and the Overview's card can open one.
 *
 * ## What left the screen
 *
 * The paragraph explaining the encoding is behind the info icon. The accessibility statement is
 * **gone because it stopped being true**: the drawing is now keyboard-operable and every element
 * has an accessible name, which the group's own label says. The second projection and its
 * paragraph repeating the first's limitation became one table, *Elements by load*, whose only
 * job is the ranking the drawing cannot give exactly.
 */

import { html, Component } from "../dom.js";
import { Empty, DataTable, SectionHeading } from "../widgets.js";
import { count, plural, score } from "../format.js";
import * as store from "../store.js";
import { NetGraph } from "../netgraph.js";
import { InfoTip } from "../info.js";
import { ElementPanel } from "./parts/element.js";

/** Rows of the load table before "show every element". */
const TOP_N = 15;

function name(node) { return node.label || node.ip; }

export class GraphView extends Component {
  constructor(props) {
    super(props);
    this.state = { live: store.get(), all: false, focus: null, query: "", miss: false };
    this.onKey = this.onKey.bind(this);
  }

  componentDidMount() {
    this.unsubscribe = store.subscribe((live) => this.setState({ live: { ...live } }));
    globalThis.document.addEventListener("keydown", this.onKey);
  }

  componentWillUnmount() {
    if (this.unsubscribe) this.unsubscribe();
    globalThis.document.removeEventListener("keydown", this.onKey);
  }

  onKey(event) {
    if (event.key === "Escape" && this.selected() != null) this.select(null);
  }

  /** The selected element, read from the address: a selection is a link. */
  selected() {
    const raw = this.props.query && this.props.query.get("ne");
    return raw && /^\d+$/.test(raw) ? Number(raw) : null;
  }

  select(id) {
    const same = id === this.selected();
    this.props.navigate(id == null || same ? "#/graph" : `#/graph?ne=${id}`);
  }

  /** Select an element and bring it into view: the find box and the load table use this. */
  focus(id) {
    const seq = (this.state.focus ? this.state.focus.seq : 0) + 1;
    this.setState({ focus: { id, seq }, miss: false });
    if (id !== this.selected()) this.props.navigate(`#/graph?ne=${id}`);
    globalThis.document.querySelector(".graphview .netgraph")
      ?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }

  /** Find by address or name: exact first, then the first that contains the text. */
  find(nodes, text) {
    const q = text.trim().toLowerCase();
    if (!q) return;
    const label = (n) => String(n.label || "").toLowerCase();
    const hit = nodes.find((n) => String(n.ip).toLowerCase() === q || label(n) === q)
      || nodes.find((n) => String(n.ip).toLowerCase().includes(q) || label(n).includes(q));
    if (hit) this.focus(hit.id);
    else this.setState({ miss: true });
  }

  render(_props, { live, all }) {
    const graph = live.graph;
    if (!graph || !graph.nodes.length) {
      return html`<${Empty} title="No network elements yet."
        will="An element appears for each device the appliance hears from; a line joins two once
              their alarms have appeared together often enough." />`;
    }
    const selected = this.selected();
    const known = graph.nodes.some((node) => node.id === selected);
    const { focus, query, miss } = this.state;
    return html`<div class="graphview">
      <div class="graph-bar">
        <form class="graph-find" role="search" onSubmit=${(e) => { e.preventDefault(); this.find(graph.nodes, query); }}>
          <label class="visually-hidden" for="graph-find">Find an element</label>
          <input id="graph-find" type="search" list="graph-names" placeholder="Find IP or name"
                 value=${query} autocomplete="off"
                 onInput=${(e) => this.setState({ query: e.currentTarget.value, miss: false })} />
          <datalist id="graph-names">${graph.nodes.map((n) => html`<option key=${n.id}
            value=${n.label ? `${n.label}` : n.ip}>${n.label ? n.ip : ""}</option>`)}</datalist>
          <button type="submit" class="tap">Find</button>
          ${miss ? html`<span class="hint" role="status">No element matches.</span>` : null}
        </form>
        <span class="muted">${plural(graph.nodes.length, "element")}${" · "}${
          plural(graph.edges.length, "learned relationship")}</span>
        <${InfoTip} label="How to read the graph">
          A line is a learned affinity: how often two elements' alarms appeared together. It is
          thicker and darker the stronger it is. A dot grows with the element's active alarms and
          is ringed from ${URGENT_AT_TEXT}. Elements with no learned relationship sit in the band
          at the bottom. Scroll or pinch to zoom, drag to move, double-click to zoom in; + and −
          and 0 work on the keyboard.
        <//>
      </div>
      <div class=${known ? "graph-split" : "graph-solo"}>
        <${NetGraph} nodes=${graph.nodes} edges=${graph.edges} selected=${known ? selected : null}
                     focus=${focus} onSelect=${(id) => this.select(id)} />
        ${known
          ? html`<div class="elpanel-wrap"><${ElementPanel} neId=${selected}
              onClose=${() => this.select(null)} /></div>`
          : null}
      </div>
      <${Load} nodes=${graph.nodes} edges=${graph.edges} all=${all}
        onAll=${() => this.setState({ all: !all })} onSelect=${(id) => this.focus(id)} />
      <${Strongest} graph=${graph} />
    </div>`;
  }
}

const URGENT_AT_TEXT = "47 active alarms";

/**
 * **Elements by load** — the exact ranking the drawing only approximates (a dot stops growing),
 * with how many relationships each element has. One table, where two sections used to say the
 * same thing twice (item 12).
 */
function Load({ nodes, edges, all, onAll, onSelect }) {
  const degree = new Map();
  for (const e of edges) {
    degree.set(e.a_id, (degree.get(e.a_id) || 0) + 1);
    degree.set(e.b_id, (degree.get(e.b_id) || 0) + 1);
  }
  const ranked = [...nodes].sort((a, b) => b.active_alarms - a.active_alarms
    || String(a.id).localeCompare(String(b.id), "en", { numeric: true }));
  const peak = Math.max(1, ranked[0] ? ranked[0].active_alarms : 1);
  const rows = (all ? ranked : ranked.slice(0, TOP_N)).map((node) => ({
    key: node.id,
    tone: node.active_alarms ? "alarm" : null,
    cells: {
      element: html`<button type="button" class="cell-edit" onClick=${() => onSelect(node.id)}
        >${name(node)}</button>`,
      load: html`<span class="load-bar"><span class="load-fill"
        style=${`width:${((node.active_alarms / peak) * 100).toFixed(1)}%`}></span></span>`,
      alarms: count(node.active_alarms),
      links: count(degree.get(node.id) || 0),
    },
  }));
  return html`<section class="panel-block">
    <${SectionHeading} title="Elements by load" />
    <${DataTable} caption="Every element, busiest first; the name opens it on the graph."
      columns=${[
        { key: "element", label: "element" },
        { key: "load", label: "" },
        { key: "alarms", label: "active alarms", numeric: true },
        { key: "links", label: "relationships", numeric: true },
      ]} rows=${rows} />
    ${ranked.length > TOP_N
      ? html`<button type="button" class="tap" onClick=${onAll}>
          ${all ? `Show the busiest ${TOP_N}` : `Show all ${count(ranked.length)}`}</button>`
      : null}
  </section>`;
}

/** The learned relationships as numbers, with the evidence behind each. */
function Strongest({ graph }) {
  const named = new Map(graph.nodes.map((node) => [node.id, name(node)]));
  const rows = [...graph.edges]
    .filter((edge) => named.has(edge.a_id) && named.has(edge.b_id))
    .sort((a, b) => b.weight - a.weight || b.n - a.n)
    .slice(0, 10);
  if (!rows.length) return null;
  return html`<section class="panel-block">
    <${SectionHeading} title="Strongest learned relationships"
      hint=${"Evidence is how many times the pair was observed together. A pair seen a handful " +
             "of times can already score highly, and that is a weaker claim than the same score " +
             "over hundreds."} />
    <${DataTable} columns=${[
      { key: "pair", label: "pair" },
      { key: "weight", label: "affinity", numeric: true },
      { key: "n", label: "evidence (n)", numeric: true },
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
