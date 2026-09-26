/* The Timeline: where and when alarms were raised, and what, over a window you choose.
 *
 * ## v0.22.0 — the axis follows the window, and the list is read the way an operator reads it
 *
 * The screen asked for the 300 alarms most recently seen and drew them on an axis spanning their
 * own oldest to newest mark: with "last 24 hours" chosen, the chart covered **124 seconds** of the
 * lab's storm, labelled with clock times (F155). Beneath it, a hundred raw rows with the trap OID
 * as the primary column. Now:
 *
 *   * the **window** is the query — `/api/activity/*` counts everything inside it, in SQL, and
 *     nothing truncates it; the lanes' columns are equal slices of it, so position is time;
 *   * the **list** is bursts — one row per trap per element, repeats folded into *"×14 over
 *     2 min"* — with the trap's name first and its OID under it, paged, with the total;
 *   * every control is in the address, so the configured screen is a link (DECISIONS #311).
 *
 * No d3. Both drawings are hand-written, so the harness asserts them.
 */

import { html, Component } from "../dom.js";
import { get } from "../api.js";
import { Loading, Empty, Failed } from "../widgets.js";
import { plural } from "../format.js";
import { can } from "../session.js";
import { Bursts, DEFAULTS, LANE_BUCKETS, Lanes, PAGE, WINDOWS } from "./parts/marks.js";
import { FilterBar } from "./parts/tlfilters.js";
import { Sequence } from "./parts/sequence.js";

const ID = /^\d+$/;
const OID = /^[0-9]+(\.[0-9]+)+$/;
/** The longest window the activity routes answer; a situation older than this is cut at it. */
const MAX_S = 30 * 24 * 60 * 60;

/** The parameters, read from the address and clamped: a hand-edited URL is input (v0.23.0: org,
 *  oid and sid join win, ne, kind and page — #392). */
function configOf(q) {
  const raw = (key) => (q && q.get(key)) || "";
  const win = Number(raw("win"));
  const kind = raw("kind");
  return {
    win: WINDOWS.some((w) => w.seconds === win) ? win : DEFAULTS.win,
    ne: ID.test(raw("ne")) ? raw("ne") : "",
    org: ID.test(raw("org")) ? raw("org") : "",
    oid: OID.test(raw("oid")) ? raw("oid") : "",
    sid: ID.test(raw("sid")) ? raw("sid") : "",
    kind: ["raise", "clear"].includes(kind) ? kind : "both",
    page: Math.max(0, Number.parseInt(raw("page"), 10) || 0),
  };
}

export class Timeline extends Component {
  constructor(props) {
    super(props);
    this.state = { status: "loading", lanes: null, groups: null, error: null,
                   elements: [], orgs: [], traps: [], situation: null };
    this.reload = this.reload.bind(this);
  }

  componentDidMount() { this.options(); this.reload(); }

  /* Re-read when a parameter that reaches SQL changed — not when the address merely did. A
     parameter this screen does not know (a pasted `chart=`) is not a reason to ask again. */
  componentDidUpdate(previous) {
    if (JSON.stringify(configOf(previous.query)) !== JSON.stringify(this.config())) this.reload();
  }

  config() { return configOf(this.props.query); }

  /** What the filters offer: every element with its organization, and the named traps. Read once;
   *  a failure leaves that control empty and the timeline working. */
  async options() {
    const [inv, cat] = await Promise.allSettled([
      get("/api/inventory"),
      can("classes.read") ? get("/api/catalogue?limit=200") : { classes: [] },
    ]);
    const elements = inv.status === "fulfilled" ? inv.value.elements : [];
    const orgs = [...new Map(elements.filter((e) => e.organization_id != null)
      .map((e) => [e.organization_id, { id: e.organization_id, name: e.organization }])).values()];
    const traps = cat.status === "fulfilled"
      ? cat.value.classes.filter((c) => c.name).map((c) => ({ oid: c.oid, name: c.name }))
      : [];
    this.setState({ elements, orgs, traps });
  }

  async reload() {
    const { win, ne, kind, page, org, oid, sid } = this.config();
    this.setState({ status: this.state.groups ? "ready" : "loading" });
    try {
      // A situation's timeline covers the whole situation, from its first alarm to now.
      let situation = null;
      let range = win;
      if (sid) {
        situation = await get(`/api/situations/${sid}`);
        const t0 = Math.min(...(situation.alarms || []).map((a) => a.first_seen), Date.now() / 1000);
        range = Math.min(MAX_S, Math.max(900, Math.ceil(Date.now() / 1000 - t0) + 60));
      }
      const extra = (ne ? `&ne_id=${ne}` : "") + (org ? `&organization_id=${org}` : "") +
        (oid ? `&oid=${oid}` : "") + (sid ? `&sid=${sid}` : "");
      const [lanes, groups] = await Promise.all([
        get(`/api/activity/lanes?range_s=${range}&buckets=${LANE_BUCKETS}&top=8${extra}`),
        get(`/api/activity/groups?range_s=${range}&kind=${kind}&limit=${PAGE}` +
            `&offset=${page * PAGE}${extra}`),
      ]);
      this.setState({ status: "ready", lanes, groups, situation, error: null });
    } catch (error) {
      this.setState({ status: "error", error });
    }
  }

  /** Write one parameter into the address; a filter change returns to the first page. Choosing an
   *  organization clears an element outside it. */
  set(key, value) {
    const q = new URLSearchParams(String(this.props.query ?? ""));
    if (value === "" || value == null || String(value) === String(DEFAULTS[key])) q.delete(key);
    else q.set(key, String(value));
    if (key !== "page") q.delete("page");
    if (key === "org" && value) {
      const el = this.state.elements.find((e) => String(e.ne_id) === q.get("ne"));
      if (el && String(el.organization_id) !== String(value)) q.delete("ne");
    }
    const text = q.toString();
    this.props.navigate(`#/timeline${text ? `?${text}` : ""}`);
  }

  clear() { this.props.navigate("#/timeline"); }

  render(_props, { status, lanes, groups, error, elements, orgs, traps, situation }) {
    if (status === "loading") return html`<${Loading} label="Reading alarm activity" />`;
    if (status === "error") {
      return html`<${Failed} error=${error} retry=${this.reload} what="the timeline" />`;
    }
    const config = this.config();
    const sname = situation ? situation.operator_name || situation.derived_name : null;
    return html`<div class="timelineview">
      <${FilterBar} config=${config} elements=${elements} orgs=${orgs} traps=${traps}
        situation=${sname} set=${(key, value) => this.set(key, value)} clear=${() => this.clear()} />
      ${situation ? html`<${Sequence} situation=${situation} />` : null}
      ${groups.repeated
        ? html`<p class="timeline-repeated">
            <a href="#/situations">${plural(groups.repeated, "active alarm")} re-reported</a>
          </p>`
        : null}
      ${groups.total === 0
        ? html`<${Empty} title=${groups.repeated
              ? "Nothing new was raised or cleared in this window."
              : "Nothing was raised or cleared in this window."}
            will="A longer window, or fewer filters, may show more." />`
        : html`<${Lanes} data=${lanes} />
            <${Bursts} data=${groups}
              onPage=${(step) => this.set("page", Math.max(0, config.page + step))} />`}
    </div>`;
  }
}
