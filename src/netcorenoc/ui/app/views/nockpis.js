/* NOC → KPIs: how fast the NOC takes and documents what reaches it (ADRs #460, #464, #468).
 *
 * Two clocks over a window, from every time a case entered the NOC queue: the take (critical 25
 * minutes, major one hour, minor two hours) and the customer update (30 minutes from the take).
 * A miss is reported with its overrun, because "missed" alone hides the difference between a
 * minute and a shift. Cases that ended before anyone took them (the situation cleared) are
 * withdrawn, and are not charged.
 */

import { html, cx } from "../dom.js";
import { get } from "../api.js";
import { Loader, Stat, SectionHeading } from "../widgets.js";
import { Bars } from "../compare.js";
import { span } from "./parts/caseclock.js";

const TONE = { critical: "alarm", major: "warn", minor: null };

const WINDOWS = [[1, "24 hours"], [7, "7 days"], [30, "30 days"]];
const pct = (v) => (v == null ? "—" : `${v}%`);

export class NocKpis extends Loader {
  constructor(props) {
    super(props);
    this.what = "the NOC's KPIs";
    this.loadingLabel = "Reading the clocks";
  }

  days() {
    const asked = Number(this.props.query && this.props.query.get("days"));
    return WINDOWS.some(([d]) => d === asked) ? asked : 7;
  }

  routeKey(props) { return String(props.query && props.query.get("days")); }

  async load() { return get(`/api/noc/cases/kpis?days=${this.days()}`); }

  view(k) {
    const days = this.days();
    const open = ["new", "auto", "queue"].reduce((n, s) => n + (k.counts[s] || 0), 0);
    return html`<div class="noc-kpis">
      <div class="noc-toolbar"><div class="seg" role="tablist" aria-label="Window">
        ${WINDOWS.map(([d, label]) => html`<button type="button" role="tab" key=${d}
          aria-selected=${d === days} class=${cx("seg-btn", d === days && "on")}
          onClick=${() => this.props.navigate(`#/kpis?days=${d}`)}>${label}</button>`)}
      </div></div>
      <div class="stat-row noc-stats">
        <${Stat} label="Waiting in the queue" value=${open} note="now" />
        <${Stat} label="Taken within target" value=${pct(k.take.met_pct)}
          note=${`${k.take.met} met · ${k.take.missed + k.take.late} late`}
          tone=${k.take.met_pct != null && k.take.met_pct < 90 ? "warn" : null} />
        <${Stat} label="Median time to take" value=${k.take.median_s == null ? "—" : span(k.take.median_s)} />
        <${Stat} label="Time past target" value=${span(k.take.overrun_s)}
          note="summed over every late take" tone=${k.take.overrun_s > 0 ? "warn" : null} />
        <${Stat} label="Customer update within 30 min" value=${pct(k.document.met_pct)}
          note=${`${k.document.met} met · ${k.document.missed + k.document.late} late`} />
      </div>
      <${SectionHeading} title="By priority"
        hint="Targets: take — critical 25 min, major 1 h, minor 2 h; restore service — 4 h, 24 h, 72 h." />
      <div class="kpi-split">
        <table class="data"><thead><tr><th scope="col">Priority</th><th scope="col">Take target</th>
          <th scope="col">Restore target</th><th scope="col">Met</th><th scope="col">Missed</th>
          <th scope="col">Waiting</th></tr></thead>
          <tbody>${Object.entries(k.by_priority).map(([p, row]) => html`<tr key=${p}>
            <td>${k.priorities ? k.priorities[p] : p}</td><td>${span(k.targets.take_s[p])}</td>
            <td>${span(k.targets.restore_s[p])}</td><td>${row.met}</td>
            <td>${row.missed}</td><td>${row.open}</td></tr>`)}</tbody></table>
        <${Bars} title="Takes missed, by priority" source="the NOC queue's entries"
          span=${WINDOWS.find(([d]) => d === days)[1]} note="nothing taken yet"
          rows=${Object.values(k.by_priority).some((r) => r.met + r.missed)
            ? Object.entries(k.by_priority).map(([p, row]) => ({ key: p,
              label: k.priorities ? k.priorities[p] : p, value: row.missed, tone: TONE[p] }))
            : []} />
      </div>
      <p class="hint">${`${k.cycles} entr${k.cycles === 1 ? "y" : "ies"} into the queue in the last ${
        WINDOWS.find(([d]) => d === days)[1]}; ${k.take.withdrawn} ended before anyone took them.`}</p>
    </div>`;
  }
}
