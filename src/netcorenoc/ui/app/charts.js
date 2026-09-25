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
 * **`Series`** — a time axis carrying `line` or `column` marks, and it is what this file holds.
 * **`Bars`** (horizontal categorical magnitudes) and **`Map`** (a deterministic grid, one cell
 * per element) moved to `compare.js` in v0.18.0 at the module-graph ceiling; they answer
 * *"how do these compare right now"* rather than *"how did this change"*, and they import the
 * shared chrome from here.
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
import { SeverityShape } from "./widgets.js";

// Re-exported for `compare.js`, which draws the categorical marks and needs the same
// rounding rule: two chart families that round differently are two chart families an
// operator cannot compare (v0.18.0).
export { ceiling };

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

/**
 * The series names, so a multi-series chart is never colour alone. A series carrying a `shape`
 * (a severity band) shows that band's shape and a sample of its line's dash pattern as well, so
 * the plot's lines are told apart by dash and the legend by shape and word — three encodings.
 */
export function Legend({ series }) {
  if (series.length < 2) return null;
  return html`<ul class="chart-legend">
    ${series.map((one) => html`<li key=${one.name} class=${cx(one.tone && `chart-${one.tone}`)}>
      ${one.shape
        ? html`<${SeverityShape} shape=${one.shape} />
            <svg class="chart-dash-sample" viewBox="0 0 16 4" aria-hidden="true" focusable="false">
              <line x1="0" y1="2" x2="16" y2="2" /></svg>`
        : html`<i class=${cx("chart-swatch", one.tone && `chart-${one.tone}`)}
               aria-hidden="true"></i>`}
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
export function Caption({ source, span, note }) {
  return html`<p class="chart-caption">
    <span class="chart-source">${source}</span>
    ${span ? html` <span class="chart-span">· ${span}</span>` : null}
    ${note ? html` <span class="chart-note">· ${note}</span>` : null}
  </p>`;
}

/** The header a chart with nothing readable in it renders. One shape, three call sites. */
export function Absent({ title, why, source, span }) {
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
/**
 * The ceiling for a percentage axis: the smallest band that contains the data.
 *
 * **This replaces a hard 0-100, and the reason is a live pass.** The Overview's CPU and memory
 * charts ran at 5-6 % against a fixed 100 % ceiling, which draws a straight line along the floor:
 * two hours of readings, no shape, nothing an operator could act on. Three of the four charts in
 * that panel were unreadable for the same reason.
 *
 * The old ceiling was defended on the ground that *"a CPU chart that rescaled to its own peak
 * would draw a busy minute and an idle one as the same picture"*, and that objection is right
 * about rescaling to the peak. It is answered by the bands rather than by refusing to rescale:
 * the top of the axis is one of five fixed values and **`.chart-y-top` prints it**, so an idle
 * appliance and a busy one differ in the label as well as in the shape, and nothing is implied
 * that the number beside it does not say. A series that reaches half the scale keeps the full
 * 0-100, because near the top the whole is the comparison that matters.
 */
export function percentTop(observed) {
  const bands = [5, 10, 25, 50, 100];
  return bands.find((band) => observed <= band) ?? 100;
}

export function Series({
  title, hint, series, mark = "line", unit = "", source, span, note, max, labels = [], height,
  total = false,
}) {
  // **A `line` needs TWO readings and a `column` needs one**, and that difference is a defect the
  // live pass found: `some(v => v != null)` let a one-point series through, so a freshly started
  // appliance drew an axis, printed `2.8%` beside it, and left the plot **empty**. An empty plot
  // inside a chart's frame is the "chart of zeroes" problem wearing a better costume — #289's rule
  // is that an absence is stated, and "one reading so far" is the statement.
  const enough = mark === "line" ? 2 : 1;
  const readable = (one) => (one.values || []).filter((v) => v != null).length;
  const lines = (series || []).filter((one) => readable(one) >= enough);
  if (!lines.length) {
    const partial = (series || []).some((one) => readable(one) > 0);
    return html`<${Absent} title=${title} source=${source} span=${span}
      why=${partial
        ? "only one reading so far — a line needs two"
        : note || "no reading has arrived yet"} />`;
  }
  // The domain. Everything but a percentage takes a round ceiling over every series, so two
  // series on one chart are comparable by construction.
  const observed = Math.max(
    ...lines.flatMap((one) => (one.values || []).filter((v) => v != null).map(Number)),
  );
  const top = max != null ? max : unit === "%" ? percentTop(observed) : ceiling(observed);
  const n = Math.max(...lines.map((one) => (one.values || []).length));
  // **What the header number means depends on what the series IS** (v0.18.0, F133).
  //
  // Found by looking at the Overview beside the database. The header read
  // `new 1 · being worked 0 · resolved 1` while the plot beside it drew three resolved columns
  // and `/api/situations?status=resolved` returned three rows. Both were "right": the header was
  // the LAST BUCKET's count, printed with no word saying so.
  //
  // A `line` series is a gauge sampled over time — CPU, queue depth — and its last reading is the
  // only honest summary; a total would be meaningless. A `column` series here is a COUNT PER
  // BUCKET, and the summary an operator reads as "how many" is the sum over the window. Summing a
  // gauge and taking the last count are each the other's defect, so the mark decides, and
  // `summary` names which was taken so the two can never again be read as the same thing.
  // `total` says a LINE is also a count per bucket (the severity lines, v0.22.0), so its
  // summary is the sum like a column's, not the last bucket's reading.
  const perBucketCount = mark === "column" || total;
  const perSeries = lines.map((one) => {
    const readable = (one.values || []).filter((v) => v != null);
    if (!readable.length) return { name: one.name, value: null };
    const value = perBucketCount
      ? readable.reduce((sum, v) => sum + Number(v), 0)
      : readable[readable.length - 1];
    return { name: one.name, value };
  });
  // A `total` chart of many series prints ONE sum: the legend already names each series, and a
  // header repeating six of them wrapped onto three lines at 1440 px (v0.22.0).
  const latest = total && perSeries.length > 2
    ? [{ name: "all", value: perSeries.reduce((sum, one) => sum + (one.value ?? 0), 0) }]
    : perSeries;
  const summary = perBucketCount ? "Total" : "Latest";
  const ticks = [0, Math.floor((n - 1) / 2), n - 1]
    .filter((i, at, all) => i >= 0 && all.indexOf(i) === at)
    .map((i) => ({ at: n < 2 ? 0 : (i / (n - 1)) * 100, text: labels[i] ?? "" }))
    .filter((t) => t.text);

  return html`<section class="chart-block">
    <div class="chart-head">
      <h4 class="chart-title">${title}</h4>
      <p class="chart-latest">
        <span class="chart-latest-kind">${summary} </span>
        ${latest.map((one, i) => html`<span key=${one.name}>
          ${i ? html`<span class="chart-sep"> · </span>` : null}
          ${latest.length > 1 ? html`<span class="chart-latest-name">${one.name} </span>` : null}
          <b>${unitText(one.value, unit)}</b>
        </span>`)}
      </p>
    </div>
    ${hint ? html`<p class="hint">${hint}</p>` : null}
    <div class=${cx("chart", `chart-${mark}`)} data-chart=${mark} role="img"
         aria-label=${ariaFor(title, latest, unit, span, summary)}
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
        ${/* No inline `left`: `.chart-x` lays these out with `space-between`, which puts the
              first, middle and last bucket exactly where the percentages did and cannot overlap
              when the chart is narrow (F142). `at` stays on the tick because it is what says the
              positions are evenly spaced rather than arbitrary. */ null}
        ${ticks.map((t) => html`<span key=${t.text} class="chart-x-tick"
             data-at=${t.at.toFixed(2)}>${t.text}</span>`)}
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

/** The accessible name: what it is, what it reads now, over what. Never the word "chart".
 *
 * `summary` is "Latest" or "Total" and comes from the caller rather than being assumed here, so
 * a screen reader is told the same thing the sighted header says (F133). Hardcoding "Latest"
 * is what made the two disagree.
 */
function ariaFor(title, latest, unit, span, summary = "Latest") {
  const readings = latest
    .map((one) => `${one.name}: ${one.value == null ? "not measured" : unitText(one.value, unit)}`)
    .join(", ");
  return `${title}. ${summary} ${readings}${span ? `, ${span}` : ""}.`;
}
