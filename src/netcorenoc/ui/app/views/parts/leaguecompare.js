/* Judge → every model on one axis (v0.27.0, ADR #427).
 *
 * The comparisons that are fair to draw together, and only those: every model in the league was
 * trained on the same rows, tuned on the same validation streams and measured on the same test
 * streams, over the same feature vector. So the curves overlay — ROC, precision–recall, reliability,
 * and the search's best-so-far validation loss — and the per-model numbers share a scale.
 *
 * What is **not** overlaid: a hyperparameter's axis (each family has its own) and the train ×
 * validation curve (each family's capacity axis is different — rounds, trees, depth, iterations).
 * Those stay on the per-model view.
 *
 * Each model keeps one colour on every chart and its name is in every legend, so no reading rests
 * on colour alone.
 */

import { html } from "../../dom.js";
import { Bars } from "../../compare.js";
import { Legend } from "../../charts.js";
import { Curve, Scatter } from "../../modelcharts.js";
import { GEN, suiteRows } from "./leaguecharts.js";

const VALID = "generated data, validation streams";

/** One colour per model, in league order: `chart-m0` … `chart-m6` in the stylesheet (seven
 *  kinds since v0.29.0 added XGBoost and k-nearest neighbours). */
export const toneOf = (index) => `m${index % 7}`;

function iidPairs(model) {
  return (model.pairs || {}).test_iid || {};
}

function bestSoFar(trials) {
  let low = Infinity;
  return trials.filter((t) => t.valid_loss != null).map((t, i) => {
    low = Math.min(low, t.valid_loss);
    return [i + 1, low];
  });
}

export function CompareCharts({ models }) {
  const trained = (models || []).filter((m) => m.origin !== "site");
  if (!trained.length) {
    return html`<p class="hint">No pre-trained model is loaded, so there is nothing to compare.</p>`;
  }
  const legend = trained.map((m, i) => ({ name: m.name, tone: toneOf(i) }));
  const curve = (pick) => trained.map((m, i) => ({ name: m.name, tone: toneOf(i), points: pick(m) }));
  const bars = (pick) => trained.map((m, i) => ({ key: m.ref, label: m.name, value: pick(m), tone: toneOf(i) }));
  const n = trained.map((m) => iidPairs(m).pairs).filter(Boolean)[0];
  const span = n ? `${n} pairs per model` : "";
  // v0.29.0: three questions, each a row — how good, how honest, what it costs. Titles say what
  // is drawn and nothing else; the axis that spans decades is logarithmic and says so.
  const scores = trained.map(meanF1).filter((v) => v != null);
  const floor = scores.length ? Math.max(0, Math.floor((Math.min(...scores) - 0.05) * 20) / 20) : 0;
  return html`<div class="league-compare">
    <${Legend} series=${legend} />
    <h4 class="league-group">How good</h4>
    <div class="judge-grid">
      <${Bars} title="Score (mean pairwise F1)" max=${1} from=${floor} unit="ratio"
        rows=${bars(meanF1)} source=${`${GEN}; axis from ${floor.toFixed(2)}`} span="the judge's offline score"
        note="higher is better" />
      <${Bars} title="Hand-labelled corpus" max=${1} unit="ratio"
        rows=${bars((m) => (m.suites || {}).corpus ?? null)}
        source="eval/corpus, hand-labelled" span="mean over scenarios" note="higher is better" />
      <${Curve} title="ROC" xLabel="false positive rate" yLabel="true positive rate" xLog=${true} keyed=${false}
        xRange=${[0, 1]} reference="diagonal"
        series=${curve((m) => (iidPairs(m).roc_curve || []).map(([f, t]) => [f, t]))}
        source=${GEN} n=${span} note="up and left is better" />
      <${Curve} title="Precision–recall" xLabel="recall" yLabel="precision" xRange=${[0, 1]} keyed=${false}
        reference=${{ y: iidPairs(trained[0]).positive_rate_weighted }}
        series=${curve((m) => (iidPairs(m).pr_curve || []).map(([r, p]) => [r, p]))}
        source=${GEN} n=${span} note="up and right is better" />
    </div>
    <h4 class="league-group">How honest</h4>
    <div class="judge-grid">
      <${Curve} title="Prediction vs actual" xLabel="predicted probability" keyed=${false}
        yLabel="observed rate" xRange=${[0, 1]} yRange=${[0, 1]} reference="diagonal"
        series=${curve((m) => ((iidPairs(m).calibration || {}).bins || []).filter((b) => b[2] > 0).map(([p, o]) => [p, o]))}
        source=${GEN} n=${span} note="on the diagonal is honest" />
      <${Bars} title="Mean |residual|" unit="ratio"
        rows=${bars((m) => (iidPairs(m).residuals || {}).mean_abs ?? null)}
        source=${GEN} span=${span} note="lower is better" />
    </div>
    <h4 class="league-group">What it costs</h4>
    <div class="judge-grid">
      <${Curve} title="Search: best validation loss" xLabel="fit, in order" yLog=${true} keyed=${false}
        yLabel="log loss" series=${curve((m) => bestSoFar((m.search || {}).trials || []))}
        source=${VALID} n="same validation rows for every model" note="lower is better" />
      <div>
        <${Scatter} title="Training time × score" logX=${true}
          points=${trained.map((m, i) => ({ x: searchSeconds(m), y: meanF1(m), tone: toneOf(i) }))}
          xLabel="seconds to search and fit (log)" yLabel="score"
          source=${`${GEN}; seconds on the training machine`} n=${`${trained.length} models`} />
      </div>
      <${Bars} title="Scoring cost per pair" unit="µs" log=${true}
        rows=${bars((m) => (m.latency || {}).appliance_us ?? null)}
        source="generated benchmark pairs, timed here at start-up" span="median of three passes"
        note="log scale" />
    </div>
  </div>`;
}

export function meanF1(model) {
  const values = suiteRows(model).map((r) => r.value).filter((v) => v != null);
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
}

function searchSeconds(model) {
  const trials = (model.search || {}).trials || [];
  const search = trials.reduce((a, t) => a + (t.seconds || 0), 0);
  return search + ((model.final_fit || {}).seconds || 0);
}
