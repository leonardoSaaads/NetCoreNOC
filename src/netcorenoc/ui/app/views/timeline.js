/* The timeline: raises and clears over time, per device — and the two filters an operator asked
 * for.
 *
 * The second d3 screen, and the same honesty applies as on the graph: the SVG below is drawn
 * against a recording double in the harness and is not executed by any assertion. **The list and
 * the summary beside it are not** — they are ordinary DOM, they carry the same information, and
 * they are what a keyboard or screen-reader operator reads. That pairing is deliberate: a drawing
 * nobody can verify should never be the only way to reach a fact.
 *
 * **v0.15.2 (F72): the caption used to describe two encodings that are not there.** It promised
 * "a triangular glyph in the table" — the table renders the bare word — and that "both encodings
 * are present so neither colour nor shape is load-bearing alone", when `circle.tl-raise` and
 * `circle.tl-clear` differ in exactly one declaration and it is `fill`. The pairing that actually
 * carries the fact is drawing-and-table, not colour-and-shape, and the caption now says that.
 *
 * ## v0.16.1 — what an operator does here, and what they do not
 *
 * **This screen gains no gesture, and that is the registered answer rather than an omission.**
 * `PREREGISTRATION-0.16.0.md` §1 extends the `incumbent_linked` prohibition to *any signal that is
 * not an assertion about a grouping*, and names the alarm lifecycle by name: a raise and a clear
 * are facts about an **alarm**. A "this mark is wrong" control here would be exactly the
 * prohibited signal wearing a new shape — a fact about a different question doing the work of a
 * measurement about this one. Part VII rule 5 permits *"none, and here is why"*, and this is why.
 *
 * What the screen gains instead is the two things the maintainer asked for, and **both are query
 * filters**. The element filter sends an `ne_id` — the same key the scope predicate uses, never
 * the rendered `device` string, because two elements can carry one label and v0.7.0 already
 * proved what happens when a display string decides anything (F35, DECISIONS #67). The window
 * filter sends `since`/`until`, so `limit` bounds the filtered set rather than a page that was
 * truncated first (F38).
 */

import { html, Component } from "../dom.js";
import { get } from "../api.js";
import { Loading, Empty, Failed, DataTable, TimeCell, SectionHeading, cell } from "../widgets.js";
import { absolute, count, plural, utcOffset, TIMEZONE } from "../format.js";
import { d3Ready } from "../vendor.js";

const LIMIT = 300;
const HEIGHT = 240;
const PAD = 30;
/* The LEFT pad is its own number, and it is the F83 repair.
 *
 * `d3.axisLeft` draws its tick labels to the left of the axis, so a y axis translated by PAD=30
 * had 30 px for a device name. `127.0.0.2` is about 55 px, which put every label at x=-9 — off the
 * canvas, at every width, on the one screen no test executes. Measured in Chromium at 390x844: six
 * elements outside the viewport, `document.scrollWidth == clientWidth`, so nothing scrolled and
 * the axis was simply unreadable.
 *
 * A pad alone is not enough, because a device LABEL is operator text of any length. So the pad
 * fits an address and the tick text is truncated to it; the full name is in the table below, which
 * is where an operator who needs it is already told to look. */
const PAD_LEFT = 82;
const TICK_CHARS = 13;

/** The windows the control offers, in seconds. `0` means "everything retention still holds". */
const WINDOWS = [
  [0, "all retained"],
  [3600, "last hour"],
  [21600, "last 6 hours"],
  [86400, "last 24 hours"],
  [604800, "last 7 days"],
];

/** An x-axis tick: the clock, in the browser's zone, without the offset (v0.16.4, #294).
 *
 * **The zone is named once, in the caption below the chart, and not on every tick.** Five ticks
 * each carrying `-03:00` would be five copies of one fact in the space a chart has least of. Every
 * point's own tooltip carries the full stamp with the offset in it, and the table beneath renders
 * `TimeCell`, so the offset is one hover or one glance away from every mark. */
function clockTick(t) {
  const d = new Date(t * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** A y-axis tick label, clipped to what PAD_LEFT can hold. The full name is in the table. */
function clipTick(name) {
  const text = String(name);
  return text.length <= TICK_CHARS ? text : `${text.slice(0, TICK_CHARS - 1)}…`;
}

export class Timeline extends Component {
  constructor(props) {
    super(props);
    this.state = { status: "loading", marks: [], error: null, neId: "", windowS: 0, options: [] };
    this.reload = this.reload.bind(this);
  }

  componentDidMount() {
    d3Ready().then(() => this.draw());  // DECISIONS #228
    this.reload();
  }
  componentDidUpdate() { this.draw(); }

  /** The filters, as a query string. **Every one of them is answered in SQL.** */
  query() {
    const parts = [`limit=${LIMIT}`];
    if (this.state.neId) parts.push(`ne_id=${encodeURIComponent(this.state.neId)}`);
    if (this.state.windowS) {
      parts.push(`since=${Math.floor(Date.now() / 1000) - this.state.windowS}`);
    }
    return parts.join("&");
  }

  async reload() {
    this.setState({ status: "loading" });
    try {
      const data = await get(`/api/timeline?${this.query()}`);
      const marks = data.marks || [];
      this.setState({
        status: "ready",
        marks,
        // The element list is rebuilt only from an UNFILTERED answer, so selecting one element
        // cannot empty the control that selects them — which would strand the operator on it.
        options: this.state.neId ? this.state.options : elementOptions(marks),
      }, () => this.draw());
    } catch (error) {
      this.setState({ status: "error", error });
    }
  }

  set(field, value) { this.setState({ [field]: value }, () => this.reload()); }

  draw() {
    const d3 = globalThis.d3;
    const marks = this.state.marks;
    if (!d3 || !marks.length || !this.svgRef) return;
    const selection = d3.select("#timeline");
    selection.selectAll("*").remove();
    const box = selection.node().getBoundingClientRect();
    const width = box.width || 640;
    const times = marks.map((m) => m.ts);
    const x = d3.scaleLinear()
      .domain([Math.min(...times), Math.max(...times) + 1]).range([PAD_LEFT, width - PAD]);
    const devices = [...new Set(marks.map((m) => m.device))];
    const y = d3.scalePoint().domain(devices).range([PAD, HEIGHT - PAD]).padding(0.5);
    const g = selection.attr("viewBox", `0 0 ${width} ${HEIGHT}`).append("g");
    g.append("g").attr("class", "axis").attr("transform", `translate(0,${HEIGHT - PAD})`)
      // **The axis names its zone once, in the caption, and not on every tick** (#294). Five
      // ticks each carrying `-03:00` would be five copies of one fact in the space a chart has
      // least of; the caption below states it, and every point's own tooltip carries the full
      // stamp with the offset in it.
      .call(d3.axisBottom(x).ticks(5).tickFormat(clockTick));
    g.append("g").attr("class", "axis").attr("transform", `translate(${PAD_LEFT},0)`)
      .call(d3.axisLeft(y).tickFormat(clipTick));
    g.selectAll("circle").data(marks).join("circle")
      .attr("cx", (m) => x(m.ts)).attr("cy", (m) => y(m.device)).attr("r", 4)
      .attr("class", (m) => (m.kind === "clear" ? "tl-clear" : "tl-raise"))
      .append("title")
      .text((m) => `${m.device} ${m.class} (${m.kind}) ${absolute(m.ts)}`);
  }

  filters() {
    const { neId, windowS, options } = this.state;
    return html`<div class="filters" role="group" aria-label="Timeline filters">
      <label for="tlNe">Element</label>
      <select id="tlNe" value=${neId} onChange=${(e) => this.set("neId", e.target.value)}>
        <option value="">every element in your scope</option>
        ${options.map(([id, name]) => html`<option key=${id} value=${id}>${name}</option>`)}
      </select>
      <label for="tlWindow">Window</label>
      <select id="tlWindow" value=${String(windowS)}
              onChange=${(e) => this.set("windowS", Number(e.target.value))}>
        ${WINDOWS.map(([value, label]) => html`
          <option key=${value} value=${String(value)}>${label}</option>`)}
      </select>
      ${neId || windowS ? html`<button type="button" class="tap"
        onClick=${() => this.setState({ neId: "", windowS: 0 }, () => this.reload())}
      >Clear filters</button>` : null}
    </div>`;
  }

  render(_props, { status, marks, error, neId, windowS }) {
    if (status === "loading") return html`<${Loading} label="Reading recent activity" />`;
    if (status === "error") {
      return html`<${Failed} error=${error} retry=${this.reload} what="the timeline" />`;
    }
    const filtered = Boolean(neId || windowS);
    if (!marks.length) {
      return html`<div class="timelineview">
        ${this.filters()}
        <${Empty}
          title=${filtered ? "No activity matches these filters." : "No recent alarm activity."}
          will=${filtered
            ? "The element and the window are both applied by the server, inside your visibility " +
              "scope — an element outside it answers the same nothing an element that does not " +
              "exist answers."
            : "Each raise and each clear the appliance has seen appears here, per device, for " +
              "as long as retention keeps it."}
          meanwhile=${filtered
            ? null
            : "If traps are arriving but nothing shows here, check the Quarantine screen " +
              "— a datagram the parser refused never becomes an alarm."} />
      </div>`;
    }

    const columns = [
      { key: "when", label: "when" },
      { key: "device", label: "device" },
      { key: "class", label: "class" },
      { key: "kind", label: "raise / clear" },
    ];
    const rows = marks.slice(0, 100).map((m, index) => ({
      key: `${m.ts}-${index}`,
      tone: m.kind === "clear" ? "quiet" : "alarm",
      cells: {
        when: cell(html`<${TimeCell} ts=${m.ts} />`),
        device: m.device,
        class: m.class,
        kind: m.kind,
      },
    }));

    return html`<div class="timelineview">
      ${this.filters()}
      <p class="hint">Alarms over time, one row per device. <b>In the drawing a mark's kind is
        encoded by colour alone</b> — raises in the alarm colour, clears in the quiet colour. The
        table below carries the word instead, and it is what a colour-blind operator, a screen
        reader, or anyone copying a fact into a ticket should read.</p>
      <svg id="timeline" role="img" ref=${(node) => { this.svgRef = node; }}
           aria-label=${`Timeline of ${plural(marks.length, "mark")}. The same marks are listed as text below.`}></svg>
      ${/* The chart's zone, named once (v0.16.4, #294). Five axis ticks each carrying `-03:00`
            would be five copies of one fact in the space a chart has least of, so it is said here
            instead — and every point's tooltip and every table row still carry the full stamp. */
        null}
      ${/* `${" "}` before the `<b>`, and it is Bug 2's exact shape caught in this release's own
            new markup: `are in\n        <b>` renders as `are inAsia/Tokyo`, because htm drops a
            whitespace-only run between a text node and an element. Found by reading the rendered
            string in a browser rather than the template — which is the whole reason the live pass
            exists, three commits after fixing the same thing in the gesture history. */ null}
      <p class="hint timeline-zone">Times on this axis are in${" "}
        <b>${TIMEZONE}</b>${" "}(${utcOffset(new Date())} from UTC), the zone of the device you are
        reading this on. Every mark's tooltip and every row below carry the full stamp.</p>
      <${Summary} marks=${marks} />
      <p class="hint">The ${plural(Math.min(marks.length, 100), "most recent mark")}, as text:</p>
      <${DataTable} columns=${columns} rows=${rows} />
    </div>`;
  }
}

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
