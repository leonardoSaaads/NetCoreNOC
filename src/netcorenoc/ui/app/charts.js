/* The chart vocabulary: three types, hand-written, with the honesty rules built in.
 *
 * ## Why this module exists at all (DECISIONS #305, #307)
 *
 * Four screens stopped describing and started showing, and the rules those charts obey are not
 * rules a caller can be trusted to remember:
 *
 *   * **a gap breaks the line** — never a dip to zero, never an interpolation across a period
 *     nobody measured;
 *   * **an unreadable metric renders `—` and says "not measured"** — never `0`;
 *   * **the axis is the span the data actually covers**, derived from the data and never from the
 *     window that was asked for;
 *   * **the number printed beside a chart comes from the same array the chart was drawn from**, in
 *     the same function, so the two cannot disagree.
 *
 * The arithmetic behind the first three is in `chartdata.js`, which is pure and driven directly by
 * tests. `health.js` imports `runs` from there too, because a second implementation of *"where does
 * the line break"* is how two charts come to disagree about it.
 *
 * ## Three types, and no SVG text anywhere (DECISIONS #305)
 *
 * **`Series`** — a time axis carrying `line` or `column` marks. **`Bars`** — horizontal categorical
 * magnitudes. **`Map`** — a deterministic grid, one cell per element.
 *
 * Only `Series` draws SVG, and it draws **geometry only**: a `viewBox` of `0 0 100 40` with
 * `preserveAspectRatio="none"`, exactly as the sparkline does, so the plot stretches to whatever
 * box it lands in with no measurement, no resize listener and no layout pass. **Every label is
 * HTML.** That is not a stylistic choice: an `<svg><text>` inside a stretched `viewBox` scales with
 * the box, so one chart would render 9 px type at 390 px and 33 px at 1440 and the axis would be
 * illegible at one end. HTML labels stay at the stylesheet's size at every width, are selectable,
 * are read by a screen reader, and are visible to the DOM harness.
 *
 * `Bars` and `Map` are HTML for the same reason and one more: a horizontal bar is a box with a
 * width, which is what `.meter-fill` has been since v0.15.2. Drawing it in SVG would be a second
 * way to say `width: 42%`.
 *
 * ## What is deliberately not here
 *
 * No dependency, no `<canvas>`, no interaction model, no configuration object. A chart takes the
 * numbers, the unit, and the sentence saying where the numbers came from. Anything else a caller
 * wants to vary is a caller writing a chart.
 */

import { html, cx } from "./dom.js";
import { count } from "./format.js";
import { ceiling, runs, unitText } from "./chartdata.js";

/** The plot's height in user units. The rendered height is the stylesheet's; this is arithmetic. */
const H = 40;

/**
 * **What a chart renders instead of a number nothing measures.**
 *
 * `—` and the word, never a zero — and never an empty box either: an empty chart and a chart of
 * zeroes are both indistinguishable from a broken one, which is the mistake #289 was written about
 * and the reason the health control says "not measured" in words.
 */
export function Unmeasured({ what, why }) {
  return html`<div class="chart-unmeasured" role="img" aria-label=${`${what}: not measured`}>
    ${/* The explicit spaces are F112 again. `role="img"` with an `aria-label` means assistive
          tech reads the label and ignores these children, so the announcement was always right —
          but `textContent` read `—Over-merge ratenot measured`, which is what a copy-paste gets
          and what anyone reading a DOM dump sees. Three spans on three lines are three boxes, not
          three words. */ null}
    <span class="chart-dash" aria-hidden="true">—</span>${" "}
    <span class="chart-unmeasured-what">${what}</span>${" "}
    <span class="chart-unmeasured-why">not measured${why ? ` — ${why}` : ""}</span>
  </div>`;
}

/** The series names, so a multi-series chart is never colour alone. */
function Legend({ series }) {
  if (series.length < 2) return null;
  return html`<ul class="chart-legend">
    ${series.map((one) => html`<li key=${one.name}>
      <i class=${cx("chart-swatch", one.tone && `chart-${one.tone}`)} aria-hidden="true"></i>
      ${one.name}
    </li>`)}
  </ul>`;
}

/**
 * The sentence under every chart: where the numbers came from, and over what.
 *
 * Required rather than optional. A chart whose source nobody can name is a chart nobody can check,
 * and the whole difficulty of this release is that a wrong axis renders perfectly.
 */
function Caption({ source, span, note }) {
  return html`<p class="chart-caption">
    <span class="chart-source">${source}</span>
    ${span ? html` <span class="chart-span">· ${span}</span>` : null}
    ${note ? html` <span class="chart-note">· ${note}</span>` : null}
  </p>`;
}

/** The header a chart with nothing readable in it renders. One shape, three call sites. */
function Absent({ title, why, source, span }) {
  return html`<section class="chart-block">
    <h4 class="chart-title">${title}</h4>
    <${Unmeasured} what=${title} why=${why} />
    <${Caption} source=${source} span=${span} />
  <//>`;
}

/**
 * **A quantity over time.** `line` for something continuous that was sampled; `column` for a count
 * per bucket.
 *
 * `series` is `[{ name, tone, values }]`, every series sharing one bucket grid — `values[i]` is the
 * same instant in every one of them, and `null` means *this bucket was not measured*. A bucket that
 * was measured and held nothing is `0`. That difference is the whole point of the type: a sampler
 * that missed a window and an estate that was quiet are different facts, and a chart that drew them
 * alike would be lying about one of them.
 *
 * `labels[i]` is the clock text for bucket `i`, from `chartdata.buckets`. **Three ticks are
 * rendered** — first, middle, last — because a phone has room for three and the operator's question
 * is *when did it start*, which three answers.
 *
 * The **latest reading is printed in the header from `values` itself**, so the number beside the
 * chart cannot disagree with the chart.
 */
export function Series({
  title, hint, series, mark = "line", unit = "", source, span, note, max, labels = [], height,
}) {
  const lines = (series || []).filter((one) => (one.values || []).some((v) => v != null));
  if (!lines.length) {
    return html`<${Absent} title=${title} source=${source} span=${span}
      why=${note || "no reading has arrived yet"} />`;
  }
  // The domain. A percentage is pinned to 0-100 because a CPU chart that rescaled to its own peak
  // would draw a busy minute and an idle one as the same picture; everything else takes a round
  // ceiling over every series, so two series on one chart are comparable by construction.
  const observed = Math.max(
    ...lines.flatMap((one) => (one.values || []).filter((v) => v != null).map(Number)),
  );
  const top = max != null ? max : unit === "%" ? 100 : ceiling(observed);
  const n = Math.max(...lines.map((one) => (one.values || []).length));
  const latest = lines.map((one) => {
    const readable = (one.values || []).filter((v) => v != null);
    return { name: one.name, value: readable.length ? readable[readable.length - 1] : null };
  });
  const ticks = [0, Math.floor((n - 1) / 2), n - 1]
    .filter((i, at, all) => i >= 0 && all.indexOf(i) === at)
    .map((i) => ({ at: n < 2 ? 0 : (i / (n - 1)) * 100, text: labels[i] ?? "" }))
    .filter((t) => t.text);

  return html`<section class="chart-block">
    <div class="chart-head">
      <h4 class="chart-title">${title}</h4>
      <p class="chart-latest">
        ${latest.map((one, i) => html`<span key=${one.name}>
          ${i ? html`<span class="chart-sep"> · </span>` : null}
          ${latest.length > 1 ? html`<span class="chart-latest-name">${one.name} </span>` : null}
          <b>${unitText(one.value, unit)}</b>
        </span>`)}
      </p>
    </div>
    ${hint ? html`<p class="hint">${hint}</p>` : null}
    <div class=${cx("chart", `chart-${mark}`)} data-chart=${mark} role="img"
         aria-label=${ariaFor(title, latest, unit, span)}
         style=${height ? `--chart-h:${height}px` : null}>
      <div class="chart-plot">
        <svg viewBox=${`0 0 100 ${H}`} preserveAspectRatio="none" focusable="false"
             aria-hidden="true">
          ${mark === "column"
            ? lines.map((one, s) => html`<g key=${one.name}
                 class=${cx("chart-cols", one.tone && `chart-${one.tone}`)}>
                ${columns(one.values, top, lines.length, s)}
              <//>`)
            : lines.map((one) => html`<g key=${one.name}
                 class=${cx("chart-line", one.tone && `chart-${one.tone}`)}>
                ${runs(one.values, { max: top, height: H }).map((r, i) =>
                  html`<polyline key=${i} points=${r.join(" ")} />`)}
              <//>`)}
        <//>
        <div class="chart-y" aria-hidden="true">
          <span class="chart-y-top">${unitText(top, unit)}</span>
          <span class="chart-y-zero">0</span>
        </div>
      </div>
      <div class="chart-x" aria-hidden="true">
        ${ticks.map((t) => html`<span key=${t.text} class="chart-x-tick"
             style=${`left:${t.at.toFixed(2)}%`}>${t.text}</span>`)}
      </div>
    </div>
    <${Legend} series=${lines} />
    <${Caption} source=${source} span=${span} note=${note} />
  <//>`;
}

/** One `<rect>` per readable bucket. A `null` bucket draws NOTHING, which is the gap. */
function columns(values, top, seriesCount, seriesIndex) {
  const list = values || [];
  const slot = 100 / Math.max(1, list.length);
  const bar = (slot * 0.78) / Math.max(1, seriesCount);
  const out = [];
  list.forEach((value, index) => {
    if (value == null) return;
    const h = (Math.max(0, Math.min(top, value)) / (top || 1)) * H;
    // A readable zero still draws a hairline, so "measured and empty" is visibly not "not
    // measured". Without it a quiet hour and a sampler outage are the same blank column.
    const drawn = Math.max(h, value === 0 ? 0.4 : 0.6);
    out.push(html`<rect key=${index}
      x=${(index * slot + slot * 0.11 + bar * seriesIndex).toFixed(2)}
      y=${(H - drawn).toFixed(2)} width=${bar.toFixed(2)} height=${drawn.toFixed(2)} />`);
  });
  return out;
}

/** The accessible name: what it is, what it reads now, over what. Never the word "chart". */
function ariaFor(title, latest, unit, span) {
  const readings = latest
    .map((one) => `${one.name}: ${one.value == null ? "not measured" : unitText(one.value, unit)}`)
    .join(", ");
  return `${title}. Latest ${readings}${span ? `, over ${span}` : ""}.`;
}

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
    ${hidden > 0
      ? html`<p class="hint">${count(hidden)} quieter ${hidden === 1 ? "element" : "elements"}
          are not drawn. The grid is sorted by load, so what is missing is the quiet end.</p>`
      : null}
    <${Caption} source=${source} note=${note} />
  <//>`;
}
