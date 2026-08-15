/* The feedback-corpus retention tiers — **the one control in this product that really destroys.**
 *
 * Split out of `settings.js` because it is a different noun and a different risk. Every other
 * configuration in this appliance is reversible: `scorer_config` is append-only and rollback is a
 * pointer move, governance policies are versioned. **Lowering the audit tier deletes rows and
 * there is no rollback for a DELETE** — an admin moving it from twelve months to three destroys
 * nine months of human labels, the most expensive and least reconstructible asset in the system.
 *
 * It is also the only destructive control whose preview is a **real count from the appliance**
 * rather than a statement that no count is available, because `POST /api/dataset/retention`
 * previews by default (`preview=True` must be deliberately overridden). Both the preview and the
 * change are audited.
 */

import { html, Component } from "../dom.js";
import { post } from "../api.js";
import { SectionHeading } from "../widgets.js";
import { count } from "../format.js";
import { Destructive } from "../destructive.js";
import { MECHANISM } from "../parameters.js";

export class DatasetRetention extends Component {
  constructor(props) {
    super(props);
    const d = props.data;
    this.state = {
      sink_days: d.sink_days, sink_rows: d.sink_rows,
      training_days: d.training_days, audit_days: d.audit_days,
    };
  }

  body(preview) {
    return {
      sink_days: Number(this.state.sink_days),
      sink_rows: Number(this.state.sink_rows),
      training_days: Number(this.state.training_days),
      audit_days: Number(this.state.audit_days),
      preview,
    };
  }

  render({ data, onSaved }) {
    const stats = data.stats || {};
    return html`<section class=${`panel-block param-${MECHANISM}`}>
      <${SectionHeading} title="Feedback-corpus retention"
        hint=${"The three tiers that decide how long the human-labelled corpus lives. This is " +
               "the most expensive and least reconstructible asset in the system: six months " +
               "not captured cannot be reconstructed."} />

      <div class="stat-row">
        ${Object.entries(stats).map(([key, value]) => html`
          <div class="stat" key=${key}>
            <div class="stat-value">${count(value)}</div>
            <div class="stat-label">${key.replaceAll("_", " ")}</div>
          </div>`)}
      </div>
      <p class="hint">Capture is currently <b>${data.capture_enabled ? "on" : "off"}</b>.
        Since this process started, the background sweep has removed
        ${Object.entries(data.audit_swept || {}).map(([k, v]) => `${count(v)} ${k}`).join(", ") || "nothing"}.</p>

      <form class="grid-form" onSubmit=${(e) => e.preventDefault()}>
        ${[["sink_days", "Sink tier (days)"], ["sink_rows", "Sink tier (rows)"],
           ["training_days", "Training tier (days)"], ["audit_days", "Audit tier (days)"]]
          .map(([key, label]) => html`<div key=${key}>
            <label for=${`ret-${key}`}>${label}</label>
            <input id=${`ret-${key}`} type="number" step="1" value=${this.state[key]}
                   onInput=${(e) => this.setState({ [key]: e.target.value })} />
          </div>`)}
      </form>
      <p class="impact">The ordering <code>sink &lt; training ≤ audit</code> is enforced by the
        appliance with a precise reason, not a bare rejection. <b>The audit tier is the
        destructive one</b>: it is the outer edge of the data's life. Lowering the training tier
        destroys nothing — it narrows what a model may read.</p>

      <${Destructive}
        title="Apply these retention tiers"
        hint="The preview is a real count from the appliance, and the preview itself is audited."
        previewLabel="Preview what this would delete"
        confirmLabel="Apply and delete"
        consequence=${"Rows older than the audit tier are deleted in the same audited " +
                      "transaction as the change. Human labels are the input to every evidence " +
                      "claim this product makes, and a deleted label cannot be recovered."}
        preview=${async () => post("/api/dataset/retention", this.body(true))}
        renderPreview=${(result) => html`<div>
          <p><b>Would delete:</b></p>
          <ul>${Object.entries(result.would_delete || {}).map(([k, v]) => html`
            <li key=${k}>${count(v)} ${k.replaceAll("_", " ")}</li>`)}</ul>
          <p class="hint">Bound: <code>${result.bound}</code>. Lowering the training tier deletes
            ${count(result.training_deletes)} rows — it selects rather than destroys.</p>
        </div>`}
        apply=${async () => post("/api/dataset/retention", this.body(false))}
        renderResult=${(result) => html`<span>Deleted
          ${Object.entries(result.deleted || {}).map(([k, v]) => `${count(v)} ${k}`).join(", ") || "nothing"}.</span>`}
        onDone=${onSaved} />
    </section>`;
  }
}

