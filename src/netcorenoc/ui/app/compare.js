/* The chart that compares things to each other, rather than to their own past.
 *
 * Split out of `app/charts.js` in v0.18.0, at the module-graph ceiling: F133's fix took that
 * file to 18 382 bytes against 17 579, and the guard asked for a seam. It has chosen six before
 * this one, and each time the right answer was a subject rather than a slice.
 *
 * **The subject is the question the mark answers.** `Series` in `charts.js` answers *"how did
 * this change over time"* — it has a time axis, a gap is a hole in it, and its summary is either
 * the latest reading or the window's total. `Bars` here answers *"how do these compare right
 * now"*: no time axis, no gaps, and the categories are names rather than instants. A
 * reader asking "why is this bar longer" and a reader asking "why did this dip at 14:05" are
 * looking for different code.
 *
 * The shared chrome — `Legend`, `Caption`, `Unmeasured`, the rounding — stays in `charts.js` and
 * is imported from here. One direction, no cycle, and one implementation of the rules that make
 * an unmeasured value render as an absence rather than as a zero.
 */

import { html, cx } from "./dom.js";
import { unitText } from "./chartdata.js";
import { Absent, Caption } from "./charts.js";

/**
 * **Horizontal bars, with the value as text beside each one.**
 *
 * Horizontal because a category is a name and a name needs room: five vertical bars at 390 px carry
 * five rotated labels or five truncated ones, and the operator's question here is *which element*,
 * which is the label.
 *
 * The value is **printed**, not only drawn. A bar answers *how much bigger* at a glance and cannot
 * answer *how much*, and during an incident the second one goes in the ticket.
 */
export function Bars({ title, hint, rows, unit = "", source, span, note, max }) {
  const readable = (rows || []).filter((r) => r.value != null);
  if (!readable.length) {
    return html`<${Absent} title=${title} source=${source} span=${span}
      why=${note || "nothing to rank yet"} />`;
  }
  const top = max != null ? max : Math.max(...readable.map((r) => Number(r.value))) || 1;
  return html`<section class="chart-block">
    <h4 class="chart-title">${title}</h4>
    ${hint ? html`<p class="hint">${hint}</p>` : null}
    <div class="chart chart-bars" data-chart="bars" role="img"
         aria-label=${`${title}. ` +
           `${readable.map((r) => `${r.label}: ${unitText(r.value, unit)}`).join(", ")}.`}>
      ${(rows || []).map((row) => html`<div class="chart-bar-row" key=${row.key ?? row.label}>
        <span class="chart-bar-label" title=${row.title}>${row.face ?? row.label}</span>${" "}
        <span class="chart-bar-track">
          <span class=${cx("chart-bar-fill", row.tone && `chart-${row.tone}`)}
                style=${`width:${row.value == null ? 0 : ((row.value / top) * 100).toFixed(2)}%`}
          ></span>
        </span>${" "}
        <span class="chart-bar-value">${unitText(row.value, unit)}</span>
      </div>`)}
    </div>
    <${Caption} source=${source} span=${span} note=${note} />
  <//>`;
}
