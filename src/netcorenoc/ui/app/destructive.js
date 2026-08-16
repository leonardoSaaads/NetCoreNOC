/* **Nothing destructive without a preview** (§IV.1, draft §7.2).
 *
 * This project already had the pattern; it had it in one place. `routes_admin.py` reasons about
 * dataset retention like this:
 *
 * > *"lowering retention deletes rows and there is no rollback"* — so the endpoint **previews by
 * > default**, `preview=True` must be deliberately overridden, and both the preview and the
 * > change are audited.
 *
 * Every destructive control in this console goes through this component, so the sequence is the
 * same everywhere an operator meets one:
 *
 *   1. **the consequence in words, before anything is clicked** — what is destroyed, and that it
 *      cannot be undone;
 *   2. **a preview**, which is a separate deliberate action and never happens on render;
 *   3. **the apply button does not exist until the preview has been seen.** Not disabled —
 *      absent. A disabled button is a thing to click twice; an absent one is a step you have not
 *      reached.
 *
 * ## When the route has no preview mode
 *
 * `preview()` may answer `{ measured: false, message }`. That renders as an explicit statement
 * that the count is **not** available, and the apply step still requires the operator to have
 * asked. **A fabricated count would be worse than none** — it is Appendix B's "measuring nothing
 * and concluding CLOSED" pointed at an operator instead of at a gate.
 */

import { html, Component } from "./dom.js";
import { SectionHeading } from "./widgets.js";

export class Destructive extends Component {
  constructor(props) {
    super(props);
    this.state = { stage: "idle", preview: null, error: null, result: null };
  }

  async runPreview() {
    this.setState({ stage: "previewing", error: null });
    try {
      this.setState({ stage: "previewed", preview: await this.props.preview() });
    } catch (error) {
      this.setState({ stage: "idle", error });
    }
  }

  async runApply() {
    this.setState({ stage: "applying", error: null });
    try {
      const result = await this.props.apply();
      this.setState({ stage: "done", result });
      if (this.props.onDone) this.props.onDone(result);
    } catch (error) {
      this.setState({ stage: "previewed", error });
    }
  }

  render(props, { stage, preview, error, result }) {
    const { title, hint, consequence, previewLabel, confirmLabel, renderPreview } = props;
    return html`<section class="panel-block destructive" data-stage=${stage}>
      <${SectionHeading} title=${title} hint=${hint} />

      <p class="consequence" role="note"><b>This cannot be undone.</b> ${consequence}</p>

      ${stage === "idle" || stage === "previewing" ? html`
        <button type="button" disabled=${stage === "previewing"} onClick=${() => this.runPreview()}>
          ${stage === "previewing" ? "Checking…" : (previewLabel || "Preview")}
        </button>` : null}

      ${stage === "previewed" || stage === "applying" ? html`<div class="preview-result">
        ${preview && preview.measured === false
          ? html`<p class="warnbox"><b>No count is available.</b> ${preview.message}</p>`
          : (renderPreview ? renderPreview(preview) : html`<pre class="mono">${JSON.stringify(preview, null, 2)}</pre>`)}
        <div class="fb">
          <button type="button" class="warn" disabled=${stage === "applying"}
                  onClick=${() => this.runApply()}>
            ${stage === "applying" ? "Applying…" : (confirmLabel || "Apply")}
          </button>
          <button type="button" disabled=${stage === "applying"}
                  onClick=${() => this.setState({ stage: "idle", preview: null })}>Cancel</button>
        </div>
      </div>` : null}

      ${stage === "done" ? html`<div class="ok-note" role="status">
        <b>Done.</b> ${props.renderResult ? props.renderResult(result) : "The change was applied and audited."}
        <button type="button" onClick=${() => this.setState({ stage: "idle", preview: null, result: null })}>
          Close
        </button>
      </div>` : null}

      ${error ? html`<p class="err" role="alert">${error.detail || error.message}</p>` : null}
    </section>`;
  }
}
