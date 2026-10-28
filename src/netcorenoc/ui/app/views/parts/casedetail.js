/* One case, as a Salesforce case record (ADRs #459-#461, #468).
 *
 * Top to bottom, the order a NOC engineer reads a case in: the record's header — what it is and
 * what can be done now; its highlights (priority, status, level, who holds it, since when); the
 * status path the handbook draws (pending assignment → working → pending → validate resolution →
 * resolved → closed) with the step it is on and what that step asks of the NOC; then the work —
 * the feed, the customer update, its history in the queue — beside its milestones, who holds it
 * among the agents, and the situation it is about.
 */

import { html, Component, cx } from "../../dom.js";
import { get, post } from "../../api.js";
import { can } from "../../session.js";
import { age, relative, timeTitle } from "../../format.js";
import { Icon } from "../../icons.js";
import { Clock, Priority, span } from "./caseclock.js";
import { CaseForm } from "./caseform.js";
import { PendForm, ResolveForm } from "./caseacts.js";
import { CaseFeed } from "./casefeed.js";
import { Milestones } from "./casemilestones.js";
import { Assignment, MoveForm } from "./caseassign.js";

const PATH = [
  ["queue", "Pending assignment"], ["working", "Working"], ["pending", "Pending"],
  ["validate", "Validate resolution"], ["resolved", "Resolved"], ["closed", "Closed"],
];
const REFRESH_MS = 15000;

/** What the step the case is on asks of the NOC — Salesforce's guidance, in the handbook's words. */
function guidance(c) {
  const take = c.clocks && c.clocks.take;
  if (c.status === "auto") return "Automated checks are running a playbook on it before the queue.";
  return {
    queue: `Take it${take ? ` within ${span(take.target_s)} of entering the queue` : ""}: the take is the NOC's first promise.`,
    working: "Fill the customer update within 30 minutes of the take, and say when the next one comes.",
    pending: "Waiting on someone outside the NOC. It returns to the queue at its next update — sooner if someone posts.",
    validate: "Its alarms cleared. Confirm the service is restored, then resolve it.",
    resolved: "It closes after a day with nothing new; a new alarm reopens it.",
    closed: "Closed.",
  }[c.super_state] || "";
}

function StatusPath({ c, now }) {
  const at = c.super_state;
  const here = PATH.findIndex(([id]) => id === at);
  return html`<div class="path-block">
    <ol class="case-path" aria-label="Status path">
      ${PATH.map(([id, label], i) => html`<li key=${id}
        class=${cx("case-step", i < here && "done", i === here && "here")}
        aria-current=${i === here ? "step" : null}>
        ${i < here ? html`<${Icon} name="check" />` : null}
        <span>${id === "queue" && c.status === "auto" ? "Automated checks" : label}</span>
      </li>`)}
    </ol>
    <p class="path-guide"><b>${c.status_label}</b> <span class="muted">for ${age(c.status_at, now)}.</span>${" "}${guidance(c)}</p>
  </div>`;
}

export class CaseDetail extends Component {
  constructor(props) {
    super(props);
    this.state = { c: null, error: null, mode: null, busy: false, note: null, tab: "feed",
      now: Date.now() / 1000 };
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
    const worked = ["take", "pend", "resolve", "release"].some(has);
    return html`<div class="record-actions">
      ${has("take") ? html`<button type="button" class="primary" disabled=${busy}
        onClick=${() => this.act(() => post(`/api/noc/cases/${c.id}/take`, {}))}>${!c.owner ? "Take" : c.owner.you ? "Resume work"
          : "Take it over"}</button>` : null}
      ${has("pend") ? html`<button type="button" disabled=${busy}
        onClick=${() => this.setState({ mode: "pend" })}>Set pending…</button>` : null}
      ${has("resolve") ? html`<button type="button" disabled=${busy}
        onClick=${() => this.setState({ mode: "resolve" })}>Resolve…</button>` : null}
      ${has("release") ? html`<button type="button" disabled=${busy}
        onClick=${() => this.act(() => post(`/api/noc/cases/${c.id}/release`, { note: "" }))}>Back to the queue</button>` : null}
      ${!worked ? html`<span class="muted">${c.owner && !c.owner.you
        ? `Owned by ${c.owner.name}.` : "Read-only for you."}</span>` : null}
    </div>`;
  }

  highlights(c) {
    return html`<dl class="highlights">
      <div><dt>Priority</dt><dd><${Priority} priority=${c.priority} label=${c.priority_label} />
        ${c.priority_auto ? html`<span class="muted hl-note">from the Broker</span>` : null}</dd></div>
      <div><dt>Status</dt><dd><span class=${cx("case-status", `case-${c.super_state}`)}>${c.status_label}</span></dd></div>
      <div><dt>Level</dt><dd>${c.agent || c.with_people ? c.level_label : "—"}</dd></div>
      <div><dt>Owner</dt><dd>${c.owner ? `${c.owner.name}${c.owner.you ? " (you)" : ""}` : html`<span class="muted">Nobody has taken it</span>`}</dd></div>
      <div><dt>Opened</dt><dd title=${timeTitle(c.opened_at)}>${relative(c.opened_at)}</dd></div>
      <div><dt>Take</dt><dd><${Clock} clock=${c.clocks && c.clocks.take} now=${this.state.now} label="Take" /></dd></div>
    </dl>`;
  }

  history(c) {
    if (!c.cycles || !c.cycles.length) return html`<p class="muted">It has not been in the NOC queue yet.</p>`;
    return html`<div class="table-scroll"><table class="data compact-table">
      <caption class="visually-hidden">Each time the case entered the NOC queue</caption>
      <thead><tr><th scope="col">Why</th><th scope="col">Entered</th>
        <th scope="col">Take</th><th scope="col">Customer update</th></tr></thead>
      <tbody>${c.cycles.map((y, i) => html`<tr key=${i}>
        <td>${y.reason_label}</td><td title=${timeTitle(y.entered_at)}>${relative(y.entered_at)}</td>
        <td><${Clock} clock=${y.take} now=${this.state.now} /></td>
        <td><${Clock} clock=${y.document} now=${this.state.now} /></td>
      </tr>`)}</tbody></table></div>`;
  }

  main(c, catalogue) {
    const { tab } = this.state;
    const edit = can("cases.work") && c.actions.includes("edit");
    const tabs = [["feed", "Feed"], ["update", "Customer update"], ["history", `In the queue (${(c.cycles || []).length})`]];
    return html`<section class="record-main">
      <div class="tabs-line" role="tablist" aria-label="The case">
        ${tabs.map(([id, label]) => html`<button type="button" role="tab" key=${id}
          aria-selected=${tab === id} class=${cx("tab-line", tab === id && "on")}
          onClick=${() => this.setState({ tab: id })}>${label}</button>`)}
      </div>
      ${tab === "feed" ? html`<${CaseFeed} id=${c.id} version=${c.version} onRequeued=${() => this.read()} />` : null}
      ${tab === "update" ? html`<div class="case-update">
        ${edit && c.missing.length ? html`<p class="hint">Still to fill before it can wait on
          someone: ${c.missing.map((m) => (catalogue.fields[m] ? catalogue.fields[m].label : "Next update")).join(", ")}.</p>` : null}
        <${CaseForm} c=${c} editable=${edit} fields=${catalogue.fields}
          priorities=${catalogue.priorities} onSaved=${(x) => this.done(x)} />
      </div>` : null}
      ${tab === "history" ? this.history(c) : null}
    </section>`;
  }

  side(c, now) {
    const s = c.situation;
    return html`<aside class="record-side">
      <${Milestones} c=${c} now=${now} />
      <${Assignment} c=${c} onMode=${(mode) => this.setState({ mode })} />
      <section class="panel-block" aria-label="The situation">
        <h3>The situation</h3>
        <p>${s.name || `Situation #${c.id}`}</p>
        <p class="muted">${`${s.members} alarm${s.members === 1 ? "" : "s"}${s.severity ? ` · ${s.severity} on the Broker` : ""} · ${s.status}`}</p>
        <a class="tap" href=${`#/situations/${c.id}`}>Its traps and elements on the Broker →</a>
      </section>
      ${c.resolution ? html`<section class="panel-block case-resolution">
        <h3>${c.resolution.how_label}</h3>
        <p><b>${c.resolution.cause_label}.</b> ${c.resolution.solution}</p>
        ${c.resolution.alarms_cleared ? html`<p class="hint">Its alarms were verified clear on the device and cleared.</p>` : null}
      </section>` : null}
    </aside>`;
  }

  render({ catalogue, onBack }, { c, error, mode, note, now }) {
    if (error) return html`<div><button type="button" class="linkish" onClick=${onBack}>← Cases</button>
      <p class="err" role="alert">${error}</p></div>`;
    if (!c) return html`<p class="muted">Opening the case…</p>`;
    const close = () => this.setState({ mode: null });
    return html`<article class="case-detail" aria-labelledby=${`case-${c.id}-heading`}>
      <button type="button" class="linkish" onClick=${onBack}>← Cases</button>
      <header class="record-head">
        <span class="record-icon"><${Icon} name="cases" /></span>
        <div class="record-title">
          <p class="record-kind">Case <span class="mono">#${c.id}</span></p>
          <h2 id=${`case-${c.id}-heading`}>${c.title || c.situation.name || "Untitled case"}</h2>
        </div>
        ${this.actions(c)}
      </header>
      ${this.highlights(c)}
      <${StatusPath} c=${c} now=${now} />
      ${note ? html`<p class="err" role="alert">${note}</p>` : null}
      ${mode === "pend" ? html`<${PendForm} c=${c} pending=${catalogue.pending}
        statuses=${catalogue.statuses} fields=${catalogue.fields} onDone=${(x) => this.done(x)}
        onCancel=${close} />` : null}
      ${mode === "resolve" ? html`<${ResolveForm} c=${c} causes=${catalogue.causes}
        onDone=${(x) => this.done(x.id ? x : c)} onCancel=${close} />` : null}
      <${MoveForm} mode=${mode} c=${c} onDone=${(x) => this.done(x)} onCancel=${close} />
      <div class="record-body">
        ${this.main(c, catalogue)}
        ${this.side(c, now)}
      </div>
    </article>`;
  }
}
