/* The timeline: raises and clears over time, per device.
 *
 * The second d3 screen, and the same honesty applies as on the graph: the SVG below is drawn
 * against a recording double in the harness and is not executed by any assertion. **The list
 * beside it is not** — it is ordinary DOM, it carries the same information, and it is what a
 * keyboard or screen-reader operator reads. That pairing is deliberate: a drawing nobody can
 * verify should never be the only way to reach a fact.
 *
 * **v0.15.2 (F72): the caption used to describe two encodings that are not there.** It promised
 * "a triangular glyph in the table" — the table renders the bare word — and that "both encodings
 * are present so neither colour nor shape is load-bearing alone", when `circle.tl-raise` and
 * `circle.tl-clear` differ in exactly one declaration and it is `fill`. The pairing that actually
 * carries the fact is drawing-and-table, not colour-and-shape, and the caption now says that.
 */

import { html, Component } from "../dom.js";
import { get } from "../api.js";
import { Loading, Empty, Failed, DataTable, TimeCell, cell } from "../widgets.js";
import { plural } from "../format.js";
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

/** A y-axis tick label, clipped to what PAD_LEFT can hold. The full name is in the table. */
function clipTick(name) {
  const text = String(name);
  return text.length <= TICK_CHARS ? text : `${text.slice(0, TICK_CHARS - 1)}…`;
}

export class Timeline extends Component {
  constructor(props) {
    super(props);
    this.state = { status: "loading", marks: [], error: null };
    this.reload = this.reload.bind(this);
  }

  componentDidMount() {
    d3Ready().then(() => this.draw());  // DECISIONS #228
    this.reload();
  }
  componentDidUpdate() { this.draw(); }

  async reload() {
    this.setState({ status: "loading" });
    try {
      const data = await get(`/api/timeline?limit=${LIMIT}`);
      this.setState({ status: "ready", marks: data.marks || [] }, () => this.draw());
    } catch (error) {
      this.setState({ status: "error", error });
    }
  }

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
      .call(d3.axisBottom(x).ticks(5)
        .tickFormat((t) => new Date(t * 1000).toLocaleTimeString()));
    g.append("g").attr("class", "axis").attr("transform", `translate(${PAD_LEFT},0)`)
      .call(d3.axisLeft(y).tickFormat(clipTick));
    g.selectAll("circle").data(marks).join("circle")
      .attr("cx", (m) => x(m.ts)).attr("cy", (m) => y(m.device)).attr("r", 4)
      .attr("class", (m) => (m.kind === "clear" ? "tl-clear" : "tl-raise"))
      .append("title")
      .text((m) => `${m.device} ${m.class} (${m.kind}) ${new Date(m.ts * 1000).toLocaleString()}`);
  }

  render(_props, { status, marks, error }) {
    if (status === "loading") return html`<${Loading} label="Reading recent activity" />`;
    if (status === "error") {
      return html`<${Failed} error=${error} retry=${this.reload} what="the timeline" />`;
    }
    if (!marks.length) {
      return html`<${Empty}
        title="No recent alarm activity."
        will=${"Each raise and each clear the appliance has seen appears here, per device, for " +
               "as long as retention keeps it."}
        meanwhile=${"If traps are arriving but nothing shows here, check the Quarantine screen " +
                    "— a datagram the parser refused never becomes an alarm."} />`;
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
      <p class="hint">Alarms over time, one row per device. <b>In the drawing a mark's kind is
        encoded by colour alone</b> — raises in the alarm colour, clears in the quiet colour. The
        table below carries the word instead, and it is what a colour-blind operator, a screen
        reader, or anyone copying a fact into a ticket should read.</p>
      <svg id="timeline" role="img" ref=${(node) => { this.svgRef = node; }}
           aria-label=${`Timeline of ${plural(marks.length, "mark")}. The same marks are listed as text below.`}></svg>
      <p class="hint">The ${plural(Math.min(marks.length, 100), "most recent mark")}, as text:</p>
      <${DataTable} columns=${columns} rows=${rows} />
    </div>`;
  }
}
