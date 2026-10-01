/* Labelling: the groupings waiting for a human — and what each judgement teaches.
 *
 * The gesture itself lives on the situation card, because that is where the evidence is — an
 * operator confirming a grouping needs the per-term contributions in front of them. So this screen
 * is the **queue and the standard**, and it links into the card for the decision.
 *
 * v0.27.0: two queues, in the order they matter. **Pending** first — the model wants to add alarms
 * to a situation an operator already confirmed, and is waiting for an answer (ADR #428); each
 * answer is also the most informative label there is, because it is exactly the case the model was
 * unsure about. Then the **New** groupings with something to judge. The v0.14.0 "what a model needs
 * before it can decide" floors are gone with the gate they described (ADR #425): models decide from
 * the first trap, and labels re-order them.
 */

import { html, Component } from "../dom.js";
import { Empty, SectionHeading, DataTable, Badge } from "../widgets.js";
import { plural, relative, timeTitle } from "../format.js";
import { situationName } from "./parts/card.js";
import * as store from "../store.js";
import { ModelHealth } from "./parts/models.js";

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
    // A resolved situation can still be labelled, but it is not what an operator works through:
    // this queue is the live population, which is what it has always shown.
    const all = (live.situations || []).filter((s) => s.status !== "resolved");
    const pending = all.filter((s) => s.status === "pending");
    const judgeable = all.filter((s) => s.alarm_count >= 2 && s.status !== "pending");
    const singletons = all.filter((s) => s.alarm_count < 2 && s.status !== "pending").length;

    return html`<div class="labellingview">
      <div class="settings-intro">
        <p><b>Your judgements teach every model.</b> A confirm says the grouping is right; a split
          says it is wrong (and the members you mark do not belong); a move, a merge, and an answer
          to a proposal each say which alarms belong together. The judge re-scores all five models
          on these labels every five minutes.</p>
      </div>
      <${ModelHealth} />

      <${SectionHeading} title="Proposals waiting for an answer"
        hint=${"The model would have added these alarms to a situation an operator already " +
               "confirmed. It never does that on its own: accept to add them, reject to keep them " +
               "apart. Either answer is a label."} />
      ${pending.length ? html`<${DataTable} kind="pending"
        columns=${[
          { key: "id", label: "proposal" },
          { key: "into", label: "would join" },
          { key: "sure", label: "model's confidence", numeric: true },
          { key: "members", label: "alarms", numeric: true },
          { key: "go", label: "" },
        ]}
        rows=${pending.map((s) => ({
          key: s.id,
          cells: {
            id: html`<a class="tap" href=${`#/situations/${s.id}`}>#${s.id} ${situationName(s)}</a>`,
            into: html`<a class="tap" href=${`#/situations/${s.proposed_into}`}>#${s.proposed_into}</a>`,
            sure: s.proposal_confidence == null ? "—" : `${Math.round(100 * s.proposal_confidence)}%`,
            members: s.alarm_count,
            go: html`<a class="tap" href=${`#/situations/${s.id}`}>answer it →</a>`,
          },
        }))} />` : html`<p class="hint">No proposals are waiting.</p>`}

      <${SectionHeading} title="Groupings waiting for a verdict"
        hint=${"A confirm asserts every pair in the grouping is positive. A split asserts the " +
               "grouping is wrong, and the members you mark are asserted negative — nothing " +
               "else. A situation with one member has no pair to judge, so it is not listed."} />

      <div class="stat-row">
        <div class="stat"><div class="stat-value">${judgeable.length}</div>
          <div class="stat-label">waiting for a verdict</div>
          <div class="stat-note">two or more members</div></div>
        <div class="stat"><div class="stat-value">${pending.length}</div>
          <div class="stat-label">proposals</div>
          <div class="stat-note">waiting for accept or reject</div></div>
        <div class="stat"><div class="stat-value">${singletons}</div>
          <div class="stat-label">singletons</div>
          <div class="stat-note">nothing to confirm or split</div></div>
      </div>

      ${judgeable.length ? html`<${DataTable}
        caption="Open the situation to see why the appliance grouped it, then decide there."
        columns=${[
          { key: "id", label: "situation" },
          { key: "name", label: "name" },
          { key: "members", label: "members", numeric: true },
          { key: "status", label: "status" },
          { key: "updated", label: "last change" },
          { key: "go", label: "" },
        ]}
        rows=${judgeable.map((s) => ({
          key: s.id,
          cells: {
            id: html`<a class="tap" href=${`#/situations/${s.id}`}>#${s.id}</a>`,
            name: situationName(s) || "—",
            members: s.alarm_count,
            status: html`<${Badge} tone=${s.status === "resolved" ? "quiet" : "alarm"}>${s.status}<//>`,
            updated: html`<span title=${timeTitle(s.updated_at)}>${relative(s.updated_at)}</span>`,
            go: html`<a class="tap" href=${`#/situations/${s.id}`}>judge it →</a>`,
          },
        }))} />` : html`<${Empty}
          title="Nothing is waiting for a verdict."
          will=${"A grouping arrives here as soon as two or more alarms correlate. Every one of " +
                 "them is a decision a human can confirm or overturn."}
          meanwhile=${"Singleton situations need no judgement — there is no pair in them to be " +
                      "right or wrong about."} />`}

      <p class="hint">${plural(all.length, "situation")} are currently loaded.</p>
    </div>`;
  }
}
