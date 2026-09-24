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
import { PlannedWork } from "./parts/mwsummary.js";
import {
  DEFAULT_RANGE_S, Estate, Happening, RANGE_BUCKETS, RANGES, RangePicker,
} from "./parts/pulse.js";
import { Keeping } from "./parts/keeping.js";
import { ModelHealth } from "./parts/models.js";
import { Severity } from "./parts/severity.js";
import { plural, relative, count, timeTitle } from "../format.js";
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
      rangeS: rangeSeconds(RANGE_VALUES, DEFAULT_RANGE_S),
    };
  }

  componentDidMount() {
    this.unsubscribe = store.subscribe((live) => this.setState({ live: { ...live } }));
    this.readActivity();
  }

  componentWillUnmount() { if (this.unsubscribe) this.unsubscribe(); }

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
        activity: await get(`/api/timeline?buckets=${RANGE_BUCKETS}&range_s=${rangeS}`),
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

  render(_props, { live, activity, activityError, rangeS }) {
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

      ${/* **Two per row** (v0.20.0). Every panel used to take a full row, so the severity card —
            six short rows of counts — occupied as much of the screen as the estate grid, and an
            operator scrolled past a great deal of whitespace to reach anything. `.grid-2` is one
            column under 900 px, so the phone layout is unchanged. */ null}
      <div class="grid-2">
        <section class="panel-block"><${Severity} census=${stats.severity} /><//>
        <section class="panel-block">
          <${SectionHeading} title="What is happening" />
          <${Happening} data=${activity} rangeS=${rangeS} error=${activityError}
                        retry=${() => this.readActivity()} />
        <//>
        ${/* **Planned work sits third, and the position is the argument** (v0.21.1). It is not
              another statistic: while a window is in force the alarm counts in the two panels
              above are incomplete by construction, so it has to be read before the estate grid
              and the situation list rather than after them. It is behind `mw.read`, which is
              `viewer` — prime directive 4 again: an operator who cannot see that planned work
              exists reads a quiet estate as a healthy one. */ null}
        ${can("mw.read") ? html`<${PlannedWork} />` : null}
        <${Estate} nodes=${nodes} />
        <${Keeping} stats=${stats} ring=${live.ring} rate=${live.trapRate} />
        <section class="panel-block">
          <${SectionHeading} title="Open situations" />
          ${situations.length
            ? html`<ul class="mini-list">${situations.slice(0, 8).map((s) => html`
                <li key=${s.id}>
                  <a href=${`#/situations/${s.id}`}>#${s.id}</a>
                  <span>${plural(s.alarm_count, "alarm")}</span>
                  <span class="muted" title=${timeTitle(s.updated_at)}
                    >${relative(s.updated_at)}</span>
                </li>`)}</ul>`
            : html`<p class="hint">Nothing open.</p>`}
          ${/* **The two "learned, not configured" tiles moved in here** (v0.20.0). They were a
                row of their own carrying two numbers and a four-word caption each, under no
                heading, between the health panel and the situation list — "a bit thrown
                together" is exactly right. They are facts about the estate this appliance has
                discovered, so they sit under the list of what it is currently working. */ null}
          <p class="learned-line">
            <b>${count(stats.devices)}</b>${" "}devices${" "}·${" "}
            <b>${count(stats.classes)}</b>${" "}alarm classes${" "}·${" "}learned from the stream
          </p>
        <//>
        <section class="panel-block">
          <${SectionHeading} title="The models" />
          ${/* **Moved down from directly under the alarm summary** (v0.20.0). It led the screen
                in v0.19.0, which put a sentence about training above the thing an operator opens
                the console for. It keeps its place on Labelling, where judging happens. */ null}
          <${ModelHealth} admin=${can("model.register")} />
        <//>
      </div>
    </div>`;
  }
}
