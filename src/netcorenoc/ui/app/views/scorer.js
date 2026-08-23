/* The link scorer: the formula that decides which alarms group.
 *
 * ## The hardening-only refusal, live (draft §6.2, §7.4)
 *
 * This form is the one place in the console where an admin can submit a value that would loosen a
 * project floor. The bounds come from `GET /api/scorer` — **the server's own numbers, never a
 * client copy** — and a submission that falls outside them is refused here with the four things
 * §6.2 requires:
 *
 *   1. the project floor, and that it cannot be lowered here;
 *   2. the value they submitted, and that it was not applied;
 *   3. **why the floor exists**;
 *   4. that the stricter direction is available, with the field still usable.
 *
 * **And no request is issued.** That is the affordance half of principle 9; `scoring.validate_params`
 * is the control half and refuses the same submission if it arrives anyway.
 *
 * Preview before apply is unchanged from v0.6.0, and so is the caveat: `SECURITY-REVIEW-0.6` §4
 * treats the wording as a control, because a preview that presented itself as authoritative would
 * be worse than none. The sentence comes from the API and is displayed, never paraphrased.
 */

import { html, Component } from "../dom.js";
import { get, post } from "../api.js";
import { Loader, DataTable, SectionHeading, TimeCell, Loading, Failed, cell } from "../widgets.js";
import { score, count, plural } from "../format.js";
import { scorerRefusals } from "../parameters.js";
import { Running } from "./model.js";
import { can } from "../session.js";
import { Destructive } from "../destructive.js";

const FIELDS = [
  ["w_t", "w_t — temporal weight"],
  ["w_a", "w_a — class-affinity weight"],
  ["w_e", "w_e — entity-affinity weight"],
  ["tau_s", "tau (s) — temporal decay constant"],
  ["threshold", "threshold — link when the score exceeds this"],
];

export class Scorer extends Loader {
  constructor(props) {
    super(props);
    this.what = "the scorer configuration";
    this.loadingLabel = "Reading the scorer";
  }

  async load() { return get("/api/scorer"); }

  view(config) {
    // `running` is v0.14.0's addition and the screen leads with it: **what is deciding** comes
    // before **what is configured**, because until v0.14.0 the two were conflated and the second
    // was rendered under the first's heading whenever a model version was active (F60).
    const running = config.running || {
      kind: config.scorer_id, scorer_id: config.scorer_id,
      contract_version: config.contract_version, params_hash: config.params_hash,
      tunable: true, model_version: null,
    };
    return html`<div class="scorerview">
      <${Running} running=${running} degraded=${config.degraded}
                  degradedReason=${config.degraded_reason} />
      <${Configured} config=${config} running=${running} />
      <${Form} config=${config} onApplied=${this.reload} />
      <${History} config=${config} onRolledBack=${this.reload} />
    </div>`;
  }
}

/**
 * The five-number table an admin edits — **and whether it is currently deciding anything.**
 *
 * Named `Configured` rather than `Active` deliberately. The old name was the display half of F60:
 * with a model version running there is no scorer configuration at all, the route returned the
 * coded defaults, and this section rendered them under the word "Active".
 */
function Configured({ config, running }) {
  return html`<section class="panel-block">
    <${SectionHeading} title="Configured parameters"
      hint=${running.tunable
        ? "The five numbers the running engine is grouping with."
        : "Stored and inactive: a model version is deciding, so these numbers group nothing " +
          "until it is rolled back."} />
    <p class="muted">configuration #${config.config_id ?? "—"}
      ${running.tunable ? "" : " — not active"}</p>
    <p class="mono">${FIELDS.map(([key]) => `${key}=${config.params[key]}`).join("  ")}</p>
  </section>`;
}

class Form extends Component {
  constructor(props) {
    super(props);
    this.state = {
      values: { ...props.config.params }, note: "",
      refusals: [], preview: null, previewing: false, applying: false, error: null,
    };
  }

  candidate() {
    const out = { note: this.state.note };
    for (const [key] of FIELDS) out[key] = Number(this.state.values[key]);
    return out;
  }

  /**
   * Check before sending. Returns true when the submission is acceptable.
   *
   * **The refusal path issues no request.** A client that fired the request and rendered the
   * server's 400 would be teaching the operator that the console does not know its own rules,
   * and would put a rejected parameter set in the audit log's denied column for no reason.
   */
  checkHardening() {
    const refusals = scorerRefusals(this.candidate(), this.props.config.bounds);
    this.setState({ refusals });
    return refusals.length === 0;
  }

  async runPreview() {
    if (!this.checkHardening()) return;
    this.setState({ previewing: true, error: null });
    try {
      this.setState({ previewing: false, preview: await post("/api/scorer/preview", this.candidate()) });
    } catch (error) {
      this.setState({ previewing: false, error });
    }
  }

  async apply() {
    if (!this.checkHardening()) return null;
    this.setState({ applying: true, error: null });
    try {
      const out = await post("/api/scorer", this.candidate());
      this.setState({ applying: false });
      return out;
    } catch (error) {
      this.setState({ applying: false, error });
      throw error;
    }
  }

  render({ config, onApplied }, { values, note, refusals, preview, previewing, error }) {
    const bounds = config.bounds;
    return html`<section class="panel-block param-hardening">
      <${SectionHeading} title="Retune"
        hint=${"Changing these changes how every alarm groups, at the next engine reload. " +
               "Preview first; every change is audited and every configuration is kept."} />

      <form class="grid-form" onSubmit=${(e) => e.preventDefault()}>
        ${FIELDS.map(([key, label]) => html`<div key=${key}>
          <label for=${`sc-${key}`}>${label}</label>
          <input id=${`sc-${key}`} type="number" step="0.01" value=${values[key]}
                 onInput=${(e) => this.setState({ values: { ...values, [key]: e.target.value } })} />
        </div>`)}
        <div class="wide">
          <label for="sc-note">Note (recorded in the audit log)</label>
          <input id="sc-note" value=${note} placeholder="why are you changing this?"
                 onInput=${(e) => this.setState({ note: e.target.value })} />
        </div>
      </form>

      <p class="impact">Bounds: weights and threshold in [0, 1]; tau in
        [${score(bounds.min_tau_s, 2)}, ${score(bounds.max_tau_s, 2)}] s; the weights must sum to
        at least ${score(bounds.min_weight_sum, 2)}; the threshold must stay at least
        ${score(bounds.threshold_margin, 2)} below the weight sum. These are hardening-only:
        stricter is accepted, looser is refused.</p>

      ${refusals.length ? html`<${Refusals} refusals=${refusals} />` : null}

      ${can("scorer.preview") ? html`<div class="fb">
        <button type="button" disabled=${previewing} onClick=${() => this.runPreview()}>
          ${previewing ? "Running…" : "Preview effect"}
        </button>
      </div>` : html`<p class="hint">Your account holds <code class="mono">scorer.write</code>
        but not <code class="mono">scorer.preview</code>, so the what-if is not offered here.
        Applying still previews before it changes anything.</p>`}
      ${preview ? html`<${Preview} delta=${preview} />` : null}

      <${Destructive}
        title="Apply these parameters"
        hint="Grouping changes at the next engine reload."
        previewLabel="Apply…"
        confirmLabel="Apply and change grouping"
        consequence=${"Every alarm the appliance correlates from the next engine reload onwards " +
                      "is grouped by these numbers. Existing situations are not rewritten. The " +
                      "previous configuration is kept and can be rolled back in one click — " +
                      "scorer configuration is append-only, so this is reversible."}
        preview=${async () => {
          if (!this.checkHardening()) throw new Error("The values above cannot be applied.");
          return post("/api/scorer/preview", this.candidate());
        }}
        renderPreview=${(delta) => html`<${Preview} delta=${delta} />`}
        apply=${() => this.apply()}
        onDone=${onApplied} />

      ${error ? html`<p class="err" role="alert">${error.detail || error.message}</p>` : null}
    </section>`;
  }
}

/** The four things §6.2 requires, for each refused value. */
function Refusals({ refusals }) {
  return html`<div class="refusal" role="alert">
    <h4>Not applied — these values would loosen a project floor</h4>
    ${refusals.map((r, index) => html`<div class="refusal-item" key=${index}>
      <p><b>${r.headline}</b></p>
      <p>You entered <code class="mono">${String(r.submitted)}</code>; the project floor is
        <code class="mono">${String(r.floor)}${r.unit ? ` ${r.unit}` : ""}</code>.
        <b>Your value was not applied and nothing was sent to the appliance.</b></p>
      <p class="why">${r.why}</p>
      <p class="hint">${r.stricterIs} is accepted — this floor can be made stricter here, only
        not looser. The field is still usable; change it and try again.</p>
    </div>`)}
  </div>`;
}

function Preview({ delta }) {
  const merged = delta.merged || [];
  const split = delta.split || [];
  return html`<div class="scorer-preview">
    <${SectionHeading} title="Preview" hint=${delta.caveat} />
    <${DataTable} columns=${[
      { key: "which", label: "" },
      { key: "situations", label: "situations", numeric: true },
      { key: "links", label: "links", numeric: true },
    ]} rows=${[
      { key: "now", cells: { which: "now", situations: count(delta.situations_before), links: count(delta.links_before) } },
      { key: "after", cells: { which: "with these parameters", situations: html`<b>${count(delta.situations_after)}</b>`, links: count(delta.links_after) } },
    ]} />
    <p class="hint">${plural(merged.length, "group")} would merge,
      ${plural(split.length, "group")} would split, ${count(delta.unchanged_groups)} unchanged,
      over ${count(delta.alarms_considered)} recent alarm(s) (cap ${count(delta.alarms_cap)}).</p>
    ${merged.slice(0, 10).map((m, i) => html`<p class="mono" key=${`m${i}`}>
      merge: ${m.from_groups.length} groups → ${m.size} alarms</p>`)}
    ${split.slice(0, 10).map((s, i) => html`<p class="mono" key=${`s${i}`}>
      split: ${s.size} alarms → sizes ${s.into_sizes.join(", ")}</p>`)}
  </div>`;
}

class History extends Component {
  constructor(props) {
    super(props);
    this.state = { busy: null, error: null };
  }

  async rollback(id) {
    this.setState({ busy: id, error: null });
    try {
      await post("/api/scorer/rollback", { config_id: id });
      this.setState({ busy: null });
      this.props.onRolledBack();
    } catch (error) {
      this.setState({ busy: null, error });
    }
  }

  render({ config }, { busy, error }) {
    const history = config.history || [];
    return html`<section class="panel-block">
      <${SectionHeading} title="History"
        hint=${"Immutable and append-only: nothing here is ever edited or deleted. Rolling back " +
               "moves the active pointer to an earlier row, so it is not destructive and needs " +
               "no preview."} />
      <${DataTable} columns=${[
        { key: "id", label: "#", numeric: true },
        { key: "params", label: "parameters" },
        { key: "by", label: "by" },
        { key: "when", label: "when" },
        { key: "note", label: "note" },
        { key: "action", label: "" },
      ]} rows=${history.map((row) => ({
        key: row.id,
        cells: {
          id: row.active ? html`<b>${row.id} (active)</b>` : String(row.id),
          params: html`<code class="mono">${`${row.w_t}/${row.w_a}/${row.w_e} tau=${row.tau_s} thr=${row.threshold}`}</code>`,
          by: row.created_by || "—",
          when: cell(html`<${TimeCell} ts=${row.created_at} />`),
          note: row.note || "—",
          action: row.active ? "" : html`<button type="button" disabled=${busy === row.id}
            onClick=${() => this.rollback(row.id)}>
            ${busy === row.id ? "Rolling back…" : "Roll back"}</button>`,
        },
      }))} />
      ${error ? html`<p class="err" role="alert">${error.detail || error.message}</p>` : null}
    </section>`;
  }
}

void Loading; void Failed;
