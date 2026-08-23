/* Judge and promotion — what the gate decided, and **why it refused**.
 *
 * `GET /api/promotion` and `POST /api/promotion` had no UI surface in v0.12.0 (draft §4). The
 * read was deferred in v0.11.0 for a stated reason (#163): the v0.7.5 defect was a click gesture
 * in a UI no test executed, and a promotion screen where a mis-click swaps the correlator is that
 * failure mode with a strictly larger consequence. **The prerequisite is now met** — this UI is
 * executed, and the propose control goes through the same destructive-preview machinery as every
 * other consequential action.
 *
 * ## A refusal is an explanation, not an error state (draft §5.3)
 *
 * The route returns the verdict, the triggers, the refusal reason and what was unavailable. All
 * of it is displayed. A gate that refuses and cannot say why is indistinguishable from a gate
 * that is broken, and this project's whole claim is that its refusals are legible.
 *
 * ## The seal is displayed and never actionable (draft §6, third class)
 *
 * `seal_query_count` is a fact about what has been read from the sealed holdout. There is no
 * control to change it, and an admin who sees "0 queries" with no control learns something true:
 * it is a guarantee rather than a preference.
 */

import { html, Component } from "../dom.js";
import { get, post } from "../api.js";
import { Loader, Empty, DataTable, SectionHeading, Stat, TimeCell, cell } from "../widgets.js";
import { count, plural } from "../format.js";
import { can } from "../session.js";
import { Destructive } from "../destructive.js";
import { Decision } from "./verdict.js";

export class Promotion extends Loader {
  constructor(props) {
    super(props);
    this.what = "the promotion record";
    this.loadingLabel = "Reading the promotion record";
  }

  async load() { return get("/api/promotion"); }

  view(data) {
    const promotions = data.promotions || [];
    const versions = data.model_versions || [];
    return html`<div class="promotionview">
      <section class="panel-block param-structural">
        <${SectionHeading} title="The sealed holdout"
          hint=${"Displayed, never actionable. The query count is a property of what has been " +
                 "read from the holdout — not a setting — and there is no control here because " +
                 "there is no choice. That is what makes it a guarantee."} />
        <div class="stat-row">
          <${Stat} label="seal query count" value=${data.seal_query_count}
                   tone=${data.seal_query_count === 0 ? "quiet" : "warn"}
                   note=${data.seal_query_count === 0
                     ? "the holdout has never been read"
                     : "every read is counted and printed beside every holdout number"} />
          <${Stat} label="active model version" value=${data.active_model_version_id ?? "—"} />
          <${Stat} label="active scorer configuration" value=${data.active_config_id ?? "—"} />
        </div>
        <p class="hint">Ratified plan: <code class="mono">${data.plan_sha256}</code>. Every
          decision below was taken against this plan and records the hash it was taken against.</p>
      </section>

      <${SectionHeading} title="Decisions"
        hint=${"Refusals are included — that is the audit question this table exists to answer. " +
               "A refusal writes a row exactly as an application does."} />
      ${promotions.length
        ? html`<${DataTable} columns=${DECISION_COLUMNS} rows=${promotions.map(toDecision)} />
            <${Decision} row=${promotions[0]} />
            <p class="hint">Expanded above: the most recent decision. Every decision carries the
              same record — the four named quantities for both arms, the triggers, and what was
              recorded as absent — and it is written at the moment the gate decides, never
              recomputed afterwards.</p>`
        : html`<${Empty}
            title="No promotion has been proposed."
            will=${"Every proposal — applied or refused — is recorded here with its verdict, " +
                   "the triggers that fired, and the reason. Nothing is promoted automatically."}
            meanwhile=${"A proposal needs a registered model version with a challenger run " +
                        "behind it, and the corpus has to have reached the pre-registered " +
                        "floors. Both are visible on the Corpus screen."} />`}


      <${SectionHeading} title="Model versions"
        hint="The artefact inventory. A version without a challenger run behind it can be registered but never applied." />
      ${versions.length
        ? html`<${DataTable} columns=${VERSION_COLUMNS} rows=${versions.map(toVersion)} />`
        : html`<p class="hint">No model versions have been registered.</p>`}

      ${can("promotion.write") && versions.length ? html`<${Propose} versions=${versions} onDone=${this.reload} />` : null}
    </div>`;
  }
}

const DECISION_COLUMNS = [
  { key: "when", label: "decided" },
  { key: "verdict", label: "verdict" },
  { key: "outcome", label: "outcome" },
  { key: "reason", label: "reason" },
  { key: "triggers", label: "triggers" },
  { key: "queries", label: "seal queries", numeric: true },
  { key: "by", label: "by" },
];

function toDecision(row) {
  let triggers = [];
  try { triggers = JSON.parse(row.triggers || "[]"); } catch { triggers = []; }
  return {
    key: row.id,
    tone: row.outcome === "refused" ? "quiet" : null,
    cells: {
      when: cell(html`<${TimeCell} ts=${row.decided_at} />`),
      verdict: html`<b class="mono">${row.verdict}</b>`,
      outcome: html`<span class=${`outcome outcome-${row.outcome === "applied" ? "ok" : "denied"}`}>
        ${row.outcome}</span>`,
      // The whole point of this screen: a refusal that cannot say why is indistinguishable from
      // a broken gate.
      reason: row.refusal_reason
        ? html`<span class="refusal-reason">${row.refusal_reason}</span>`
        : html`<span class="muted">—</span>`,
      triggers: (Array.isArray(triggers) ? triggers : []).length
        ? html`<span class="mono">${(Array.isArray(triggers) ? triggers : []).map(String).join(", ")}</span>`
        : html`<span class="muted">none</span>`,
      queries: count(row.query_count),
      by: row.approved_by || "—",
    },
  };
}

const VERSION_COLUMNS = [
  { key: "id", label: "#", numeric: true },
  { key: "kind", label: "kind" },
  { key: "hash", label: "params hash" },
  { key: "run", label: "challenger run" },
];

function toVersion(row) {
  return {
    key: row.id,
    cells: {
      id: count(row.id),
      kind: row.kind,
      hash: html`<code class="mono">${row.params_hash}</code>`,
      run: row.challenger_run_id == null
        ? html`<span class="muted">none — can be registered, never applied</span>`
        : count(row.challenger_run_id),
    },
  };
}

/**
 * Choose a candidate, then propose it.
 *
 * v0.13.0 proposed `versions[0]` with no selector, which was defensible when the inventory held one
 * logistic artefact and is not now: v0.14.0 registers five kinds, and a screen that silently picks
 * the newest is a screen where the consequential action depends on a sort order the operator cannot
 * see. The selection is a plain `<select>` and the confirmation names the version it will propose.
 *
 * A version with no challenger run behind it stays selectable **on purpose**. It cannot be applied
 * — the server refuses it with a reason — and letting an admin propose one and read that reason is
 * how the rule becomes visible. Hiding it would leave the operator to infer a rule nobody stated.
 */
class Propose extends Component {
  constructor(props) {
    super(props);
    this.state = { chosen: props.versions[0].id };
  }

  render({ versions, onDone }, { chosen }) {
    const candidate = versions.find((one) => one.id === Number(chosen)) || versions[0];
    return html`<section class="panel-block param-structural">
      <${SectionHeading} title="Propose"
        hint=${"The body names a candidate and nothing else. Which one it names is your choice; " +
               "what happens next is not."} />
      <div class="grid-form">
        <div>
          <label for="promote-candidate">Model version</label>
          <select id="promote-candidate" value=${String(chosen)}
                  onChange=${(e) => this.setState({ chosen: Number(e.target.value) })}>
            ${versions.map((one) => html`<option key=${one.id} value=${String(one.id)}>
              #${one.id} — ${one.kind}${one.challenger_run_id == null ? " (no challenger run)" : ""}
            </option>`)}
          </select>
        </div>
      </div>
      ${candidate.challenger_run_id == null
        ? html`<p class="warnbox">This artefact has <b>no challenger run behind it</b>. It can be
            proposed and the decision will be recorded, but it cannot be applied — the server
            refuses it with that reason, whatever the verdict.</p>`
        : null}
      <${ProposeControl} candidate=${candidate} onDone=${onDone} />
    </section>`;
  }
}

function ProposeControl({ candidate, onDone }) {
  return html`<${Destructive}
    title="Propose a promotion"
    hint=${"The body names a candidate. **The server decides**: the floors, the power condition, " +
           "the seal, the verdict and the metrics are all re-derived, and a client cannot assert " +
           "any of them — the request model has no field for it."}
    previewLabel="Propose model version…"
    confirmLabel=${`Propose model version #${candidate.id}`}
    consequence=${`If the gate accepts, the correlator's active model version changes and every ` +
                  `grouping decision from the next reload onwards is taken by the new artefact. ` +
                  `If it refuses, a row is written recording the refusal and its reason, and ` +
                  `nothing changes. Either way the decision is permanent in the record.`}
    preview=${async () => ({
      measured: false,
      message: "There is no dry run for a promotion: the gate's verdict IS the evaluation, and " +
               "computing it without recording it would create a decision nobody can audit. " +
               `Proposing model version #${candidate.id} records what the gate decided, ` +
               "applied or refused.",
    })}
    apply=${async () => post("/api/promotion", { model_version_id: candidate.id, note: "" })}
    renderResult=${(result) => html`<span>
      Verdict <b class="mono">${result.verdict}</b>, outcome <b>${result.status}</b>.${" "}
      ${result.reason ? `Reason: ${result.reason}` : ""}
      ${(result.unavailable || []).length
        ? html` ${plural(result.unavailable.length, "quantity", "quantities")} could not be
            computed and are recorded as absent rather than as zero.` : ""}
    </span>`}
    onDone=${onDone} />`;
}
