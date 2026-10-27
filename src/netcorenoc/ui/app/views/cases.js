/* NOC → Cases: the queue the NOC works from, and one case opened at a time (ADRs #459-#461).
 *
 * The queue reads like a case management system's: what nobody has taken yet first, most urgent
 * first, each with its take clock counting — and the same table for what is being worked, what
 * waits on someone else, and what is over. A case opens full width, because it is where the work
 * is: the customer update, the feed, the clocks.
 *
 * Read again every fifteen seconds, without the loading state, so the clocks and the queue move
 * while the screen is open. Every act is the server's to allow (`actions` on each case).
 */

import { html, cx } from "../dom.js";
import { get } from "../api.js";
import { Loader, Empty } from "../widgets.js";
import { relative, timeTitle } from "../format.js";
import { Clock, NextUpdate, Priority } from "./parts/caseclock.js";
import { CaseDetail } from "./parts/casedetail.js";

const REFRESH_MS = 15000;
const TABS = [
  ["queue", "Queue", ["new", "auto", "queue"]],
  ["working", "Working", ["working", "validate"]],
  ["pending", "Pending", null],
  ["resolved", "Resolved", ["resolved", "closed"]],
  ["all", "All", undefined],
];

export class Cases extends Loader {
  constructor(props) {
    super(props);
    this.what = "the cases";
    this.loadingLabel = "Reading the queue";
    this.state = { ...this.state, now: Date.now() / 1000 };
  }

  tab() {
    const asked = this.props.query && this.props.query.get("view");
    return TABS.some(([id]) => id === asked) ? asked : "queue";
  }

  mine() { return Boolean(this.props.query && this.props.query.get("mine") === "1"); }

  selected() { return Number((this.props.params || [])[0]) || null; }

  routeKey(props) {
    const q = props.query;
    return `${q && q.get("view")}|${q && q.get("mine")}`;
  }

  async load() {
    const mine = this.mine() ? "&mine=true" : "";
    return get(`/api/noc/cases?view=${this.tab()}${mine}`);
  }

  async refresh() {
    try {
      this.setState({ data: await this.load(), now: Date.now() / 1000 });
    } catch { /* the rows stay as they were; the next tick reads again */ }
  }

  componentDidMount() {
    super.componentDidMount();
    this.timer = setInterval(() => this.refresh(), REFRESH_MS);
    this.tick = setInterval(() => this.setState({ now: Date.now() / 1000 }), 1000);
  }

  componentWillUnmount() { clearInterval(this.timer); clearInterval(this.tick); }

  go(view, mine = this.mine()) {
    this.props.navigate(`#/cases?view=${view}${mine ? "&mine=1" : ""}`);
  }

  open(id) {
    const back = `view=${this.tab()}${this.mine() ? "&mine=1" : ""}`;
    this.props.navigate(id == null ? `#/cases?${back}` : `#/cases/${id}?${back}`);
  }

  count(data, statuses) {
    const counts = data.counts || {};
    const keys = statuses === null ? data.pending : statuses || Object.keys(counts);
    return keys.reduce((n, k) => n + (counts[k] || 0), 0);
  }

  view(data) {
    const sid = this.selected();
    if (sid) {
      return html`<${CaseDetail} key=${sid} id=${sid} catalogue=${data}
        onBack=${() => this.open(null)} onChanged=${() => this.refresh()} />`;
    }
    const tab = this.tab();
    return html`<div class="cases">
      <div class="cases-bar">
        <div class="access-tabs" role="tablist" aria-label="Cases">
          ${TABS.map(([id, label, statuses]) => html`<button type="button" role="tab" key=${id}
            aria-selected=${tab === id} class=${cx("access-tab", tab === id && "on")}
            onClick=${() => this.go(id)}>${label}${statuses !== undefined
              ? html` <b class="case-count">${this.count(data, statuses)}</b>` : null}</button>`)}
        </div>
        <label class="cases-mine"><input type="checkbox" checked=${this.mine()}
          onChange=${(e) => this.go(tab, e.target.checked)} /> Only mine</label>
      </div>
      ${this.table(data)}
    </div>`;
  }

  table(data) {
    const { now } = this.state;
    if (!data.cases.length) {
      return html`<${Empty} title="Nothing here"
        will="A situation the Broker opens arrives in the queue with its take clock running." />`;
    }
    return html`<div class="table-scroll"><table class="data cases-table">
      <thead><tr>
        <th scope="col">Priority</th><th scope="col">Case</th><th scope="col">Status</th>
        <th scope="col">Owner</th><th scope="col">Take</th><th scope="col" class="wide-only">Update</th>
        <th scope="col" class="wide-only">Next update</th><th scope="col" class="wide-only">Opened</th>
      </tr></thead>
      <tbody>${data.cases.map((c) => html`<tr key=${c.id}>
        <td><${Priority} priority=${c.priority} label=${c.priority_label} /></td>
        <td><button type="button" class="linkish case-open" onClick=${() => this.open(c.id)}>
          <span class="mono">#${c.id}</span> ${c.title || c.situation.name || "Untitled"}</button></td>
        <td><span class=${cx("case-status", `case-${c.super_state}`)}>${c.status_label}</span></td>
        <td>${c.owner ? c.owner.name : html`<span class="muted">nobody</span>`}</td>
        <td><${Clock} clock=${c.clocks && c.clocks.take} now=${now} label="Take" /></td>
        <td class="wide-only"><${Clock} clock=${c.clocks && c.clocks.document} now=${now}
          label="Customer update" /></td>
        <td class="wide-only"><${NextUpdate} cadence=${c.cadence} now=${now} /></td>
        <td class="wide-only" title=${timeTitle(c.opened_at)}>${relative(c.opened_at)}</td>
      </tr>`)}</tbody>
    </table></div>`;
  }
}
