/* The Overview's network bands: what is happening, where, and which element is worst.
 *
 * Split out of `views/overview.js` in v0.16.6 because that file reached **22 552 bytes** against
 * the module graph's ceiling of 17 579 — one third of the v0.12.0 `app.js` this console replaced —
 * and **split again in v0.16.7** (#318), at 17 095 bytes with 484 to spare, when this release
 * needed to add a band above everything here.
 *
 * The second seam is a subject, not a size: this file is about **the network**, and
 * `views/parts/keeping.js` is about **the appliance**. `views/parts/severity.js` holds the band
 * that now comes first, because *how bad is it* is a different question from *what is happening*
 * and the answer to the first is a count while the answer to the second is a shape.
 *
 * All three chart types come from `app/charts.js` and every series' arithmetic from
 * `app/chartdata.js`, so the rules — a gap breaks the line, an unmeasured metric renders `—`, the
 * axis is the span the data covers — are properties of the primitives rather than of this file.
 */

import { html } from "../../dom.js";
import { Failed, SectionHeading } from "../../widgets.js";
import { Series } from "../../charts.js";
import { Bars, Map as EstateMap } from "../../compare.js";
import { buckets, spanText, tally } from "../../chartdata.js";
import { plural, relative, count, TIMEZONE } from "../../format.js";

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

/**
 * **Band 3 — where.** The estate, deterministic, so two glances can be compared.
 *
 * **No topology is drawn here, and the caption links to the one that is** (v0.16.7, #317). The
 * maintainer asked for the network topology on this screen; measured on the three-scenario estate,
 * `edge` holds **one** row of `kind='device'` and its weight is **0.0**, so `graph_snapshot`'s
 * filter returns **zero** edges and a topology here would draw two unconnected circles. A third
 * drawing of the same two tables would also double a surface no assertion can reach —
 * `tests/domharness/env.mjs` substitutes a recording double for d3 — to say what this grid and the
 * Graph screen already say between them. So the answer is one click, made obvious.
 */
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
      note=${html`one cell per element, busiest first; pulses above ${URGENT_AT} —
        ${" "}<a href="#/graph">how they are connected is on the Graph screen</a>`} />
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
