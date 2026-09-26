/* The Overview's network bands: what is happening, where, and which element is worst.
 *
 * Split out of `views/overview.js` in v0.16.6 because that file reached **22 552 bytes** against
 * the module graph's ceiling of 17 579 — one third of the v0.12.0 `app.js` this console replaced —
 * and **split again in v0.16.7** (#318), at 17 095 bytes with 484 to spare, when this release
 * needed to add a band above everything here.
 *
 * The second seam is a subject, not a size: this file is about **the network**, and
 * `views/parts/keeping.js` is about **the appliance**. `views/parts/severity.js` holds the band
 * that now comes first, because *how bad is it* is a different question from *what is happening*
 * and the answer to the first is a count while the answer to the second is a shape.
 *
 * All three chart types come from `app/charts.js` and every series' arithmetic from
 * `app/chartdata.js`, so the rules — a gap breaks the line, an unmeasured metric renders `—`, the
 * axis is the span the data covers — are properties of the primitives rather than of this file.
 */

import { html } from "../../dom.js";
import { Failed, SectionHeading } from "../../widgets.js";
import { StackedArea } from "../../stack.js";
import { NetGraph } from "../../netgraph.js";
import { clock, spanText } from "../../chartdata.js";
import { SCALE, UNPLACED, count, TIMEZONE } from "../../format.js";

/**
 * The ranges the Overview offers, and the default.
 *
 * Here rather than in the view because the chart's axis and the picker's labels have to mean the
 * same thing, and two lists of durations is how they come to differ by one entry.
 */
export const RANGES = [
  { label: "15m", seconds: 15 * 60 },
  { label: "1h", seconds: 60 * 60 },
  { label: "2h", seconds: 2 * 60 * 60 },
  { label: "6h", seconds: 6 * 60 * 60 },
  { label: "12h", seconds: 12 * 60 * 60 },
  { label: "1d", seconds: 24 * 60 * 60 },
  { label: "3d", seconds: 3 * 24 * 60 * 60 },
  { label: "7d", seconds: 7 * 24 * 60 * 60 },
];

/** Two hours: the window the health sampler already keeps, so the two panels agree by default. */
export const DEFAULT_RANGE_S = 2 * 60 * 60;

/** Columns in the activity chart. Twenty-four reads at 390 px and resolves at 1440. */
export const RANGE_BUCKETS = 24;

/** The range picker: eight buttons, the current one pressed. */
export function RangePicker({ value, onPick }) {
  return html`<div class="ranges" role="group" aria-label="How far back to look">
    ${RANGES.map((r) => html`<button type="button" key=${r.label}
        class=${r.seconds === value ? "range on" : "range"}
        aria-pressed=${r.seconds === value}
        onClick=${() => onPick(r.seconds)}>${r.label}</button>`)}
  </div>`;
}


/**
 * **What is happening** — alarms ACTIVE over the range, stacked by severity (v0.23.0, #393).
 *
 * v0.22.0 plotted alarms RAISED per bucket, and the maintainer read the moment ten critical alarms
 * appeared as a drop to zero: a fault that keeps firing re-reports an existing alarm and raises
 * nothing, so the flow was empty while the stock was at its highest. The chart is the stock now —
 * how many are active at each point, stacked by band with the most severe at the bottom — so its
 * top edge is the total and its last point is the number on the severity card beside it.
 * `unplaced` is a band of its own, labelled, never folded into another.
 */
export function Happening({ data, rangeS, error, retry }) {
  if (error) {
    return html`<${Failed} error=${error} retry=${retry} what="active alarms over time" />`;
  }
  const series = (data && data.series) || {};
  const n = data ? data.buckets : 0;
  // A point is the count at its bucket's END, so it is labelled there; the last one is "now".
  const labels = n
    ? Array.from({ length: n }, (_, i) => clock(data.from + (i + 1) * data.bucket_s, rangeS))
    : [];
  const bands = [...SCALE, UNPLACED]
    .filter((b) => series[b.key])
    .map((b) => ({ key: b.key, label: b.label, level: b.level, values: series[b.key] }));
  if (series.vendor) {
    bands.push({ key: "vendor", label: "vendor scale", level: 1, values: series.vendor });
  }
  return html`<${StackedArea} title="Active alarms, by severity" bands=${bands} labels=${labels}
    source=${data ? "active at each point" : "reading…"}
    span=${data ? `every ${spanText(data.bucket_s)}` : null}
    note=${TIMEZONE} />`;
}

/** Elements the Overview's graph draws: the whole estate when it is small, the busiest otherwise. */
export const CARD_NODES = 40;

/**
 * **The estate** — how the elements are connected, busiest first (v0.22.0 item 6; v0.23.0).
 *
 * `app/netgraph.js` at card size — hand-written, deterministic, asserted by the harness — and an
 * element opens on the Graph screen with its panel. Past `CARD_NODES` elements it draws the
 * busiest. The "busiest five" bars that sat above it became the Top 10 card (#393).
 */
export function Estate({ nodes, edges }) {
  const drawn = nodes.length <= CARD_NODES
    ? nodes
    : [...nodes].sort((a, b) => b.active_alarms - a.active_alarms).slice(0, CARD_NODES);
  return html`<section class="panel-block estate">
    <${SectionHeading} title="The estate" />
    <${NetGraph} compact nodes=${drawn} edges=${edges || []}
      onSelect=${(id) => { globalThis.location.hash = `#/graph?ne=${id}`; }} />
    <p class="chart-caption">
      ${drawn.length < nodes.length ? `the busiest ${count(drawn.length)} of ${count(nodes.length)} · ` : ""}
      <a href="#/graph">open the graph</a>
    </p>
  </section>`;
}
