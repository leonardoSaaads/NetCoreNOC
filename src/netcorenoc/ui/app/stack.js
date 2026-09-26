/* A stacked area of a STOCK — how many alarms are active — and a trend line for one row (v0.23.0).
 *
 * `charts.Series` draws flows and gauges as lines and columns. The Overview's "What is happening"
 * chart is neither: it is a count of alarms active at each instant, by severity (#393), and the
 * number an operator reads first is the TOTAL. So the bands are stacked, most severe at the bottom
 * where the eye lands, and the top edge of the stack is the total; the header prints the total now
 * and the most severe bands by name. Every bucket carries a hover title with its time and each
 * band's count, so the exact value is one pointer away without a legend lookup.
 *
 * Hand-written SVG, like every chart here: the DOM harness can read its geometry.
 */

import { html, cx } from "./dom.js";
import { unitText } from "./chartdata.js";
import { Legend, Caption } from "./charts.js";
import { count } from "./format.js";
import { severityTone } from "./widgets.js";

const H = 40;

/** Headroom above the peak: the top of the stack is data, and it must not touch the frame. */
const HEADROOM = 1.12;
const GRID = 4;

/** A round top at least 12 % above `v`, on steps that divide into four clean gridlines:
 *  100 active reads on a 0–120 axis, 2 037 on 0–2 400 (v0.24.0, #398). */
function niceTop(v) {
  const want = Math.max(1, v) * HEADROOM;
  const p = 10 ** Math.floor(Math.log10(want));
  return [1, 1.2, 1.6, 2, 2.4, 3, 4, 5, 6, 8, 10].map((m) => m * p).find((t) => t >= want) ?? 10 * p;
}

/** `points` for a polygon between two cumulative runs, left to right along the top and back. */
function scale(n, top) {
  const x = (i) => (n < 2 ? 0 : (i / (n - 1)) * 100).toFixed(2);
  const y = (v) => (H - (Math.min(v, top) / (top || 1)) * H).toFixed(2);
  return { x, y };
}

function band(lower, upper, top) {
  const { x, y } = scale(upper.length, top);
  const along = upper.map((v, i) => `${x(i)},${y(v)}`);
  const back = lower.map((v, i) => `${x(i)},${y(v)}`).reverse();
  return { area: [...along, ...back].join(" "), edge: along.join(" ") };
}

/**
 * `bands`: `[{ key, label, level, values }]` in stacking order (bottom first). `labels`: one per
 * bucket. The total is the sum of the bands at each bucket; the header reads its last value.
 */
export function StackedArea({ title, bands, labels = [], source, span, note }) {
  const shown = (bands || []).filter((b) => (b.values || []).some((v) => v > 0));
  const n = Math.max(0, ...(bands || []).map((b) => (b.values || []).length));
  const totals = Array.from({ length: n }, (_, i) =>
    (bands || []).reduce((sum, b) => sum + Number((b.values || [])[i] || 0), 0));
  const now = n ? totals[n - 1] : null;
  const top = niceTop(Math.max(1, ...totals));
  let lower = new Array(n).fill(0);
  const layers = shown.map((b) => {
    const upper = lower.map((v, i) => v + Number(b.values[i] || 0));
    const layer = { key: b.key, ...band(lower, upper, top) };
    lower = upper;
    return layer;
  });
  const worst = shown.slice(0, 2).map((b) => `${count(b.values[n - 1] || 0)} ${b.label}`);
  const aria = `${title}. Now ${now == null ? "not measured" : count(now)} active` +
    `${worst.length ? `: ${worst.join(", ")}` : ""}.`;
  const slot = 100 / Math.max(1, n);
  const ticks = Array.from({ length: GRID + 1 }, (_, i) => (top / GRID) * i);
  const gridY = (t) => (H - (t / top) * H).toFixed(2);
  const xAt = (i) => (n < 2 ? 50 : (i / (n - 1)) * 100);
  return html`<section class="chart-block">
    <div class="chart-head">
      <h4 class="chart-title">${title}</h4>
      <p class="chart-latest"><span class="chart-latest-kind">Now </span>
        <b>${now == null ? "—" : count(now)}</b> active
        ${worst.length ? html`<span class="chart-sep"> · </span>
          <span class="chart-latest-name">${worst.join(" · ")}</span>` : null}</p>
    </div>
    <div class="chart chart-stack" data-chart="stack" role="img" aria-label=${aria}>
      <div class="chart-plot stack-plot">
        <svg viewBox=${`0 0 100 ${H}`} preserveAspectRatio="none" focusable="false"
             aria-hidden="true">
          ${ticks.map((t) => html`<line key=${`g${t}`} class="stack-grid" x1="0" x2="100"
               y1=${gridY(t)} y2=${gridY(t)} />`)}
          ${layers.map((l) => html`<polygon key=${l.key} class=${cx("stack-area", `stack-${l.key}`)}
               points=${l.area} />`)}
          ${layers.map((l) => html`<polyline key=${`e${l.key}`}
               class=${cx("stack-edge", `stack-${l.key}`)} points=${l.edge} />`)}
          ${totals.map((t, i) => html`<g key=${i} class="stack-col">
            <line class="stack-cursor" x1=${xAt(i).toFixed(2)} x2=${xAt(i).toFixed(2)} y1="0"
                  y2=${H} />
            <rect class="stack-hit" x=${Math.max(0, xAt(i) - slot / 2).toFixed(2)} y="0"
                  width=${slot.toFixed(2)} height=${H}>
              <title>${`${labels[i] ?? ""} — ${count(t)} active` +
                shown.map((b) => `\n${b.label}: ${count(b.values[i] || 0)}`).join("")}</title>
            </rect>
          </g>`)}
        <//>
        <div class="stack-y" aria-hidden="true">
          ${ticks.map((t) => html`<span key=${t} style=${`top:${((1 - t / top) * 100).toFixed(2)}%`}
            >${unitText(t, "")}</span>`)}
        </div>
      </div>
      <div class="chart-x" aria-hidden="true">
        ${[0, Math.floor((n - 1) / 2), n - 1].filter((i, at, all) => i >= 0 && all.indexOf(i) === at)
          .map((i) => html`<span key=${i} class="chart-x-tick">${labels[i] ?? ""}</span>`)}
      </div>
    </div>
    <${Legend} series=${shown.map((b) => ({ name: b.label, tone: severityTone(b.key), level: b.level,
                                           values: b.values }))} />
    <${Caption} source=${source} span=${span} note=${note} />
  <//>`;
}

/** A trend in a table row: a line scaled to its own peak, whose peak is printed beside it. */
export function Trend({ values, label }) {
  const list = values || [];
  const peak = Math.max(1, ...list);
  const n = list.length;
  const points = list.map((v, i) =>
    `${(n < 2 ? 0 : (i / (n - 1)) * 100).toFixed(1)},${(22 - (v / peak) * 20).toFixed(1)}`);
  return html`<svg class="trend" viewBox="0 0 100 24" preserveAspectRatio="none"
       role="img" aria-label=${label} focusable="false">
    <polyline points=${points.join(" ")} />
  </svg>`;
}
