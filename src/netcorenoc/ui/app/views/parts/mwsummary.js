/* Planned work, on the Overview (v0.21.1).
 *
 * The Overview is the screen an operator opens first and reads in about four seconds. Until now
 * it answered *"what is broken?"* completely and *"is any of this on purpose?"* not at all — so a
 * quiet estate at 02:00 looked the same whether nobody had touched it or somebody had a window
 * open over half of it. The marker on a device says so once you are looking at that device; this
 * says so before you go looking.
 *
 * ## What it leads with, and why that ordering
 *
 * **A running window first, always.** It is the only state that changes how the rest of the
 * screen should be read: while it is in force, the alarm counts above are incomplete by
 * construction. Scheduled work is context; running work is a caveat on everything else, so its
 * tile is the one that turns red and the hosts it covers get the card's one sentence (v0.29.0:
 * three counts and the next three windows — the rest is on the Maintenance screen).
 *
 * Then at most three upcoming, because this is a summary and the screen it summarises is one
 * click away. A fourth row would buy nothing an operator cannot get by following the link.
 *
 * ## It cannot take the dashboard down
 *
 * Its own `get`, its own state, its own failure. `/api/maintenance-windows` needs `mw.read`, and
 * a deployment that has withheld it — or an appliance whose migration has not run — answers 403
 * or 500 here. This is v0.12.0's `Promise.all` lesson as a component boundary: a panel that
 * cannot read its own route renders **one quiet line** and the other five panels are untouched.
 * A blank Overview during an incident is the worst thing this console can do.
 */

import { Component, html } from "../../dom.js";
import { get } from "../../api.js";
import { SectionHeading } from "../../widgets.js";
import { count, plural } from "../../format.js";
import { humanise } from "./mwdraft.js";

/* How many upcoming rows a summary shows. Three, because the fourth is a scroll and the whole
 * screen it summarises is one click away. */
const SHOWN = 3;

const TONE = { active: "alarm", pending_confirmation: "warn", scheduled: "info" };
const LABEL = { active: "running", pending_confirmation: "to confirm", scheduled: "scheduled" };

/* Windows that are in force now, and the ones still to come — in the order an operator cares
 * about them. Pure, so `tests/ui/test_maintenance_dom.py` can drive it without a network. */
export function split(windows) {
  const rows = Array.isArray(windows) ? windows : [];
  const running = rows.filter((w) => w.status === "active");
  const ahead = rows
    .filter((w) => w.status === "scheduled" || w.status === "pending_confirmation")
    .sort((a, b) => (a.starts_at || 0) - (b.starts_at || 0));
  return { running, ahead };
}

export class PlannedWork extends Component {
  constructor(props) {
    super(props);
    this.state = { windows: null, error: null };
  }

  componentDidMount() {
    this.read();
    // The list is a clock: a countdown that never moves is a countdown an operator stops
    // believing. One read a minute, and cleared on unmount so a closed screen stops polling.
    this.timer = setInterval(() => this.read(), 60000);
  }

  componentWillUnmount() { clearInterval(this.timer); }

  async read() {
    try {
      const body = await get("/api/maintenance-windows?limit=10");
      this.setState({ windows: body.windows || [], error: null });
    } catch (error) {
      this.setState({ error: error.message || String(error) });
    }
  }

  render() {
    const { windows, error } = this.state;
    if (error) {
      // A read that failed is a sentence, not a spinner and not a blank: the same choice
      // `CorrelationHealth` makes one panel over.
      return html`<section class="panel-block" data-role="planned-work">
        <${SectionHeading} title="Planned work" />
        <p class="hint" data-role="planned-error">Could not read planned work. ${error}</p>
      <//>`;
    }
    if (windows === null) return null;  // first read in flight; no skeleton for one line

    // v0.29.0: three counts and the next three windows. The running count is the caveat on the
    // rest of the screen, so it is the one tile that turns red, and the hosts it covers are said
    // once under it; every other word the card used to carry is on the Maintenance screen.
    const { running, ahead } = split(windows);
    const pending = ahead.filter((w) => w.status === "pending_confirmation").length;
    const hosts = running.reduce((n, w) => n + (w.target_count || 0), 0);
    const next = [...running, ...ahead].slice(0, SHOWN);
    const tiles = [
      ["running", running.length, running.length ? "mwsum-hot" : ""],
      ["scheduled", ahead.length - pending, ""],
      ["to confirm", pending, pending ? "mwsum-warn" : ""],
    ];
    return html`<section class="panel-block mwsum" data-role="planned-work">
      <div class="section-heading"><h3>Planned work</h3>
        <a class="sitsum-all" href="#/maintenance">View all</a></div>
      <div class="mwsum-body">
        <div class="mwsum-kpis">
          ${tiles.map(([word, n, tone]) => html`<a key=${word} href="#/maintenance"
              class=${`mwsum-kpi ${tone}`} data-role=${word === "running" ? "running" : null}>
            <b>${count(n)}</b><span>${word}</span></a>`)}
          ${hosts
            ? html`<p class="mwsum-caveat">Alarms from ${plural(hosts, "host", "hosts")} may be
                suppressed.</p>`
            : null}
        </div>
        ${next.length
          ? html`<ul class="mwsum-list" data-role="upcoming">${next.map((w) => html`<li key=${w.id}
              data-window=${w.id} data-status=${w.status}>
              <a class="mwsum-row" href="#/maintenance">
                <i class=${`mwsum-dot mwsum-${TONE[w.status] || "muted"}`} title=${LABEL[w.status] || w.status}
                  aria-label=${LABEL[w.status] || w.status}></i>
                ${/* A redacted row still says a window EXISTS and when — prime directive 4. */ null}
                <span class="mw-sum-name">${w.redacted
                  ? html`<span class="muted">Maintenance on ${plural(w.target_count, "host", "hosts")}</span>`
                  : w.name}</span>
                <span class="muted mw-row-when">${w.status === "active"
                  ? `${humanise(w.ends_in_s)} left`
                  : w.starts_in_s > 0 ? `in ${humanise(w.starts_in_s)}` : "starting"}</span>
              </a></li>`)}</ul>`
          : html`<p class="muted" data-role="planned-none">Nothing planned.</p>`}
      </div>
    <//>`;
  }
}
