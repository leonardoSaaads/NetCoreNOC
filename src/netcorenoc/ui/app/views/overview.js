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
import { Stat, Empty, Loading, Failed, SectionHeading } from "../widgets.js";
import { Happening, MARK_LIMIT, Where, Worst } from "./parts/pulse.js";
import { Keeping, Learned } from "./parts/keeping.js";
import { ModelHealth } from "./parts/models.js";
import { Severity } from "./parts/severity.js";
import { plural, relative, absolute, timeTitle, TIMEZONE } from "../format.js";
import { can, scopeSummary } from "../session.js";
import * as store from "../store.js";

export class Overview extends Component {
  constructor(props) {
    super(props);
    this.state = { live: store.get(), marks: null, marksAt: null, marksError: null };
  }

  componentDidMount() {
    this.unsubscribe = store.subscribe((live) => this.setState({ live: { ...live } }));
    this.readMarks();
  }

  componentWillUnmount() { if (this.unsubscribe) this.unsubscribe(); }

  /** The one read this screen makes. A failure is reported beside the chart, never as a zero. */
  async readMarks() {
    try {
      const data = await get(`/api/timeline?limit=${MARK_LIMIT}`);
      this.setState({ marks: data.marks || [], marksAt: Date.now() / 1000, marksError: null });
    } catch (error) {
      this.setState({ marksError: error });
    }
  }

  render(_props, { live, marks, marksAt, marksError }) {
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

      <${Severity} census=${stats.severity} />
      ${/* **Directly under the alarm summary, and above everything else.** The maintainer's
            standing request is to migrate correlation from the fixed formula to a learned
            model; until v0.19.0 the console said nothing at all about how that was going, on
            any screen. It is one line and one bar, so the position costs an operator who does
            not care about it a single glance. */ null}
      <${ModelHealth} admin=${can("model.register")} />
      <${Happening} situations=${live.situations || []} marks=${marks} at=${marksAt}
                    error=${marksError} retry=${() => this.readMarks()} />
      <${Where} nodes=${nodes} />
      <${Worst} nodes=${nodes} />
      <${Keeping} stats=${stats} ring=${live.ring} rate=${live.trapRate} />
      <${Learned} stats=${stats} />

      <${SectionHeading} title="Open situations"
        hint="Newest first. Open one to see the per-term contributions that produced each link." />
      ${situations.length
        ? html`<ul class="mini-list">${situations.slice(0, 8).map((s) => html`
            <li key=${s.id}>
              <a href=${`#/situations/${s.id}`}>#${s.id}</a>
              <span>${plural(s.alarm_count, "alarm")}</span>
              <span class="muted" title=${timeTitle(s.updated_at)}
                >${relative(s.updated_at)}</span>
            </li>`)}</ul>`
        : html`<p class="hint">No open situations — alarms are arriving, nothing has
            correlated.</p>`}

      ${/* **"Your labelling" left here in v0.16.7** (#318). A heading, a 24-word paragraph and
            two stat tiles that answered *"how many situations can I judge"* — a count on a screen
            with nothing to do about it. The Labelling screen answers the same question with the
            situations themselves in front of the operator, and its link is in the sidebar with
            every other screen's. What replaced it above is the count that decides whether an
            operator stands up. */ null}
      ${/* **The two on-demand panels left in v0.19.0.**
            They sat at the foot of this screen reading *"Not computed yet"* beside a
            `Compute now` button, which is what an operator saw every time they opened the
            console — two dead boxes at the end of the page they use most.
            `Judge and promotion` answered *"is a model running, and what has the gate
            decided"*, which the model line at the top of this screen now answers live and
            without a click. `What capture is holding` answered *"how big is the corpus"*,
            which is the Corpus screen's whole subject and is linked from the sidebar. Both
            were a second, colder copy of a question that already had a home. The seal's
            query count and the decision history stay on **Judge & promotion**, which is
            where an auditor looks for them. */ null}
    </div>`;
  }
}
