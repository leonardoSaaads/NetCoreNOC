/* The Overview's situation card: how many need someone, and the ones to open first (v0.23.0).
 *
 * It was a list of eight "#82 1 alarm 20.1h ago" rows at the bottom of the screen. It is the card
 * an operator acts from, so it sits beside the severity card now, and it answers in the order it is
 * read: how many are NEW (nobody has looked), how many are OPEN (being worked), and — one click
 * between the two — the six most recent of either, each with its name, size and age.
 *
 * The counts are `/api/stats`' (exact, scoped in SQL); the rows come from the live list, which is the
 * newest fifty, so a row list shorter than its count says "view all" rather than implying that is
 * everything.
 */

import { html, Component, cx } from "../../dom.js";
import { Badge } from "../../widgets.js";
import { count, plural, relative, timeTitle } from "../../format.js";

const SHOWN = 6;

export class SituationSummary extends Component {
  constructor(props) {
    super(props);
    this.state = { tab: null };
  }

  render({ stats, situations }, { tab: picked }) {
    const live = (situations || []).filter((s) => s.status !== "resolved");
    const counts = {
      new: Number(stats.new_situations ?? live.filter((s) => s.status === "new").length),
      open: Number(stats.working_situations ?? live.filter((s) => s.status === "open").length),
    };
    // Start on New when there is anything new, else on Open: the tab with work in it.
    const tab = picked ?? (counts.new || !counts.open ? "new" : "open");
    const rows = live
      .filter((s) => s.status === tab)
      .sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0))
      .slice(0, SHOWN);
    const tabs = [
      ["new", "New", "nobody has looked yet"],
      ["open", "Open", "being worked"],
    ];
    return html`<section class="panel-block sitsum">
      <div class="section-heading"><h3>Situations</h3>
        <a class="sitsum-all" href="#/situations">View all</a></div>
      <div class="sitsum-kpis" role="tablist" aria-label="Situations by state">
        ${tabs.map(([key, word, what]) => html`<button type="button" key=${key} role="tab"
            aria-selected=${tab === key ? "true" : "false"} title=${`${word}: ${what}`}
            class=${cx("sitsum-kpi", `sitsum-${key}`, tab === key && "on")}
            onClick=${() => this.setState({ tab: key })}>
          <b>${count(counts[key])}</b><span>${word}</span>
        </button>`)}
        <div class="sitsum-kpi sitsum-static" title="Active alarms on the whole estate, from the census">
          <b>${count(stats.active_alarms ?? 0)}</b><span>active alarms</span>
        </div>
      </div>
      ${rows.length
        ? html`<ul class="sitsum-list" role="tabpanel">${rows.map((s) => html`<li key=${s.id}>
            <a href=${`#/situations/${s.id}`} class="sitsum-row">
              <span class="sitsum-id">#${s.id}</span>
              <span class="sitsum-name">${s.operator_name || s.derived_name || "unnamed"}</span>
              ${s.maintenance ? html`<${Badge} tone="warn">MW<//>` : null}
              <span class="sitsum-size">${plural(s.alarm_count, "alarm")}</span>
              <span class="sitsum-age" title=${timeTitle(s.updated_at)}>${relative(s.updated_at)}</span>
            </a></li>`)}</ul>
          ${counts[tab] > rows.length
            ? html`<a class="sitsum-more" href="#/situations">${count(counts[tab] - rows.length)} more</a>`
            : null}`
        : html`<p class="muted sitsum-empty">${tab === "new" ? "No new situations." : "Nothing is being worked."}</p>`}
    </section>`;
  }
}
