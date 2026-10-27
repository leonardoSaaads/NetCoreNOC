/* The two acts that take a form: setting a case pending, and resolving it (ADR #459).
 *
 * * **Pending** names who the NOC waits on and when it looks again. The server refuses it until
 *   the customer update is complete; the form says what is still missing before it is sent.
 * * **Resolving** records the cause and the solution, and closes the situation. Its alarms are
 *   cleared only when the box is ticked — the person checked the device and nothing is active
 *   any more — because a clear is a fact about the alarm, not a convenience.
 */

import { html, Component } from "../../dom.js";
import { post } from "../../api.js";
import { fromLocalInput, toLocalInput } from "./caseform.js";

export class PendForm extends Component {
  constructor(props) {
    super(props);
    const first = props.pending[0];
    this.state = { status: first, next: toLocalInput(props.c.next_update_at), note: "", busy: false,
      error: null };
  }

  async submit(event) {
    event.preventDefault();
    const { c } = this.props;
    const st = this.state;
    const next = fromLocalInput(st.next);
    this.setState({ busy: true, error: null });
    try {
      const body = { status: st.status, note: st.note, version: c.version };
      if (next && next !== c.next_update_at) body.next_update_at = next;
      this.props.onDone(await post(`/api/noc/cases/${c.id}/status`, body));
    } catch (error) {
      this.setState({ busy: false, error: error.message });
    }
  }

  render({ c, pending, statuses, fields, onCancel }, st) {
    const p = `pend-${c.id}`;
    const missing = c.missing.filter((m) => m !== "next_update_at");
    return html`<form class="case-act panel-block" onSubmit=${(e) => this.submit(e)}>
      <h3>Set pending</h3>
      <p class="hint">The case waits until its next update, then returns to the NOC queue — sooner if
        a person posts on its feed.</p>
      ${missing.length ? html`<p class="warnbox">Complete the customer update first: ${missing
        .map((m) => (fields[m] ? fields[m].label : m)).join(", ")}.</p>` : null}
      <div class="pe-fields">
        <div class="pe-field"><label for=${`${p}-status`}>Waiting on</label>
          <select id=${`${p}-status`} value=${st.status}
            onChange=${(e) => this.setState({ status: e.currentTarget.value })}>
            ${pending.map((s) => html`<option key=${s} value=${s}>${statuses[s]}</option>`)}
          </select></div>
        <div class="pe-field"><label for=${`${p}-next`}>Next update</label>
          <input id=${`${p}-next`} type="datetime-local" required value=${st.next}
            onInput=${(e) => this.setState({ next: e.currentTarget.value })} /></div>
        <div class="pe-field case-wide"><label for=${`${p}-note`}>Note for the feed (optional)</label>
          <input id=${`${p}-note`} maxlength="2000" value=${st.note}
            onInput=${(e) => this.setState({ note: e.currentTarget.value })} /></div>
      </div>
      ${st.error ? html`<p class="err" role="alert">${st.error}</p>` : null}
      <div class="stepup-acts">
        <button type="submit" class="primary" disabled=${st.busy}>${st.busy ? "Saving…" : "Set pending"}</button>
        <button type="button" onClick=${onCancel}>Cancel</button>
      </div>
    </form>`;
  }
}

export class ResolveForm extends Component {
  constructor(props) {
    super(props);
    this.state = { cause: "", solution: "", verified: false, busy: false, error: null };
  }

  async submit(event) {
    event.preventDefault();
    const { c } = this.props;
    const st = this.state;
    this.setState({ busy: true, error: null });
    try {
      this.props.onDone(await post(`/api/noc/cases/${c.id}/resolve`, {
        cause: st.cause, solution: st.solution, alarms_verified_clear: st.verified, version: c.version,
      }));
    } catch (error) {
      this.setState({ busy: false, error: error.message });
    }
  }

  render({ c, causes, onCancel }, st) {
    const p = `resolve-${c.id}`;
    return html`<form class="case-act panel-block" onSubmit=${(e) => this.submit(e)}>
      <h3>Resolve</h3>
      <p class="hint">Resolving closes situation #${c.id} on the Broker. A day later, with nothing
        new, the case closes.</p>
      <div class="pe-fields">
        <div class="pe-field"><label for=${`${p}-cause`}>Cause</label>
          <select id=${`${p}-cause`} required value=${st.cause}
            onChange=${(e) => this.setState({ cause: e.currentTarget.value })}>
            <option value="">Choose…</option>
            ${Object.entries(causes).map(([k, v]) => html`<option key=${k} value=${k}>${v}</option>`)}
          </select></div>
        <div class="pe-field case-wide"><label for=${`${p}-solution`}>Solution — what, how, why</label>
          <textarea id=${`${p}-solution`} rows="4" required maxlength="8000" value=${st.solution}
            onInput=${(e) => this.setState({ solution: e.currentTarget.value })}></textarea></div>
      </div>
      <label class="check-row"><input type="checkbox" checked=${st.verified}
        onChange=${(e) => this.setState({ verified: e.currentTarget.checked })} />
        I checked the device: these alarms are no longer active. Clear them (the clear trap never
        arrived).</label>
      ${st.error ? html`<p class="err" role="alert">${st.error}</p>` : null}
      <div class="stepup-acts">
        <button type="submit" class="primary" disabled=${st.busy || !st.cause || !st.solution.trim()}>
          ${st.busy ? "Resolving…" : "Resolve the case"}</button>
        <button type="button" onClick=${onCancel}>Cancel</button>
      </div>
    </form>`;
  }
}
