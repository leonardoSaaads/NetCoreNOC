/* The health control: CPU, memory and storage, each with a two-hour sparkline.
 *
 * Split out of `notices.js` in v0.16.5 because that file reached **19 005 bytes** and the module
 * graph's ceiling is 17 579 — one third of the v0.12.0 `app.js` this console replaced. The seam is
 * the one the guard would have chosen anyway: `notices.js` owns the disclosure mechanism and the
 * warning list, and this owns what the appliance says about itself.
 *
 * Everything here reads `stats.resources`, which `engine/operate/resources.py` fills from three
 * stdlib reads. **No metric is ever invented**: one the host will not give up renders as `—` and
 * says "not measured", never `0%` (DECISIONS #289, kept; #300, which made the readings possible).
 */

import { html, cx } from "./dom.js";
import { count, plural, score } from "./format.js";
import { runs as splitRuns } from "./chartdata.js";
import { Disclosure, healthState } from "./notices.js";

/**
 * A sparkline: one `<polyline>` per run, drawn from the series `/api/stats` already carries.
 *
 * **No charting library and no `<canvas>`.** Twenty-four points scaled into a `viewBox` is nine
 * lines of arithmetic, and the alternative is a dependency this project does not take — the same
 * answer d3 got, except d3 draws a force simulation and this draws a line.
 *
 * A gap in the series is a **break in the line**, not a dip to zero. **v0.16.6 moved that rule into
 * `chartdata.runs`** and this calls it: the splitting was written here first and the Overview's
 * charts need the same behaviour, and two implementations of *"where does the line break"* is how
 * two charts on one screen come to disagree about it. The arithmetic is byte-for-byte the same —
 * `tests/test_ui_invariants.py` drives both surfaces over the same series.
 */
export function Spark({ series, tone }) {
  const parts = splitRuns(series, { max: 100, height: 24 });
  if (!parts.length) return null;
  return html`<svg class=${cx("spark", tone && `spark-${tone}`)} viewBox="0 0 100 24"
       preserveAspectRatio="none" aria-hidden="true" focusable="false">
    ${parts.map((r, i) => html`<polyline key=${i} points=${r.join(" ")} />`)}
  <//>`;
}

/** `94.2 GB`, and never a bare byte count: nobody reads 239923093504. */
function bytes(value) {
  if (value == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = value;
  let u = 0;
  while (n >= 1024 && u < units.length - 1) {
    n /= 1024;
    u += 1;
  }
  return `${n < 10 && u > 0 ? n.toFixed(1) : Math.round(n)} ${units[u]}`;
}

/** One row of the health panel: name, percentage, bar, sparkline, and what it is of. */
function Meter({ name, pct, detail, series, title }) {
  const known = pct != null;
  const tone = !known ? null : pct >= 90 ? "alarm" : pct >= 75 ? "warn" : null;
  return html`<div class=${cx("meter", tone && `meter-${tone}`)} title=${title}>
    <div class="meter-head">
      <span class="meter-name">${name}</span>
      <span class="meter-pct">${known ? `${pct.toFixed(0)}%` : "—"}</span>
    </div>
    <div class="meter-bar" role="img"
         aria-label=${known ? `${name}: ${pct.toFixed(0)} percent` : `${name}: not measured`}>
      <span class="meter-fill" style=${`width:${known ? Math.min(100, pct) : 0}%`}></span>
    </div>
    <div class="meter-foot">
      <span class="meter-detail">${detail}</span>
      <${Spark} series=${series} tone=${tone} />
    </div>
  </div>`;
}

/**
 * **CPU, memory and storage** — three numbers, three bars, three two-hour sparklines.
 *
 * ## What changed, and why #289 does not forbid it
 *
 * v0.16.4 refused to show these and said so in the panel, on the ground that the alternative to
 * four true numbers was *"add a source"*. The measurement that reopened it: **all three are stdlib
 * reads** — `/proc/stat`, the cgroup's `memory.max` (or `/proc/meminfo`), and `os.statvfs` on the
 * database's filesystem. `psutil` was never the only way to read them, and the dependency count is
 * still five. #289's actual rule — *never invent a number* — is intact and is why a metric this
 * appliance cannot read renders `—` and says "not measured", never `0%`.
 *
 * ## The panel got shorter, which is the point
 *
 * It carried 49 words across five rows of qualifier text. The three the operator opens it for are
 * now the first thing in it; the four correlation counters that used to be the whole panel are one
 * line of secondary text underneath, because *"is the appliance keeping up"* is answered by the
 * word at the top and the numbers behind it are for when the answer is no.
 */
export function Health({ stats, rate }) {
  const state = healthState(stats);
  const receiver = stats?.receiver;
  const res = stats?.resources;
  const hours = res?.window_s ? Math.round(res.window_s / 3600) : 2;
  /* `hover` on the health control and NOT on the bell. Health is a glance — the operator wants to
     know the appliance is fine and carry on, and making them click for that is a tax on the common
     case. A warning is something they are going to act on: opening that panel by accidentally
     crossing it, over the work area, while they are reading something else, is an interruption
     they did not ask for. Both still open on click, at every width. */
  return html`<${Disclosure} id="healthPanel" icon="pulse" tone=${state.tone}
      title="System health" hover
      label=${`Appliance health: ${state.word}. Open the numbers.`}>
    <p class=${cx("health-state", `health-${state.key}`)}>${state.word}</p>
    ${res
      ? html`<div class="meters">
          <${Meter} name="CPU" pct=${res.cpu_pct} series=${res.cpu_series}
            detail=${res.cpu_pct == null
              ? "not measured"
              : `of ${plural(res.cpu_count ?? 0, "core")}`}
            title=${`Busy time across all cores, sampled every ${res.interval_s ?? 30} s.`} />
          <${Meter} name="Memory" pct=${res.mem_pct} series=${res.mem_series}
            detail=${res.mem_total == null
              ? "not measured"
              : `${bytes(res.mem_used)} of ${bytes(res.mem_total)}` +
                `${res.mem_source === "cgroup" ? " (container)" : ""}`}
            title=${res.mem_source === "cgroup"
              ? "The container's limit, not the host's memory."
              : "The host's memory. No container limit is set."} />
          <${Meter} name="Storage" pct=${res.disk_pct} series=${res.disk_series}
            detail=${res.disk_total == null
              ? "not measured"
              : `${bytes(res.disk_used)} of ${bytes(res.disk_total)}`}
            title="The filesystem holding the database — the one that stops this appliance." />
          ${/* The sparklines' window, stated once for all three. It used to sit under CPU alone
                ("16 cores · last 2 hours"), where it read as a qualifier of the percentage. */
            null}
          <p class="meter-span">lines: last ${plural(hours, "hour")}</p>
        </div>`
      : html`<p class="hint">No <code>resources</code> block in <code>/api/stats</code>: this API is
          running without the process runner, so nothing is sampling the host.</p>`}
    <p class="health-sub">
      queue <b>${count(stats?.queue_depth ?? 0)}</b> ·
      p95 <b>${score(stats?.latency_p95_s ?? 0, 4)} s</b> ·
      ${rate
        ? html`<b>${rate.perSecond.toFixed(rate.perSecond < 10 ? 2 : 0)} /s</b>`
        : html`rate <b>—</b>`}
      ${receiver
        ? html` · refused <b>${count(receiver.denied)}</b> ·
            dropped <b>${count(receiver.dropped)}</b>`
        : null}
    </p>
  <//>`;
}
