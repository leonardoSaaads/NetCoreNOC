/* Labelling: the queue of groupings waiting for a human verdict.
 *
 * The gesture itself lives on the situation card, because that is where the evidence is — an
 * operator confirming a grouping needs the per-term contributions in front of them, and a
 * labelling screen that showed a list of ids without them would be asking for a judgement nobody
 * is in a position to make. So this screen is the **queue and the standard**, and it links into
 * the card for the decision.
 *
 * ## What it will not do
 *
 * It shows no sufficiency verdict, no minimum detectable difference, no projection. Those come
 * from `make shadow-report`, which runs offline over frozen inputs and is compared byte-for-byte
 * by a test. **No second implementation of a number a CLI report already computes** (draft §11.11)
 * — a second implementation of the shadow verdict would be a second source of truth for the
 * figure four releases of evidence discipline rest on. The report has no HTTP route, so what this
 * screen honestly offers is the command and what it answers.
 */

import { html, Component } from "../dom.js";
import { Empty, SectionHeading, DataTable, Badge } from "../widgets.js";
import { plural, relative, timeTitle } from "../format.js";
import * as store from "../store.js";

export class Labelling extends Component {
  constructor(props) {
    super(props);
    this.state = { live: store.get() };
  }

  componentDidMount() {
    this.unsubscribe = store.subscribe((live) => this.setState({ live: { ...live } }));
  }

  componentWillUnmount() { if (this.unsubscribe) this.unsubscribe(); }

  render(_props, { live }) {
    const all = live.situations || [];
    const judgeable = all.filter((s) => s.alarm_count >= 2);
    const singletons = all.length - judgeable.length;

    return html`<div class="labellingview">
      <${SectionHeading} title="What a label asserts"
        hint=${"A confirm asserts every pair in the grouping is positive. A split asserts the " +
               "grouping is wrong, and the members you mark are asserted negative — nothing " +
               "else. Marking nothing is a plain split: it says the grouping is wrong without " +
               "saying which member does not belong."} />
      <p class="hint">A situation with one member contains no pair to judge, so it is not in this
        queue. That is why the two counts below differ, and why a raw "situations labelled" figure
        would overstate what has been decided.</p>

      <div class="stat-row">
        <div class="stat"><div class="stat-value">${judgeable.length}</div>
          <div class="stat-label">waiting for a verdict</div>
          <div class="stat-note">two or more members</div></div>
        <div class="stat"><div class="stat-value">${singletons}</div>
          <div class="stat-label">singletons</div>
          <div class="stat-note">nothing to confirm or split</div></div>
      </div>

      ${judgeable.length ? html`<${DataTable}
        caption="Open the situation to see why the appliance grouped it, then decide there."
        columns=${[
          { key: "id", label: "situation" },
          { key: "members", label: "members", numeric: true },
          { key: "status", label: "status" },
          { key: "updated", label: "last change" },
          { key: "go", label: "" },
        ]}
        rows=${judgeable.map((s) => ({
          key: s.id,
          cells: {
            id: html`<a class="tap" href=${`#/situations/${s.id}`}>#${s.id}</a>`,
            members: s.alarm_count,
            status: html`<${Badge} tone=${s.status === "open" ? "alarm" : "quiet"}>${s.status}<//>`,
            updated: html`<span title=${timeTitle(s.updated_at)}>${relative(s.updated_at)}</span>`,
            go: html`<a class="tap" href=${`#/situations/${s.id}`}>judge it →</a>`,
          },
        }))} />` : html`<${Empty}
          title="Nothing is waiting for a verdict."
          will=${"A grouping arrives here as soon as two or more alarms correlate. Every one of " +
                 "them is a decision a human can confirm or overturn."}
          meanwhile=${"Singleton situations need no judgement — there is no pair in them to be " +
                      "right or wrong about."} />`}

      <${SectionHeading} title="Where the corpus stands"
        hint=${"Deliberately not computed here."} />
      <p class="structural-note">Whether the corpus has reached the pre-registered floors, what
        the minimum detectable difference is, and how far off the projection is are all answered
        by <code class="mono">make shadow-report</code>. It runs offline over frozen inputs and a
        test compares its output byte-for-byte. <b>This console displays such numbers and never
        recomputes them</b>: a second implementation of the shadow verdict would be a second
        source of truth for the figure the whole evidence chain rests on. That report has no HTTP
        route, so running it needs shell access to the appliance.</p>
      <p class="hint">${plural(all.length, "situation")} are currently loaded.</p>
    </div>`;
  }
}
