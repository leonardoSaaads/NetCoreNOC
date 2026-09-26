/* A situation's sequence of events: what happened first, and what followed (v0.23.0, #396).
 *
 * For an RFO the question is the chain — an optical line fails, the amplifiers shut down, the far
 * end loses signal, the OSRP line fails for want of the path. The situation already holds every
 * alarm with its first sighting; this orders them, groups one trap on one element into one row
 * (a storm of 350 identical raises is one step of the chain, with its count), and places each step
 * on a real time axis from the first, with its offset. The first step is marked as where the chain
 * starts — the likeliest trigger, which is a reading and not a verdict, and the marker says so.
 *
 * "Copy as text" gives the same rows as plain text for the RFO document.
 */

import { html, Component } from "../../dom.js";
import { SeverityChip } from "../../widgets.js";
import { band, absolute, count, TIMEZONE } from "../../format.js";
import { spanText } from "../../chartdata.js";

const ROWS = 30;

/** The steps: one per (element, trap), ordered by first sighting. */
export function steps(alarms) {
  const by = new Map();
  for (const a of alarms || []) {
    const key = `${a.ne_id}|${a.class_id}`;
    const name = a.class_name || a.class_label || a.class_oid;
    const hit = by.get(key);
    const rank = a.declared_severity_rank ?? a.rule_severity_rank ?? a.severity_rank;
    if (!hit) {
      by.set(key, { key, element: a.device_label || a.device_ip, trap: name, oid: a.class_oid,
                    first: a.first_seen, last: a.last_seen, alarms: 1,
                    traps: Number(a.count || 1), rank, active: a.status === "active" });
    } else {
      hit.first = Math.min(hit.first, a.first_seen);
      hit.last = Math.max(hit.last, a.last_seen);
      hit.alarms += 1;
      hit.traps += Number(a.count || 1);
      if (rank != null && (hit.rank == null || rank < hit.rank)) hit.rank = rank;
      hit.active = hit.active || a.status === "active";
    }
  }
  return [...by.values()].sort((a, b) => a.first - b.first || a.key.localeCompare(b.key));
}

/** `+0.24 s`, `+3.1 s`, `+2 min`: sub-second where the chain is that fast, which a storm is. */
function offset(seconds) {
  if (seconds < 0.005) return "+0 s";
  if (seconds < 10) return `+${seconds.toFixed(seconds < 1 ? 2 : 1)} s`;
  return `+${spanText(seconds)}`;
}

export class Sequence extends Component {
  constructor(props) {
    super(props);
    this.state = { all: false, copied: null };
  }

  text(rows, t0) {
    const s = this.props.situation;
    return [
      `Situation #${s.id} — ${s.operator_name || s.derived_name || "unnamed"}`,
      `Sequence of events (times ${TIMEZONE})`,
      ...rows.map((r) => `${offset(r.first - t0).padEnd(10)} ${absolute(r.first)}  ${r.element}  ` +
        `${r.trap}${r.alarms > 1 ? `  (x${r.alarms})` : ""}${r.rank != null ? `  [${band(r.rank).label}]` : ""}`),
    ].join("\n");
  }

  async copy(rows, t0) {
    const body = this.text(rows, t0);
    try {
      await globalThis.navigator.clipboard.writeText(body);
      this.setState({ copied: "copied" });
    } catch {
      this.setState({ copied: body }); // no clipboard: show the text to select by hand
    }
  }

  render({ situation }, { all, copied }) {
    const rows = steps(situation.alarms);
    if (!rows.length) return html`<p class="muted">No member alarm is visible to you.</p>`;
    const t0 = rows[0].first;
    const end = Math.max(...rows.map((r) => r.last), t0 + 1);
    const span = end - t0;
    const shown = all ? rows : rows.slice(0, ROWS);
    const elements = new Set(rows.map((r) => r.element)).size;
    const x = (t) => (((t - t0) / span) * 100).toFixed(2);
    return html`<section class="panel-block seq" aria-label="Sequence of events">
      <div class="section-heading"><h3>Sequence of events</h3></div>
      <p class="seq-sum">
        <b>${count(situation.alarms.length)}</b> alarms · <b>${count(rows.length)}</b> steps on
        ${" "}<b>${count(elements)}</b> ${elements === 1 ? "element" : "elements"} · over
        ${" "}<b>${spanText(span)}</b>
      </p>
      <ol class="seq-list">
        ${shown.map((r, i) => html`<li key=${r.key} class=${i === 0 ? "seq-row seq-first" : "seq-row"}>
          <span class="seq-off" title=${absolute(r.first)}>${offset(r.first - t0)}</span>
          <span class="seq-what">
            <span class="seq-trap" title=${r.oid}>${r.trap}</span>
            <span class="seq-meta">${r.element}${r.alarms > 1 ? ` · ×${count(r.alarms)}` : ""}
              ${i === 0 ? html`<span class="seq-start" title="The earliest alarm in the situation: where the chain starts, and the likeliest trigger.">first</span>` : null}</span>
          </span>
          ${r.rank != null ? html`<${SeverityChip} band=${band(r.rank)} />` : html`<span></span>`}
          <span class="seq-track" aria-hidden="true">
            <span class="seq-bar" style=${`left:${x(r.first)}%;width:${Math.max(0.6, x(r.last) - x(r.first))}%`}></span>
            <span class="seq-dot" style=${`left:${x(r.first)}%`}></span>
          </span>
        </li>`)}
      </ol>
      <div class="seq-acts">
        ${rows.length > ROWS ? html`<button type="button" class="tap"
            onClick=${() => this.setState({ all: !all })}>${all ? "Show the first " + ROWS : `Show all ${count(rows.length)} steps`}</button>` : null}
        <button type="button" class="tap" onClick=${() => this.copy(rows, t0)}>Copy as text (RFO)</button>
        ${copied === "copied" ? html`<span role="status" class="muted">Copied.</span>` : null}
      </div>
      ${copied && copied !== "copied"
        ? html`<textarea class="seq-text" readonly rows="8" aria-label="Sequence as text">${copied}</textarea>`
        : null}
    </section>`;
  }
}
