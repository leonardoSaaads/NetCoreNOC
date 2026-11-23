/* Planned work at a glance, every broker's (ADR #503): the numbers, each network's share, the week.
 *
 * The list below answers "what is next?"; this answers "how much planned work is there, where, and
 * does any of it need me?": what is running now and over how many hosts, what starts in the next
 * day and the next week, what waits for a confirmation (and the first one to lapse), and what the
 * last week ended with. Then one row per broker — each with its own *Schedule* — and the next seven
 * days drawn as bars, so two windows on one network on the same night are seen before they clash.
 *
 * It reads the same list route as the rows — every live window, and what ended or expired in the
 * last week — so a viewer sees every window's existence and timing and a redacted name, as on the
 * rows. A read that failed is a dash on its tile, never a broken screen.
 */

import { html, cx } from "../../dom.js";
import { brokerName } from "../../brokers.js";
import { SectionMark } from "../../widgets.js";
import { Icon } from "../../icons.js";
import { count, plural } from "../../format.js";
import { humanise } from "./mwdraft.js";

const DAY = 86400;
const WEEK = 7 * DAY;
const UPCOMING = new Set(["pending_confirmation", "scheduled"]);
const OVER = new Set(["ended", "cancelled", "expired"]);
/** At most this many windows are drawn on the week; the list below has them all. */
const MAX_BARS = 14;

/** The numbers a lead reads first: from the live windows, and the last week's ends (`past`,
 * null when that read failed). */
export function tally(windows, now, past = []) {
  const active = windows.filter((w) => w.status === "active");
  const upcoming = windows.filter((w) => UPCOMING.has(w.status));
  const pending = upcoming.filter((w) => w.status === "pending_confirmation");
  const lapse = pending.map((w) => w.starts_at - (w.patch_s || 0) - now).filter((s) => s > 0);
  return {
    active: active.length,
    hosts: active.reduce((n, w) => n + (w.target_count || 0), 0),
    day: upcoming.filter((w) => w.starts_at - now <= DAY).length,
    week: upcoming.filter((w) => w.starts_at - now <= WEEK).length,
    pending: pending.length,
    lapse: lapse.length ? Math.min(...lapse) : null,
    ended: past ? past.filter((w) => w.status === "ended").length : null,
    expired: past ? past.filter((w) => w.status === "expired").length : 0,
  };
}

/** One row per broker (and one for windows tied to none), in the order the sidebar lists them. */
export function byBroker(windows, brokers, now) {
  const rows = new Map((brokers || []).map((b) => [b.id, { id: b.id, name: b.name, list: [] }]));
  for (const w of windows) {
    const key = w.broker_id ?? null;
    if (!rows.has(key)) rows.set(key, { id: key, name: key == null ? "Not tied to one broker" : brokerName(key), list: [] });
    rows.get(key).list.push(w);
  }
  return [...rows.values()].map((r) => ({ ...r, ...tally(r.list, now) }));
}

function Tile({ icon, label, value, note, tone }) {
  return html`<div class=${cx("mwb-tile", tone && `mwb-tile-${tone}`)}>
    <span class="mwb-tile-head"><${Icon} name=${icon} /> ${label}</span>
    <b class="mwb-num">${value == null ? "—" : count(value)}</b>
    <span class="mwb-note">${note}</span>
  </div>`;
}

const DAY_FMT = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric" });

/** The next seven days from today's midnight, each window a bar from its start to its end. */
function Week({ windows, now }) {
  const midnight = new Date(now * 1000);
  midnight.setHours(0, 0, 0, 0);
  const from = midnight.getTime() / 1000;
  const to = from + WEEK;
  const shown = windows
    .filter((w) => !OVER.has(w.status) && w.ends_at > from && w.starts_at < to)
    .sort((a, b) => a.starts_at - b.starts_at);
  const pct = (t) => `${(((Math.min(Math.max(t, from), to) - from) / WEEK) * 100).toFixed(2)}%`;
  const days = Array.from({ length: 7 }, (_, i) => DAY_FMT.format(new Date((from + i * DAY) * 1000)));
  return html`<section class="panel-block mwb-week" aria-labelledby="mwb-week">
    <h3 id="mwb-week"><${SectionMark} icon="timeline" />The next seven days</h3>
    ${shown.length ? html`<div class="mwb-gantt" role="list">
      <div class="mwb-days" aria-hidden="true"><span class="mwb-gantt-label"></span>
        <span class="mwb-day-row">${days.map((d) => html`<span key=${d} class="mwb-day">${d}</span>`)}</span></div>
      ${shown.slice(0, MAX_BARS).map((w) => html`<div key=${w.id} class="mwb-gantt-row" role="listitem">
        <span class="mwb-gantt-label" title=${w.redacted ? "" : w.name}>${w.redacted
          ? plural(w.target_count, "host") : w.name}<small class="muted">${w.broker_id ? brokerName(w.broker_id) : "Any broker"}</small></span>
        <span class="mwb-track"><span class="mwb-now" style=${`left:${pct(now)}`} aria-hidden="true"></span>
          <span class=${cx("mwb-bar", `mwb-bar-${w.status}`)}
            style=${`left:${pct(w.starts_at)};width:${`calc(${pct(w.ends_at)} - ${pct(w.starts_at)})`}`}
            title=${`${humanise(w.ends_at - w.starts_at)} · ${w.status.replace("_", " ")}`}></span></span>
      </div>`)}
      ${shown.length > MAX_BARS ? html`<p class="hint">and ${plural(shown.length - MAX_BARS, "more window")} — all in the list below.</p>` : null}
    </div>` : html`<p class="muted">Nothing is planned for the next seven days.</p>`}
  </section>`;
}

function Brokers({ rows, onBroker, onSchedule }) {
  return html`<section class="panel-block mwb-brokers" aria-labelledby="mwb-brokers">
    <h3 id="mwb-brokers"><${SectionMark} icon="network" />By broker</h3>
    <div class="table-scroll"><table class="data compact-table">
      <thead><tr><th scope="col">Broker</th><th scope="col" class="num">Running</th>
        <th scope="col" class="num">Hosts</th><th scope="col" class="num">7 days</th>
        <th scope="col" class="num">To confirm</th><th scope="col"><span class="visually-hidden">Act</span></th></tr></thead>
      <tbody>${rows.map((r) => html`<tr key=${String(r.id)}>
        <th scope="row">${r.id == null ? html`<span class="muted">${r.name}</span>`
          : html`<button type="button" class="linkish" onClick=${() => onBroker(r.id)}>${r.name}</button>`}</th>
        <td class=${cx("num", r.active && "mwb-on")}>${count(r.active)}</td>
        <td class="num">${count(r.hosts)}</td>
        <td class="num">${count(r.week)}</td>
        <td class=${cx("num", r.pending && "mwb-warn")}>${count(r.pending)}</td>
        <td class="mwb-act">${onSchedule && r.id != null ? html`<button type="button" data-role="mw-schedule-broker"
          aria-label=${`Schedule on ${r.name}`} onClick=${() => onSchedule(r.id)}><${Icon} name="maintenance" /> Schedule</button>` : null}</td>
      </tr>`)}</tbody></table></div>
  </section>`;
}

/** The board: its tiles, the brokers' rows (on the general tab with more than one), the week. */
export function MaintenanceBoard({ live, recent, now, brokers, inBroker, onBroker, onSchedule }) {
  const windows = (live && live.windows) || [];
  const at = (live && live.now) || now || Date.now() / 1000;
  const t = tally(windows, at, recent ? recent.windows || [] : null);
  return html`<div class="mwb" data-role="mw-board">
    <div class="mwb-tiles">
      <${Tile} icon="play" label="Running now" value=${t.active} tone=${t.active ? "on" : null}
        note=${t.active ? `${plural(t.hosts, "host")} under maintenance` : "No host is under maintenance"} />
      <${Tile} icon="timeline" label="Next 24 hours" value=${t.day} note=${`${plural(t.week, "window")} in the next 7 days`} />
      <${Tile} icon="warn" label="To confirm" value=${t.pending} tone=${t.pending ? "warn" : null}
        note=${t.lapse != null ? `the first lapses in ${humanise(t.lapse)}` : "Nothing waits for a confirmation"} />
      <${Tile} icon="check" label="Last 7 days" value=${t.ended} tone=${t.expired ? "bad" : null}
        note=${t.ended == null ? "Could not read the last week" : t.expired
          ? `ended · ${plural(t.expired, "window")} expired unconfirmed` : "ended as planned"} />
    </div>
    ${live && live.total > windows.length ? html`<p class="hint">Counting the first ${count(windows.length)} of ${count(live.total)} live windows.</p>` : null}
    ${live ? null : html`<p class="hint">The live windows could not be read; the list below is current.</p>`}
    <div class=${cx("mwb-grid", (inBroker || (brokers || []).length < 2) && "mwb-grid-one")}>
      ${inBroker || (brokers || []).length < 2 ? null : html`<${Brokers} rows=${byBroker(windows, brokers, at)}
        onBroker=${onBroker} onSchedule=${onSchedule} />`}
      <${Week} windows=${windows} now=${at} />
    </div>
  </div>`;
}
