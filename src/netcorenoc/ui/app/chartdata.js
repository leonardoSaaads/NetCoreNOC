/* What a series IS, separately from what a chart looks like.
 *
 * Every function here is **pure**, and that is why they are not in `charts.js`: the honesty rules
 * this release is built on are arithmetic, and arithmetic can be driven directly from a test with
 * no DOM, no fixture and no render — the same argument `parameters.js` makes for the hardening
 * refusals. `charts.js` decides what a chart looks like; this decides where the line breaks, what
 * the axis reads, and what span the data actually covers.
 *
 * The three rules that live here rather than in a caller:
 *
 *   * **a gap breaks the line** (`runs`) — never a dip to zero, never an interpolation;
 *   * **the axis is the span the data covers** (`buckets`) — derived from the oldest and newest
 *     datum and never from the window that was requested;
 *   * **a count's empty bucket is `0` and a sampler's missed bucket is `null`** (`tally`), because
 *     a quiet estate and an outage are different facts.
 */

import { count, score } from "./format.js";

/**
 * A number with its unit, in the console's existing vocabulary. `null` is always `—`.
 *
 * **`count` and `score` are imported and not reimplemented**, and a guard is the reason this line
 * says so: the first version wrote `Math.round(n).toLocaleString("en-US")` here and
 * `test_no_screen_renders_a_bare_locale_timestamp` went red. That guard is about *timestamps* and
 * this is a thousands separator, so the match was incidental — but it was pointing at something
 * real anyway, because `format.count` is that exact expression and `format.js` is the one module
 * allowed to own what a rendered number looks like. Two implementations of "group the thousands" is
 * how one chart comes to print `1359` beside another printing `1,359`.
 */
export function unitText(value, unit) {
  if (value == null) return "—";
  const n = Number(value);
  if (unit === "%") return `${n.toFixed(n < 10 ? 1 : 0)}%`;
  if (unit === "s") return `${score(n, 4)} s`;
  const whole = Math.round(n);
  const rounded = count(whole);
  // Units are plural nouns ("alarms", "rows"); one of them is singular — "1 alarms" shipped.
  const word = whole === 1 && /[a-z]s$/.test(unit) ? unit.slice(0, -1) : unit;
  return unit ? `${rounded} ${word}` : rounded;
}

/**
 * **Where the line breaks.** One implementation, imported by every chart and by the sparkline.
 *
 * Returns a list of runs of consecutive readable points, each run a list of `"x,y"` strings. A
 * `null` ends the current run and starts a new one; a run of one point cannot be a polyline and is
 * dropped, which is why a series alternating readings and gaps draws nothing rather than a row of
 * invisible one-point lines.
 *
 * A graph that joined across a period nobody measured would be inventing the measurement, which is
 * the one thing these charts must never do (DECISIONS #289, #300, #306).
 */
export function runs(values, { max = 100, height = 24, width = 100 } = {}) {
  const list = values || [];
  const step = width / Math.max(1, list.length - 1);
  const out = [];
  let run = [];
  list.forEach((value, index) => {
    if (value == null) {
      if (run.length > 1) out.push(run);
      run = [];
      return;
    }
    const y = height - (Math.max(0, Math.min(max, value)) / (max || 1)) * height;
    run.push(`${(index * step).toFixed(1)},${y.toFixed(1)}`);
  });
  if (run.length > 1) out.push(run);
  return out;
}

/**
 * A nice round ceiling at or above `value`, so a y axis reads 1, 2, 5, 10, 20, 50, 100…
 *
 * Scaling to the exact maximum puts `1 359` on the axis and makes every chart's gridline mean
 * something different; a round ceiling is one a reader can divide by eye. `0` yields `1`, because
 * an axis labelled `0` at both ends is not an axis.
 */
export function ceiling(value) {
  const v = Number(value) || 0;
  if (v <= 1) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(v));
  for (const factor of [1, 2, 5, 10]) {
    if (v <= factor * magnitude) return factor * magnitude;
  }
  return 10 * magnitude;
}

/**
 * **Bucket timestamps, and let the axis be the span the data actually covers.**
 *
 * `stamps` is every timestamp that matters. The returned edges span **the oldest and newest of
 * them**, never a requested window — which is the structural reason a chart here cannot claim a
 * range it does not hold. Measured on this project's own corpus:
 * `GET /api/timeline?limit=1000` came back **full, 1 000 marks spanning 15.6 seconds**, so a chart
 * titled *"last 7 days"* whose axis came from the request would have been wrong on its first
 * render, on the appliance this release was built against.
 *
 * Returns `{ edges, labels, index, spanS, n }`. `index(ts)` is the bucket a timestamp falls in, or
 * `-1`. An empty input returns `n: 0` and an `index` that answers `-1`, so a caller's counting
 * loop runs zero times rather than needing a guard of its own.
 */
export function buckets(stamps, wanted = 24) {
  const list = (stamps || []).filter((t) => t != null && Number.isFinite(Number(t))).map(Number);
  if (!list.length) return { edges: [], labels: [], index: () => -1, spanS: 0, n: 0 };
  const oldest = Math.min(...list);
  const newest = Math.max(...list);
  // **A span of zero is ONE bucket**, and this line is a defect its own test found. `n` was
  // `min(wanted, list.length)`, so two readings at the same instant produced two buckets one
  // second apart — an axis reading `00:08:20  00:08:21` over data that covers no time at all.
  // That is precisely the failure this module exists to prevent, reached from the inside: the
  // chart would have rendered perfectly and claimed a second it never measured.
  //
  // Never more buckets than readings either, for the same reason in the other direction: a
  // 24-bucket grid over four readings draws twenty empty columns, which reads as twenty quiet
  // minutes rather than as an absence of data.
  const n = newest === oldest ? 1 : Math.max(1, Math.min(wanted, list.length));
  const width = (newest - oldest) / n || 1;
  const edges = Array.from({ length: n }, (_, i) => oldest + i * width);
  return {
    edges,
    n,
    spanS: newest - oldest,
    /** How wide one bucket is. A count chart names it, because "12 per bucket" is not a rate. */
    bucketS: newest === oldest ? 0 : width,
    labels: edges.map((t) => clock(t, newest - oldest)),
    index: (ts) => {
      const at = Math.floor((Number(ts) - oldest) / width);
      return at < 0 ? -1 : Math.min(n - 1, at);
    },
  };
}

/**
 * Count `stamps` into `grid`'s buckets. A bucket nothing fell into is **`0`** — measured and empty
 * — because every bucket inside the span was covered by the same read.
 *
 * That is the opposite of a `line` series' `null`, and the difference is deliberate: a count over a
 * window that returned rows knows a quiet bucket is quiet, while a sampler that missed a window
 * knows nothing at all about it. Collapsing the two would make an outage look like a quiet hour.
 */
export function tally(grid, stamps) {
  const values = new Array(grid.n).fill(0);
  for (const ts of stamps || []) {
    const at = grid.index(ts);
    if (at >= 0) values[at] += 1;
  }
  return values;
}

/**
 * A bucket label whose resolution follows the span: seconds inside three minutes, clock time inside
 * two days, a date beyond that. Five ticks reading `18:06:07` across a fortnight say nothing.
 *
 * The **zone is named once in the caption**, never on a tick — five ticks each carrying `-03:00`
 * would be five copies of one fact in the space a chart has least of (#294, and the timeline has
 * said so since v0.16.4).
 */
export function clock(ts, spanS) {
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, "0");
  if (spanS <= 180) return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  if (spanS <= 86400 * 2) return `${p(d.getHours())}:${p(d.getMinutes())}`;
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** How long a span is, in the words a caption uses. `92 s`, `14 min`, `3.2 h`, `6.0 days`. */
export function spanText(seconds) {
  const s = Math.max(0, Number(seconds) || 0);
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${(s / 3600).toFixed(1)} h`;
  return `${(s / 86400).toFixed(1)} days`;
}
