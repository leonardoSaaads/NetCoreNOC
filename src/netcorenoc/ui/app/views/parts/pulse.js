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
import { Failed, SectionHeading, severityTone } from "../../widgets.js";
import { Series } from "../../charts.js";
import { Bars } from "../../compare.js";
import { NetGraph, URGENT_AT } from "../../netgraph.js";
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

/** How many elements the top-elements bars rank. Enough to act on, short enough to read. */
const TOP_N = 5;


/**
 * **What is happening** — alarms raised per bucket, **one line per severity band** (v0.22.0).
 *
 * v0.20.0 drew raises against clears here, and the maintainer read it as broken: a red column at
 * the right edge and a green hairline along the floor. The question the panel is under is *how
 * bad*, so the lines are the bands the card beside it counts — and **`unplaced` is a band of its
 * own, labelled**, rather than a remainder that vanishes. On an estate whose devices send no
 * severity word it is the tallest line and the others lie flat; that is the chart telling the
 * truth about the estate, not a rendering fault (item 4).
 *
 * Counted in SQL over the range chosen above (`/api/activity/severity`), resolved per class with
 * the census's precedence, so a band here means what it means on the card.
 */
export function Happening({ data, rangeS, error, retry }) {
  if (error) {
    return html`<${Failed} error=${error} retry=${retry} what="recent alarm activity" />`;
  }
  const series = (data && data.series) || {};
  const n = data ? data.buckets : 0;
  const labels = n
    ? Array.from({ length: n }, (_, i) => clock(data.from + (i + 0.5) * data.bucket_s, rangeS))
    : [];
  const lines = [...SCALE, UNPLACED]
    .filter((b) => series[b.key])
    .map((b) => ({ name: b.label, tone: severityTone(b.key), shape: b.shape, values: series[b.key] }));
  if (series.vendor) {
    lines.push({ name: "vendor scale", tone: severityTone("vendor"), shape: "square",
                 values: series.vendor });
  }
  const total = lines.reduce((sum, one) => sum + one.values.reduce((a, b) => a + b, 0), 0);
  return html`<${Series} title="Alarms raised, by severity" mark="line" total=${true}
    series=${lines} labels=${labels}
    source=${data ? `${count(total)} raised` : "reading…"}
    span=${data ? `per ${spanText(data.bucket_s)}` : null}
    note=${TIMEZONE} />`;
}

/** Elements the Overview's graph draws: the whole estate when it is small, the busiest otherwise. */
export const CARD_NODES = 40;

/**
 * **Where it is happening** — the five busiest, and how the estate is connected (v0.22.0, item 6).
 *
 * The grid of every element became the graph: the maintainer wanted the relationships on the first
 * screen. It is `app/netgraph.js` at card size — hand-written, deterministic, asserted by the
 * harness, never the d3 scene — and an element opens on the Graph screen with its panel. Past
 * `CARD_NODES` elements it draws the busiest, which is the question this card answers.
 */
export function Estate({ nodes, edges }) {
  const rows = [...nodes]
    .filter((node) => node.active_alarms > 0)
    .sort((a, b) => b.active_alarms - a.active_alarms
      || String(a.id).localeCompare(String(b.id)))
    .slice(0, TOP_N)
    .map((node) => ({
      key: String(node.id),
      label: node.label || node.ip,
      value: node.active_alarms,
      tone: node.active_alarms >= URGENT_AT ? "alarm" : "warn",
    }));
  const drawn = nodes.length <= CARD_NODES
    ? nodes
    : [...nodes].sort((a, b) => b.active_alarms - a.active_alarms).slice(0, CARD_NODES);
  return html`<section class="panel-block">
    <${SectionHeading} title="Where it is happening" />
    <${Bars} title=${`Busiest ${TOP_N}`} rows=${rows} unit="alarms" source="active now" />
    <${NetGraph} compact nodes=${drawn} edges=${edges || []}
      onSelect=${(id) => { globalThis.location.hash = `#/graph?ne=${id}`; }} />
    <p class="chart-caption">
      ${drawn.length < nodes.length ? `the busiest ${count(drawn.length)} of ${count(nodes.length)} · ` : ""}
      <a href="#/graph">open the graph</a>
    </p>
  </section>`;
}
