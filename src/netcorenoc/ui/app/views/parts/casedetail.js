/* One case, full width: where the NOC works it (ADRs #459-#461).
 *
 * Top to bottom, the order a NOC engineer reads a case in: who owns it and how urgent it is; the
 * status path (queue → working → pending → validate → resolved → closed) with the step it is on;
 * the clocks (take, customer update, next update); what can be done now; the customer update; the
 * feed. The traps and the elements are the situation's, one link away on the Broker.
 */

import { html, Component, cx } from "../../dom.js";
import { get, post } from "../../api.js";
import { can } from "../../session.js";
import { age, relative, timeTitle } from "../../format.js";
import { Clock, NextUpdate, Priority } from "./caseclock.js";
import { CaseForm } from "./caseform.js";
import { PendForm, ResolveForm } from "./caseacts.js";
import { CaseFeed } from "./casefeed.js";

const PATH = [
  ["queue", "Queue"], ["automation", "Automated"], ["working", "Working"], ["pending", "Pending"],
  ["validate", "Validate"], ["resolved", "Resolved"], ["closed", "Closed"],
];
const REFRESH_MS = 15000;

function StatusPath({ at }) {
  const auto = at === "auto";
  const steps = PATH.filter(([id]) => id !== "automation" || auto)
    .filter(([id]) => id !== "validate" || at === "validate");
  const here = steps.findIndex(([id]) => id === (auto ? "automation" : at));
  return html`<ol class="case-path" aria-label="Status path">
    ${steps.map(([id, label], i) => html`<li key=${id}
      class=${cx("case-step", i < here && "done", i === here && "here")}
      aria-current=${i === here ? "step" : null}>${label}</li>`)}
  </ol>`;
}

export class CaseDetail extends Component {
  constructor(props) {
    super(props);
    this.state = { c: null, error: null, mode: null, busy: false, note: null, now: Date.now() / 1000 };
  }

  componentDidMount() {
    this.read();
    this.poll = setInterval(() => { if (!this.state.mode) this.read(); }, REFRESH_MS);
    this.tick = setInterval(() => this.setState({ now: Date.now() / 1000 }), 1000);
  }

  componentWillUnmount() { clearInterval(this.poll); clearInterval(this.tick); }

  async read() {
    try {
      this.setState({ c: await get(`/api/noc/cases/${this.props.id}`), error: null,
        now: Date.now() / 1000 });
    } catch (error) {
      this.setState({ error: error.message });
    }
  }

  done(c) {
    this.setState({ c, mode: null, busy: false, note: null });
    this.props.onChanged();
  }

  async act(write) {
    this.setState({ busy: true, note: null });
    try {
      this.done(await write());
    } catch (error) {
      this.setState({ busy: false, note: error.message });
    }
  }

  actions(c) {
    const { busy } = this.state;
    // The server lists what this reader may do (`actions`); `can` is the console's own floor.
    const has = (a) => can("cases.work") && c.actions.includes(a);
    return html`<div class="case-actions">
      ${has("take") ? html`<button type="button" class="primary" disabled=${busy}
        onClick=${() => this.act(() => post(`/api/noc/cases/${c.id}/take`, {}))}>${!c.owner ? "Take" : c.owner.you ? "Resume work"
          : "Take it over"}</button>` : null}
      ${has("pend") ? html`<button type="button" disabled=${busy}
        onClick=${() => this.setState({ mode: "pend" })}>Set pending…</button>` : null}
      ${has("resolve") ? html`<button type="button" disabled=${busy}
        onClick=${() => this.setState({ mode: "resolve" })}>Resolve…</button>` : null}
      ${has("release") ? html`<button type="button" disabled=${busy}
        onClick=${() => this.act(() => post(`/api/noc/cases/${c.id}/release`, { note: "" }))}>Back to the queue</button>` : null}
      ${!c.actions.length ? html`<span class="muted">${c.owner && !c.owner.you
        ? `Owned by ${c.owner.name}.` : "Read-only for you."}</span>` : null}
    </div>`;
  }

  milestones(c, now) {
    const clocks = c.clocks || {};
    return html`<dl class="case-clocks">
      <div><dt>Take</dt><dd><${Clock} clock=${clocks.take} now=${now} label="Take" /></dd></div>
      <div><dt>Customer update</dt><dd><${Clock} clock=${clocks.document} now=${now}
        label="Customer update" /></dd></div>
      <div><dt>Next update</dt><dd><${NextUpdate} cadence=${c.cadence} now=${now} /></dd></div>
      <div><dt>Open for</dt><dd title=${timeTitle(c.opened_at)}>${age(c.opened_at, now)}</dd></div>
    </dl>`;
  }

  history(c) {
    if (!c.cycles || !c.cycles.length) return null;
    return html`<details class="case-cycles"><summary>In the NOC queue ${c.cycles.length} time${c.cycles.length > 1 ? "s" : ""}</summary>
      <table class="data"><thead><tr><th scope="col">Why</th><th scope="col">Entered</th>
        <th scope="col">Take</th><th scope="col">Customer update</th></tr></thead>
      <tbody>${c.cycles.map((y, i) => html`<tr key=${i}>
        <td>${y.reason_label}</td><td title=${timeTitle(y.entered_at)}>${relative(y.entered_at)}</td>
        <td><${Clock} clock=${y.take} now=${this.state.now} /></td>
        <td><${Clock} clock=${y.document} now=${this.state.now} /></td>
      </tr>`)}</tbody></table></details>`;
  }

  render({ catalogue, onBack }, { c, error, mode, note, now }) {
    if (error) return html`<div><button type="button" class="linkish" onClick=${onBack}>← The queue</button>
      <p class="err" role="alert">${error}</p></div>`;
    if (!c) return html`<p class="muted">Opening the case…</p>`;
    const edit = can("cases.work") && c.actions.includes("edit");
    return html`<article class="case-detail" aria-labelledby=${`case-${c.id}-heading`}>
      <button type="button" class="linkish" onClick=${onBack}>← The queue</button>
      <header class="case-head">
        <${Priority} priority=${c.priority} label=${c.priority_label} />
        <h2 id=${`case-${c.id}-heading`}><span class="mono">#${c.id}</span> ${c.title || c.situation.name
          || "Untitled case"}</h2>
        <span class=${cx("case-status", `case-${c.super_state}`)}>${c.status_label}</span>
        <span class="case-owner">${c.owner ? `Owner: ${c.owner.name}${c.owner.you ? " (you)" : ""}`
          : "Nobody has taken it"}</span>
      </header>
      <${StatusPath} at=${c.status === "auto" ? "auto" : c.super_state} />
      ${this.milestones(c, now)}
      ${this.actions(c)}
      ${note ? html`<p class="err" role="alert">${note}</p>` : null}
      ${mode === "pend" ? html`<${PendForm} c=${c} pending=${catalogue.pending}
        statuses=${catalogue.statuses} fields=${catalogue.fields} onDone=${(x) => this.done(x)}
        onCancel=${() => this.setState({ mode: null })} />` : null}
      ${mode === "resolve" ? html`<${ResolveForm} c=${c} causes=${catalogue.causes}
        onDone=${(x) => this.done(x.id ? x : c)} onCancel=${() => this.setState({ mode: null })} />` : null}
      ${c.resolution ? html`<section class="panel-block case-resolution">
        <h3>${c.resolution.how_label}</h3>
        <p><b>${c.resolution.cause_label}.</b> ${c.resolution.solution}</p>
        ${c.resolution.alarms_cleared ? html`<p class="hint">Its alarms were verified clear on the device and cleared.</p>` : null}
      </section>` : null}
      <div class="case-columns">
        <section class="case-update" aria-label="Customer update">
          <h3>Customer update</h3>
          ${edit && c.missing.length ? html`<p class="hint">Still to fill before it can wait on
            someone: ${c.missing.map((m) => (catalogue.fields[m] ? catalogue.fields[m].label : "Next update")).join(", ")}.</p>` : null}
          <${CaseForm} c=${c} editable=${edit} fields=${catalogue.fields}
            priorities=${catalogue.priorities} onSaved=${(x) => this.done(x)} />
          <p class="case-links"><a class="tap" href=${`#/situations/${c.id}`}>Its traps and elements:
            situation #${c.id} on the Broker →</a></p>
          ${this.history(c)}
        </section>
        <${CaseFeed} id=${c.id} version=${c.version} onRequeued=${() => this.read()} />
      </div>
    </article>`;
  }
}
