/* A case's customer update: what the customer and the next NOC shift read (ADRs #459, #470).
 *
 * The handbook's Customer Update tab, with the appliance's words: title and description, the
 * executive summary (and each organization's own, when a company wants its own title), the current
 * analysis, the NOC's next step, the customer's, and when the NOC looks again. **It is never
 * final**: whoever works cases — the agent it is routed to above all — keeps it current as facts
 * arrive (the server says so in `actions`); a reader who does not work cases reads it. Saving
 * sends only what changed, with the version it was read at, so two writers never overwrite each
 * other.
 *
 * **Send customer update** sits at the end of the form, beside saving: it is how the update will
 * reach the customer by email. Sending is not connected yet, and the button says so rather than
 * pretending; it asks for the update to be saved first, because what is sent is what was saved.
 *
 * **The next update** offers its times as one click each — the handbook's cadence for the case's
 * priority first, then the usual steps — beside the free choice.
 */

import { html, Component, cx } from "../../dom.js";
import { post } from "../../api.js";
import { absolute } from "../../format.js";
import { Icon } from "../../icons.js";
import { span } from "./caseclock.js";

const pad = (n) => String(n).padStart(2, "0");

/** Epoch seconds as a `datetime-local` value, in the browser's zone. */
export function toLocalInput(epoch) {
  if (!epoch) return "";
  const d = new Date(epoch * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** A `datetime-local` value back to epoch seconds (null when empty or unreadable). */
export function fromLocalInput(text) {
  if (!text) return null;
  const t = new Date(text).getTime();
  return Number.isFinite(t) ? t / 1000 : null;
}

/** The times a next update is usually set to, as one click each: the handbook's cadence for the
 *  case's priority (`cadence_s`, from the catalogue's targets) first, then the usual steps. */
export function nextChoices(now, cadenceS) {
  const tomorrow = new Date(now * 1000);
  tomorrow.setDate(tomorrow.getDate() + 1);
  tomorrow.setHours(9, 0, 0, 0);
  const steps = [[30 * 60, "In 30 min"], [3600, "In 1 h"], [4 * 3600, "In 4 h"]]
    .filter(([s]) => s !== cadenceS)
    .map(([s, label]) => [now + s, label]);
  return [
    ...(cadenceS ? [[now + cadenceS, `Cadence (${span(cadenceS)})`]] : []),
    ...steps,
    [tomorrow.getTime() / 1000, "Tomorrow 09:00"],
  ];
}

/** A `datetime-local` with its usual times beside it. `value` is the input's text. */
export function NextPicker({ id, value, onChange, cadenceS, required }) {
  const now = Date.now() / 1000;
  return html`<div class="next-picker">
    <input id=${id} type="datetime-local" value=${value} required=${required}
      onInput=${(e) => onChange(e.currentTarget.value)} />
    <div class="next-choices" role="group" aria-label="Usual times">
      ${nextChoices(now, cadenceS).map(([at, label]) => html`<button type="button" key=${label}
        class=${cx("next-choice", toLocalInput(at) === value && "on")}
        onClick=${() => onChange(toLocalInput(at))}>${label}</button>`)}
    </div>
  </div>`;
}

const LONG = new Set(["description", "current_analysis", "noc_next_step", "customer_next_step"]);
const ORDER = ["title", "executive_summary", "description", "current_analysis", "noc_next_step",
  "customer_next_step"];

function initial(c) {
  const fields = Object.fromEntries(ORDER.map((k) => [k, c[k] || ""]));
  const summaries = Object.fromEntries((c.organizations || []).map((o) => [o.id, o.summary || ""]));
  return { fields, summaries, next: toLocalInput(c.next_update_at), priority: c.priority };
}

export class CaseForm extends Component {
  constructor(props) {
    super(props);
    this.state = { ...initial(props.c), busy: false, error: null, saved: false, sendNote: null };
  }

  changes() {
    const { c } = this.props;
    const st = this.state;
    const body = {};
    for (const k of ORDER) if ((c[k] || "") !== st.fields[k]) body[k] = st.fields[k];
    const next = fromLocalInput(st.next);
    if (next && toLocalInput(next) !== toLocalInput(c.next_update_at)) body.next_update_at = next;
    if (st.priority !== c.priority) body.priority = st.priority;
    const summaries = {};
    for (const o of c.organizations || []) {
      if ((o.summary || "") !== (st.summaries[o.id] || "")) summaries[o.id] = st.summaries[o.id] || "";
    }
    if (Object.keys(summaries).length) body.summaries = summaries;
    return body;
  }

  async save(event) {
    event.preventDefault();
    const body = this.changes();
    if (!Object.keys(body).length) return;
    this.setState({ busy: true, error: null, saved: false, sendNote: null });
    try {
      const c = await post(`/api/noc/cases/${this.props.c.id}`, { ...body, version: this.props.c.version });
      this.setState({ busy: false, saved: true });
      this.props.onSaved(c);
    } catch (error) {
      this.setState({ busy: false, error: error.message });
    }
  }

  componentDidUpdate(previous) {
    // A newer version read from the server replaces the form only when nothing is being typed.
    if (previous.c.version !== this.props.c.version && !Object.keys(this.changes()).length) {
      this.setState(initial(this.props.c));
    }
  }

  field(name) {
    const meta = this.props.fields[name];
    const id = `case-${this.props.c.id}-${name}`;
    const value = this.state.fields[name];
    const set = (e) => this.setState({ fields: { ...this.state.fields, [name]: e.currentTarget.value }, saved: false });
    const control = LONG.has(name)
      ? html`<textarea id=${id} rows=${name === "current_analysis" ? 6 : 3} maxlength=${meta.max}
          value=${value} onInput=${set}></textarea>`
      : html`<input id=${id} value=${value} maxlength=${meta.max} onInput=${set} />`;
    return html`<div class=${LONG.has(name) ? "pe-field case-wide" : "pe-field"} key=${name}>
      <label for=${id}>${meta.label}</label>${control}</div>`;
  }

  /** Not connected yet (ADR #470): the button is the place, and it says what it will do. */
  send() {
    this.setState({ sendNote: "Sending to the customer by email is not connected yet. The update "
      + "is saved, and the NOC and the agents read it." });
  }

  render({ c, editable, priorities, cadence }, st) {
    if (!editable) return html`<${CaseUpdateView} c=${c} fields=${this.props.fields} />`;
    const p = `case-${c.id}`;
    const dirty = Object.keys(this.changes()).length > 0;
    const cadenceS = cadence && cadence.working ? cadence.working[c.priority] : null;
    return html`<form class="case-form" onSubmit=${(e) => this.save(e)} autocomplete="off">
      <div class="pe-fields">
        ${ORDER.map((k) => this.field(k))}
        <div class="pe-field case-wide"><label for=${`${p}-next`}>Next update</label>
          <${NextPicker} id=${`${p}-next`} value=${st.next} cadenceS=${cadenceS}
            onChange=${(next) => this.setState({ next, saved: false })} /></div>
        <div class="pe-field"><label for=${`${p}-prio`}>Priority</label>
          <select id=${`${p}-prio`} value=${st.priority}
            onChange=${(e) => this.setState({ priority: e.currentTarget.value, saved: false })}>
            ${Object.entries(priorities).map(([k, v]) => html`<option key=${k} value=${k}>${v}</option>`)}
          </select></div>
      </div>
      ${(c.organizations || []).length ? html`<fieldset class="case-orgs">
        <legend>Executive summary per company — empty reads the one above</legend>
        ${c.organizations.map((o) => html`<div class="pe-field" key=${o.id}>
          <label for=${`${p}-org-${o.id}`}>${o.name}</label>
          <input id=${`${p}-org-${o.id}`} maxlength="200" value=${st.summaries[o.id] || ""}
            placeholder=${st.fields.executive_summary || "The case's executive summary"}
            onInput=${(e) => this.setState({ summaries: { ...st.summaries, [o.id]: e.currentTarget.value }, saved: false })} />
        </div>`)}
      </fieldset>` : null}
      ${st.error ? html`<p class="err" role="alert">${st.error}</p>` : null}
      <div class="case-form-acts">
        <button type="submit" class="primary" disabled=${st.busy || !dirty}>
          ${st.busy ? "Saving…" : "Save the customer update"}</button>
        ${st.saved && !dirty ? html`<span class="muted" role="status">Saved.</span>` : null}
        <button type="button" class="primary send-update" disabled=${st.busy || dirty}
          title=${dirty ? "Save the customer update first: what is sent is what was saved."
            : "Email this customer update to the customer."}
          onClick=${() => this.send()}><${Icon} name="send" /> Send customer update</button>
      </div>
      ${st.sendNote ? html`<p class="hint send-note" role="status">${st.sendNote}</p>` : null}
    </form>`;
  }
}

/** The customer update, read-only. */
function CaseUpdateView({ c, fields }) {
  return html`<dl class="case-view">
    ${ORDER.map((k) => html`<div key=${k} class=${LONG.has(k) ? "case-wide" : ""}>
      <dt>${fields[k].label}</dt><dd class="case-text">${c[k] || html`<span class="muted">—</span>`}</dd>
    </div>`)}
    <div><dt>Next update</dt><dd>${c.next_update_at ? absolute(c.next_update_at) : "—"}</dd></div>
    ${(c.organizations || []).map((o) => html`<div key=${o.id}>
      <dt>Executive summary — ${o.name}</dt><dd>${o.reads || "—"}</dd></div>`)}
  </dl>`;
}
