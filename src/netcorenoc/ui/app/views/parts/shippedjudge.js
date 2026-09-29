/* Judge → the shipped model, **measured on generated data** (v0.26.0, ADR #414).
 *
 * Every number here was computed before release, on generated streams the model never trained on
 * — including whole families it never saw — and is read from the manifest packaged beside it. It is
 * **not** this appliance's traffic, and every caption says so: the site block below draws this
 * appliance's labels, and the two are never on one axis.
 *
 * The page leads with the four quantities the quality bar is set on (pairwise F1, ARI, over-merge
 * and under-merge), each beside the additive formula's on the same streams, with its 95 % interval
 * and its n. Then the pair-level charts (precision–recall against its baseline rate, ROC,
 * calibration, confusion), then the search that chose the hyperparameters, then what the model
 * learned (one shape per feature, which is the whole model — an additive model has nothing else).
 */

import { html, Component } from "../../dom.js";
import { SectionHeading } from "../../widgets.js";
import { Bars } from "../../compare.js";
import { Curve, Scatter, Steps, Confusion, Versus, Diverging } from "../../modelcharts.js";

const GEN = "generated data (eval/synth), held-out test streams";
const HEADLINE = [
  ["repair_gestures", "repair gestures / incident", true],
  ["pairwise_f1", "pairwise F1", false],
  ["ari", "adjusted Rand", false],
  ["over_merge_rate", "over-merge rate", true],
  ["under_merge_rate", "under-merge rate", true],
  ["split_bag_intact_rate", "concurrent incidents merged", true],
  ["asserted_negative_respected_rate", "negatives kept apart", false],
];
const SPLITS = {
  test_iid: "unseen streams, trained families",
  test_optical: "held-out optical families",
  test_protocol: "held-out protocol families",
  test_concurrency: "concurrent incidents",
  test_time: "later in time (last 30 %)",
};

const n = (m) => (m ? `${m.streams} streams · ${m.incidents} incidents · ${m.activations} activations` : "");

/** A reason the server phrases as a clause, shown as a sentence. */
const sentence = (text) => text.charAt(0).toUpperCase() + text.slice(1);

export function ShippedJudge({ shipped }) {
  if (!shipped || !shipped.available) {
    // A build that carries no model (#422) is a state, shown as a note; refused files are a fault.
    return html`<section class="panel-block"><${SectionHeading} title="Shipped model" />
      <p class=${shipped && shipped.absent ? "hint" : "err"}>${sentence(shipped ? shipped.reason : "not available")}</p>
    </section>`;
  }
  const ev = shipped.evaluation || {};
  const iid = (ev.splits || {}).test_iid || {};
  const pairs = (ev.pairs || {}).test_iid || {};
  const verdict = shipped.verdict || {};
  return html`<section class="judge-block" aria-labelledby="judge-shipped">
    <header class="judge-head">
      <h3 id="judge-shipped">Shipped model</h3>
      <span class="dataset-chip dataset-generated">generated data</span>
      <span class=${verdict.passed ? "ok-note" : "err"}>${verdict.passed
        ? `quality bar met on all ${verdict.checked} checks`
        : "quality bar not met"}</span>
    </header>
    <div class="versus-row">
      ${HEADLINE.map(([k, label, lower]) => html`<${Versus} key=${k} label=${label}
        model=${iid.model && iid.model[k]} formula=${iid.formula && iid.formula[k]} lower=${lower} />`)}
      ${pairs.average_precision ? html`<${Versus} label="PR-AUC" model=${pairs.average_precision} />` : null}
      ${pairs.log_loss ? html`<${Versus} label="log loss" model=${pairs.log_loss} lower=${true} />` : null}
      ${pairs.brier ? html`<${Versus} label="Brier" model=${pairs.brier} lower=${true} />` : null}
    </div>
    <p class="judge-caption">${GEN} — ${SPLITS.test_iid}; ${n(iid.model)}. Intervals: 95 % cluster
      bootstrap over streams. ▲/▼: better/worse than the additive formula on the same streams.</p>

    <div class="judge-grid">
      <${SplitBars} splits=${ev.splits || {}} held=${ev.held_out_families || {}} />
      <${Curve} title="Precision–recall" xLabel="recall" yLabel="precision" xRange=${[0, 1]} yRange=${[0, 1]}
        series=${[{ name: "model", tone: null, points: (pairs.pr_curve || []).map(([r, p]) => [r, p]) }]}
        reference=${{ y: pairs.positive_rate_weighted }}
        latest=${pairs.average_precision ? `AP ${pairs.average_precision.point.toFixed(3)}` : null}
        source=${GEN} n=${`${pairs.pairs} pairs, ${pairs.streams} streams`}
        note=${`baseline (positive rate) ${(pairs.positive_rate_weighted ?? 0).toFixed(3)}, drawn flat`} />
      <${Curve} title="ROC" xLabel="false positive rate" yLabel="true positive rate"
        xRange=${[0, 1]} yRange=${[0, 1]} reference="diagonal"
        series=${[{ name: "model", tone: null, points: (pairs.roc_curve || []).map(([f, t]) => [f, t]) }]}
        latest=${pairs.roc_auc ? `AUC ${pairs.roc_auc.point.toFixed(3)}` : null}
        source=${GEN} n=${`${pairs.pairs} pairs`} note="read beside precision–recall: under imbalance ROC flatters" />
      <${Calibration} cal=${pairs.calibration} n=${pairs.pairs} />
      <${Confusion} matrix=${pairs.confusion} source=${GEN} n=${`${pairs.pairs} pairs`} />
      <${FirstHour} fh=${ev.first_hour} />
    </div>

    <h4 class="judge-sub">How the hyperparameters were chosen</h4>
    <${SearchCharts} search=${shipped.search || {}} fit=${shipped.final_fit} source="generated data, validation streams" />

    <h4 class="judge-sub">What the model learned</h4>
    <div class="judge-grid">
      <${Diverging} title="Ablation: loss without each feature"
        rows=${Object.entries((shipped.ablation || {}).delta_without || {}).map(([k, v]) => ({
          label: k, value: v, tone: (shipped.ablation.dropped || []).includes(k) ? "alarm" : null }))}
        source="generated data, validation streams" n=${`${shipped.provenance ? shipped.provenance.validation_rows : "?"} pairs`}
        note="positive = the feature pays; dropped features in red" />
    </div>
    <div class="shape-grid" role="list" aria-label="Shape functions">
      ${(shipped.shapes || []).map((s) => html`<div role="listitem" key=${s.feature}>
        <${Steps} feature=${s.feature} edges=${s.edges} scores=${s.scores} /></div>`)}
    </div>
    <p class="judge-caption">Shape functions: each feature's contribution to the log-odds of "same
      incident", per bin; the sum of the bars one pair falls in, plus the intercept, is the
      model's whole decision. Fitted on ${GEN.replace(", held-out test streams", "")}, training streams.</p>
  </section>`;
}

function SplitBars({ splits, held }) {
  const rows = [];
  const counts = Object.entries(splits).map(([k, s]) => `${k} ${s.model.streams}`).join(", ");
  for (const [key, label] of Object.entries(SPLITS)) {
    const s = splits[key];
    if (!s) continue;
    rows.push({ key: `${key}-m`, label: `${label} — model`, value: s.model.pairwise_f1.point, tone: null });
    rows.push({ key: `${key}-f`, label: `${label} — formula`, value: s.formula.pairwise_f1.point, tone: "muted" });
  }
  for (const [fam, s] of Object.entries(held)) {
    rows.push({ key: `${fam}-m`, label: `${fam} (held out) — model`, value: s.model.pairwise_f1.point, tone: null });
    rows.push({ key: `${fam}-f`, label: `${fam} (held out) — formula`, value: s.formula.pairwise_f1.point, tone: "muted" });
  }
  return html`<${Bars} title="Pairwise F1 by test split" rows=${rows} max=${1} unit="ratio"
    source=${GEN} span=${`streams per split: ${counts}`}
    note="held-out families were never in training or validation" />`;
}

function Calibration({ cal, n }) {
  if (!cal) return null;
  const bins = (cal.bins || []).filter((b) => b[2] > 0);
  return html`<${Curve} title="Calibration" xLabel="predicted probability" yLabel="observed rate"
    xRange=${[0, 1]} yRange=${[0, 1]} reference="diagonal"
    series=${[{ name: "model", tone: null, points: bins.map(([p, o]) => [p, o]) }]}
    latest=${`Brier ${cal.brier.toFixed(4)}`}
    source=${GEN} n=${`${n} pairs, ${bins.length} bins`}
    note=${`reliability ${cal.reliability.toFixed(4)} · resolution ${cal.resolution.toFixed(4)} · uncertainty ${cal.uncertainty.toFixed(4)}`} />`;
}

function FirstHour({ fh }) {
  if (!fh || !fh.model) return null;
  return html`<${Bars} title="A fresh appliance's first hour" max=${1} unit="ratio"
    rows=${HEADLINE.filter(([k]) => k !== "repair_gestures").map(([k, label]) => [
      { key: `${k}-m`, label: `${label} — model`, value: fh.model[k].point, tone: null },
      { key: `${k}-f`, label: `${label} — formula`, value: fh.formula[k].point, tone: "muted" },
    ]).flat()}
    source=${GEN} span=${`${fh.model.streams} streams, first hour each; ${fh.model.decisions} join decisions`}
    note="no memory yet: day-0 behaviour" />`;
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
