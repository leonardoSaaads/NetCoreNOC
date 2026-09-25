/* The element panel: what opens when an operator selects a host on the graph (v0.22.0, item 11).
 *
 * Clicking a host did nothing. Now it answers the four things an operator asks of one element —
 * how bad, which incidents, what it just sent, what to do next — from three scoped reads, each of
 * which fails on its own line rather than taking the panel down.
 */

import { html, Component } from "../../dom.js";
import { get } from "../../api.js";
import { SeverityChip, Badge } from "../../widgets.js";
import { SCALE, UNPLACED, count, plural, relative, timeTitle } from "../../format.js";
import { MaintenanceMark } from "./mwmarker.js";
import { repeats } from "./marks.js";

const DAY = 24 * 60 * 60;

export class ElementPanel extends Component {
  constructor(props) {
    super(props);
    this.state = { element: null, situations: null, recent: null, errors: {} };
  }

  componentDidMount() { this.read(); }

  componentDidUpdate(previous) {
    if (previous.neId !== this.props.neId) this.read();
  }

  async read() {
    const id = this.props.neId;
    this.setState({ element: null, situations: null, recent: null, errors: {} });
    const [element, situations, recent] = await Promise.allSettled([
      get(`/api/elements/${id}`),
      get(`/api/situations?ne_id=${id}&limit=8`),
      get(`/api/activity/groups?range_s=${DAY}&ne_id=${id}&limit=5`),
    ]);
    if (id !== this.props.neId) return;
    const pick = (r) => (r.status === "fulfilled" ? r.value : null);
    this.setState({
      element: pick(element),
      situations: pick(situations),
      recent: pick(recent),
      errors: {
        element: element.status === "rejected" ? element.reason : null,
        situations: situations.status === "rejected" ? situations.reason : null,
        recent: recent.status === "rejected" ? recent.reason : null,
      },
    });
  }

  render({ neId, onClose }, { element, situations, recent, errors }) {
    const census = element && element.severity;
    const bands = census
      ? [...SCALE.map((b) => [b, Number((census.placed || {})[String(b.rank)] || 0)]),
         [UNPLACED, Number(census.unplaced || 0)]].filter(([, n]) => n > 0)
      : [];
    return html`<aside class="elpanel" aria-label="Selected element" data-ne=${neId}>
      <div class="elpanel-head">
        <h2 class="elpanel-name">${element ? element.device : "…"}</h2>
        <button type="button" class="disclosure-close" aria-label="Close" title="Close (Esc)"
                onClick=${onClose}>×</button>
      </div>
      ${errors.element ? html`<p class="hint">Could not read this element.</p>` : null}
      ${element
        ? html`<p class="elpanel-sub">
            ${element.label ? html`<code class="mono">${element.ip}</code>${" · "}` : null}
            ${element.organization || null}
            ${element.maintenance
              ? html`${" "}<${MaintenanceMark} marker=${element.maintenance} />` : null}
          </p>
          <div class="elpanel-sev" aria-label="Active alarms by severity">
            ${bands.length
              ? bands.map(([b, n]) => html`<${SeverityChip} key=${b.key} band=${b} count=${n} />`)
              : html`<span class="muted">No active alarms.</span>`}
          </div>`
        : null}

      <h3 class="elpanel-title">Situations</h3>
      ${errors.situations ? html`<p class="hint">Could not read its situations.</p>` : null}
      ${situations && situations.length
        ? html`<ul class="mini-list">${situations.map((s) => html`<li key=${s.id}>
            <a href=${`#/situations/${s.id}`}>#${s.id}</a>
            <span class="elpanel-sname">${s.operator_name || s.derived_name || ""}</span>
            <${Badge} tone=${s.status === "new" ? "warn" : null}>${s.status}<//>
            <span class="muted">${plural(s.alarm_count, "alarm")}</span>
          </li>`)}</ul>`
        : situations ? html`<p class="muted">None live.</p>` : null}

      <h3 class="elpanel-title">Recent traps</h3>
      ${errors.recent ? html`<p class="hint">Could not read its recent traps.</p>` : null}
      ${recent && recent.groups.length
        ? html`<ul class="mini-list">${recent.groups.map((g) => html`<li
              key=${`${g.class_id}-${g.kind}-${g.last}`}>
            <span class="elpanel-trap">${g.class_named ? g.class : g.class_oid}</span>
            <span class=${`kind kind-${g.kind}`}>${g.kind}</span>
            <span class="muted" title=${timeTitle(g.last)}>${relative(g.last)}</span>
            <span class="muted">${repeats(g)}</span>
          </li>`)}</ul>`
        : recent ? html`<p class="muted">Nothing in the last 24 hours.</p>` : null}

      <div class="elpanel-acts">
        <a class="tap" href=${`#/timeline?ne=${neId}&win=86400`}>Timeline</a>
        <a class="tap" href="#/entities">Entities</a>
        ${element && element.active_alarms
          ? html`<span class="muted">${count(element.active_alarms)} active</span>` : null}
      </div>
    </aside>`;
  }
}
