/* The Overview's five bands: the charts, and what each one may claim (v0.16.6, DECISIONS #304).
 *
 * Split out of `views/overview.js` because that file reached **22 552 bytes** against the module
 * graph's ceiling of 17 579 — one third of the v0.12.0 `app.js` this console replaced. The seam is
 * the one the guard would have chosen anyway, and it is a real one: `overview.js` owns the screen's
 * **composition and its four states**, and this owns **the five charts and the population each one
 * is allowed to claim**. Every sentence below about what a chart may not say lives beside the chart
 * that must not say it.
 *
 * All three chart types come from `app/charts.js` and every series' arithmetic from
 * `app/chartdata.js`, so the rules — a gap breaks the line, an unmeasured metric renders `—`, the
 * axis is the span the data covers — are properties of the primitives rather than of this file.
 */

import { html } from "../../dom.js";
import { Stat, Failed, SectionHeading } from "../../widgets.js";
import { Series, Bars, Map as EstateMap } from "../../charts.js";
import { buckets, tally, spanText } from "../../chartdata.js";
import { plural, relative, count, score, TIMEZONE } from "../../format.js";

/** How many alarms the marks read asks for. The route clamps at 1 000; this is that clamp. */
export const MARK_LIMIT = 1000;

/** How many elements the top-elements bars rank. Enough to act on, short enough to read. */
const TOP_N = 5;

/**
 * The load at which an estate cell starts pulsing (DECISIONS #309).
 *
 * **47 is not arbitrary**: it is where the network graph's node radius saturates —
 * `min(24, 7 + 2.5 * sqrt(n))` reaches its 24 px ceiling there (F77) — so it is exactly the load
 * above which the other projection stops being able to tell two elements apart. Marking the same
 * threshold in both places is what makes the two drawings agree about which elements are urgent.
 */
export const URGENT_AT = 47;

/**
 * **Band 1 — what is happening.** Two column series, and they answer different halves.
 *
 * *Situations by creation time* says whether the correlator is forming groups steadily or all at
 * once. Its population is **the situations in the live list** — the 50 most recently active, which
 * is what the update stream carries — and the caption says so. That is deliberately not a second,
 * larger read: one source for the situations on this screen means the chart and the list beneath it
 * cannot disagree, and the question *"burst or trickle"* is answered by the population an operator
 * is actually working through. *(DECISIONS #306 measured `?limit=500` at 2.5 ms and it would have
 * been affordable; it was refused for the disagreement, not for the cost.)*
 *
 * *Raises and clears* is the half that says whether it is **recovering**. Five hundred raises and
 * three clears and five hundred raises and 495 clears are opposite situations and the counters
 * cannot tell them apart. Both series share one bucket grid, so a column in one lines up with the
 * column beside it.
 */
export function Happening({ situations, marks, at, error, retry }) {
  const created = situations.map((s) => s.created_at).filter((t) => t != null);
  const grid = buckets(created, 24);
  const byStatus = (want) =>
    tally(grid, situations.filter((s) => (want === "resolved"
      ? s.status === "resolved"
      : s.status === want)).map((s) => s.created_at));

  const markTimes = (marks || []).map((m) => m.ts);
  const markGrid = buckets(markTimes, 24);
  const raises = tally(markGrid, (marks || []).filter((m) => m.kind !== "clear").map((m) => m.ts));
  const clears = tally(markGrid, (marks || []).filter((m) => m.kind === "clear").map((m) => m.ts));
  const truncated = (marks || []).length >= MARK_LIMIT;

  return html`<section class="panel-block">
    <${SectionHeading} title="What is happening" />
    <${Series} title="Situations, by when they were created" mark="column"
      series=${[
        { name: "new", tone: "alarm", values: byStatus("new") },
        { name: "being worked", tone: "warn", values: byStatus("open") },
        { name: "resolved", tone: "quiet", values: byStatus("resolved") },
      ]}
      labels=${grid.labels}
      source=${`the ${plural(situations.length, "situation")} in the live list (50 newest)`}
      span=${grid.n ? `over ${spanText(grid.spanS)}, one column per ${spanText(grid.bucketS)}`
        : null}
      note=${`in ${TIMEZONE}`} />

    ${error
      ? html`<${Failed} error=${error} retry=${retry} what="recent alarm activity" />`
      : html`<${Series} title="Alarm raises and clears" mark="column"
          series=${[
            { name: "raises", tone: "alarm", values: raises },
            { name: "clears", tone: "quiet", values: clears },
          ]}
          labels=${markGrid.labels}
          source=${marks == null
            ? "reading /api/timeline…"
            : `${plural(marks.length, "mark")} from /api/timeline`}
          span=${markGrid.n
            ? `over ${spanText(markGrid.spanS)}, one column per ${spanText(markGrid.bucketS)}`
            : null}
          note=${[
            truncated ? `bounded at ${count(MARK_LIMIT)} alarms: the axis is what this page covers`
              : null,
            at ? `read ${relative(at)}` : null,
          ].filter(Boolean).join("; ") || null} />`}
  </section>`;
}

/** **Band 2 — where.** The estate, deterministic, so two glances can be compared. */
export function Where({ nodes }) {
  return html`<section class="panel-block">
    <${SectionHeading} title="Where it is happening" />
    <${EstateMap} title="The estate, by active alarms"
      cells=${nodes.map((node) => ({
        key: String(node.id),
        label: node.label || node.ip,
        value: node.active_alarms,
      }))}
      urgentAt=${URGENT_AT}
      source="/api/graph, live · load, not topology"
      note=${`one cell per element, busiest first; pulses above ${URGENT_AT}`} />
  </section>`;
}

/**
 * **Band 3 — which element is worst.** Active alarms, which is exact and complete.
 *
 * **Not "by alarms in a week"**, and that refusal is Phase 0's most useful result. The appliance
 * serves *active* alarms per element and a page of recent marks, and neither is a count over a
 * window: measured, `GET /api/timeline?limit=1000` came back full — 1 000 marks spanning **15.6
 * seconds** — so a chart labelled *"last 7 days"* would have shown fifteen seconds of one storm.
 * What a later release needs is `GROUP BY ne_id` over `alarm.first_seen` inside a window, and it is
 * recorded in `docs/plans/releases.md` rather than approximated here.
 */
export function Worst({ nodes }) {
  const rows = [...nodes]
    .filter((node) => node.active_alarms > 0)
    .sort((a, b) => b.active_alarms - a.active_alarms
      || String(a.id).localeCompare(String(b.id)))
    .slice(0, TOP_N)
    .map((node) => ({
      key: String(node.id),
      label: node.label || node.ip,
      value: node.active_alarms,
      tone: node.active_alarms >= URGENT_AT ? "alarm" : "warn",
      title: "Open this element's situations from the Graph screen's tables",
    }));
  return html`<section class="panel-block">
    <${Bars} title=${`Busiest ${TOP_N} elements`}
      rows=${rows} unit="alarms" source="/api/graph, live"
      note="active now, not over a window" />
  </section>`;
}

/**
 * **Band 4 — is the appliance keeping up.**
 *
 * The host series are the same three `/api/stats.resources` carries for the top bar's control, at a
 * size that can hold an axis: the control's sparkline is 104 x 22 px and carries none. One
 * vocabulary, two sizes, and never two implementations — both go through `charts.js`.
 *
 * **The restart sentence is not decoration.** `resources.py` keeps a `deque` in process memory and
 * nothing writes it to the database: no migration creates a table whose name holds `sample`,
 * `series`, `history`, `metric` or `snapshot`, measured. So the two-hour window is two hours *since
 * this process started*, and a screen that implied otherwise would be claiming a history the
 * appliance does not have.
 */
export function Keeping({ stats, ring, rate }) {
  const res = stats.resources;
  const receiver = stats.receiver;
  const hours = res && res.window_s ? Math.round(res.window_s / 3600) : 2;
  const ringGrid = buckets((ring && ring.at) || [], 60);
  return html`<section class="panel-block">
    <${SectionHeading} title="Is the appliance keeping up" />
    ${/* **The queue chart is OUTSIDE the `resources` branch, and that placement is a repair.**
          It was inside it first, and a DOM test drove an `/api/stats` with no `resources` block
          and found the chart gone. `queue_depth` is served whether or not the process runner is
          present and the series is derived in this browser, so gating it on the host block would
          lose the one chart that says whether correlation is falling behind — for a reason that
          has nothing to do with it. Two sources, two conditions. */ null}
    <div class="chart-grid">
      ${res
        ? html`<${Series} title="CPU" unit="%" series=${[{ name: "CPU", values: res.cpu_series }]}
              source="/api/stats.resources" span=${`last ${plural(hours, "hour")}`}
              note=${`sampled every ${res.interval_s ?? 30} s; resets on restart`} />
            <${Series} title="Memory" unit="%"
              series=${[{ name: "Memory", values: res.mem_series }]}
              source=${res.mem_source === "cgroup"
                ? "/api/stats.resources — the container's limit"
                : "/api/stats.resources — the host's memory"}
              span=${`last ${plural(hours, "hour")}`}
              note="resets on restart" />
            <${Series} title="Storage" unit="%"
              series=${[{ name: "Storage", values: res.disk_series }]}
              source="/api/stats.resources — the filesystem holding the database"
              span=${`last ${plural(hours, "hour")}`}
              note="resets on restart" />`
        : null}
      <${Series} title="Queue depth" unit="traps"
        series=${[{ name: "queued", tone: "warn", values: (ring && ring.queue) || [] }]}
        labels=${ringGrid.labels}
        source="derived in this browser from /api/stats.queue_depth"
        span=${ringGrid.n > 1 ? `over ${spanText(ringGrid.spanS)}` : null}
        note="this browser's own history; lost on reload" />
    </div>
    ${res
      ? null
      : html`<p class="hint">No <code>resources</code> block in <code>/api/stats</code>: this API
          runs without the process runner, so nothing samples the host. Queue depth is
          unaffected.</p>`}
    ${/* **The counters F68 found, as one line rather than seven tiles.**
          They did not leave: `receiver.denied` is still the only evidence an operator has that
          their own allowlist is refusing their own equipment. What left is the tile chrome — two
          headings, two paragraphs and seven boxes — for numbers that are read when the answer
          above is "no" and skipped when it is "yes" (#300's shape for the health panel).

          Each carries `data-metric`, which is the same `label`/`value` pairing `.stat` has had
          since v0.13.0 and is what lets the DOM harness read a number by its name instead of by
          its position in a sentence. */ null}
    ${/* The separator is **an element**, not a CSS `::before`, and that is F112's second half.
          `content: " · "` renders a gap and contributes nothing to `textContent`, so the line read
          `0.0000 strap rate` and `0accepted 0refused` to a screen reader and to the clipboard
          while looking correct on screen. `charts.js` already had this right — `.chart-sep` is a
          real span — and this is the same answer in the same words. */ null}
    <p class="health-sub">
      ${[
        { name: "p95 latency", value: `${score(stats.latency_p95_s ?? 0, 4)} s` },
        {
          name: "trap rate",
          value: rate ? `${rate.perSecond.toFixed(rate.perSecond < 10 ? 2 : 0)} /s` : "—",
          note: rate ? `over ${rate.windowS.toFixed(1)} s` : "waiting for a second reading",
        },
        ...(receiver
          ? ["received", "accepted", "denied", "quarantined", "dropped"].map((key) => ({
              name: key === "denied" ? "refused" : key,
              metric: key,
              value: count(receiver[key]),
            }))
          : []),
      ].map((one, index) => html`<span key=${one.metric || one.name}>
        ${index ? html`<span class="metric-sep"> · </span>` : null}
        <${Metric} ...${one} />
      </span>`)}
    </p>
    ${receiver && receiver.denied
      ? html`<p class="warnbox">The trap allowlist has refused${" "}
          <b>${plural(receiver.denied, "datagram")}</b>. A refused source is dropped at the socket:
          it is not quarantined and it never becomes an alarm.</p>`
      : null}
  </section>`;
}

/**
 * One number on the secondary line: its name, its value, and optionally what it is over.
 *
 * `data-metric` names the number rather than leaving it to be found by position in a sentence —
 * the same reason `.stat` carries `.stat-label` and `.stat-value`. A guard that read
 * `"received"` out of a run of prose would break the day a separator changed, which is a guard
 * measuring the copy instead of the fact.
 */
function Metric({ name, metric, value, note }) {
  /* **`${" "}` and not a CSS margin, and the difference is F112.**
   *
   * Written first as `<span>${name}</span>\n<b>${value}</b>`, which rendered `p95 latency0.0000 s`
   * — `htm` drops a whitespace-only run between two elements. F110's guard exempts
   * element-to-element pairs on the stated ground that *"the container is a flex row whose `gap`
   * separates them"*, and `.health-sub` is a `<p>`: `display: block`, no gap, premise false.
   *
   * The first repair was `margin-right` on the name, and **it was wrong in a way worth recording**:
   * a margin fixes the pixels and leaves `textContent` as `p95 latency0.0000 s`, which is what a
   * screen reader announces and what an operator pastes into a ticket. An explicit space is in the
   * DOM, so all three agree.
   */
  return html`<span class="metric" data-metric=${metric || name}>
    <span class="metric-name">${name}</span>${" "}<b>${value}</b>
    ${note ? html`${" "}<span class="metric-note">${note}</span>` : null}
  </span>`;
}

/** **Band 5 — what the appliance has learned.** Three numbers, and they are numbers, not prose. */
export function Learned({ stats }) {
  return html`<div class="stat-row">
    <${Stat} label="devices" value=${stats.devices} note="learned, not configured" />
    <${Stat} label="alarm classes" value=${stats.classes} note="learned, not configured" />
    ${(stats.ingest_gaps || []).length
      ? html`<${Stat} label="ingest gaps" value=${stats.ingest_gaps.length} tone="warn"
                      note="closed gaps, kept for history" />`
      : null}
  </div>`;
}

