/* The two charts that compare things to each other, rather than to their own past.
 *
 * Split out of `app/charts.js` in v0.18.0, at the module-graph ceiling: F133's fix took that
 * file to 18 382 bytes against 17 579, and the guard asked for a seam. It has chosen six before
 * this one, and each time the right answer was a subject rather than a slice.
 *
 * **The subject is the question the mark answers.** `Series` in `charts.js` answers *"how did
 * this change over time"* — it has a time axis, a gap is a hole in it, and its summary is either
 * the latest reading or the window's total. `Bars` and `Map` here answer *"how do these compare
 * right now"*: no time axis, no gaps, and the categories are names rather than instants. A
 * reader asking "why is this bar longer" and a reader asking "why did this dip at 14:05" are
 * looking for different code.
 *
 * The shared chrome — `Legend`, `Caption`, `Unmeasured`, the rounding — stays in `charts.js` and
 * is imported from here. One direction, no cycle, and one implementation of the rules that make
 * an unmeasured value render as an absence rather than as a zero.
 */

import { html, cx } from "./dom.js";
import { count } from "./format.js";
import { ceiling, unitText } from "./chartdata.js";
import { Absent, Caption, Legend, Unmeasured } from "./charts.js";

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
        <span class="chart-bar-label" title=${row.title}>${row.label}</span>${" "}
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

/**
 * **The estate, as a deterministic grid — one cell per element** (DECISIONS #310).
 *
 * The question the force drawing cannot answer: *is this one element or the whole estate?* Two
 * independent reasons, both measured. The node radius saturates at **47** active alarms, so an
 * element carrying 1 359 and one carrying 501 draw at exactly the same 24 px; and the layout comes
 * from a force simulation with drag, so two glances at an unchanged estate do not agree.
 *
 * This is a pure function of the payload — sorted by load, then by key — so the same estate draws
 * the same grid every time and an operator can compare this morning with now.
 *
 * **It is a map of LOAD and not of topology**, and the caption says so. This appliance learns
 * affinity from co-occurrence and has no physical topology to draw.
 *
 * Three encodings per cell, none of them alone: the **band** (a four-step ramp), the **share bar**
 * inside the cell, and the **count as text**. The busiest band also carries `chart-urgent`, whose
 * pulse is a CSS animation and is therefore off under `prefers-reduced-motion` by construction
 * (DECISIONS #309) — and whose ring is 3 px against 1 px, so the cell stays marked with motion off.
 */
export function Map({ title, hint, cells, source, note, cap = 60, urgentAt }) {
  const all = [...(cells || [])].sort(
    (a, b) => (b.value ?? 0) - (a.value ?? 0) || String(a.key).localeCompare(String(b.key)),
  );
  if (!all.length) {
    return html`<${Absent} title=${title} source=${source}
      why=${note || "the appliance has heard from no element yet"} />`;
  }
  const shown = all.slice(0, cap);
  const hidden = all.length - shown.length;
  const top = Math.max(...all.map((c) => Number(c.value) || 0)) || 1;
  const total = all.reduce((sum, c) => sum + (Number(c.value) || 0), 0);
  const urgent = urgentAt == null ? Infinity : urgentAt;
  return html`<section class="chart-block">
    <h4 class="chart-title">${title}</h4>
    ${hint ? html`<p class="hint">${hint}</p>` : null}
    <div class="chart chart-map" data-chart="map" role="img"
         aria-label=${`${title}. ${shown.length} of ${all.length} elements, ` +
                      `busiest ${shown[0].label} at ${count(shown[0].value ?? 0)}.`}>
      ${shown.map((cell) => {
        const value = Number(cell.value) || 0;
        const band = value === 0 ? 0 : value >= top * 0.5 ? 3 : value >= top * 0.2 ? 2 : 1;
        return html`<div key=${cell.key}
             class=${cx("map-cell", `map-band-${band}`, value >= urgent && "chart-urgent")}
             title=${`${cell.label}: ${count(value)} active ` +
                     `${value === 1 ? "alarm" : "alarms"}` +
                     `${total ? ` — ${((value / total) * 100).toFixed(1)}% of the estate's load` : ""}`}>
          ${/* The explicit spaces are F112, third occurrence. These are grid ROWS, so the
                pixels were always right — and `textContent` read `127.0.0.11,458`, which a screen
                reader announces as one number and which could be misread as an address. A layout
                that separates boxes does not separate text. */ null}
          <span class="map-name">${cell.label}</span>${" "}
          <span class="map-share" aria-hidden="true">
            <span class="map-share-fill" style=${`width:${((value / top) * 100).toFixed(1)}%`}
            ></span>
          </span>${" "}
          <span class="map-value">${count(value)}</span>
        <//>`;
      })}
    </div>
    ${/* The `${" "}` below, and not the newline after it: `htm` drops a whitespace-only run
          between an interpolation and the text that follows, so this rendered
          `23 quieter elementsare not drawn`. F112's lesson, found by reading the Overview rather
          than the source — the template looks correct and the DOM is not. */ null}
    ${hidden > 0
      ? html`<p class="hint">${count(hidden)} quieter ${hidden === 1 ? "element" : "elements"}${" "}
          are not drawn. The grid is sorted by load, so what is missing is the quiet end.</p>`
      : null}
    <${Caption} source=${source} note=${note} />
  <//>`;
}
