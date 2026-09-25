/* The Overview's appliance half: is it keeping up (v0.16.7, #318; v0.22.0, #380).
 *
 * ## The range reaches the query now (F154)
 *
 * The range picker at the top of the Overview offered 15 minutes to 7 days, and these charts drew
 * the same two hours whatever was chosen: they read `stats.resources`, a 240-reading ring in the
 * appliance's memory that no restart survived, and the queue chart read a ring **in this browser**.
 * A control that silently ignores the operator is worse than no control. The readings are now
 * persisted (`host_sample`, migration 0022) and this panel asks `/api/resources` for the window it
 * was given, so a different range is a different query.
 *
 * ## What left
 *
 * The verdict restated the tiles beside it (*"Keeping up — 24 traps accepted, none dropped, CPU
 * 3.5%"*) and every caption restated the axis (*"16 cores · 2.0 h of a 2.0 h window"*). The verdict
 * is now the answer alone, and a caption says only what the chart is **of**.
 */

import { html, Component } from "../../dom.js";
import { get } from "../../api.js";
import { SectionHeading } from "../../widgets.js";
import { Series } from "../../charts.js";
import { clock, spanText } from "../../chartdata.js";
import { count, score } from "../../format.js";

/** Points per chart. The same as the activity chart, so the two share a column grid. */
export const HOST_BUCKETS = 24;

/** The five host charts: title, series key, unit, and what the percentage is OF. */
function charts(res) {
  return [
    ["CPU", "cpu_pct", "%", res && res.cpu_count ? `${res.cpu_count} cores` : "this host"],
    ["Memory", "mem_pct", "%", res && res.mem_source === "cgroup" ? "container limit" : "host"],
    ["Storage", "disk_pct", "%", "database filesystem"],
    ["Database", "db_mb", "MB", "file and journal"],
    ["Queue depth", "queue_depth", "traps", "waiting, worst per bucket"],
  ];
}

export class Keeping extends Component {
  constructor(props) {
    super(props);
    this.state = { history: null, error: null };
  }

  componentDidMount() { this.read(); }

  componentDidUpdate(previous) {
    if (previous.rangeS !== this.props.rangeS) this.read();
  }

  /** The window the operator picked, answered in SQL. A failure is stated, never drawn as zero. */
  async read() {
    const asked = this.props.rangeS;
    try {
      const history = await get(`/api/resources?range_s=${asked}&buckets=${HOST_BUCKETS}`);
      if (asked === this.props.rangeS) this.setState({ history, error: null });
    } catch (error) {
      this.setState({ error });
    }
  }

  render({ stats, rate }, { history, error }) {
    const res = stats.resources;
    const receiver = stats.receiver;
    const verdict = keepingUp(res, receiver);
    const series = (history && history.series) || {};
    const n = history ? history.buckets : 0;
    // Each served point is a bucket MEAN, so it is labelled at the bucket's middle.
    const labels = history
      ? Array.from({ length: n }, (_, i) =>
          clock(history.from + (i + 0.5) * history.bucket_s, history.to - history.from))
      : [];
    const partial = history && history.measured_from != null
      && history.measured_from - history.from > history.bucket_s
      ? `measured for the last ${spanText(history.to - history.measured_from)}`
      : null;
    return html`<section class="panel-block">
      <${SectionHeading} title="Is the appliance keeping up" />
      <p class=${`verdict verdict-${verdict.tone}`}>${verdict.text}</p>
      ${error ? html`<p class="hint">Could not read the host history: ${error.message}</p>` : null}
      <div class="chart-grid">
        ${charts(res).map(([title, key, unit, what]) => html`<${Series} key=${key}
          title=${title} unit=${unit} labels=${labels}
          series=${[{ name: title, tone: key === "queue_depth" ? "warn" : null,
                      values: series[key] || [] }]}
          source=${what} span=${partial} />`)}
      </div>
      <p class="health-sub">
        ${[
          { name: "p95 latency", value: `${score(stats.latency_p95_s ?? 0, 4)} s` },
          // A rate names its window, and one sample is "waiting", never a zero.
          { name: "trap rate",
            value: rate ? `${rate.perSecond.toFixed(rate.perSecond < 10 ? 2 : 0)} /s` : "—",
            note: rate ? `over ${spanText(rate.windowS)}` : "waiting" },
          // Only what is non-zero, plus the two always worth a glance: a zero here is the good
          // case and needs no words.
          ...(receiver
            ? ["received", "accepted", "denied", "quarantined", "dropped"]
                .filter((key) => key === "received" || key === "accepted" || receiver[key])
                .map((key) => ({
                  name: key === "denied" ? "refused" : key,
                  metric: key,
                  value: count(receiver[key]),
                }))
            : []),
        ].map((one, index) => html`<span key=${one.metric || one.name}>
          ${index ? html`<span class="metric-sep"> · </span>` : null}
          <${Metric} ...${one} />
        </span>`)}
      </p>
    </section>`;
  }
}

/**
 * The one-line answer to the panel's question. Every branch is a threshold on a number the panel
 * draws, in the order an operator would act: storage that stops the appliance, traps lost, traps
 * refused, load, then the all-clear. A metric this host does not expose does not vote.
 */
export function keepingUp(res, receiver) {
  const disk = res && res.disk_pct;
  const cpu = res && res.cpu_pct;
  const mem = res && res.mem_pct;
  const dropped = (receiver && receiver.dropped) || 0;
  const denied = (receiver && receiver.denied) || 0;
  if (disk != null && disk >= 90) {
    return { tone: "bad", text: `Storage is ${disk}% full — this appliance stops when it fills.` };
  }
  if (dropped > 0) return { tone: "bad", text: `${count(dropped)} traps dropped.` };
  if (denied > 0) {
    return { tone: "warn", text: `${count(denied)} datagrams refused by the trap allowlist.` };
  }
  if ((cpu != null && cpu >= 85) || (mem != null && mem >= 85)) {
    return { tone: "warn", text: "Running hot." };
  }
  if (cpu == null && mem == null && disk == null) {
    return { tone: "quiet", text: "This host exposes no CPU, memory or storage readings." };
  }
  // "Keeping up" with nothing to keep up with is not an answer.
  if (!((receiver && receiver.accepted) || 0)) return { tone: "quiet", text: "No traps yet." };
  return { tone: "ok", text: "Keeping up." };
}

/**
 * One number on the secondary line. `data-metric` names it so a test reads it by name rather than
 * by position; the explicit `${" "}` is F112 — a margin fixes the pixels and leaves `textContent`
 * reading `p95 latency0.0000 s`.
 */
function Metric({ name, metric, value, note }) {
  return html`<span class="metric" data-metric=${metric || name}>
    <span class="metric-name">${name}</span>${" "}<b>${value}</b>
    ${note ? html`${" "}<span class="metric-note">${note}</span>` : null}
  </span>`;
}
