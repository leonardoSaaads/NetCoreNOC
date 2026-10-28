/* An agent's Runs tab (ADR #468): each run, and — opened — what it did, step by step.
 *
 * A run is one wake-up: why (case assigned, heartbeat, invoked, escalated, or on its own over MCP),
 * how it ended, how long it took, how many tools it called and what it cost. Its steps are the
 * transcript Paperclip shows: each tool it called, with what it asked (never a lease, token or
 * key) and how the route answered; what it said; and the error that ended it, if one did.
 */

import { html, Component, cx } from "../../dom.js";
import { get } from "../../api.js";
import { relative, timeTitle, absolute } from "../../format.js";
import { RunState, money } from "./nocui.js";
import { span } from "./caseclock.js";

const KIND = { wake: "Woken", tool: "Tool", text: "Said", error: "Error" };
const INLINE = 80;

class Steps extends Component {
  constructor(props) {
    super(props);
    this.state = { steps: null, error: null };
  }

  async componentDidMount() {
    try {
      this.setState({ steps: (await get(`/api/noc/runs/${this.props.id}`)).steps });
    } catch (error) {
      this.setState({ error: error.message });
    }
  }

  render(_props, { steps, error }) {
    if (error) return html`<p class="err" role="alert">${error}</p>`;
    if (!steps) return html`<p class="muted">Reading its steps…</p>`;
    if (!steps.length) return html`<p class="muted">It took no step.</p>`;
    return html`<ol class="run-steps">${steps.map((s) => html`<li key=${s.id}
        class=${cx("run-step", `step-${s.kind}`, s.ok === false && "step-failed")}>
      <div class="run-step-head">
        <span class="step-kind">${KIND[s.kind] || s.kind}</span>
        ${s.name ? html`<code>${s.name}</code>` : null}
        ${s.ok === false ? html`<span class="run-state run-bad">refused</span>`
          : s.kind === "tool" ? html`<span class="run-state run-ok">ok</span>` : null}
        <span class="muted" title=${timeTitle(s.at)}>${absolute(s.at)}</span>
        ${s.detail && s.detail.length <= INLINE ? html`<code class="run-step-short">${s.detail}</code>` : null}
      </div>
      ${s.detail && s.detail.length > INLINE ? html`<p class="run-step-detail">${s.detail}</p>` : null}
    </li>`)}</ol>`;
  }
}

export class AgentRuns extends Component {
  constructor(props) {
    super(props);
    this.state = { open: props.runs.length ? props.runs[0].id : null };
  }

  render({ runs }, { open }) {
    if (!runs.length) return html`<p class="muted">No run yet.</p>`;
    return html`<ul class="run-list">${runs.map((r) => {
      const took = r.ended_at && r.started_at ? span(r.ended_at - r.started_at) : r.started_at ? "running" : "—";
      const shown = open === r.id;
      return html`<li key=${r.id} class=${cx("run-item", shown && "open")}>
        <button type="button" class="run-row" aria-expanded=${shown ? "true" : "false"}
          onClick=${() => this.setState({ open: shown ? null : r.id })}>
          <span class="mono muted">#${r.id}</span>
          <${RunState} status=${r.status} label=${r.status_label} />
          <span class="run-row-why">${r.trigger_label}${r.situation_id ? ` · case #${r.situation_id}` : ""}</span>
          <span class="muted" title=${timeTitle(r.queued_at)}>${relative(r.queued_at)}</span>
          <span class="muted wide-only">${took}</span>
          <span class="muted wide-only">${r.tool_calls} tool${r.tool_calls === 1 ? "" : "s"}</span>
          <span class="muted wide-only">${money(r.cost_cents)}</span>
        </button>
        ${shown ? html`<div class="run-body">
          ${r.summary ? html`<p class="run-summary">${r.summary}</p>` : null}
          ${r.error ? html`<p class="err">${r.error}</p>` : null}
          ${r.input_tokens || r.output_tokens ? html`<p class="muted">${r.input_tokens} tokens in ·
            ${r.output_tokens} out${r.requested_by ? ` · asked by ${r.requested_by}` : ""}</p>`
            : r.requested_by ? html`<p class="muted">Asked by ${r.requested_by}</p>` : null}
          <${Steps} id=${r.id} />
        </div>` : null}
      </li>`;
    })}</ul>`;
  }
}
