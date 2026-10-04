/* The charts a trained model is judged by (v0.26.0, ADR #414). Hand-written SVG, no library.
 *
 * Same rules as `charts.js`, and they are the reason this file does not import a chart library:
 *
 *   * **geometry only in the SVG**, in a fixed `viewBox` stretched with `preserveAspectRatio="none"`,
 *     and **every label is HTML** — so type stays at the stylesheet's size at 390 px and at 1440;
 *   * **a caption is required**: the dataset (generated, site, or live — never unlabelled) and the
 *     sample size. Generated data and site data are different claims, and a chart that did not say
 *     which it drew could put them on one axis without anyone noticing;
 *   * **nothing unmeasured is drawn as zero**: an empty series renders `Unmeasured`.
 *
 * Points are zero-length lines with round caps and `vector-effect: non-scaling-stroke`, so a dot
 * stays round in a stretched box — a `<circle>` would become an ellipse.
 */

import { html, cx } from "./dom.js";
import { Caption, Legend, Unmeasured } from "./charts.js";

const W = 100;
const H = 60;

const fmt = (v, digits = 3) => (v == null || Number.isNaN(v) ? "—" : Number(v).toFixed(digits));

/** Map a value in [lo, hi] onto the plot's x or y. `log` for strictly positive axes. */
function scaler(lo, hi, size, { invert = false, log = false } = {}) {
  const f = log ? (v) => Math.log(Math.max(v, 1e-12)) : (v) => v;
  const a = f(lo);
  const b = f(hi);
  const span = b - a || 1;
  return (v) => {
    const t = (f(v) - a) / span;
    return (invert ? 1 - t : t) * size;
  };
}

/** The smallest positive value — where a log axis starts. */
const low = (values) => Math.min(1, ...values.filter((v) => v > 0)) || 1e-6;
/** An axis end: two decimals, or `1e-4` when two decimals would print 0.00. */
const tick = (v) => (v != null && v > 0 && v < 0.005 ? Number(v).toExponential(0) : fmt(v, 2));

function extent(values, pad = 0) {
  const finite = values.filter((v) => v != null && Number.isFinite(v));
  if (!finite.length) return [0, 1];
  let lo = Math.min(...finite);
  let hi = Math.max(...finite);
  if (lo === hi) { lo -= 0.5; hi += 0.5; }
  const p = (hi - lo) * pad;
  return [lo - p, hi + p];
}

function Frame({ title, latest, children, caption, legend, xLabel, yLabel, yTop, yBottom, xLow, xHigh, tall }) {
  return html`<section class="chart-block mchart">
    <div class="chart-head">
      <h4 class="chart-title">${title}</h4>
      ${latest != null ? html`<p class="chart-latest">${latest}</p>` : null}
    </div>
    <div class=${cx("chart", tall && "mchart-tall")}>
      <div class="chart-plot">
        ${children}
        <div class="chart-y" aria-hidden="true"><span>${yTop}</span><span>${yBottom}</span></div>
      </div>
      <div class="chart-x" aria-hidden="true">
        <span class="chart-x-tick">${xLow}</span>
        <span class="chart-x-tick mchart-axis">${xLabel}</span>
        <span class="chart-x-tick">${xHigh}</span>
      </div>
      ${yLabel ? html`<p class="mchart-ylabel">${yLabel}</p>` : null}
    </div>
    ${legend ? html`<${Legend} series=${legend} />` : null}
    ${caption}
  <//>`;
}

/**
 * Lines over x in [x0, x1] and y in [0, 1] (or a given range). `series` is
 * `[{ name, tone, points: [[x, y], ...] }]`; `reference` draws the diagonal (`"diagonal"`) or a
 * horizontal baseline (`{ y }`) — the thing a curve is read against.
 */
export function Curve({ title, series, reference, xRange, yRange, xLabel, yLabel, source, n, note, latest, tall,
  xLog = false, yLog = false, keyed = true }) {
  const drawn = (series || []).filter((s) => (s.points || []).length);
  if (!drawn.length) {
    return html`<section class="chart-block mchart">
      <h4 class="chart-title">${title}</h4>
      <${Unmeasured} what=${title} why=${note || "no data"} />
      <${Caption} source=${source} span=${n} />
    <//>`;
  }
  const xs = drawn.flatMap((s) => s.points.map((p) => p[0]));
  const ys = drawn.flatMap((s) => s.points.map((p) => p[1]));
  let [x0, x1] = xRange || extent(xs);
  let [y0, y1] = yRange || extent(ys, 0.05);
  // v0.29.0: a log axis starts at the smallest positive value drawn; a 0 is pinned to that edge.
  if (xLog) x0 = Math.max(x0, low(xs));
  if (yLog) y0 = Math.max(y0, low(ys));
  const sx0 = scaler(x0, x1, W, { log: xLog });
  const sy0 = scaler(y0, y1, H, { invert: true, log: yLog });
  const sx = (x) => sx0(Math.max(x, x0));
  const sy = (y) => sy0(Math.min(Math.max(y, y0), y1));
  const ref = Array.from({ length: 25 }, (_, i) => (xLog ? x0 * (x1 / x0) ** (i / 24) : x0 + (x1 - x0) * i / 24));
  const label = `${title}. ${drawn.map((s) => `${s.name}: ${s.points.length} points`).join("; ")}.`;
  return html`<${Frame} title=${title} latest=${latest} tall=${tall}
      yTop=${tick(y1)} yBottom=${tick(y0)} xLow=${tick(x0)} xHigh=${tick(x1)}
      xLabel=${xLog ? `${xLabel} (log)` : xLabel} yLabel=${yLog ? `${yLabel} (log)` : yLabel}
      legend=${keyed && drawn.length > 1 ? drawn : null}
      caption=${html`<${Caption} source=${source} span=${n} note=${note} />`}>
    <svg viewBox=${`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label=${label}
         class="mchart-svg" focusable="false">
      ${reference === "diagonal"
        ? html`<polyline class="mchart-ref" fill="none" points=${ref.map((v) => `${sx(v).toFixed(2)},${sy(v).toFixed(2)}`).join(" ")} />`
        : reference && reference.y != null
          ? html`<line class="mchart-ref" x1="0" y1=${sy(reference.y)} x2=${W} y2=${sy(reference.y)} />`
          : null}
      ${drawn.map((s) => html`<polyline key=${s.name} class=${cx("mchart-line", s.tone && `chart-${s.tone}`)}
        points=${s.points.map(([x, y]) => `${sx(x).toFixed(2)},${sy(y).toFixed(2)}`).join(" ")} />`)}
    </svg>
  <//>`;
}

/**
 * Points, optionally with a step line (the best-so-far of an optimisation history). `points` is
 * `[{ x, y, tone }]`; `logX` for a hyperparameter drawn on a log scale.
 */
export function Scatter({ title, points, step, xLabel, yLabel, source, n, note, logX, latest }) {
  const drawn = (points || []).filter((p) => p.y != null && Number.isFinite(p.y));
  if (!drawn.length) {
    return html`<section class="chart-block mchart">
      <h4 class="chart-title">${title}</h4>
      <${Unmeasured} what=${title} why=${note || "no trials yet"} />
      <${Caption} source=${source} span=${n} />
    <//>`;
  }
  const [x0, x1] = extent(drawn.map((p) => p.x), logX ? 0 : 0.03);
  const [y0, y1] = extent(drawn.map((p) => p.y), 0.08);
  const sx = scaler(logX ? Math.max(x0, 1e-9) : x0, x1, W, { log: logX });
  const sy = scaler(y0, y1, H, { invert: true });
  const best = [];
  if (step) {
    let low = Infinity;
    [...drawn].sort((a, b) => a.x - b.x).forEach((p) => {
      low = Math.min(low, p.y);
      best.push(`${sx(p.x).toFixed(2)},${sy(low).toFixed(2)}`);
    });
  }
  return html`<${Frame} title=${title} latest=${latest}
      yTop=${fmt(y1, 3)} yBottom=${fmt(y0, 3)} xLow=${fmt(x0, logX ? 3 : 1)} xHigh=${fmt(x1, logX ? 3 : 1)}
      xLabel=${xLabel} yLabel=${yLabel}
      caption=${html`<${Caption} source=${source} span=${n} note=${note} />`}>
    <svg viewBox=${`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" class="mchart-svg"
         aria-label=${`${title}. ${drawn.length} points; lowest ${fmt(y0 + (y1 - y0) * 0.08 / 1.16, 3)}.`}
         focusable="false">
      ${step ? html`<polyline class="mchart-step" points=${best.join(" ")} />` : null}
      ${drawn.map((p, i) => html`<line key=${i} class=${cx("mchart-dot", p.tone && `chart-${p.tone}`)}
        x1=${sx(p.x).toFixed(2)} y1=${sy(p.y).toFixed(2)} x2=${sx(p.x).toFixed(2)} y2=${sy(p.y).toFixed(2)} />`)}
    </svg>
  <//>`;
}

/**
 * A shape function: one feature's contribution to the logit, per bin. The x axis is the **bin**,
 * not the value — `dt` runs from 0 to 3 600 s and its interesting bins are all under a minute, so a
 * value axis would squash the shape into one pixel. The first and last edges are printed instead.
 */
export function Steps({ feature, edges, scores, source }) {
  if (!scores || !scores.length) return null;
  const lo = Math.min(0, ...scores);
  const hi = Math.max(0, ...scores);
  const sy = scaler(lo === hi ? lo - 1 : lo, hi, H, { invert: true });
  const step = W / scores.length;
  const zero = sy(0);
  const range = `${fmt(Math.min(...scores), 2)} … ${fmt(Math.max(...scores), 2)}`;
  return html`<div class="mchart-shape">
    <div class="mchart-shape-head"><b>${feature}</b> <span>${range}</span></div>
    <div class="chart-plot mchart-shape-plot">
      <svg viewBox=${`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" focusable="false"
           aria-label=${`${feature}: contribution per bin, from ${range}`} class="mchart-svg">
        <line class="mchart-ref" x1="0" y1=${zero} x2=${W} y2=${zero} />
        ${scores.map((s, i) => html`<rect key=${i}
          class=${s >= 0 ? "mchart-up" : "mchart-down"}
          x=${(i * step).toFixed(2)} width=${Math.max(step - 0.4, 0.2).toFixed(2)}
          y=${Math.min(zero, sy(s)).toFixed(2)} height=${Math.abs(sy(s) - zero).toFixed(2)} />`)}
      </svg>
    </div>
    <div class="chart-x" aria-hidden="true">
      <span class="chart-x-tick">${edges.length ? `< ${fmt(edges[0], edges[0] < 10 ? 2 : 0)}` : "all"}</span>
      <span class="chart-x-tick">${edges.length ? `≥ ${fmt(edges[edges.length - 1], edges[edges.length - 1] < 10 ? 2 : 0)}` : ""}</span>
    </div>
    ${source ? html`<p class="chart-caption">${source}</p>` : null}
  </div>`;
}

/** The confusion matrix at the operating threshold, as a 2 × 2 grid of weighted counts. */
export function Confusion({ matrix, source, n, title = "Confusion at the threshold" }) {
  if (!matrix) return null;
  const total = matrix.tp + matrix.fp + matrix.tn + matrix.fn || 1;
  const cellOf = (label, value, tone) => html`<div class=${cx("mchart-cm-cell", tone)}>
    <b>${(100 * value / total).toFixed(1)}%</b><span>${label}</span></div>`;
  return html`<section class="chart-block mchart">
    <h4 class="chart-title">${title}</h4>
    <div class="chart mchart-cm" data-chart="confusion" role="img"
      aria-label=${`${title}. precision ${fmt(matrix.precision, 3)}, recall ${fmt(matrix.recall, 3)}.`}>
      <span></span><span class="mchart-cm-h">same incident</span><span class="mchart-cm-h">different</span>
      <span class="mchart-cm-h">linked</span>
      ${cellOf("true positive", matrix.tp, "good")}${cellOf("false positive", matrix.fp, "bad")}
      <span class="mchart-cm-h">not linked</span>
      ${cellOf("false negative", matrix.fn, "bad")}${cellOf("true negative", matrix.tn, "good")}
    </div>
    <p class="chart-latest">precision <b>${fmt(matrix.precision, 3)}</b> · recall <b>${fmt(matrix.recall, 3)}</b></p>
    <${Caption} source=${source} span=${n} note="weighted: one unit per activation" />
  <//>`;
}

/**
 * One quantity, the model against the formula on the same streams, with the interval and n.
 * `lower` says which direction is better, so the tile can say which one won without colour alone.
 */
export function Versus({ label, model, formula, lower }) {
  if (!model) return null;
  const better = formula && (lower ? model.point < formula.point : model.point > formula.point);
  const worse = formula && (lower ? model.point > formula.point : model.point < formula.point);
  // The explicit spaces are F112's: four boxes on four rows still concatenate in textContent,
  // which is what a copy-paste and a screen reader get.
  return html`<div class=${cx("versus", better && "versus-better", worse && "versus-worse")}>
    <div class="versus-label">${label}</div>${" "}
    <div class="versus-value">${fmt(model.point, 3)}${" "}
      <span class="versus-mark" aria-label=${better ? "better than the formula" : worse ? "worse than the formula" : "same as the formula"}
        >${better ? "▲" : worse ? "▼" : "="}</span></div>${" "}
    <div class="versus-ci">${fmt(model.low, 3)} – ${fmt(model.high, 3)}</div>${" "}
    ${formula ? html`<div class="versus-formula">formula ${fmt(formula.point, 3)}</div>` : null}
  </div>`;
}

/**
 * Signed horizontal bars around zero — an ablation's loss change, which can go either way. HTML,
 * like `compare.js`'s `Bars`: a category is a name and needs the room.
 */
export function Diverging({ title, rows, source, n, note }) {
  const readable = (rows || []).filter((r) => r.value != null && Number.isFinite(r.value));
  if (!readable.length) {
    return html`<section class="chart-block mchart"><h4 class="chart-title">${title}</h4>
      <${Unmeasured} what=${title} why=${note || "not recorded"} />
      <${Caption} source=${source} span=${n} /><//>`;
  }
  const top = Math.max(...readable.map((r) => Math.abs(r.value))) || 1;
  return html`<section class="chart-block mchart">
    <h4 class="chart-title">${title}</h4>
    <div class="chart diverging" role="img"
         aria-label=${`${title}. ${readable.map((r) => `${r.label}: ${fmt(r.value, 4)}`).join(", ")}.`}>
      ${readable.map((r) => {
        const width = (Math.abs(r.value) / top) * 50;
        return html`<div class="chart-bar-row" key=${r.label}>
          <span class="chart-bar-label">${r.label}</span>
          <span class="diverging-track">
            <span class=${cx("chart-bar-fill", r.tone && `chart-${r.tone}`)}
              style=${`width:${width.toFixed(2)}%;${r.value < 0 ? `right:50%` : "left:50%"}`}></span>
          </span>
          <span class="chart-bar-value">${r.value > 0 ? "+" : ""}${fmt(r.value, 4)}</span>
        </div>`;
      })}
    </div>
    <${Caption} source=${source} span=${n} note=${note} />
  <//>`;
}

/**
 * A distribution over fixed bins — a classifier's residuals ``y − p`` split by the true class
 * (v0.27.0, ADR #427). Series side by side per bin, each a share of the weighted pairs. Mass near
 * the ends is confident error; mass near zero is confident truth.
 */
export function Histogram({ title, edges, series, xLabel, source, n, note, latest }) {
  const drawn = (series || []).filter((s) => (s.values || []).some((v) => v > 0));
  if (!drawn.length || !edges || edges.length < 2) {
    return html`<section class="chart-block mchart"><h4 class="chart-title">${title}</h4>
      <${Unmeasured} what=${title} why=${note || "not recorded"} />
      <${Caption} source=${source} span=${n} /><//>`;
  }
  const bins = edges.length - 1;
  const top = Math.max(...drawn.flatMap((s) => s.values)) || 1;
  const sy = scaler(0, top, H, { invert: true });
  const slot = W / bins;
  const bar = slot / (drawn.length + 0.5);
  return html`<${Frame} title=${title} latest=${latest}
      yTop=${`${(top * 100).toFixed(1)}%`} yBottom="0" xLow=${fmt(edges[0], 1)} xHigh=${fmt(edges[bins], 1)}
      xLabel=${xLabel} legend=${drawn.length > 1 ? drawn : null}
      caption=${html`<${Caption} source=${source} span=${n} note=${note} />`}>
    <svg viewBox=${`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" class="mchart-svg" focusable="false"
         aria-label=${`${title}. ${drawn.map((s) => `${s.name}: ${(100 * s.values.reduce((a, b) => a + b, 0)).toFixed(1)}% of pairs`).join("; ")}.`}>
      <line class="mchart-ref" x1=${W / 2} y1="0" x2=${W / 2} y2=${H} />
      ${drawn.map((s, k) => s.values.map((v, i) => v > 0 ? html`<rect key=${`${k}-${i}`}
        class=${cx("mchart-bin", s.tone && `chart-${s.tone}`)}
        x=${(i * slot + bar * (k + 0.25)).toFixed(2)} width=${(bar * 0.92).toFixed(2)}
        y=${sy(v).toFixed(2)} height=${(H - sy(v)).toFixed(2)} />` : null))}
    </svg>
  <//>`;
}

/**
 * Intervals around zero, one row each — a forest plot of paired differences (v0.27.0). `rows` is
 * `[{ label, mean, low, high, verdict }]`; the zero line is what each interval is read against, and
 * the verdict is printed beside it, so the reading never rests on colour.
 */
export function Forest({ title, rows, unit, source, n, note }) {
  const readable = (rows || []).filter((r) => r.mean != null && r.low != null && r.high != null);
  if (!readable.length) {
    return html`<section class="chart-block mchart"><h4 class="chart-title">${title}</h4>
      <${Unmeasured} what=${title} why=${note || "no comparison yet"} />
      <${Caption} source=${source} span=${n} /><//>`;
  }
  const reach = Math.max(...readable.flatMap((r) => [Math.abs(r.low), Math.abs(r.high)])) || 1;
  const at = (v) => `${(50 + (v / reach) * 48).toFixed(2)}%`;
  return html`<section class="chart-block mchart">
    <h4 class="chart-title">${title}</h4>
    <div class="chart forest" role="img"
      aria-label=${`${title}. ${readable.map((r) => `${r.label}: ${fmt(r.mean, 3)} (${fmt(r.low, 3)} to ${fmt(r.high, 3)}) ${r.verdict || ""}`).join("; ")}.`}>
      ${readable.map((r) => html`<div class="forest-row" key=${r.label}>
        <span class="chart-bar-label">${r.label}</span>
        <span class="forest-track">
          <span class="forest-zero"></span>
          <span class=${cx("forest-ci", r.verdict && `forest-${r.verdict}`)}
            style=${`left:${at(r.low)};width:calc(${at(r.high)} - ${at(r.low)})`}></span>
          <span class="forest-mean" style=${`left:${at(r.mean)}`}></span>
        </span>
        <span class="chart-bar-value">${fmt(r.mean, 3)}${unit ? ` ${unit}` : ""} · ${r.verdict || "—"}</span>
      </div>`)}
    </div>
    <${Caption} source=${source} span=${n} note=${note} />
  <//>`;
}
