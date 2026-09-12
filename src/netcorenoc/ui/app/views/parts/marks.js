/* The timeline's **hand-written half**: the five controls, the column alternative to the d3
 * scatter, and the per-element summary (v0.16.6, DECISIONS #311).
 *
 * The same boundary `views/parts/estate.js` draws for the Graph screen, for the same reason: the
 * scatter in `views/timeline.js` runs against a recording double and is asserted by nothing, and
 * everything here produces real DOM and is asserted. Splitting on that line means the import list
 * of each screen states which half is covered.
 *
 * ## Two of the five controls are presentational, and the file says which
 *
 * `ne`, `win` and `depth` become `ne_id`, `since` and `limit` and are answered **in SQL** —
 * `views/timeline.js::query` builds them and nothing here filters a row. `chart` and `split`
 * choose how the rows the server already returned are drawn.
 *
 * That split is not a nicety. v0.7.0 truncated globally and then compared the rendered
 * `COALESCE(label, ip)` string against a scope's address set, which made a non-unique display
 * string an authorization key (F35, F38). **A scope-bearing control is a query filter, always**,
 * and `chart`/`split` are safe to be presentational precisely because neither can name an element.
 */

import { html } from "../../dom.js";
import { DataTable, SectionHeading } from "../../widgets.js";
import { Series } from "../../charts.js";
import { buckets, spanText, tally } from "../../chartdata.js";
import { count, plural } from "../../format.js";

/** The windows the control offers, in seconds. `0` means "everything retention still holds". */
export const WINDOWS = [
  [0, "all retained"],
  [3600, "last hour"],
  [21600, "last 6 hours"],
  [86400, "last 24 hours"],
  [604800, "last 7 days"],
];

/** How deep to read. **A query filter**: `limit` bounds what SQL returns, never what is drawn. */
export const DEPTHS = [
  [100, "100 alarms"],
  [300, "300 alarms"],
  [1000, "1 000 alarms"],
];

/** The two ways to draw the same marks. Presentational, and the caption says so. */
export const CHARTS = [
  ["scatter", "one row per element"],
  ["column", "counts over time"],
];

/** Combine every element into one series, or draw one series per element. Presentational. */
export const SPLITS = [
  ["combined", "combined"],
  ["host", "separate by element"],
];

/**
 * The value each control carries when it is **absent from the address**.
 *
 * `views/timeline.js::set` deletes a parameter that equals its default rather than writing it, so
 * the address of an unconfigured screen is `#/timeline` and not `#/timeline?win=0&chart=scatter&…`.
 * A permalink whose every default is spelled out is a permalink nobody can read, and a reader
 * cannot tell which parts of it the sender actually chose.
 */
export const DEFAULTS = { ne: "", win: 0, chart: "scatter", split: "combined", depth: 300 };

/**
 * The five controls, each writing one parameter into the address.
 *
 * `onChange` calls `set`, `set` navigates, the router publishes, and `componentDidUpdate` reloads.
 * There is no second copy of the configuration anywhere in that loop, which is what makes the
 * address and the screen unable to disagree.
 */
export function Controls({ config, options, set, onClear }) {
  const { ne, windowS, chart, split, depth } = config;
  const configured = Boolean(ne || windowS || chart !== DEFAULTS.chart
    || split !== DEFAULTS.split || depth !== DEFAULTS.depth);
  return html`<div class="filters" role="group" aria-label="Timeline filters">
    <label for="tlNe">Element</label>
    <select id="tlNe" value=${ne} onChange=${(e) => set("ne", e.target.value)}>
      <option value="">every element in your scope</option>
      ${options.map(([id, name]) => html`<option key=${id} value=${id}>${name}</option>`)}
    </select>

    <label for="tlWindow">Window</label>
    <select id="tlWindow" value=${String(windowS)}
            onChange=${(e) => set("win", Number(e.target.value))}>
      ${WINDOWS.map(([value, label]) => html`
        <option key=${value} value=${String(value)}>${label}</option>`)}
    </select>

    <label for="tlDepth">Depth</label>
    <select id="tlDepth" value=${String(depth)}
            onChange=${(e) => set("depth", Number(e.target.value))}>
      ${DEPTHS.map(([value, label]) => html`
        <option key=${value} value=${String(value)}>${label}</option>`)}
    </select>

    <label for="tlChart">Chart</label>
    <select id="tlChart" value=${chart} onChange=${(e) => set("chart", e.target.value)}>
      ${CHARTS.map(([value, label]) => html`
        <option key=${value} value=${value}>${label}</option>`)}
    </select>

    <label for="tlSplit">Elements</label>
    <select id="tlSplit" value=${split} onChange=${(e) => set("split", e.target.value)}
            disabled=${chart !== "column"}
            title=${chart === "column"
              ? "Combine every element into one series, or draw one series each."
              : "The scatter already draws one row per element; this applies to the column chart."}>
      ${SPLITS.map(([value, label]) => html`
        <option key=${value} value=${value}>${label}</option>`)}
    </select>

    ${configured ? html`<button type="button" class="tap" onClick=${onClear}
    >Clear filters</button>` : null}
  </div>`;
}

/**
 * The column alternative to the scatter — **the same marks, counted per bucket**.
 *
 * It answers a question the scatter cannot: *how many*. A scatter with 1 000 marks over two minutes
 * is a solid band, and an operator asking whether the rate is rising reads nothing from it. It is
 * also the half of `chart` that is hand-written, so this screen's chart choice is now one drawing a
 * test executes and one it does not.
 *
 * `split` decides whether the estate is one series or one per element. **Both are arithmetic over
 * the marks the server already returned** — no row is dropped here, because dropping a row in the
 * render is exactly what F35 was.
 */
export function Columns({ marks, split }) {
  const grid = buckets(marks.map((m) => m.ts), 24);
  const series = split === "host"
    ? [...new Set(marks.map((m) => m.device))].slice(0, SERIES_CAP).map((device, index) => ({
        name: device,
        tone: TONES[index % TONES.length],
        values: tally(grid, marks.filter((m) => m.device === device).map((m) => m.ts)),
      }))
    : [
        {
          name: "raises",
          tone: "alarm",
          values: tally(grid, marks.filter((m) => m.kind !== "clear").map((m) => m.ts)),
        },
        {
          name: "clears",
          tone: "quiet",
          values: tally(grid, marks.filter((m) => m.kind === "clear").map((m) => m.ts)),
        },
      ];
  const hidden = split === "host"
    ? Math.max(0, new Set(marks.map((m) => m.device)).size - SERIES_CAP)
    : 0;
  return html`<section class="panel-block">
    <${Series} title=${split === "host" ? "Marks per element" : "Raises and clears"} mark="column"
      series=${series} labels=${grid.labels}
      source=${`${plural(marks.length, "mark")} from /api/timeline`}
      span=${grid.n ? `over ${spanText(grid.spanS)}, one column per ${spanText(grid.bucketS)}`
        : null}
      note=${hidden
        ? `${count(hidden)} further elements are not drawn; narrow with the Element control`
        : null} />
  </section>`;
}

/** How many per-element series the column chart will draw before it says it stopped. */
const SERIES_CAP = 4;

/** The tones a per-element series cycles through. Four, because a fifth is not distinguishable. */
const TONES = [null, "alarm", "warn", "quiet"];

/** `[[ne_id, rendered name]]` for the element control, from marks the server already sent. */
export function elementOptions(marks) {
  const byId = new Map();
  for (const mark of marks) {
    if (mark.ne_id != null && !byId.has(mark.ne_id)) byId.set(mark.ne_id, mark.device);
  }
  return [...byId.entries()].sort((a, b) => String(a[1]).localeCompare(String(b[1])));
}

/**
 * Raises and clears per element, over the marks on screen. **Ordinary DOM, not a drawing.**
 *
 * The one derived figure this screen carries, and it answers a question an operator actually asks
 * during an incident: *which element is still shouting?* An element with raises and no clears has
 * something outstanding; one with equal counts has finished flapping. It is arithmetic over marks
 * the server already sent — no route, no chart library, no new dependency (Part VII rules 1 and
 * 2), and it is testable, which the SVG above is not.
 */
export function Summary({ marks }) {
  const tally = new Map();
  for (const mark of marks) {
    const row = tally.get(mark.device) || { raises: 0, clears: 0 };
    if (mark.kind === "clear") row.clears += 1; else row.raises += 1;
    tally.set(mark.device, row);
  }
  const rows = [...tally.entries()]
    .sort((a, b) => (b[1].raises - b[1].clears) - (a[1].raises - a[1].clears))
    .slice(0, 10)
    .map(([device, row]) => ({
      key: device,
      tone: row.raises > row.clears ? "alarm" : null,
      cells: {
        device,
        raises: count(row.raises),
        clears: count(row.clears),
        outstanding: count(row.raises - row.clears),
      },
    }));
  if (rows.length < 2) return null;
  return html`<section class="panel-block">
    <${SectionHeading} title="Raises and clears, per element"
      hint=${"Over the marks shown. An element with more raises than clears has something " +
             "outstanding in this window; equal counts mean it raised and recovered. Sorted by " +
             "what is outstanding, which is the question an incident asks first."} />
    <${DataTable} columns=${[
      { key: "device", label: "element" },
      { key: "raises", label: "raises", numeric: true },
      { key: "clears", label: "clears", numeric: true },
      { key: "outstanding", label: "outstanding", numeric: true,
        title: "raises minus clears over the marks shown" },
    ]} rows=${rows} />
  </section>`;
}
