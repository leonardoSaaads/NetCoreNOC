/* A case's customer update: what the customer and the next NOC shift read (ADR #459).
 *
 * The handbook's Customer Update tab, with the appliance's words: title and description, the
 * executive summary (and each organization's own, when a company wants its own title), the current
 * analysis, the NOC's next step, the customer's, and when the NOC looks again. Editable by the
 * case's owner (the server says so in `actions`); read-only for everyone else. Saving sends only
 * what changed, with the version it was read at, so two people never overwrite each other.
 */

import { html, Component } from "../../dom.js";
import { post } from "../../api.js";
import { absolute } from "../../format.js";

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
    this.state = { ...initial(props.c), busy: false, error: null, saved: false };
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
    this.setState({ busy: true, error: null, saved: false });
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

  render({ c, editable, priorities }, st) {
    if (!editable) return html`<${CaseUpdateView} c=${c} fields=${this.props.fields} />`;
    const p = `case-${c.id}`;
    const dirty = Object.keys(this.changes()).length > 0;
    return html`<form class="case-form" onSubmit=${(e) => this.save(e)} autocomplete="off">
      <div class="pe-fields">
        ${ORDER.map((k) => this.field(k))}
        <div class="pe-field"><label for=${`${p}-next`}>Next update</label>
          <input id=${`${p}-next`} type="datetime-local" value=${st.next}
            onInput=${(e) => this.setState({ next: e.currentTarget.value, saved: false })} /></div>
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
      <div class="stepup-acts">
        <button type="submit" class="primary" disabled=${st.busy || !dirty}>
          ${st.busy ? "Saving…" : "Save the customer update"}</button>
        ${st.saved && !dirty ? html`<span class="muted" role="status">Saved.</span>` : null}
      </div>
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
