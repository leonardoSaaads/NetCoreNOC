/* Judge → site training: what the in-product search made of this appliance's labels (v0.26.0).
 *
 * The search adapts the league's GAM to this site (ADR #413) and registers the result as a model
 * the judge can rank — a league member of its own, compared only on labels that arrived after it
 * was fitted (ADR #425). This block shows the newest search's verdict against the pre-trained GAM
 * and its trials. **No labelling floors** (retired in v0.27.0): the verdict's 95 % t-interval is the
 * whole evidence standard, and a handful of labels simply yields a wide one.
 *
 * Every caption here says **site data**; nothing on this block shares an axis with generated data.
 */

import { html, Component } from "../../dom.js";
import { Bars } from "../../compare.js";
import { Curve, Scatter, Versus } from "../../modelcharts.js";

const SITE = "site data (this appliance's labels)";

export function SiteJudge({ site, canSearch = true }) {
  if (!site) return null;
  const latest = (site.runs || [])[0];
  const judgement = latest && latest.judgement;
  const trials = (site.trials || []).map((t) => ({ ...t, valid_loss: t.valid_loss }));
  return html`<section class="judge-block" aria-labelledby="judge-site">
    <header class="judge-head">
      <h3 id="judge-site">Site training</h3>
      <span class="dataset-chip dataset-site">site data</span>
    </header>
    <div class="judge-grid">
      ${judgement ? html`<${Judgement} j=${judgement} run=${latest} />`
        : html`<section class="chart-block"><h4 class="chart-title">Latest site search</h4>
            <p class="hint">No search has run on this appliance. ${canSearch
              ? "Settings → Site training starts one: it adapts the GAM to this site's labels, and the result joins the league."
              : "A search adapts the league's GAM, and this build carries none."}</p>
            <p class="judge-caption">${SITE}</p></section>`}
    </div>
    ${trials.length ? html`<h4 class="judge-sub">The latest search on this site</h4>
      <${SearchCharts} search=${{ trials }} fit=${null} source=${`${SITE}, search #${latest.id}`} />` : null}
    ${site.labelled_pairs_without_features ? html`<p class="hint">${site.labelled_pairs_without_features}
      labelled pair(s) predate v0.26.0 and carry no feature vector; they count toward nothing here.</p>` : null}
  </section>`;
}

function Judgement({ j, run }) {
  const d = j.difference || {};
  const b = j.benchmark || {};
  return html`<section class="chart-block">
    <h4 class="chart-title">Latest comparison: <b class="mono">${j.verdict}</b></h4>
    <p>${j.reason}</p>
    <div class="versus-row">
      ${d.mean != null ? html`<${Versus} label="log-loss change, site − shipped"
        model=${{ point: d.mean, low: d.low, high: d.high }} lower=${true} />` : null}
      ${b.shipped != null ? html`<${Versus} label="benchmark log loss (site model)"
        model=${{ point: b.site, low: b.site, high: b.site }} formula=${null} lower=${true} />` : null}
    </div>
    <p class="judge-caption">${SITE}, search #${run.id}; ${d.incidents ?? 0} newest incidents, paired;
      95 % t-interval over incidents. ${b.shipped != null
        ? `Do-no-harm: shipped ${b.shipped.toFixed(4)} on ${b.rows} generated benchmark pairs, margin ${b.margin}.`
        : ""}</p>
  </section>`;
}

/** The search: its history, each hyperparameter against the score, importance, time, and the fit. */
export class SearchCharts extends Component {
  constructor(props) {
    super(props);
    this.state = { param: null };
  }

  render({ search, fit, source }, { param }) {
    const trials = (search.trials || []).filter((t) => t.valid_loss != null);
    const names = Object.keys((trials[0] && trials[0].params) || {});
    const chosen = param && names.includes(param) ? param : names[0];
    const space = search.space || {};
    const order = trials.map((t, i) => ({ x: i + 1, y: t.valid_loss, tone: t.rung > 0 ? null : "muted" }));
    const importance = Object.entries(search.importance || {}).sort((a, b) => b[1] - a[1]);
    const nTrials = `${trials.length} fits`;
    return html`<div class="judge-grid">
      <${Scatter} title="Optimisation history" points=${order} step=${true}
        xLabel="fit, in order" yLabel="validation log loss" source=${source} n=${nTrials}
        note="line: best so far; darker: survived a halving" />
      <section class="chart-block mchart">
        <label class="mchart-pick">Hyperparameter
          <select value=${chosen} onChange=${(e) => this.setState({ param: e.target.value })}>
            ${names.map((p) => html`<option key=${p} value=${p}>${p}</option>`)}
          </select></label>
        <${Scatter} title=${`Score × ${chosen || "parameter"}`} logX=${(space[chosen] || [])[0] === "log"}
          points=${trials.filter((t) => t.rung === 0).map((t) => ({ x: t.params[chosen], y: t.valid_loss }))}
          xLabel=${chosen} yLabel="validation log loss" source=${source} n=${`${trials.filter((t) => t.rung === 0).length} first-rung fits`} />
      </section>
      <${Bars} title="Hyperparameter importance" max=${1} unit="ratio"
        rows=${importance.map(([k, v]) => ({ key: k, label: k, value: v, tone: null }))}
        source=${source} span=${`${trials.filter((t) => t.rung === 0).length} first-rung fits`}
        note="Spearman ρ² with the loss — a rank correlation, not fANOVA" />
      <${Scatter} title="Time × performance"
        points=${trials.map((t) => ({ x: t.seconds, y: t.valid_loss, tone: t.rung > 0 ? null : "muted" }))}
        xLabel="seconds" yLabel="validation log loss" source=${source} n=${nTrials} />
      ${fit ? html`<${Curve} title="Final fit: train vs validation" xLabel="boosting round" yLabel="log loss"
        series=${[
          { name: "train", tone: "muted", points: (fit.train_trace || []).map((v, i) => [i * fit.trace_every, v]) },
          { name: "validation", tone: null, points: (fit.valid_trace || []).map((v, i) => [i * fit.trace_every, v]) },
        ]}
        latest=${`best round ${fit.best_round}`} source=${source}
        n=${`${(fit.valid_trace || []).length} checkpoints`} note="the gap is the overfit; training stops at the best round" />` : null}
    </div>`;
  }
}
