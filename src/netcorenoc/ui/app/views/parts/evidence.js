/* Evidence over time: what the gate has decided, and **what this appliance cannot draw**
 * (v0.16.6, DECISIONS #308).
 *
 * ## The measurement that shaped this screen, before anything was drawn
 *
 * The maintainer asked for loss curves, residuals and partitions — *"we are completely in the
 * dark"* — and Phase 0 answered three of those with **cannot be drawn**, by execution:
 *
 *   * **a loss curve.** `challenger_run` has 24 columns and not one holds a per-iteration loss, a
 *     residual or a convergence trace. `iterations` is a count; `learning_rate` and `fit_seconds`
 *     are a duration each.
 *   * **a residual distribution.** The only label in the schema is in `feedback` and reaching it
 *     needs the join; `incumbent_linked` is a comparison basis and **never a target** (`0009`, and
 *     `PREREGISTRATION-0.16.0.md` §1). What *could* be drawn is the score distribution of
 *     `shadow_opinion` — and **no module under `src/` mentions that table and an `/api/` path**,
 *     because `0009`'s posture is *no read below admin, on any route, in any format, ever* and
 *     v0.9.0 added no route at all. It is a security decision before it is a chart.
 *   * **fold results.** `evaluation_fold` holds `(run_id, incident_id, repeat, fold)` —
 *     membership, not results, which is exactly what `0013` says it is for.
 *
 * Each is recorded in `docs/plans/releases.md` with the table and columns a later release would
 * need. **Recording the gap is the honest half of this release**, and it is also why nothing here
 * is a placeholder: a reserved region promising a chart is the failure #219 recorded.
 *
 * ## What DOES exist, and it is more than the brief thought
 *
 * `promotion.metrics` already holds *"the four named quantities with clustered intervals, both
 * arms"* per decision, and `promotion.decided_at` gives them a time axis — `0013`'s own column
 * comment says so. So *"which model is winning over time"* is a render, not a migration.
 *
 * ## Never composed, and that is structural here
 *
 * `PREREGISTRATION-0.10.0.md` §5: the four are *"three numbers, never composed"* plus a fourth,
 * *"also never composed"*, and §5 names the entire entity-resolution family as **not adopted**
 * because a single scalar cannot say whether a difference came from merges or splits.
 *
 * **There is no expression in this file that adds, averages, ranks or scores the four against each
 * other.** They are four charts, drawn by four separate calls, with four separate axes. A single
 * "quality" line would look like insight and would reverse a decision seven releases refused to
 * reverse — and it would be one `reduce` away, which is why the rule is written here beside the
 * code that must not contain it.
 */

import { html } from "../../dom.js";
import { Series } from "../../charts.js";
import { Bars } from "../../compare.js";
import { buckets, spanText, tally } from "../../chartdata.js";
import { plural } from "../../format.js";
import { SectionHeading } from "../../widgets.js";

/**
 * The four named quantities, in `promotion.QUANTITY_NAMES` order, with the direction each moves in.
 *
 * **Transcribed, exactly as `views/parts/verdict.js` transcribes them, and for its reason**: a
 * document that carried three would then render three charts and one visibly missing, instead of
 * silently drawing whatever it happened to contain. A chart set that shows what it was given cannot
 * tell you it was given less than it should have been.
 */
const QUANTITIES = [
  ["over_merge_rate", "Over-merge rate", "lower is better"],
  ["under_merge_rate", "Under-merge rate", "lower is better"],
  ["split_bag_intact_rate", "Split-bag intact rate", "higher is better"],
  ["asserted_negative_respected_rate", "Asserted-negative respected rate", "higher is better"],
];

const VERDICTS = [
  ["BETTER", "better", "quiet"],
  ["NOT_BETTER", "not better", "warn"],
  ["INSUFFICIENT_EVIDENCE", "insufficient evidence", "alarm"],
];

/** Parse one decision's stored metrics document. A malformed one is absent, never invented. */
function metricsOf(row) {
  try {
    const out = JSON.parse(row.metrics || "{}");
    return out && typeof out === "object" ? out : {};
  } catch {
    return {};
  }
}

/**
 * A degenerate interval — `[0, 0, 0]` — is **not computable**, not zero.
 *
 * `promotion_metrics.measure` returns exactly that shape with a stated reason when the input is
 * genuinely absent, and `views/parts/verdict.js` renders it as *"not computable"*. A chart has to
 * make the same distinction or it would plot a flat zero line through every decision this corpus
 * has ever produced — which is a chart asserting the challenger scored nothing, when the truth is
 * that nothing was measurable.
 */
function rateOf(arm) {
  if (!Array.isArray(arm) || arm.length < 3) return null;
  const [rate, low, high] = arm;
  if (rate === 0 && low === 0 && high === 0) return null;
  return rate;
}

/**
 * **What the gate has decided, over time.**
 *
 * `promotions` arrives newest-first from `/api/promotion`; a time axis needs oldest-first, so it is
 * reversed here and nowhere else.
 */
export function Evidence({ data }) {
  const decisions = [...(data.promotions || [])].reverse();
  if (!decisions.length) {
    return html`<section class="panel-block">
      <${SectionHeading} title="Evidence over time"
        hint=${"Nothing has been proposed, so there is nothing to plot. This is not an empty " +
               "chart standing in for a missing one — there is no history yet, and the tables " +
               "above say what a first proposal would need."} />
      <${Gap} />
    </section>`;
  }

  const times = decisions.map((row) => row.decided_at);
  const grid = buckets(times, 24);
  const latest = decisions[decisions.length - 1];
  const insufficient = latest.verdict === "INSUFFICIENT_EVIDENCE";

  return html`<section class="panel-block">
    <${SectionHeading} title="Evidence over time"
      hint=${`${plural(decisions.length, "decision")}, oldest first. Every chart below is drawn ` +
             `from the metrics document the gate wrote when it decided, and none of them is ` +
             `recomputed here.`} />

    ${/* **`INSUFFICIENT_EVIDENCE` is a first-class state on screen, not an empty chart.**
          `PREREGISTRATION-0.10.0.md` §6.1: *"the challenger is not better"* and *"this corpus
          cannot tell"* are opposite claims a binary type collapses into one. The corpus has
          returned the third value every release since v0.9.1, so a screen that rendered it as a
          blank would be showing an absence where there is a measurement. */ null}
    ${insufficient
      ? html`<p class="warnbox"><b>The most recent verdict is${" "}
          <code class="mono">INSUFFICIENT_EVIDENCE</code>.</b> That is a measurement of this
          corpus and <b>not</b> a finding that the challenger is worse — the gate could not
          decide. The charts below are what was measurable; a quantity that was not says
          "not measured" rather than showing a zero.</p>`
      : null}

    <${Series} title="Verdicts" mark="column"
      series=${VERDICTS.map(([value, name, tone]) => ({
        name,
        tone,
        values: tally(grid, decisions.filter((row) => row.verdict === value)
          .map((row) => row.decided_at)),
      }))}
      labels=${grid.labels}
      source="promotion.verdict, as the gate recorded it"
      span=${grid.n ? `over ${spanText(grid.spanS)}` : null}
      note="three states, never two — a refusal and a shortage of evidence are different facts" />

    ${/* The four, as FOUR. See this module's header: nothing here composes them, and the four
          separate calls below are what that looks like in code. */ null}
    <div class="chart-grid">
      ${QUANTITIES.map(([key, label, direction]) => html`
        <${Series} key=${key} title=${label} mark="line"
          series=${[
            {
              name: "challenger",
              tone: "alarm",
              values: decisions.map((row) => rateOf(metricsOf(row)[key]?.challenger)),
            },
            {
              name: "champion",
              tone: null,
              values: decisions.map((row) => rateOf(metricsOf(row)[key]?.champion)),
            },
          ]}
          labels=${grid.labels}
          max=${1}
          source=${`promotion.metrics · ${direction}`}
          note=${"both arms from one code path; a quantity that was not computable breaks the " +
                 "line rather than reading zero"} />`)}
    </div>

    <${Series} title="Seal query count" mark="line" unit="queries"
      series=${[{ name: "queries", tone: "warn",
        values: decisions.map((row) => Number(row.query_count) || 0) }]}
      labels=${grid.labels}
      source="promotion.query_count, at each decision"
      note="printed beside every holdout number this project publishes (§4.3(4))" />

    <${Triggers} decisions=${decisions} />
    <${Gap} />
  </section>`;
}

/**
 * Which triggers have fired, across every decision. **A census, not a score.**
 *
 * A refusal names every trigger that fired rather than the first — `promotion.evaluate` does not
 * short-circuit — so counting them across decisions says which constraint this corpus keeps
 * running into. That is the actionable half of a refusal, and it is the one thing a reader cannot
 * get from the decisions table without reading every row.
 */
function Triggers({ decisions }) {
  const tally = new Map();
  for (const row of decisions) {
    let names = [];
    try {
      const parsed = JSON.parse(row.triggers || "[]");
      names = Array.isArray(parsed) ? parsed : [];
    } catch {
      names = [];
    }
    for (const name of names) tally.set(String(name), (tally.get(String(name)) ?? 0) + 1);
  }
  const rows = [...tally.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([name, times]) => ({ key: name, label: name, value: times, tone: "warn" }));
  if (!rows.length) {
    return html`<p class="hint">No trigger has fired on any decision recorded here.</p>`;
  }
  return html`<${Bars} title="Triggers, across every decision" rows=${rows} unit="decisions"
    source="promotion.triggers"
    note=${"every trigger is evaluated and none short-circuits, so a decision's list is " +
           "complete rather than first-wins"} />`;
}

/**
 * **What this appliance cannot draw, on the screen where someone would look for it.**
 *
 * Not a placeholder and not a promise: three named absences, each with the table a later release
 * would have to add. A screen that simply omitted them would leave an operator to conclude the
 * appliance had measured something it has not — which is the same dishonesty as drawing a zero.
 */
function Gap() {
  return html`<div class="unavailable">
    <h4>Not drawn, because nothing measures it</h4>
    <p class="hint">A <b>loss curve</b> — <code class="mono">challenger_run</code> records${" "}
      <code class="mono">iterations</code> as a count and keeps no per-iteration loss, residual or
      convergence trace.</p>
    <p class="hint">A <b>residual distribution</b> — the only label in this schema is in${" "}
      <code class="mono">feedback</code>, and <code class="mono">incumbent_linked</code> is a
      comparison basis and never a target. The sampled opinions that could show a score
      distribution have <b>no route at all</b>: they are captured engine-side where visibility
      scoping does not exist.</p>
    <p class="hint"><b>Fold results</b> — <code class="mono">evaluation_fold</code> stores which
      incident went into which fold, which makes an evaluation reproducible; it does not store
      what each fold scored.</p>
    <p class="hint"><b>No chart on this screen is drawn from a sampled table.</b> If one ever is,
      it must state its sampling rate beside it — the shadow sampler's default is <b>0.01</b> and a
      deployment may change it, so the rate would have to be read from${" "}
      <code class="mono">challenger_run.sample_rate</code> rather than assumed.</p>
    <p class="hint">What each would need is written down in${" "}
      <code class="mono">docs/plans/releases.md</code>, with its table and its columns.</p>
  </div>`;
}
