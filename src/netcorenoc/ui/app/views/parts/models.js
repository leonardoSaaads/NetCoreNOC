/* "Which model is deciding, and are my judgements doing anything?" — on the Overview and on
 * Labelling, in a line (v0.27.0).
 *
 * ## What changed, and why
 *
 * Until v0.26.0 this line answered *"when does a model take over?"* with four bars — groupings
 * marked wrong 1/50, partly right 1/20, incidents judged 2/30, people 1/3 — and a percentage
 * "ready". The maintainer's objection was the right one: pre-trained models should be deciding from
 * the first trap, and an operator should never be told to wait for a count before the machine is
 * useful. In v0.27.0 that is what happens (ADRs #424, #425): five pre-trained models compete, the
 * judge's champion decides from day 0, and the labels **re-order** the models rather than unlock
 * them. So this line now says who is deciding, how many are competing, and what the operator's
 * labels amount to — and links to the Judge, where every model's learning is charted.
 *
 * ## It is not evidence
 *
 * Reading this writes nothing. The judge's switches are made by the slow loop on the server and
 * recorded in the audit log; this component only reports them.
 */

import { Component, html } from "../../dom.js";
import { get } from "../../api.js";
import { count } from "../../format.js";

export class ModelHealth extends Component {
  constructor(props) {
    super(props);
    this.state = { data: null, error: null };
  }

  async componentDidMount() {
    try {
      this.setState({ data: await get("/api/decider") });
    } catch (error) {
      this.setState({ error });
    }
  }

  render(_props, { data, error }) {
    if (error) {
      // A role without the read sees nothing rather than a failure: the line is a courtesy.
      return error.status === 403 ? null : html`<p class="hint models-error">
        Could not read which model is deciding (${error.detail || "request failed"}).</p>`;
    }
    if (!data) return null;
    const members = data.members || [];
    const champ = data.champion;
    const tone = data.fallback || !champ ? "warn" : "ok";
    const text = data.fallback || !champ
      ? "No model could be loaded; the fail-safe formula is grouping."
      : `${champ.name} is deciding${data.pinned ? " (pinned by an admin)" : ", chosen by the judge"}.`;
    const lastSwitch = (data.decisions || [])[0];
    return html`<section class="models">
      <p class=${`corr-line corr-${tone}`} role="status">
        <span class="corr-dot" aria-hidden="true"></span>
        <span class="corr-text">${text}${" "}${count(members.length)} models compete;${" "}
          <a href="#/promotion">see how each is learning</a>.</span>
      </p>
      <p class="hint">Every confirm, split, move, merge and answer to a <b>Pending</b> proposal is a
        label. The judge re-scores every model on them every five minutes and switches only when one
        is clearly better here${lastSwitch ? html` — last decision: ${lastSwitch.reason}` : ""}.
        Nothing waits for a minimum number of labels.</p>
    </section>`;
  }
}
