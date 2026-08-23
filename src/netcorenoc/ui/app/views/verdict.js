/* A promotion decision, rendered in **every branch** the gate can reach — not just the refusal.
 *
 * v0.13.0's promotion screen rendered a decision as a table row: verdict, outcome, reason,
 * triggers. That is complete for `INSUFFICIENT_EVIDENCE`, which was the only verdict this project
 * had ever produced, and it is thin for the other two — because a `BETTER` or a `NOT_BETTER` is a
 * *comparison*, and a comparison shown without the numbers it compared is an assertion.
 *
 * `PREREGISTRATION-0.11.0.md` §2 item 4: **both arms come from one code path**, because a
 * challenger number with no champion number beside it is not a comparison. This module is that
 * sentence applied to the screen: the four named quantities, both arms, side by side, with their
 * intervals — or an explicit statement that a quantity was not computable, never a zero standing
 * in for one.
 *
 * ## The three branches, and what each one has to say
 *
 *   * **`INSUFFICIENT_EVIDENCE`** — which triggers fired, and what would have to change. The gate
 *     could not decide. It is *not* a finding that the challenger is worse, and the wording says so
 *     because an operator who reads it as one will stop labelling.
 *   * **`NOT_BETTER`** — **which quantity produced it.** `promotion.deciding_quantity` picks one to
 *     name, and a screen that reported the verdict without naming it would be asking an operator to
 *     guess among four.
 *   * **`BETTER`** — the numbers that cleared, and the fact that the champion changed. This is the
 *     one branch with a consequence, so it is the one that has to be hardest to misread.
 *
 * ## Never composed
 *
 * The four quantities are shown as four. There is no expression in this file that adds, averages or
 * ranks them against each other — the same rule `promotion_metrics.py` states on the server, for the
 * same reason. A screen that showed a single "score" would be inventing a fifth quantity that no
 * plan registered and no code derives.
 */

import { html } from "../dom.js";
import { DataTable, SectionHeading } from "../widgets.js";
import { score } from "../format.js";

/** The four, in `promotion.QUANTITY_NAMES` order. Transcribed, not imported: see below. */
const QUANTITIES = [
  ["over_merge_rate", "over-merge rate", "lower is better"],
  ["under_merge_rate", "under-merge rate", "lower is better"],
  ["split_bag_intact_rate", "split-bag intact rate", "higher is better"],
  ["asserted_negative_respected_rate", "asserted-negative respected rate", "higher is better"],
];

const VERDICT_TONE = {
  BETTER: "ok",
  NOT_BETTER: "quiet",
  INSUFFICIENT_EVIDENCE: "warn",
};

const VERDICT_MEANS = {
  BETTER:
    "The gate found the challenger better on the deciding quantity, with the interval excluding " +
    "zero, and every trigger clear.",
  NOT_BETTER:
    "The gate decided, and the decision went against the challenger. This is a finding, not a " +
    "shortage of evidence.",
  INSUFFICIENT_EVIDENCE:
    "The gate could not decide. This is NOT a finding that the challenger is worse — it is the " +
    "statement that this corpus cannot tell.",
};

/** An interval as an operator reads it: the rate, then the bounds it is known within. */
function interval(arm) {
  if (!Array.isArray(arm) || arm.length < 3) return html`<span class="muted">—</span>`;
  const [rate, low, high] = arm;
  if (rate === 0 && low === 0 && high === 0) {
    return html`<span class="muted">not computable</span>`;
  }
  return html`<span class="mono">${score(rate, 4)}</span>
    <span class="muted"> [${score(low, 4)}, ${score(high, 4)}]</span>`;
}

/** Parse the stored metrics document. A malformed one is absent, never invented. */
function metricsOf(row) {
  if (!row || !row.metrics) return null;
  try {
    const out = JSON.parse(row.metrics);
    return out && typeof out === "object" ? out : null;
  } catch {
    return null;
  }
}

/**
 * The four named quantities, **both arms**, with their cluster-bootstrap intervals.
 *
 * The names are transcribed here rather than derived from the document's own keys, so a document
 * that carried three quantities renders three rows and one visibly missing — instead of silently
 * rendering whatever it happened to contain. A table that shows what it was given cannot tell you
 * that it was given less than it should have been.
 */
export function Quantities({ row }) {
  const metrics = metricsOf(row);
  if (!metrics) {
    return html`<p class="hint">This decision recorded no metrics document. Nothing is inferred
      from its absence and no number is shown in its place.</p>`;
  }
  const rows = QUANTITIES.map(([key, label, direction]) => {
    const quantity = metrics[key];
    return {
      key,
      cells: {
        name: html`<span>${label}</span> <span class="muted">(${direction})</span>`,
        challenger: quantity ? interval(quantity.challenger) : html`<span class="muted">absent</span>`,
        champion: quantity ? interval(quantity.champion) : html`<span class="muted">absent</span>`,
        clusters: quantity && quantity.clusters != null
          ? html`<span class="mono">${quantity.clusters}</span>`
          : html`<span class="muted">—</span>`,
      },
    };
  });
  return html`<div class="quantities">
    <${DataTable} columns=${[
      { key: "name", label: "quantity" },
      { key: "challenger", label: "challenger" },
      { key: "champion", label: "champion" },
      { key: "clusters", label: "incidents", numeric: true },
    ]} rows=${rows} />
    <p class="hint">Both arms come from one code path, because a challenger number with no champion
      number beside it is not a comparison. The interval is a cluster bootstrap over
      <b>incidents</b> — never over pairs and never over bags, so one large storm cannot speak for
      the corpus. A quantity that could not be computed says so; it is never recorded as zero.</p>
  </div>`;
}

/**
 * One decision, expanded: what the verdict means, what fired, and the numbers behind it.
 *
 * Rendered under the decisions table rather than inside it. A table cell wide enough for four
 * intervals is a table nobody can read, and the refusal reason is already several sentences long.
 */
export function Decision({ row }) {
  let triggers = [];
  try { triggers = JSON.parse(row.triggers || "[]"); } catch { triggers = []; }
  let unavailable = [];
  try { unavailable = JSON.parse(row.unavailable || "[]"); } catch { unavailable = []; }
  const applied = row.outcome === "applied";
  return html`<section class="panel-block decision-detail">
    <${SectionHeading} title=${`Decision #${row.id} — ${row.verdict}`}
      hint=${VERDICT_MEANS[row.verdict] || "A verdict this build does not recognise."} />

    <p>
      <span class=${`outcome outcome-${applied ? "ok" : "denied"}`}>${row.outcome}</span>
      <span class="muted"> · model version ${row.model_version_id} · by
        ${row.approved_by || "—"} · seal queries ${row.query_count}</span>
    </p>

    ${applied
      ? html`<p class="warnbox"><b>The champion changed.</b> Every grouping decision from the
          engine reload after this one onwards was taken by model version
          ${row.model_version_id}. Situations that already existed were not regrouped.</p>`
      : null}

    ${row.refusal_reason
      ? html`<pre class="refusal-reason">${row.refusal_reason}</pre>`
      : null}

    ${(Array.isArray(triggers) ? triggers : []).length
      ? html`<p class="hint">Triggers that fired:
          <b class="mono">${triggers.map(String).join(", ")}</b>.
          Every trigger is evaluated; none short-circuits, so this list is complete rather than
          first-wins.</p>`
      : html`<p class="hint">No trigger fired.</p>`}

    <${Quantities} row=${row} />

    ${(Array.isArray(unavailable) ? unavailable : []).length
      ? html`<div class="unavailable">
          <h4>Recorded as absent, not as zero</h4>
          ${unavailable.map((note, index) => html`<p class="hint" key=${index}>${String(note)}</p>`)}
        </div>`
      : null}

    <p class="hint">Taken against ratified plan
      <code class="mono">${row.plan_sha256}</code>, evaluation run
      <code class="mono">${row.evaluation_run_id || "—"}</code>.</p>
  </section>`;
}

export { VERDICT_TONE };
