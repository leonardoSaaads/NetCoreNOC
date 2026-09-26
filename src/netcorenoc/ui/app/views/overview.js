/* The landing screen. **Six questions, in the order an operator asks them** (#304, #318).
 *
 * ## What this screen is for, in the maintainer's words
 *
 * *"The user wants to open the web application and immediately understand what is happening."*
 * Until v0.16.6 it was eleven paragraphs, 172 words, eleven counter tiles and **no charts**. It
 * gained eight charts there — and still did not answer the one question that decides whether a
 * duty operator gets out of their chair: **how many critical alarms are active right now.** That
 * number was on no screen in this console. It is now the first thing on this one.
 *
 *   1. **How bad is it** — active alarms by band, and how many are on no band at all.
 *   2. **What is happening** — situations and alarms over time.
 *   3. **Where** — the estate map.
 *   4. **Which element is worst** — elements by active alarms.
 *   5. **Is the appliance itself keeping up** — CPU, memory, storage, queue depth, as series.
 *   6. **What has it learned** — the two learned counters.
 *
 * A count of active alarms cannot say whether it is a burst or a trickle, which is why band 2
 * exists; and a shape over time cannot say how serious any of it is, which is why band 1 comes
 * first.
 *
 * ## What left, and what only changed shape
 *
 * **Gone in v0.16.6**: this file's own `Health` component — seven tiles, two section headings and
 * two paragraphs — and the paragraph pointing at five offline reports, which is `docs/operate.md`'s
 * job and was a screen telling an operator to read a file.
 *
 * **Gone in v0.16.7**: *"Your labelling"* — a heading, a 24-word paragraph and two stat tiles
 * counting situations an editor could judge. A count is not a task, and the Labelling screen
 * answers the same question with the situations themselves in front of the operator (#318).
 *
 * **Not gone**: the receiver's five counters. F68's finding was that `receiver.denied` is the only
 * evidence an operator has that their own allowlist is refusing their own equipment, and that is as
 * true as it was in v0.15.2. They became one secondary line — which is the shape #300 already chose
 * for the health panel's four correlation counters, for the same reason: the word above answers
 * *"is it keeping up"* and the numbers are for when the answer is no.
 *
 * ## Live versus on demand, unchanged since v0.13.0
 *
 * Everything in bands 1-5 except the alarm marks is already in the store because the update stream
 * writes it, so reading it costs this screen nothing. The alarm marks are **one** extra read on
 * mount — `/api/timeline`, measured at 8.3 ms against 181 750 `dataset_pair` rows — and it carries
 * the time it was taken, because a number with no timestamp is a number an operator will assume is
 * current. The corpus figures stay behind their on-demand control: `/api/dataset/retention` is
 * **32.6 ms** measured, and that is the one read on this screen nobody should pay for on load.
 */

import { html, Component } from "../dom.js";
import { get } from "../api.js";
import { Empty, Loading, Failed, SectionHeading } from "../widgets.js";

/** The two counted reads re-read on this period, so the screen is never older than it. */
const REFRESH_MS = 30_000;
import { PlannedWork } from "./parts/mwsummary.js";
import {
  DEFAULT_RANGE_S, Estate, Happening, RANGE_BUCKETS, RANGES, RangePicker,
} from "./parts/pulse.js";
import { Keeping } from "./parts/keeping.js";
import { ModelHealth } from "./parts/models.js";
import { Severity } from "./parts/severity.js";
import { SituationSummary } from "./parts/sitsummary.js";
import { TopAssets } from "./parts/topassets.js";
import { plural, count } from "../format.js";
import { can, scopeSummary } from "../session.js";
import { rangeSeconds, setRangeSeconds } from "../theme.js";
import * as store from "../store.js";

/**
 * The eight ranges as a set of seconds, which is the closed set `theme.js` validates against.
 *
 * **The preference is a cookie and not `localStorage`** (ADR #172, F2). This was written against
 * `localStorage` first and `tests/test_security_ui.py` refused it, which is the guard doing its
 * job: the value of *"no `localStorage` anywhere"* is that it is an absolute, and the first
 * carve-out turns it into a judgement call on every future diff. A third preference goes in a
 * third cookie, beside the theme and the sidebar, and nothing new is invented for it.
 */
const RANGE_VALUES = RANGES.map((r) => r.seconds);

export class Overview extends Component {
  constructor(props) {
    super(props);
    this.state = {
      live: store.get(), activity: null, activityError: null,
      rangeS: rangeSeconds(RANGE_VALUES, DEFAULT_RANGE_S), tick: 0,
    };
  }

  componentDidMount() {
    this.unsubscribe = store.subscribe((live) => this.setState({ live: { ...live } }));
    this.readActivity();
    // v0.23.0: the activity chart and the Top 10 are counted reads, not stream updates; without a
    // period they showed the moment the screen was opened for as long as it stayed open.
    this.timer = setInterval(() => {
      this.readActivity();
      this.setState({ tick: this.state.tick + 1 });
    }, REFRESH_MS);
  }

  componentWillUnmount() {
    if (this.unsubscribe) this.unsubscribe();
    if (this.timer) clearInterval(this.timer);
  }

  /**
   * The one read this screen makes. A failure is reported beside the chart, never as a zero.
   *
   * **Counts, not marks** (v0.20.0, F144). This asked for a thousand rows and counted them in the
   * browser — 108.5 KiB per load for twenty-four numbers, truncated at a thousand alarms so the
   * chart covered whatever span those happened to span. The bucketed form is 0.2 KiB and covers
   * the range the operator picked.
   */
  async readActivity() {
    try {
      const { rangeS } = this.state;
      this.setState({
        activity: await get(`/api/activity/active?buckets=${RANGE_BUCKETS}&range_s=${rangeS}`),
        activityError: null,
      });
    } catch (error) {
      this.setState({ activityError: error });
    }
  }

  /** Change the range, remember it for next time, and re-read at the new resolution. */
  pick(rangeS) {
    this.setState({ rangeS, activity: null }, () => {
      setRangeSeconds(rangeS, RANGE_VALUES, DEFAULT_RANGE_S);
      this.readActivity();
    });
  }

  render(_props, { live, activity, activityError, rangeS, tick }) {
    const stats = live.stats;
    // The panel below is titled "Open situations" and the store now holds all three states, so the
    // filter is here rather than in the transport — the same expression the sidebar count and the
    // labelling panel use, for the same reason (DECISIONS #254).
    const situations = (live.situations || []).filter((s) => s.status !== "resolved");
    const scope = scopeSummary();

    // **A spinner is not an error state, and a monitoring console must not confuse them.**
    // Driving this screen with a failing `/api/stats` left it on "Reading the appliance…"
    // indefinitely: on screen, a console that cannot reach its own API and a network that is
    // merely quiet looked identical.
    if (!stats) {
      if (live.connection === "error") {
        return html`<${Failed}
          error=${{ detail: "The appliance did not answer /api/stats, /api/graph or " +
                            "/api/situations. Nothing on this screen is current." }}
          what="the live picture"
          retry=${() => globalThis.location.reload()} />`;
      }
      return html`<${Loading} label="Reading the appliance" />`;
    }

    const quiet = stats.active_alarms === 0 && situations.length === 0 && stats.devices === 0;
    if (quiet) {
      return html`<${Empty}
        title="Nothing has arrived yet."
        will=${"This screen fills in as soon as the first SNMP trap reaches the appliance. " +
              "Devices, alarm classes, entities and severities are all learned from the trap " +
              "stream — there is no inventory to import and no MIB to load."}
        meanwhile=${`Point a device's trap destination at this appliance (UDP 162 by default) ` +
                    `and send one trap. The Alarm classes screen shows it immediately; grouping ` +
                    `begins once a second, related alarm arrives.`} />`;
    }

    const nodes = (live.graph && live.graph.nodes) || [];
    return html`<div class="overview">
      ${scope ? html`<p class="warnbox" title=${scope.title}>
        Your view is scoped to ${plural(scope.neCount, "network element", "network elements")}.
        Situations may extend beyond it; members you cannot see are shown as a redacted count.
      </p>` : null}

      ${/* **The range belongs at the top, once, and it drives the activity chart** (v0.20.0).
            Before this the charts each derived an axis from whatever their own data happened to
            cover, so nothing on the screen said what period was being looked at and nothing let
            an operator change it. */ null}
      <${RangePicker} value=${rangeS} onPick=${(seconds) => this.pick(seconds)} />

      ${/* **Rows, not columns** (v0.23.0). v0.20.0's two independent columns left holes wherever
            a short card sat beside a tall one — the empty half-screen under "Planned work". Each
            row now pairs cards of similar height on a 12-column grid and stretches them to one
            height, so every row ends level. Order is the order of the questions: is planned work
            skewing the counts; how bad; what needs someone; where; is the appliance coping. */ null}
      <div class="ov-grid">
        ${can("mw.read") ? html`<div class="ov-12 ov-slot"><${PlannedWork} /></div>` : null}
        <section class="panel-block ov-5"><${Severity} census=${stats.severity} /><//>
        <section class="panel-block ov-7">
          <${SectionHeading} title="What is happening" />
          <${Happening} data=${activity} rangeS=${rangeS} error=${activityError}
                        retry=${() => this.readActivity()} />
        <//>
        <div class="ov-5 ov-slot"><${SituationSummary} stats=${stats} situations=${situations} /></div>
        <div class="ov-7 ov-slot"><${TopAssets} rangeS=${rangeS} tick=${tick} /></div>
        <div class="ov-5 ov-slot"><${Estate} nodes=${nodes} edges=${(live.graph && live.graph.edges) || []} /></div>
        <div class="ov-7 ov-slot"><${Keeping} stats=${stats} rate=${live.trapRate} rangeS=${rangeS} /></div>
        <section class="panel-block ov-12 ov-models">
          <${SectionHeading} title="The models" />
          <${ModelHealth} admin=${can("model.register")} />
          <p class="learned-line">
            <b>${count(stats.devices)}</b>${" "}devices${" "}·${" "}
            <b>${count(stats.classes)}</b>${" "}alarm classes${" "}·${" "}learned from the stream
          </p>
        <//>
      </div>
    </div>`;
  }
}
