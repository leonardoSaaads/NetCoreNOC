/* The ten assets with the most active alarms, and which way each is heading (v0.23.0, #393).
 *
 * Ranked by the alarms active NOW in the severities chosen — Critical, then Critical + Major, then
 * down to everything — because "the worst ten" depends on what worst means and an operator changes
 * that as a situation develops. Each row carries its count (a link to the element's timeline),
 * the severity mix of what it has active (a bar, with each band's count in its title), and a trend:
 * the same count at each point of the range above, so the number is the last point of its own line.
 *
 * Counted in SQL over the caller's scope (`/api/activity/top`); nothing is ranked in the browser.
 */

import { html, Component, cx } from "../../dom.js";
import { get } from "../../api.js";
import { SectionHeading, SeverityMix } from "../../widgets.js";
import { Trend } from "../../stack.js";
import { SCALE, UNPLACED, count, plural } from "../../format.js";

export const TIERS = [
  { key: "c", label: "Critical", bands: "critical" },
  { key: "cm", label: "+ Major", bands: "critical,major" },
  { key: "cmm", label: "+ Minor", bands: "critical,major,minor" },
  { key: "all", label: "All", bands: "critical,major,minor,warning,indeterminate,unplaced,vendor" },
];

const MIX = [...SCALE, UNPLACED];

export class TopAssets extends Component {
  constructor(props) {
    super(props);
    this.state = { tier: "cm", data: null, error: null };
  }

  componentDidMount() { this.read(); }

  componentDidUpdate(previous) {
    if (previous.rangeS !== this.props.rangeS || previous.tick !== this.props.tick) this.read();
  }

  async read() {
    const tier = TIERS.find((t) => t.key === this.state.tier) ?? TIERS[1];
    try {
      const data = await get(`/api/activity/top?range_s=${this.props.rangeS}&buckets=24` +
        `&limit=10&bands=${tier.bands}`);
      this.setState({ data, error: null });
    } catch (error) {
      this.setState({ error });
    }
  }

  pick(key) { this.setState({ tier: key }, () => this.read()); }

  render({ rangeS }, { tier, data, error }) {
    const rows = (data && data.top) || [];
    const win = rangeS <= 86400 ? rangeS : 86400;
    return html`<section class="panel-block topassets">
      <${SectionHeading} title="Assets with the most active alarms" />
      <div class="seg" role="group" aria-label="Which severities count">
        ${TIERS.map((t) => html`<button type="button" key=${t.key}
            class=${cx("seg-btn", tier === t.key && "on")} aria-pressed=${tier === t.key ? "true" : "false"}
            onClick=${() => this.pick(t.key)}>${t.label}</button>`)}
      </div>
      ${error ? html`<p class="hint">Could not read the ranking: ${error.message}</p>` : null}
      ${!data && !error ? html`<p class="muted">Reading…</p>` : null}
      ${data && !rows.length ? html`<p class="muted">No element has an alarm of these severities active.</p>` : null}
      ${rows.length ? html`<ol class="toplist">${rows.map((r, i) => {
          return html`<li key=${r.ne_id} class="toprow">
            <span class="toprank">${i + 1}</span>
            <a class="topname" href=${`#/graph?ne=${r.ne_id}`} title="Open on the graph">${r.device}</a>
            <${SeverityMix} bands=${MIX} counts=${r.now} />
            <${Trend} values=${r.trend} label=${`${r.device}: ${plural(r.count, "alarm")} active now`} />
            <a class="topcount" href=${`#/timeline?ne=${r.ne_id}&win=${win}`}
               title="Open its timeline">${count(r.count)}</a>
          </li>`;
        })}</ol>
        ${data.elements > rows.length
          ? html`<p class="muted topfoot">${count(data.elements - rows.length)} more elements with
              these severities active</p>` : null}` : null}
    </section>`;
  }
}
