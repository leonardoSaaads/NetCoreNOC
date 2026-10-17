/* The Overview's trap pipeline: is every trap kept, and classified within the KPI (ADRs #450, #451).
 *
 * ## Why this panel replaced three of the "keeping up" charts
 *
 * The queue chart sampled the in-memory queue once every 30 s, so a storm that filled it and
 * drained it between two samples drew a flat zero — the report that it "was not functioning
 * particularly well" was exact. And with the spool most of a storm waits on disk, where that queue
 * never looked. The appliance now records each 30 s **interval** (`host_sample`, migration 0030):
 * the backlog's peak wherever it waited, traps in and out, the p95 and the worst decision latency,
 * and how many traps fell outside the KPI. Those are what this panel draws, for the range picked.
 *
 * ## Read like Zabbix's queue
 *
 * The tiles answer "now": the backlog and how long its oldest trap has waited, throughput in and
 * out, the latency percentiles, and the share of traps within the KPI's target and limit. The bars
 * are Zabbix's delay buckets — how many traps took up to 5 s, 10 s, 30 s, 1 min, 2 min, 5 min,
 * 10 min, or longer. A trap past the limit is still classified: the KPI measures, it never drops.
 */

import { html, Component } from "../../dom.js";
import { SectionHeading } from "../../widgets.js";
import { Series } from "../../charts.js";
import { clock, spanText } from "../../chartdata.js";
import { count, score } from "../../format.js";
import { history } from "./keeping.js";

/** Seconds, as an operator reads a wait: `0.42 s`, `48 s`, `3.5 min`. */
export function seconds(value) {
  if (value == null) return "—";
  const s = Number(value);
  if (s < 10) return `${score(s, 2)} s`;
  if (s < 120) return `${Math.round(s)} s`;
  return `${(s / 60).toFixed(1)} min`;
}

/** The edge of one delay bucket, as its label: `≤ 5 s` … `> 10 min`. */
function edge(upto, previous) {
  const text = (s) => (s < 60 ? `${s} s` : `${s / 60} min`);
  return upto == null ? `> ${text(previous)}` : `≤ ${text(upto)}`;
}

/** The one-line answer, only when there is something to act on (the all-clear is the tiles). */
export function pipelineVerdict(clockStats) {
  if (!clockStats) return null;
  const spool = clockStats.spool;
  if (spool && spool.error) {
    return { tone: "bad", text: `The trap spool refuses writes (${spool.error}): traps are being dropped.` };
  }
  if (clockStats.dropped > 0) return { tone: "bad", text: `${count(clockStats.dropped)} traps dropped.` };
  if (clockStats.oldest_wait_s > clockStats.limit_s) {
    return { tone: "bad", text: `The oldest waiting trap is past the ${seconds(clockStats.limit_s)} limit.` };
  }
  if (clockStats.oldest_wait_s > clockStats.target_s) {
    return { tone: "warn", text: `Behind: the oldest waiting trap is past the ${seconds(clockStats.target_s)} target.` };
  }
  return null;
}

export class Pipeline extends Component {
  constructor(props) {
    super(props);
    this.state = { data: null, error: null };
  }

  componentDidMount() { this.read(); }

  componentDidUpdate(previous) {
    if (previous.rangeS !== this.props.rangeS || previous.tick !== this.props.tick) this.read();
  }

  async read() {
    const asked = this.props.rangeS;
    try {
      const data = await history(asked, this.props.tick);
      if (asked === this.props.rangeS) this.setState({ data, error: null });
    } catch (error) {
      this.setState({ error });
    }
  }

  render({ stats, rate }, { data, error }) {
    const live = stats.classification;
    const verdict = pipelineVerdict(live);
    const series = (data && data.series) || {};
    const n = data ? data.buckets : 0;
    const labels = data
      ? Array.from({ length: n }, (_, i) =>
          clock(data.from + (i + 0.5) * data.bucket_s, data.to - data.from))
      : [];
    return html`<section class="panel-block pipeline" data-panel="pipeline">
      <${SectionHeading} title="Trap pipeline"
        hint=${"Every accepted trap is written to the spool on disk before it is classified, so " +
               "a slow model never loses one. Decision latency runs from each trap's own arrival " +
               "to the commit that placed it in a situation."} />
      ${verdict ? html`<p class=${`verdict verdict-${verdict.tone}`}>${verdict.text}</p>` : null}
      ${error ? html`<p class="hint">Could not read the pipeline history: ${error.message}</p>` : null}
      ${live ? html`<${Tiles} live=${live} rate=${rate} />` : html`<p class="hint">This API
        publishes no <code>classification</code> block.</p>`}
      ${live ? html`<${Delays} live=${live} />` : null}
      <div class="chart-grid">
        <${Series} title="Throughput" unit="/s" labels=${labels}
          series=${[
            { name: "in", values: series.traps_per_s || [] },
            { name: "classified", tone: "quiet", values: series.classified_per_s || [] },
          ]}
          source="received and classified, mean per bucket" />
        <${Series} title="Backlog" unit="traps" labels=${labels}
          series=${[{ name: "Backlog", tone: "warn", values: series.queue_depth || [] }]}
          source="waiting to be classified, peak per bucket" />
        <${Series} title="Latency" unit="ms" labels=${labels}
          series=${[
            { name: "p95", values: series.latency_ms || [] },
            { name: "worst", tone: "warn", values: series.latency_max_ms || [] },
          ]}
          source="decision latency, arrival to classification, per bucket" />
        <${Series} title="KPI misses" unit="traps" mark="column" labels=${labels}
          series=${[
            { name: "over target", tone: "warn", values: series.over_target || [] },
            { name: "over limit", tone: "alarm", values: series.over_limit || [] },
          ]}
          source=${live ? `target ${seconds(live.target_s)} · limit ${seconds(live.limit_s)}`
                        : "per bucket"} />
      </div>
    </section>`;
  }
}

/** Now: the backlog, throughput, latency and compliance — each a name and a number. */
function Tiles({ live, rate }) {
  const spool = live.spool;
  const share = (pct) => (pct == null ? "—" : `${pct.toFixed(pct >= 99.95 || pct < 10 ? 1 : 2)}%`);
  const perSecond = (value) => (value == null ? "—" : value.toFixed(value < 10 ? 2 : 0));
  const tiles = [
    { key: "backlog", name: "Backlog", value: count(live.backlog),
      note: spool ? `${count(spool.on_disk)} on disk · ${count(live.queue.depth)} queued`
                  : `${count(live.queue.depth)} queued` },
    { key: "oldest", name: "Oldest waiting", value: seconds(live.oldest_wait_s),
      tone: live.oldest_wait_s > live.limit_s ? "bad" : live.oldest_wait_s > live.target_s ? "warn" : null },
    { key: "throughput", name: "In / classified, per second",
      value: rate ? `${perSecond(rate.perSecond)} / ${perSecond(rate.outPerSecond)}` : "—",
      note: rate ? `over ${spanText(rate.windowS)}` : "waiting for a second reading" },
    { key: "latency", name: "Decision latency p95", value: seconds(live.p95_s),
      note: `p50 ${seconds(live.p50_s)} · p99 ${seconds(live.p99_s)} · worst ${seconds(live.worst_s)}` },
    { key: "target", name: `Within ${seconds(live.target_s)} target`, value: share(live.within_target_pct),
      note: `${count(live.over_target)} of ${count(live.classified)} over`,
      tone: live.over_target ? "warn" : null },
    { key: "limit", name: `Within ${seconds(live.limit_s)} limit`, value: share(live.within_limit_pct),
      note: `${count(live.over_limit)} over · ${count(live.dropped)} dropped`,
      tone: live.over_limit || live.dropped ? "bad" : null },
  ];
  if (spool) {
    tiles.push({ key: "spool", name: "Spool on disk",
      value: spool.bytes == null ? "—" : `${count(Math.round(spool.bytes / 2 ** 20))} MB`,
      note: spool.max_bytes ? `of ${count(Math.round(spool.max_bytes / 2 ** 20))} MB` +
                              `${spool.dead_lettered ? ` · ${count(spool.dead_lettered)} set aside` : ""}` : "",
      tone: spool.error ? "bad" : spool.dead_lettered ? "warn" : null });
  }
  return html`<div class="pipe-tiles">
    ${tiles.map((t) => html`<div key=${t.key} class=${`pipe-tile${t.tone ? ` pipe-${t.tone}` : ""}`}
        data-metric=${`pipeline.${t.key}`}>
      <span class="pipe-name">${t.name}</span>${" "}
      <b class="pipe-value">${t.value}</b>
      ${t.note ? html`${" "}<span class="pipe-note">${t.note}</span>` : null}
    </div>`)}
  </div>`;
}

/** Zabbix's delay buckets, since start: how long each classified trap took. */
function Delays({ live }) {
  const delays = live.delays || [];
  const top = Math.max(1, ...delays.map((d) => d.count));
  let previous = 0;
  return html`<div class="pipe-delays">
    <h4 class="chart-title">Decision latency, since start</h4>
    <div class="chart-bars">
      ${delays.map((d) => {
        const label = edge(d.upto_s, previous);
        const late = d.upto_s == null || d.upto_s > live.target_s;
        const over = d.upto_s == null || d.upto_s > live.limit_s;
        previous = d.upto_s ?? previous;
        return html`<div key=${label} class="chart-bar-row" data-delay=${label}>
          <span class="chart-bar-label">${label}</span>
          <span class="chart-bar-track"><span
            class=${`chart-bar-fill${over ? " chart-alarm" : late ? " chart-warn" : ""}`}
            style=${`width:${((100 * d.count) / top).toFixed(1)}%`}></span></span>
          <span class="chart-bar-value">${count(d.count)}</span>
        </div>`;
      })}
    </div>
  </div>`;
}
