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
import { Series } from "../../charts.js";
import { Bars, Map as EstateMap } from "../../compare.js";
import { clock, spanText } from "../../chartdata.js";
import { count, TIMEZONE } from "../../format.js";

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
 * The load at which an estate cell starts pulsing (DECISIONS #309).
 *
 * **47 is not arbitrary**: it is where the network graph's node radius saturates —
 * `min(24, 7 + 2.5 * sqrt(n))` reaches its 24 px ceiling there (F77) — so it is exactly the load
 * above which the other projection stops being able to tell two elements apart. Marking the same
 * threshold in both places is what makes the two drawings agree about which elements are urgent.
 */
export const URGENT_AT = 47;

/**
 * **What is happening** — one chart, over a range the operator chose.
 *
 * ## What this replaced, and why (v0.20.0, F144)
 *
 * Two column charts sat here. The first plotted *situations by creation time* over **the 50 most
 * recent situations the live list carries**; the second plotted raises and clears by fetching
 * `/api/timeline?limit=1000` and counting in the browser. Both drew their own axis from whatever
 * span their own data happened to cover, so the two charts under one heading were usually over
 * two different periods, neither of them stated in a unit an operator picked — which is what
 * made the panel read as blocks rather than as time passing.
 *
 * The thousand-row read was also 108.5 KiB on every load to produce twenty-four numbers, and it
 * was **truncated**: on the measured estate those thousand marks spanned 15.6 seconds of one
 * storm out of 1 963 raises. `Worst` below already recorded that a chart labelled "last 7 days"
 * over that data would be a lie, and deferred the fix to "a later release" with a `GROUP BY` in
 * it. This is that release: `/api/timeline?buckets=…&range_s=…` counts in SQL, so the axis is
 * the range that was asked for and the counts are all of it.
 *
 * One chart, because raises against clears is the question — five hundred raises with three
 * clears and five hundred with 495 are opposite situations and no counter distinguishes them.
 * Whether situations arrive in a burst is answered by the list beside it.
 */
export function Happening({ data, rangeS, error, retry }) {
  if (error) {
    return html`<${Failed} error=${error} retry=${retry} what="recent alarm activity" />`;
  }
  const series = (data && data.series) || null;
  const n = series ? series.raises.length : 0;
  const bucketS = (data && data.bucket_s) || 0;
  // Ticks are clock times counted back from the end of the range, so the axis is the range the
  // operator chose rather than the span the rows happened to cover.
  const labels = n && data
    ? Array.from({ length: n }, (_, i) => clock(data.from + (i + 0.5) * bucketS, rangeS))
    : [];
  const total = series ? series.raises.reduce((a, b) => a + b, 0) : 0;
  const cleared = series ? series.clears.reduce((a, b) => a + b, 0) : 0;
  return html`<${Series} title="Alarm activity" mark="column"
    series=${[
      { name: "raised", tone: "alarm", values: series ? series.raises : [] },
      { name: "cleared", tone: "quiet", values: series ? series.clears : [] },
    ]}
    labels=${labels}
    source=${series
      ? `${count(total)} raised, ${count(cleared)} cleared`
      : "reading…"}
    span=${bucketS ? `one column per ${spanText(bucketS)}` : null}
    note=${TIMEZONE} />`;
}

/**
 * **The estate** — the grid and the ranking, in one card.
 *
 * They were two panels, each taking a full row, and they are two views of one array: one drew
 * every element as a cell, the other ranked the busiest five of the same elements. An operator
 * reads them together — *is it everywhere or somewhere, and if somewhere, which* — so they are
 * one card under one heading. That is a row of the Overview saved and a question answered once.
 *
 * **No topology is drawn here, and the caption links to the one that is** (v0.16.7, #317).
 * Measured on the three-scenario estate, `edge` holds one row of `kind='device'` at weight 0.0,
 * so a topology here would draw unconnected circles.
 *
 * The ranking is **active alarms now**, not a count over the selected range, and it says so.
 * `/api/graph` serves what is active; a per-element count over a window is a different query and
 * deriving one from this would be inventing it.
 */
export function Estate({ nodes }) {
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
  return html`<section class="panel-block">
    <${SectionHeading} title="Where it is happening" />
    <${Bars} title=${`Busiest ${TOP_N}`} rows=${rows} unit="alarms"
      source="active now, not over the range above" />
    <${EstateMap} title="Every element"
      cells=${nodes.map((node) => ({
        key: String(node.id),
        label: node.label || node.ip,
        value: node.active_alarms,
      }))}
      urgentAt=${URGENT_AT}
      source="load, not topology"
      note=${html`busiest first —${" "}<a href="#/graph">how they connect</a>`} />
  </section>`;
}
