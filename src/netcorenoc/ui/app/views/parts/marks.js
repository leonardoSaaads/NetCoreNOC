/* The Timeline's parts: the controls, the lanes, and the list of bursts (v0.22.0, F155, #381).
 *
 * Everything here is hand-written DOM and SVG, so the harness asserts it — the d3 scatter this
 * replaced ran against a recording double and was asserted by nothing. Every control is a query
 * parameter answered in SQL (`/api/activity/*`); nothing here filters a row it was sent.
 */

import { html, cx } from "../../dom.js";
import { clock, spanText } from "../../chartdata.js";
import { absolute, count, relative, timeTitle } from "../../format.js";

/** The windows offered, in seconds, and the column count that keeps a bucket readable. */
export const WINDOWS = [
  { label: "15m", seconds: 15 * 60 },
  { label: "1h", seconds: 60 * 60 },
  { label: "6h", seconds: 6 * 60 * 60 },
  { label: "24h", seconds: 24 * 60 * 60 },
  { label: "7d", seconds: 7 * 24 * 60 * 60 },
];

export const KINDS = [
  ["both", "raises and clears"],
  ["raise", "raises"],
  ["clear", "clears"],
];

/** A parameter's value when it is absent from the address, so a default is never written. */
export const DEFAULTS = { win: 3600, ne: "", kind: "both", page: 0 };

export const PAGE = 25;
export const LANE_BUCKETS = 48;

/** The three controls. Each writes one parameter into the address; the address is the state. */
export function Controls({ config, elements, set }) {
  return html`<div class="filters tl-controls" role="group" aria-label="Timeline filters">
    <div class="ranges" role="group" aria-label="Window">
      ${WINDOWS.map((w) => html`<button type="button" key=${w.label}
          class=${cx("range", w.seconds === config.win && "on")}
          aria-pressed=${w.seconds === config.win}
          onClick=${() => set("win", w.seconds)}>${w.label}</button>`)}
    </div>
    <label class="visually-hidden" for="tlNe">Element</label>
    <select id="tlNe" value=${config.ne} onChange=${(e) => set("ne", e.target.value)}>
      <option value="">every element</option>
      ${elements.map(([id, name]) => html`<option key=${id} value=${String(id)}>${name}</option>`)}
    </select>
    <label class="visually-hidden" for="tlKind">Show</label>
    <select id="tlKind" value=${config.kind} onChange=${(e) => set("kind", e.target.value)}>
      ${KINDS.map(([value, label]) => html`<option key=${value} value=${value}>${label}</option>`)}
    </select>
  </div>`;
}

/**
 * **Where and when, as a matrix**: one row per busy element, one column per slice of the window,
 * each cell shaded by how many alarms were raised there. The axis IS the window — equal slices of
 * it — so a burst is one dark cell and a quiet hour is a row of empty ones, whatever the row count.
 *
 * Shade encodes a magnitude on one hue, never a category, and every cell carries its count as a
 * `<title>`; each row's label carries its total as text.
 */
export function Lanes({ data }) {
  const lanes = [...(data.lanes || [])];
  const others = data.others || { elements: 0, counts: [] };
  if (others.elements) {
    lanes.push({ ne_id: null, device: `${count(others.elements)} more`, counts: others.counts,
                 total: others.counts.reduce((a, b) => a + b, 0) });
  }
  if (!lanes.length) return null;
  const n = data.buckets;
  const peak = Math.max(1, ...lanes.flatMap((lane) => lane.counts));
  const span = data.to - data.from;
  const ticks = [0, Math.floor(n / 2), n].map((i) => clock(data.from + i * data.bucket_s, span));
  return html`<figure class="lanes" data-chart="lanes">
    <div class="lanes-grid" role="img"
         aria-label=${`Alarms raised per element over the last ${spanText(span)}, in slices of ${
           spanText(data.bucket_s)}.`}>
      ${lanes.map((lane) => html`<div class="lane" key=${lane.ne_id ?? "rest"}>
        <span class="lane-name" title=${lane.device}>${lane.device}</span>
        <svg class="lane-cells" viewBox=${`0 0 ${n} 1`} preserveAspectRatio="none"
             aria-hidden="true" focusable="false">
          ${lane.counts.map((c, i) => (c
            ? html`<rect key=${i} x=${i} y="0" width="1" height="1"
                 fill-opacity=${(0.15 + 0.85 * (c / peak)).toFixed(3)}>
                 <title>${`${count(c)} at ${clock(data.from + i * data.bucket_s, span)}`}</title>
               </rect>`
            : null))}
        </svg>
        <span class="lane-total">${count(lane.total)}</span>
      </div>`)}
    </div>
    <div class="lanes-axis" aria-hidden="true">
      ${ticks.map((t, i) => html`<span key=${i}>${t}</span>`)}
    </div>
  </figure>`;
}

/** `×14 over 2 min`, or nothing for a single mark. */
export function repeats(group) {
  if (group.n < 2) return "";
  const over = group.last - group.first;
  return `×${count(group.n)}${over >= 1 ? ` over ${spanText(over)}` : ""}`;
}

/**
 * **The list, as an operator reads it**: one row per burst — the same trap on the same element,
 * less than five minutes between marks — newest first, with the trap's name when anything gives
 * it one and its OID beneath. Raise and clear are words with a glyph, never a colour alone.
 */
export function Bursts({ data, onPage }) {
  const rows = data.groups || [];
  const first = data.offset + 1;
  const last = data.offset + rows.length;
  return html`<section class="panel-block">
    <div class="table-scroll"><table class="data bursts">
      <caption class="visually-hidden">
        Alarm activity, newest first. Each row is one trap on one element; repeats less than
        ${spanText(data.gap_s)} apart are one row with their count.
      </caption>
      <thead><tr>
        <th scope="col">when</th><th scope="col">element</th><th scope="col">trap</th>
        <th scope="col">what</th><th scope="col" class="num">repeats</th>
      </tr></thead>
      <tbody>
        ${rows.map((g) => html`<tr key=${`${g.ne_id}-${g.class_id}-${g.kind}-${g.last}`}
            class=${g.kind === "clear" ? "burst-clear" : "burst-raise"}>
          <td title=${timeTitle(g.last)}>
            <span class="burst-when">${relative(g.last)}</span>
            <span class="burst-abs">${absolute(g.last)}</span>
          </td>
          <td>${g.device}</td>
          <td title=${g.class_oid}>
            ${g.class_named
              ? html`<span class="burst-name">${g.class}</span>`
              : html`<span class="burst-name burst-vendor">${g.class_vendor || "unnamed trap"}</span>`}
            <code class="burst-oid">${g.class_oid}</code>
          </td>
          <td><span class=${cx("kind", `kind-${g.kind}`)}>
            <span aria-hidden="true">${g.kind === "clear" ? "✓" : "▲"}</span>${" "}<span
              class="kind-word">${g.kind}</span>
          </span></td>
          <td class="num">${repeats(g)}</td>
        </tr>`)}
      </tbody>
    </table></div>
    <div class="pager">
      <span>${rows.length ? `${count(first)}–${count(last)} of ${count(data.total)}` : "0"}</span>
      <button type="button" disabled=${data.offset === 0}
              onClick=${() => onPage(-1)}>Newer</button>
      <button type="button" disabled=${last >= data.total}
              onClick=${() => onPage(1)}>Older</button>
    </div>
  </section>`;
}
