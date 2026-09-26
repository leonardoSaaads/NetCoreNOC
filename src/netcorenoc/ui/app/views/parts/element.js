/* The element panel: what opens when an operator selects a host on the graph (v0.22.0 item 11;
 * redesigned v0.23.0, #394).
 *
 * It answers four things in the order they are asked — how bad, which incidents, what it just
 * sent, what next — from three scoped reads, each failing on its own line. v0.23.0 made it a
 * panel rather than a dump: a header that is the element, one number and its severity mix, the
 * LIVE situations (resolved ones are a count, not a list), the last day's traps by NAME, and the
 * actions in a footer. The panel is the map's height and scrolls inside itself, so the two stay
 * aligned whatever the element carries.
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
      get(`/api/situations?ne_id=${id}&limit=30`),
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
    const live = (situations || []).filter((s) => s.status !== "resolved");
    const resolved = (situations || []).length - live.length;
    return html`<aside class="elpanel" aria-label="Selected element" data-ne=${neId}>
      <header class="elp-head">
        <div class="elp-id">
          <h2 class="elp-name">${element ? element.device : "…"}</h2>
          ${element ? html`<p class="elp-meta">
            ${element.label ? html`<code>${element.ip}</code>` : null}
            <span>${element.organization || ""}</span>
            ${element.maintenance ? html`<${MaintenanceMark} marker=${element.maintenance} />` : null}
          </p>` : null}
        </div>
        <button type="button" class="elp-close" aria-label="Close" title="Close (Esc)"
                onClick=${onClose}>×</button>
      </header>
      ${errors.element ? html`<p class="hint">Could not read this element.</p>` : null}
      ${element ? html`<div class="elp-kpi">
          <div class="elp-total"><b>${count(element.active_alarms)}</b><span>active alarms</span></div>
          <ul class="elp-bands" aria-label="Active alarms by severity">
            ${bands.length
              ? bands.map(([b, n]) => html`<li key=${b.key}><${SeverityChip} band=${b} count=${n} /></li>`)
              : html`<li class="muted">None active.</li>`}
          </ul>
        </div>` : null}

      <section class="elp-sec">
        <h3>Situations${situations ? html` <span class="elp-n">${count(live.length)}</span>` : null}</h3>
        ${errors.situations ? html`<p class="hint">Could not read its situations.</p>` : null}
        ${live.length
          ? html`<ul class="elp-list">${live.slice(0, 6).map((s) => html`<li key=${s.id}>
              <a class="elp-row" href=${`#/situations/${s.id}`}>
                <span class="elp-sid">#${s.id}</span>
                <span class="elp-body">
                  <span class="elp-text">${s.operator_name || s.derived_name || "unnamed"}</span>
                  <span class="elp-sub"><${Badge} tone=${s.status === "new" ? "warn" : null}>${s.status}<//>
                    ${" "}${plural(s.alarm_count, "alarm")}</span>
                </span>
              </a></li>`)}</ul>`
          : situations ? html`<p class="muted">None live.</p>` : null}
        ${resolved ? html`<p class="muted elp-foot">${plural(resolved, "resolved situation")} recently</p>` : null}
      </section>

      <section class="elp-sec">
        <h3>Last 24 hours</h3>
        ${errors.recent ? html`<p class="hint">Could not read its recent traps.</p>` : null}
        ${recent && recent.groups.length
          ? html`<ul class="elp-list">${recent.groups.map((g) => html`<li
                key=${`${g.class_id}-${g.kind}-${g.last}`} class="elp-row">
              <span class=${`kind kind-${g.kind}`} title=${g.kind}>
                <span aria-hidden="true">${g.kind === "clear" ? "✓" : "▲"}</span>
                <span class="visually-hidden">${g.kind}</span></span>
              <span class="elp-body">
                <span class="elp-text" title=${g.class_oid}>${g.class_named ? g.class : g.class_oid}</span>
                <span class="elp-sub"><span title=${timeTitle(g.last)}>${relative(g.last)}</span>
                  ${repeats(g) ? ` · ${repeats(g)}` : ""}</span>
              </span>
            </li>`)}</ul>`
          : recent ? html`<p class="muted">Nothing raised or cleared.</p>` : null}
      </section>

      <nav class="elp-acts" aria-label="Open this element in">
        <a class="tap" href=${`#/timeline?ne=${neId}&win=86400`}>Timeline</a>
        <a class="tap" href=${`#/entities?ne=${neId}`}>Details</a>
      </nav>
    </aside>`;
  }
}
