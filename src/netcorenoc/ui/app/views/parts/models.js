/* "Which model is deciding, and how do they compare?" — the Overview's models card (v0.29.0).
 *
 * ## Three charts and one line
 *
 * v0.27.0 answered this in a status line and a 70-word paragraph about labels and the judge's
 * schedule. The maintainer's review: too much text, and the card should show the models. It now
 * shows the three charts that answer the question in order:
 *
 *   1. **Score by model** — the judge's offline score, the number it ranks by;
 *   2. **ROC, all models** — how well each separates related alarms from unrelated ones;
 *   3. **Live agreement with the champion** — what the challengers would have done on this
 *      appliance's own traffic, since it started.
 *
 * The first two are **generated data** (held-out streams no model trained or tuned on), the third
 * is **live** traffic; each caption says which. One colour per model, in league order — the same
 * colours as the Judge screen, which the line links to for everything else; the ROC carries the
 * legend.
 *
 * It reads `/api/judge?brief=true` (a few KiB; `model.read`). A role without it gets the one
 * line from `/api/decider` (`scorer.read`), and a role without that gets nothing. Labelling asks
 * for the line alone (`line`).
 *
 * ## It is not evidence
 *
 * Reading this writes nothing. The judge's switches are made by the slow loop on the server and
 * recorded in the audit log; this component only reports them.
 */

import { Component, html } from "../../dom.js";
import { get } from "../../api.js";
import { Bars } from "../../compare.js";
import { Curve } from "../../modelcharts.js";
import { count } from "../../format.js";
import { toneOf } from "./leaguecompare.js";

const HELD_OUT = "generated data, held-out streams";

export class ModelHealth extends Component {
  constructor(props) {
    super(props);
    this.state = { data: null, error: null, lineOnly: false };
  }

  async componentDidMount() {
    try {
      // `line`: Labelling shows who decides and nothing else, from the light read.
      if (this.props.line) throw Object.assign(new Error("line"), { status: 403 });
      this.setState({ data: await get("/api/judge?brief=true") });
    } catch (error) {
      if (error.status !== 403) { this.setState({ error }); return; }
      try {
        this.setState({ data: await get("/api/decider"), lineOnly: true });
      } catch (second) {
        this.setState({ error: second });
      }
    }
  }

  render(_props, { data, error, lineOnly }) {
    if (error) {
      // A role without the read sees nothing rather than a failure: the card is a courtesy.
      return error.status === 403 ? null : html`<p class="hint models-error">
        Could not read the models (${error.detail || "request failed"}).</p>`;
    }
    if (!data) return null;
    const members = data.members || [];
    const champ = data.champion;
    const down = data.fallback || !champ;
    const line = html`<p class=${`corr-line corr-${down ? "warn" : "ok"}`} role="status">
      <span class="corr-dot" aria-hidden="true"></span>
      <span class="corr-text">${down
        ? "No model could be loaded; the fail-safe formula is grouping."
        : html`<b>${champ.name}</b> decides${data.pinned ? " (pinned)" : ""}`}
        <span class="muted">${" · "}${count(members.length)} models compete</span>
        ${" · "}<a href="#/promotion">Judge & promotion</a></span>
    </p>`;
    if (lineOnly) return html`<section class="models">${line}</section>`;
    const models = data.models || [];
    const tone = new Map(models.map((m, i) => [m.ref, toneOf(i)]));
    const name = new Map(models.map((m) => [m.ref, m.name]));
    const challengers = Object.entries((data.shadow || {}).challengers || {});
    return html`<section class="models">
      ${line}
      <div class="ov-model-charts">
        <${Bars} title="Score by model" max=${1} unit="ratio"
          rows=${members.map((m) => ({ key: m.ref, label: m.name, value: m.score,
            face: m.ref === (champ && champ.ref) ? `${m.name} ★` : m.name,
            tone: tone.get(m.ref) }))}
          source=${HELD_OUT} note="mean pairwise F1 · ★ deciding" />
        <${Curve} title="ROC" xLabel="false positive rate" yLabel="true positive rate"
          xRange=${[0, 1]} yRange=${[0, 1]} reference="diagonal"
          series=${models.map((m) => ({ name: m.name, tone: tone.get(m.ref), points: m.roc_curve || [] }))}
          source=${HELD_OUT} note="up and left is better" />
        <${Bars} title="Agreement with the champion" max=${1} unit="ratio"
          rows=${challengers.map(([ref, t]) => ({ key: ref, label: name.get(ref) || ref,
            value: t.agreement, tone: tone.get(ref) }))}
          source="live traffic since start-up" note="share of sampled pairs decided alike" />
      </div>
    </section>`;
  }
}
