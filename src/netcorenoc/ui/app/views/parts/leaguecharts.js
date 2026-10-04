/* Judge → one model, in ten charts (v0.27.0, ADR #427).
 *
 * The maintainer asked for these ten, in this order, for every model the league holds:
 *
 *   1 validation score × hyperparameter   2 train × validation curve    3 model performance
 *   4 hyperparameter importance           5 optimisation history        6 confusion matrix
 *   7 ROC / precision–recall              8 prediction vs actual        9 residual distribution
 *   10 training time × performance
 *
 * **Two of them are regression charts, and these models are classifiers** — they predict the
 * probability that two alarms share an incident. They are drawn in the form that means the same
 * thing for a probability: *prediction vs actual* is the **reliability curve** (predicted
 * probability against the observed rate in each bin; on the diagonal is honest), and the *residual*
 * is ``y − p``, drawn as its distribution per true class. Each caption says so.
 *
 * Every number is from the model's manifest: **generated data**, measured when the build was
 * trained, on streams it never trained or tuned on. A model adapted on this site has no such
 * record, and its charts say where its evidence is instead.
 */

import { html, Component } from "../../dom.js";
import { Bars } from "../../compare.js";
import { Curve, Scatter, Confusion, Versus, Histogram } from "../../modelcharts.js";

export const GEN = "generated data (eval/synth), held-out test streams";
const VALID = "generated data, validation streams";
const CAPACITY = {
  rounds: "boosting round",
  trees: "trees in the forest",
  depth: "tree depth",
  iterations: "Newton iteration",
};
export const SUITES = {
  test_iid: "unseen streams",
  test_concurrency: "concurrent incidents",
  test_optical: "held-out optical families",
  test_protocol: "held-out protocol families",
  test_adverse: "adverse conditions",
  corpus: "hand-labelled corpus",
};
const HEADLINE = [
  ["pairwise_f1", "pairwise F1", false],
  ["ari", "adjusted Rand", false],
  ["over_merge_rate", "over-merge", true],
  ["under_merge_rate", "under-merge", true],
  ["repair_gestures", "repair gestures / incident", true],
];

const pct = (v) => `${(100 * v).toFixed(1)}%`;

/** The chart's name. v0.29.0: the maintainer's numbers (1-10) left the titles — they were noise
 *  on screen — and stay here, as each call's first argument, to map the list onto the code. */
const T = (_k, name) => name;

export class ModelCharts extends Component {
  constructor(props) {
    super(props);
    this.state = { param: null };
  }

  render({ model }, { param }) {
    if (!model) return null;
    if (model.origin === "site") {
      return html`<p class="hint">This model was fitted on this site's labels by the in-product
        search. It has no generated-data record; its evidence is the paired comparison under${" "}
        <b>This site</b>, on labels that arrived after it was fitted.</p>`;
    }
    const trials = ((model.search || {}).trials || []).filter((t) => t.valid_loss != null);
    const first = trials.filter((t) => t.rung === 0);
    const names = Object.keys((trials[0] && trials[0].params) || {});
    const chosen = param && names.includes(param) ? param : names[0];
    const space = (model.search || {}).space || {};
    const fit = model.final_fit || {};
    const iid = (model.splits || {}).test_iid || {};
    const pairs = (model.pairs || {}).test_iid || {};
    const res = pairs.residuals || {};
    const cal = pairs.calibration || {};
    const nPairs = pairs.pairs ? `${pairs.pairs} pairs, ${pairs.streams} streams` : "";
    const importance = Object.entries((model.search || {}).importance || {}).sort((a, b) => b[1] - a[1]);
    return html`<div class="judge-grid league-charts" data-model=${model.kind}>
      <div class="mchart-wrap">
        ${names.length > 1 ? html`<label class="mchart-pick">Hyperparameter
          <select value=${chosen} onChange=${(e) => this.setState({ param: e.target.value })}>
            ${names.map((p) => html`<option key=${p} value=${p}>${p}</option>`)}
          </select></label>` : null}
        <${Scatter} title=${T(1, `Validation score × ${chosen || "hyperparameter"}`)}
          logX=${(space[chosen] || [])[0] === "log"}
          points=${first.map((t) => ({ x: t.params[chosen], y: t.valid_loss }))}
          xLabel=${chosen || "—"} yLabel="validation log loss (lower is better)"
          source=${VALID} n=${`${first.length} first-rung fits`} />
      </div>
      <${Curve} title=${T(2, "Train × validation curve")} xLabel=${CAPACITY[fit.capacity] || "capacity"}
        yLabel="log loss"
        series=${[
          { name: "train", tone: "muted", points: (fit.points || []).map((x, i) => [x, (fit.train_trace || [])[i]]) },
          { name: "validation", tone: null, points: (fit.points || []).map((x, i) => [x, (fit.valid_trace || [])[i]]) },
        ]}
        latest=${fit.best != null ? `kept at ${fit.best}` : null} source=${VALID}
        n=${`${(fit.points || []).length} checkpoints`}
        note="the gap between the lines is overfitting; validation chose where to stop" />
      <section class="chart-block mchart league-performance">
        <h4 class="chart-title">${T(3, "Model performance")}</h4>
        <div class="versus-row">
          ${HEADLINE.map(([k, label, lower]) => html`<${Versus} key=${k} label=${label}
            model=${iid.model && iid.model[k]} formula=${iid.formula && iid.formula[k]} lower=${lower} />`)}
        </div>
        <p class="chart-caption">${GEN} — ${SUITES.test_iid}; 95 % bootstrap over streams.
          ▲/▼ against the retired additive formula on the same streams, kept as a baseline.</p>
      </section>
      <${Bars} title=${T(3, "Pairwise F1 on every suite")} max=${1} unit="ratio"
        rows=${suiteRows(model)} source=${`${GEN}; the corpus is hand-labelled`}
        span=${"the suites the judge averages"} note="higher is better" />
      <${Bars} title=${T(3, "Hand-labelled corpus, scenario by scenario")} max=${1} unit="ratio"
        rows=${corpusRows(model)} source="eval/corpus: hand-labelled, replayed through the engine"
        span=${`${corpusRows(model).length} scenarios; the judge averages them, each weighing the same`}
        note="pairwise F1; red: below 0.95" />
      <${Bars} title=${T(4, "Hyperparameter importance")} max=${1} unit="ratio"
        rows=${importance.map(([k, v]) => ({ key: k, label: k, value: v, tone: null }))}
        source=${VALID} span=${`${first.length} first-rung fits`}
        note="Spearman ρ² with the validation loss — a rank correlation, not fANOVA" />
      <${Scatter} title=${T(5, "Optimisation history")} step=${true}
        points=${trials.map((t, i) => ({ x: i + 1, y: t.valid_loss, tone: t.rung > 0 ? null : "muted" }))}
        xLabel="fit, in order" yLabel="validation log loss" source=${VALID} n=${`${trials.length} fits`}
        note="dashed: best so far; dark dots survived a halving round" />
      <${Confusion} title=${T(6, "Confusion matrix")} matrix=${pairs.confusion} source=${GEN}
        n=${nPairs} />
      <${Curve} title=${T(7, "ROC")} xLabel="false positive rate" yLabel="true positive rate"
        xRange=${[0, 1]} reference="diagonal" xLog=${true}
        series=${[{ name: model.name, tone: null, points: (pairs.roc_curve || []).map(([f, t]) => [f, t]) }]}
        latest=${pairs.roc_auc ? `AUC ${pairs.roc_auc.point.toFixed(3)}` : null} source=${GEN} n=${nPairs}
        note="read beside precision–recall: under class imbalance ROC flatters" />
      <${Curve} title=${T(7, "Precision–recall")} xLabel="recall" yLabel="precision"
        xRange=${[0, 1]} reference=${{ y: pairs.positive_rate_weighted }}
        series=${[{ name: model.name, tone: null, points: (pairs.pr_curve || []).map(([r, p]) => [r, p]) }]}
        latest=${pairs.average_precision ? `AP ${pairs.average_precision.point.toFixed(3)}` : null}
        source=${GEN} n=${nPairs}
        note=${`dashed: the baseline positive rate ${(pairs.positive_rate_weighted ?? 0).toFixed(3)}`} />
      <${Curve} title=${T(8, "Prediction vs actual")} xLabel="predicted probability" yLabel="observed rate"
        xRange=${[0, 1]} yRange=${[0, 1]} reference="diagonal"
        series=${[{ name: model.name, tone: null, points: (cal.bins || []).filter((b) => b[2] > 0).map(([p, o]) => [p, o]) }]}
        latest=${cal.brier != null ? `Brier ${cal.brier.toFixed(4)}` : null} source=${GEN} n=${nPairs}
        note="a classifier's prediction vs actual: on the diagonal, a stated 0.8 comes true 80 % of the time" />
      <${Histogram} title=${T(9, "Residual distribution")} edges=${res.edges} xLabel="residual y − p"
        series=${[
          { name: "same incident (y = 1)", tone: null, values: res.positive || [] },
          { name: "different incidents (y = 0)", tone: "alarm", values: res.negative || [] },
        ]}
        latest=${res.mean_abs != null ? `mean |y − p| ${res.mean_abs.toFixed(3)}` : null}
        source=${GEN} n=${nPairs} note="near 0: confident and right; near ±1: confident and wrong" />
      <${Scatter} title=${T(10, "Training time × performance")} logX=${true}
        points=${trials.map((t) => ({ x: t.seconds, y: t.valid_loss, tone: t.rung > 0 ? null : "muted" }))}
        xLabel="seconds to fit (log)" yLabel="validation log loss" source=${VALID} n=${`${trials.length} fits`}
        note=${`final fit ${fit.seconds ?? "—"} s; scoring ${latencyText(model)}`} />
    </div>`;
  }
}

export function latencyText(model) {
  const lat = model.latency || {};
  const here = lat.appliance_us;
  const there = (lat.training || {}).logit_us;
  if (here != null) return `${here.toFixed(1)} µs per pair on this appliance`;
  if (there != null) return `${there.toFixed(1)} µs per pair on the training machine`;
  return "not measured";
}

/** The judge's five suite scores, exactly as the server ranked on them (the corpus is the mean
 * over its scenarios, each weighing the same — ADR #429). */
export function suiteRows(model) {
  const got = model.suites || {};
  return Object.entries(SUITES).map(([key, label]) => ({
    key, label, value: got[key] ?? null, tone: key === "corpus" ? "warn" : null,
  }));
}

/** One bar per corpus scenario: where a model that scores well on the mean still fails. */
export function corpusRows(model) {
  const scenarios = (model.corpus || {}).scenarios || {};
  return Object.entries(scenarios).map(([name, m]) => ({
    key: name, label: name.replaceAll("_", " "), value: m.pairwise_f1 ?? null,
    tone: m.pairwise_f1 != null && m.pairwise_f1 < 0.95 ? "alarm" : null,
  }));
}

export { pct };
