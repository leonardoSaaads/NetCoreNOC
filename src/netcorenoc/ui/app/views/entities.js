/* Entities: every network element, its state, and what it is made of (v0.23.0, #395).
 *
 * The screen was a list of addresses with "1 entity" beside each — accurate, and useless: nothing
 * said which element was in trouble, which organization it belonged to, what vendor it was, or why
 * it had 186 "entities". It is rebuilt around what an operator asks of an inventory:
 *
 *   1. **the estate at a glance** — six counts, each of which is also the filter for itself;
 *   2. **find one** — search by address, name or vendor, narrow by organization, sort;
 *   3. **each element in one row** — its load and severity mix, its live situations, how many
 *      components (ports, ONUs, slots) the appliance learned beneath it, and when it last spoke;
 *   4. **one element opened** — its last day, its busiest components, where to go next, and the
 *      identification evidence behind one disclosure (`parts/nedetail.js`).
 *
 * One read (`/api/inventory`), counted and scoped in SQL. `?ne=` opens an element, so the graph's
 * panel can link here.
 */

import { html, Component, cx } from "../dom.js";
import { get } from "../api.js";
import { Empty, Loading, Failed, DataTable, SeverityMix } from "../widgets.js";
import { MaintenanceMark } from "./parts/mwmarker.js";
import { ElementDetail } from "./parts/nedetail.js";
import { SCALE, UNPLACED, count, plural, relative, timeTitle } from "../format.js";

const MIX = [...SCALE, UNPLACED];

const TILES = [
  ["all", "elements", (t) => t.elements],
  ["alarming", "with active alarms", (t) => t.alarming],
  ["critical", "with critical", (t) => t.critical],
  ["silent", "silent for 24 h", (t) => t.silent],
  ["maintenance", "in maintenance", (t) => t.maintenance],
];

const KEEP = {
  all: () => true,
  alarming: (r) => r.active > 0,
  critical: (r) => r.bands.critical > 0,
  silent: (r) => r.silent,
  maintenance: (r) => r.maintenance,
};

const SORTS = {
  load: ["most alarms", (a, b) => b.active - a.active || b.bands.critical - a.bands.critical],
  critical: ["most critical", (a, b) => b.bands.critical - a.bands.critical || b.active - a.active],
  name: ["name", (a, b) => a.device.localeCompare(b.device, "en", { numeric: true })],
  recent: ["last trap", (a, b) => (b.last_seen || 0) - (a.last_seen || 0)],
  parts: ["most components", (a, b) => b.components - a.components],
};

export class Entities extends Component {
  constructor(props) {
    super(props);
    this.state = { data: null, error: null, q: "", org: "", show: "all", sort: "load", open: null };
  }

  componentDidMount() {
    this.read();
    this.follow();
  }

  /** `?ne=` opens that element; the view is kept when only the address's query changes. */
  componentDidUpdate(previous) {
    if (String(previous.query || "") !== String(this.props.query || "")) this.follow();
    if (this.state.scroll && this.state.data) {
      this.setState({ scroll: false });
      const row = globalThis.document.querySelector(".inv-item.open");
      if (row) row.scrollIntoView({ block: "start", behavior: "smooth" });
    }
  }

  follow() {
    const ne = this.props.query && this.props.query.get("ne");
    if (ne && /^\d+$/.test(ne)) this.setState({ open: Number(ne), show: "all", q: "", org: "", scroll: true });
  }

  async read() {
    try { this.setState({ data: await get("/api/inventory"), error: null }); }
    catch (error) { this.setState({ error }); }
  }

  render(_props, { data, error, q, org, show, sort, open }) {
    if (error) return html`<${Failed} error=${error} retry=${() => this.read()} what="the inventory" />`;
    if (!data) return html`<${Loading} label="Reading the estate" />`;
    if (!data.elements.length) {
      return html`<${Empty} title="No network elements yet."
        will="One row appears for every device the appliance hears a trap from; nothing has to be imported." />`;
    }
    const t = data.totals;
    const orgs = [...new Map(data.elements.filter((e) => e.organization_id != null)
      .map((e) => [e.organization_id, e.organization])).entries()];
    const needle = q.trim().toLowerCase();
    const rows = data.elements
      .filter(KEEP[show])
      .filter((e) => !org || String(e.organization_id) === org)
      .filter((e) => !needle || [e.device, e.ip, e.vendor, e.organization]
        .some((v) => v && String(v).toLowerCase().includes(needle)))
      .sort(SORTS[sort][1]);
    return html`<div class="inventory">
      <div class="inv-tiles" role="group" aria-label="The estate, and filters">
        ${TILES.map(([key, word, pick]) => html`<button type="button" key=${key}
            class=${cx("inv-tile", `inv-${key}`, show === key && "on")} aria-pressed=${show === key}
            onClick=${() => this.setState({ show: show === key ? "all" : key })}>
          <b>${count(pick(t))}</b><span>${word}</span></button>`)}
        <div class="inv-tile inv-static"><b>${count(t.components)}</b><span>components learned</span></div>
      </div>
      <div class="inv-bar">
        <input type="search" placeholder="Search address, name or vendor" aria-label="Search elements"
               value=${q} onInput=${(e) => this.setState({ q: e.currentTarget.value })} />
        ${orgs.length > 1 ? html`<select aria-label="Organization" value=${org}
            onChange=${(e) => this.setState({ org: e.currentTarget.value })}>
            <option value="">every organization</option>
            ${orgs.map(([id, name]) => html`<option key=${id} value=${String(id)}>${name}</option>`)}
          </select>` : null}
        <label class="inv-sort">Sort <select value=${sort} onChange=${(e) => this.setState({ sort: e.currentTarget.value })}>
          ${Object.entries(SORTS).map(([k, [label]]) => html`<option key=${k} value=${k}>${label}</option>`)}
        </select></label>
        <span class="muted">${plural(rows.length, "element")}</span>
      </div>
      ${rows.length ? html`<ul class="inv-list">${rows.map((e) => html`<li key=${e.ne_id}
          class=${cx("inv-item", open === e.ne_id && "open")}>
        <${Row} e=${e} open=${open === e.ne_id}
          onToggle=${() => this.setState({ open: open === e.ne_id ? null : e.ne_id })} />
        ${open === e.ne_id ? html`<${ElementDetail} neId=${e.ne_id} />` : null}
      </li>`)}</ul>` : html`<p class="muted">No element matches.</p>`}
      <${Clears} />
    </div>`;
  }
}

function Row({ e, open, onToggle }) {
  return html`<button type="button" class="inv-row" aria-expanded=${open ? "true" : "false"}
      onClick=${onToggle}>
    <span class="inv-name">
      <b>${e.device}</b>
      ${e.label ? html`<code>${e.ip}</code>` : null}
      <span class="inv-sub">${[e.organization, e.vendor].filter(Boolean).join(" · ") || "—"}</span>
    </span>
    <span class="inv-load">
      <b>${count(e.active)}</b>
      <${SeverityMix} bands=${MIX} counts=${e.bands} />
    </span>
    <span class="inv-fact"><b class=${e.situations ? null : "inv-zero"}>${count(e.situations)}</b><span>situations</span></span>
    <span class="inv-fact"><b class=${e.components ? null : "inv-zero"}>${count(e.components)}</b><span>components</span></span>
    <span class="inv-fact" title=${timeTitle(e.last_seen)}>
      <b class=${e.silent ? "inv-silent" : null}>${relative(e.last_seen)}</b><span>last trap</span></span>
    <span class="inv-mw">${e.maintenance ? html`<${MaintenanceMark} marker=${e.maintenance} />` : null}</span>
  </button>`;
}

/** The learned clear fields: per trap class, which varbind value means "cleared". Reference data. */
class Clears extends Component {
  async toggle(event) {
    if (!event.currentTarget.open || this.state.rows) return;
    try { this.setState({ rows: await get("/api/state-clears") }); }
    catch { this.setState({ rows: [] }); }
  }

  render(_props, { rows }) {
    return html`<details class="inv-clears" onToggle=${(e) => this.toggle(e)}>
      <summary>Learned clear fields — which varbind value tells the appliance a trap has cleared</summary>
      ${rows ? html`<${DataTable} kind="compact" columns=${[
          { key: "class", label: "trap" }, { key: "oid", label: "varbind OID" },
          { key: "raise", label: "raise value" }, { key: "clear", label: "clear value" },
        ]} rows=${rows.map((s, i) => ({ key: `${s.class}-${i}`, cells: {
          class: s.class, oid: html`<code>${s.varbind_oid}</code>`,
          raise: s.raise_value, clear: s.clear_value } }))}
        empty=${html`<p class="muted">None learned yet.</p>`} />` : null}
    </details>`;
  }
}
